"""METRO/Bayer field failures: pure evidence and intercepted local browser cases."""
import json,os
from types import SimpleNamespace
import pytest
from sponsorscout.core.job_identity import posting_candidate,split_career_posting,explicit_detail_url,posting_identity
from sponsorscout.core.session_details import SessionDetailEnricher,DetailResult,parse_detail
from sponsorscout.scanning.detail_state import DetailRequestState
from sponsorscout.scanning.response_evidence import stated_job_total,listing_continuation
from sponsorscout.scanning.jd_support import JDSupportDetector
from sponsorscout.core import blocked_portals as bp,browser_evidence as be

BASE='https://talent.fixture.invalid'
URL=BASE+'/careers?pid=600001&domain=fixture.invalid&sort_by=relevance'

@pytest.mark.parametrize('reason',['TimeoutError: waiting for commit','net::ERR_NAME_NOT_RESOLVED'])
def test_current_prefetch_failure_is_not_budget_skip(reason):
    cfg=SimpleNamespace(MAX_DETAIL_SCAN_PER_COMPANY=1500,MAX_DETAIL_SCAN_TOTAL=20000,DETAIL_MIN_PER_COMPANY=1,DETAIL_SCAN_TIME_BUDGET_SEC=900,ENABLE_DETAIL_SCAN=True)
    scanner=SimpleNamespace(config=cfg,_detail_state=DetailRequestState())
    reader=SessionDetailEnricher(None,scanner,'fixture');reader.prime_lookahead([URL]);reader.preferred_origins.add(BASE)
    def prefetch(urls,index):
        scanner._detail_state.register(URL,'fixture')
        assert scanner._detail_state.reserve(URL,reader.policy,browser=True)
        reader._prefetched_results[URL]=reader._finish(URL,DetailResult(status='failed',reason=reason,phase='parallel_navigation',error=reason))
    reader._prefetch_ahead=prefetch
    result=reader.read(URL)
    assert result.status=='failed'and result.error==reason
    assert reader.counts['failed']==1 and reader.counts['budget_skipped']==0
    assert scanner._detail_state.total==1 and not scanner._detail_state.scopes['fixture']['stops']
    assert reader.read(URL).status=='failed'

@pytest.mark.parametrize('text,expected',[
 ('538 open jobs. Apply Now ID: 896548 Job Description',538),
 ('538 available jobs. ID: 896548 Job Details',538),
 ('ID: 896548 Job Description',None),('ID: 896548 Job Details',None),
 ('Showing 1 - 10 of 538',538),('26 Möglichkeiten bei mitzumachen',26),
 ('1.234 Stellenangebote',1234),('12 vacancies',12),('0 open jobs',0),
])
def test_count_is_not_selected_reference(text,expected):assert stated_job_total(text)==expected

def test_reference_is_not_dynamic_continuation():assert not listing_continuation('ID: 896548 Job Description',4)
def test_positions_control_requires_continuation():assert listing_continuation('<button>Show More Positions</button>',10)

@pytest.mark.parametrize('path,title',[
 ('whybayer/benefits-rewards','Your benefits'),('whybayer/development-teamwork','Our culture'),
 ('how-to-join-us','How to join us'),('working-at-bayer/meet_our_employees','Meet our people'),
])
def test_exported_information_links_never_postings(path,title):
    url='https://career.bayer.com/en/career/'+path
    assert not posting_candidate(url,title,lambda _:True)
    assert posting_candidate(url+'?jobid=123456','Benefits Manager',lambda _:True)

@pytest.mark.parametrize('url,valid',[
 (URL,True),('https://other.invalid/careers?pid=600001&domain=fixture.invalid',False),
 (BASE+'/contact?pid=600001&domain=fixture.invalid',False),
 (BASE+'/careers?pid=abc123&domain=fixture.invalid',False),
 (BASE+'/careers?pid=600001',False),
 (BASE+'/careers?pid=600001&domain=invalid',False),
 ('https://talent.fixture.invalid.attacker.invalid/careers?pid=600001&domain=fixture.invalid',False),
])
def test_tenant_bound_native_identity(url,valid):assert split_career_posting(url)is valid

def test_native_postings_distinct_and_not_self_or_page_probe():
    second=URL.replace('600001','600002')
    assert explicit_detail_url(URL) and posting_candidate(URL,'Engineer')
    assert split_career_posting(second,URL)
    assert posting_identity(second)!=posting_identity(URL)
    assert not split_career_posting(second+'&country=DE',URL)
    assert not bp.is_listing_self_link(URL,'/careers')
    assert bp.next_page_url(URL,2)is None

@pytest.mark.parametrize('text,verdict',[
 ('Unser Relocation Services Team betreut den Metro Campus in Düsseldorf und übernimmt vielfältige Dienstleistungen rund um den Gebäudebetrieb.','Unknown'),
 ('Zur Verstärkung unseres Relocation Services Teams suchen wir einen engagierten Hausmeister Facility Service Mitarbeiter.','Unknown'),
 ('Our Relocation Services Team manages the campus. We offer a relocation package to new hires.','Yes'),
 ('Our Relocation Services Team provides relocation assistance to new hires.','Yes'),
 ('Our Relocation Services Team manages the campus. We do not offer relocation assistance.','No'),
])
def test_facility_team_is_not_benefit_but_actual_offers_survive(text,verdict):assert JDSupportDetector().detect(text)['relocation']['verdict']==verdict


def test_repeated_locations_keep_two_distinct_places():
    snapshot={'detail_ready':True,'job_locations':['Düsseldorf, NRW, Germany']*4+['Berlin, Germany'],'primary_text':'Develop reliable applications and maintain customer services. '*12}
    result=parse_detail('',URL,URL,SimpleNamespace(),browser_evidence=snapshot)
    assert result.location=='Düsseldorf, NRW, Germany | Berlin, Germany'

@pytest.fixture
def local_page():
    if os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1':pytest.skip('opt-in intercepted browser fixture')
    from playwright.sync_api import sync_playwright
    with sync_playwright()as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox']);context=browser.new_context()
        context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body='<html></html>'))
        page=context.new_page();page.goto(URL)
        try:yield page
        finally:browser.close()

NATIVE_HTML='''<main><h1>Jobs at Fixture</h1><p>3 open jobs</p><div class="position-cards-container"><div role="list" id="cards"></div><button class="show-more-positions" onclick="grow()">Show More Positions</button></div><div class="position-full-card"><h2 class="position-title"></h2><div class="job-description"></div></div><aside class="similar-jobs"><div class="position-card" role="link"><div class="position-title">Unrelated job</div></div></aside><a href="https://career.bayer.com/en/career/whybayer/benefits-rewards">Your benefits</a></main><script type="application/ld+json" id="schema">{}</script><script>
const jobs=[['600001','Engineer','Düsseldorf, Germany'],['600002','Engineer','Berlin, Germany'],['600003','Analyst','Hamburg, Germany']];let count=0;
function select(i){const j=jobs[i];const u=new URL(location.href);u.searchParams.set('pid',j[0]);history.pushState({},'',u);document.querySelectorAll('.card-selected').forEach(e=>e.classList.remove('card-selected'));document.getElementById('position-card-'+i).parentElement.classList.add('card-selected');document.querySelector('.position-full-card .position-title').innerText=j[1];const description='Build robust customer applications and maintain reliable services. '.repeat(12);document.querySelector('.job-description').innerText=description;document.getElementById('schema').textContent=JSON.stringify({'@type':'JobPosting','url':location.href,'title':j[1],'description':description,'jobLocation':{'address':{'addressLocality':j[2].split(',')[0],'addressCountry':'DE'}}});}
function grow(){const limit=Math.min(count+2,jobs.length);for(;count<limit;count++){let i=count,j=jobs[i],e=document.createElement('div');e.className='position-card';e.setAttribute('role','link');e.onclick=()=>select(i);e.innerHTML='<div class="position-title" id="position-card-'+i+'">'+j[1]+'</div><div class="job-location">'+j[2]+'</div>';document.getElementById('cards').appendChild(e);}if(count===jobs.length)document.querySelector('button').disabled=true;}
grow();select(Number(new URL(location.href).searchParams.get('pid'))-600001);
</script>'''


def test_native_real_clicks_selected_jd_and_append(local_page):
    from sponsorscout.core.split_careers import observed_split_cards
    page=local_page;page.set_content(NATIVE_HTML)
    rows=observed_split_cards(page)
    assert len(rows)==2 and rows[0][0]==rows[1][0]=='Engineer'
    assert len({r[1]for r in rows})==2 and all('pid='in r[1]for r in rows)
    assert page.url==URL
    snap=be.inspect_page(page,validator=lambda _:True)
    assert snap['visible_job_links']==2 and snap['detail_ready']
    assert not any('benefits'in a['url']for a in snap['admitted_anchors'])
    result=parse_detail(page.content(),URL,URL,SimpleNamespace(),browser_evidence=snap)
    assert result.status=='readable' and 'Unrelated job'not in result.text and result.country=='Germany'
    signature=bp._first_listing_signature(page)
    assert not bp.terminal_listing_evidence(page,2,2)
    page.locator('button').click()
    assert bp._first_listing_signature(page)!=signature
    rows=observed_split_cards(page)
    assert len(rows)==3 and len({r[1]for r in rows})==3
    assert bp.terminal_listing_evidence(page,3,3)['kind']=='disabled_next'


def test_zero_height_card_anchor_and_search_form_census(local_page):
    page=local_page
    page.set_content('<main><h1>Aktuelle Stellenangebote</h1><form><a style="display:contents" href="/stellenangebote/engineer?jid=abcdef12"><div>Software Engineer</div></a></form><aside><a href="/stellenangebote/recommended?jid=12345678">Recommended engineer</a></aside><a style="display:none" href="/stellenangebote/hidden?jid=99999999">Hidden job</a></main>')
    snap=be.inspect_page(page,validator=lambda _:False)
    assert snap['visible_job_links']==1 and snap['listing_ready']


def test_visible_disabled_german_more_without_pager(local_page):
    page=local_page;page.set_content('<main><button disabled>Mehr laden</button></main>')
    assert bp._pagination_end_reached(page)
    page.set_content('<main><button style="display:none" disabled>Mehr laden</button></main>')
    assert not bp._pagination_end_reached(page)


def test_browser_404_is_not_posting_identity_mismatch(local_page):
    page=local_page;page.context.route('**/missing',lambda route:route.fulfill(status=404,content_type='text/html',body='<h1>Page not found</h1>'))
    cfg=SimpleNamespace(MAX_DETAIL_SCAN_PER_COMPANY=1500,MAX_DETAIL_SCAN_TOTAL=20000,DETAIL_MIN_PER_COMPANY=1,DETAIL_SCAN_TIME_BUDGET_SEC=900,ENABLE_DETAIL_SCAN=True,DETAIL_BROWSER_TIMEOUT_MS=1000)
    reader=SessionDetailEnricher(page.context,SimpleNamespace(config=cfg,_detail_state=DetailRequestState()),'fixture')
    reader.page=page
    result=reader._browser(BASE+'/missing')
    assert result.status=='failed' and '404'in result.reason and result.phase=='browser_response'


def test_selection_pid_is_not_a_listing_filter_but_other_filters_are():
    from sponsorscout.core.assisted_resume import observed_url_in_scope
    second=URL.replace('600001','600002')
    assert observed_url_in_scope(URL,second)
    assert not observed_url_in_scope(URL,second.replace('relevance','date'))
    assert not observed_url_in_scope(URL,second.replace('fixture.invalid','attacker.invalid',1))


def test_stale_schema_on_spa_selection_is_not_new_jd(local_page):
    from sponsorscout.core.split_careers import observed_split_cards
    page=local_page;page.set_content(NATIVE_HTML)
    # Simulate an app that keeps initial server schema while changing its DOM.
    initial=page.locator('#schema').text_content()
    page.evaluate('''() => {const original=select;window.select=(i)=>{original(i);document.getElementById('schema').textContent=window.savedSchema};}''')
    page.evaluate('(schema)=>window.savedSchema=schema',initial)
    rows=observed_split_cards(page)
    assert len(rows)==2 and len({r[1]for r in rows})==2
    page.locator('#position-card-1').click()
    snap=be.inspect_page(page)
    assert not snap['detail_ready']
    assert parse_detail(page.content(),page.url,page.url,SimpleNamespace(),browser_evidence=snap).status=='empty'


def test_native_local_full_ingestion_pipeline(monkeypatch,tmp_path):
    if os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1':pytest.skip('opt-in intercepted browser fixture')
    import time
    from playwright.sync_api import BrowserType,Browser,Page,APIRequestContext
    from sponsorscout.scanning.career.career_scanner import ProductionScannerConfig
    from sponsorscout.db import database as db
    from sponsorscout import paths
    launch0=BrowserType.launch;context0=Browser.new_context;wait0=Page.wait_for_timeout
    def serve(route):
        assert route.request.url.startswith(BASE+'/')
        body=NATIVE_HTML.replace('grow();select(Number(',"grow();if(new URL(location.href).searchParams.get('pid')==='600003')grow();select(Number(")
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
    path=tmp_path/'native.db';db.initialize(path);conn=db.get_connection(path)
    try:
        bp.run_assisted_ingestion(URL,'Fixture',conn,max_pages=4,clear_wait_s=.1,db_path=str(path))
        report=bp.LAST_INGESTION_REPORT
        assert report['ingested']==3,{k:report.get(k)for k in ('stop_reason','harvest_census','browser_error')}
        assert report['completed'],report['stop_reason']
        rows=conn.execute('SELECT * FROM jobs').fetchall()
        assert len(rows)==3 and len({r['url']for r in rows})==3
        assert all(r['country']=='Germany'for r in rows)
        assert report['jd_coverage']['readable']==3
        from sponsorscout.core.job_identity import read_job_csv
        exports=list(tmp_path.glob('*_career_jobs.csv'));assert len(exports)==1
        assert len(read_job_csv(exports[0]))==3
    finally:conn.close()

@pytest.mark.parametrize('previous,expected',[('failed','failed'),('attempted','unattempted'),('cap','budget_skipped')])
def test_refusal_without_limit_exhaustion_is_not_budget_status(previous,expected):
    cfg=SimpleNamespace(MAX_DETAIL_SCAN_PER_COMPANY=1,MAX_DETAIL_SCAN_TOTAL=20000,DETAIL_MIN_PER_COMPANY=1,DETAIL_SCAN_TIME_BUDGET_SEC=900,ENABLE_DETAIL_SCAN=True)
    scanner=SimpleNamespace(config=cfg,_detail_state=DetailRequestState())
    reader=SessionDetailEnricher(None,scanner,'fixture')
    state=scanner._detail_state;state.register(URL,'fixture')
    if previous=='failed':state.failed.add(URL)
    elif previous=='attempted':state.browser_attempted.add(URL)
    else:state._scope(URL)['requests']=1
    assert not state.reserve(URL,reader.policy,browser=True)
    result=reader._reservation_refusal(URL,browser=True)
    assert result.status==expected
    assert bool(state.scopes.get('fixture',{}).get('stops'))==(previous=='cap')

@pytest.mark.parametrize('extra',['&pid=600002','&domain=other.invalid'])
def test_ambiguous_pid_or_tenant_is_not_explicit(extra):assert not split_career_posting(URL+extra)


def test_disabled_load_more_with_unreconciled_count_not_terminal(local_page):
    page=local_page;page.set_content('<main><h1>538 open jobs</h1><button disabled>Show More Positions</button></main>')
    assert bp.terminal_listing_evidence(page,4,4)is None
    page.set_content('<main><h1>4 open jobs</h1><button disabled>Show More Positions</button></main>')
    assert bp.terminal_listing_evidence(page,4,4)['kind']=='disabled_next'
    page.set_content('<main><div style="opacity:0"><button disabled>Mehr laden</button></div></main>')
    assert not bp._pagination_end_reached(page)


def test_retry_cap_is_budget_not_already_attempted():
    cfg=SimpleNamespace(MAX_DETAIL_SCAN_PER_COMPANY=1,MAX_DETAIL_SCAN_TOTAL=20000,DETAIL_MIN_PER_COMPANY=1,DETAIL_SCAN_TIME_BUDGET_SEC=900,ENABLE_DETAIL_SCAN=True)
    scanner=SimpleNamespace(config=cfg,_detail_state=DetailRequestState())
    reader=SessionDetailEnricher(None,scanner,'fixture');state=scanner._detail_state;state.register(URL,'fixture')
    state.browser_attempted.add(URL);state._scope(URL)['requests']=1
    assert not state.reserve(URL,reader.policy,browser=True,retry=True)
    assert reader._reservation_refusal(URL,browser=True,retry=True).status=='budget_skipped'


def test_proven_saved_jd_location_is_normalized_without_history_write():
    cfg=SimpleNamespace(MAX_DETAIL_SCAN_PER_COMPANY=1500)
    scanner=SimpleNamespace(config=cfg,_detail_state=DetailRequestState())
    reader=SessionDetailEnricher(None,scanner,'fixture')
    reader._saved=lambda url:DetailResult(status='readable',reused=True,text='Maintain reliable applications. '*20,location=' | '.join(['Düsseldorf, NRW, Germany']*4))
    result=reader.read(URL)
    assert result.location=='Düsseldorf, NRW, Germany'
    assert scanner._detail_state.total==0


def test_readable_current_dom_still_overrides_stale_access_status(local_page):
    page=local_page
    body='<main><h1>Backend Engineer</h1><div class="job-description">'+('Build robust applications and reliable services. '*20)+'</div></main>'
    page.context.route('**/cleared-job',lambda route:route.fulfill(status=403,content_type='text/html',body=body))
    cfg=SimpleNamespace(MAX_DETAIL_SCAN_PER_COMPANY=1500,MAX_DETAIL_SCAN_TOTAL=20000,DETAIL_MIN_PER_COMPANY=1,DETAIL_SCAN_TIME_BUDGET_SEC=900,ENABLE_DETAIL_SCAN=True,DETAIL_BROWSER_TIMEOUT_MS=1000)
    reader=SessionDetailEnricher(page.context,SimpleNamespace(config=cfg,_detail_state=DetailRequestState()),'fixture');reader.page=page
    result=reader._browser(BASE+'/cleared-job')
    assert result.status=='readable'and result.browser_evidence['ready']
