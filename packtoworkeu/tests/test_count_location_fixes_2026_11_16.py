"""Count/location fixes (2026-11-16, user "fix all" on run analysis).

A. Location resolver: unspaced compounds ("Lisbon/Porto"), pipe lists,
   missing cities (Klagenfurt/Makati), UK counties/regions.
B. Pagination: URL-parameter fallback (SuccessFactors `startrow`-style)
   when no clickable next control is recognised; stated totals already
   parsed by response_evidence ("Results 1 - 25 of 173").
C. Persisted location-recovery queue: unlocated rows are saved across
   runs and drained first (revives deferred U3 for locations).
"""
from types import SimpleNamespace

from packtoworkeu.core.location_country import country_from_location as resolve
from packtoworkeu.scanning.response_evidence import stated_job_total


# ── A ────────────────────────────────────────────────────────────────────
def test_a_unspaced_compounds_and_pipe_lists():
    assert resolve("Lisbon/Porto") == "Portugal"
    assert resolve("remote|koeln|darmstadt|duesseldorf|berlin") == "Germany"


def test_a_missing_cities_and_uk_regions():
    assert resolve("Klagenfurt") == "Austria"
    assert resolve("Makati") == "Philippines"
    assert resolve("Tainan") == "Taiwan"
    assert resolve("Greater London") == "United Kingdom"
    assert resolve("Bolton, Greater Manchester") == "United Kingdom"
    assert resolve("East Sussex") == "United Kingdom"


def test_a_guards_unchanged():
    assert resolve("La Paz - Bolivia") == "Bolivia"   # explicit country wins
    assert resolve("Remote") == ""
    assert resolve("Munich, Germany") == "Germany"
    assert resolve("Milan, MI") == "Italy"


# ── B ────────────────────────────────────────────────────────────────────
def test_b_stated_total_parsing():
    assert stated_job_total("<span>Results <b>1 – 25</b> of <b>173</b></span>") == 173


class _FakeTarget:
    def __init__(self, data):
        self._data = data

    def evaluate(self, js, *args):
        if "a[href]" in js:
            return self._data
        return {}


class _FakePage:
    def __init__(self):
        self.visited = []

    def goto(self, url, **kw):
        self.visited.append(url)


def _scanner_for_pagination():
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    obj = CareerPortalScanner.__new__(CareerPortalScanner)
    obj.config = SimpleNamespace(ACTION_TIMEOUT_MS=1000)
    obj._wait_for_listing_transition = lambda *a: None
    return obj


def test_b_url_param_pagination_successfactors_style():
    obj = _scanner_for_pagination()
    base = "https://jobs.example.com/search/?q=&searchby=location"
    data = {"href": base, "links": [
        {"key": "startrow", "value": 25, "href": base + "&startrow=25"},
        {"key": "startrow", "value": 50, "href": base + "&startrow=50"},
    ]}
    page = _FakePage()
    assert obj._try_url_param_pagination(page, _FakeTarget(data)) is True
    assert page.visited == [base + "&startrow=25"]
    # From page 2 the next step is 50, never backwards.
    data2 = {"href": base + "&startrow=25", "links": data["links"]}
    page2 = _FakePage()
    assert obj._try_url_param_pagination(page2, _FakeTarget(data2)) is True
    assert page2.visited == [base + "&startrow=50"]


def test_b_url_param_pagination_no_candidate():
    obj = _scanner_for_pagination()
    page = _FakePage()
    data = {"href": "https://jobs.example.com/jobs", "links": []}
    assert obj._try_url_param_pagination(page, _FakeTarget(data)) is False
    assert page.visited == []


# ── C ────────────────────────────────────────────────────────────────────
def test_c_location_queue_persists_and_drops_recovered(tmp_path, monkeypatch):
    import packtoworkeu.paths as paths
    monkeypatch.setattr(paths, "USER_DATA_DIR", tmp_path)
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    obj = CareerPortalScanner.__new__(CareerPortalScanner)
    target_row = {"seed_name": "Siemens", "name": "Siemens"}
    jobs = {
        "1": {"Job URL": "https://jobs.example.com/1", "Job Title": "A",
              "Company Name": "Siemens", "Job Location": "Unknown"},
        "2": {"Job URL": "https://jobs.example.com/2", "Job Title": "B",
              "Company Name": "Siemens", "Job Location": "Munich, Germany"},
    }
    obj._update_location_queue(target_row, jobs, {})
    queue = obj._load_location_queue()
    assert "https://jobs.example.com/1" in queue          # unlocated persisted
    assert "https://jobs.example.com/2" not in queue      # located never queued
    # Next run: row 1 recovered -> dropped from the persisted queue.
    jobs["1"]["Job Location"] = "Berlin, Germany"
    obj._loc_queue_cache = None
    obj._update_location_queue(target_row, jobs, obj._load_location_queue())
    obj._loc_queue_cache = None
    assert obj._load_location_queue() == {}


# ── E (2026-11-16 follow-up: fixable without a full scan) ────────────────
def test_e_scope_browser_cap_not_strangled_by_jd_cap(monkeypatch):
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    obj = CareerPortalScanner.__new__(CareerPortalScanner)
    monkeypatch.delenv("PACKTOWORKEU_SCOPE_BROWSER_MAX", raising=False)
    assert obj._scope_browser_cap() == 150          # was effectively 12
    monkeypatch.setenv("PACKTOWORKEU_SCOPE_BROWSER_MAX", "40")
    assert obj._scope_browser_cap() == 40
    monkeypatch.setenv("PACKTOWORKEU_SCOPE_BROWSER_MAX", "junk")
    assert obj._scope_browser_cap() == 150


def test_e_load_more_branch_present_in_pagination_js():
    import inspect
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    src = inspect.getsource(CareerPortalScanner.execute_advanced_pagination)
    assert "'load_more'" in src or '"load_more"' in src or "load_more" in src
    assert "mehr" in src and "laden" in src


# ── G (2026-11-17: URL-slug locations, measured on run 20261116T192054) ──
def _bare_scanner():
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    return CareerPortalScanner.__new__(CareerPortalScanner)


def test_g_workday_alpha2_url_format():
    obj = _bare_scanner()
    assert obj._workday_url_location(
        "https://agilent.wd5.myworkdayjobs.com/Agilent_Careers/job/US-CO-Boulder/Automation-Project-Manager_R-123") == ("Boulder", "United States")
    assert obj._workday_url_location(
        "https://3m.wd1.myworkdayjobs.com/Search/job/GB-Bracknell-Berks/Content-Creator") == ("Bracknell", "United Kingdom")
    assert obj._workday_url_location(
        "https://x.wd1.myworkdayjobs.com/S/job/VN-DANANG-VIRTUAL/Sales") == ("DANANG", "Vietnam")
    # alpha-2 form is Workday-only: a generic host must not be read this way
    assert obj._workday_url_location("https://example.com/job/DE-Berlin-Office/Dev") == ("", "")


def test_g_job_url_slug_places():
    obj = _bare_scanner()
    assert obj._location_from_job_url_slug(
        "https://careers.tuigroup.com/en/job/marrakesh/front-desk-agent/2937/468") == ("Marrakesh", "Morocco")
    assert obj._location_from_job_url_slug(
        "https://jobs.volkswagen-group.com/Volkswagen/job/Lisboa-Fullstack-Developer-1200-246/123") == ("Lisboa", "Portugal")
    assert obj._location_from_job_url_slug(
        "https://www.arbeitnow.com/jobs/companies/naroiq/capital-markets-manager-berlin-16410") == ("Berlin", "Germany")
    # genuinely placeless slugs stay Unknown
    assert obj._location_from_job_url_slug(
        "https://careers.tuigroup.com/en/job/flexible/swim-coach-eu-nationals/2937/465") == ("", "")
    assert obj._location_from_job_url_slug(
        "https://www.arbeitnow.com/jobs/companies/luminovo/remote-senior-staff-platform-engineer-rust-228633") == ("", "")


def test_g3_slug_folded_forms_and_abbreviations():
    obj = _bare_scanner()
    # Umlaut dropped by the board's slug builder (AERQ run 20261117T121539)
    assert obj._location_from_job_url_slug(
        "https://ag.wd3.myworkdayjobs.com/Airbus/job/Mnchen-Area/Technical-Specialist") == ("München", "Germany")
    assert obj._location_from_job_url_slug(
        "https://ag.wd3.myworkdayjobs.com/Airbus/job/Donauwrth/Certifying-Staff") == ("Donauwörth", "Germany")
    # Native spelling the gazetteer only knows in English form
    assert obj._location_from_job_url_slug(
        "https://jobs.bilfinger.com/job/Warszawa-Projektant/123") == ("Warszawa", "Poland")
    assert obj._location_from_job_url_slug(
        "https://careers.tuigroup.com/en/job/willemstad/customer-service-agent/3116") == ("Willemstad", "Curaçao")
    # Abbreviation expansion + Iberian particle
    assert obj._location_from_job_url_slug(
        "https://ag.wd3.myworkdayjobs.com/Airbus/job/El-Puerto-Sta-Maria/XMLNAME--X") == ("El Puerto de Santa Maria", "Spain")


def test_g3_slug_title_fragments_stay_unknown():
    obj = _bare_scanner()
    # Title-only slugs: "Engineer" is a Kenyan town in GeoNames, and
    # "Principal Product ..." resolved to Cabo Verde before the guard.
    assert obj._location_from_job_url_slug(
        "https://jobs.wacker.com/job/QC-Engineer/31259-en_US") == ("", "")
    assert obj._location_from_job_url_slug(
        "https://www.amazon.jobs/de/jobs/10601744/principal-product-manager-ww-seller-economics-and-fees") == ("", "")
    assert obj._location_from_job_url_slug(
        "https://www.amazon.jobs/de/jobs/10503245/verification-engineer") == ("", "")
    # Workday country-state form defers to the G1 pass, never fragments:
    # "Cedar Creek" (Texas) must not become "Creek" -> Cayman Islands.
    assert obj._location_from_job_url_slug(
        "https://agilent.wd5.myworkdayjobs.com/Agilent_Careers/job/US-TX-Cedar-Creek/Logistics-Supervisor_4040225") == ("", "")
    # Gazetteer-ambiguous towns behind "-Area" suffixes stay Unknown.
    assert obj._location_from_job_url_slug(
        "https://ag.wd3.myworkdayjobs.com/Airbus/job/Saint-Nazaire-Area/STAGE-2027") == ("", "")
    assert obj._location_from_job_url_slug(
        "https://ag.wd3.myworkdayjobs.com/Airbus/job/Cadiz-Area/XMLNAME--X") == ("", "")


# ── H (2026-11-17 PM: fragmentation + wrong-country + chrome titles) ──
# Measured offline on runs 20261117T121539 and 20261116T192054; the
# user explicitly did not want group re-runs to verify these.

def test_h_us_state_code_not_iso_country():
    # "Mobile, AL" shipped as Albania; the GeoNames-unique US place
    # guard now reads the code as the US state it is.
    assert resolve("Mobile Area, AL") == "United States"
    assert resolve("Mobile, AL") == "United States"
    assert resolve("Rehoboth Beach, DE") == "United States"
    assert resolve("Castle Rock, CO") == "United States"
    assert resolve("Chicago Fashion Outlet, IL") == "United States"
    # Guards: genuinely German/Canadian/Italian readings must not move.
    assert resolve("Berlin, Belgium, DE") == "Germany"
    assert resolve("Hamburg, DE") == "Germany"
    assert resolve("Milan, MI") == "Italy"
    assert resolve("Toronto, CA") == "Canada"


def test_h_czech_short_form():
    assert resolve("Czech") == "Czech Republic"


def test_h_workday_place_not_fragmented():
    obj = _bare_scanner()
    assert obj._workday_url_location(
        "https://agilent.wd5.myworkdayjobs.com/Agilent_Careers/job/US-TX-Cedar-Creek/Logistics-Supervisor_4040225") == ("Cedar Creek", "United States")
    assert obj._workday_url_location(
        "https://agilent.wd5.myworkdayjobs.com/Agilent_Careers/job/US-MI-Grand-Rapids/Field-Service-Engineer_1") == ("Grand Rapids", "United States")
    assert obj._workday_url_location(
        "https://agilent.wd5.myworkdayjobs.com/Agilent_Careers/job/US-RI-North-Kingstown/Manager_1") == ("North Kingstown", "United States")
    assert obj._workday_url_location(
        "https://agilent.wd5.myworkdayjobs.com/Agilent_Careers/job/US-CO-Boulder/Engineer_1") == ("Boulder", "United States")


def test_h_org_name_is_not_a_location(tmp_path):
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    seed = tmp_path / "seed.csv"
    seed.write_text("company,url\nBilfinger,https://jobs.bilfinger.com\n", encoding="utf-8")
    obj = CareerPortalScanner(input_csv=str(seed), skip_preflight=True)
    assert obj._sanitize_job_location(
        "Bilfinger ISP Germany GmbH", "Bilfinger") == "Unknown"
    assert obj._sanitize_job_location("Linz", "Bilfinger") == "Linz"
    assert obj._sanitize_job_location(
        "Mobile Area, AL", "Agilent") == "Mobile Area, AL"


def test_h_category_pages_and_chrome_are_not_jobs(tmp_path):
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    seed = tmp_path / "seed.csv"
    seed.write_text("company,url\nArbeitnow,https://www.arbeitnow.com\n", encoding="utf-8")
    obj = CareerPortalScanner(input_csv=str(seed), skip_preflight=True)
    assert not obj.is_valid_job_title("DevOps", "Arbeitnow", "https://www.arbeitnow.com/jobs/devops")
    assert not obj.is_valid_job_title("Data Analyst", "Arbeitnow", "https://www.arbeitnow.com/jobs/data-analyst")
    assert not obj.is_valid_job_title("Companies Hiring in Germany", "Arbeitnow", "https://www.arbeitnow.com/jobs/companies")
    assert not obj.is_valid_job_title("Post free jobs on our job board", "Arbeitnow", "https://www.arbeitnow.com/free-job-posting")
    # Real postings keep passing, including bare-title slugs with an ID.
    assert obj.is_valid_job_title("Software Engineer", "Example", "https://jobs.example.com/job/software-engineer")
    assert obj.is_valid_job_title("DevOps Engineer", "Example", "https://jobs.example.com/jobs/12345")


# ── I (2026-11-17 evening: live leftovers from run 20261117T183401) ──
# Agilent shipped 42 rows with a place but Country Unknown (Santa
# Clara / Remote / Greater Toronto) although every job URL stated
# the country; arbeitnow categories returned with a "Find " prefix.

def test_i_greater_metro_prefix():
    assert resolve("Greater Toronto") == "Canada"
    assert resolve("Greater London") == "United Kingdom"


def test_i_workday_remote_and_spelled_country_forms(tmp_path):
    obj = _bare_scanner()
    assert obj._workday_url_location(
        "https://ag.wd5.myworkdayjobs.com/A/job/US-CA-Santa-Clara/Eng_1") == ("Santa Clara", "United States")
    assert obj._workday_url_location(
        "https://ag.wd5.myworkdayjobs.com/A/job/US-TX-Remote-Location/Eng_1") == ("Remote", "United States")
    assert obj._workday_url_location(
        "https://ag.wd5.myworkdayjobs.com/A/job/Canada-Remote-Location-Greater-Toronto/Eng_1") == ("Remote", "Canada")


def test_i_entity_escaped_slug():
    obj = _bare_scanner()
    place, country = obj._location_from_job_url_slug(
        "https://jobs.volkswagen-group.com/Lamborghini/job/Sant&apos;Agata-Bolognese-Internship-in-Buyer-Support/1457")
    assert country == "Italy"
    assert place.startswith("Sant")


def test_i_place_only_rows_get_url_country(tmp_path):
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    seed = tmp_path / "seed.csv"
    seed.write_text("company,url\nAgilent,https://agilent.wd5.myworkdayjobs.com\n", encoding="utf-8")
    obj = CareerPortalScanner(input_csv=str(seed), skip_preflight=True)
    rec = {"Job Location": "Santa Clara", "Location Source": "url",
           "Job URL": "https://agilent.wd5.myworkdayjobs.com/A/job/US-CA-Santa-Clara/Eng_1",
           "Company Name": "Agilent"}
    jobs = {1: rec}
    obj._resolve_deferred_scope(
        {"target_country": "Global", "scope_policy": "global"},
        jobs, [], {}, [])
    assert rec["Job Location"] == "Santa Clara, United States"
    # An explicit/resolvable country is never overwritten.
    rec2 = {"Job Location": "Milan, MI", "Location Source": "card",
            "Job URL": "https://agilent.wd5.myworkdayjobs.com/A/job/US-CA-Santa-Clara/Eng_1",
            "Company Name": "Agilent"}
    jobs2 = {1: rec2}
    obj._resolve_deferred_scope(
        {"target_country": "Global", "scope_policy": "global"},
        jobs2, [], {}, [])
    assert rec2["Job Location"] == "Milan, MI"


def test_i_find_prefix_categories_and_editorial_titles(tmp_path):
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    seed = tmp_path / "seed.csv"
    seed.write_text("company,url\nArbeitnow,https://www.arbeitnow.com\n", encoding="utf-8")
    obj = CareerPortalScanner(input_csv=str(seed), skip_preflight=True)
    assert not obj.is_valid_job_title("Find DevOps", "Arbeitnow", "https://www.arbeitnow.com/jobs/devops")
    assert not obj.is_valid_job_title("Find Data Analyst", "Arbeitnow", "https://www.arbeitnow.com/jobs/data-analyst")
    assert not obj.is_valid_job_title("Was ist eigentlich ein Minijob?", "Arbeitnow", "https://www.arbeitnow.com/stellenangebote/minijob")
    assert obj.is_valid_job_title("Software Engineer", "Example", "https://jobs.example.com/job/software-engineer")
