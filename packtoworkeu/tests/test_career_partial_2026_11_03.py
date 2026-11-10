"""Approved partial Career regressions: offline, no portals, no database writes."""
import csv
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest
from packtoworkeu.core.job_identity import nonposting_record
from packtoworkeu.scanning.career import career_scanner as cs
from packtoworkeu.scanning.career.evidence import CATEGORY_LABELS
from packtoworkeu.scanning.jd_support import JDSupportDetector

@pytest.fixture
def scanner():
    return cs.CareerPortalScanner(skip_preflight=True)

@pytest.mark.parametrize('title', sorted(CATEGORY_LABELS))
def test_industry_label_is_not_a_generated_card_title(scanner, title):
    assert not scanner.is_valid_job_title(title, 'Fixture', 'https://fixture.invalid/jobs#job=industry--rome')

@pytest.mark.parametrize('title,url', [
    ('Analyst','https://search.jobs.barclays/job/chennai/analyst/13015/101953782336'),
    ('Analyst','https://search.jobs.barclays/job/pune/analyst/13015/101941261296'),
    ('Engineer','https://fixture.invalid/job/123456'),
    ('Specialist','https://fixture.invalid/job/654321'),
    ('Automotive Engineer','https://fixture.invalid/jobs#job=automotive-engineer--rome'),
    ('Software Engineer','https://fixture.invalid/jobs#job=12345'),
])
def test_posting_backed_roles_and_provider_cards_survive(scanner,title,url):
    assert scanner.is_valid_job_title(title,'Fixture',url)
    assert not nonposting_record(url,title)

@pytest.mark.parametrize('title', ['Associated with 2 categories', 'Associated with 1 category'])
def test_card_metadata_requires_title_rescue(scanner,title):
    assert not scanner.is_valid_job_title(title,'Fixture','https://careers.cisco.com/global/en/job/2023856/Customer-Experience-Splunk-Technical-Leader')

@pytest.mark.parametrize('title,url', [
    ('Development opportunities','https://www.schaeffler.com/en/careers/why-schaeffler/development'),
    ('Order now','https://www.schaeffler.com/en/media/media-library/mediabasket_detail_1.jsp'),
])
def test_information_pages_do_not_become_jobs(title,url):assert nonposting_record(url,title)

@pytest.mark.parametrize('slug', [
    'research-engineer-m-f-d-for-electric-vehicle-power-electronics-parental-leave-cover-temporary-position-initially-limited-to-1-year',
    'skilled-worker-in-construction-technology-for-track-construction-business-unit-pfm-permanent-way-construction-train',
])
def test_long_posting_bound_slug_can_rescue_open_job(scanner,slug):
    url='https://www.workinaustria.com/en/employees/jobs/jobdetail/5601/'+slug
    title=scanner._title_from_url_slug(url)
    assert ' '.join(slug.split('-')).casefold()==title.casefold()
    assert scanner.is_valid_job_title(title,'Work in Austria',url)

@pytest.mark.parametrize('text', [
    'You have the right to work in the EU/Austria and are based in Vienna or open to relocating.',
    'You are willing to relocate to Vienna.',
    'You are prepared to relocate to Vienna.',
])
def test_optional_mobility_is_not_employer_refusal(text):
    result=JDSupportDetector().detect(text)['relocation']
    assert result['verdict']=='Unknown' and not result['required']

@pytest.mark.parametrize('text,expected', [
    ('We offer relocation assistance.','Yes'),
    ('We do not offer relocation assistance.','No'),
    ('Relocation: No','No'),
])
def test_relocation_decisions_remain_explicit(text,expected):
    assert JDSupportDetector().detect(text)['relocation']['verdict']==expected

def test_existing_visa_eligibility_policy_is_preserved():
    assert JDSupportDetector().detect('Applicants must have the right to work in the UK.')['visa']['verdict']=='No'


def _seed_scanner(tmp_path, cancel=None):
    seed=tmp_path/'seed.csv'; seed.write_text('name,careers_url,provider,source_type,target_country,scope_policy\nFixture,https://fixture.invalid/careers,personio,direct_employer,Global,global\n')
    return cs.CareerPortalScanner(input_csv=str(seed),output_csv=str(tmp_path/'jobs.csv'),
        max_workers=1,skip_preflight=True,cancel_event=cancel)


def _read(path):
    with path.open(encoding='utf-8-sig',newline='')as f:return list(csv.DictReader(f))


def test_cancelled_is_not_logged_as_portal_failure(tmp_path,monkeypatch):
    s=_seed_scanner(tmp_path)
    def cancelled(_):raise cs.ScanCancelled()
    monkeypatch.setattr(s,'_fetch_provider_jobs',cancelled)
    s.execute_crawler()
    rows=_read(tmp_path/'jobs_scan_log.csv')
    assert len(rows)==1 and rows[0]['Status']=='cancelled'
    assert not _read(tmp_path/'jobs_errors.csv')


def test_stop_joins_late_writer_before_finalizing(tmp_path,monkeypatch):
    cancel=threading.Event();started=threading.Event();release=threading.Event();finalized=threading.Event()
    s=_seed_scanner(tmp_path,cancel)
    def delayed(_):
        started.set()
        assert release.wait(10)
        return [{'job_title':'Software Engineer','job_url':'https://fixture.invalid/job/123456','location_hint':'Berlin, Germany','card_context':''}],'offline fixture'
    monkeypatch.setattr(s,'_fetch_provider_jobs',delayed)
    original=cs.finalize_job_csvs
    def finalize(*a,**kw):
        finalized.set()
        assert release.is_set(), 'target writer is still active'
        return original(*a,**kw)
    monkeypatch.setattr(cs,'finalize_job_csvs',finalize)
    failures=[]
    def run():
        try:s.execute_crawler()
        except BaseException as exc:failures.append(exc)
    runner=threading.Thread(target=run)
    runner.start()
    try:
        assert started.wait(5);cancel.set()
        assert not finalized.wait(3.6), 'finalization overtook the cancelled target worker'
        assert runner.is_alive()
    finally:
        release.set();runner.join(10)
    assert not runner.is_alive() and not failures
    assert finalized.is_set()
    assert len(_read(tmp_path/'jobs.csv'))==1
    assert _read(tmp_path/'jobs_scan_log.csv')[0]['Status']=='cancelled'
    assert not _read(tmp_path/'jobs_errors.csv')


def test_cancelled_scope_pass_does_not_start_network(scanner,monkeypatch):
    scanner.cancel_event=threading.Event();scanner.cancel_event.set()
    monkeypatch.setattr(scanner,'_detail_location_from_url',lambda *a,**k:pytest.fail('network resolver after Stop'))
    row={'Canonical Job ID':'test','Job URL':'https://fixture.invalid/job/123456','Job Location':'Unknown','_loc_pending':True}
    rows={'test':row};diagnostics=[]
    scanner._resolve_deferred_scope({'target_country':'Germany','scope_policy':'seed_url'},rows,[],{'quarantined':0,'rejected_scope':0},diagnostics)
    assert '_loc_pending'not in row
    assert any('skipped after cancellation'in s for s in diagnostics)


def test_dom_title_priority_and_no_guessed_id_urls():
    node=shutil.which('node')
    if not node:pytest.skip('Node not installed; optional JS helper execution')
    # Minimal in-memory DOM doubles, not a browser, server or network call.
    setup='''const document={};const window={location:{href:'https://fixture.invalid/jobs'},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'})};
const mk=t=>({nodeType:1,innerText:t,textContent:t,childNodes:[],getBoundingClientRect:()=>({width:10,height:10}),matches:()=>false});
'''
    # querySelectorAllDeep walks nodes by matches(), so provide faithful selector matches.
    exercise='''
const category=mk('AUTOMOTIVE');category.matches=s=>s==='[class*="title" i]';
const heading=mk('Automotive Engineer');heading.matches=s=>s==='h3';
const card=mk('AUTOMOTIVE\\nAutomotive Engineer');card.childNodes=[category,heading];
card.getAttribute=k=>k==='data-id'?'123456':'';card.querySelector=()=>null;
console.log(JSON.stringify({title:titleFromScope(card),url:pickJobUrl(card),line:firstGoodLine('AUTOMOTIVE\\nAddetto alla produzione')}));
'''
    proc=subprocess.run([node,'-e',setup+cs.JS_HELPERS+exercise],capture_output=True,text=True,check=True)
    result=json.loads(proc.stdout)
    assert result=={'title':'Automotive Engineer','url':'','line':'Addetto alla produzione'}

@pytest.mark.parametrize('found',[0,3])
def test_completion_status_retains_cancelled(found):
    from packtoworkeu.scanning.common import completion_status
    assert completion_status(found,'ScanCancelled: stopped by user',[])=='cancelled'


def test_real_requisition_category_label_is_rescued_not_accepted(scanner):
    assert not scanner.is_valid_job_title('AUTOMOTIVE','Fixture','https://fixture.invalid/job/123456/automotive-engineer')
    assert scanner.is_valid_job_title(scanner._title_from_url_slug('https://fixture.invalid/job/123456/automotive-engineer'),'Fixture','https://fixture.invalid/job/123456/automotive-engineer')


def test_pipeline_keeps_short_role_and_rescues_metadata_and_quarantines_category(tmp_path,monkeypatch):
    s=_seed_scanner(tmp_path)
    monkeypatch.setattr(s,'_fetch_provider_jobs',lambda _:([
        {'job_title':'Analyst','job_url':'https://fixture.invalid/job/123456/analyst','location_hint':'Berlin, Germany'},
        {'job_title':'Associated with 2 categories','job_url':'https://fixture.invalid/job/234567/customer-experience-technical-leader','location_hint':'Berlin, Germany'},
        {'job_title':'AUTOMOTIVE','job_url':'https://fixture.invalid/careers#job=automotive--berlin','location_hint':'Berlin, Germany'},
    ],'offline fixture'))
    # JD transport isn't this test's subject; an empty response must not be a
    # sponsorship refusal. No title rescue request is needed for these rows.
    monkeypatch.setattr(s,'_fetch_jd_text',lambda *a,**kw:('','',''))
    monkeypatch.setattr(s,'_needs_browser_detail',lambda *_:False)
    monkeypatch.setattr(s,'_title_from_detail_page',lambda *a,**kw:pytest.fail('unexpected title rescue fetch'))
    s.execute_crawler()
    rows=_read(tmp_path/'jobs.csv');quarantine=_read(tmp_path/'jobs_quarantine.csv')
    assert {r['Job Title']for r in rows}=={'Analyst','Customer Experience Technical Leader'}
    assert all(r['Visa Sponsorship']=='Unknown'for r in rows)
    assert len(quarantine)==1 and quarantine[0]['Raw Job Title']=='AUTOMOTIVE'
