import hashlib
import sqlite3
import time
import pytest
from packtoworkeu.insights.role_skills import RoleIndex,title_matcher

BODY='Requirements\nExperience with SQL and effective communication skills. SQL is used daily.\n'+'Maintain the reporting process and review the records each week. '*4

def proof(body):
    return 'jd_read_status=readable; jd_captured_at=2025-01-01T12:00:00+00:00; jd_sha256='+hashlib.sha256(body.encode()).hexdigest()

def make(path,rows):
    with sqlite3.connect(path) as c:
        c.execute('create table jobs(id integer primary key,title,company,description,support_evidence_type,country,source_subtype,verified_active,is_expired)')
        c.executemany("insert into jobs values(null,?,'Example',?,?,?,'direct',1,?)",rows)

@pytest.mark.parametrize('mode,pattern,title,expected',[
    ('role','SDE','Senior Software Development Engineer',True),('role','analyst','Analystin',False),
    ('role','Project Manager','Product Manager',False),('role','Analyst','Senior Data Analyst',True),
    ('wildcard','*sales*manager*','Regional Sales Area Manager',True),('wildcard','sales?','sales',False),
    ('regex',r'\b(project|programme?)\s+manager\b','Senior Programme Manager',True)])
def test_modes(mode,pattern,title,expected):assert title_matcher(pattern,mode)(title)==expected

def test_invalid_and_slow_pattern():
    with pytest.raises(ValueError,match='Invalid'):title_matcher('[','regex')
    with pytest.raises(ValueError,match='too long'):title_matcher('(a+)+$','regex')('a'*100000+'!')

def test_coverage_and_cache(tmp_path):
    path=tmp_path/'jobs.db';plain='Le informazioni relative alla posizione sono disponibili nella sede indicata. '*4
    make(path,[('Python Analyst',BODY,proof(BODY),'DE',0),('Analyst',plain,proof(plain),'DE',0),('Analyst',BODY,'','DE',0),('Analyst','short','','DE',0),('Analyst',BODY,proof(plain),'DE',0),('Analyst',BODY,proof(BODY),'IT',0),('Analyst',BODY,proof(BODY),'DE',1)])
    idx=RoleIndex();r=idx.analyze(path,'analyst',country='DE');assert (r['matched'],r['analyzed'],r['legacy'],r['excluded'])==(5,2,1,3)
    assert ('SQL',1) in r['ranks']['technical_skill'];assert 'Python' not in r['examples'];assert 'SQL' in r['examples']['SQL'][0][-1];assert r['ranks']['soft_skill']
    assert idx.analyze(path,'analyst',country='DE',legacy=True)['analyzed']==3
    assert idx.analyze(path,'analyst',source='recruiter')['matched']==0
    assert r==idx.analyze(path,'analyst',country='DE')
    revised=BODY.replace('SQL','Python');
    with sqlite3.connect(path) as c:c.execute('update jobs set description=?,support_evidence_type=? where id=1',(revised,proof(revised)))
    r=idx.analyze(path,'analyst',country='DE');assert 'SQL' not in r['examples'] and 'Python' in r['examples']

def test_widget_latest_query_and_examples(tmp_path):
    from PySide6.QtWidgets import QApplication
    from packtoworkeu.ui.role_skills import RoleSkillsBox
    app=QApplication.instance() or QApplication([]);path=tmp_path/'jobs.db';make(path,[('Senior Software Engineer',BODY,proof(BODY),'DE',0)])
    w=RoleSkillsBox(path,lambda:('',''));w.entry.setText('analyst');w.launch();w.entry.setText('SDE')
    end=time.monotonic()+7
    while time.monotonic()<end and not w.result:app.processEvents();time.sleep(.01)
    assert w.result and w.result['matched']==1;assert 'Senior Software Engineer' in w.tables[0].item(0,0).toolTip()
    from PySide6.QtWidgets import QPlainTextEdit
    assert not w.findChildren(QPlainTextEdit)
    assert ('Communication',1) in w.result['ranks']['soft_skill']
    assert 'Communication skills' not in w.result['examples']
    w.entry.clear();assert w.result is None and w.tables[0].rowCount()==0;w.shutdown();w.deleteLater();app.processEvents()

@pytest.mark.parametrize('title,body,skill',[
    ('Analista dati','Requisiti\nEsperienza con SQL e Python.\nCosa offriamo:\nLeadership', 'SQL'),
    ('Datenanalyst','Anforderungen: Problemlösung und Erfahrung mit SQL.','Problem Solving')])
def test_multilingual_saved_text(tmp_path,title,body,skill):
    body+='\n'+'Documentazione interna. '*12;path=tmp_path/'multi.db';make(path,[(title,body,proof(body),'DE',0)])
    result=RoleIndex().analyze(path,title);assert skill in result['examples'];assert 'Leadership' not in result['examples']


def test_missing_regex_does_not_prevent_desktop_startup(tmp_path, monkeypatch):
    import sys
    from PySide6.QtWidgets import QApplication
    from packtoworkeu.ui.tabs.insights import InsightsTab
    monkeypatch.setitem(sys.modules,'regex',None)
    app=QApplication.instance() or QApplication([])
    tab=InsightsTab(str(tmp_path/'test.db'),defer_load=True)
    try:
        assert title_matcher('SDE','role')('Software Engineer')
        assert title_matcher('*sales? [eu]*','wildcard')('Sales1 [EU] manager')
        with pytest.raises(ValueError,match='Regex mode needs'):title_matcher('sales','regex')
    finally:tab.shutdown();tab.deleteLater();app.processEvents()


def test_full_rankings_fit_without_inner_vertical_scroll():
    from PySide6.QtWidgets import QApplication
    from packtoworkeu.ui.role_skills import RoleSkillsBox
    app=QApplication.instance() or QApplication([]);w=RoleSkillsBox('',lambda:('',''));w.resize(950,300);w.show()
    ranks=[(f'Term {i}',1) for i in range(25)]
    try:
        w.apply(dict(matched=855,analyzed=753,excluded=102,proven=697,legacy=56,titles=[('Business Analyst',9)],ranks={'technical_skill':ranks,'soft_skill':ranks},examples={s:[] for s,n in ranks}))
        app.processEvents()
        assert all(t.rowCount()==25 and t.verticalScrollBar().maximum()==0 for t in w.tables)
    finally:w.shutdown();w.close();w.deleteLater();app.processEvents()
