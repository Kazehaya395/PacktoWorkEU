"""Artifact-driven regression tests: missing UUID slug and numbered append control."""
import os
import pytest
from packtoworkeu.core import blocked_portals as bp
from packtoworkeu.core.job_identity import explicit_detail_url,posting_candidate
from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner

UUID='fd1d08c2-baaf-4642-99b5-7a5f82c85859'
PRICING='https://careers.fixture.invalid/jobs/pricing-packaging-senior-lead-x-f-m-'+UUID+'/'


def test_slug_uuid_posting_with_marketing_word_is_not_rejected():
    s=CareerPortalScanner(input_csv='unused',skip_preflight=True)
    assert explicit_detail_url(PRICING)
    assert s.is_valid_job_url(PRICING)
    assert posting_candidate(PRICING,'Pricing & Packaging Senior Lead',s.is_valid_job_url)


@pytest.mark.skipif(os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1',reason='real Chromium opt-in')
def test_numbered_native_append_is_recognised_before_href_navigation():
    from playwright.sync_api import sync_playwright
    with sync_playwright()as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page()
            page.set_content('<main id="listing"><h1>Engineering jobs</h1>'+''.join(
                f'<article class="job-card"><h2><a href="https://fixture.invalid/job/engineer-{10000+i}">Platform Engineer {i}</a></h2><p>Berlin, Germany</p></article>'for i in range(10))+
                '''<a id="more" href="https://fixture.invalid/jobs/?show_results=1">Load 10 more</a></main>
                <script>document.getElementById('more').onclick=e=>{e.preventDefault();document.getElementById('listing').insertAdjacentHTML('beforeend','<article class="job-card"><h2><a href="https://fixture.invalid/job/engineer-99999">Cloud Engineer</a></h2><p>Berlin, Germany</p></article>');document.getElementById('more').remove();};</script>''')
            s=CareerPortalScanner(input_csv='unused',skip_preflight=True)
            page._assisted_job_validator=s.is_valid_job_url
            assert bp._native_append_advance(page) is True
            assert page.url=='about:blank'
            assert 'retained primary identities'in page._ss_native_append_diagnostic
        finally:browser.close()


@pytest.mark.parametrize('suffix', ['bad-uuid','fd1d08c2-baaf-4642-99b5-7a5f82c8585Z'])
def test_marketing_paths_without_strong_posting_identity_stay_rejected(suffix):
    s=CareerPortalScanner(input_csv='unused',skip_preflight=True)
    assert not explicit_detail_url('https://fixture.invalid/pricing/'+UUID)
    assert not s.is_valid_job_url('https://fixture.invalid/jobs/pricing-'+suffix)


def test_identity_reconciliation_never_clears_smaller_or_different_batch():
    from packtoworkeu.core.assisted_workflow import reconcile_details
    original={'https://fixture.invalid/jobs/1','https://fixture.invalid/jobs/2'}
    assert not reconcile_details(original,[dict(url='https://fixture.invalid/jobs/1',jd_read_status='readable')])['verified']
    assert not reconcile_details(original,[dict(url='https://fixture.invalid/jobs/3',jd_read_status='readable'),dict(url='https://fixture.invalid/jobs/4',jd_read_status='readable')])['verified']
    assert not reconcile_details(None,[])['verified']
    assert reconcile_details(original,[dict(url=u+'/',jd_read_status='readable')for u in original])['verified']


def test_completion_notice_separates_listing_jds_and_unproven_closure():
    from packtoworkeu.ui.tabs.assisted_ingestion import _completion_notice
    from packtoworkeu import i18n
    i18n.set_locale('en')
    title,message=_completion_notice(dict(listing_completed=False,jd_coverage=dict(observed=151,readable=151)),complete=False)
    assert title=='Descriptions read; listing not verified'and '151'in message
    title,message=_completion_notice(dict(listing_completed=True,jd_coverage=dict(observed=10,readable=9)),complete=False)
    assert title=='Listing collected; descriptions incomplete'and '9/10'in message
    title,message=_completion_notice(dict(stop_reason='verification exit: browser closed before verification accepted; observed=132.75s; configured=180s'))
    assert title=='Browser closed before access was confirmed'and 'why it closed'in message


# Real headed Chromium full-engine fixtures. Only pacing is shortened: no
# admission, pagination, SQLite, support or completion result is mocked.
@pytest.fixture
def real_board(tmp_path,monkeypatch):
    import csv,json,threading,time
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from urllib.parse import urlsplit,parse_qs
    from playwright.sync_api import BrowserType,BrowserContext,Page
    from packtoworkeu import paths
    if os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1':pytest.skip('real Chromium opt-in')
    if not os.environ.get('DISPLAY'):pytest.skip('headed Chromium needs DISPLAY/Xvfb')
    calls=[]; launches=[];pages=[]
    state=dict(mode='numbered',total=30,ready=True,label='Load 10 more',replay=False)
    def path_for(i):
        return '/jobs/pricing-packaging-senior-lead-'+UUID+'/'if i==50 else f'/job/platform-engineer-{100000+i}/'
    def cards(indices):
        return ''.join('<article class="job-card"><h2><a href="'+path_for(i)+'">'+('Pricing &amp; Packaging Senior Lead (x/f/m)'if i==50 else f'Platform Engineer {i}')+'</a></h2><p>Berlin, Germany</p></article>'for i in indices)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            p=urlsplit(self.path);q=parse_qs(p.query);calls.append(self.path);status=200
            if state.get('proof_query') and p.path==path_for(0) and self.headers.get('Sec-Fetch-Dest')!='document':
                raw=b'<title>Azure WAF</title><h1>Access denied</h1>'
                self.send_response(403);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw);return
            if state.get('proof_query') and p.path==path_for(0) and 'afd_azwaf_tok'not in q:
                self.send_response(302);self.send_header('Location',self.path+'?afd_azwaf_tok=LOCAL_FAKE_PROOF')
                self.send_header('Content-Length','0');self.end_headers();return
            if p.path=='/api/more':
                n=int(q.get('page',['2'])[0]);body=cards(range((n-1)*10,min(n*10,state['total']))) if not state['replay']else ''
            elif p.path.startswith(('/job/','/jobs/pricing')):
                title='Pricing & Packaging Senior Lead'if p.path.startswith('/jobs/pricing')else'Platform Engineer'
                text='Build reliable services, design production systems and collaborate with engineers. '*10
                node={'@type':'JobPosting','url':base+self.path,'title':title,'description':text,'jobLocation':{'@type':'Place','address':{'addressCountry':'DE','addressLocality':'Berlin'}}}
                body='<h1>'+title+'</h1>'+(('<section class="job-description">'+text+'</section><script type="application/ld+json">'+json.dumps(node)+'</script>')if state['ready']else'<p>Loading...</p>')
            elif p.path=='/jobs/':
                total=state['total'];mode=state['mode'];n=int(q.get('Page',['1'])[0])
                if mode in('append','replay'):
                    body='<main id="listing"><h1>Engineering jobs</h1>'+cards(range(min(10,total)))
                    body+='<a id="more" href="?show_results=1">'+state['label']+'</a></main>'
                    body+='''<script>let page=1;document.getElementById('more').onclick=async e=>{e.preventDefault();let response=await fetch('/api/more?page='+(++page));let html=await response.text();document.getElementById('listing').insertAdjacentHTML('beforeend',html);if(page*10>='''+str(total)+''')document.getElementById('more').setAttribute('aria-disabled','true');};</script>'''
                else:
                    page_size=12 if mode=='numbered'else 10;maximum=(total+page_size-1)//page_size
                    n=min(n,maximum)
                    body='<main><h1>'+str(total)+' jobs</h1>'+cards(range((n-1)*page_size,min(n*page_size,total)))
                    body+='<nav class="pagination" aria-label="pagination">'
                    body+=''.join(f'<a href="?show_results=1&amp;Page={k}" '+('aria-current="page"'if k==n else'')+f'>{k}</a>'for k in range(1,maximum+1))
                    body+=('<a rel="next" href="?show_results=1&amp;Page='+str(n+1)+'">Next</a>'if n<maximum else'<button disabled aria-label="Next">Next</button>')+'</nav></main>'
                    if mode=='ajax':body+='''<script>document.addEventListener('click',async e=>{let a=e.target.closest('nav a');if(!a)return;e.preventDefault();const html=await(await fetch(a.href)).text();const d=new DOMParser().parseFromString(html,'text/html');document.querySelector('main').replaceWith(d.querySelector('main'));});</script>'''
            else:body='<h1>Not found</h1>'
            human_gate=state.get('human_gate')
            if human_gate and ((human_gate=='listing'and p.path=='/jobs/')or(human_gate=='detail'and p.path==path_for(0))):
                import html
                action='document.body.innerHTML='+json.dumps(body)
                status=403;body='<h1>Verify you are human</h1><div id="px-captcha">Press &amp; Hold</div><button id="human-clear" onclick="'+html.escape(action,quote=True)+'">Clear local fixture</button>'
            if state.get('wall') and p.path=='/jobs/':
                status=403;body='<title>Just a moment...</title><h1>Verify you are human</h1><div id="cf-challenge-running"></div>'
            raw=('<!doctype html><html><head><title>Engineering careers</title></head><body>'+body+'</body></html>').encode()
            self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    base=f'http://127.0.0.1:{server.server_port}'
    original_launch=BrowserType.launch;original_new=BrowserContext.new_page;wait=Page.wait_for_timeout
    def launch(self,*a,**kw):
        launches.append(kw.copy());kw['args']=list(kw.get('args',[]))+['--no-sandbox'];return original_launch(self,*a,**kw)
    def new_page(self,*a,**kw):
        def guard(route):
            assert urlsplit(route.request.url).hostname=='127.0.0.1','non-local fixture request'
            route.continue_()
        self.route('**/*',guard);page=original_new(self,*a,**kw);pages.append(page);return page
    monkeypatch.setattr(BrowserType,'launch',launch);monkeypatch.setattr(BrowserContext,'new_page',new_page)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda page,ms:wait(page,min(ms,25)))
    class Clock:
        def __getattr__(self,k):return getattr(time,k)
        def sleep(self,s):time.sleep(min(s,.003))
    monkeypatch.setattr(bp,'time',Clock())
    monkeypatch.setattr('packtoworkeu.core.session_details.time',Clock())
    monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',tmp_path/'output');paths.SCAN_OUTPUT_DIR.mkdir()
    from types import SimpleNamespace
    yield SimpleNamespace(base=base,url=base+'/jobs/?show_results=1',state=state,calls=calls,launches=launches,pages=pages,path_for=path_for)
    server.shutdown();server.server_close();t.join(timeout=2)


def test_real_full_engine_13_pages_152_identities_with_pricing_posting(real_board,tmp_path):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.job_identity import posting_identity
    b=real_board;b.state.update(mode='numbered',total=152)
    path=tmp_path/'numbered.db';db.initialize(path);conn=db.get_connection(path)
    try:
        count=bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),max_pages=20,auto_continue=True)
        r=bp.LAST_INGESTION_REPORT
        assert count==152 and r['pages_visited']==13
        assert r['listing_completed']and r['completed']
        assert r['jd_coverage']['readable']==152
        ids={posting_identity(row['url'])for row in conn.execute('SELECT url FROM jobs')}
        assert ids=={posting_identity(b.base+b.path_for(i))for i in range(152)}
        assert any(b.path_for(50)in call for call in b.calls)
        assert all(not x.get('headless',True)for x in b.launches)
    finally:conn.close()


def test_real_full_engine_numbered_load_more_30_jobs_no_listing_reload(real_board,tmp_path):
    from packtoworkeu.db import database as db
    b=real_board;b.state.update(mode='append',total=30)
    path=tmp_path/'append.db';db.initialize(path);conn=db.get_connection(path)
    try:
        count=bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),max_pages=10,auto_continue=True)
        r=bp.LAST_INGESTION_REPORT
        assert count==30 and r['jd_coverage']['readable']==30
        assert r['listing_completed']and r['completed']
        assert len([c for c in b.calls if c.startswith('/jobs/?')])==1
        assert '/api/more?page=2'in b.calls and '/api/more?page=3'in b.calls
        assert conn.execute('SELECT COUNT(DISTINCT posting_key)FROM jobs').fetchone()[0]==30
    finally:conn.close()


def test_real_ajax_replacement_pages_use_new_identities_not_url_change(real_board,tmp_path):
    from packtoworkeu.db import database as db
    b=real_board;b.state.update(mode='ajax',total=30)
    path=tmp_path/'ajax.db';db.initialize(path);conn=db.get_connection(path)
    try:
        count=bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),max_pages=10,auto_continue=True)
        r=bp.LAST_INGESTION_REPORT
        assert count==30 and r['listing_completed']and r['completed']
        assert r['jd_coverage']['readable']==30 and r['pages_visited']==3
        assert all(item['url']==b.url for item in r['page_summaries']) # same-document AJAX
        assert conn.execute('SELECT COUNT(DISTINCT posting_key)FROM jobs').fetchone()[0]==30
    finally:conn.close()


def test_real_qt_one_action_continues_only_unread_jds_and_resolves_original_ids(real_board,tmp_path,monkeypatch):
    import time,csv
    from pathlib import Path
    from PySide6.QtCore import QTimer
    from packtoworkeu.tests._qt_support import application,wait_loaders,dispose
    from packtoworkeu.ui.tabs import assisted_ingestion as ui
    from packtoworkeu.db import database as db
    from packtoworkeu import paths,i18n
    b=real_board;b.state.update(mode='numbered',total=1,ready=False)
    app=application();i18n.set_locale('en');path=tmp_path/'ui.db';db.initialize(path)
    with (paths.SCAN_OUTPUT_DIR/'ORIGINAL_career_jobs.csv').open('w',newline='')as f:
        w=csv.DictWriter(f,fieldnames=['Seed URL','Job URL']);w.writeheader();w.writerow({'Seed URL':b.url,'Job URL':b.base+b.path_for(0)})
    db.enqueue_blocked_portals(path,[dict(seed_name='Fixture',url=b.url,access_scope='details',reason='JD gap')],'ORIGINAL')
    notices=[];monkeypatch.setattr(ui.QMessageBox,'information',lambda *args:notices.append(args))
    tab=ui.AssistedIngestionTab(str(path));tab.show();wait_loaders(tab)
    ticks=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(time.monotonic()));timer.start()
    def wait_finished():
        end=time.monotonic()+40
        while tab._worker is not None and time.monotonic()<end:app.processEvents();time.sleep(.002)
        assert tab._worker is None
        wait_loaders(tab)
    try:
        assert not b.launches # refresh/startup must never open a browser
        tab.portals_table.cellWidget(0,4).click();wait_finished()
        r=tab._last_outcome[2]
        assert r['listing_completed']and not r['completed']
        assert r['jd_coverage'].get('readable',0)==0
        assert 'Listing collected; descriptions incomplete'in notices[-1][1]
        root_reads=len([c for c in b.calls if c.startswith('/jobs/?')])
        assert root_reads>=1
        b.state['ready']=True
        tab.launch_btn.click();wait_finished()
        r=tab._last_outcome[2]
        assert r['workflow_plan']['mode']=='details'
        assert r['completed']and r['jd_coverage']['readable']==1
        assert r['queue_reconciliation']['verified']
        assert len([c for c in b.calls if c.startswith('/jobs/?')])==root_reads
        assert tab.portals_table.rowCount()==0
        conn=db.get_connection(path)
        try:
            row=dict(conn.execute('SELECT * FROM jobs').fetchone())
            assert row['visa_sponsorship']=='Unknown'and len(row['description'])>=200
            assert conn.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==1
        finally:conn.close()
        assert len(b.launches)==2 # one owning browser per explicit run, no duplicates
        assert len(ticks)>20 and max(y-x for x,y in zip(ticks,ticks[1:]))<1.0
    finally:timer.stop();dispose(tab)


def test_real_replayed_append_is_bounded_partial_not_success(real_board,tmp_path):
    from packtoworkeu.db import database as db
    b=real_board;b.state.update(mode='append',total=30,replay=True)
    path=tmp_path/'replay.db';db.initialize(path);conn=db.get_connection(path)
    try:
        count=bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),max_pages=5,auto_continue=True)
        r=bp.LAST_INGESTION_REPORT
        assert count==10 and not r['listing_completed']and not r['completed']
        assert r['stop_reason'].startswith('native load-more unproven:')
        assert r['jd_coverage']['readable']==10 # actual readable partial listing may hand off
        assert len([x for x in b.calls if x.startswith('/api/more')])==1
        assert len([x for x in b.calls if x.startswith('/jobs/?')])==1
    finally:conn.close()


def test_real_page_cap_never_claims_total_complete(real_board,tmp_path):
    from packtoworkeu.db import database as db
    b=real_board;b.state.update(mode='numbered',total=152)
    path=tmp_path/'cap.db';db.initialize(path);conn=db.get_connection(path)
    try:
        count=bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),max_pages=1,auto_continue=True)
        r=bp.LAST_INGESTION_REPORT
        assert count==12 and not r['listing_completed']and not r['completed']
        assert r['jd_coverage']['readable']==12
        assert r['stated_total']==152
    finally:conn.close()


def test_real_stop_preserves_saved_jobs_without_more_requests(real_board,tmp_path):
    import threading
    from packtoworkeu.db import database as db
    b=real_board;b.state.update(mode='append',total=30)
    path=tmp_path/'cancel.db';db.initialize(path);conn=db.get_connection(path);stop=threading.Event()
    def progress(message):
        if '10 jobs ingested'in message:stop.set()
    try:
        bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),max_pages=5,cancel_event=stop,progress_callback=progress,auto_continue=True)
        r=bp.LAST_INGESTION_REPORT
        assert r['cancelled']and not r['completed']
        assert conn.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==10
        assert not any('/api/more'in x for x in b.calls)
    finally:conn.close()


def test_real_browser_close_during_details_keeps_durable_unattempted_rows(real_board,tmp_path):
    from packtoworkeu.db import database as db
    b=real_board;b.state.update(mode='numbered',total=3)
    path=tmp_path/'closed.db';db.initialize(path);conn=db.get_connection(path);closed=[]
    def progress(message):
        if 'Reading full job descriptions'in message and not closed:
            b.pages[0].close();closed.append(True)
    try:
        bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),progress_callback=progress,auto_continue=True)
        r=bp.LAST_INGESTION_REPORT
        assert closed and r['jd_interrupted']and not r['completed']
        assert r['listing_completed'] # later close never erases a verified phase
        assert conn.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==3
        assert r['jd_coverage'].get('unattempted',0)>=1
    finally:conn.close()


def test_real_challenge_is_not_bypassed_or_reported_empty(real_board,tmp_path):
    from packtoworkeu.db import database as db
    b=real_board;b.state.update(wall=True)
    path=tmp_path/'wall.db';db.initialize(path);conn=db.get_connection(path)
    try:
        count=bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),clear_wait_s=.15,auto_continue=True)
        r=bp.LAST_INGESTION_REPORT
        assert count==0 and not r['completed']and not r['listing_completed']
        assert 'verification'in r['stop_reason']and 'unresolved'in r['stop_reason']
        assert not any(x.startswith('/job/')for x in b.calls)
    finally:conn.close()


def test_auto_plan_exact_scope_and_no_invented_resume(tmp_path,monkeypatch):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.assisted_resume import save_batch
    from packtoworkeu.core.assisted_workflow import plan_collection
    from packtoworkeu.core.job_identity import portal_queue_key
    from packtoworkeu import paths
    monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',tmp_path/'output')
    path=tmp_path/'plan.db';db.initialize(path);conn=db.get_connection(path)
    root='https://fixture.invalid/jobs/?country=DE'
    rows=[dict(url='https://fixture.invalid/job/10000',title='Platform Engineer',jd_read_status='unattempted')]
    try:
        assert plan_collection(conn,root,'Fixture')['mode']=='fresh'
        save_batch(conn,root,'Fixture',rows,run_id='LISTING',listing_completed=False,pages=2,continuation={})
        assert plan_collection(conn,root,'Fixture')['mode']=='fresh'
        assert plan_collection(conn,root.replace('DE','IT'),'Fixture')['mode']=='fresh'
        state=dict(url=root+'&Page=3',source_url=root+'&Page=2',page=3,provenance='observed_forward_link',portal_key=portal_queue_key(root))
        save_batch(conn,root,'Fixture',rows,run_id='PARTIAL',listing_completed=False,pages=2,continuation=state)
        assert plan_collection(conn,root,'Fixture')['mode']=='listing'
        save_batch(conn,root,'Fixture',rows,run_id='COMPLETE',listing_completed=True,pages=3)
        assert plan_collection(conn,root,'Fixture')['mode']=='details'
    finally:conn.close()


def test_ajax_active_pager_controls_forward_links_even_without_url_change():
    from types import SimpleNamespace
    root='https://fixture.invalid/jobs/?country=DE'
    data=[dict(h=root+'&Page=1',rel=''),dict(h=root+'&Page=2',rel='',active=True),dict(h=root+'&Page=3',rel='')]
    page=SimpleNamespace(eval_on_selector_all=lambda *a:data)
    assert bp.paging_href_candidates(page,root,set())==[root+'&Page=3']
    assert not bp.paging_href_candidates(page,root+'&Page=3',set())
    data[:]=[dict(h='https://fixture.invalid/jobs/?Page=3',rel='next')]
    assert not bp.paging_href_candidates(page,root,set()) # no lost country filter


@pytest.mark.parametrize('phase',['listing','detail'])
def test_real_explicit_local_human_clearance_uses_attention_not_obsolete_message(real_board,tmp_path,phase):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.assisted_control import AssistedControl
    b=real_board;b.state.update(mode='numbered',total=3,human_gate=phase)
    path=tmp_path/'cleared.db';db.initialize(path);conn=db.get_connection(path);clears=[]
    def attention(info):
        if info.get('waiting'):
            assert info['phase']==phase
            page=next(p for p in b.pages if not p.is_closed() and p.url==info['url'])
            page.locator('#human-clear').click();clears.append(info)
    try:
        count=bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),clear_wait_s=2,control=AssistedControl(attention),auto_continue=True)
        r=bp.LAST_INGESTION_REPORT
        assert clears and count==3 and r['completed']and r['listing_completed']
        assert r['jd_coverage']['readable']==3 and len(b.launches)==1
        assert len([x for x in b.calls if x.startswith('/jobs/?')])==1
        assert conn.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==3
    finally:conn.close()


@pytest.mark.parametrize('failure',['invalid_encoding','unreadable'])
def test_unreadable_original_inventory_fails_closed_without_stopping_collection(tmp_path,monkeypatch,failure):
    from packtoworkeu import paths
    from packtoworkeu.core.assisted_workflow import required_postings,reconcile_details
    from pathlib import Path
    monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',tmp_path)
    file=tmp_path/'ORIGINAL_career_jobs.csv';file.write_bytes(b'\xff\xff')
    if failure=='unreadable':
        original=Path.open
        def denied(self,*a,**kw):
            if self==file:raise PermissionError('local fixture denied')
            return original(self,*a,**kw)
        monkeypatch.setattr(Path,'open',denied)
    required=required_postings(dict(access_scope='details',run_id='ORIGINAL',url='https://fixture.invalid/jobs/'))
    assert required is None
    assert not reconcile_details(required,[dict(url='https://fixture.invalid/job/123456',jd_read_status='readable')])['verified']



def test_real_access_proof_redirect_keeps_identity_and_must_read_the_actual_jd(real_board,tmp_path):
    from packtoworkeu.db import database as db
    b=real_board;b.state.update(mode='numbered',total=3,proof_query=True)
    path=tmp_path/'proof.db';db.initialize(path);conn=db.get_connection(path)
    try:
        count=bp.run_assisted_ingestion(b.url,'Fixture',conn,db_path=str(path),auto_continue=True)
        report=bp.LAST_INGESTION_REPORT
        assert count==3 and report['completed']and report['listing_completed']
        assert report['jd_coverage']['readable']==3
        assert report['session_detail_activity']['http_blocked']==1
        assert report['session_detail_activity']['browser']>=1
        assert any('afd_azwaf_tok=LOCAL_FAKE_PROOF'in call for call in b.calls)
        assert conn.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==3
        assert all(row[0]=='Unknown'for row in conn.execute('SELECT visa_sponsorship FROM jobs'))
        from packtoworkeu.core.browser_evidence import public_snapshot
        assert 'LOCAL_FAKE_PROOF'not in str(report.get('session_detail_samples'))
    finally:conn.close()
