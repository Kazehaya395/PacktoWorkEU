"""Local field regressions: no external portal traffic."""
import os
from types import SimpleNamespace
import pytest
from packtoworkeu.core import browser_evidence as be, blocked_portals as bp
from packtoworkeu.core.job_identity import nonposting_record, posting_candidate
from packtoworkeu.core.session_details import parse_detail
from packtoworkeu.scanning.jd_support import JDSupportDetector

CASES = [('https://4screen.com/it/detail-screen', 'Detail Screen - 4screen'), ('https://en.avocargo.one/karriere#job=tag-manager-new-', 'Career I Avocargo'), ('https://en.avocargo.one/karriere#job=tag-manager-1-new-', 'Career I Avocargo'), ('https://careers.smartrecruiters.com/LGCGroup/lgc_axolabs_en', 'LGC Group'), ('https://www.beiersdorf.com/career/students/internships-and-working-students', 'INTERNSHIPS & WORKING STUDENTS'), ('https://www.beiersdorf.com/career/students/trainee-program/overview', 'BEYOND'), ('https://www.beiersdorf.com/career/students/phd-program', 'PHD PROGRAM AT BEIERSDORF'), ('https://www.freenet.ag/karriere/berufseinsteiger', 'Teil unseres Teams werden'), ('https://www.freenet.ag/karriere/berufserfahrene', 'Für Erfahrene'), ('https://www.kiongroup.com/en/Careers/Career-paths/Start-at-KION', 'Grow with us and be part of our success story.'), ('https://misterspex.wd103.myworkdayjobs.com/en-US/Mister_Spex_Careers', 'Go to jobs'), ('https://misterspex.wd103.myworkdayjobs.com/de-DE/Mister_Spex_Careers/page/e58aa53b13c21001284d616e5d0e0005', 'Opticians'), ('https://misterspex.wd103.myworkdayjobs.com/de-DE/Mister_Spex_Careers/page/003b92cb0e391001ab561d33814b0000', 'Headquarter'), ('https://www.moia.io/en/career/moia-gmbh', 'Get on board'), ('https://www.rheinmetall.com/en/career/rheinmetall-top-employer/human-ressource-development/human-ressource-development-overview', 'training and further education'), ('https://www.rheinmetall.com/en/career/inside-rheinmetall-careers/people-projects/career-extra-soldiers', 'Soldiers'), ('https://www.rheinmetall.com/en/career/rheinmetall-top-employer/human-ressource-development', 'Human Resource development'), ('https://www.rheinmetall.com/en/career/rheinmetall-top-employer/performance-and-remuneration', 'Benefits and remuneration'), ('https://www.rheinmetall.com/en/career/career-contact', 'PLEASE CONTACT US!'), ('https://www.suedzuckergroup.com/en/careers/what-we-offer', 'Reasons to join Südzucker'), ('https://www.sma.de/karriere/schuelerinnen', "Hol' dir alle Infos!"), ('https://www.sma.de/karriere/arbeiten-bei-sma', 'Erfahre, was es bedeutet, bei uns zu arbeiten.'), ('https://www.sma.de/karriere/studierende', 'Entdecke deine Möglichkeiten!'), ('https://www.thyssenkrupp.com/de/karriere/thyssenkrupp-als-arbeitgeber/nord-amerika', 'Entdecke spannende Karrierewege und vielfältige Projekte an unseren Standorten in Nordamerika.'), ('https://www.elringklinger.de/en/career/areas-of-work/production-and-logistics', 'Production & Logistics'), ('https://elringklinger.de/en/career/job-openings', 'Your expertise makes the difference - in projects that shape our future.')]

@pytest.mark.parametrize('url,title', CASES)
def test_information_rows_are_rejected_before_rescue(url,title):
    assert nonposting_record(url,title)
    assert not posting_candidate(url,title,lambda _:True)

@pytest.mark.parametrize('title',['Benefits Manager','Group Controller','Contact Center Specialist','Training Engineer','Soldier','Optician','Headquarter IT Engineer','Tag Manager Specialist'])
def test_real_requisition_wins_over_category_words(title):
    url='https://jobs.fixture.invalid/jobs/123456/role'
    assert not nonposting_record(url,title)
    assert posting_candidate(url,title,lambda _:True)

def test_open_application_policy_reversed_2026_11_09():
    # User decision 2026-11-09: generic applications are noise, not
    # vacancies - even on an explicit-detail-shaped URL.
    assert not posting_candidate('https://board.fixture.invalid/jobs/open-application','Unsolicited Application')

@pytest.fixture
def page():
    if os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1':pytest.skip('opt-in local browser')
    from playwright.sync_api import sync_playwright
    with sync_playwright()as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page()
        page.route('**/*',lambda route:route.fulfill(status=200,body='<html></html>',content_type='text/html'))
        page.goto('https://jobs.fixture.invalid/jobs/123456/engineer')
        yield page
        browser.close()

def test_complete_sections_include_benefits_not_recommendations(page):
    duties='Build reliable software and improve customer services. '*12
    page.set_content('<main><h1>Engineer</h1><article><section class="job-description"><h2>Job Description</h2>'+duties+'</section><section><h2>Qualifications</h2>Experience building software.</section><section><h2>Benefits</h2>We offer visa sponsorship and a relocation package.</section><section><h2>Contact</h2>CONTACT_SECRET</section><section><h2>Related jobs</h2>UNRELATED_SECRET</section></article></main>')
    snapshot=be.inspect_page(page,kind='detail')
    text=snapshot['primary_text']
    assert 'Qualifications' in text and 'relocation package' in text
    assert 'CONTACT_SECRET' not in text and 'UNRELATED_SECRET' not in text
    result=parse_detail(page.content(),page.url,page.url,SimpleNamespace(),browser_evidence=snapshot)
    assert result.status=='readable' and result.text==text
    assert JDSupportDetector().detect(result.text)['relocation']['verdict']=='Yes'

def test_multiple_posting_roots_do_not_merge_benefits(page):
    page.set_content('<main><h1>Engineer</h1><div class="job-description">'+'Develop reliable applications. '*20+'</div><h1>Another Engineer</h1><h2>Qualifications</h2>UNRELATED_QUALIFICATION<h2>Benefits</h2>UNRELATED_RELOCATION</main>')
    assert 'UNRELATED_RELOCATION' not in be.inspect_page(page,kind='detail')['primary_text']

def test_native_nonsemantic_append_uses_real_click_and_growth(page):
    page.set_content('''<main><h1>Jobs</h1><div id="list"><article><a href="/jobs/123456/engineer">Engineer</a></article><article><a href="/jobs/123457/analyst">Analyst</a></article></div><div class="load-more" onclick="document.querySelector('#list').insertAdjacentHTML('beforeend','<article><a href=/jobs/123458/tester>Tester</a></article>');this.remove()"><span>Mehr laden</span></div></main>''')
    assert bp._native_append_advance(page) is True
    assert '/jobs/123458/tester' in bp._first_listing_signature(page)

def test_sidebar_append_is_not_clicked(page):
    page.set_content('<main><article><a href="/jobs/123456/engineer">Engineer</a></article><article><a href="/jobs/123457/analyst">Analyst</a></article></main><aside><button onclick="window.BAD_CLICK=true">Mehr laden</button></aside>')
    assert bp._native_append_advance(page) is None
    assert not page.evaluate('Boolean(window.BAD_CLICK)')

def test_ambiguous_append_controls_are_not_clicked(page):
    page.set_content('<main><article><a href="/jobs/123456/engineer">Engineer</a></article><article><a href="/jobs/123457/analyst">Analyst</a></article><button>Mehr laden</button><button>Mehr laden</button></main>')
    assert bp._native_append_advance(page) is False

def test_no_growth_is_not_a_success(page):
    page.set_content('<main><article><a href="/jobs/123456/engineer">Engineer</a></article><article><a href="/jobs/123457/analyst">Analyst</a></article><button>Mehr laden</button></main>')
    assert bp._native_append_advance(page) is False


def test_regular_split_discovery_uses_observed_source_urls(page):
    from packtoworkeu.tests.test_metro_bayer_repairs_2026_10_31 import NATIVE_HTML,URL
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    page.goto(URL)
    page.set_content(NATIVE_HTML)
    scanner=SimpleNamespace(cancel_event=None)
    rows=CareerPortalScanner.extract_visible_jobs(scanner,page)
    assert len(rows)==2
    assert all(r['extraction_method']=='native_selected_card' for r in rows)
    assert all('pid=' in r['job_url'] and '#job=' not in r['job_url'] for r in rows)
    assert len({r['job_url'] for r in rows})==2


def test_changed_queue_url_requires_explicit_confirmation(monkeypatch):
    from packtoworkeu.ui.tabs import assisted_ingestion as ui
    monkeypatch.setattr(ui.QMessageBox,'question',lambda *a,**k:ui.QMessageBox.StandardButton.No)
    edit=lambda value:SimpleNamespace(text=lambda:value)
    fake=SimpleNamespace(_worker=None,_scan_running=False,url_edit=edit('https://other.invalid/jobs'),
        name_edit=edit('Metro'),portals_table=SimpleNamespace(currentRow=lambda:0),
        _queue_rows=[{'seed_name':'Metro','url':'https://original.invalid/jobs'}])
    # Rejecting the warning must exit before touching controls or creating a worker.
    ui.AssistedIngestionTab._start_ingestion(fake)


def test_regular_native_pid_tenant_and_filters_survive_admission():
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    from packtoworkeu.tests.test_metro_bayer_repairs_2026_10_31 import URL
    scanner=object.__new__(CareerPortalScanner)
    assert scanner.clean_and_normalize_url(URL)==URL
    assert scanner.has_job_identifier_query(URL)
    assert not scanner.is_self_listing_url(URL,URL)
    second=URL.replace('600001','600002')
    assert scanner.canonical_job_id('Fixture',URL)!=scanner.canonical_job_id('Fixture',second)
    assert not scanner.has_job_identifier_query('https://other.invalid/careers?pid=600001&domain=fixture.invalid')
