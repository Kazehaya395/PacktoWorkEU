"""Offline follow-up: fair admission, staged attribution and DOM readiness."""
import ast
import csv
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sponsorscout.scanning.career import career_scanner as cs
from sponsorscout.scanning.career.http_schedule import HttpSchedule


def wait_pending(s,n):
    # Tests only: wait for deterministic admission ordering without network.
    until=time.monotonic()+2
    while time.monotonic()<until:
        with s.condition:
            if len(s.waiters)>=n:return
        time.sleep(.001)
    raise AssertionError('workers failed to queue')


def test_cold_target_turn_is_not_behind_entire_hot_backlog():
    s=HttpSchedule(1,1);order=[];errors=[]
    def run(name):
        try:
            with s.slot('https://'+name+'.invalid/job/123',scope=name,phase='scope_http')as ok:
                assert ok;order.append(name)
        except BaseException as e:errors.append(e)
    with s.slot('https://hot.invalid/job/0',scope='hot')as ok:
        assert ok
        threads=[threading.Thread(target=run,args=('hot',))for _ in range(5)]
        for t in threads:t.start()
        wait_pending(s,5)
        cold=threading.Thread(target=run,args=('cold',));cold.start();wait_pending(s,6)
    for t in threads+[cold]:t.join(3);assert not t.is_alive()
    assert not errors and order[0]=='cold' and order.count('hot')==5
    assert not s.waiters and s.snapshot()['active']==0
    assert s.scope_snapshot('cold')['scope_http']['admitted']==1


def test_host_saturation_does_not_leave_other_global_capacity_idle():
    s=HttpSchedule(2,1);seen=threading.Event()
    def hot():
        with s.slot('https://hot.invalid/job/2',scope='hot'):pass
    with s.slot('https://hot.invalid/job/1',scope='hot'):
        t=threading.Thread(target=hot);t.start();wait_pending(s,1)
        with s.slot('https://cold.invalid/job/1',scope='cold',deadline=time.monotonic()+1)as ok:
            assert ok;seen.set();assert s.snapshot()['active']==2
    t.join(2);assert not t.is_alive()and seen.is_set()


@pytest.mark.parametrize('mode',['deadline','cancel','breaker','cooldown'])
def test_waiter_cleanup_and_next_target_after_abort(mode):
    s=HttpSchedule(1,1);cancel=threading.Event();block=threading.Event();result=[]
    def work():
        with s.slot('https://a.invalid/job/1',cancel=cancel,blocked=block.is_set,
                    deadline=time.monotonic()+(.06 if mode=='deadline' else 2),scope='A')as ok:result.append(ok)
    with s.slot('https://b.invalid/job/1'):
        t=threading.Thread(target=work);t.start();wait_pending(s,1)
        if mode=='cancel':cancel.set()
        if mode=='breaker':block.set()
        if mode=='cooldown':s.defer('https://a.invalid/',60)
        t.join(1);assert not t.is_alive()
    assert result==[False]and not s.waiters and s.snapshot()['active']==0
    with s.slot('https://next.invalid/job/1')as ok:assert ok


def test_exception_releases_fair_slot_and_metrics_are_scope_local():
    s=HttpSchedule(3,2)
    with pytest.raises(ValueError):
        with s.slot('https://a.invalid/job/1',scope='A',phase='scope_http'):
            s.note('generic_wire_seconds',.1,scope='A',phase='scope_http');raise ValueError()
    with s.slot('https://b.invalid/job/2',scope='B',phase='enrichment_http'):pass
    a=s.scope_snapshot('A');b=s.scope_snapshot('B')
    assert a['scope_http']['generic_wire_seconds']==.1
    assert set(a)=={'scope_http'}and set(b)=={'enrichment_http'}
    assert s.snapshot()['active']==0 and not s.waiters


class FakePage:
    def __init__(self):self.waits=[]
    def wait_for_timeout(self,ms):self.waits.append(ms)


def test_ready_transition_removes_only_fixed_delay(monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True);page=FakePage();calls=[]
    def ready(predicate,**kw):
        assert 'signature !== before'in predicate and 'aria-busy'in predicate
        assert kw['timeout']==s.config.PAGINATION_WAIT_MS
        calls.append('transition')
    target=SimpleNamespace(wait_for_function=ready)
    monkeypatch.setattr(s,'wait_for_dom_quiet',lambda *a,**kw:calls.append('original quiet'))
    s._wait_for_listing_transition(page,target,'[["old"]]')
    assert not page.waits and calls==['transition','original quiet']


@pytest.mark.parametrize('before',[None,'[]','',False])
def test_ambiguous_page_keeps_original_delay_and_quiet(before,monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True);page=FakePage();calls=[]
    monkeypatch.setattr(s,'wait_for_dom_quiet',lambda *a,**kw:calls.append(kw['timeout_ms']))
    s._wait_for_listing_transition(page,object(),before)
    assert page.waits==[s.config.PAGINATION_WAIT_MS]and calls==[s.config.DOM_QUIET_MS]


def test_frame_or_api_failure_keeps_render_delay(monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True);page=FakePage();quiet=[]
    monkeypatch.setattr(s,'wait_for_dom_quiet',lambda *a,**kw:quiet.append(True))
    s._wait_for_listing_transition(page,object(),'[["old"]]')
    assert sum(page.waits)>s.config.PAGINATION_WAIT_MS-50 and quiet==[True]


def test_node_signature_does_not_treat_pager_as_posting_change():
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    signature=cs.CareerPortalScanner._PAGINATION_SIGNATURE_JS
    script='''let role='Old engineer',next='2';
const a=(href,text,nav=false)=>({href,textContent:text,getAttribute:()=>href,closest:()=>nav?{}:null});
const document={querySelectorAll:()=>[a('/job/123',role),a('/jobs/page/2',next,true),a('/jobs','All jobs')]};
const sig=('''+signature+''');const before=sig();next='3';if(sig()!==before)throw Error('pager mistaken for posting');
role='New engineer';if(sig()===before)throw Error('posting title change missed');console.log('ok');'''
    assert subprocess.run([node,'-e',script],capture_output=True,text=True,check=True).stdout.strip()=='ok'


def test_pagination_uses_snapshot_before_click_and_preserves_filter_params(monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True);events=[];page=FakePage()
    class Target:
        def evaluate(self,script,*args):
            if script==s._PAGINATION_SIGNATURE_JS:events.append('snapshot');return '[["old"]]'
            assert 'orgIds'in script;events.append('click');return 'next'
    monkeypatch.setattr(s,'_wait_for_listing_transition',lambda *a:events.append('wait'))
    assert s.execute_advanced_pagination(page,Target(),1,[('orgIds','123')])
    assert events==['snapshot','click','wait']


@pytest.fixture
def scanner(monkeypatch):
    monkeypatch.setattr(cs,'_jd_cache',lambda:SimpleNamespace(get=lambda *_:None,put=lambda *_:None))
    return cs.CareerPortalScanner(skip_preflight=True)


def test_scope_and_support_reuse_same_jd_with_phase_attribution(scanner,monkeypatch):
    s=scanner;calls=[];scope='https://a.invalid/jobs';url='https://a.invalid/job/123456'
    body='We offer visa sponsorship. '+('Build Python software with SQL and five years of experience. '*10)
    s._detail_state.register(url,scope)
    def fetch(u):
        calls.append(u);s._detail_state.observe(u,body,transport='fake');return body,'Berlin, Germany','Engineer'
    monkeypatch.setattr(s,'_fetch_jd_text_uncached',fetch)
    rec={'Job URL':url,'Job Title':'Software Engineer','Company Name':'Fixture','_scope_pending':True}
    jobs={'123':rec};diagnostics=[]
    s._resolve_deferred_scope({'target_country':'Global','scope_policy':'global','careers_url':scope},jobs,[],{'rejected_scope':0},diagnostics)
    s._enrich_support_from_detail(list(jobs.values()),max_workers=3,use_browser=False)
    assert calls==[url]
    assert s._http_schedule.scope_snapshot(scope)['scope_http']['admitted']==1
    assert rec.get('_jd_complete') and rec.get('Visa Sponsorship')=='Yes'
    assert any(x.startswith('scope phase seconds:')for x in diagnostics)


def test_new_instrumentation_keeps_existing_duration_semantics():
    source=Path(cs.__file__).read_text()
    assert "'worker_occupancy_including_browser_wait'"in source
    assert "'submission_to_log'"in source
    assert 'started += _browser_wait'in source
    ast.parse(source)


def test_actual_coordinator_emits_queue_inclusive_and_target_phase_metrics(tmp_path,monkeypatch):
    from sponsorscout.tests.test_career_runtime_2026_11_04 import test_dom_enrichment_is_outside_browser_slot_and_context_is_closed
    test_dom_enrichment_is_outside_browser_slot_and_context_is_closed(tmp_path,monkeypatch)
    with (tmp_path/'jobs_scan_log.csv').open(encoding='utf-8-sig')as f:row=next(csv.DictReader(f))
    import re
    timing=json.loads(re.search(r'target elapsed seconds: (\{[^}]+\})',row['Diagnostics'])[1])
    assert timing['submission_to_log']>=timing['worker_occupancy_including_browser_wait']
    assert timing['company_queue']>=0
    assert 'HTTP scheduling (target phases):'in row['Diagnostics']


def test_scope_cancelled_before_submission_has_no_request(scanner,monkeypatch):
    s=scanner;s.cancel_event=threading.Event();s.cancel_event.set()
    monkeypatch.setattr(s,'_detail_location_from_url',lambda *a,**kw:pytest.fail('request after Stop'))
    rec={'Job URL':'https://a.invalid/job/123456','Job Title':'Engineer','_scope_pending':True}
    diagnostics=[]
    s._resolve_deferred_scope({'target_country':'Global','scope_policy':'global'},{'123':rec},[],{},diagnostics)
    assert s._http_schedule.snapshot()['admitted']==0
    assert any('skipped after cancellation'in d for d in diagnostics)


def test_busy_indicator_prevents_early_ready_even_when_posting_changes():
    node=shutil.which('node')
    if not node:pytest.skip('Node unavailable')
    s=cs.CareerPortalScanner(skip_preflight=True);captured=[]
    class Target:
        def wait_for_function(self,predicate,**kw):captured.append(predicate)
    s.wait_for_dom_quiet=lambda *a,**kw:None
    s._wait_for_listing_transition(FakePage(),Target(),'old')
    script="""let busy=true;const a={href:'/job/123',textContent:'Engineer',getAttribute:()=>'/job/123',closest:()=>null};
const document={readyState:'complete',querySelectorAll:s=>s==='a[href]'?[a]:(busy?[{getClientRects:()=>[1]}]:[])};
const getComputedStyle=()=>({visibility:'visible'});const ready=("""+captured[0]+""");
if(ready('old'))throw Error('busy page released early');busy=false;if(!ready('old'))throw Error('ready page not released');console.log('ok');"""
    assert subprocess.run([node,'-e',script],capture_output=True,text=True,check=True).stdout.strip()=='ok'


@pytest.mark.parametrize('delay',[0,400,1800])
def test_delayed_batch_keeps_observed_pages_and_original_wait_fallback(delay,monkeypatch):
    s=cs.CareerPortalScanner(skip_preflight=True)
    class DOM:
        clock=0.;current=0;pending=None
        def advance(self,ms):
            self.clock+=ms
            if self.pending is not None and self.clock>=self.pending:self.current+=1;self.pending=None
        def evaluate(self,script,*args):
            if script==s._PAGINATION_SIGNATURE_JS:return str(self.current)
            self.pending=self.clock+delay;self.advance(0);return 'next'
        def wait_for_timeout(self,ms):self.advance(ms)
        def wait_for_function(self,predicate,*,arg,timeout):
            deadline=self.clock+timeout
            while self.clock<deadline:
                if str(self.current)!=arg and self.pending is None:return
                self.advance(min(25,deadline-self.clock))
            raise TimeoutError()
    dom=DOM()
    def quiet(page,target,timeout_ms):
        prev=dom.current;stable=0;deadline=dom.clock+timeout_ms
        while dom.clock<deadline:
            dom.advance(500)
            if dom.current==prev:
                stable+=1
                if stable>=2:return
            else:prev=dom.current;stable=0
    monkeypatch.setattr(s,'wait_for_dom_quiet',quiet)
    monkeypatch.setattr(cs.time,'monotonic',lambda:dom.clock/1000)
    seen=[]
    for page in range(8):
        assert s.execute_advanced_pagination(dom,dom,page+1);seen.append(dom.current)
    assert seen==list(range(1,9))
    assert dom.clock=={0:8000,400:11200,1800:21600}[delay]
