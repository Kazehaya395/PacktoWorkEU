"""Production-loader and observed-geography release guards. No network."""
import pytest
from packtoworkeu.core.location_evidence import detail_location

@pytest.mark.parametrize('existing,incoming,country,source,expected',[
    ('Italia','IT','Italy','job_location','Italia'),
    ('Berlin, Germany','DE','Germany','job_location','Berlin, Germany'),
    ('Berlin, Germany','Munich, Germany','Germany','job_location','Munich, Germany'),
    ('Berlin, Germany','IT','Italy','job_location','IT'),
    ('Berlin, Germany','DE','Germany','seed','DE'),
    ('Berlin, Germany','DE','Germany','company_hq','DE'),
    ('Unknown','DE','Germany','none','DE'),
    ('Paris','FR','France','none','Paris'),
    ('Victoria','CA','Canada','none','CA'),
])
def test_detail_country_only_never_erases_proven_place_or_imports_hints(existing,incoming,country,source,expected):
    assert detail_location(existing,incoming,country,source)==expected


def test_incomplete_original_identity_inventory_is_not_a_smaller_verified_set(tmp_path,monkeypatch):
    from packtoworkeu import paths
    from packtoworkeu.core.assisted_workflow import required_postings,reconcile_details
    monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',tmp_path)
    url='https://fixture.invalid/jobs/'
    (tmp_path/'ORIGINAL_career_jobs.csv').write_text('Seed URL,Job URL\n'+url+',https://fixture.invalid/job/123456\n'+url+',\n')
    required=required_postings(dict(access_scope='details',run_id='ORIGINAL',url=url))
    assert required is None
    assert not reconcile_details(required,[dict(url='https://fixture.invalid/job/123456',jd_read_status='readable')])['verified']


@pytest.mark.skipif(__import__('os').environ.get('PACKTOWORKEU_TEST_BROWSER')!='1',reason='real Chromium opt-in')
def test_native_append_wait_obeys_stop_before_the_eight_second_deadline():
    import threading,time
    from playwright.sync_api import sync_playwright
    from packtoworkeu.core import blocked_portals as bp
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page()
            page.set_content('<main><h1>Engineering jobs</h1>'+''.join(
                '<article class="job-card"><h2><a href="https://fixture.invalid/job/platform-engineer-'+str(100000+i)+'">Platform Engineer</a></h2></article>'for i in range(10))+
                '<button id="more" onclick="this.dataset.clicked=1">Load 10 more</button></main>')
            page._assisted_job_validator=CareerPortalScanner(input_csv='unused',skip_preflight=True).is_valid_job_url
            event=threading.Event();page._assisted_cancel_event=event
            timer=threading.Timer(.25,event.set);timer.start();start=time.monotonic()
            try:
                assert bp._native_append_advance(page) is False
                assert event.is_set() and time.monotonic()-start<2
                assert page.locator('#more').get_attribute('data-clicked')=='1'
            finally:timer.cancel();timer.join()
        finally:browser.close()


def test_same_job_security_proof_query_is_not_a_different_posting():
    from packtoworkeu.core.job_identity import posting_identity
    url='https://fixture.invalid/job/d365-business-central-consultant-projectpro/'
    assert posting_identity(url+'?afd_azwaf_tok=LOCAL_FAKE_PROOF')==posting_identity(url)


def test_security_proof_query_is_private_in_diagnostics():
    from packtoworkeu.core.browser_evidence import redact_url
    result=redact_url('https://fixture.invalid/job/123456?afd_azwaf_tok=LOCAL_FAKE_PROOF&country=DE')
    assert 'LOCAL_FAKE_PROOF'not in result and 'country=DE'in result



@pytest.mark.parametrize('proof',['afd_azwaf_tok','__cf_chl_tk','__cf_chl_rt_tk'])
def test_access_proofs_do_not_change_operational_urls_or_scope(proof):
    from packtoworkeu.core.job_identity import posting_identity,portal_queue_key
    from packtoworkeu.core.url_normalizer import normalize_url
    url='https://fixture.invalid/jobs/123456?country=DE&tenant=one&'+proof+'=LOCAL_FAKE#RoleA'
    assert proof in normalize_url(url) and proof in portal_queue_key(url)
    assert posting_identity(url)==posting_identity('https://fixture.invalid/jobs/123456?country=DE&tenant=one#RoleA')
    assert posting_identity(url)!=posting_identity('https://fixture.invalid/jobs/123456?country=DE&tenant=one#RoleB')


@pytest.mark.parametrize('selector',['jobId','pid','req_id','tenant','country','domain','token','job_token','__cf_chl_unknown'])
def test_security_equivalence_never_discards_actual_or_unknown_selectors(selector):
    from packtoworkeu.core.job_identity import posting_identity
    root='https://fixture.invalid/jobs/123456?afd_azwaf_tok=LOCAL&'+selector+'='
    assert posting_identity(root+'one')!=posting_identity(root+'two')
    assert posting_identity(root+'one')!=posting_identity(root.replace('/123456?','/999999?')+'one')


@pytest.mark.parametrize('finish',['normal','cancel','error','dns_retry'])
def test_company_pool_retires_browser_on_its_owner_before_worker_exit(tmp_path,monkeypatch,finish):
    """A scan must not abandon a Playwright driver by closing it cross-thread."""
    from types import SimpleNamespace
    from packtoworkeu.scanning.career import career_scanner as cs
    from packtoworkeu.tests.test_career_browser_launch_cleanup_2026_11_05 import DriverFactory,Browser
    seed=tmp_path/'seed.csv'
    seed.write_text('name,careers_url,provider,target_country,scope_policy\nFixture,https://fixture.invalid/jobs,auto,Global,global\n')
    s=cs.CareerPortalScanner(input_csv=str(seed),output_csv=str(tmp_path/'jobs.csv'),skip_preflight=True)
    class Page:
        closed=False
        def goto(self,*a,**kw):
            if finish=='cancel':s.cancel_event.set()
            if finish=='dns_retry'and len(factory.drivers)==1:
                raise RuntimeError('net::ERR_NAME_NOT_RESOLVED')
            return SimpleNamespace(status=200)
        def wait_for_timeout(self,*a):pass
        def close(self):self.closed=True
    page=Page()
    class Context:
        closed=False
        def new_page(self):return page
        def close(self):self.closed=True
    ctx=Context();browser=Browser();browser.new_context=lambda **kw:ctx
    second_browser=Browser();second_browser.new_context=lambda **kw:ctx
    factory=DriverFactory([browser,second_browser]);monkeypatch.setattr(cs,'sync_playwright',factory)
    if finish=='cancel':
        import threading
        s.cancel_event=threading.Event()
    monkeypatch.setattr(cs,'fetch_static_jobs',lambda *a,**kw:([],'fixture static'))
    monkeypatch.setattr(cs,'install_page_resource_blocking',lambda *_:None)
    monkeypatch.setattr(s,'_fetch_provider_jobs',lambda *_:([],'fixture provider'))
    for name in ('dismiss_initial_blockers','navigate_to_fragment','wait_for_job_cards_to_load','check_iframes','try_increase_page_size'):
        monkeypatch.setattr(s,name,lambda *a,**kw:None)
    monkeypatch.setattr(s,'_bot_challenge_reason',lambda *a,**kw:'')
    monkeypatch.setattr(s,'_direct_listing_pagination_state',lambda *_:{})
    def extract(*a):
        if finish=='error':raise ValueError('fixture worker error')
        return []
    monkeypatch.setattr(s,'extract_visible_jobs',extract)
    s.execute_crawler()
    assert ctx.closed and page.closed
    assert len(factory.drivers)==(2 if finish=='dns_retry'else 1)
    # DriverFactory.stop enforces the original owning thread, just like Sync API.
    assert all(driver.stops==1 for driver in factory.drivers)
    assert browser.closed==1 and not s._worker_browsers_all
    if finish=='dns_retry':assert second_browser.closed==1


@pytest.mark.parametrize('workers',[1,3])
@pytest.mark.parametrize('cancel_queued',[False,True])
def test_pool_owner_cleanup_preserves_warm_reuse_and_joins_cancelled_work(monkeypatch,workers,cancel_queued):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from packtoworkeu.scanning.career import career_scanner as cs
    from packtoworkeu.tests.test_career_browser_launch_cleanup_2026_11_05 import DriverFactory,Browser,scanner
    s=scanner();browsers=[Browser()for _ in range(workers)];factory=DriverFactory(browsers)
    monkeypatch.setattr(cs,'sync_playwright',factory)
    pool=ThreadPoolExecutor(max_workers=workers);ready=threading.Barrier(workers+1);release=threading.Event()
    def work():
        first=s._get_or_launch_worker_browser()
        assert s._get_or_launch_worker_browser()is first
        ready.wait(timeout=3);assert release.wait(3)
    futures=[pool.submit(work)for _ in range(workers)]
    ready.wait(timeout=3)
    queued=[]
    if cancel_queued:
        queued=[pool.submit(lambda:None)for _ in range(workers*2)]
        timer=threading.Timer(.05,release.set);timer.start()
    else:release.set()
    try:
        s._finish_worker_pool(pool,workers,futures+queued)
    finally:
        release.set();pool.shutdown(wait=True,cancel_futures=True)
        if cancel_queued:timer.join()
    assert all(f.done()for f in futures+queued)
    assert not cancel_queued or all(f.cancelled()for f in queued)
    assert len(factory.drivers)==workers and all(d.stops==1 for d in factory.drivers)
    assert all(b.closed==1 for b in browsers)and not s._worker_browsers_all


@pytest.mark.skipif(__import__('os').environ.get('PACKTOWORKEU_TEST_BROWSER')!='1',reason='real Chromium opt-in')
def test_real_company_pool_has_no_connected_browser_after_three_targets(tmp_path,monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from playwright.sync_api import BrowserType,Page
    from packtoworkeu.scanning.career import career_scanner as cs
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            raw=b'<html><body><h1>Careers</h1><p>No open positions at this time.</p></body></html>'
            self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    browsers=[];launch0=BrowserType.launch;wait0=Page.wait_for_timeout
    def launch(self,*a,**kw):
        browser=launch0(self,*a,**kw);browsers.append(browser);return browser
    monkeypatch.setattr(BrowserType,'launch',launch)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda self,ms:wait0(self,min(ms,5)))
    monkeypatch.setattr(cs,'fetch_static_jobs',lambda *a,**kw:([],'fixture browser'))
    monkeypatch.setattr(cs.CareerPortalScanner,'_fetch_provider_jobs',lambda *_:([],'fixture browser'))
    seed=tmp_path/'seed.csv';seed.write_text('name,careers_url,provider,target_country,scope_policy\n'+''.join(f'Fixture{i},http://127.0.0.1:{server.server_port}/careers?n={i},auto,Global,global\n'for i in range(3)))
    s=cs.CareerPortalScanner(input_csv=str(seed),output_csv=str(tmp_path/'jobs.csv'),skip_preflight=True);s.max_workers=2
    try:
        s.execute_crawler()
        assert 1<=len(browsers)<=2  # warm reuse, not one launch per company
        assert all(not b.is_connected()for b in browsers)
        assert not s._worker_browsers_all
    finally:server.shutdown();server.server_close();thread.join(3)
