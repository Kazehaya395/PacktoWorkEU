"""Consolidated repair regressions: stored failure classes + local-only fixtures."""
import csv,json,os
from pathlib import Path
from types import SimpleNamespace
import pytest
from packtoworkeu.core import blocked_portals as bp,browser_evidence as be
from packtoworkeu.core.job_identity import posting_candidate,explicit_detail_url,metadata_title,read_job_csv,write_job_csv
from packtoworkeu.core.job_metadata import structured_job_locations,title_job_location
from packtoworkeu.core.session_details import parse_detail
from packtoworkeu.scanning.jd_support import JDSupportDetector,detect_blue_card,detect_blue_card_result
from packtoworkeu.scanning.response_evidence import stated_job_total,listing_continuation
from packtoworkeu.scanning.common import completion_status,reconcile_scan_artifacts,final_seed_reference_counts

BASE='https://fixture.invalid'
MOBILITY='Lifecycle & Global Mobility: Profound experience in full HR lifecycle management, including dealing with international relocation and immigration cases, as well as EOR services.'

@pytest.mark.parametrize('suffix',['?jid=e350f99f-359e-4f03-a084-32d1a268b2e3','?jid=abc123'])
def test_explicit_german_job_context(suffix):
    assert explicit_detail_url(BASE+'/stellenangebote/property-manager'+suffix)
    assert posting_candidate(BASE+'/stellenangebote/property-manager'+suffix,'Property Manager',lambda _:False)
    assert not explicit_detail_url(BASE+'/contact'+suffix)

@pytest.mark.parametrize('title',['location_on','Job Type','List all','0 jobs available','No jobs found','Vai alle offerte salvate','Oferty pracy Adecco','INDUSTRIA METALMECCANICA','INDUSTRIA MANIFATTURIERA','SERVIZI/ TERZIARIO','Tag Manager','€ 4000 per month'])
def test_ui_labels_not_used_as_synthetic_postings(title):
    assert metadata_title(title)
    assert not posting_candidate(BASE+'/careers#job=placeholder',title,lambda _:True)
    assert posting_candidate(BASE+'/jobs/123456/engineer',title,lambda _:True)

@pytest.mark.parametrize('title',['Google Tag Manager Specialist','Location Intelligence Engineer','Manufacturing Engineer','Job Type Systems Analyst','Software Engineer'])
def test_real_role_controls_not_blacklisted(title):
    assert not metadata_title(title)
    assert posting_candidate(BASE+'/jobs/123456-engineer',title)


def test_talent_community_is_not_a_posting():
    # Removed from the role controls 2026-11-09: user decision treats
    # talent-pool/community postings as generic-application noise.
    assert not metadata_title('Talent Community')
    assert not posting_candidate(BASE+'/jobs/123456-engineer','Talent Community')

@pytest.mark.parametrize('html,expected',[('<h1>26 Möglichkeiten bei mitzumachen</h1>',26),('<h1>1.234 Stellenangebote</h1>',1234),('<h1>3 offene Stellen</h1>',3),('<p>26 Möglichkeiten zur Weiterbildung</p>',None),('<h1>2026 Career event</h1>',None)])
def test_observed_totals(html,expected):assert stated_job_total(html)==expected

def test_german_more_is_continuation_not_complete():
    assert listing_continuation('<button>Mehr laden</button>',5)
    assert not listing_continuation('<button disabled>Mehr laden</button>',5)

@pytest.mark.parametrize('field',['visa','relocation'])
def test_hr_experience_is_not_employee_support(field):
    d=JDSupportDetector();assert d.detect(MOBILITY)[field]['verdict']=='Unknown'

@pytest.mark.parametrize('offer,field',[('We offer visa sponsorship for this role.','visa'),('We offer a relocation bonus.','relocation'),('We do not offer visa sponsorship.','visa')])
def test_independent_offer_or_refusal_survives(offer,field):
    expected='No'if 'not offer'in offer else'Yes'
    assert JDSupportDetector().detect(MOBILITY+' '+offer)[field]['verdict']==expected

@pytest.mark.parametrize('text,verdict',[('We support the EU Blue Card.','Yes'),('We do not offer the EU Blue Card.','No'),('EU Blue Card eligibility requirements.','Unknown'),('We support relocation.','Unknown')])
def test_blue_card_evidence_is_selected_by_decision(text,verdict):
    detector=JDSupportDetector();v,e=detect_blue_card_result(detector,text)
    assert v==verdict and detect_blue_card(detector,text)==verdict
    assert bool(e)==(verdict!='Unknown')
    if e:assert e in text

@pytest.mark.parametrize('address',[{'addressLocality':'Hamburg','addressRegion':'HH','addressCountry':{'@type':'Country','name':'Germany'}},[{'addressLocality':'Hamburg','addressCountry':'DE'},{'addressLocality':'Berlin','addressCountry':'DE'}],'invalid'])
def test_structured_address_shapes(address):
    locations=structured_job_locations({'jobLocation':{'address':address}})
    if isinstance(address,str):assert locations==[]
    else:assert locations and 'Hamburg'in locations[0]

def test_multiple_structured_locations_preserved():
    assert structured_job_locations({'jobLocation':[{'address':{'addressLocality':'Hamburg','addressCountry':'DE'}},{'address':{'addressLocality':'Berlin','addressCountry':'DE'}}]})==['Hamburg, DE','Berlin, DE']

def test_title_location_requires_explicit_country():
    assert title_job_location('Consultant in Düsseldorf, DEU | PwC',{'DEU':'Germany'})=='Düsseldorf, Germany'
    assert not title_job_location('Consultant in the team | Employer')

def test_real_detail_reader_preserves_primary_location():
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    scanner=CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True)
    url=BASE+'/job/2681/consultant'
    job={'@type':'JobPosting','url':url,'description':'Build reliable systems and support a strong engineering team. '*20,'jobLocation':{'address':{'addressLocality':'Hamburg','addressCountry':{'name':'Germany'}}}}
    result=parse_detail('<script type="application/ld+json">'+json.dumps(job)+'</script>',url,url,scanner)
    assert result.status=='readable'and result.location=='Hamburg, Germany'and result.country=='Germany'

class OffsetPage:
    url=BASE+'/search-results?country=DE&from=370'
    _assisted_nav_status=200
    _assisted_failed_responses=[]

def offset_args():
    prev=dict(stated_total=370,forward_page_links=[OffsetPage.url])
    rows=[dict(url=BASE+'/search-results?country=DE&from=350',page=36,parsed_rows=10,stated_total=370),dict(url=BASE+'/search-results?country=DE&from=360',page=37,parsed_rows=10,stated_total=370)]
    return prev,rows

def clean_response():
    return dict(status=200,reason='',html='<h1>Search results</h1>',title='Search results',rendered=dict(headings=['Search results'],visible_job_links=0,failed_data_responses=[],document_error='',rendered_unextractable=False))

def test_empty_observed_offset_end(monkeypatch):
    monkeypatch.setattr(bp,'_page_response_evidence',lambda p:clean_response())
    prev,rows=offset_args();assert bp.reconciled_offset_end(OffsetPage(),370,prev,rows,True)['kind']=='observed_offset_boundary'

@pytest.mark.parametrize('case',['unmet','uncounted','unstable','not_observed','bad_offset','access','error','failed_data','has_jobs','not_listing'])
def test_empty_offset_safety_controls(monkeypatch,case):
    p=OffsetPage();prev,rows=offset_args();response=clean_response();observed=370;complete=True
    if case=='unmet':observed=360
    elif case=='uncounted':complete=False
    elif case=='unstable':rows[0]['stated_total']=380
    elif case=='not_observed':prev['forward_page_links']=[]
    elif case=='bad_offset':p.url=p.url.replace('370','371')
    elif case=='access':response['reason']='Access denied'
    elif case=='error':response['rendered']['document_error']='404'
    elif case=='failed_data':response['rendered']['failed_data_responses']=[{'http_status':500}]
    elif case=='has_jobs':response['rendered']['visible_job_links']=1
    elif case=='not_listing':response.update(title='Home');response['rendered']['headings']=['Home']
    monkeypatch.setattr(bp,'_page_response_evidence',lambda p:response)
    assert bp.reconciled_offset_end(p,observed,prev,rows,complete)is None

@pytest.mark.parametrize('diag',['workday API failed: JSONDecodeError: invalid body','listing incomplete: no data'])
def test_failed_discovery_not_verified_empty(diag):assert completion_status(0,'',[diag])=='error'

def test_ordinary_empty_unchanged():assert completion_status(0,'',['API returned 0 jobs'])=='empty'

@pytest.mark.parametrize('explicit',[True,False])
def test_same_name_route_attribution(tmp_path,explicit):
    main,log,q=[tmp_path/(x+'.csv')for x in ('main','log','quarantine')]
    source=BASE+'/working';job={'Run ID':'RUN','Seed Name':'Employer','Company Name':'Employer','Job URL':BASE+'/job/123456/role','Target Country':'Germany'}
    if explicit:job['Seed URL']=source
    write_job_csv(main,[job],list(job));write_job_csv(q,[],list(job))
    rows=[{'Run ID':'RUN','Seed Name':'Employer','Company':'Employer','Seed URL':BASE+'/old','Target Country':'Germany','Jobs Found':0,'Error':'blocked','Diagnostics':''},{'Run ID':'RUN','Seed Name':'Employer','Company':'Employer','Seed URL':source,'Target Country':'Germany','Jobs Found':1,'Error':'','Diagnostics':''}]
    write_job_csv(log,rows,list(rows[0]));reconcile_scan_artifacts(log,main,q,'RUN')
    rows=read_job_csv(log);assert [int(r['Jobs Retained'])for r in rows]==[0,1]

def test_baseline_counts_final_source_references(tmp_path):
    p=tmp_path/'main.csv';rows=[{'Seed Name':'A; B','Run ID':'RUN'},{'Seed Name':'A','Run ID':'RUN'},{'Seed Name':'C','Run ID':'OLD'}];write_job_csv(p,rows,list(rows[0]))
    assert final_seed_reference_counts(p,'RUN')=={'A':2,'B':1}

@pytest.fixture
def local_context():
    if os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1':pytest.skip('opt-in local browser fixtures')
    from playwright.sync_api import sync_playwright
    with sync_playwright()as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);ctx=browser.new_context()
        try:yield ctx
        finally:ctx.close();browser.close()

def test_local_metro_five_anchors_and_appended_batch(local_context):
    ctx=local_context
    cards=''.join(f'<a href="/stellenangebote/engineer-{n}?jid={n}">Software Engineer {n}</a>'for n in range(5))
    ctx.route('**/*',lambda r:r.fulfill(content_type='text/html; charset=utf-8',body='<main><h1>26 Möglichkeiten bei mitzumachen</h1><article>'+cards+'</article><nav class="pagination"><button onclick="document.querySelector(\'article\').insertAdjacentHTML(\'beforeend\',\'<a href=/stellenangebote/new?jid=6>New Engineer</a>\');this.disabled=true">Mehr laden</button></nav></main>'))
    p=ctx.new_page();p.goto(BASE+'/stellenangebote');be.watch_page_responses(p)
    links,_=be.admitted_job_links(p);assert len(links)==5
    p.locator("button:has-text('Mehr laden')").click();links,_=be.admitted_job_links(p);assert len(links)==6
    assert bp._pagination_end_reached(p)


def test_local_explicit_location_not_neighboring_role(local_context):
    ctx=local_context
    ctx.route('**/*',lambda r:r.fulfill(content_type='text/html; charset=utf-8',body='<main><h1>Property Manager</h1><span class="job-location">Düsseldorf, NRW, Germany</span><div class="job-description">'+('Manage real property and coordinate tenancy processes. '*20)+'</div></main><aside><span class="job-location">Paris, France</span></aside>'))
    p=ctx.new_page();p.goto(BASE+'/jobs/123456/manager');e=be.inspect_page(p,kind='detail');assert e['job_locations']==['Düsseldorf, NRW, Germany']
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    result=parse_detail(p.content(),p.url,p.url,CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True),browser_evidence=e)
    assert result.location=='Düsseldorf, NRW, Germany'and result.country=='Germany'

@pytest.mark.parametrize('verdict,text',[('Yes','We support the EU Blue Card.'),('No','We do not offer the EU Blue Card.'),('Unknown','We offer learning opportunities.')])
def test_ats_row_exports_same_decisive_blue_sentence(verdict,text):
    from packtoworkeu.scanning.ats.ats_scanner import ATSScanner
    s=ATSScanner(seed_file='__unused__.csv');target=dict(name='Fixture',source_type='direct_employer',url=BASE+'/jobs',scope_policy='global',industry='',ats_type='fixture')
    row=s.make_row(target,'Software Engineer',BASE+'/jobs/123456-engineer','Berlin, Germany','Berlin, Germany','Full-time',text,'fixture')
    assert row['EU Blue Card']==verdict
    assert bool(row['Blue Card Evidence'])==(verdict!='Unknown')
    if row['Blue Card Evidence']:assert row['Blue Card Evidence'] in text


def test_local_complete_assisted_append_and_metadata_pipeline(monkeypatch,tmp_path):
    """Full actual browser→card→detail→SQLite→CSV, all network calls intercepted."""
    if os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1':pytest.skip('opt-in local browser fixtures')
    from playwright.sync_api import BrowserType,Browser,BrowserContext,Page,APIRequestContext
    from packtoworkeu.scanning.career.career_scanner import ProductionScannerConfig
    from packtoworkeu.db import database as db
    from packtoworkeu import paths
    import time
    launch0=BrowserType.launch;context0=Browser.new_context;wait0=Page.wait_for_timeout
    requested=[]
    def serve(route):
        url=route.request.url;assert url.startswith(BASE+'/');requested.append(url)
        if '/stellenangebote/engineer-'in url:
            node={'@type':'JobPosting','url':url,'title':'Software Engineer','description':'Build reliable services and improve automated tests. '*20,'jobLocation':{'address':{'addressLocality':'Düsseldorf','addressRegion':'NRW','addressCountry':'Germany'}}}
            body='<h1>Software Engineer</h1><script type="application/ld+json">'+json.dumps(node)+'</script>'
        else:
            body='''<main><h1>26 Möglichkeiten bei mitzumachen</h1><article id="allcards"></article><nav class="pagination"><button id="more" onclick="grow()">Mehr laden</button></nav></main><script>
            let count=0;function grow(){for(let i=0;i<5 && count<26;i++){count++;document.querySelector('#allcards').insertAdjacentHTML('beforeend','<div class="result"><h3><a href="/stellenangebote/engineer-'+count+'?jid='+count+'">Software Engineer '+count+'</a></h3></div>');}if(count===26)document.querySelector('#more').disabled=true;}grow();</script>'''
        route.fulfill(status=200,content_type='text/html; charset=utf-8',body=body)
    def context(self,*a,**k):
        ctx=context0(self,*a,**k);ctx.route('**/*',serve);return ctx
    monkeypatch.setattr(BrowserType,'launch',lambda self,*a,**k:launch0(self,headless=True,args=['--no-sandbox']))
    monkeypatch.setattr(Browser,'new_context',context)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda self,ms:wait0(self,min(ms,20)))
    monkeypatch.setattr(APIRequestContext,'get',lambda self,url,**kw:SimpleNamespace(status=403,url=url,text=lambda:'<h1>Access denied</h1>',dispose=lambda:None))
    monkeypatch.setattr(bp,'time',SimpleNamespace(monotonic=time.monotonic,sleep=lambda _:None))
    monkeypatch.setattr(ProductionScannerConfig,'ENABLE_DETAIL_SCAN',True)
    monkeypatch.setattr(ProductionScannerConfig,'ASSISTED_DETAIL_TABS',1)
    monkeypatch.setattr(paths,'ensure_scan_output_dir',lambda:tmp_path)
    path=tmp_path/'fixture.db';db.initialize(path);conn=db.get_connection(path)
    try:
        bp.run_assisted_ingestion(BASE+'/stellenangebote','Fixture',conn,max_pages=10,clear_wait_s=.1,db_path=str(path))
        report=bp.LAST_INGESTION_REPORT
        assert report['ingested']==26,{k:report.get(k) for k in ('stop_reason','harvest_census','failure_response','browser_error')}
        rows=conn.execute('SELECT * FROM jobs').fetchall();assert len(rows)==26
        assert all(r['location']=='Düsseldorf, NRW, Germany'and r['country']=='Germany'for r in rows)
        exports=list(tmp_path.glob('*_career_jobs.csv'));assert len(exports)==1
        exported=read_job_csv(exports[0]);assert len(exported)==26
        assert all(r['location']=='Düsseldorf, NRW, Germany'for r in exported)
        assert report['completed'],report
        assert len(requested)>=27
    finally:conn.close()

@pytest.mark.parametrize('url',[BASE,BASE+'/go/Engineering/3605900',BASE+'/lp/Join-Our-Community/abcdef',BASE+'/en/careers/1104-employer/profile',BASE+'/en/careers/jobs_by_region',BASE+'/@inside.employer'])
def test_structural_navigation_rejected_even_with_permissive_adapter(url):
    from packtoworkeu.core.job_identity import administrative_action
    assert administrative_action(url)
    assert not posting_candidate(url,'Navigation label',lambda _:True)


def test_static_cap_with_real_forward_href_requires_dom(monkeypatch):
    from packtoworkeu.scanning.career import career_scanner as cs
    def html(n):
        return '<main>'+''.join(f'<a href="/jobs/{n*10+i}/software-engineer">Software Engineer {n*10+i}</a>'for i in range(3))+f'<a rel="next" href="?page={n+1}">Next</a></main>'
    class Response:
        headers={'Content-Type':'text/html; charset=utf-8'}
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,*a):return html(1).encode()
    monkeypatch.setattr(cs.urllib.request,'urlopen',lambda *a,**k:Response())
    from urllib.parse import urlsplit,parse_qs
    calls=[]
    def fetch(url,*a):
        n=int(parse_qs(urlsplit(url).query)['page'][0]);calls.append(n);return html(n)
    monkeypatch.setattr(cs,'_static_fetch_html',fetch)
    monkeypatch.setattr(cs,'_STATIC_MAX_PAGES',12)
    jobs,diag=cs.fetch_static_jobs(BASE+'/jobs',min_jobs=1,url_validator=lambda u:'/jobs/'in u)
    assert len(jobs)==36 and calls[-1]==12
    assert diag.needs_dom and diag.complete is False and 'forward page remains'in str(diag)

@pytest.mark.parametrize('source,target,expected',[
 ('https://jobs.workinlithuania.com/?sort=-created_at','https://www.cvbankas.lt/support-engineer-vilniuje/1-12345678',True),
 ('https://other.invalid/jobs','https://www.cvbankas.lt/support-engineer-vilniuje/1-12345678',False),
 ('https://jobs.workinlithuania.com/','https://www.cvbankas.lt/company',False),
 ('https://jobs.workinlithuania.com/','https://attacker.invalid/support/1-12345678',False),
 ('https://jobs.workinlithuania.com/','https://www.cvbankas.lt.attacker.invalid/support/1-12345678',False),
 ('https://jobs.workinlithuania.com/','https://secret@www.cvbankas.lt/support/1-12345678',False),
])
def test_external_posting_is_bound_to_selected_observed_board(source,target,expected):
    from packtoworkeu.core.job_identity import source_bound_external_posting
    assert source_bound_external_posting(target,source)is expected
    if expected:
        from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
        scanner=CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True);scanner._current_seed_url=source
        assert scanner.is_valid_job_url(target)
        assert posting_candidate(target,'Support Engineer',scanner.is_valid_job_url)

@pytest.mark.parametrize('label',['INDUSTRIA ALIMENTARE','INDUSTRIA CHIMICA/ CHIMICA-FARMACEUTICA','INDUSTRIA CARTOTECNICA / GRAFICA','INDUSTRIA TESSILE'])
def test_sector_only_taxonomy_is_not_a_role(label):assert metadata_title(label)

def test_sector_prefixed_real_occupation_survives():assert not metadata_title('INDUSTRIA ALIMENTARE OPERATORE')

def test_title_only_noise_check_does_not_treat_missing_url_as_homepage():
    assert not bp.is_noise_cta_title('Software Engineer 1')
    assert bp.is_noise_cta_title('Create job alert')

@pytest.mark.parametrize('text,field',[
 ('We offer visa sponsorship supported by our experience in global mobility.','visa'),
 ('We provide relocation assistance drawing on experience in immigration cases.','relocation'),
])
def test_employer_offer_not_erased_by_experience_word(text,field):
    assert JDSupportDetector().detect(text)[field]['verdict']=='Yes'

def test_country_only_schema_keeps_explicit_city_title_for_same_country():
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    scanner=CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True)
    url=BASE+'/job/2681/consultant';job={'@type':'JobPosting','url':url,'description':'Build reliable distributed services. '*20,'jobLocation':{'address':{'addressCountry':'DEU'}}}
    result=parse_detail('<script type="application/ld+json">'+json.dumps(job)+'</script>',url,url,scanner,title='Consultant in Düsseldorf, DEU | Employer')
    assert result.location=='Düsseldorf, Germany'and result.country=='Germany'
