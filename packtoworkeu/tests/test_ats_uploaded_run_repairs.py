"""Regressions derived from the 20261122T164140 ATS artifacts; no network."""
import csv
import io
import threading
import pytest
from packtoworkeu.scanning.ats.ats_scanner import ATSScanner
from packtoworkeu.scanning.ats.verified_routes import verified_target
from packtoworkeu.application.scan_scope import retry_plan, save_plan
from packtoworkeu.db import database as db


def target(**changes):
    t=dict(name='Fixture',url='https://fixture.jobs.personio.com/',ats_type='personio',
           board_slug='stale-slug',source_type='direct_employer',target_country='Global',
           scope_policy='global',industry='Technology')
    t.update(changes);return t


def test_personio_uses_exact_configured_host_and_xml_description(monkeypatch):
    s=ATSScanner();calls=[]
    text='<p>'+'Build and maintain test automation. '*15+'</p>'
    xml=f'<workzag-jobs><position><id>123</id><name>QA Engineer</name><office>Berlin</office><jobDescriptions><jobDescription><name>Your role</name><value><![CDATA[{text}]]></value></jobDescription></jobDescriptions></position></workzag-jobs>'
    def fetch(url):calls.append(url);return xml
    monkeypatch.setattr(s,'_fetch',fetch)
    rows=s.scan_personio(target())
    assert calls==['https://fixture.jobs.personio.com/xml?language=en']
    assert rows[0]['Job URL']=='https://fixture.jobs.personio.com/job/123'
    jd=s.get_captured_jd(rows[0]['Job URL'])
    assert 'Your role' in jd and 'test automation' in jd and '<p>' not in jd
    assert len(jd)>200


def test_lever_preserves_all_sections(monkeypatch):
    s=ATSScanner();job=dict(text='QA Engineer',hostedUrl='https://jobs.lever.co/fixture/123456',categories={'location':'Berlin'},openingPlain='Introduction',descriptionPlain='Introduction',descriptionBody='<p>Build systems.</p>',lists=[{'text':'Requirements','content':'<ul><li>Python and SQL.</li></ul>'}],additionalPlain='Visa sponsorship is available for this role.')
    monkeypatch.setattr(s,'_get_json',lambda url:[job])
    row=s.scan_lever(target(url='https://jobs.lever.co/fixture',board_slug='fixture',ats_type='lever'))[0]
    jd=s.get_captured_jd(row['Job URL'])
    assert all(x in jd for x in ('Introduction','Build systems.','Requirements','Python and SQL.','Visa sponsorship'))
    assert jd.count('Introduction')==1


def test_short_qa_workday_requisition_not_department():
    s=ATSScanner()
    assert s.valid_title('QA','https://dxctechnology.wd1.myworkdayjobs.com/DXCJobs/job/Shanghai/QA_51509923')
    assert not s.valid_title('QA','https://fixture.invalid/departments/qa')
    assert not s.valid_title('HR','https://fixture.invalid/departments/hr')
    assert not s.valid_title('Career Day Universität Ulm 2026 - Jobs @Airbus Public Safety and Security','https://ag.wd3.myworkdayjobs.com/Airbus/job/Ulm/Event_JR10456901')


@pytest.mark.parametrize('url,provider,slug,expected',[
    ('https://job-boards.eu.greenhouse.io/brainrocketltd','bamboohr','brainrocket','greenhouse'),
    ('https://jobs.virtuagym.com/jobs','recruitee','virtuagym','teamtailor')])
def test_routes_preserve_seed_url(url,provider,slug,expected):
    t=target(url=url,ats_type=provider,board_slug=slug)
    fixed=verified_target(t)
    assert fixed['url']==url and fixed['ats_type']==expected
    assert t['ats_type']==provider


def test_identity_conflict_is_not_silently_imported():
    with pytest.raises(ValueError,match='identity conflict'):
        verified_target(target(name='IT-tronics (IT Infrastructure)',url='https://itonics-gmbh.jobs.personio.de/?language=en',board_slug='itonics'))


def test_retry_preserves_ats_only_scope(tmp_path):
    p=tmp_path/'retry.db';db.initialize(p)
    with db.get_connection(p) as conn:
        conn.execute("INSERT INTO scan_runs(run_id,status,method)VALUES('fixture','partial','full')");conn.commit()
    scope=dict(name='Jobandtalent',careers_url='https://jobs.lever.co/jobandtalent/',target_country='Global')
    save_plan(p,'fixture',dict(version=1,method='full',ats=[scope],career=[]))
    db.record_scan_log_rows(p,'fixture','ats',[dict(seed_name=scope['name'],seed_url=scope['careers_url'],target_country='Global',status='partial',jobs_found=31,diagnostics='JD coverage incomplete: 9')])
    plan=retry_plan(p,'fixture')
    assert plan['ats']==[scope] and plan['career']==[]


def test_bol_mapping_requires_official_host_id_and_title():
    import json
    from packtoworkeu.scanning.ats.bol_descriptions import sitemap_jobs, posting_description
    assert sitemap_jobs('<urlset><url><loc>https://careers.bol.com/en/jobs/role/123/</loc></url><url><loc>https://evil.invalid/en/jobs/role/456/</loc></url></urlset>')=={'123':'https://careers.bol.com/en/jobs/role/123/'}
    posting=dict(identifier={'value':123},title='Buyer',description='<p>Actual duties</p>')
    posting['@type']='JobPosting'
    html='<script type="application/ld+json">'+json.dumps(posting)+'</script>'
    assert posting_description(html,123,'Buyer')=='Actual duties'
    assert posting_description(html,456,'Buyer')==''
    assert posting_description(html,123,'Engineer')==''


def test_career_event_quarantine_has_specific_reason():
    s=ATSScanner()
    row=s.make_row(target(), 'Career Day Universität Ulm 2026 - Jobs @Airbus Public Safety and Security',
                   'https://ag.wd3.myworkdayjobs.com/Airbus/job/Ulm/Event_JR10456901',
                   'Ulm','Ulm','','','workday_api')
    assert row['Record Status']=='quarantine'
    assert row['Quarantine Reason']=='non_vacancy_career_event'


def test_bol_adapter_uses_only_id_verified_official_description(monkeypatch):
    import json
    s=ATSScanner();t=target(url='https://job-boards.greenhouse.io/bolcomen',board_slug='bolcomen',ats_type='greenhouse')
    job=dict(id=123,title='Buyer',absolute_url='https://job-boards.greenhouse.io/bolcomen/jobs/123',location={'name':'Utrecht'},content='x')
    monkeypatch.setattr(s,'_get_json',lambda u:{'jobs':[job]})
    link='https://careers.bol.com/en/jobs/buyer/123/'
    node={'@type':'JobPosting','title':'Buyer','identifier':{'value':123},'description':'<p>'+'Negotiate contracts with suppliers. '*15+'</p>'}
    def fetch(url):
        if url.endswith('/sitemap.xml'):return '<urlset><url><loc>'+link+'</loc></url></urlset>'
        assert url==link
        return '<script type="application/ld+json">'+json.dumps(node)+'</script>'
    monkeypatch.setattr(s,'_fetch',fetch)
    row=s.scan_greenhouse(t)[0]
    assert row['Extraction Method']=='greenhouse_api+official_jsonld'
    assert 'Negotiate contracts' in s.get_captured_jd(row['Job URL'])


@pytest.mark.parametrize('extra,review',[
    ('Workday listing complete: observed=3; total=3 | Workday descriptions captured: nonempty=3',True),
    ('Workday listing complete: observed=3; total=4 | Workday descriptions captured: nonempty=3',False),
    ('Workday listing complete: observed=3; total=3 | Workday descriptions captured: nonempty=2',False),
    ('',False)])
def test_source_short_workday_is_review_not_endless_retry(extra,review):
    from packtoworkeu.application.scan_scope import _completed_listing_review
    row=dict(status='partial',error='',scanner='ats',jobs_found=3,seed_name='Fixture',company='Fixture',diagnostics='JD coverage incomplete: 1 | JD coverage: observed=3; readable=2; thin=1; empty=0; failed=0; blocked=0; unattempted=0 | '+extra)
    assert bool(_completed_listing_review(row))==review


def test_literal_test_posting_excluded_without_rejecting_test_engineer():
    s=ATSScanner()
    assert not s.valid_title('Test Job','https://fixture.jobs.personio.de/job/123')
    assert s.valid_title('Test Engineer','https://fixture.jobs.personio.de/job/124')


@pytest.mark.parametrize('total,complete',[(1,True),(2,False)])
def test_workday_census_checks_provider_total(monkeypatch,total,complete):
    s=ATSScanner()
    monkeypatch.setenv('PACKTOWORKEU_WORKDAY_WORKERS','1')
    monkeypatch.setattr(s,'_post_json',lambda u,b:dict(total=total,jobPostings=[dict(title='Operator',externalPath='/job/Malaysia/Operator_R123',locationsText='Malaysia')]))
    monkeypatch.setattr(s,'_get_json',lambda u:dict(jobPostingInfo={'jobDescription':'Short source description.'}))
    s.scan_workday(target(url='https://fixture.myworkdayjobs.com/careers',board_slug='fixture/careers',ats_type='workday'))
    assert any(('Workday listing complete:' if complete else 'listing incomplete: Workday') in n for n in s._target_notes)
