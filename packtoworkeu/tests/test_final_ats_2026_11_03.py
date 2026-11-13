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


# ── Run 20261110T185858: seed slugs were trusted blindly, an unsupported
#    provider emptied silently, and network resets were final ─────────────

def _ats():
    from packtoworkeu.scanning.ats.ats_scanner import ATSScanner
    return ATSScanner(seed_file='__unused__.csv', output_file='__unused__.csv')


def _http_error(code):
    import urllib.error
    return urllib.error.HTTPError('https://fixture.invalid', code, 'x', None, None)


_LEVER_JOB = [{"text": "Engineer", "hostedUrl": "https://jobs.lever.co/x/1",
               "categories": {"location": "Berlin, Germany"},
               "descriptionPlain": "Build useful things with Python. " * 10}]


def _lever_scanner(monkeypatch, behaviour):
    s = _ats()
    monkeypatch.setattr(s, '_get_json', behaviour)
    monkeypatch.setattr(s, 'make_row', lambda target, title, url, *a, **k:
                        {"Job Title": title, "Job URL": url})
    return s


def test_lever_falls_back_to_url_board_when_seed_slug_404s(monkeypatch):
    def behaviour(url):
        if '/swapcards?' in url or '/swapcards/' in url:
            raise _http_error(404)
        assert '/swapcard?' in url or '/swapcard/' in url
        return _LEVER_JOB
    s = _lever_scanner(monkeypatch, behaviour)
    rows = s.scan_lever({"name": "swapcards", "url": "https://jobs.lever.co/swapcard",
                         "board_slug": "swapcards", "ats_type": "lever"})
    assert len(rows) == 1 and rows[0]["Job Title"] == "Engineer"


def test_lever_falls_back_to_other_region(monkeypatch):
    def behaviour(url):
        if 'api.eu.lever.co' in url:
            raise _http_error(404)
        return _LEVER_JOB
    s = _lever_scanner(monkeypatch, behaviour)
    rows = s.scan_lever({"name": "InnoGames", "url": "https://jobs.eu.lever.co/innogames",
                         "board_slug": "innogames", "ats_type": "lever"})
    assert len(rows) == 1


def test_lever_all_404s_still_raise(monkeypatch):
    def behaviour(url):
        raise _http_error(404)
    s = _lever_scanner(monkeypatch, behaviour)
    with pytest.raises(Exception):
        s.scan_lever({"name": "Gone", "url": "https://jobs.lever.co/gone",
                      "board_slug": "gone", "ats_type": "lever"})


def test_workday_single_piece_slug_names_site_not_tenant(monkeypatch):
    s = _ats()
    calls = []
    monkeypatch.setattr(s, '_post_json', lambda url, body:
                        (calls.append(url), {"total": 0, "jobPostings": []})[1])
    rows = s.scan_workday({"name": "Airbus Deutschland GmbH",
                           "url": "https://ag.wd3.myworkdayjobs.com/de-DE/Airbus",
                           "board_slug": "Airbus", "ats_type": "workday"})
    assert rows == []
    assert calls == ["https://ag.wd3.myworkdayjobs.com/wday/cxs/ag/Airbus/jobs"]


def test_workday_site_falls_back_to_url_site_on_422(monkeypatch):
    s = _ats()
    calls = []

    def post(url, body):
        calls.append(url)
        if '/WrongSite/' in url:
            raise _http_error(422)
        return {"total": 0, "jobPostings": []}
    monkeypatch.setattr(s, '_post_json', post)
    rows = s.scan_workday({"name": "Fixture",
                           "url": "https://ag.wd3.myworkdayjobs.com/de-DE/Airbus",
                           "board_slug": "ag/WrongSite", "ats_type": "workday"})
    assert rows == [] and len(calls) == 2 and '/Airbus/' in calls[1]


def test_unsupported_provider_fails_loudly():
    s = _ats()
    with pytest.raises(ValueError, match="unsupported ATS provider"):
        s.scan_target({"name": "Klarna AG", "url": "https://jobs.deel.com/klarna",
                       "ats_type": "deel"})


def _seed_csv(path):
    path.write_text(
        "name,careers_url,provider,board_slug,source_type,target_country,"
        "scope_policy,industry,sponsorship_history,english_friendly,remote_score,notes\n"
        "Fixture,https://jobs.lever.co/fixture,lever,fixture,direct_employer,"
        "Germany,seed_url,Tech,70,80,50,\n", encoding='utf-8')
    return path


def test_network_reset_target_is_retried_once_and_recovers(tmp_path, monkeypatch):
    import csv as _csv
    import urllib.error
    from packtoworkeu.scanning.ats.ats_scanner import ATSScanner
    seed = _seed_csv(tmp_path / 'seed.csv')
    out = tmp_path / 'jobs.csv'
    s = ATSScanner(seed_file=str(seed), output_file=str(out))
    calls = []

    def flaky(target):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError(
                "<urlopen error [WinError 10054] forcibly closed>")
        return [s.make_row(target, "Software Engineer",
                           "https://jobs.lever.co/fixture/1",
                           "Berlin, Germany", "Berlin, Germany", "",
                           "Build useful things. " * 30, "lever_api")]
    monkeypatch.setattr(s, 'scan_target', flaky)
    s.run()
    jobs = list(_csv.DictReader(out.open(encoding='utf-8-sig')))
    log = list(_csv.DictReader((tmp_path / 'jobs_scan_log.csv').open(encoding='utf-8-sig')))
    errors = list(_csv.DictReader((tmp_path / 'jobs_errors.csv').open(encoding='utf-8-sig')))
    assert len(calls) == 2 and len(jobs) == 1
    assert log[0]['Status'] == 'ok' and log[0]['Attempts'] == '2'
    assert errors == []


def test_network_reset_that_sticks_is_one_error_with_two_attempts(tmp_path, monkeypatch):
    import csv as _csv
    import urllib.error
    from packtoworkeu.scanning.ats.ats_scanner import ATSScanner
    seed = _seed_csv(tmp_path / 'seed.csv')
    out = tmp_path / 'jobs.csv'
    s = ATSScanner(seed_file=str(seed), output_file=str(out))

    def dead(target):
        raise urllib.error.URLError("<urlopen error [WinError 10054] forcibly closed>")
    monkeypatch.setattr(s, 'scan_target', dead)
    s.run()
    log = list(_csv.DictReader((tmp_path / 'jobs_scan_log.csv').open(encoding='utf-8-sig')))
    errors = list(_csv.DictReader((tmp_path / 'jobs_errors.csv').open(encoding='utf-8-sig')))
    assert log[0]['Status'] == 'error' and log[0]['Attempts'] == '2'
    assert len(errors) == 1


def _seed_csv_text(tmp_path, name, url, provider, slug):
    path = tmp_path / 'seed.csv'
    path.write_text(
        "name,careers_url,provider,board_slug,source_type,target_country,"
        "scope_policy,industry,sponsorship_history,english_friendly,remote_score,notes\n"
        f"{name},{url},{provider},{slug},direct_employer,Global,global,Tech,70,80,50,\n",
        encoding='utf-8')
    return path


def test_known_dead_board_is_skipped_without_error(tmp_path, monkeypatch):
    import csv as _csv
    from packtoworkeu.scanning.ats.ats_scanner import ATSScanner
    seed = _seed_csv_text(tmp_path, "QuantumDiam",
                          "https://quantum-diamond.jobs.personio.de/",
                          "personio", "quantum-diamond")
    out = tmp_path / 'jobs.csv'
    s = ATSScanner(seed_file=str(seed), output_file=str(out))
    called = []
    monkeypatch.setattr(s, 'scan_target', lambda t: called.append(1) or [])
    s.run()
    log = list(_csv.DictReader((tmp_path / 'jobs_scan_log.csv').open(encoding='utf-8-sig')))
    errors = list(_csv.DictReader((tmp_path / 'jobs_errors.csv').open(encoding='utf-8-sig')))
    assert called == []  # never scanned
    assert log[0]['Status'] == 'empty' and errors == []
    assert 'known-dead' in log[0]['Diagnostics']


def test_known_dead_name_with_new_url_scans_normally(tmp_path, monkeypatch):
    import csv as _csv
    from packtoworkeu.scanning.ats.ats_scanner import ATSScanner
    seed = _seed_csv_text(tmp_path, "QuantumDiam",
                          "https://jobs.example.com/boards/quantum",
                          "personio", "quantum-diamond")
    out = tmp_path / 'jobs.csv'
    s = ATSScanner(seed_file=str(seed), output_file=str(out))
    called = []
    monkeypatch.setattr(s, 'scan_target', lambda t: called.append(1) or [])
    s.run()
    log = list(_csv.DictReader((tmp_path / 'jobs_scan_log.csv').open(encoding='utf-8-sig')))
    assert called == [1]  # moved board -> scanned again
    assert 'known-dead' not in log[0]['Diagnostics']


def test_bundled_seed_klarna_moved_off_deel():
    import csv as _csv
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    ats = list(_csv.DictReader((root / 'data/seeds/company_ATS_seed.csv').open(encoding='utf-8-sig')))
    career = list(_csv.DictReader((root / 'data/seeds/company_Career_seed.csv').open(encoding='utf-8-sig')))
    assert all(r['provider'].strip().lower() != 'deel' for r in ats)
    assert not any(r['name'] == 'Klarna AG' for r in ats)
    assert any(r['name'] == 'Klarna AG' and 'klarna.com/careers' in r['careers_url'] for r in career)


def test_workday_host_without_wd_cluster_is_scanned(monkeypatch):
    # NXP's tenant lives on nxp.myworkdayjobs.com (no wdN segment) and
    # answers CXS there; requiring wd\d+ silently returned [] (2026-11-10).
    s = _ats()
    calls = []
    monkeypatch.setattr(s, '_post_json', lambda url, body:
                        (calls.append(url), {"total": 0, "jobPostings": []})[1])
    rows = s.scan_workday({"name": "NXP Semiconductors",
                           "url": "https://nxp.myworkdayjobs.com/careers",
                           "board_slug": "nxp/careers", "ats_type": "workday"})
    assert rows == []
    assert calls == ["https://nxp.myworkdayjobs.com/wday/cxs/nxp/careers/jobs"]


def test_bundled_seed_boards_verified_live_2026_11_10():
    import csv as _csv
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    ats = {r['name']: r for r in _csv.DictReader(
        (root / 'data/seeds/company_ATS_seed.csv').open(encoding='utf-8-sig'))}
    assert ats['Brain Rocket']['provider'] == 'greenhouse'
    assert ats['Brain Rocket']['board_slug'] == 'brainrocketltd'
    assert ats['Virtuagym']['provider'] == 'teamtailor'
    assert ats['QuantumDiam']['provider'] == 'ashby'
    assert ats['QuantumDiam']['board_slug'] == 'quantumdiamonds'
    assert ats['NXP Semiconductors']['careers_url'] == 'https://nxp.myworkdayjobs.com/careers'
