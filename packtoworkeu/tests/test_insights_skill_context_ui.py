"""Employer industry, role skills and employee benefits must not be conflated."""
import pytest
from packtoworkeu.insights import terms

@pytest.mark.parametrize('text',[
    'Mental health support is available to all employees.',
    'Mental health support is provided for our employees.',
    'Mental health support is offered to all staff.',
    'Supporto alla salute mentale disponibile per tutti i dipendenti.',
    'Benefits:\nMental health support and flexible working hours.',
    'Cosa offriamo:\nSupporto alla salute mentale e orari flessibili.',
])
def test_employee_benefit_is_not_a_job_skill(text):
    result=terms.extract_terms('Software Engineer','','',text,'Software Development')
    assert not any(term=='Mental health support'for _,term in result['terms'])


def test_clinical_competency_is_professional_not_software_tool_or_benefit():
    result=terms.extract_terms('Counsellor','','','Responsibilities: Deliver mental health support to patients as a trained therapist.','Healthcare')
    assert ('professional_skill','Mental health support')in result['terms']
    assert ('technical_skill','Mental health support')not in result['terms']


def test_explicit_skill_partitions_sales_python_collaboration():
    result=terms.extract_terms('Account Manager','','','Requirements: Experience in sales, Python and collaboration.','HealthTech')
    assert ('professional_skill','Sales')in result['terms']
    assert ('technical_skill','Python')in result['terms']
    assert ('soft_skill','Collaboration')in result['terms']


def test_ordinary_verb_soar_is_not_security_platform():
    assert 'SOAR'not in terms.extract_skills('Help our business soar to new heights.')
    assert 'SOAR'in terms.extract_skills('Experience configuring a SOAR platform for incident response.')


def test_saved_jobs_are_reclassified_without_rescan_or_description_changes(tmp_path):
    from packtoworkeu.db import database as db
    from packtoworkeu.insights import queries,cube
    p=tmp_path/'skills.db';db.initialize(p)
    descriptions=['Requirements: Python. Mental health support is available to all employees.',
                  'Responsibilities: Deliver mental health support to patients as a trained therapist.']
    with db.get_connection(p)as c:
        for i,description in enumerate(descriptions):
            cur=c.execute('INSERT INTO jobs(url,title,company,description,verified_active,is_expired,insights_dirty,insights_version) VALUES(?,?,?,?,1,0,0,?)',
                (f'https://fixture.invalid/{i}','Software Engineer'if i==0 else'Counsellor','Fixture',description,'old-cached-taxonomy'))
            c.executemany('INSERT INTO job_terms(job_id,kind,term) VALUES(?,?,?)',[(cur.lastrowid,'skill','Mental health support'),(cur.lastrowid,'technical_skill','Mental health support')])
        c.commit()
    assert queries.has_pending_insights(p)
    assert queries.backfill_existing_job_terms(p)==2
    assert not queries.has_pending_insights(p)
    for warm_cube in (False,True):
        if warm_cube:cube.refresh(p,force=True)
        parts=queries.get_segregated_skills(p)
        assert ('Mental health support',1)in parts['professional']
        assert 'Mental health support'not in dict(parts['technical'])
        assert ('Python',1)in parts['technical']
    with db.get_connection(p)as c:
        assert [row[0]for row in c.execute('SELECT description FROM jobs ORDER BY id')]==descriptions
        assert not c.execute("SELECT 1 FROM job_terms WHERE kind='technical_skill' AND term='Mental health support'").fetchone()


def test_collapsed_tools_and_assisted_instructions_switch_languages(tmp_path,monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from packtoworkeu.db import database as db
    from packtoworkeu import i18n
    from packtoworkeu.tests._qt_support import application,wait_loaders,dispose
    from packtoworkeu.ui.tabs.tools import ToolsTab
    from packtoworkeu.ui.tabs.assisted_ingestion import AssistedIngestionTab
    application();p=tmp_path/'ui.db';db.initialize(p)
    tools=ToolsTab(str(p));assisted=AssistedIngestionTab(str(p));wait_loaders(tools);wait_loaders(assisted)
    try:
        assert tools.advanced_panel.isHidden() and not tools.advanced_toggle.isChecked()
        assert not tools._scanner_box.isHidden() and not tools._history_box.isHidden()
        assert tools.turbo_cb.parentWidget()is tools.advanced_panel
        tools.advanced_toggle.setChecked(True)
        assert not tools.advanced_panel.isHidden()
        tools.advanced_toggle.setChecked(False)
        assert tools.advanced_panel.isHidden()
        for locale in ('it','en','it','en'):
            monkeypatch.setattr(i18n,'_locale',locale);tools.retranslate();assisted.retranslate()
            assert tools.advanced_toggle.text()==i18n._('Advanced maintenance and diagnostics')
            assert ('Seleziona'in assisted.quick_help.text())==(locale=='it')
            assert not tools.advanced_toggle.isChecked()
        assert 'view-only'in assisted.quick_help.text()
    finally:
        assisted.shutdown();wait_loaders(tools);wait_loaders(assisted);dispose(tools);dispose(assisted)


def test_chart_scopes_and_professional_bar_preserve_selection(tmp_path,monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from packtoworkeu.db import database as db
    from packtoworkeu import i18n
    from packtoworkeu.tests._qt_support import application,wait_loaders,dispose
    from packtoworkeu.ui.tabs.insights import InsightsTab
    application();p=tmp_path/'charts.db';db.initialize(p);w=InsightsTab(str(p),defer_load=True)
    snapshot=dict(cov=dict(total_jobs=78,categorized_jobs=78,jobs_with_skills=60),
        cats=[('Software Engineering',78)],skills_dict=dict(technical=[('Python',30)],professional=[('Sales',5)],soft=[('Collaboration',10)]),
        domains=[('Tech',78),('Healthcare',153),('Other',12)],spon_data=[],pending=False)
    try:
        for locale in ('it','en'):
            monkeypatch.setattr(i18n,'_locale',locale);w.retranslate();wait_loaders(w)
            w._apply_charts(snapshot,'domain','Tech')
            assert sum(n for _,n in w.pie_dom_chart.items)==243
            assert w.pie_dom_chart.center_text()=='243\n'+i18n._('Chart total')
            assert '78'in w.coverage_label.text() and '243'not in w.coverage_label.text()
            assert w.professional_skill_chart.items==[('Sales',5)]
            assert w.soft_skill_box.title().startswith(i18n._('In-Demand Soft Skills'))
            assert w.scope_help.text()==i18n._('Employer industry describes the company, not the job function: a healthcare company can hire salespeople or engineers. Chart total is the full distribution in that doughnut; a highlighted slice is the selection. Skills and coverage use the selected jobs.')
        w.professional_skill_chart.bar_clicked.emit('Sales')
        assert w.active_pie_filter==('skill','Sales')
        wait_loaders(w)
    finally:w.shutdown();dispose(w)


def test_all_italian_printf_format_signatures_are_preserved():
    import re
    from packtoworkeu.i18n import LANGUAGES
    pattern=re.compile(r'%(?:\([^)]+\))?[-+ #0]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]')
    for key,value in LANGUAGES['it'].items():
        assert [x for x in pattern.findall(key)if x!='%%']==[x for x in pattern.findall(value)if x!='%%'],key


def test_employer_industry_does_not_override_commercial_job_function():
    result=terms.extract_terms('Sales Manager','','','Requirements: Experience in sales and customer success.','Healthcare & Life Sciences',company='Fixture')
    assert result['domain']=='Healthcare, Pharma & Life Sciences'
    assert result['category']=='Sales & Business Development'
    assert ('professional_skill','Sales')in result['terms']


@pytest.mark.parametrize('term',['Project Management','Product Strategy','Process Optimization','Customer Relationship Management','Financial Modeling'])
def test_business_methods_are_not_soft_skills_or_programming_tools(term):
    assert terms.skill_kind(term)=='professional_skill'


def test_skill_taxonomy_partitions_are_explicit_and_disjoint():
    terms._load_data()
    groups={row['group']for row in terms._SKILLS}
    assert terms._TECHNICAL_SKILL_GROUPS<=groups
    assert not terms._TECHNICAL_SKILL_GROUPS & terms._SOFT_SKILL_GROUPS
    assert terms.taxonomy_version()==terms._DATA_VERSION
