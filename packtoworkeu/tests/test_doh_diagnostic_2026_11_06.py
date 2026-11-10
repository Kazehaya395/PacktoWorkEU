"""Regression: removed DNS experiments cannot affect normal collection.

Retained filename replaces obsolete experimental tests in incremental updates.
"""
import os,time,json
from types import SimpleNamespace
import pytest
from packtoworkeu.core import blocked_portals as bp

@pytest.mark.parametrize('wall',[False,True])
def test_normal_launch_and_wall_guards_after_diagnostic_removal(monkeypatch,tmp_path,wall):
    if os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1':pytest.skip('opt-in intercepted Chromium')
    from playwright.sync_api import BrowserType,Browser,Page,APIRequestContext
    from packtoworkeu.db import database as db
    from packtoworkeu import paths
    from packtoworkeu.scanning.career.career_scanner import ProductionScannerConfig
    launch0=BrowserType.launch;context0=Browser.new_context;wait0=Page.wait_for_timeout
    launches=[];contexts=[];requests=[]
    root='https://jobs.fixture.invalid/search';job='https://jobs.fixture.invalid/jobs/123451/engineer'
    def serve(route):
        url=route.request.url;requests.append(url);assert url.startswith('https://jobs.fixture.invalid/')
        body=('<title>Just a moment...</title><div id=cf-challenge-running>Checking your browser</div>'if wall else
            '<main><h1>Jobs</h1><article><a href="'+job+'">Backend Engineer</a></article></main>')if url==root else '<main><h1>Backend Engineer</h1><div class=job-description>'+('Responsibilities: Build services. Requirements: Engineering experience. '*12)+'</div></main>'
        route.fulfill(status=200,content_type='text/html',body=body)
    def launch(self,**kw):launches.append(kw);return launch0(self,headless=True,args=['--no-sandbox'])
    def context(self,**kw):
        contexts.append(kw);ctx=context0(self,**kw);ctx.route('**/*',serve);return ctx
    monkeypatch.setattr(BrowserType,'launch',launch)
    monkeypatch.setattr(Browser,'new_context',context)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda self,ms:wait0(self,min(ms,10)))
    monkeypatch.setattr(APIRequestContext,'get',lambda self,url,**kw:SimpleNamespace(status=403,url=url,text=lambda:'<h1>Access denied</h1>',dispose=lambda:None))
    monkeypatch.setattr(bp,'time',SimpleNamespace(monotonic=time.monotonic,sleep=lambda _:None))
    monkeypatch.setattr(ProductionScannerConfig,'ENABLE_DETAIL_SCAN',True)
    monkeypatch.setattr(ProductionScannerConfig,'ASSISTED_DETAIL_TABS',1)
    monkeypatch.setattr(paths,'ensure_scan_output_dir',lambda:tmp_path)
    path=tmp_path/'normal.db';db.initialize(path);conn=db.get_connection(path)
    try:
        bp.run_assisted_ingestion(root,'Fixture',conn,max_pages=1,clear_wait_s=.1,db_path=str(path))
        assert len(launches)==1 and launches[0]['headless']is False
        assert launches[0]['args'][:-1]==['--disable-blink-features=AutomationControlled','--disable-gpu','--start-maximized']
        assert launches[0]['args'][-1].startswith('--crash-dumps-dir=')
        assert launches[0]['executable_path'] and launches[0]['channel']=='chromium'
        assert 'PacktoWorkEU'in launches[0]['env']['HOME'] or '.packtoworkeu'in launches[0]['env']['HOME']
        assert contexts[0]['no_viewport']and 'Chrome/126.0.0.0'in contexts[0]['user_agent']
        report=bp.LAST_INGESTION_REPORT
        assert report['ingested']==(0 if wall else 1)
        if wall:assert job not in requests
        else:assert report['jd_coverage']['readable']==1
        info=json.loads(conn.execute("SELECT message FROM scan_events WHERE phase='runtime_info'").fetchone()[0])
        assert 'browser_diagnostic'not in info and 'dns_profile_removed'not in info
    finally:conn.close()
