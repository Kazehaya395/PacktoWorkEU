"""Offline final ATS/interaction regressions; no user data or portal requests."""
import threading
import html
import pytest
from packtoworkeu.core.jd_text import normalize_jd_text
from packtoworkeu.db import database as db
from packtoworkeu.insights import terms, queries, cube
from packtoworkeu.tests._qt_support import application, wait_loaders, wait_until, dispose

@pytest.mark.parametrize('wrapping', [lambda s:s, html.escape, lambda s:html.escape(html.escape(s))])
def test_jd_html_envelopes_preserve_text_and_lists(wrapping):
    raw='<div><p>We’re hiring 🚀</p><ul><li>Python &amp; SQL</li><li>Use &lt;vector&gt;</li></ul><script>hidden()</script></div>'
    text=normalize_jd_text(wrapping(raw))
    assert 'We’re hiring 🚀' in text and '• Python & SQL' in text and 'Use <vector>' in text
    assert '<div>' not in text and 'hidden()' not in text

@pytest.mark.parametrize('text', ['Use C++ vector<T> and x < y > z.', 'R&D needs C#/.NET.', 'Keep &lt;T&gt; as written.', 'Python\n\nSQL\n  indentation'])
def test_plain_jd_is_not_rewritten(text):assert normalize_jd_text(text)==text

def test_stored_jd_and_clipboard_are_plain_without_db_rewrite(tmp_path):
    from packtoworkeu.ui.tabs.tailoring import TailoringTab, build_clipboard_text
    application();path=tmp_path/'jd.db';db.initialize(path)
    raw='<p>Engineering role</p><ul><li>Python &amp; SQL</li></ul>'
    with db.get_connection(path) as conn:
        conn.execute('INSERT INTO jobs(title,company,url,description)VALUES(?,?,?,?)',('Engineer','Fixture','https://fixture.invalid/job/1',raw));conn.commit()
    widget=TailoringTab(str(path))
    try:
        widget.set_selected_job('https://fixture.invalid/job/1');widget._load_jd()
        assert widget.jd_edit.toPlainText()==normalize_jd_text(raw)
        assert '<p>' not in build_clipboard_text('Prompt',raw,'CV','')
        with db.get_connection(path)as conn:assert conn.execute('SELECT description FROM jobs').fetchone()[0]==raw
    finally:dispose(widget)

@pytest.mark.parametrize('module', ['ats','career'])
@pytest.mark.parametrize('title,body,expected',[
    ('Lead Machine Learning Scientist','This is a full-time senior role building our next stage of growth.','Full-time'),
    ('Senior Engineer','You will mentor interns. This is a full-time position.','Full-time'),
    ('Software Engineer','Build the next stage of our platform.',''),
    ('Intern Engineer','Build the next stage of our platform.','Internship'),
    ('Ingénieur','Un stage de six mois.','Internship'),
    ('Engineer','This internship lasts six months.','Internship'),
    ('Engineer','This is a permanent role.',''),
])
def test_workload_context_parity(module,title,body,expected):
    if module=='ats':from packtoworkeu.scanning.ats.ats_scanner import classify_workload
    else:from packtoworkeu.scanning.career.career_scanner import classify_workload
    assert classify_workload('',title,body)==expected

def test_provider_backed_titles_and_navigation_controls():
    from packtoworkeu.scanning.ats.ats_scanner import ATSScanner
    s=ATSScanner.__new__(ATSScanner)
    assert s.valid_title('Internal Communications, GTM','https://job-boards.greenhouse.io/fixture/jobs/123456')
    title='Sales, Territory Business Manager - Oral Health/Gestionnaire de territoire – Soins buccodentaires (Ventes professionnelles dentaires, Rive-Sud de Montréal) – Poste à distance, basé sur la Rive-Sud de Montréal (Bilingue)'
    url='https://fixture.wd3.myworkdayjobs.com/careers/job/Montreal/Sales_594064-1'
    assert len(title)>180 and s.valid_title(title,url)
    assert not s.valid_title(title,'https://fixture.invalid/category/long')
    assert not s.valid_title('Career FAQ',url)
    assert not s.valid_title('Talent Community',url)
    assert not s.valid_title('Job '*200,url)
    assert not s.valid_title('Milano, MI','https://fixture.invalid/job/1')

@pytest.mark.parametrize('text',[
    'Benefits: We offer stock options and mental health support.',
    'Benefits\nStock options\nMental health support\nRequirements\nPython and SQL',
    '<h2>Benefits</h2><ul><li>Stock options</li><li>Mental health support</li></ul><h2>Requirements</h2><p>Python and SQL</p>',
])
def test_benefits_are_not_technical_skills(text):
    result=terms.extract_terms('Software Engineer','','',text,'Fintech')
    names={name for kind,name in result['terms']if kind=='skill'}
    assert 'Derivatives' not in names and 'Mental health support' not in names
    if 'Python' in text:assert 'Python' in names and 'SQL' in names

@pytest.mark.parametrize('title,text,skill',[
    ('Therapist','You will provide mental health support to patients.','Mental health support'),
    ('Quantitative Analyst','Experience with derivatives pricing and options trading.','Derivatives'),
    ('Data Engineer','Requirements\nPython and SQL.\nBenefits\nStock options','Python'),
])
def test_real_competencies_survive_context_filter(title,text,skill):
    result=terms.extract_terms(title,'','',text,'')
    assert ('skill',skill) in result['terms']

@pytest.fixture
def local_db(tmp_path):
    path=tmp_path/'cube.db';db.initialize(path)
    with db.get_connection(path)as conn:
        conn.execute("INSERT INTO jobs(title,company,url,description,verified_active,is_expired) VALUES('Software Engineer','Fixture','https://fixture.invalid/job/1','Python and SQL',1,0)");conn.commit()
    return path

def test_cube_invalidates_on_derived_progress_without_updated_at(local_db):
    assert cube.refresh(local_db,force=True)
    assert cube.jobs_by_category(local_db)==[('Other',1)]
    with db.get_connection(local_db)as conn:
        old=conn.execute('SELECT updated_at FROM jobs').fetchone()[0]
        conn.execute("UPDATE jobs SET category='Engineering & IT',insights_dirty=0");conn.commit()
        assert conn.execute('SELECT updated_at FROM jobs').fetchone()[0]==old
    assert cube.jobs_by_category(local_db)is None
    assert queries.get_jobs_by_category(local_db)==[('Engineering & IT',1)]

def test_forced_backfill_invalidates_existing_cube_and_preserves_job(local_db):
    queries.backfill_existing_job_terms(local_db)
    assert cube.refresh(local_db,force=True)
    with db.get_connection(local_db)as conn:
        conn.execute("UPDATE jobs SET category='Other'");conn.commit()
    assert cube.refresh(local_db,force=True)
    queries.backfill_existing_job_terms(local_db,force_all=True)
    assert cube.jobs_by_category(local_db)is None
    assert queries.get_jobs_by_category(local_db)==[('Engineering & IT',1)]
    with db.get_connection(local_db)as conn:assert conn.execute('SELECT description FROM jobs').fetchone()[0]=='Python and SQL'

def test_failed_cube_rebuild_keeps_last_snapshot_atomically(local_db,monkeypatch):
    assert cube.refresh(local_db,force=True)
    with db.get_connection(local_db)as conn:before=[tuple(r)for r in conn.execute('SELECT * FROM insights_cube_fact')]
    monkeypatch.setattr(cube,'_BUILD',cube._BUILD+'; INSERT INTO deliberately_missing_table VALUES(1);')
    assert cube.refresh(local_db,force=True)is False
    with db.get_connection(local_db)as conn:assert [tuple(r)for r in conn.execute('SELECT * FROM insights_cube_fact')]==before

def test_read_work_does_not_serialize_behind_one_unrelated_view():
    from packtoworkeu.ui.async_load import AsyncLoader
    from PySide6.QtWidgets import QWidget
    application();parent=QWidget();started=threading.Event();release=threading.Event();done=[]
    slow=AsyncLoader(parent,name='slow chart fixture');fast=AsyncLoader(parent,name='search fixture')
    try:
        slow.request(lambda:(started.set(),release.wait(3)),lambda _:None)
        wait_until(started.is_set)
        fast.request(lambda:'ready',lambda value:done.append(value))
        wait_until(lambda:bool(done),1)
        assert done==['ready'] and not release.is_set()
    finally:
        release.set();wait_loaders(parent);dispose(parent)

def test_pager_label_does_not_repeat_full_result_sort(local_db,monkeypatch):
    from packtoworkeu.ui.tabs.search import SearchTab
    application();tab=SearchTab(str(local_db));tab.run_search();wait_loaders(tab)
    try:
        monkeypatch.setattr(tab,'_ordered_rows',lambda:pytest.fail('pager must not sort'))
        tab._refresh_pager_label()
        assert '1' in tab.page_info.text()
    finally:dispose(tab)


def test_cube_stamp_cache_is_scoped_to_task_and_database(local_db,tmp_path):
    from packtoworkeu.core.db_tasks import DatabaseReadTask
    other=tmp_path/'other.db';db.initialize(other)
    with DatabaseReadTask():
        with db.get_connection(local_db) as conn:first=cube._stamp(conn)
        with db.get_connection(other) as conn:second=cube._stamp(conn)
        assert first!=second
    with db.get_connection(local_db)as conn:
        conn.execute("UPDATE jobs SET insights_dirty=0");conn.commit()
    with DatabaseReadTask():
        with db.get_connection(local_db)as conn:assert cube._stamp(conn)!=first
