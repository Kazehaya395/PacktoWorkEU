"""Permanent integration contracts for repeat scans; synthetic data only."""
import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
import pytest
from packtoworkeu.core.saved_details import SavedDetailReader, capture_proof
from packtoworkeu.tests.test_session_reuse_followup import saved, URL, TEXT
from packtoworkeu.tests.test_access_followup_2026_11_08 import scanner
from packtoworkeu.tests.test_custom_scan_scope import scoped_env, _FakeAts, _FakeCareer
from packtoworkeu.tests.test_two_operation_progress_2026_11_05 import tools
from packtoworkeu.tests.test_assisted_clean_ui_2026_11_02 import clean_tab


def test_policy_miss_does_not_consult_unproven_disk_cache(saved, monkeypatch):
    from packtoworkeu.scanning.career import career_scanner as module
    path,c=saved;s=scanner();s._saved_jd_reader=SavedDetailReader(path,force_refresh=True)
    calls=[]
    class Disk:
        def get(self,url):pytest.fail('legacy 14-day cache bypassed refresh policy')
        def put(self,*args):pass
    monkeypatch.setattr(module,'_jd_cache',lambda:Disk())
    monkeypatch.setattr(s,'_fetch_jd_text_uncached',lambda url:(calls.append(url) or (TEXT,'Berlin','')))
    assert s._fetch_jd_text(URL,allow_browser=False)[0]==TEXT
    assert calls==[URL]
    assert c.execute('SELECT description FROM jobs').fetchone()[0]==TEXT


def test_later_title_change_invalidates_saved_memory_and_state_aliases(saved):
    path,c=saved;s=scanner();reader=s._saved_jd_reader=SavedDetailReader(path)
    assert s.get_captured_jd(URL)==TEXT.strip()
    reader.observe(URL,title='Senior Platform Engineer')
    alias=URL+'?utm_source=repeat'
    assert s.get_captured_jd(alias)==''
    assert s._detail_state.get(URL) is None
    assert not capture_proof(s,URL,TEXT)


def test_long_card_never_acquires_saved_description_capture_proof(saved):
    path,c=saved;s=scanner();s._saved_jd_reader=SavedDetailReader(path)
    card='Unproven listing card. '*500
    s.store_captured_jd(URL,card)
    assert s.get_captured_jd(URL)==TEXT.strip()
    assert capture_proof(s,URL,TEXT)
    assert not capture_proof(s,URL,card)


def test_force_does_not_reuse_long_unproven_card(saved):
    path,c=saved;s=scanner();s._saved_jd_reader=SavedDetailReader(path,force_refresh=True)
    s.store_captured_jd(URL,'Long listing-card teaser. '*200)
    assert s.get_captured_jd(URL)==''


def test_proven_saved_description_is_not_clipped_or_retimestamped(saved):
    path,c=saved;long=TEXT*40
    stamp=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    marker=f'jd_read_status=readable; jd_sha256={hashlib.sha256(long.encode()).hexdigest()}; jd_captured_at={stamp}'
    c.execute('UPDATE jobs SET description=?,support_evidence_type=?',(long,marker));c.commit()
    s=scanner();s._saved_jd_reader=SavedDetailReader(path)
    assert len(long)>32000
    assert s.get_captured_jd(URL)==long.strip()
    assert stamp in capture_proof(s,URL,long)


def test_actual_semantic_http_read_has_capture_proof_and_can_be_reused(saved):
    path,c=saved;s=scanner();s._saved_jd_reader=SavedDetailReader(path,force_refresh=True)
    html='<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Platform Engineer','description':TEXT,'url':URL})+'</script>'
    result=s._remember_detail_html(URL,html,transport='HTTP',status=200)
    s.store_captured_jd(URL,result[0])
    assert capture_proof(s,URL,result[0])
    assert s.get_captured_jd(URL)==result[0]


def test_card_and_clipped_detail_do_not_get_full_jd_proof(saved):
    path,c=saved;s=scanner();s._saved_jd_reader=SavedDetailReader(path)
    text=TEXT*20
    html='<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','description':text,'url':URL})+'</script>'
    result=s._remember_detail_html(URL,html,transport='HTTP',status=200)
    assert len(text)>8000 and len(result[0])==8000
    assert not capture_proof(s,URL,result[0])


def test_unavailable_database_never_fabricates_new_jobs(tmp_path):
    reader=SavedDetailReader(tmp_path/'missing.db')
    reader.observe(URL,title='Platform Engineer')
    assert reader.read(URL) is None
    assert reader.summary()['jobs_new']==0
    assert not reader.summary()['tracking_complete']


def test_tracking_limit_fails_closed_without_unbounded_metadata(saved):
    path,c=saved;reader=SavedDetailReader(path);reader.limit=0
    reader.observe(URL,title='Changed title')
    assert reader.read(URL) is None
    assert reader.rejects_previous(URL)
    assert not reader.summary()['tracking_complete']
    assert len(reader._observed)==0


def test_unattempted_is_not_blocked_and_read_success_supersedes_failure(saved):
    path,c=saved;reader=SavedDetailReader(path)
    reader.observe(URL,title='Platform Engineer')
    reader.note_outcome(URL,'unattempted')
    assert reader.summary()['jd_pending']==1 and reader.summary()['jd_blocked']==0
    reader.note_outcome(URL,'blocked')
    assert reader.summary()['jd_blocked']==1
    reader.read(URL)
    assert reader.summary()['jd_pending']==0 and reader.summary()['jd_blocked']==0


def test_campaign_force_resume_can_share_only_proven_campaign_reads(saved):
    path,c=saved;reader=SavedDetailReader(path,force_refresh=True)
    reader.policy_started_at=reader.started_at-timedelta(minutes=1)
    assert reader.read(URL)
    assert reader.summary()['jd_reused']==1 and reader.summary()['jd_fresh']==0
    reader2=SavedDetailReader(path,force_refresh=True)
    reader2.policy_started_at=reader2.started_at-timedelta(microseconds=1)
    assert reader2.read(URL) is None


def test_csv_ingestion_observes_title_before_jd_lookup_and_keeps_original_stamp(tmp_path):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.persistence import upsert_job
    from packtoworkeu.scanning import pipeline
    from packtoworkeu.tests.test_pipeline import _career_row
    path=tmp_path/'jobs.db';db.initialize(path);c=db.get_connection(path)
    stamp=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    marker=f'jd_read_status=readable; jd_sha256={hashlib.sha256(TEXT.encode()).hexdigest()}; jd_captured_at={stamp}'
    upsert_job(c,dict(title='Platform Engineer',company='Fixture',url=URL,description=TEXT,support_evidence_type=marker));c.close()
    row=_career_row(**{'Job URL':URL,'Job Title':'Senior Platform Engineer','Canonical Job ID':'','Support Evidence Type':'none'})
    export=tmp_path/'jobs.csv'
    with export.open('w',newline='')as fh:
        writer=csv.DictWriter(fh,fieldnames=list(row));writer.writeheader();writer.writerow(row)
    reader=SavedDetailReader(path)
    def lookup(url):
        assert reader.rejects_previous(url)
        assert reader.read(url) is None
        return ''
    pipeline._ingest_output_csv(path,export,'r1','direct',seen_canonical=set(),jd_lookup=lookup,refresh_reader=reader)
    assert reader.summary()['jobs_existing']==1
    c=db.get_connection(path)
    assert c.execute('SELECT description FROM jobs').fetchone()[0]==TEXT
    assert stamp in c.execute('SELECT support_evidence_type FROM jobs').fetchone()[0]
    c.close()


def test_force_flag_and_career_only_scope_survive_resume(scoped_env,tmp_path,monkeypatch):
    from packtoworkeu.scanning import pipeline
    from packtoworkeu.application import scan_scope
    out=tmp_path/'out';out.mkdir();monkeypatch.setattr(pipeline.paths,'ensure_scan_output_dir',lambda:out)
    first=pipeline.run_scan(db_path=scoped_env,run_ats=False,only_career=['CarOne'],refresh_descriptions=True)
    plan=scan_scope.load_plan(scoped_env,first['run_id'])
    assert plan['refresh_descriptions'] is True and plan['ats']==[]
    assert _FakeCareer.instances[-1]._saved_jd_reader.force_refresh
    _FakeAts.instances.clear();_FakeCareer.instances.clear()
    second=pipeline.run_scan(db_path=scoped_env,resume_from=first['run_id'],run_ats=True,only_career=['CarTwo'],refresh_descriptions=False)
    assert _FakeAts.instances==[]
    assert _FakeCareer.instances[0].kw['only_companies']==['CarOne']
    assert _FakeCareer.instances[0]._saved_jd_reader.force_refresh
    assert second['repeat_scan']['force_refresh']


def test_old_plan_defaults_to_normal_reuse_even_if_new_checkbox_true(scoped_env,tmp_path,monkeypatch):
    from packtoworkeu.scanning import pipeline
    from packtoworkeu.application import scan_scope
    out=tmp_path/'out';out.mkdir();monkeypatch.setattr(pipeline.paths,'ensure_scan_output_dir',lambda:out)
    first=pipeline.run_scan(db_path=scoped_env,run_ats=False,only_career=['CarOne'])
    from packtoworkeu.db import database as db
    c=db.get_connection(scoped_env);plan=scan_scope.load_plan(scoped_env,first['run_id']);plan.pop('refresh_descriptions')
    c.execute('UPDATE scan_run_scopes SET scope_json=? WHERE run_id=?',(json.dumps(plan),first['run_id']));c.commit();c.close()
    result=pipeline.run_scan(db_path=scoped_env,resume_from=first['run_id'],refresh_descriptions=True)
    assert not result['repeat_scan']['force_refresh']


def test_advanced_refresh_is_off_and_summary_retranslates_without_losing_state(tools,clean_tab,monkeypatch):
    from packtoworkeu import i18n
    counts=dict(jobs_new=2,jobs_existing=9,jd_reused=7,jd_fresh=2,jd_pending=2,jd_blocked=1,jd_inconclusive=1)
    assert not tools.refresh_jd_cb.isChecked() and not clean_tab.refresh_jd_cb.isChecked()
    assert tools.advanced_panel.isHidden() and clean_tab.advanced_panel.isHidden()
    tools._last_repeat_summary=counts;clean_tab._last_outcome=(11,'',dict(repeat_scan=counts))
    for locale in ('it','en','it','en'):
        monkeypatch.setattr(i18n,'_locale',locale);tools.retranslate();clean_tab.retranslate()
        assert ('già salvate' in tools.repeat_summary.text())==(locale=='it')
        assert ('già salvate' in clean_tab.repeat_summary.text())==(locale=='it')
        assert tools._last_repeat_summary==counts
        assert tools.advanced_panel.isHidden() and clean_tab.advanced_panel.isHidden()
    for key,value in i18n._INCREMENTAL_REFRESH_IT.items():
        assert value and value!=key


def test_force_requires_confirmation_and_is_consumed_once(tools,monkeypatch):
    from packtoworkeu.ui.tabs import tools as module
    from PySide6.QtWidgets import QMessageBox
    calls=[];monkeypatch.setattr(tools.coordinator,'start',lambda *a,**kw:calls.append(kw))
    tools.refresh_jd_cb.setChecked(True)
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.No)
    tools.start_scan();assert calls==[] and tools.refresh_jd_cb.isChecked()
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    tools.start_scan();assert calls==[{'refresh_descriptions':True}]
    assert not tools.refresh_jd_cb.isChecked()


def test_priority_does_not_count_eligibility_as_successful_reuse(saved):
    path,c=saved;reader=SavedDetailReader(path)
    reader.observe(URL,title='Platform Engineer')
    reader.observe(URL.replace('123456','999999'),title='New Engineer')
    assert reader.priority(URL)==1
    assert reader.priority(URL.replace('123456','999999'))==0
    assert reader.summary()['jd_reused']==0
    reader.observe(URL,title='Changed Engineer')
    assert reader.priority(URL)==0


def test_three_real_local_pipeline_runs_find_new_job_reuse_and_force(tmp_path,monkeypatch):
    """Real HTTP/SQLite/Career path; provider index is a controlled fixture."""
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from packtoworkeu.scanning import pipeline
    from packtoworkeu.application import seed_manager
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    requests=[];count=[3];titles={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            requests.append(self.path)
            url=f'http://127.0.0.1:{self.server.server_port}'+self.path
            index=int(self.path.rsplit('-',1)[-1])-100000
            data={'@type':'JobPosting','title':titles.get(index,'Platform Engineer'),'url':url,'description':TEXT,
                  'jobLocation':{'@type':'Place','address':{'addressLocality':'Berlin','addressCountry':'DE'}}}
            body=('<h1>Platform Engineer</h1><script type="application/ld+json">'+json.dumps(data)+'</script>').encode()
            self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        origin=f'http://127.0.0.1:{server.server_port}'
        seed=tmp_path/'career.csv';seed.write_text('name,careers_url,provider,board_slug,source_type,target_country,scope_policy\nFixture,'+origin+'/jobs,greenhouse,fixture,direct_employer,Global,global\n')
        out=tmp_path/'exports';out.mkdir()
        monkeypatch.setattr(seed_manager,'user_career_path',lambda:seed)
        monkeypatch.setattr(seed_manager,'user_ats_path',lambda:seed)
        monkeypatch.setattr(pipeline.paths,'ensure_scan_output_dir',lambda:out)
        monkeypatch.setattr(CareerPortalScanner,'_fetch_provider_jobs',lambda self,target:([
            dict(job_title=titles.get(i,'Platform Engineer'),job_url=origin+f'/job/engineer-{100000+i}',
                 location_hint='Berlin, Germany',extraction_method='greenhouse_api')for i in range(count[0])],f'greenhouse API: {count[0]}'))
        path=tmp_path/'jobs.db'
        first=pipeline.run_scan(db_path=path,run_ats=False,only_career=['Fixture'])
        assert not first['errors'],first
        assert first['repeat_scan']['jobs_new']==3
        assert first['repeat_scan']['jd_fresh']==3,first['repeat_scan']
        before=len(requests);count[0]=4
        second=pipeline.run_scan(db_path=path,run_ats=False,only_career=['Fixture'])
        assert not second['errors'],second
        assert second['repeat_scan']['jobs_new']==1 and second['repeat_scan']['jobs_existing']==3
        assert second['repeat_scan']['jd_reused']==3 and second['repeat_scan']['jd_fresh']==1,second['repeat_scan']
        assert len(requests)-before==1,requests
        # The next repeat mixes changed, stale, unread and unchanged jobs.
        from packtoworkeu.db import database as db
        c=db.get_connection(path)
        old_stamp=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        stale_url=origin+'/job/engineer-100001'
        row=c.execute('SELECT support_evidence_type FROM jobs WHERE url=?',(stale_url,)).fetchone()
        import re
        stale_marker=re.sub(r'jd_captured_at=[^;\s]+','jd_captured_at='+old_stamp,row[0])
        c.execute('UPDATE jobs SET support_evidence_type=? WHERE url=?',(stale_marker,stale_url))
        c.execute('UPDATE jobs SET support_evidence_type=? WHERE url=?',('jd_read_status=unattempted',origin+'/job/engineer-100002'))
        c.commit();c.close();titles[0]='Senior Platform Engineer'
        before=len(requests)
        mixed=pipeline.run_scan(db_path=path,run_ats=False,only_career=['Fixture'])
        assert not mixed['errors'],mixed
        assert mixed['repeat_scan']['jd_fresh']==3 and mixed['repeat_scan']['jd_reused']==1,mixed['repeat_scan']
        assert len(requests)-before==3,requests
        before=len(requests)
        forced=pipeline.run_scan(db_path=path,run_ats=False,only_career=['Fixture'],refresh_descriptions=True)
        assert not forced['errors'],forced
        assert forced['repeat_scan']['jd_fresh']==4 and forced['repeat_scan']['jd_reused']==0,forced['repeat_scan']
        assert len(requests)-before==4,requests
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_assisted_checkpoint_freezes_force_policy_and_campaign_start(tmp_path,monkeypatch):
    from packtoworkeu import paths
    from packtoworkeu.db import database as db
    from packtoworkeu.core.assisted_resume import save_batch,load_batch,_validate
    monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',tmp_path/'out')
    p=tmp_path/'jobs.db';db.initialize(p);c=db.get_connection(p)
    stamp=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
    try:
        save_batch(c,'https://careers.fixture.invalid/jobs','Fixture',[dict(url=URL,title='Platform Engineer')],
                   run_id='fixture1',listing_completed=True,pages=1,refresh_descriptions=True,refresh_started_at=stamp)
        batch=load_batch(c,'https://careers.fixture.invalid/jobs','Fixture')
        assert batch['refresh_descriptions'] and batch['refresh_started_at']==stamp
        bad=dict(batch,refresh_descriptions='false')
        with pytest.raises(ValueError):_validate(c,'https://careers.fixture.invalid/jobs',bad)
        bad=dict(batch,refresh_started_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
        with pytest.raises(ValueError):_validate(c,'https://careers.fixture.invalid/jobs',bad)
    finally:c.close()


def test_assisted_force_control_confirms_then_passes_frozen_worker_value(clean_tab,monkeypatch):
    from packtoworkeu.ui.tabs.assisted_ingestion import AssistedWorker
    from PySide6.QtWidgets import QMessageBox
    workers=[]
    monkeypatch.setattr(AssistedWorker,'start',lambda self:workers.append(self))
    clean_tab.url_edit.setText('https://careers.fixture.invalid/jobs')
    clean_tab.name_edit.setText('Fixture')
    clean_tab.refresh_jd_cb.setChecked(True)
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.No)
    clean_tab._start_ingestion();assert workers==[]
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    clean_tab._start_ingestion()
    assert len(workers)==1 and workers[0].refresh_descriptions
    assert not clean_tab.refresh_jd_cb.isChecked()
    worker=clean_tab._worker;clean_tab._worker=None;worker.deleteLater()


def test_saved_memory_expiry_does_not_fall_back_to_old_detail_state(saved,monkeypatch):
    path,c=saved;s=scanner();s._saved_jd_reader=SavedDetailReader(path)
    assert s.get_captured_jd(URL)==TEXT.strip()
    # Expiry is checked on access, not only at first discovery. Invalidating
    # proof also invalidates the saved-derived state tuple.
    from packtoworkeu.core import saved_details
    original=saved_details.capture_proof
    monkeypatch.setattr(saved_details,'capture_proof',lambda *args:'')
    monkeypatch.setattr(s._saved_jd_reader,'read',lambda url:None)
    assert s.get_captured_jd(URL)==''
    assert s._detail_state.get(URL) is None


def test_quick_pipeline_preserves_card_text_without_promoting_it_to_a_read(tmp_path,monkeypatch):
    from packtoworkeu.application import seed_manager
    from packtoworkeu.scanning import pipeline
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    from packtoworkeu.db import database as db
    seed=tmp_path/'career.csv';seed.write_text('name,careers_url,provider,board_slug,source_type,target_country,scope_policy\nFixture,https://fixture.invalid/jobs,greenhouse,fixture,direct_employer,Global,global\n')
    out=tmp_path/'exports';out.mkdir()
    monkeypatch.setattr(seed_manager,'user_career_path',lambda:seed)
    monkeypatch.setattr(seed_manager,'user_ats_path',lambda:seed)
    monkeypatch.setattr(pipeline.paths,'ensure_scan_output_dir',lambda:out)
    monkeypatch.setattr(CareerPortalScanner,'_fetch_provider_jobs',lambda *a:([
        dict(job_title='Platform Engineer',job_url=URL,location_hint='Berlin, Germany',
             extraction_method='greenhouse_api',description=TEXT)],'greenhouse API: 1'))
    path=tmp_path/'jobs.db';result=pipeline.run_scan(method='quick',db_path=path,run_ats=False,only_career=['Fixture'])
    assert not result['errors'],result
    c=db.get_connection(path)
    try:
        row=c.execute('SELECT description,support_evidence_type FROM jobs').fetchone()
        assert row[0]==TEXT.strip()
        assert 'jd_captured_at='not in row[1]
        assert result['repeat_scan']['jd_fresh']==0
    finally:c.close()
