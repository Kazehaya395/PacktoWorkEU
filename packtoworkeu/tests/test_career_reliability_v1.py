"""Reliability repairs must improve proof, not inflate portal pass rates."""
import csv,json,time
from pathlib import Path
import pytest
from packtoworkeu.scanning.career import career_scanner as cs
from packtoworkeu.scanning.career.primary_capture import primary_capture
from packtoworkeu.scanning.response_evidence import ListingDiagnostic
from packtoworkeu.core.saved_details import SavedDetailReader,capture_proof
from packtoworkeu.scanning.common import completion_status

URL='https://careers.fixture.invalid/jobs/12345'
TEXT='Build reliable software systems. Collaborate with engineers and deliver useful products. '*15


def html(url=URL,text=TEXT):
    return (f'<link rel="canonical" href="{url}"><h1>Reliability Engineer</h1>'
        '<nav>'+'Browse jobs. '*200+'</nav><script type="application/ld+json">'
        +json.dumps({'@type':'JobPosting','url':url,'title':'Reliability Engineer','description':text,
         'jobLocation':{'@type':'Place','address':{'@type':'PostalAddress','addressLocality':'Milan','addressCountry':'Italy'}}})+'</script>')


def scanner(tmp_path):
    s=cs.CareerPortalScanner(input_csv='unused',skip_preflight=True)
    s._saved_jd_reader=SavedDetailReader(str(tmp_path/'none.db'));return s


def test_primary_same_source_replaces_longer_unproven_body(tmp_path):
    s=scanner(tmp_path);s.store_captured_jd(URL,'Unproven page chrome. '*500)
    text,loc,_=s._remember_detail_html(URL,html(),final_url=URL)
    assert text==TEXT.strip()==s.get_captured_jd(URL) and capture_proof(s,URL,text)
    assert 'Browse jobs' not in text and 'Milan' in loc


@pytest.mark.parametrize('failure',['schema-url','canonical','final','cross-host','missing-identity','multiple','oversized','thin','active-wall','unclosed','multiple-containers','blank-canonical'])
def test_primary_identity_ambiguity_and_completeness_guards(failure):
    h=html();final=URL
    if failure=='schema-url':h=h.replace('"url": "'+URL+'"','"url": "'+URL.replace('12345','99999')+'"')
    if failure=='canonical':h=h.replace('href="'+URL+'"','href="'+URL.replace('12345','99999')+'"')
    if failure=='final':final=URL.replace('12345','99999')
    if failure=='cross-host':final=URL.replace('careers.fixture.invalid','other.invalid')
    if failure=='missing-identity':h=h.replace('"url": "'+URL+'", ','')
    if failure=='multiple':h+=html()
    if failure=='oversized':h=html(text='Long description. '*4000)
    if failure=='thin':h=html(text='A preview only.')
    if failure=='active-wall':h='<title>Just a moment...</title>'+h
    if failure=='unclosed':h=h.replace('</script>','')
    if failure=='multiple-containers':h+='<div class="job-description">'+TEXT+'</div><div class="job-description">'+TEXT+'</div>'
    if failure=='blank-canonical':h=h.replace('href="'+URL+'"','href=""')
    assert primary_capture(URL,final,h) is None


def test_single_semantic_container_outranks_structured_preview():
    h=html(text='Structured preview of the posting. '*8)+'<section class="job-description"><p>'+TEXT*2+'</p></section>'
    result=primary_capture(URL,URL,h)
    assert result and len(result[0])>len(TEXT) and result[0].startswith('Build reliable')


def test_legacy_body_without_identity_does_not_gain_fresh_proof(tmp_path):
    s=scanner(tmp_path);h='<h1>Reliability Engineer</h1><p>'+TEXT+'</p>'
    text,_,_=s._remember_detail_html(URL,h,final_url=URL)
    # Existing independently proven generic fallback may still work; the new
    # structured primary reader itself must decline rather than invent identity.
    assert primary_capture(URL,URL,h) is None


def sf(url,body=TEXT):
    return f'<link rel="canonical" href="{url}"><meta itemprop="streetAddress" content="Milan, IT"><span itemprop="description" data-careersite-propertyid="description"><p>{body}</p></span>'


def test_published_successfactors_container_not_limited_to_named_hosts(tmp_path):
    u='https://jobs.newpublisher.invalid/job/Engineer/12345/';s=scanner(tmp_path)
    text,loc,_=s._remember_detail_html(u,sf(u),final_url=u)
    assert text==TEXT.strip()==s.get_captured_jd(u) and capture_proof(s,u,text) and loc=='Milan, IT'


@pytest.mark.parametrize('failure',['canonical','shape','duplicate','unclosed','oversized','marker'])
def test_platform_primary_container_guards(failure):
    u='https://jobs.newpublisher.invalid/job/Engineer/12345/';h=sf(u)
    if failure=='canonical':h=h.replace('rel="canonical"','rel="alternate"')
    if failure=='shape':u=u.replace('/job/','/search/');h=sf(u)
    if failure=='duplicate':h+=sf(u)
    if failure=='unclosed':h=h.replace('</span>','')
    if failure=='oversized':h=sf(u,TEXT*50)
    if failure=='marker':h=h.replace('data-careersite-propertyid="description"','data-preview="description"')
    assert primary_capture(u,u,h) is None


class Response:
    headers={'Content-Type':'text/html; charset=utf-8'}
    def __init__(self,h):self.h=h
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n=-1):return self.h.encode()


def links(start=1,end=6):
    return ''.join(f'<a href="/jobs/{i}">Reliability Engineer {i}</a>' for i in range(start,end))


def test_static_deadline_before_initial_http_is_explicit(monkeypatch):
    monkeypatch.setattr(cs.urllib.request,'urlopen',lambda *a,**k:pytest.fail('deadline HTTP'))
    jobs,note=cs.fetch_static_jobs('https://fixture.invalid/board',deadline=time.monotonic()-1)
    assert not jobs and note.complete is False and not note.needs_dom


def test_static_first_request_is_clamped_to_remaining_deadline(monkeypatch):
    seen=[]
    def fetch(req,timeout,context):seen.append(timeout);return Response(links())
    monkeypatch.setattr(cs.urllib.request,'urlopen',fetch)
    cs.fetch_static_jobs('https://fixture.invalid/board',deadline=time.monotonic()+.5)
    assert 0<seen[0]<=.5


def test_static_self_link_and_content_fragment_are_not_vacancies(monkeypatch):
    u='https://fixture.invalid/jobs/Analyst';h='<a href="'+u+'">Analyst</a><a href="'+u+'#content">Analyst careers</a>'+links()
    monkeypatch.setattr(cs.urllib.request,'urlopen',lambda *a,**k:Response(h))
    jobs,note=cs.fetch_static_jobs(u,min_jobs=1)
    assert len(jobs)==5 and not any(j['job_url'].split('#')[0]==u for j in jobs)


def test_generic_next_uses_bounded_retry_transport(monkeypatch):
    u='https://fixture.invalid/board';calls=[]
    def fetch(req,timeout,context):
        calls.append(req.full_url)
        if req.full_url==u:return Response(links()+'<a rel="next" href="?page=2">Next</a>')
        if calls.count(u+'?page=2')==1:raise TimeoutError('transient read')
        return Response(links(6,8))
    monkeypatch.setattr(cs.urllib.request,'urlopen',fetch)
    jobs,note=cs.fetch_static_jobs(u,deadline=time.monotonic()+10)
    assert len(jobs)==7 and calls.count(u+'?page=2')==2
    assert note.complete is not True  # no independent count


def test_no_default_clean_empty_on_missing_evidence():
    assert completion_status(0,'',[])=='unverified_empty'
    assert completion_status(0,'',['unknown provider'])=='unverified_empty'
    assert completion_status(0,'',['verified empty: publisher returned zero'])=='empty'


def run_crawler(tmp_path,monkeypatch,rows,note):
    seed=tmp_path/'input.csv'
    with seed.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['name','careers_url','provider','target_country','scope_policy']);w.writeheader();w.writerow(dict(name='Fixture',careers_url='https://fixture.invalid/board',provider='auto',target_country='Global',scope_policy='global'))
    monkeypatch.setattr(cs.ProductionScannerConfig,'ENABLE_DETAIL_SCAN',False)
    s=cs.CareerPortalScanner(input_csv=str(seed),output_csv=str(tmp_path/'jobs.csv'),skip_preflight=True,detail_scan=False)
    monkeypatch.setattr(s,'_fetch_provider_jobs',lambda r:([],'no dedicated provider adapter'))
    def static(url,*args,**kwargs):
        assert kwargs['title_validator']('Reliability Engineer',URL)
        return rows,note
    monkeypatch.setattr(cs,'fetch_static_jobs',static)
    monkeypatch.setattr(s,'_get_or_launch_worker_browser',lambda *a,**k:pytest.fail('unnecessary DOM restart'))
    calls=[];original=s.is_valid_job_title
    def title(value,company='',url=''):calls.append((value,company,url));return original(value,company,url)
    monkeypatch.setattr(s,'is_valid_job_title',title)
    s.execute_crawler()
    return list(csv.DictReader((tmp_path/'jobs_scan_log.csv').open(encoding='utf-8-sig'))),calls


def test_real_orchestrator_honours_verified_empty(tmp_path,monkeypatch):
    logs,calls=run_crawler(tmp_path,monkeypatch,[],ListingDiagnostic('publisher zero',complete=True,expected_total=0))
    assert logs[0]['Status']=='empty' and 'verified empty:' in logs[0]['Diagnostics']
    assert ('Reliability Engineer','Fixture',URL) in calls


def test_real_orchestrator_accepts_small_complete_census(tmp_path,monkeypatch):
    rows=[dict(job_url=URL,job_title='Reliability Engineer',location_hint='Milan, Italy')]
    logs,_=run_crawler(tmp_path,monkeypatch,rows,ListingDiagnostic('one published job reconciled',complete=True,expected_total=1))
    assert logs[0]['Jobs Found']=='1' and 'static accepted: 1 kept' in logs[0]['Diagnostics']


def test_real_orchestrator_retains_small_transport_partial(tmp_path,monkeypatch):
    rows=[dict(job_url=URL,job_title='Reliability Engineer',location_hint='Milan, Italy')]
    logs,_=run_crawler(tmp_path,monkeypatch,rows,ListingDiagnostic('listing incomplete: deadline exhausted',complete=False,needs_dom=False))
    assert logs[0]['Status']=='partial' and logs[0]['Jobs Found']=='1'


@pytest.mark.parametrize('title',['Farmacista - Firenze','Farmacista - Varese','Infermiere - Milano','Sviluppatore - Milano'])
def test_actual_role_with_city_is_not_location_metadata(tmp_path,title):
    s=scanner(tmp_path)
    assert s.is_valid_job_title(title,'Employer','https://jobs.fixture.invalid/job/Role/12345/')


@pytest.mark.parametrize('title',['Firenze','Varese','Privacy Policy','Candidatura spontanea','Join our Talent Community'])
def test_role_nouns_do_not_open_navigation_or_general_applications(tmp_path,title):
    s=scanner(tmp_path)
    assert not s.is_valid_job_title(title,'Employer','https://jobs.fixture.invalid/job/Role/12345/')


def test_complete_primary_between_legacy_clip_and_capture_limit_is_not_clipped(tmp_path):
    s=scanner(tmp_path);body=TEXT*16
    assert 8000<len(body)<32000
    text,_,_=s._remember_detail_html(URL,html(text=body),final_url=URL)
    assert text==body.strip()==s.get_captured_jd(URL) and capture_proof(s,URL,text)


def test_scorecard_inventory_preserves_scope_and_queries():
    from packtoworkeu.scanning.career.reliability import inventory
    r=dict(name='A',careers_url='https://fixture.invalid/jobs?q=&country=DE',provider='auto',board_slug='',target_country='Germany',scope_policy='seed_url')
    entries=inventory([r,dict(r,name='Alias'),dict(r,target_country='Global'),dict(r,careers_url=r['careers_url']+'&'),dict(r,enabled='false')])
    assert len(entries)==3 and entries[0]['seed_names']==['A','Alias']


def full_evidence():
    return dict(full_scan_completed=True,listing_complete=True,published_total=2,source_observed=2,accepted=2,
        scan_status='ok',scope_accounted=True,description_reads=2,stored_confirmed=2,ingest_errors=[])


def test_scorecard_requires_full_ingestion_not_readable_sample():
    from packtoworkeu.scanning.career.reliability import assess
    assert assess(full_evidence())=='verified_working'
    e=full_evidence();e.update(full_scan_completed=False,sampled_path_pass=True)
    assert assess(e)=='sampled_only'


@pytest.mark.parametrize('change',[
 {'full_scan_completed':False},{'listing_complete':None},{'listing_complete':False},
 {'stored_confirmed':1},{'description_reads':1},{'scope_accounted':False},
 {'source_observed':3},{'scan_status':'partial'},{'ingest_errors':['failed write']},
 {'block_reason':'HTTP 403'},{'stored_confirmed':True}])
def test_no_false_portal_pass_when_any_gate_missing(change):
    from packtoworkeu.scanning.career.reliability import assess,PASS_STATES
    e=full_evidence();e.update(change)
    assert assess(e) not in PASS_STATES


def test_empty_requires_explicit_completed_zero_evidence():
    from packtoworkeu.scanning.career.reliability import assess
    assert assess({})=='not_fully_audited'
    e=dict(full_scan_completed=True,listing_complete=True,published_total=0,accepted=0,scan_status='empty')
    assert assess(e)!='verified_empty'
    e['verified_empty_evidence']=True
    assert assess(e)=='verified_empty'


def test_scorecard_does_not_drop_blocked_or_unknown_denominator():
    from packtoworkeu.scanning.career.reliability import inventory,scorecard
    configs=inventory([dict(name=str(i),careers_url=f'https://fixture.invalid/{i}') for i in range(100)])
    observations=[dict(full_evidence(),configuration_id=configs[0]['configuration_id'])]
    observations.append(dict(configuration_id=configs[1]['configuration_id'],block_reason='403'))
    rows,summary=scorecard(configs,observations)
    assert len(rows)==100 and summary['verified']==1 and summary['verified_coverage_percent']==1
    assert summary['required_for_90_percent']==90 and summary['required_for_95_percent']==95
    assert not summary['target_90_met'] and not summary['target_95_met']
