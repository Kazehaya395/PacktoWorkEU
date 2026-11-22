"""Strict readers for the named publishers in campaign 234418."""
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

LUX='careers.essilorluxottica.com'
TABLE_HOSTS={LUX,'jobs.intesasanpaolo.com','jobs.pradagroup.com'}

def result_range(html):
    # This label belongs to the search table, not a number in the footer.
    m=re.search(r'<span\b[^>]*class=["\']paginationLabel["\'][^>]*>(.*?)</span>',html,re.I|re.S)
    if m:
        n=re.fullmatch(r'\s*(?:Results|Risultati)\s+([\d,]+)\s*[-–]\s*([\d,]+)\s+(?:of|di)\s+([\d,]+)\s*',re.sub('<[^>]+>','',m[1]),re.I)
        if n:return tuple(int(v.replace(',','')) for v in n.groups())


def lux_page(html,url):
    rows={};links=[]
    for tr in re.findall(r'<tr\b[^>]*class=["\']data-row["\'][^>]*>(.*?)</tr>',html,re.I|re.S):
        p=_Page(url);p.feed(tr);p.close()
        if len(p.jobs)==1:
            u,t=next(iter(p.jobs.items()));rows[u]=dict(job_url=u,job_title=t,location_hint=' '.join(p.loc.split()),posting_location_field=bool(p.loc),extraction_method='successfactors_table')
    p=_Page(url,pager=True);p.feed(html);p.close();return rows,p.links


class _Page(HTMLParser):
    def __init__(self,url,pager=False):
        super().__init__();self.url=url; self.pager=pager;self.jobs={};self.links=[];self.href='';self.title='';self.loc='';self.location=False
    def handle_starttag(self,t,attrs):
        a=dict(attrs); classes=(a.get('class') or '').split()
        if t=='td':self.location='colLocation' in classes
        if t=='a':
            u=urljoin(self.url,a.get('href') or '');q=urlsplit(u)
            if q.scheme!='https' or q.netloc!=urlsplit(self.url).netloc or q.netloc not in TABLE_HOSTS:return
            if self.pager and q.path=='/search/' and 'startrow' in parse_qs(q.query):self.links.append(u)
            elif not self.pager and 'jobTitle-link' in classes and re.fullmatch(r'/job/[^/]+/\d+/?',q.path):self.href=u;self.title=''
    def handle_data(self,d):
        if self.href:self.title+=d
        if self.location:self.loc+=d
    def handle_endtag(self,t):
        if t=='td':self.location=False
        if t=='a' and self.href:
            self.jobs[self.href]=' '.join(self.title.split());self.href=''


def lux_listing(seed,first,fetch,check,cap=250,*,cursor=None,save=None):
    """Resume at an observed offset, then recheck the prefix. A cursor is not a census."""
    import hashlib
    from packtoworkeu.scanning.response_evidence import ListingDiagnostic
    url=seed;html=first;records={};expected=1;total=None;pages_seen=set();positions=set();frontier=None
    filters={k:v for k,v in parse_qs(urlsplit(seed).query).items() if k not in ('startrow','createNewAlert')}
    def offset(u):
        if not isinstance(u,str):return None
        p=urlsplit(u);q=parse_qs(p.query);v=q.get('startrow',[])
        if not (p.scheme=='https' and p.netloc==urlsplit(seed).netloc and p.netloc in TABLE_HOSTS and
                p.path==urlsplit(seed).path and not p.fragment and len(v)==1 and v[0].isdigit() and
                all(q.get(k)==vs for k,vs in filters.items())):return None
        return int(v[0])
    for page in range(cap):
        check();rg=result_range(html)
        if not rg or rg[0]!=expected or (total is not None and rg[2]!=total):break
        start,end,total=rg;rows,links=lux_page(html,url)
        if len(rows)!=end-start+1 or end>total or start in pages_seen or records.keys() & rows.keys():break
        pages_seen.add(start);records.update(rows);positions.update(range(start,end+1))
        if len(records)==total and len(positions)==total:
            if save:save(None)
            return list(records.values()),ListingDiagnostic(f'SuccessFactors: {total} distinct posting URLs reconciled on {page+1} pages',complete=True,expected_total=total)
        nxt=next((u for u in links if offset(u)==end),None)
        if page==0:
            first_digest=hashlib.sha256('\n'.join(sorted(rows)).encode()).hexdigest()
            cur=cursor or {};jump=offset(cur.get('next')) if cur.get('seed')==seed and cur.get('total')==total and cur.get('first')==first_digest else None
            if jump is not None and end<jump<total:
                frontier=nxt;nxt=cur['next']
        if end==total and frontier:
            nxt=frontier;frontier=None
        if not nxt or offset(nxt)+1 in pages_seen:break
        if save:save(dict(seed=seed,total=total,first=first_digest,next=nxt))
        if page+1>=cap:break
        check();url=nxt;expected=offset(url)+1;html=fetch(url)
        if not html:break
    return list(records.values()),ListingDiagnostic(f'listing incomplete: SuccessFactors range stopped; {len(records)} distinct URLs, stated total {total}',needs_dom=True,complete=False,expected_total=total)


def detail_host(url):
    p=urlsplit(url)
    return (p.scheme=='https' and ((p.netloc==LUX and re.fullmatch(r'/job/[^/]+/\d+/?',p.path)) or
            (p.netloc=='www.hamilton-barnes.com' and p.path.startswith('/candidates/job/')) or
            (p.netloc=='ic-resources.com' and re.fullmatch(r'/[a-z]{2}/jobs/[^/]+-\d+/?',p.path))))


class _Detail(HTMLParser):
    def __init__(self):
        super().__init__(); self.canon=[];self.blocks=[];self.ld=None;self.text=[];self.depth=0;self.started=0;self.place={};self.hidden=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='link' and 'canonical' in (a.get('rel') or '').split():self.canon.append(a.get('href') or '')
        if tag=='script' and a.get('type')=='application/ld+json':self.ld=[]
        if tag=='meta' and a.get('itemprop') in ('addressCountry','addressLocality'):self.place[a['itemprop']]=a.get('content') or ''
        if self.depth:
            if tag=='span':self.depth+=1
            if tag in ('script','style'):self.hidden=tag
            if tag in ('br','p','li','div'):self.text.append('\n')
        elif tag=='span' and a.get('itemprop')=='description' and a.get('data-careersite-propertyid')=='description':self.depth=1; self.started+=1
    def handle_endtag(self,tag):
        if tag=='script' and self.ld is not None:self.blocks.append(''.join(self.ld));self.ld=None
        if self.hidden==tag:self.hidden=None
        if self.depth and tag=='span':self.depth-=1
        if self.depth and tag in ('p','li','div'):self.text.append('\n')
    def handle_data(self,d):
        if self.ld is not None:self.ld.append(d)
        elif self.depth and not self.hidden:self.text.append(d)


def proven_detail(url,final,html):
    """Same destination AND canonical ad required; decline ambiguous/multiple posting schemas."""
    if not detail_host(url) or final.rstrip('/')!=url.rstrip('/'):return None
    p=_Detail();p.feed(html);p.close()
    if not p.canon or any(urljoin(final,u).rstrip('/')!=url.rstrip('/') for u in p.canon):return None
    if urlsplit(url).netloc==LUX:
        if p.started!=1 or p.depth:return None
        desc='\n'.join(' '.join(x.split()) for x in ''.join(p.text).splitlines() if x.strip());loc=', '.join(filter(None,(p.place.get('addressLocality'),p.place.get('addressCountry'))));org=''
    else:
        from packtoworkeu.core.job_metadata import load_job_json,structured_job_locations
        from packtoworkeu.scanning.career.career_scanner import normalize_jd_text
        posts=[]
        for block in p.blocks:
            try:data=load_job_json(block)
            except (ValueError,TypeError):return None
            items=data if isinstance(data,list) else (data.get('@graph') or [data]) if isinstance(data,dict) else []
            posts.extend(x for x in items if isinstance(x,dict) and x.get('@type')=='JobPosting')
        if len(posts)!=1:return None
        job=posts[0];u=job.get('url');raw=job.get('description')
        if (u and urljoin(final,u).rstrip('/')!=url.rstrip('/')) or not isinstance(raw,str):return None
        desc=normalize_jd_text(raw);loc=' | '.join(structured_job_locations(job));org=job.get('hiringOrganization') or ''
        if isinstance(org,dict):org=org.get('name') or ''
    return (desc,loc,org) if 200<=len(desc)<=32000 else None


def lidl_jobs(fetch,check,clean,deadline):
    import json,time
    from urllib.parse import urlencode
    rows={};total=None
    for page in range(1,251):
        check()
        if time.monotonic()>=deadline:break
        general=dict(page=page,resultsPerPage=10,sortField='',sortOrder='asc')
        url='https://lavoro.lidl.it/api/v1/search?'+urlencode({'general':json.dumps(general,separators=(',',':'))})
        try:data=json.loads(fetch(url))
        except (OSError,ValueError) as exc:
            return list(rows.values()),f'listing incomplete: Lidl public search page {page}: {type(exc).__name__}'
        if not isinstance(data,dict) or not isinstance(data.get('meta'),dict):break
        meta=data['meta']; published=meta.get('totalCount');batch=data.get('jobs')
        if (meta.get('page')!=page or type(published) is not int or published<0 or not isinstance(batch,list)
                or (total is not None and total!=published)):break
        if meta.get('count')!=len(batch) or meta.get('resultsPerPage')!=10 or len(batch)!=min(10,max(0,published-(page-1)*10)):break
        total=published;before=len(rows)
        for job in batch:
            if not isinstance(job,dict):continue
            ident=str(job.get('requisitionId') or '');u=job.get('jobDetailUrl') or '';p=urlsplit(u)
            if not (ident.isdigit() and p.scheme=='https' and p.netloc=='lavoro.lidl.it' and
                    p.path.startswith('/jobs/') and p.path.endswith('-'+ident) and job.get('title')):continue
            loc=job.get('location') or {}; city=loc.get('city') or '';cc=loc.get('country') or ''
            raw='\n'.join(str(job[k]) for k in ('descHeader','descResponsibilities') if job.get(k))
            rows[ident]=dict(job_url=u,job_title=job['title'],location_hint=city or cc,posting_country_code=cc,
                posting_location_field=True,jd_text=clean(raw,limit=max(32001,len(raw))),listing_revision=job.get('modifiedTime'),extraction_method='lidl_search_api')
        if len(rows)-before!=len(batch):break
        if len(rows)==total:return list(rows.values()),f'Lidl public search: {total} distinct requisitions reconciled on {page} page(s)'
        if before==len(rows) or len(batch)<10:break
    return list(rows.values()),f'listing incomplete: Lidl search retained {len(rows)} of published {total}'
