"""Offline regressions: primary form cards, terminal evidence, exits and polarity."""
from types import SimpleNamespace
import pytest
from packtoworkeu.core import blocked_portals as bp,browser_evidence as be
from packtoworkeu.scanning.jd_support import JDSupportDetector
from packtoworkeu.tests.test_primary_sections_native_2026_11_01 import page
from packtoworkeu.tests.test_finishing_2026_11_01 import native_board

@pytest.mark.parametrize('prefix',[
    'Geen cao en mogelijkheden om ',
    'No company car and we offer ',
    'Keine Kantine und wir bieten ',
    'No company car; we provide ',
])
def test_independent_offer_not_negated_by_unrelated_benefit(prefix):
    d=JDSupportDetector()
    result=d.sentence_verdict(prefix+'relocation assistance.',d.RELOCATION_CONCEPTS)
    assert result and result[0]!='No',result


def test_reported_dutch_tax_ruling_is_not_a_refusal():
    d=JDSupportDetector()
    sentence='Geen cao en mogelijkheden om de 30%-regeling toe te passen, indien je hiervoor in aanmerking komt.'
    assert d.sentence_verdict(sentence,d.RELOCATION_CONCEPTS)[0]=='Unknown'

@pytest.mark.parametrize('text,concept',[
    ('We cannot provide relocation assistance and visa sponsorship.','visa'),
    ('No relocation assistance and visa sponsorship.','visa'),
    ('No relocation assistance is available.','relocation'),
    ('Geen ondersteuning bij verhuizing.','relocation'),
    ('Geen cao en geen ondersteuning bij verhuizing.','relocation'),
    ('We provide lunch; no relocation assistance is available.','relocation'),
])
def test_shared_and_explicit_denials_remain_negative(text,concept):
    d=JDSupportDetector();regex=d.VISA_CONCEPTS if concept=='visa'else d.RELOCATION_CONCEPTS
    assert d.sentence_verdict(text,regex)[0]=='No'


def test_readable_jd_does_not_acquire_support():
    result=JDSupportDetector().detect('Responsibilities: Develop software. Requirements: Testing experience. '*8)
    assert result['visa']['verdict']=='Unknown' and result['relocation']['verdict']=='Unknown'


def test_primary_native_cards_inside_search_form_share_terminal_proof(page):
    # DOM URL attributes are explicit; no title-derived URL or onclick parsing.
    page.set_content('''<main><form><h1>3 Jobs</h1><div id="list">
    <div class="card-job" data-url="/jobs/123451/engineer">Engineer One</div>
    <div class="vacancy-card" data-url="/jobs/123452/engineer">Engineer Two</div>
    </div><button type="button" onclick="document.querySelector('#list').insertAdjacentHTML('beforeend','<div class=card-job data-url=/jobs/123453/engineer>Engineer Three</div>');this.remove()">Load more</button></form></main>''')
    links,meta=be.admitted_job_links(page)
    assert len(links)==2 and meta['ready']
    assert meta['census_stages']['admitted']==2
    assert bp._native_append_advance(page) is True
    links,meta=be.admitted_job_links(page)
    assert len(links)==3
    proof=bp.reconciled_native_append_end(page,3,True,[a['url']for a in links])
    assert proof and proof['kind']=='native_append_reconciled'

@pytest.mark.parametrize('form',[
    '<form><button type="button">Load more</button></form>',
    '<form><input type="email"><button type="button">Load more</button></form>',
])
def test_form_control_cannot_borrow_primary_cards_outside_its_form(page,form):
    page.set_content('<main><article><a href="/jobs/123451/engineer">Engineer One</a></article><article><a href="/jobs/123452/engineer">Engineer Two</a></article>'+form+'</main>')
    assert bp._native_append_advance(page) is None


def test_admitted_census_excludes_current_listing_self_link(page):
    page.goto('https://jobs.fixture.invalid/search')
    page.set_content('<main><a href="/search">Job search</a></main>')
    snapshot=be.inspect_page(page,validator=lambda _:True)
    assert not snapshot['visible_job_links'] and not snapshot['listing_ready']
    assert snapshot['census_rejections'][0]['stage']=='self_listing'


def test_bounded_census_rejections_do_not_leak_tokens(page):
    page.set_content('<main>'+''.join('<a href="/privacy?token=SECRET">Privacy</a>'for _ in range(20))+'</main>')
    snapshot=be.public_snapshot(be.inspect_page(page))
    assert len(snapshot['census_rejections'])==8
    assert 'SECRET'not in str(snapshot)

@pytest.mark.parametrize('failed,blocked',[
    ({'url':'https://metrics.fixture.invalid/ccm/collect?en=page_view','resource_type':'fetch'},False),
    ({'url':'https://metrics.fixture.invalid/rmkt/collect/123/?en=gtag.config','resource_type':'xhr'},False),
    ({'url':'https://jobs.fixture.invalid/api/jobs','resource_type':'fetch'},True),
    ({'url':'https://metrics.fixture.invalid/collect?en=job_results','resource_type':'fetch'},True),
    ({'url':'https://metrics.fixture.invalid/collect?en=page_view','resource_type':'document'},True),
    ({'url':'https://metrics.fixture.invalid/unknown','resource_type':'fetch'},True),
])
def test_only_explicit_event_collection_failures_are_nonblocking(failed,blocked):
    assert bool(bp._blocking_listing_failures([failed]))==blocked


def test_telemetry_failure_does_not_replace_native_terminal_proof(page):
    urls=native_board(page)
    failure={'url':'https://metrics.fixture.invalid/ccm/collect?en=page_view','resource_type':'fetch','error':'net::ERR_ABORTED'}
    page._assisted_failed_responses=[failure]
    assert bp.reconciled_native_append_end(page,len(urls),True,urls)
    del page._ss_native_append_proof
    assert not bp.reconciled_native_append_end(page,len(urls),True,urls)


def test_detail_wall_reports_detail_not_listing(page):
    page.set_content('<title>Just a moment...</title><div id="cf-challenge-running">Checking your browser</div>')
    snapshot=be.inspect_page(page,kind='detail')
    assert 'job detail'in snapshot['reason'] and 'job list;'not in snapshot['reason']

@pytest.mark.parametrize('closed,available,failures,expected',[
    (True,False,0,'browser closed'),
    (False,False,0,'observation unavailable'),
    (False,True,2,'document read failures'),
    (False,True,0,'deadline'),
])
def test_verification_exit_does_not_conflate_closure_observation_deadline(monkeypatch,closed,available,failures,expected):
    page=SimpleNamespace(is_closed=lambda:closed)
    monkeypatch.setattr(bp,'inspect_page',lambda *a,**kw:{'reason':'wall'}if available else None)
    assert expected in bp._verification_exit_reason(page,read_failures=failures)

@pytest.mark.parametrize('url,expected',[
    ('https://jobs.fixture.invalid/jobs/123451/contact-center-specialist',False),
    ('https://jobs.fixture.invalid/jobs/123451/privacy-engineer',False),
    ('https://jobs.fixture.invalid/contact',True),
    ('https://jobs.fixture.invalid/privacy',True),
])
def test_harvest_does_not_override_strong_posting_identity_with_substring_blocklist(url,expected):
    assert bp._harvest_utility_url(url)==expected

@pytest.mark.parametrize('mode',['role_word','unharvestable','native_form','verification_deadline','observation_unavailable'])
def test_actual_single_link_harvest_uses_shared_identity_and_no_false_recovery(monkeypatch,tmp_path,mode):
    import os,time
    if os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1':pytest.skip('opt-in intercepted browser')
    from playwright.sync_api import BrowserType,Browser,Page,APIRequestContext
    from packtoworkeu.db import database as db
    from packtoworkeu import paths
    from packtoworkeu.scanning.career.career_scanner import ProductionScannerConfig
    launch0=BrowserType.launch;context0=Browser.new_context;wait0=Page.wait_for_timeout
    root='https://jobs.fixture.invalid/search'
    job='https://jobs.fixture.invalid/jobs/123451/contact-center-specialist'
    title='Contact Center Specialist'if mode=='role_word'else'Contact us'
    listing='<main><h1>Jobs</h1><article><a href="'+job+'">'+title+'</a></article></main>'
    if mode in ('verification_deadline','observation_unavailable'):
        listing='<title>Just a moment...</title><div id=cf-challenge-running>Checking your browser</div>'
    if mode=='observation_unavailable':monkeypatch.setattr(bp,'inspect_page',lambda *a,**kw:None)
    if mode=='native_form':
        listing='''<main><form><h1>3 Jobs</h1><div id="list">
        <div class="card-job" data-url="/jobs/123451/engineer">Engineer One</div>
        <div class="vacancy-card" data-url="/jobs/123452/engineer">Engineer Two</div>
        </div><button type="button" onclick="document.querySelector('#list').insertAdjacentHTML('beforeend','<div class=card-job data-url=/jobs/123453/engineer>Engineer Three</div>');this.remove()">Load more</button></form></main>'''
    def serve(route):
        assert route.request.url.startswith('https://jobs.fixture.invalid/')
        body=listing if route.request.url==root else '<main><h1>Contact Center Specialist</h1><div class="job-description">'+('Responsibilities: Serve clients. Requirements: Relevant experience. '*12)+'</div></main>'
        route.fulfill(status=200,content_type='text/html',body=body)
    def context(self,*a,**kw):
        ctx=context0(self,*a,**kw);ctx.route('**/*',serve);return ctx
    monkeypatch.setattr(BrowserType,'launch',lambda self,*a,**kw:launch0(self,headless=True,args=['--no-sandbox']))
    monkeypatch.setattr(Browser,'new_context',context)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda self,ms:wait0(self,min(ms,10)))
    monkeypatch.setattr(APIRequestContext,'get',lambda self,url,**kw:SimpleNamespace(status=403,url=url,text=lambda:'<h1>Access denied</h1>',dispose=lambda:None))
    monkeypatch.setattr(bp,'time',SimpleNamespace(monotonic=time.monotonic,sleep=lambda _:None))
    monkeypatch.setattr(ProductionScannerConfig,'ENABLE_DETAIL_SCAN',True)
    monkeypatch.setattr(ProductionScannerConfig,'ASSISTED_DETAIL_TABS',1)
    monkeypatch.setattr(paths,'ensure_scan_output_dir',lambda:tmp_path)
    def forbidden(*a,**kw):raise AssertionError('rendered rejected link must not be called recovered by reload')
    if mode=='unharvestable':monkeypatch.setattr(bp,'_recover_blank_page',forbidden)
    path=tmp_path/'single.db';db.initialize(path);conn=db.get_connection(path)
    try:
        bp.run_assisted_ingestion(root,'Fixture',conn,max_pages=4 if mode=='native_form'else 1,clear_wait_s=.1,db_path=str(path))
        report=bp.LAST_INGESTION_REPORT
        assert report['ingested']==({'role_word':1,'unharvestable':0,'native_form':3,'verification_deadline':0,'observation_unavailable':0}[mode]),report['stop_reason']
        assert report['completed']==(mode=='native_form'),report['stop_reason']
        if mode in ('verification_deadline','observation_unavailable'):
            assert ('deadline'if mode=='verification_deadline'else'observation unavailable')in report['stop_reason']
            assert report['verification_exit']['configured_wait_seconds']==.1
        elif mode=='unharvestable':
            assert 'harvest did not accept'in report['stop_reason']
            assert 'admitted sample='in report['stop_reason']
        else:assert report['jd_coverage']['readable']==(3 if mode=='native_form'else 1)
    finally:conn.close()


def test_excluded_dom_candidates_have_bounded_sanitized_stage_diagnostics(page):
    page.set_content('''<main><div class="unknown" data-url="/jobs/123451/engineer?token=SECRET">Engineer</div>
    <a hidden href="/jobs/123452/engineer?token=SECRET">Hidden</a></main>
    <aside><a href="/jobs/123453/engineer?token=SECRET">Related</a></aside>''')
    snapshot=be.public_snapshot(be.inspect_page(page))
    stages={item['stage']for item in snapshot['census_excluded']}
    assert stages=={'unsupported_card_shape','not_visible','secondary_context'}
    assert 'SECRET'not in str(snapshot)
