"""Refusals are not verification; bounded rate-limit retention; honest empties."""
from __future__ import annotations
import csv
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from packtoworkeu.scanning.response_evidence import is_plain_http_refusal, timeout_listing_error
from packtoworkeu.scanning.career.http_schedule import HttpSchedule
from packtoworkeu.scanning.detail_state import DetailRequestState

TEXT = 'Design reliable services, write automated tests, collaborate with engineers and document production changes. ' * 10
PLAIN = 'HTTP {code} access blocked (human verification not confirmed)'
WALL = '<title>Just a moment...</title><h1>Verify you are human</h1><div id="cf-challenge-running"></div>'


def scanner():
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    s = CareerPortalScanner(input_csv='unused',skip_preflight=True)
    s._tls.detail_scope = 'https://fixture.invalid/jobs'
    return s


@pytest.mark.parametrize('reason,status,plain', [
    (PLAIN.format(code=403),403,True),
    (PLAIN.format(code=429),429,True),
    (PLAIN.format(code=401),401,False),
    ('Cloudflare challenge page served instead of the job detail text',403,False),
    ('Client challenge interstitial (human verification not confirmed)',403,False),
    ('verification unresolved: '+PLAIN.format(code=403),403,False),
])
def test_only_status_only_refusals_qualify(reason,status,plain):
    assert is_plain_http_refusal(reason,status) is plain


def test_two_generic_403s_do_not_close_browser_or_condemn_new_urls():
    s=scanner()
    for i in range(2):
        url=f'https://fixture.invalid/job/{i}'
        s._note_detail_failure(url,PLAIN.format(code=403),blocked=True,transport='HTTP',status=403)
        assert not s._detail_url_blocked(url)
        assert s._detail_can_browser(url)
    fresh='https://fixture.invalid/job/new'
    assert s._detail_host_unavailable(fresh)  # bounded HTTP cooldown, not unlimited retries
    assert not s._detail_host_unavailable(fresh,browser=True)
    assert s._detail_can_browser(fresh)
    assert not s._detail_blocked_hosts and not s._detail_security_failures


@pytest.mark.parametrize('status',[403,429])
def test_actual_challenge_remains_protected(status):
    s=scanner()
    for i in range(2):
        url=f'https://fixture.invalid/job/{i}'
        s._note_detail_failure(url,'Cloudflare challenge page served instead of the job detail text',
                               blocked=True,transport='HTTP',status=status)
        assert s._detail_url_blocked(url)
        assert not s._detail_can_browser(url)
    assert s._detail_host_unavailable('https://fixture.invalid/new',browser=True)


def test_browser_403_does_not_turn_into_cross_transport_security_wall():
    s=scanner()
    for i in range(2):
        s._note_detail_failure(f'https://fixture.invalid/{i}',PLAIN.format(code=403),
                               blocked=True,transport='browser',status=403)
    assert not s._detail_host_unavailable('https://fixture.invalid/new')
    assert s._detail_host_unavailable('https://fixture.invalid/new',browser=True)
    assert not s._detail_blocked_hosts


def test_429_is_not_bypassed_with_browser():
    s=scanner();url='https://fixture.invalid/limited'
    s._note_detail_failure(url,PLAIN.format(code=429),blocked=True,transport='HTTP',status=429)
    assert not s._detail_can_browser(url)
    s._http_schedule.defer(url,.05)
    assert not s._detail_can_browser('https://fixture.invalid/new')
    assert not s._detail_blocked_hosts


def test_retained_cooldown_does_not_reserve_capacity_or_block_other_host():
    schedule=HttpSchedule(total=1)
    schedule.defer('https://slow.invalid',.12)
    events=[]
    def retained():
        with schedule.slot('https://slow.invalid/1',deadline=time.monotonic()+1,wait_cooldown=True)as ok:
            events.append(('slow',ok,time.monotonic()))
    start=time.monotonic()
    with ThreadPoolExecutor(max_workers=1)as pool:
        future=pool.submit(retained)
        with schedule.slot('https://fast.invalid/1',deadline=time.monotonic()+1)as ok:
            assert ok
            assert schedule.active==1
            events.append(('fast',ok,time.monotonic()))
        future.result(timeout=2)
    assert events[0][0]=='fast' and events[1][1]
    assert events[1][2]-start>=.10
    assert schedule.snapshot()['peak']==1 and schedule.active==0
    assert not schedule.waiters


@pytest.mark.parametrize('mode',['cancel','deadline','long_delay'])
def test_retained_cooldown_exit_is_bounded(mode):
    schedule=HttpSchedule();cancel=threading.Event()
    schedule.defer('https://fixture.invalid', 86400 if mode=='long_delay'else .5)
    if mode=='cancel':cancel.set()
    start=time.monotonic()
    with schedule.slot('https://fixture.invalid/1',cancel=cancel,
                       deadline=start+(.02 if mode=='deadline'else 1),wait_cooldown=True)as ok:
        assert not ok
    assert time.monotonic()-start<.2
    assert schedule.active==0 and not schedule.waiters


@pytest.fixture
def local_board():
    calls={};times={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            count=calls.get(self.path,0)+1;calls[self.path]=count
            times.setdefault(self.path,[]).append(time.monotonic())
            status=200;after=None
            if self.path.startswith('/limited')and count==1:
                status=429;after='.08';body='<title>Too many requests</title>Retry later'
            elif self.path.startswith('/always'):
                status=429;after='.01';body='Rate limited'
            elif self.path.startswith('/wall'):
                status=403;body=WALL
            elif self.path.startswith('/refused')and 'HeadlessChrome'not in self.headers.get('User-Agent',''):
                status=403;body='<title>Forbidden</title>Forbidden'
            else:
                data={'@type':'JobPosting','title':'Backend Engineer','description':TEXT,
                      'jobLocation':{'address':{'addressLocality':'Berlin','addressCountry':'DE'}}}
                body='<html><title>Backend Engineer</title><h1>Backend Engineer</h1><script type="application/ld+json">'+json.dumps(data)+'</script></html>'
            raw=body.encode();self.send_response(status)
            if after:self.send_header('Retry-After',after)
            self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield SimpleNamespace(base=f'http://127.0.0.1:{server.server_port}',calls=calls,times=times)
    server.shutdown();server.server_close();thread.join(timeout=2)


def test_real_429_one_retry_reads_original_and_queued_urls(local_board,monkeypatch):
    from packtoworkeu.scanning.career import career_scanner as mod
    s=scanner();s._http_schedule=HttpSchedule(total=2,per_host=1)
    monkeypatch.setattr(mod,'_jd_cache',lambda:SimpleNamespace(get=lambda _:None,put=lambda *_:None))
    urls=[local_board.base+'/limited/0',local_board.base+'/normal/1',local_board.base+'/normal/2']
    for url in urls:s._detail_state.register(url,s._tls.detail_scope)
    def fetch(url):return s._fetch_jd_text(url,allow_browser=False)
    with ThreadPoolExecutor(max_workers=2)as ex:results=list(ex.map(fetch,urls))
    assert all(len(result[0])>=200 for result in results)
    assert local_board.calls['/limited/0']==2
    assert local_board.times['/limited/0'][1]-local_board.times['/limited/0'][0]>=.075
    assert s._detail_state.total==4 and len(s._detail_state.http_retried)==1
    coverage=s._detail_state.coverage(s._tls.detail_scope,urls)
    assert coverage['readable']==3 and coverage['unattempted']==coverage['blocked']==0
    assert not s._detail_blocked_hosts and not s._http_schedule.waiters


def test_persistent_429_never_loops_or_uses_browser(local_board,monkeypatch):
    s=scanner();url=local_board.base+'/always'
    monkeypatch.setattr(s,'_fetch_jd_text_browser',lambda *a,**k:pytest.fail('rate limit bypassed'))
    assert s._fetch_jd_text(url)==('','','')
    assert local_board.calls['/always']==2
    assert s._fetch_jd_text(url)==('','','')
    assert local_board.calls['/always']==2
    assert not s._detail_blocked_hosts


@pytest.mark.skipif(os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1',reason='Opt-in local Chromium refusal recovery')
def test_real_http_403_to_ordinary_chromium_without_challenge_bypass(local_board,monkeypatch):
    from packtoworkeu.scanning.career import career_scanner as mod
    s=scanner()
    monkeypatch.setattr(mod,'_jd_cache',lambda:SimpleNamespace(get=lambda _:None,put=lambda *_:None))
    url=local_board.base+'/refused/1';s._detail_state.register(url,s._tls.detail_scope)
    result=s._fetch_jd_text(url)
    assert len(result[0])>=200
    assert s._detail_state.coverage(s._tls.detail_scope,[url])['readable']==1
    assert local_board.calls['/refused/1']==2
    assert s._jd_browser_used==1
    wall=local_board.base+'/wall/1'
    monkeypatch.setattr(s,'_fetch_jd_text_browser',lambda *a,**k:pytest.fail('confirmed wall retried'))
    assert s._fetch_jd_text(wall)==('','','')
    assert s._detail_url_blocked(wall)
    assert local_board.calls['/wall/1']==1


@pytest.mark.parametrize('html,url,error',[
    ('<title>Loading</title><body>Please wait</body>','https://fixture.invalid',True),
    ('','about:blank',True),
    ('<h1>0 jobs</h1>','chrome-error://chromewebdata/',True),
    ('<h1>0 jobs</h1>','https://fixture.invalid',False),
    (WALL+'<h2>0 jobs</h2>','https://fixture.invalid',True),
])
def test_timeout_zero_cards_needs_positive_empty_evidence(html,url,error):
    assert bool(timeout_listing_error(html,timed_out=True,page_url=url)) is error
    assert timeout_listing_error(html,timed_out=False,page_url=url)==''


def test_explicit_empty_census_does_not_count_listing_wall_as_a_jd():
    state=DetailRequestState();scope='https://fixture.invalid/jobs'
    state.register(scope,scope);state.observe(scope,kind='blocked',reason='listing wall')
    assert state.coverage(scope)['observed']==1  # optional membership inspection
    assert state.coverage(scope,[])['observed']==0  # explicit final posting census
    assert len(state.scope_blocked(scope))==1  # listing evidence still survives


@pytest.mark.parametrize('locale',['en','it'])
def test_access_queue_heading_does_not_call_all_entries_blocked(tmp_path,locale):
    from packtoworkeu import i18n
    from packtoworkeu.db import database as db
    from packtoworkeu.ui.tabs.assisted_ingestion import AssistedIngestionTab
    from packtoworkeu.tests._qt_support import application,wait_loaders,dispose
    from PySide6.QtWidgets import QGroupBox
    application();i18n.set_locale(locale)
    path=tmp_path/'ui.db';db.initialize(path)
    tab=AssistedIngestionTab(str(path));wait_loaders(tab)
    assert i18n._('Listing and JD access issues from your scans')in[b.title()for b in tab.findChildren(QGroupBox)]
    dispose(tab);i18n.set_locale('en')


def test_successful_thin_retry_is_eligible_for_normal_browser_rendering():
    s=scanner();url='https://fixture.invalid/job/1'
    s._note_detail_failure(url,PLAIN.format(code=429),blocked=True,transport='HTTP',status=429)
    assert s._detail_state.reserve(url,s.config,retry=True)
    s._detail_state.observe(url,'Loading the posting',transport='HTTP',status=200)
    assert s._detail_can_browser(url)
    assert s._detail_state.outcomes[url]['kind']=='thin'


def test_cancel_interrupts_retained_cooldown_without_leaking_ticket():
    schedule=HttpSchedule();cancel=threading.Event()
    schedule.defer('https://fixture.invalid',1)
    def work():
        with schedule.slot('https://fixture.invalid/job',cancel=cancel,
                           deadline=time.monotonic()+2,wait_cooldown=True)as ok:
            return ok
    with ThreadPoolExecutor(max_workers=1)as pool:
        future=pool.submit(work)
        cancel.set()
        assert future.result(timeout=.5)is False
    assert not schedule.waiters and schedule.active==0


@pytest.mark.skipif(os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1',reason='Opt-in local main browser pipeline')
def test_main_caller_owned_browser_recovers_refusals_with_unchanged_caps(local_board,monkeypatch):
    from playwright.sync_api import sync_playwright
    from packtoworkeu.scanning.career import career_scanner as mod
    from packtoworkeu.scanning.common import BROWSER_ARGS
    s=scanner();s.detail_scan=True
    s.config.DETAIL_BROWSER_FALLBACK_PER_COMPANY=3  # Deliberately small test cap, not production default.
    monkeypatch.setattr(mod,'_jd_cache',lambda:SimpleNamespace(get=lambda _:None,put=lambda *_:None))
    records={str(i):{'Job URL':local_board.base+f'/refused/{i}','Job Title':'Backend Engineer',
                     'Company Name':'Observed','Visa Sponsorship':'Unknown',
                     'Relocation Support':'Unknown','Job Location':'Unknown','Location Source':'none'}
             for i in range(20)}
    urls=[r['Job URL']for r in records.values()]
    for url in urls:s._detail_state.register(url,s._tls.detail_scope)
    s._enrich_support_from_detail(list(records.values()),use_browser=False,backfill=False)
    with sync_playwright()as pw:
        browser=pw.chromium.launch(headless=True,args=BROWSER_ARGS)
        try:s._detail_scan_for_company(browser.new_page(),'Observed',records)
        finally:browser.close()
    coverage=s._detail_state.coverage(s._tls.detail_scope,urls)
    assert coverage['readable']==3 and coverage['unattempted']==17
    assert s._jd_browser_used==3 and not s._detail_blocked_hosts
    assert all(r['Visa Sponsorship']=='Unknown'for r in records.values())


def test_browser_429_respects_retry_after_for_subsequent_pages():
    s=scanner();s.detail_scan=True
    class Page:
        urls=[]
        def goto(self,url,**kw):
            self.urls.append(url)
            return SimpleNamespace(status=429,headers={'retry-after':'60'})
        def wait_for_timeout(self,ms):pass
        def title(self):return 'Too many requests'
        def content(self):return '<title>Too many requests</title>Retry later'
        def evaluate(self,*a):return 'Retry later'
    page=Page();records={str(i):{'Job URL':f'https://fixture.invalid/job/{i}','Visa Sponsorship':'Unknown'}for i in range(20)}
    s._detail_scan_for_company(page,'Observed',records)
    assert len(page.urls)==1
    assert s._http_schedule.is_cooling(page.urls[0])
    assert not s._detail_blocked_hosts


def test_parallel_confirmed_walls_stop_host_with_bounded_inflight_allowance(monkeypatch):
    import io
    from urllib.error import HTTPError
    from packtoworkeu.scanning.career import career_scanner as mod
    s=scanner();calls=[]
    s._http_schedule=HttpSchedule(total=3,per_host=2)
    monkeypatch.setattr(mod,'_jd_cache',lambda:SimpleNamespace(get=lambda _:None,put=lambda *_:None))
    def denied(req,**kwargs):
        calls.append(req.full_url)
        raise HTTPError(req.full_url,403,'Forbidden',{},io.BytesIO(WALL.encode()))
    monkeypatch.setattr(mod.urllib.request,'urlopen',denied)
    monkeypatch.setattr(s,'_fetch_jd_text_browser',lambda *a,**kw:pytest.fail('confirmed challenge retried'))
    urls=[f'https://fixture.invalid/job/{i}'for i in range(30)]
    with ThreadPoolExecutor(max_workers=4)as ex:
        assert all(r==('','','')for r in ex.map(lambda u:s._fetch_jd_text(u,allow_browser=False),urls))
    assert 2<=len(calls)<=3  # two failures plus at most one earlier-admitted request
    assert s._detail_host_unavailable(urls[-1],browser=True)
    assert s._detail_host_unavailable(urls[-1])
    assert not s._http_schedule.waiters and s._http_schedule.active==0
