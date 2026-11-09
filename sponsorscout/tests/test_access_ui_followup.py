"""Approved Windows run follow-up: local evidence and honest UI; no live portals."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from sponsorscout.tests._qt_support import application,wait_loaders,dispose

@pytest.mark.skipif(os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1',reason='local real Chromium opt-in')
@pytest.mark.parametrize('foreground',[False,True])
def test_regular_detail_uses_current_visible_content_not_stale_status(foreground):
    from playwright.sync_api import sync_playwright
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
    with sync_playwright()as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page()
            page.set_content('<title>Access to this page has been denied</title><h1>Platform Engineer</h1><main><section class="job-description">'+('Responsibilities: Build reliable Python services. Requirements: Python and SQL. '*12)+'</section></main><div id="px-captcha" style="display:none">Press &amp; Hold</div>'+('<div style="position:fixed;inset:0;z-index:999999;background:white">Verify you are human</div>'if foreground else''))
            page._last_nav_status=403
            s=CareerPortalScanner(skip_preflight=True)
            reason=s._bot_challenge_reason(page,subject='detail')
            assert bool(reason)==foreground
        finally:browser.close()


def test_dashboard_names_saved_rows_without_claiming_jd_verification(tmp_path):
    from sponsorscout.db import database as db
    from sponsorscout.ui.tabs.dashboard import DashboardTab
    application();path=tmp_path/'ui.db';db.initialize(path);w=DashboardTab(str(path));wait_loaders(w)
    try:
        assert 'Saved Jobs'in w.CARD_KEYS and 'Verified Jobs'not in w.CARD_KEYS
        assert 'description'in w._value_labels['Saved Jobs'].toolTip().lower()
    finally:dispose(w)


def test_queue_columns_resize_persist_and_full_reason_is_visible(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QHeaderView
    from sponsorscout.db import database as db
    from sponsorscout.ui.tabs.assisted_ingestion import AssistedIngestionTab
    application();p=tmp_path/'ui.db';db.initialize(p);w=AssistedIngestionTab(str(p));wait_loaders(w)
    row=dict(url_key='fixture',seed_name='Fixture',url='https://fixture.invalid/jobs',phase='career',reason='Two detail attempts denied; remaining descriptions unattempted.',status='pending',access_scope='details')
    try:
        h=w.portals_table.horizontalHeader()
        assert all(h.sectionResizeMode(i)==QHeaderView.Interactive for i in range(5))
        w._apply_queue([row]);w.portals_table.selectRow(0)
        assert row['reason']in w.queue_details.toPlainText()
        assert 'completeness'in w.queue_details.toPlainText().lower()
        h.resizeSection(2,367);h.resizeSection(4,219);w._apply_queue([row]);assert h.sectionSize(2)==367
        from sponsorscout import i18n
        monkeypatch.setattr(w,'refresh_queue',lambda:None)
        for locale in ('it','en'):
            monkeypatch.setattr(i18n,'_locale',locale);w.retranslate()
            assert h.sectionSize(2)==367 and h.sectionSize(4)==219
    finally:w.shutdown();dispose(w)
    w=AssistedIngestionTab(str(p));wait_loaders(w)
    try:assert w.portals_table.columnWidth(2)==367
    finally:w.shutdown();dispose(w)

@pytest.mark.skipif(os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1',reason='local real Chromium opt-in')
@pytest.mark.parametrize('scenario',['late_clearance','foreground','plain403','foreign','rate429','cancel'])
def test_regular_browser_detail_passive_readiness_and_security_guards(scenario):
    import json,threading
    from playwright.sync_api import sync_playwright
    from sponsorscout.scanning.common import ScanCancelled
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
    text='Responsibilities: Build reliable Python services. Requirements: Python and SQL. '*12
    document='<title>Access to this page has been denied</title><h1>Platform Engineer</h1><main><section class="job-description">'+text+'</section></main><script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Platform Engineer','description':text})+'</script>'
    url='https://fixture.invalid/job/platform-engineer-123456'
    with sync_playwright()as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page();calls=[];s=CareerPortalScanner(skip_preflight=True);s.cancel_event=threading.Event()
            def serve(route):
                calls.append(route.request.url)
                status=403;headers={'Content-Type':'text/html'}
                if scenario=='late_clearance':
                    body='<title>Just a moment...</title><h1>Checking your browser</h1><div id="cf-challenge-running">Verify you are human</div><script>setTimeout(()=>{document.body.innerHTML='+json.dumps(document).replace('<',chr(92)+'u003c')+'},1000)</script>'
                elif scenario in('foreground','cancel'):body=document+'<div style="position:fixed;inset:0;z-index:999999;background:white">Verify you are human</div>'
                elif scenario=='plain403':body='<title>Forbidden</title><h1>403 Forbidden</h1>'
                elif scenario=='rate429':status=429;headers['Retry-After']='120';body='<h1>Too many requests</h1>'
                else:status=200;body=document.replace('Access to this page has been denied','Platform Engineer')+"<script>history.replaceState(null,'','/job/different-654321')</script>"
                route.fulfill(status=status,headers=headers,body=body)
            page.route('**/*',serve)
            if scenario=='cancel':page.on('domcontentloaded',lambda:s.cancel_event.set())
            if scenario=='cancel':
                with pytest.raises(ScanCancelled):s._fetch_jd_text_browser(url,browser_page=page)
            else:
                result=s._fetch_jd_text_browser(url,browser_page=page)
                if scenario=='late_clearance':
                    assert result and len(result[0])>=200, (s._detail_state.outcomes.get(url),page.content()[:400])
                    assert s._detail_state.outcomes[url]['kind']=='readable'
                    assert s._detail_state.outcomes[url]['http_status']==403  # historical status not fabricated200
                else:
                    assert not result
                    assert s._detail_state.outcomes[url]['kind']==('failed'if scenario=='foreign'else'blocked')
                if scenario=='rate429':assert s._http_schedule.is_cooling(url)
                if scenario=='plain403':assert 'human verification not confirmed'in s._detail_state.outcomes[url]['reason']
            assert len(calls)==1  # no reloads/new probes
        finally:browser.close()


@pytest.mark.parametrize('locale',['en','it'])
def test_uploaded_aggregate_classified_is_not_readable(tmp_path,monkeypatch,locale):
    from sponsorscout import i18n
    from sponsorscout.db import database as db
    from sponsorscout.ui.tabs.tools import ToolsTab
    from sponsorscout.application.scan_progress import ScanProgressState
    application();monkeypatch.setattr(i18n,'_locale',locale)
    path=tmp_path/'ui.db';db.initialize(path);w=ToolsTab(str(path));wait_loaders(w)
    try:
        m=ScanProgressState();m.apply(dict(kind='configure',total=1))
        m.apply(dict(kind='task_begin',task='a',name='Uploaded aggregate',attempt=1))
        m.apply(dict(kind='jd_snapshot',task='a',attempt=1,coverage=dict(observed=243,readable=78,empty=2,blocked=4,unattempted=159)))
        m.apply(dict(kind='task_end',task='a',attempt=1,status='partial'))
        w._work_snapshot=m.snapshot();w._work_finished=True;w._work_control='partial';w._render_work_progress()
        assert w.scan_bar.value()==100 and w.jd_bar.value()==34
        # A finished run has static readability, not a classified-attempt bar.
        assert w.jd_bar.isHidden()
        assert all(value in w.jd_progress_label.text() for value in ('78/243', '32.1%', '165'))
        assert '84/243' not in w.jd_progress_label.text()
        assert all(value in w.jd_outcomes.text() for value in ('78', '2', '4', '159'))
        assert w.jd_progress_label.text() == i18n._(
            'Final description results: {readable}/{observed} readable ({readable_percent}%); {unresolved} unresolved. No description workers are running.'
        ).format(readable=78, observed=243, readable_percent='32.1', unresolved=165)
    finally:dispose(w)
