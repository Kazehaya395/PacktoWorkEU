"""Current rendered browser authority, not a CAPTCHA solver or network client.

Used only in an explicitly opened Assisted context. Raw HTTP classification
stays structural; a browser may prove that a stale title/status/hidden widget
no longer represents the current primary document. No vendor/hostname presets.
"""
from __future__ import annotations
import re
from html import escape
from datetime import datetime,timezone
from packtoworkeu.scanning.response_evidence import challenge_reason, AUTOMATIC_VERIFICATION_PATTERN, LOCALIZED_VERIFICATION_PATTERN, normalized_access_text
from packtoworkeu.core.job_identity import posting_candidate,posting_identity,explicit_detail_url,is_access_proof_parameter

# One bounded DOM read, no clicks, injected handlers, cookies or storage access.
VISIBLE_DOCUMENT_JS = r"""() => {
 const cache=new WeakMap();
 const visible=(e)=>{
  if(!e || e.nodeType!==1)return false;
  if(cache.has(e))return cache.get(e);
  let ok=true;
  for(let n=e;n&&n.nodeType===1;n=n.parentElement){
   const s=getComputedStyle(n);
   if(s.display==='none'||(n===e&&(s.visibility==='hidden'||s.visibility==='collapse'))||Number(s.opacity)===0||s.clipPath==='inset(50%)'){ok=false;break;}
  }
  if(ok){
   let rects=Array.from(e.getClientRects());
   if(getComputedStyle(e).display==='contents'){const range=document.createRange();range.selectNodeContents(e);rects=Array.from(range.getClientRects());}
   ok=rects.some(r=>r.width>1&&r.height>1);
   // Inline links may wrap block cards while having zero-height own boxes.
   // Require an actually visible descendant, not hidden markup.
   if(!ok && e.matches('a,[role=link],[data-job-url],[data-href],[data-url]'))ok=Array.from(e.querySelectorAll('*')).slice(0,32).some(n=>visible(n));
  }
  cache.set(e,ok);return ok;
 };
 // A visible recommendation/sidebar is not the current primary posting or
 // listing. Do not let a longer related-job description win by text length.
 const secondary=(e,heading=false)=>{
  if(e.closest(heading?'nav,footer,aside':'nav,footer,aside,form'))return true;
  for(let n=e;n&&n!==document.body&&n!==document.documentElement;n=n.parentElement){
   const label=(n.id||'')+' '+(typeof n.className==='string'?n.className:'');
   if(/related[-_ ]|recommend|suggested[-_ ]jobs|similar[-_ ]jobs|breadcrumb/i.test(label))return true;
  }
  return false;
 };
 const text=(root,limit=60001,includeSecondary=false)=>{
  if(!root)return '';
  const walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n,out=[],size=0,count=0;
  while((n=walk.nextNode())&&count++<12000&&size<limit){
   const e=n.parentElement;
   if(!e||e.closest('script,style,noscript,template')||!visible(e)||(!includeSecondary&&secondary(e)))continue;
   // Do not include contact/application furniture or subsequent related jobs
   // when the primary section group shares their outer container.
   const heading=e.closest('h2,h3');
   if(!includeSecondary&&heading&&/^(?:kontakt|contact|weitere angebote|similar jobs|related jobs|recommended jobs)/i.test((heading.textContent||'').trim()))break;
   const s=(n.nodeValue||'').replace(/\s+/g,' ').trim();
   if(s){out.push(s);size+=s.length+1;}
  }
  return out.join(' ').slice(0,limit);
 };
 const headings=Array.from(document.querySelectorAll('h1,h2')).filter(e=>visible(e)&&!secondary(e,true)).slice(0,12).map(e=>text(e,240,true));
 const navigation_anchors=Array.from(document.querySelectorAll('a[href]')).filter(e=>visible(e)&&!e.closest('footer,aside')).slice(0,180).map(e=>({url:e.href,title:text(e,240,true)}));
 const cardSelector='article,.job-card,[class*=job-result],[class*=card-job],[class*=vacancy-card],.position-card,[role="link"]';
 document.querySelectorAll('[data-ss-census-node]').forEach(e=>e.removeAttribute('data-ss-census-node'));
 const allPostingNodes=Array.from(document.querySelectorAll('a[href],[data-job-url],[data-href],[data-url]'));
 const postingNodes=allPostingNodes.filter(e=>visible(e)&&!secondary(e,true)&&
  (e.matches('a[href]')||e.matches(cardSelector))).slice(0,2500);
 const includedPostingNodes=new Set(postingNodes);
 const census_excluded=allPostingNodes.filter(e=>!includedPostingNodes.has(e)).slice(0,8).map(e=>({
  url:e.href||e.getAttribute('data-job-url')||e.getAttribute('data-href')||e.getAttribute('data-url')||'',
  stage:!visible(e)?'not_visible':secondary(e,true)?'secondary_context':!e.matches('a[href]')&&!e.matches(cardSelector)?'unsupported_card_shape':'census_cap',
  node:e.tagName+' '+String(e.className||'').slice(0,120)}));
 const anchors=postingNodes.map((e,i)=>{
  e.setAttribute('data-ss-census-node',String(i));
  let url=e.href||e.getAttribute('data-job-url')||e.getAttribute('data-href')||e.getAttribute('data-url')||'';
  try{url=new URL(url,location.href).href;}catch(error){url='';}
  return {url,title:text(e,240,true),dom_url_card:!e.matches('a[href]'),node_token:String(i),in_card:!!e.closest(cardSelector)};
 });
 // A description selector can cover responsibilities only. Expand to the
 // nearest same-posting section group, never to body or a multi-job wrapper.
 const sectionLabel=/^(?:stellenbeschreibung|qualifikationen|vorteile|job description|qualifications|benefits|deine benefits|das erwartet dich|das bringst du mit|responsibilities|requirements)$/i;
 const completeRoot=(node)=>{
  for(let n=node,depth=0;n&&n!==document.body&&depth<7;n=n.parentElement,depth++){
   if(secondary(n)||n.querySelectorAll('h1').length>1)break;
   const hs=Array.from(n.querySelectorAll('h2,h3')).filter(h=>visible(h)&&!secondary(h));
   if(hs.filter(h=>sectionLabel.test(text(h,240))).length>=2)return n;
  }
  return node;
 };
 const descriptions=Array.from(document.querySelectorAll('[itemprop="description"],[class*="job-description"],[id*="job-description"],[class*="jobDescription"],[data-testid*="job-description"]')).filter(e=>visible(e)&&!secondary(e)).slice(0,24).map(e=>text(completeRoot(e)));
 const primary=descriptions.sort((a,b)=>b.length-a.length)[0]||'';
 const job_locations=Array.from(document.querySelectorAll('[itemprop="jobLocation"],[data-testid="job-location"],.job-location,.jobLocation')).filter(e=>visible(e)&&!secondary(e)).slice(0,32).map(e=>text(e,240));
 const main=document.querySelector('main,[role="main"]');
 const nativeCards=Array.from(document.querySelectorAll('.position-cards-container .position-card[role="link"]')).filter(e=>visible(e)&&!secondary(e));
 let selectedSchema=null;
 const selectedTitle=document.querySelector('.position-full-card .position-title');
 for(const node of document.querySelectorAll('script[type="application/ld+json"]')){
  try{const j=JSON.parse(node.textContent);if(j['@type']==='JobPosting'&&j.url&&selectedTitle&&visible(selectedTitle)&&j.title===(selectedTitle.innerText||'').trim()&&new URL(j.url,location.href).href===location.href)selectedSchema={url:j.url,title:j.title};}catch(e){}
 }
 if(nativeCards.length&&selectedSchema)anchors.push({...selectedSchema,in_card:true});
 const ids=Array.from(document.querySelectorAll('#px-captcha,#cf-challenge-running,#challenge-running'));
 const activeIds=ids.filter(visible).map(e=>({id:e.id,text:text(e,1200,true),fixed:getComputedStyle(e).position==='fixed'&&e.getBoundingClientRect().width*e.getBoundingClientRect().height>=innerWidth*innerHeight*.2}));
 const overlays=[];const frames=[];
 const nodes=Array.from(document.querySelectorAll('dialog,[aria-modal="true"],iframe,[style*="position"],#px-captcha,#cf-challenge-running,#challenge-running')).slice(0,300);
 for(const e of nodes){
  if(!visible(e))continue;
  const s=getComputedStyle(e),r=e.getBoundingClientRect();
  const modal=e.matches('dialog[open],[aria-modal="true"]');
  let fixed=s.position==='fixed';
  for(let n=e.parentElement;n&&!fixed;n=n.parentElement)fixed=getComputedStyle(n).position==='fixed'||n.getAttribute('aria-modal')==='true';
  if(e.tagName==='IFRAME'){
   if(modal||fixed&&r.width*r.height>=innerWidth*innerHeight*.2)frames.push(e.src||'');
  }else if(modal||fixed)overlays.push(text(e,1500,true));
 }
 return {version:1,url:location.href,title:document.title,headings,anchors,navigation_anchors,census_excluded,
  census_stages:{dom_candidates:allPostingNodes.length,visible_candidates:allPostingNodes.filter(visible).length,primary_candidates:postingNodes.length},
  cards_count:Array.from(document.querySelectorAll('article,.job-card,[class*=job-result]')).filter(e=>visible(e)&&!secondary(e)).length,
  native_cards:nativeCards.length,selected_schema:selectedSchema,observed_tokens:nativeCards.map(e=>e.getAttribute('data-ss-observed-card')).filter(Boolean),
  job_locations,primary_text:primary,main_text:text(main),body_preview:text(document.body,1600),
  active_ids:activeIds,hidden_challenge_nodes:ids.filter(e=>!visible(e)).length,
  overlays:overlays.slice(0,12),foreground_frames:frames.slice(0,8)};
}"""
_PROMPT=re.compile(r'(?i)verify (?:that )?you (?:are|are not) (?:a )?(?:human|robot)|confirm (?:that )?you are (?:a )?human|press\s+(?:&|and)\s+hold|access (?:to this page has been |is )?denied|403 forbidden|checking (?:your|the) browser|complete (?:the |this )?(?:security check|captcha)|' + AUTOMATIC_VERIFICATION_PATTERN + '|' + LOCALIZED_VERIFICATION_PATTERN)
_LISTING=re.compile(r'(?i)^(?:(?:[0-9][0-9,. ]*|current|available|all|latest|our|aktuelle)\s+)?(?:search|(?:job )?results?|vacancies|vacatures|careers|jobs?|open positions|stellenangebote)\b')


# Diagnostic-only sanitizers. Operational posting/scope identities are not
# rewritten. Public non-secret query/filter/fragment strings stay byte-for-byte.
def _private_key(key):
    key=str(key).casefold().replace('-','_')
    return (is_access_proof_parameter(key)or'token'in key or key.startswith('__cf_chl')or key in
        {'auth','authorization','password','passwd','session','sessionid','api_key','apikey','cookie','secret','jwt','code'})


def sensitive_url(value):
    from urllib.parse import urlsplit,parse_qsl,unquote
    try:
        p=urlsplit(str(value or''))
        return bool(p.username or p.password or '[redacted]'in unquote(str(value)).casefold()or
            any(_private_key(k)for k,v in parse_qsl(p.query,keep_blank_values=True))or
            re.search(r'(?i)(?:^|[?&])(?:[\w-]*token|auth|password|session|jwt|code)=',p.fragment))
    except ValueError:return True


def redact_url(value):
    from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
    value=str(value or'')
    try:
        p=urlsplit(value);pairs=parse_qsl(p.query,keep_blank_values=True)
        private=any(_private_key(k)for k,v in pairs)
        query=urlencode([(k,'[redacted]'if _private_key(k)else v)for k,v in pairs])if private else p.query
        fragment=re.sub(r'(?i)((?:^|[?&])(?:[\w-]*token|auth|password|session|jwt|code)=)[^&\s]*',r'\1[redacted]',p.fragment)
        if p.scheme in('data','javascript'):return '[embedded resource]'
        return urlunsplit((p.scheme,p.netloc.rsplit('@',1)[-1],p.path,query,fragment))
    except ValueError:return '[invalid URL]'


def redact_text(value):
    text=str(value or'')
    text=re.sub(r'https?://[^\s<>\]\)"\'`]+',lambda m:redact_url(m[0]),text)
    return re.sub(r'(?im)\b(?:set-cookie|cookie|proxy-authorization|authorization)\s*[:=]\s*[^\r\n]*','[private header redacted]',text)



def watch_page_responses(page):
    """Observe bounded document/data failures; no request/header/cookie reads."""
    if getattr(page,'_assisted_response_watch',False):return
    def retain(item):
        values=list(getattr(page,'_assisted_failed_responses',[]));values.append(item)
        page._assisted_failed_responses=values[-4:]
    def received(response):
        try:
            request=response.request
            if request.is_navigation_request()and request.frame==page.main_frame:
                page._assisted_nav_status=response.status;page._assisted_active_wall_seen=False;page._assisted_failed_responses=[]
            elif response.status>=400 and request.resource_type in('xhr','fetch'):
                retain(dict(url=redact_url(response.url),http_status=response.status,resource_type=request.resource_type,captured_at=datetime.now(timezone.utc).isoformat()))
        except Exception:pass
    def failed(request):
        try:
            if request.resource_type in('xhr','fetch'):
                retain(dict(url=redact_url(request.url),http_status=0,resource_type=request.resource_type,error=redact_text(request.failure)[:400],captured_at=datetime.now(timezone.utc).isoformat()))
        except Exception:pass
    try:
        page.on('response',received);page.on('requestfailed',failed);page._assisted_response_watch=True
    except Exception:pass  # nonbrowser adapters cannot claim observed failures


def career_listing_links(data):
    """Bounded same-origin entry navigation, not job admission or ATS guessing.

    External ATS links require the user to select that official route explicitly;
    a label saying Jobs is not authority to navigate to an arbitrary origin.
    """
    from urllib.parse import urlsplit,urljoin,parse_qs
    source=urlsplit(str(data.get('url')or''));out=[]
    for a in (data.get('navigation_anchors')or data.get('anchors')or[])[:180]:
        if not isinstance(a,dict):continue
        target=urljoin(str(data.get('url')or''),str(a.get('url')or''))
        p=urlsplit(target);label=str(a.get('title')or'').strip()
        if (p.scheme not in('http','https')or(p.scheme,p.netloc)!=(source.scheme,source.netloc)
                or target==data.get('url')or sensitive_url(target)):continue
        original=parse_qs(source.query,keep_blank_values=True);current=parse_qs(p.query,keep_blank_values=True)
        if any(current.get(k)!=v for k,v in original.items()):continue
        if source.fragment and p.fragment!=source.fragment:continue
        route=bool(re.search(r'/(?:search[-/]results|job[-/]search|search[-/]jobs|jobs|vacancies|open-positions)/?$',p.path,re.I))
        cta=bool(re.fullmatch(r'(?:search|find|view|browse|explore|see)(?: all| our| open| current)? (?:jobs|openings|vacancies|positions|open positions)',label,re.I))
        if route and (cta or re.fullmatch(r'(?:jobs|job search|search results|open positions|get started)',label,re.I)):
            if target not in out:out.append(target)
    return out[:4]


def _document_error(data, status):
    if status in(404,410):return 'HTTP %s: requested career document not found' % status
    if status>=500:return 'HTTP %s: career document server error' % status
    heading=' '.join(str(x)for x in data.get('headings',[])[:2])
    title=str(data.get('title')or'')
    body=str(data.get('body_preview')or'')[:500]
    if (re.search(r'(?i)\b404\b.{0,40}(?:not found|oh no)',title+' '+heading+' '+body)
            or re.search(r'(?i)^(?:page not found|404 not found)',title.strip())):
        return '404 document: requested career page not found'
    return ''


def inspect_page(page,*,kind='listing',status=0,validator=None):
    """Return None for non-browser adapters; otherwise a current DOM snapshot.

    A positive foreground wall always wins over recommended links or content
    behind an overlay. A title or old response alone cannot negate genuinely
    visible primary content. Posting identity is still checked by the reader.
    """
    try:data=page.evaluate(VISIBLE_DOCUMENT_JS)
    except Exception:return None
    if not isinstance(data,dict)or data.get('version')!=1:return None
    data['native_discovery_errors']=list(getattr(page,'_ss_split_errors',[]))[-8:]
    data['failed_data_responses']=list(getattr(page,'_assisted_failed_responses',[]))[-4:]
    data['navigation_status']=int(getattr(page,'_assisted_nav_status',status)or 0)
    headings=[str(x)[:240]for x in data.get('headings',[])[:12]]
    from packtoworkeu.core.job_identity import split_career_posting
    native_bound=split_career_posting(str(data.get('url')or''))
    anchors=list(data.get('anchors',[]))
    if native_bound:
        cache=getattr(page,'_ss_split_card_cache',{})
        for token in data.get('observed_tokens',[])[:1500]:
            item=cache.get(token)
            if item and split_career_posting(item[1],str(data.get('url')or'')):
                anchors.append({'title':item[0],'url':item[1],'in_card':True,'observed_token':token})
    admitted=[a for a in anchors[:2500]if isinstance(a,dict)and posting_candidate(str(a.get('url')or''),str(a.get('title')or''),validator)]
    self_identity=posting_identity(str(data.get('url')or''))
    if not explicit_detail_url(str(data.get('url')or'')) and not native_bound:
        admitted=[a for a in admitted if posting_identity(str(a.get('url')or''))!=self_identity]
    data['census_stages']=dict(data.get('census_stages')or{},admitted=len(admitted),
        rejected=len(anchors)-len(admitted))
    admitted_objects={id(a)for a in admitted}
    data['census_excluded']=[{'url':redact_url(str(a.get('url')or'')),
        'stage':str(a.get('stage')or'')[:60],'node':redact_text(str(a.get('node')or''))[:160]}
        for a in data.get('census_excluded',[])[:8]if isinstance(a,dict)]
    data['census_rejections']=[{'url':redact_url(str(a.get('url')or'')),
        'title':redact_text(str(a.get('title')or''))[:160],
        'stage':'self_listing'if posting_identity(str(a.get('url')or''))==self_identity else 'shared_posting_admission'}
        for a in anchors if isinstance(a,dict)and id(a)not in admitted_objects][:8]
    data['census_truncated']=len(anchors)>=2500 or len(admitted)>180
    full_admitted=admitted
    admitted=admitted[:180]
    links={posting_identity(str(a.get('url')or''))for a in admitted}
    card_listing=any(a.get('in_card')and posting_identity(str(a.get('url')or''))!=posting_identity(str(data.get('url')or''))for a in admitted)
    heading=next((s for s in headings if s.strip()),'')
    data['job_locations']=list(dict.fromkeys(str(x).strip() for x in data.get('job_locations',[]) if isinstance(x,str)and x.strip()))[:32]
    primary=str(data.get('primary_text')or'')[:60001]
    main=str(data.get('main_text')or'')[:60001]
    role=bool(heading and not _LISTING.search(heading)and not _PROMPT.search(normalized_access_text(heading)))
    listing=len(links)>=3 or card_listing or bool(native_bound and data.get('native_cards')) or bool(links and any(_LISTING.search(h) for h in headings))
    detail=bool((len(primary)>=200 and not _LISTING.search(heading))or(role and len(main)>=600 and not listing))
    if native_bound and data.get('selected_schema'):detail=True
    document_error=_document_error(data,data['navigation_status'])
    # Text evidence of job controls is NOT a posting identity or permission to
    # ingest synthetic rows. It only prevents destructive blank-page recovery.
    preview=str(data.get('main_text')or data.get('body_preview')or'')
    rendered_unextractable=bool(not links and not document_error
        and re.search(r'(?i)(?:job openings|explore our openings|open positions)', ' '.join(headings))
        and len(re.findall(r'(?i)\bjob details\b',preview))>=2 and len(preview)>=100)
    if document_error:
        listing=detail=False
        admitted=[];full_admitted=[];links=set()
    ready=listing if kind=='listing'else detail
    heading_wall=next((h for h in headings[:1]if _PROMPT.search(normalized_access_text(h))), '')
    overlays=[str(x)[:1500]for x in data.get('overlays',[])[:12]]
    foreground=next((x for x in overlays if _PROMPT.search(normalized_access_text(x))), '')
    ids=[x for x in data.get('active_ids',[])[:8]if isinstance(x,dict)]
    frames=[str(x)[:2000]for x in data.get('foreground_frames',[])[:8]if challenge_reason('',frame_urls=[str(x)])]
    # Visible provider ids in a wall-only document are strong evidence; an
    # application widget beside a readable primary JD is not a foreground wall.
    active=next((x for x in ids if not ready or x.get('fixed')),None)
    if heading_wall or foreground or active or frames:
        fragment='<h1>'+escape(heading_wall)+'</h1><div style="position:fixed">'+escape(foreground)+'</div>'
        if active:fragment+='<div id="'+escape(str(active.get('id')or''),quote=True)+'">'+escape(str(active.get('text')or''))+'</div>'
        reason=challenge_reason(fragment,frame_urls=frames,subject='detail'if kind=='detail'else'listing')
        # A cross-origin foreground verification frame need not expose text.
        if not reason and frames:reason='Foreground access verification obscures the primary document (human verification not confirmed)'
        if not reason:reason=challenge_reason('',status=status,subject='detail'if kind=='detail'else'listing') or 'Visible access-denied foreground; primary content not available (human verification not confirmed)'
    elif ready:
        reason=''
    else:
        fragment='<title>'+escape(str(data.get('title')or''))+'</title><h1>'+escape(heading)+'</h1>'+escape(str(data.get('body_preview')or''))
        reason=challenge_reason(fragment,status=status,subject='detail'if kind=='detail'else'listing')
    # The append control is bounded by the very same admitted DOM nodes, not
    # a second path-pattern census. No URL inference or arbitrary onclick parsing.
    safe_admitted=full_admitted if ready and not reason else []
    data['posting_identities']=sorted({posting_identity(str(a.get('url')or'')) for a in safe_admitted})
    try:
        page.evaluate('''tokens => {
          document.querySelectorAll('[data-ss-primary-posting]').forEach(e=>e.removeAttribute('data-ss-primary-posting'));
          for(const e of document.querySelectorAll('[data-ss-census-node],[data-ss-observed-card]')){
            if(tokens.nodes.includes(e.getAttribute('data-ss-census-node'))||tokens.observed.includes(e.getAttribute('data-ss-observed-card')))
              e.setAttribute('data-ss-primary-posting','1');
          }
        }''', {'nodes':[a['node_token']for a in safe_admitted if 'node_token'in a],
               'observed':[a['observed_token']for a in safe_admitted if 'observed_token'in a]})
    except Exception:pass
    data.update(document_error=document_error,rendered_unextractable=bool(rendered_unextractable and not reason),kind=kind,reason=reason,ready=bool(ready and not reason),listing_ready=bool(listing and not reason),
        detail_ready=bool(detail and not reason),primary_text=primary if len(primary)>=200 else main,
        primary_chars=len(primary if len(primary)>=200 else main),primary_truncated=len(primary if len(primary)>=200 else main)>=60001,visible_job_links=len(links),
        # Issue 005 P1: the count and the links behind it have to travel together, or a run
        # that says "164 visible job links" cannot be reconciled with the harvest that took 0.
        admitted_anchors=[{'url':str(a.get('url')or ''),'title':str(a.get('title')or '')[:240],'dom_url_card':bool(a.get('dom_url_card'))}for a in admitted[:180]],
        authority='current rendered primary/foreground',captured_at=datetime.now(timezone.utc).isoformat())
    return data


def admitted_job_links(page,*,kind='listing',validator=None):
    """Visible anchors the render census itself admits as postings (Issue 005 P1).

    Returns ``([( {'url','title'} … ], meta)`` — empty, not None, for a non-browser
    adapter or a failed read, so the caller can distinguish "no links" from
    "could not look". This is the same admission test that produces the
    ``visible_job_links`` count in the run's own evidence, which makes it the
    authority for whether a page rendered a listing at all.
    """
    try:
        data=inspect_page(page,kind=kind,validator=validator)
    except Exception:
        return [],{}
    if not isinstance(data,dict):
        return [],{}
    out=[]
    for a in data.get('admitted_anchors') or []:
        if not isinstance(a,dict):
            continue
        url=str(a.get('url') or '').strip()
        if url:
            out.append({'url':url,'title':str(a.get('title') or '').strip(),'dom_url_card':bool(a.get('dom_url_card'))})
    meta={k:data.get(k) for k in ('kind','reason','ready','visible_job_links',
                                  'navigation_status','authority','document_error','rendered_unextractable','census_truncated','census_stages','census_rejections','census_excluded')}
    return out,meta


def public_snapshot(data):
    """No full JD, cookies, auth or frame-query tokens in diagnostic snapshots."""
    if not data:return {}
    from urllib.parse import urlsplit,urlunsplit
    def frame_path(value):
        try:p=urlsplit(redact_url(value));return urlunsplit((p.scheme,p.netloc,p.path,'',''))
        except Exception:return ''
    public={key:data.get(key)for key in('kind','title','headings','body_preview','primary_chars',
        'visible_job_links','hidden_challenge_nodes','reason','ready','authority','captured_at','navigation_status','failed_data_responses','document_error','rendered_unextractable','native_cards','native_discovery_errors','census_stages','census_rejections','census_excluded')}|{
        'foreground_frames':[frame_path(x)for x in data.get('foreground_frames',[])[:8]]}
    for key in('title','body_preview','reason'):public[key]=redact_text(public.get(key))
    public['headings']=[redact_text(x)for x in(public.get('headings')or[])]
    return public
