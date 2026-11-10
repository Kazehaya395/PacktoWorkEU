"""Runtime admission and browser ownership: fake transport only, no portals."""
import ast
import csv
import io
import json
import re
from pathlib import Path
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from packtoworkeu.scanning.career import career_scanner as cs
from packtoworkeu.scanning.career.evidence import FAMILY_LABELS, generated_family_card, provider_slug
from packtoworkeu.scanning.career.http_schedule import HttpSchedule

BODY=('Build reliable Python services with our engineering team. Requirements: Python and SQL. '*5)

@pytest.fixture(autouse=True)
def isolated_cross_run_cache(monkeypatch):
    # Exercise run-local sharing, never consume or write persistent JD cache.
    monkeypatch.setattr(cs,'_jd_cache',lambda:SimpleNamespace(get=lambda *_:None,put=lambda *_:None))

@pytest.mark.parametrize('value',['0','',None,'null','N/A','nan'])
def test_provider_sentinel_uses_observed_account(value):
    assert provider_slug(value,'https://itonics-gmbh.jobs.personio.de/?language=en','personio')=='itonics-gmbh'

def test_provider_explicit_account_and_unknown_url():
    assert provider_slug('configured','https://x.invalid/','personio')=='configured'
    assert provider_slug('0','https://x.invalid/','personio')==''

def test_adapter_requests_real_account_not_zero(monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True);urls=[]
    monkeypatch.setattr(s,'_http_fetch_with_retry',lambda url,*a:(urls.append(url)or b'<workzag-jobs/>'))
    s._fetch_provider_jobs({'provider':'personio','board_slug':'0','careers_url':'https://itonics-gmbh.jobs.personio.de/?language=en'})
    assert urls==['https://itonics-gmbh.jobs.personio.de/xml?language=en']

@pytest.mark.parametrize('title',sorted(FAMILY_LABELS))
def test_family_is_not_a_title_location_generated_identity(title):
    slug=re.sub(r'[^a-z0-9]+','-',title.lower()).strip('-')[:70]
    assert generated_family_card(title,'https://fixture.invalid/jobs#job='+slug+'--berlin')
    assert not generated_family_card(title,'https://fixture.invalid/jobs#job=123456')
    assert not generated_family_card(title,'https://fixture.invalid/job/123456')

def test_genuine_role_substring_is_not_a_family():
    assert not generated_family_card('Operai Specializzati - Tornitore CNC','https://fixture.invalid/jobs#job=cnc--rome')


def test_schedule_bounds_across_multiple_hosts_and_releases():
    schedule=HttpSchedule(3,2);start=threading.Barrier(7);lock=threading.Lock();active={};global_active=0;peak=0;host_peak=0;fail=[]
    def work(i):
        nonlocal global_active,peak,host_peak
        try:
            start.wait(3)
            host='a'if i<4 else'b'
            with schedule.slot('https://'+host+'.invalid/job/1')as ok:
                assert ok
                with lock:
                    active[host]=active.get(host,0)+1;global_active+=1;peak=max(peak,global_active);host_peak=max(host_peak,active[host])
                time.sleep(.04)
                with lock:global_active-=1;active[host]-=1
        except BaseException as e:fail.append(e)
    threads=[threading.Thread(target=work,args=(i,))for i in range(6)]
    for t in threads:t.start()
    start.wait(3)
    for t in threads:t.join(3)
    assert not fail and all(not t.is_alive()for t in threads)
    assert peak<=3 and host_peak<=2 and schedule.snapshot()['active']==0
    assert schedule.snapshot()['admitted']==6


def test_queued_stop_is_interruptible_and_does_not_leak_permit():
    schedule=HttpSchedule(1,1);cancel=threading.Event();started=threading.Event();result=[]
    def queued():
        started.set()
        with schedule.slot('https://a.invalid/2',cancel)as ok:result.append(ok)
    with schedule.slot('https://a.invalid/1')as ok:
        assert ok
        t=threading.Thread(target=queued);t.start();assert started.wait(2);cancel.set();t.join(2)
        assert not t.is_alive()and result==[False]
    assert schedule.snapshot()['active']==0


def test_deadline_cooldown_and_exception_release():
    schedule=HttpSchedule(2,2);schedule.defer('https://a.invalid/','60')
    with schedule.slot('https://a.invalid/1',deadline=time.monotonic()+.03)as ok:assert not ok
    with pytest.raises(ValueError):
        with schedule.slot('https://b.invalid/1')as ok:
            assert ok;raise ValueError('fixture')
    assert schedule.snapshot()['active']==0 and schedule.snapshot()['rate_limited']==1


def test_429_retry_after_is_recorded_without_retry(monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True);calls=[]
    def limited(*a,**kw):
        calls.append(1);raise HTTPError('https://a.invalid/job/1',429,'Too Many Requests',{'Retry-After':'60'},io.BytesIO(b''))
    monkeypatch.setattr(cs.urllib.request,'urlopen',limited)
    s._fetch_jd_text('https://a.invalid/job/1',allow_browser=False)
    assert calls==[1] and s._http_schedule.snapshot()['rate_limited']==1
    assert s._http_schedule.cooldowns['a.invalid']>time.monotonic()+50


def test_three_workers_can_make_two_same_host_reads_with_equal_coverage(monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True);s._http_schedule=HttpSchedule(3,2)
    active=0;peak=0;calls=0;lock=threading.Lock()
    def fetch(url):
        nonlocal active,peak,calls
        with lock:active+=1;calls+=1;peak=max(peak,active)
        time.sleep(.03)
        s._detail_state.observe(url,BODY,transport='fixture')
        with lock:active-=1
        return BODY,'Berlin, Germany','Fixture'
    monkeypatch.setattr(s,'_fetch_jd_text_uncached',fetch)
    rows=[{'Job URL':f'https://a.invalid/job/{123456+i}','Job Title':'Engineer','Company Name':'Fixture'}for i in range(12)]
    s._enrich_support_from_detail(rows,max_workers=3,use_browser=False,backfill=False)
    assert calls==12 and peak==2 and all(r.get('_jd_complete')for r in rows)
    assert s._http_schedule.snapshot()['active']==0


def test_two_company_queues_share_the_same_global_bound(monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True);s._http_schedule=HttpSchedule(2,2)
    active=0;peak=0;lock=threading.Lock()
    def fetch(url):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        time.sleep(.02)
        s._detail_state.observe(url,BODY,transport='fixture')
        with lock:active-=1
        return BODY,'',''
    monkeypatch.setattr(s,'_fetch_jd_text_uncached',fetch)
    def queue(host):
        rows=[{'Job URL':f'https://{host}.invalid/job/{123456+i}','Job Title':'Engineer'}for i in range(6)]
        s._enrich_support_from_detail(rows,max_workers=3,use_browser=False,backfill=False)
    threads=[threading.Thread(target=queue,args=(host,))for host in ('a','b')]
    for t in threads:t.start()
    for t in threads:t.join(5)
    assert all(not t.is_alive()for t in threads)and peak<=2
    assert s._http_schedule.snapshot()['admitted']==12


def test_duplicate_url_has_one_fetch_and_cached_reads_spend_no_admission(monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True);calls=[]
    def fetch(url):
        calls.append(url);time.sleep(.02);s._detail_state.observe(url,BODY);return BODY,'',''
    monkeypatch.setattr(s,'_fetch_jd_text_uncached',fetch)
    ts=[threading.Thread(target=s._fetch_jd_text,args=('https://a.invalid/job/1',),kwargs={'allow_browser':False})for _ in range(5)]
    for t in ts:t.start()
    for t in ts:t.join(3)
    assert len(calls)==1 and s._http_schedule.snapshot()['admitted']==1


def test_dom_enrichment_is_outside_browser_slot_and_context_is_closed(tmp_path,monkeypatch):
    seed=tmp_path/'seed.csv';seed.write_text('name,careers_url,provider,target_country,scope_policy\nFixture,https://fixture.invalid/jobs,auto,Global,global\n')
    s=cs.CareerPortalScanner(input_csv=str(seed),output_csv=str(tmp_path/'jobs.csv'),skip_preflight=True)
    s._browser_sem=threading.BoundedSemaphore(1)
    class Page:
        def __init__(self):self.closed=False
        def goto(self,*a,**kw):return SimpleNamespace(status=200)
        def wait_for_timeout(self,*a):pass
        def close(self):self.closed=True
    page=Page()
    class Context:
        closed=False
        def new_page(self):return page
        def close(self):self.closed=True
    ctx=Context();browser=SimpleNamespace(new_context=lambda **kw:ctx)
    monkeypatch.setattr(cs,'sync_playwright',object())
    monkeypatch.setattr(cs,'fetch_static_jobs',lambda *a,**kw:([],'fixture static'))
    monkeypatch.setattr(cs,'install_page_resource_blocking',lambda *_:None)
    monkeypatch.setattr(s,'_fetch_provider_jobs',lambda *_:([],'fixture provider'))
    monkeypatch.setattr(s,'_get_or_launch_worker_browser',lambda:browser)
    for method in ('dismiss_initial_blockers','navigate_to_fragment','wait_for_job_cards_to_load','check_iframes','try_increase_page_size'):
        monkeypatch.setattr(s,method,lambda *a,**kw:None)
    monkeypatch.setattr(s,'_bot_challenge_reason',lambda *a,**kw:'')
    monkeypatch.setattr(s,'_direct_listing_pagination_state',lambda *_:{})
    monkeypatch.setattr(s,'extract_visible_jobs',lambda *_:[{'job_title':'Software Engineer','job_url':'https://fixture.invalid/job/123456','location_hint':'Berlin, Germany','jd_text':BODY}])
    checked=[]
    def http(page_arg,name,rows):
        assert page_arg is None and ctx.closed and page.closed
        assert s._browser_sem.acquire(False),'HTTP phase retained browser slot'
        s._browser_sem.release();checked.append(name)
    monkeypatch.setattr(s,'_run_detail_pipeline',http)
    monkeypatch.setattr(s,'_needs_browser_detail',lambda *_:False)
    s.execute_crawler()
    assert checked==['Fixture']
    with(tmp_path/'jobs_scan_log.csv').open(encoding='utf-8-sig')as f:log=list(csv.DictReader(f))
    assert log[0]['Status']=='ok' and 'browser_listing'in log[0]['Diagnostics']and 'http_enrichment'in log[0]['Diagnostics']


def test_family_only_generated_card_is_quarantined_but_provider_id_role_survives(tmp_path,monkeypatch):
    from packtoworkeu.tests.test_career_partial_2026_11_03 import _seed_scanner,_read
    s=_seed_scanner(tmp_path)
    monkeypatch.setattr(s,'_fetch_provider_jobs',lambda _:([
        {'job_title':'Operai Generici','job_url':'https://fixture.invalid/jobs#job=operai-generici--rome','location_hint':'Rome, Italy'},
        {'job_title':'Operai Generici','job_url':'https://fixture.invalid/jobs#job=123456','location_hint':'Rome, Italy'},
    ],'fixture'))
    monkeypatch.setattr(s,'_needs_browser_detail',lambda *_:False)
    s.execute_crawler()
    assert len(_read(tmp_path/'jobs.csv'))==1
    q=_read(tmp_path/'jobs_quarantine.csv');assert len(q)==1 and q[0]['Quarantine Reason']=='category_only_card_without_posting_identity'


def test_node_skips_category_container_and_reads_individual_title():
    node=shutil.which('node')
    if not node:pytest.skip('Node not installed')
    setup='''const document={};const window={location:{href:'https://fixture.invalid/jobs'},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'})};
const mk=t=>({nodeType:1,innerText:t,textContent:t,childNodes:[],getBoundingClientRect:()=>({width:10,height:10}),matches:()=>false});'''
    exercise='''const family=mk('Operai Generici');family.matches=s=>s==='[class*="job-title" i]';family.className='job-category-title';
const role=mk('Addetto alla produzione');role.matches=s=>s==='h2';role.closest=()=>null;
const card=mk('Operai Generici\\nAddetto alla produzione');card.className='job-card';card.childNodes=[family,role];card.parentElement={className:'category-page'};family.parentElement=card;role.parentElement=card;
console.log(JSON.stringify(titleFromScope(card)));'''
    r=subprocess.run([node,'-e',setup+cs.JS_HELPERS+exercise],capture_output=True,text=True,check=True)
    assert json.loads(r.stdout)=='Addetto alla produzione'


def test_observed_opaque_double_hyphen_id_is_not_synthetic_family():
    assert not generated_family_card('Operai Generici','https://fixture.invalid/jobs#job=account--123456')


def test_capacity_queue_aborts_when_host_breaker_opens():
    schedule=HttpSchedule(1,1)
    blocked=threading.Event();entered=threading.Event();result=[]
    def wait():
        entered.set()
        with schedule.slot('https://a.invalid/',blocked=blocked.is_set)as ok:result.append(ok)
    with schedule.slot('https://b.invalid/')as ok:
        assert ok
        t=threading.Thread(target=wait);t.start();assert entered.wait(2);blocked.set();t.join(2)
        assert not t.is_alive()and result==[False]
    assert schedule.snapshot()['active']==0


def test_http_date_retry_after_is_honored():
    from email.utils import formatdate
    schedule=HttpSchedule();schedule.defer('https://a.invalid/',formatdate(time.time()+60,usegmt=True))
    assert schedule.cooldowns['a.invalid']>time.monotonic()+50


def test_long_retry_after_defers_instead_of_parking_company():
    schedule=HttpSchedule();schedule.defer('https://a.invalid/',86400)
    start=time.monotonic()
    with schedule.slot('https://a.invalid/job/1')as ok:assert not ok
    assert time.monotonic()-start<.2 and schedule.snapshot()['admitted']==0
    assert schedule.snapshot()['cooldown_deferred']==1


def test_nonfinite_retry_after_cannot_create_infinite_wait():
    schedule=HttpSchedule();schedule.defer('https://a.invalid/','inf')
    assert 0<schedule.cooldowns['a.invalid']-time.monotonic()<=30


def test_deferred_http_observations_are_not_reported_as_portal_blocks():
    from packtoworkeu.scanning.detail_state import DetailRequestState
    state=DetailRequestState();scope='https://a.invalid/jobs';url='https://a.invalid/job/1'
    state.register(url,scope);state.count(url,'http_cooldown_deferred')
    text=state.summary(scope)
    assert 'http_cooldown_deferred=1'in text and 'blocked=0'in text
    assert state.coverage(scope,[url],lambda _: '')['unattempted']==1
