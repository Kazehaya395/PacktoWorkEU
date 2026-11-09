"""Shared localized access, admitted native URLs and safe partial JD hand-off."""
import os
import threading
from types import SimpleNamespace
import pytest
from sponsorscout.core import blocked_portals as bp, browser_evidence as be
from sponsorscout.scanning.response_evidence import challenge_reason
from sponsorscout.tests.test_primary_sections_native_2026_11_01 import page
from sponsorscout.tests.test_access_readiness_2026_11_06 import runtime
from sponsorscout.tests.test_career_verification_readiness_2026_11_05 import Page, POSTING

@pytest.mark.parametrize('title,prompt',[
    ('Ci siamo quasi…','Verifica di essere umano'),
    ('Un momento…','Verifica di sicurezza'),
    ('Just a moment…','Checking your browser'),
    ('Un momento...','Verificación de seguridad'),
    ('Einen Moment bitte…','Sicherheitsüberprüfung'),
    ('Veuillez patienter…','Vérification de sécurité'),
    ('Um momento…','Verificação de segurança'),
])
def test_localized_active_shell(title,prompt):
    html=f'<title>{title}</title><h1>{prompt}</h1><p>Cloudflare</p>'
    assert 'challenge page' in challenge_reason(html,status=403)
    assert 'job detail' in challenge_reason(html,status=403,subject='detail')

@pytest.mark.parametrize('markup',[
    '<title>Ci siamo quasi…</title><p>Application almost complete.</p>',
    '<title>Un momento…</title><script src="https://challenges.cloudflare.com/sdk.js"></script>',
    POSTING+'<p>Verifica di sicurezza</p><div class="cf-turnstile"></div>',
    POSTING.replace('<title>', '<title>Ci siamo quasi… | '),
    '<title>Careers</title><script>Verifica di sicurezza</script>',
])
def test_localized_passive_text_is_not_a_wall(markup):
    assert not challenge_reason(markup)


def test_localized_wall_gets_verification_budget_and_recovers(runtime):
    scanner,clock=runtime
    wall='<title>Ci siamo quasi…</title><h1>Verifica di sicurezza</h1><p>Cloudflare</p>'
    page=Page(clock,[wall,wall,wall,POSTING]);state={};notes=[]
    assert scanner._observe_seed_verification(page,challenge_reason(wall,status=403),notes,100,state)==''
    assert clock.now>100.6 and state['verification_seen']
    assert state['outcome']=='recovered_content'


def cards():
    return ''.join(f'<article role="link" data-job-url="/jobs/{123450+i}/engineer">Backend Engineer {i}</article>'for i in range(3))


def test_dom_urls_share_census_growth_and_hidden_recommendations_do_not(page):
    page.set_content('<main><h1>Jobs</h1><div id="cards">'+cards()+'''</div><button onclick="document.querySelector('#cards').insertAdjacentHTML('beforeend','<article role=link data-job-url=/jobs/123499/engineer>Backend Engineer New</article>')">Load more</button></main>
    <aside><a href="/jobs/999999/engineer">Sidebar Engineer</a></aside>
    <div hidden><a href="/jobs/999998/engineer">Hidden Engineer</a></div>''')
    links,meta=be.admitted_job_links(page)
    assert meta['ready'] and len(links)==3
    assert bp._native_append_advance(page) is True
    assert len(be.admitted_job_links(page)[0])==4
    assert 'new posting URL' in page._ss_native_append_diagnostic

@pytest.mark.parametrize('mode',['accessible','known_wall','wall','blank','closed','save','stop'])
def test_partial_guard_requires_current_readable_session(page,mode):
    page.set_content('<main><h1>Jobs</h1>'+cards()+'</main>')
    cancel=threading.Event();control=SimpleNamespace(save_requested=mode=='save')
    if mode=='wall':page.set_content('<title>Un momento…</title><div id="challenge-running">Verifica di sicurezza</div>')
    if mode=='blank':page.set_content('<main>Loading</main>')
    if mode=='closed':page.close()
    if mode=='stop':cancel.set()
    reason=bp._partial_listing_detail_gate(page,mode=='known_wall',control,cancel)
    assert bool(reason)==(mode!='accessible')


def test_ambiguous_native_controls_have_specific_diagnostic(page):
    page.set_content('<main>'+cards()+'<button>Load more</button><button>Load more</button></main>')
    assert bp._native_append_advance(page) is False
    assert page._ss_native_append_diagnostic=='ambiguous primary append controls'

from sponsorscout.db import database as db
from sponsorscout.tests.test_finishing_2026_11_01 import BASE

@pytest.mark.parametrize("mode",["accessible","renewed_wall"])
def test_actual_partial_append_checkpoint_and_jd_gate(monkeypatch,tmp_path,mode):
    if os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1':pytest.skip('opt-in intercepted Chromium')
    import time
    from urllib.parse import urlsplit,parse_qs
    from playwright.sync_api import BrowserType,Browser,Page,APIRequestContext
    from sponsorscout.scanning.career.career_scanner import ProductionScannerConfig
    from sponsorscout import paths
    launch0=BrowserType.launch;context0=Browser.new_context;wait0=Page.wait_for_timeout
    root='https://jobs.fixture.invalid/stellenangebote'
    listing='<main><h1>23 Jobs</h1>'+''.join('<article><a href="?jid='+str(100000+i)+'">Backend Engineer '+str(i)+'</a><p>Location: Berlin, Germany</p></article>'for i in range(5))+'<button onclick="'+("document.body.innerHTML=`<h1>Checking your browser</h1><div id=challenge-running>Verifying</div>`"if mode=='renewed_wall'else'void(0)')+'">Load more</button></main>'
    detail_requests=[]
    def serve(route):
        assert route.request.url.startswith('https://jobs.fixture.invalid/')
        jid=parse_qs(urlsplit(route.request.url).query).get('jid',[''])[0]
        if jid:detail_requests.append(jid)
        body=('<main><h1>Backend Engineer '+jid+'</h1><article><h2>Job Description</h2>'+BASE+' Posting '+jid+'<h2>Qualifications</h2>Software engineering experience.<h2>Benefits</h2>Learning budget.</article></main>')if jid else listing
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
        bp.run_assisted_ingestion(root,'Fixture',conn,max_pages=8,clear_wait_s=.1,db_path=str(path))
        report=bp.LAST_INGESTION_REPORT
        assert report['ingested']==5,{key:report.get(key)for key in ('stop_reason','harvest_census','browser_error')}
        assert not report['completed'],report['stop_reason']
        assert report['stop_reason'].startswith('native load-more unproven:'),report['stop_reason']
        rows=conn.execute('SELECT * FROM jobs').fetchall()
        assert len(rows)==5 and len({r['url']for r in rows})==5
        assert all(r['visa_sponsorship']=='Unknown'for r in rows)
        expected=5 if mode=='accessible'else 0
        assert report['jd_coverage'].get('readable',0)==expected,report['jd_coverage']
        assert len(set(detail_requests))==expected
        if not expected:assert report['jd_coverage']['budget_skipped']==5
        from sponsorscout.core.assisted_resume import list_batches
        batches=list_batches(conn,root,"Fixture")
        assert batches and not batches[0]['listing_completed']
    finally:conn.close()

