"""Identity-bound primary JobPosting bodies, never arbitrary page prose.

Shared by ordinary Career reads. Decline ambiguity rather than turn legacy
readable text into a fabricated fresh capture.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from packtoworkeu.core.job_identity import posting_identity
from packtoworkeu.core.job_metadata import load_job_json, structured_job_locations
from packtoworkeu.scanning.jd_support import normalize_jd_text
from packtoworkeu.scanning.response_evidence import challenge_reason


class PrimaryDocument(HTMLParser):
    def __init__(self):
        super().__init__(); self.canon=[]; self.blocks=[]; self.script=None
        self.stack=[]; self.sections=[]; self.active=None; self.platform_bodies=0; self.place={}
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='link' and 'canonical' in (a.get('rel') or '').split():self.canon.append(a.get('href') or '')
        if tag=='script' and a.get('type','').lower()=='application/ld+json':self.script=[]
        skip=(self.stack[-1][1] if self.stack else False) or tag in ('script','style','nav','footer','header','aside','form','noscript')
        label=' '.join(a.get(k) or '' for k in ('id','class','data-automation-id'))
        skip=skip or bool(re.search(r'(?i)cookie|consent|breadcrumb|related[-_ ]|recommend',label))
        if tag=='meta' and a.get('itemprop') in ('addressLocality','addressCountry','streetAddress','hiringOrganization'):
            self.place[a['itemprop']]=a.get('content') or ''
        platform=(tag=='span' and a.get('itemprop')=='description'
                  and a.get('data-careersite-propertyid')=='description')
        if platform and not skip:self.platform_bodies+=1
        specific=(a.get('itemprop')=='description' or bool(re.search(r'(?i)job[-_ ]?description|vacancy[-_ ]?description|posting[-_ ]?content|jobPostingDescription',label)))
        void=tag in ('br','hr','img','input','meta','link','source','wbr','area','base','embed')
        if not void:
            self.stack.append((tag,skip))
            if specific and not skip and self.active is None:
                self.active=(len(self.stack),[]);self.sections.append(self.active[1])
        if self.active and not skip and tag in ('p','div','li','br','section','h1','h2','h3'):self.active[1].append('\n')
    def handle_endtag(self,tag):
        if tag=='script' and self.script is not None:self.blocks.append(''.join(self.script));self.script=None
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                if self.active and i+1<=self.active[0]:self.active=None
                self.stack=self.stack[:i];break
    def handle_data(self,text):
        if self.script is not None:self.script.append(text)
        elif self.active and not (self.stack[-1][1] if self.stack else False):self.active[1].append(text)


def posting_nodes(value):
    if isinstance(value,list):
        for item in value:yield from posting_nodes(item)
    elif isinstance(value,dict):
        kind=value.get('@type')
        if kind=='JobPosting' or isinstance(kind,list) and 'JobPosting' in kind:yield value
        if '@graph' in value:yield from posting_nodes(value['@graph'])


def primary_capture(url,final_url,html):
    """One explicit matching schema, consistent canonical/final URL, bounded body."""
    requested=urlsplit(url);final=urlsplit(final_url or '')
    if (requested.scheme!='https' or final.scheme!='https' or requested.netloc!=final.netloc
            or posting_identity(url)!=posting_identity(final_url) or challenge_reason(html)):
        return None
    p=PrimaryDocument()
    try:p.feed(html);p.close()
    except Exception:return None
    if p.script is not None or p.active is not None or len(p.sections)>1:return None
    ident=posting_identity(url)
    if any(not c or posting_identity(urljoin(final_url,c))!=ident for c in p.canon):return None
    nodes=[]
    for block in p.blocks:
        try:nodes.extend(posting_nodes(load_job_json(block)))
        except (ValueError,TypeError):return None
    # The exact SuccessFactors primary microdata marker is stronger than a
    # legacy whole-page extraction. It is accepted only on an individual
    # numeric /job/ URL, with matching canonical/final URL and one body.
    if (p.platform_bodies==1 and len(p.sections)==1 and p.canon
            and re.fullmatch(r'/job/[^/]+/\d+/?',requested.path)):
        if len(nodes)>1:return None
        if nodes:
            target=nodes[0].get('url') or nodes[0].get('@id')
            if target and (not isinstance(target,str) or posting_identity(urljoin(final_url,target))!=ident):return None
        text=normalize_jd_text(''.join(p.sections[0])).strip()
        if not 200<=len(text)<=32000:return None
        loc=', '.join(filter(None,(p.place.get('addressLocality'),p.place.get('addressCountry')))) or p.place.get('streetAddress','')
        return text,loc,p.place.get('hiringOrganization','')
    if len(nodes)!=1:return None
    job=nodes[0];target=job.get('url') or job.get('@id')
    if not isinstance(target,str) or posting_identity(urljoin(final_url,target))!=ident:return None
    raw=job.get('description')
    if not isinstance(raw,str):return None
    text=normalize_jd_text(raw).strip()
    # A tiny JSON-LD teaser is not sufficient proof of this posting's body.
    if len(text)<200:return None
    if p.sections:
        specific=normalize_jd_text(''.join(p.sections[0])).strip()
        if len(specific)>len(text):text=specific
    if not 200<=len(text)<=32000:return None
    org=job.get('hiringOrganization') or ''
    if isinstance(org,dict):org=org.get('name') or ''
    if not isinstance(org,str):org=''
    return text,' | '.join(structured_job_locations(job)),org
