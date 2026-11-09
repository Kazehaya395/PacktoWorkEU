"""Issue 005 E2a: local-only route integrity for Teddy and Artsana.

The URLs are the user's observed public detail links; tests neither visit
those portals nor claim that the earlier listing run exposed a card anchor.
No E2b card selector, cvbankas admission or pagination behavior is changed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from urllib.parse import parse_qsl, urlsplit

import pytest

from sponsorscout.core.job_identity import posting_identity
from sponsorscout.scanning.career import career_scanner as career

TEDDY_SEED = "https://www.teddycareers.it/#/public-area/careers/ads-list/0"
TEDDY_DETAIL = "https://www.teddycareers.it/#/public-area/careers/ad-detail/671"
ALLIBO_DETAIL = (
    "https://joblink.allibo.com/ats9/job-offer.aspx"
    "?DM=2255&ID=183626&LN=IT&FT=100753&SG=2"
)
ARTSANA_SEED = "https://www.artsana.com/hr-careers/"


@pytest.fixture(scope="module")
def scanner():
    return career.CareerPortalScanner(input_csv="__unused__.csv", skip_preflight=True)


def test_e2a_teddy_hash_is_kept_as_a_real_distinct_posting(scanner):
    clean = scanner.clean_and_normalize_url(TEDDY_DETAIL)
    assert clean == TEDDY_DETAIL
    assert scanner.is_valid_job_url(clean)
    assert not scanner.is_self_listing_url(clean, TEDDY_SEED)
    assert scanner.is_valid_job_title("Retail Project Manager", "Teddy Group", clean)
    assert "#job=" not in clean  # do not substitute an unopenable synthetic URL
    another = TEDDY_DETAIL.replace("/671", "/672")
    assert scanner.clean_and_normalize_url(another) == another
    assert posting_identity(clean) != posting_identity(another)
    assert scanner.canonical_job_id("Teddy Group", clean) != scanner.canonical_job_id("Teddy Group", another)


@pytest.mark.parametrize("url", [
    TEDDY_SEED,
    "https://www.teddycareers.it/",
    "https://www.teddycareers.it/#/public-area/careers/ads-list/1",
    "https://www.teddycareers.it/#/public-area/careers/ad-detail/0",
    "https://www.teddycareers.it/#/public-area/careers/ad-detail/abc",
    "https://www.teddycareers.it/#/public-area/careers/ad-detail/671/apply",
    "https://www.teddycareers.it/#/public-area/careers/ad-detail/12345678901",
    "https://www.teddycareers.it/?token=secret#/public-area/careers/ad-detail/671",
    "https://www.teddycareers.it/private/#/public-area/careers/ad-detail/671",
    "https://other.example/#/public-area/careers/ad-detail/671",
    "https://evil@www.teddycareers.it/#/public-area/careers/ad-detail/671",
    "http://www.teddycareers.it/#/public-area/careers/ad-detail/671",
])
def test_e2a_teddy_list_and_unrelated_hashes_do_not_gain_detail_exemption(scanner, url):
    assert not scanner._is_teddy_detail_route(url)
    clean = scanner.clean_and_normalize_url(url)
    assert "ad-detail" not in urlsplit(clean).fragment
    if (urlsplit(url).scheme == "https"
            and urlsplit(url).netloc == "www.teddycareers.it"
            and urlsplit(url).path == "/"):
        assert scanner.is_self_listing_url(clean, TEDDY_SEED)


def test_e2a_preexisting_synthetic_fragment_still_kept(scanner):
    previous = "https://other.example/careers/#job=retail-project-manager--rimini"
    assert scanner.clean_and_normalize_url(previous) == previous
    assert not scanner.is_self_listing_url(previous, "https://other.example/careers/")


def test_e2a_allibo_preserves_observed_fields_in_original_order(scanner):
    clean = scanner.clean_and_normalize_url(ALLIBO_DETAIL)
    assert clean == ALLIBO_DETAIL
    assert parse_qsl(urlsplit(clean).query) == [
        ("DM", "2255"), ("ID", "183626"), ("LN", "IT"),
        ("FT", "100753"), ("SG", "2"),
    ]
    assert scanner.is_valid_job_url(clean)
    assert not scanner.is_self_listing_url(clean, ARTSANA_SEED)
    assert scanner.is_valid_job_title(
        "Addetto/a Vendite Full Time/ Part Time Assago", "Artsana Group", clean)
    assert posting_identity(clean) != posting_identity(
        clean.replace("ID=183626", "ID=183627"))


def test_e2a_allibo_does_not_keep_unknown_tracking_or_secret_query_fields(scanner):
    with_noise = ALLIBO_DETAIL + "&utm_source=careers&session=private&token=secret&auth=private"
    assert scanner.clean_and_normalize_url(with_noise) == ALLIBO_DETAIL


@pytest.mark.parametrize("url", [
    ALLIBO_DETAIL.replace("joblink.allibo.com", "other.example"),
    ALLIBO_DETAIL.replace("/ats9/job-offer.aspx", "/ats9/jobs.aspx"),
    ALLIBO_DETAIL.replace("/ats9/", "/ats1234/"),
    ALLIBO_DETAIL.replace("https://", "http://"),
    ALLIBO_DETAIL.replace("ID=183626", "ID=not-a-number"),
    ALLIBO_DETAIL.replace("ID=183626", "ID=183626&ID=183627"),
])
def test_e2a_allibo_extra_fields_require_an_exact_observed_detail_shape(scanner, url):
    clean = scanner.clean_and_normalize_url(url)
    query = dict(parse_qsl(urlsplit(clean).query))
    assert "DM" not in query
    assert "LN" not in query
    assert "FT" not in query
    assert "SG" not in query


@pytest.mark.parametrize("extra", [
    "&DM=123", "&FT=invalid", "&SG=123456789012345678901", "&LN=IT%2Fprivate",
])
def test_e2a_allibo_drops_duplicated_or_invalid_context_without_dropping_id(scanner, extra):
    clean = scanner.clean_and_normalize_url(ALLIBO_DETAIL + extra)
    fields = parse_qsl(urlsplit(clean).query)
    assert ("ID", "183626") in fields
    if extra.startswith("&DM="):
        assert not any(k == "DM" for k, _ in fields)
    elif extra.startswith("&FT="):
        assert ("FT", "100753") not in fields
    elif extra.startswith("&SG="):
        assert ("SG", "2") not in fields
    else:
        assert not any(k == "LN" for k, _ in fields)


def test_e2a_ikea_tigota_and_generic_tracking_rules_unchanged(scanner):
    ikea = ("https://jobs.ikea.com/en/job/odense/"
            "logistikmedarbejder-til-aften-og-nattearbejde-30-timer-pr-uge-odense/24107/102005222688")
    tigota = ("https://lavoro.gottardospa.it/job/view-job.php"
              "?id=7065-addetto-a-vendita-san-miniato-pi-san-miniato&language=it")
    assert scanner.clean_and_normalize_url(ikea) == ikea
    assert scanner.clean_and_normalize_url(tigota) == tigota
    assert scanner.clean_and_normalize_url(ikea + "?utm_source=private") == ikea
    assert scanner.is_valid_job_url(ikea)
    assert scanner.is_valid_job_url(tigota)


def _js_gates(seed, urls):
    """Exercise the shipped JS_HELPERS as-is; no browser/server/network."""
    script = ("const window={location:{href:" + json.dumps(seed) + "}}; "
              "const document={};\n" + career.JS_HELPERS + "\n"
              "const urls=" + json.dumps(urls) + ";\n"
              "console.log(JSON.stringify(urls.map(u=>({teddy:isTeddyDetailRoute(u),"
              "self:isSelfListingUrl(u),job:looksJobUrl(u)}))));")
    run = subprocess.run(["node", "-e", script], capture_output=True,
                         text=True, timeout=10, check=True)
    return json.loads(run.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional; Python gates still tested")
def test_e2a_teddy_dom_gate_accepts_only_the_real_hash_route():
    urls = [TEDDY_DETAIL, TEDDY_SEED,
            "https://www.teddycareers.it/#/public-area/careers/ad-detail/abc",
            "https://www.teddycareers.it/#/public-area/careers/ads-list/1",
            "https://www.teddycareers.it/#/public-area/careers/ad-detail/0",
            "https://www.teddycareers.it/#/public-area/careers/ad-detail/671/apply"]
    got = _js_gates(TEDDY_SEED, urls)
    assert got[0] == {"teddy": True, "self": False, "job": True}
    assert all(entry == {"teddy": False, "self": True, "job": False}
               for entry in got[1:])


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional; Python gates still tested")
def test_e2a_allibo_dom_gate_still_accepts_the_cross_host_detail():
    assert _js_gates(ARTSANA_SEED, [ALLIBO_DETAIL])[0]["job"] is True


def test_e2a_jd_browser_reader_navigates_the_full_hash_with_a_fake_page(scanner, monkeypatch):
    """A fake page proves goto gets #/ad-detail/671; it is NOT a live JD test."""
    class FakePage:
        def __init__(self):
            self.navigated = None
            self._last_nav_status = 0

        def goto(self, url, **_kwargs):
            self.navigated = url
            return type("Response", (), {"status": 200})()

        def wait_for_timeout(self, _timeout):
            pass

        def content(self):
            return "<main><h1>Retail Project Manager</h1></main>"

    monkeypatch.setattr(career, "sync_playwright", object())
    monkeypatch.setattr(scanner, "_detail_can_browser", lambda _url: True)
    monkeypatch.setattr(scanner._detail_state, "reserve", lambda *_a, **_kw: True)
    monkeypatch.setattr(scanner, "_bot_challenge_reason", lambda *_a, **_kw: "")
    monkeypatch.setattr(scanner, "_remember_detail_html", lambda *_a, **_kw:
                        ("A job description from a fake page", "Rimini", "Teddy Group"))
    monkeypatch.setattr(scanner, "store_captured_jd", lambda *_a, **_kw: None)
    monkeypatch.setattr(scanner, "_note_detail_success", lambda *_a, **_kw: None)
    page = FakePage()
    result = scanner._fetch_jd_text_browser(scanner.clean_and_normalize_url(TEDDY_DETAIL),
                                           browser_page=page)
    assert page.navigated == TEDDY_DETAIL
    assert result[0] == "A job description from a fake page"
