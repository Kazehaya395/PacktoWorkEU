"""Issue 005 (career scan review): harvest census, transport honesty, budget fairness.

Every fixture here is local: synthetic DOM payloads for the census, and an opt-in
127.0.0.1 Chromium board for the assisted harvest. No customer portal is contacted, so
nothing in this file certifies that a live wall was beaten - the McDonald's/Carrefour
verdicts in Changelogs/issue_2026_10_29_career_scan_review/ still come from the run
artifacts. What these tests do pin is the code contract those artifacts asked for:

  P1  a rendered page's own posting census can carry acceptance, and the run records it
  P2  an offline machine is not reported as a wall, and a recovered transport error is
      retracted for the whole no-route family, not for DNS alone
  P3  the seed-page failure message carries the captured reason
  P4  detail counters that belong to no seed say so
  P5  the shared detail budget is charged to time spent fetching, not to wall clock
"""
from __future__ import annotations

import json
import re
import time

import pytest

from packtoworkeu.core import blocked_portals as bp
from packtoworkeu.core.browser_evidence import admitted_job_links, inspect_page
from packtoworkeu.core.job_identity import posting_identity
from packtoworkeu.scanning.career.career_scanner import (
    ProductionScannerConfig,
    _OFFLINE_MARKERS,
    _OFFLINE_STREAK_LIMIT,
    is_transient_transport_failure,
    seed_page_failure_text,
)
from packtoworkeu.scanning.detail_state import DetailRequestState

LISTING_PATH = "/lavora-con-noi/posizioni-aperte/"
ITALIAN_POSTINGS = [
    ("Addetto/a Ristorazione - Crew - Roma Appia", LISTING_PATH + "addetto-ristorazione-crew-roma-appia-4211/"),
    ("Shift Leader - Milano Duomo", LISTING_PATH + "shift-leader-milano-duomo-4212/"),
    ("Manutentore Impianti - Torino Centro", LISTING_PATH + "manutentore-impianti-torino-centro-4213/"),
    ("Addetto/a Ristorazione - Napoli Chiaia", LISTING_PATH + "addetto-ristorazione-napoli-chiaia-4214/"),
]


def _payload(url, anchors, *, title="Offerte di lavoro | Fixture Italia", headings=()):
    """The DOM shape VISIBLE_DOCUMENT_JS hands back, built by hand."""
    return {
        "version": 1, "url": url, "title": title,
        "headings": list(headings) or ["Posizioni aperte"],
        "anchors": [{"url": url + path, "title": text, "in_card": False} for text, path in anchors],
        "primary_text": "", "main_text": "Posizioni aperte in Italia",
        "body_preview": "Elenco delle posizioni aperte", "overlays": [],
        "active_ids": [], "foreground_frames": [],
    }


class _FakePage:
    def __init__(self, data=None, *, error=None):
        self._data, self._error = data, error

    def evaluate(self, script, *args):
        if self._error:
            raise self._error
        return self._data


def test_render_census_admits_the_links_the_path_list_misses():
    # The 2026-10-29 McDonald's Italia run recorded visible_job_links=164 and Jobs Found 0
    # on the same page. This is the census the page itself produced.
    page = _FakePage(_payload("https://fixtures.invalid" + LISTING_PATH[:-1], ITALIAN_POSTINGS))
    # The portal's own validator is what admitted these links in the real run
    # (`visible_job_links: 164`); `posting_candidate` alone knows no Italian path.
    links, meta = admitted_job_links(page, validator=lambda url: LISTING_PATH.strip("/") in url)
    assert [link["title"] for link in links] == [text for text, _ in ITALIAN_POSTINGS]
    assert meta["visible_job_links"] == len(links)
    assert meta["kind"] == "listing" and meta["ready"] is True

    # ...and the acceptance list it had to compete with still rejects all four paths,
    # which is why the census now has standing of its own.
    tokens = ("/jobs/", "/vacancies/", "/vacancy/", "/vacatures/", "/vacature/", "/job/",
              "/stellenangebote/", "/offres/", "/offerte/", "/ofertas/")
    for _text, path in ITALIAN_POSTINGS:
        url = "https://fixtures.invalid" + path
        assert not any(token in url for token in tokens)
        assert not re.search(r"/\d+-[a-z0-9\-]+\.html", url, re.I)
        assert not re.search(r"-[a-z0-9]+-\d+-\d+$", url, re.I)


def test_census_anchors_and_counted_links_agree_on_posting_identity():
    anchors = ITALIAN_POSTINGS + [(ITALIAN_POSTINGS[0][0], ITALIAN_POSTINGS[0][1] + "?lang=it")]
    anchors += [("Backend Engineer %d" % n, LISTING_PATH + "backend-engineer-%d/" % (90000 + n)) for n in range(200)]
    page = _FakePage(_payload("https://fixtures.invalid" + LISTING_PATH[:-1], anchors))
    snapshot = inspect_page(page, validator=lambda url: LISTING_PATH.strip("/") in url)
    admitted = snapshot["admitted_anchors"]
    assert len(admitted) <= 180, "the census must stay inside the evidence row budget"
    assert len({posting_identity(a["url"]) for a in admitted}) == snapshot["visible_job_links"]
    assert all(a["title"] and len(a["title"]) <= 240 for a in admitted)


def test_census_is_empty_not_missing_for_a_non_browser_adapter():
    links, meta = admitted_job_links(_FakePage(error=RuntimeError("not a browser page")))
    assert links == [] and meta == {}
    assert admitted_job_links(object())[0] == []


def test_no_route_family_is_transient_and_a_served_wall_is_not():
    transient = [
        "Error: Page.goto: net::ERR_NAME_NOT_RESOLVED at https://x.it/jobs",
        "net::ERR_INTERNET_DISCONNECTED",
        "Timeout 30000ms exceeded",
        "seed page did not load: https://x.it/jobs",           # C2: no reason captured
    ]
    for message in transient:
        assert is_transient_transport_failure(message), message
    # A wall that answered is a verdict about the portal, not about this machine: the
    # retraction must never swallow one (Carrefour Italia returned a Cloudflare challenge).
    durable = [
        "Cloudflare challenge page served instead of the job list",
        "HTTP 403 Forbidden",
        "blocked by bot protection: WAF/CDN rejected automated HTTP and headless browser navigation",
        "429 Too Many Requests",
        "PerimeterX challenge: Press & Hold",
    ]
    for message in durable:
        assert not is_transient_transport_failure(message), message
    assert not is_transient_transport_failure("")
    # A message that names a resolution failure *and* a block stays: retracting it could
    # edit a real wall out of errors.csv, which is the one thing this must never do.
    assert not is_transient_transport_failure("blocked and the scan machine could not resolve x.it")
    # The streak wording is only allowed to use markers the classifier also trusts.
    for marker in _OFFLINE_MARKERS:
        assert is_transient_transport_failure("Error: " + marker)
    assert _OFFLINE_STREAK_LIMIT == 3


def test_seed_page_failure_text_keeps_the_reason_and_labels_an_outage():
    unreachable = seed_page_failure_text("https://x.it/jobs", "net::ERR_INTERNET_DISCONNECTED",
                                          offline=True)
    assert unreachable.startswith("seed page unreachable: no network route from the scanning machine")
    assert "net::ERR_INTERNET_DISCONNECTED" in unreachable
    assert "not bot protection" in unreachable
    plain = seed_page_failure_text("https://x.it/jobs", "Timeout 30000ms   exceeded")
    assert plain == "seed page did not load: https://x.it/jobs [Timeout 30000ms exceeded]"
    assert seed_page_failure_text("https://x.it/jobs") == "seed page did not load: https://x.it/jobs"
    assert len(seed_page_failure_text("https://x.it/jobs", "x" * 900)) < 400


def test_error_retraction_and_coverage_census_are_no_longer_gated_on_success():
    # Both fixes are structural, so they are pinned where a future edit could quietly put
    # the old behaviour back.
    import inspect
    from packtoworkeu.scanning.career import career_scanner as career_mod
    source = open(inspect.getsourcefile(career_mod), encoding="utf-8").read()
    block = source.split("def _clear_recovered_dns_errors", 1)[1].split("\n    def ", 1)[0]
    assert "is_transient_transport_failure" in block, "the retraction reverted to DNS-only"
    assert re.search(r"if not company_jobs:\s*\n\s*return", block) is None, \
        "a seed that harvested nothing must still be retracted and still gets a census"
    coverage = source.split("# Issue 005 P2: the coverage census", 1)[1][:900]
    assert "if company_jobs:" in coverage and "diagnostics.append(self._detail_state.coverage_summary" in coverage
    assert coverage.split("if company_jobs:")[0].count("coverage_summary") >= 1, \
        "the coverage line is back inside a non-empty-harvest guard"
    assert "_offline_streak" in source and "GOTO_RETRIES" in source


def test_shared_detail_budget_ignores_dwell_and_charges_fetch_time():
    config = ProductionScannerConfig()
    state = DetailRequestState()
    url = "https://fixtures.invalid/jobs/1-engineer.html"
    state.register(url, "seed-scope")
    scope = state._scope(url)
    scope["started"] = time.monotonic() - config.DETAIL_SCAN_TIME_BUDGET_SEC - 1
    assert state.allowed(url, config), "listing dwell may not spend the detail budget"
    scope["busy"] = float(config.DETAIL_SCAN_TIME_BUDGET_SEC)
    assert not state.allowed(url, config)
    assert "shared detail time budget exhausted" in scope["stops"]


def test_counters_that_belong_to_no_seed_are_declared():
    state = DetailRequestState()
    seed = "https://fixtures.invalid/lavora-con-noi"
    state.register("https://fixtures.invalid/jobs/1", seed)
    state.count("https://fixtures.invalid/jobs/1", "blocked")
    state.count("https://fixtures.invalid/assets/challenge.js", "blocked")
    # The seed's own line says the host bucket holds responses it does not show...
    line = state.summary(seed)
    assert "fixtures.invalid also holds 1 response(s) this seed does not show" in line
    assert "host-keyed fallback" not in line
    # ...and the host bucket's line says those are not any seed's postings.
    assert "host-keyed fallback" in state.summary("fixtures.invalid")
    # busy is only ever billed by a reserve()/observe() pair - a cache reuse that never
    # reserved must not look like fetch time on the log line.
    assert state.reserve("https://fixtures.invalid/jobs/1", ProductionScannerConfig())
    state.observe("https://fixtures.invalid/jobs/1", "x" * 400)
    assert "detail busy=" in state.summary(seed)
    assert "detail busy=" not in state.summary("fixtures.invalid")


# --------------------------------------------------------------------------- #
# Opt-in: an actual Chromium board with the McDonald's Italia link shape.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def local_chromium():
    import os
    if os.environ.get("PACKTOWORKEU_TEST_BROWSER") != "1":
        pytest.skip("opt-in local Chromium regression")
    pw_api = pytest.importorskip("playwright.sync_api")
    with pw_api.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            yield browser
        finally:
            browser.close()


def test_c19_admitted_posting_card_recovers_only_its_observed_place(local_chromium):
    """Local DOM, NOT a live McDonald's verification: one list wrapper, three
    postings, an unrelated privacy link inside one card, and two URL forms the
    legacy lowercase English path selector does not recognise. No site request.
    """
    from packtoworkeu.core.location_country import country_from_location
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner

    italian = "https://fixture.invalid/lavora-con-noi/posizioni-aperte/"
    mchire = "https://mchire.com/Job/crew-member-carpi-8822"
    with local_chromium.new_context() as context:
        page = context.new_page()
        page.set_content("""<!doctype html><title>Posizioni aperte</title>
          <h1>Posizioni aperte</h1><article class='job-card-list'><ul>
          <li class='job-entry'><h3><a data-case='mirandola' href='""" + italian + """mirandola-8820/'>
            Addetto/a Ristorazione Mirandola</a></h3><p>Mirandola (MO)</p>
            <a href='https://fixture.invalid/privacy'>Privacy policy</a></li>
          <li class='job-entry'><h3><a data-case='parma' href='""" + italian + """parma-est-8821/'>
            Addetto/a Ristorazione Parma Est</a></h3><p>Parma Est (PR)</p></li>
          <li class='job-entry'><h3><a data-case='carpi' href='""" + mchire + """'>
            Crew Member Carpi</a></h3><p>Carpi (MO)</p></li>
          </ul></article>""")
        links, meta = admitted_job_links(page, validator=lambda url:
            url.startswith(italian) or url == mchire)
        urls = [item["url"] for item in links]
        assert len(urls) == meta["visible_job_links"] == 3
        scanner = CareerPortalScanner(input_csv="__unused__.csv", skip_preflight=True)
        cases = [
            ("mirandola", "Mirandola (MO)", "Italy"),
            ("parma", "Unknown", ""),  # no country from an unresolved neighbourhood
            ("carpi", "Carpi (MO)", "Italy"),
        ]
        for key, place, country in cases:
            anchor = page.query_selector('a[data-case="%s"]' % key)
            title = anchor.inner_text().strip()
            # The old selector's empty result is the exercised pre-fix defect.
            assert bp._bounded_card_text(anchor) == ""
            text = bp._bounded_admitted_card_text(anchor, urls)
            assert title in text and len(text) <= 4000
            assert bp._observed_card_location(scanner, text, title,
                                              "McDonald's Italia") == place
            assert country_from_location(place) == country
            observed = {'mirandola': 'Mirandola (MO)',
                        'parma': 'Parma Est (PR)', 'carpi': 'Carpi (MO)'}
            assert all(other_place not in text for other, other_place in observed.items()
                       if other != key), 'a neighbour location contaminated this card'
        assert bp._bounded_admitted_card_text(page.query_selector('a[href$="/privacy"]'), urls) == ""
        assert bp._bounded_admitted_card_text(page.query_selector('a[data-case="mirandola"]'), ()) == ""


def test_c19_mixed_paths_do_not_turn_a_listing_wrapper_into_one_card(local_chromium):
    """One /job/ CTA used to make a multi-job Italian article look like a
    single English-path card. The census makes that cross-posting leak visible.
    """
    with local_chromium.new_context() as context:
        page = context.new_page()
        page.set_content("""<article class='job-card-list'><ul>
          <li><a data-case='a' href='https://fixture.invalid/posizioni-aperte/crew-1/'>
            Addetto/a Ristorazione Carpi</a><p>Carpi (MO)</p></li>
          <li><a data-case='b' href='https://fixture.invalid/posizioni-aperte/crew-2/'>
            Addetto/a Ristorazione Mirandola</a><p>Mirandola (MO)</p></li>
          </ul><a href='https://fixture.invalid/job/help'>Job help</a></article>""")
        links, _ = admitted_job_links(page, validator=lambda url: '/posizioni-aperte/' in url)
        assert len(links) == 2
        anchor = page.query_selector('a[data-case="a"]')
        # Proof of the pre-fix mixed-path failure, then the new fail-closed guard.
        assert 'Mirandola (MO)' in bp._bounded_card_text(anchor)
        urls = [x['url'] for x in links]
        assert bp._bounded_card_text(anchor, urls) == ""
        text = bp._bounded_admitted_card_text(anchor, urls)
        assert 'Carpi (MO)' in text and 'Mirandola (MO)' not in text


def test_c19_entire_card_link_keeps_its_own_place(local_chromium):
    """An admitted anchor can itself wrap the title and place; do not demand
    a separate li/article when its only parent also contains other jobs.
    """
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    with local_chromium.new_context() as context:
        page = context.new_page()
        page.set_content("""<article class='job-card-list'>
          <a data-case='a' href='https://fixture.invalid/posizioni-aperte/crew-1/'>
            <h3>Addetto/a Ristorazione Carpi</h3><p>Carpi (MO)</p></a>
          <a data-case='b' href='https://fixture.invalid/posizioni-aperte/crew-2/'>
            <h3>Addetto/a Ristorazione Mirandola</h3><p>Mirandola (MO)</p></a>
          </article>""")
        links, _ = admitted_job_links(page, validator=lambda url: '/posizioni-aperte/' in url)
        assert len(links) == 2
        scanner = CareerPortalScanner(input_csv="__unused__.csv", skip_preflight=True)
        for key, expected, other in (('a', 'Carpi (MO)', 'Mirandola (MO)'),
                                      ('b', 'Mirandola (MO)', 'Carpi (MO)')):
            anchor = page.query_selector('a[data-case="%s"]' % key)
            text = bp._bounded_admitted_card_text(anchor, [x['url'] for x in links])
            assert expected in text and other not in text
            assert bp._observed_card_location(scanner, text, anchor.inner_text().strip(),
                                              "McDonald's Italia") == expected


def test_c19_unseparated_postings_cannot_borrow_a_neighbour_location(local_chromium):
    """No per-card boundary: fail closed rather than inherit a sibling's city."""
    with local_chromium.new_context() as context:
        page = context.new_page()
        page.set_content("""<article class='job-card-list'>
          <a data-case='a' href='https://fixture.invalid/posizioni-aperte/crew-1/'>
            Addetto/a Ristorazione Mirandola</a><p>Carpi (MO)</p>
          <a data-case='b' href='https://fixture.invalid/posizioni-aperte/crew-2/'>
            Crew Member Carpi</a><p>Mirandola (MO)</p></article>""")
        links, _ = admitted_job_links(page, validator=lambda url: '/posizioni-aperte/' in url)
        assert len(links) == 2
        for key in ('a', 'b'):
            anchor = page.query_selector('a[data-case="%s"]' % key)
            assert bp._bounded_admitted_card_text(anchor, [x['url'] for x in links]) == ""


@pytest.mark.parametrize("wrapper", [False, True])
def test_assisted_harvest_accepts_links_the_path_list_misses(local_chromium, tmp_path, monkeypatch, wrapper):
    """A board whose postings live under /posizioni-aperte/ must not come back as a wall.

    `wrapper=False` is the bare list (no container matches, so the anchor sweep decides);
    `wrapper=True` is the shape McDonald's Italia actually served on 2026-10-29: the whole
    list inside one element the container selector matches, so `cards=1`, Strategy 1 took one
    anchor and the sweep was skipped as an `else` branch - `accepted=0` against 164 visible
    links. With the census disabled afterwards, the same fixture reproduces that failure.
    """
    import contextlib
    import http.server
    import threading
    from collections import Counter
    from types import SimpleNamespace
    from urllib.parse import urlparse

    from packtoworkeu import paths
    from packtoworkeu.db import database
    import playwright.sync_api as pw_api

    body_jd = ("Responsibilities: Serve guests, follow the kitchen routine and keep the "
               "dining area ready for service. Requirements: availability on weekends and "
               "the first days of the month. " * 6)
    requests_seen = Counter()

    class Board(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, document):
            body = ("<!doctype html><html><head><meta charset='utf-8'></head><body>"
                    + document + "</body></html>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            requests_seen[path] += 1
            host = self.headers["Host"]
            if path.startswith(LISTING_PATH):
                payload = {"@type": "JobPosting", "url": "http://" + host + path,
                           "title": "Addetto/a Ristorazione - Crew - Roma Appia",
                           "description": body_jd,
                           "jobLocation": {"address": {"addressCountry": "IT"}}}
                self._send("<title>Addetto/a Ristorazione | Fixture Italia</title><h1>Addetto/a "
                           "Ristorazione - Crew - Roma Appia</h1><script type=\"application/ld+json\">"
                           + json.dumps(payload) + "</script>")
                return
            cards = "".join("<li><a href='%s'>%s</a><p>Italia</p></li>"
                            % (path, text) for text, path in ITALIAN_POSTINGS)
            # `wrapper` reproduces production: ONE element matching `article` holding the
            # entire list, which is what made `cards=1` and let the harvest stop at zero.
            if wrapper:
                cards = "<article class='job-card-list'>" + cards + "</article>" 
            nav = ("<nav class='pagination'><ul><li class='active'><a href='?page=1'>1</a></li>"
                   "<li><a href='?page=2'>2</a></li></ul></nav>")
            if path.endswith("page=2") or self.path.endswith("page=2"):
                # Same twelve links again: every one is already known, so paging ends.
                self._send("<title>Posizioni aperte</title><ul class='listing'>" + cards + "</ul>")
                return
            self._send("<title>Offerte di lavoro | Fixture Italia</title><h2>Posizioni aperte</h2>"
                       + (cards if wrapper else "<ul class='listing'>" + cards + "</ul>") + nav)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Board)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        pages, waits = [], []
        original_context = local_chromium.new_context

        class BrowserProxy:
            def new_context(self, **kwargs):
                ctx = original_context(**kwargs)
                original_page = ctx.new_page

                def new_page():
                    page = original_page()
                    original_wait = page.wait_for_timeout
                    page.wait_for_timeout = lambda ms: (waits.append(ms), original_wait(min(ms, 5)))
                    pages.append(page)
                    return page
                ctx.new_page = new_page
                return ctx

            def close(self):
                pass  # the fixture owns the real browser lifecycle

        proxy = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: BrowserProxy()))
        monkeypatch.setattr(pw_api, "sync_playwright", lambda: contextlib.nullcontext(proxy))
        real_time = time
        monkeypatch.setattr(bp, "time", SimpleNamespace(
            monotonic=real_time.monotonic, sleep=lambda s: real_time.sleep(min(s, 0.002))))
        from packtoworkeu.core import session_details
        monkeypatch.setattr(session_details, "time", SimpleNamespace(
            monotonic=real_time.monotonic, sleep=lambda s: real_time.sleep(min(s, 0.002))))
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        monkeypatch.setattr(paths, "ensure_scan_output_dir", lambda: outputs)
        db_file = str(tmp_path / "italian.db")
        database.initialize(db_file)
        root = "http://127.0.0.1:%d" % server.server_port

        with database.get_connection(db_file) as conn:
            count = bp.run_assisted_ingestion(root + "/lavora-con-noi.html", "Fixture Italia", conn,
                                              db_path=db_file, clear_wait_s=2.0,
                                              progress_callback=lambda message: None)
            assert count >= len(ITALIAN_POSTINGS), count
            stored = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            assert stored >= len(ITALIAN_POSTINGS), stored
            # C19: this is the actual ingestion/persistence route, not only a
            # helper-level DOM assertion; it must retain this card's observed
            # place even in the one-article/many-job wrapper layout.
            observed = conn.execute("SELECT location, country FROM jobs WHERE url LIKE ?",
                                    (root + LISTING_PATH + "%",)).fetchall()
            assert len(observed) >= len(ITALIAN_POSTINGS)
            assert all(location == "Italia" and country == "Italy"
                       for location, country in observed), [tuple(row) for row in observed]
            census = bp.LAST_INGESTION_REPORT.get("harvest_census") or {}
            pages = bp.LAST_INGESTION_REPORT.get("harvest_census_pages") or []
            assert census.get("cards_seen") == (1 if wrapper else 0), (
                "the wrapped shape must exercise the card strategy, the bare list the sweep")
            assert census.get("visible_posting_links") >= len(ITALIAN_POSTINGS)
            assert census.get("accepted") >= len(ITALIAN_POSTINGS)
            assert "harvest census:" in census.get("note", "")
            # page 2 is a wall of duplicates; it must not be the record that headlines
            assert [rec["page"] for rec in pages][:2] == [1, 2]
            assert pages[0]["accepted"] >= len(ITALIAN_POSTINGS)
            assert pages[-1]["rejected"].get("duplicate") >= len(ITALIAN_POSTINGS)
            assert census["page"] == 1

        # Negative control: without the census the harvest is back to the 0-jobs wall story.
        monkeypatch.setattr(bp, "admitted_job_links", lambda *a, **k: ([], {}))
        with database.get_connection(db_file) as conn:
            bp.LAST_INGESTION_REPORT.clear()
            blocked_count = bp.run_assisted_ingestion(root + "/lavora-con-noi.html",
                                                       "Fixture Italia (no census)", conn,
                                                       db_path=db_file, clear_wait_s=2.0,
                                                       progress_callback=lambda message: None)
        # Without the census there is nothing to override the path list with: the bare list
        # takes 0 (the exact 2026-10-29 result) and the wrapped one at most the single anchor
        # its one "card" offered. Either way the harvest is a fraction of what rendered.
        assert blocked_count <= (1 if wrapper else 0)
        # The census really was switched off: whatever the run then decided, it can only have
        # said "the page rendered no postings", which is the sentence that hid the defect.
        report = bp.LAST_INGESTION_REPORT
        evidence = str(report.get("stop_reason", "")) + str(
            (report.get("harvest_census") or {}).get("note", ""))
        assert "visible_posting_links=0" in evidence, evidence
    finally:
        server.shutdown()


def test_strategy_one_drops_are_counted_not_silent():
    # C14's second half: a drop inside the card loop has to reach the tally, or the note keeps
    # printing `accepted=0` with no reason behind it.
    import inspect
    from packtoworkeu.core import blocked_portals as _bp
    src = open(inspect.getsourcefile(_bp), encoding="utf-8").read()
    region = src.split("if cards:", 1)[1].split("# Issue 005 P6 (C14):", 1)[0]
    assert region.count("continue") == region.count("_rej["), (
        "an uncounted `continue` is back in the card loop: %d continues, %d tallies"
        % (region.count("continue"), region.count("_rej[")))
    assert "no_detail_anchor" in region and "not_a_posting" in region


def test_host_breaker_defers_instead_of_blaming_the_portal():
    # Issue 005 P7 / C15.
    import threading
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    scanner = CareerPortalScanner(input_csv="__unused__.csv", skip_preflight=True)
    scanner._detail_host_lock = threading.Lock()
    scanner._detail_blocked_hosts = {"euamcareers.minorhotels.com"}
    scope = "https://euamcareers.minorhotels.com/en/job-search"
    scanner._tls.detail_scope = scope
    url = "https://euamcareers.minorhotels.com/job/Merida-Mesero-NH-Collection/1375872557"
    assert scanner._fetch_jd_text(url) == ("", "", "")
    state = scanner._detail_state
    assert state.count_of(scope, "host_breaker_deferred") == 1
    assert state.count_of(scope, "blocked") == 0, "a deferred row must not read as a block"
    line = state.summary(scope)
    assert "host_breaker_deferred=1" in line and "blocked=0" in line
    assert state.count_of("no-such-scope", "host_breaker_deferred") == 0


def test_busy_charges_the_union_of_concurrent_reads(monkeypatch):
    # Issue 005 P8 / C16: up to 3 detail tabs run in parallel, so summing per-URL spans billed
    # more time than had passed (NH Hotel Group Italy: `detail busy=3s elapsed=2s`).
    from types import SimpleNamespace
    from packtoworkeu.scanning import detail_state as ds
    clock = [0.0]
    monkeypatch.setattr(ds, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: None))
    config = ProductionScannerConfig()
    state = ds.DetailRequestState()
    first, second = "https://f.invalid/jobs/1-a.html", "https://f.invalid/jobs/2-b.html"
    state.register(first, "s")
    state.register(second, "s")
    scope = state._scope(first)
    assert state.reserve(first, config)
    clock[0] = 1.0
    assert state.reserve(second, config)
    clock[0] = 5.0
    state.observe(first, "x" * 400)
    assert scope["busy"] == 0.0, "a read is still in flight, so the span is not closed"
    clock[0] = 6.0
    state.observe(second, "x" * 400)
    assert scope["busy"] == 6.0, "the union of 0-6 and 1-6 is 6s; the old sum charged 10s"
    assert scope["inflight"] == 0 and scope["span"] is None
    state.observe("https://f.invalid/jobs/3-c.html", "y" * 400)
    assert scope["busy"] == 6.0, "an unreserved outcome must not open or close a span"


def test_cooldown_wait_checks_stop_between_half_second_browser_slices():
    # P10: a click on Stop during a 90s cool-down must not wait for 90s/reload.
    from threading import Event
    event = Event()

    class Page:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, ms):
            self.waits.append(ms)
            if len(self.waits) == 2:
                event.set()

    page = Page()
    assert bp._interruptible_page_wait(page, 90.0, event) is False
    assert page.waits == [500, 500], 'a cancelled 90s step must stop after <= 0.5s'
    assert bp._interruptible_page_wait(page, 3.0, event) is False
    assert page.waits == [500, 500]
    event.clear()
    class NeverCancelled:
        def __init__(self):
            self.waits = []
        def wait_for_timeout(self, ms):
            self.waits.append(ms)
    normal = NeverCancelled()
    assert bp._interruptible_page_wait(normal, 1.201, event) is True
    assert normal.waits == [500, 500, 201], 'polite total dwell is not shortened'


def test_identical_synthetic_probe_short_stop_requires_positive_no_next_evidence():
    # P11: an absence of pager evidence is *not* evidence that an untried URL is empty.
    evidence = {'pagination_text': '', 'forward_page_links': [], 'stated_total': None}
    values = dict(synthetic=True, replayed=True, two_index_guesses=True,
                  census_complete=True, forward_control=False,
                  last_page_evidence=evidence, current_total=None, ingested=164)
    assert bp._unadvertised_probe_replayed(**values)
    for change in (
        dict(synthetic=False), dict(replayed=False), dict(two_index_guesses=False),
        dict(census_complete=False), dict(unmet_total_seen=True),
        dict(evidence_readable=False), dict(probe_controls_absent=False),
        dict(forward_control=None), dict(forward_control=True),
        dict(last_page_evidence=None),
        dict(last_page_evidence={**evidence, 'pagination_text': '1 2 Next'}),
        dict(last_page_evidence={**evidence, 'forward_page_links': ['?page=2']}),
        dict(last_page_evidence={**evidence, 'stated_total': 200}),
        dict(current_total=200),
    ):
        assert not bp._unadvertised_probe_replayed(**(values | change)), change


def test_forward_control_check_is_conservative_on_dom_errors():
    class Page:
        url = 'https://fixture.invalid/jobs'
        def __init__(self, target=False, hrefs=(), fail=False):
            self.target, self.hrefs, self.fail = target, hrefs, fail
        def evaluate(self, *args):
            if self.fail:
                raise RuntimeError('DOM was closed')
            return self.target
        def eval_on_selector_all(self, *args):
            return self.hrefs
        def query_selector_all(self, *args):
            return []
    assert bp._advertised_forward_page(Page(), 2) is False
    assert bp._advertised_forward_page(Page(target=True), 2) is True
    assert bp._advertised_forward_page(Page(hrefs=[{'h': '?page=2'}]), 2) is True
    assert bp._advertised_forward_page(Page(fail=True), 2) is None


@pytest.mark.parametrize('board', ('unadvertised_repeat', 'unadvertised_new',
                                   'unadvertised_census_gap', 'unadvertised_pager_on_probe',
                                   'advertised_cancel', 'advertised_cancel_dwell'))
def test_pagination_probe_preserves_new_jobs_and_stop_remains_usable(
        local_chromium, tmp_path, monkeypatch, board):
    """Opt-in local Chromium: ?p=1 and ?p=2 resolve index ambiguity safely.

    The second variant returns genuinely new posting URLs despite no visible pager,
    while the first three-anchor signature is unchanged. A failed signature wait
    cannot reject those new jobs. Probe-side census gaps or pagination text
    must also retain the old retry policy; the last two variants have a real
    pager and cancel during F-38's cool-down or F-37d's same-href dwell.
    """
    import contextlib
    import http.server
    import threading
    from collections import Counter
    from types import SimpleNamespace
    from urllib.parse import urlparse, parse_qs
    from packtoworkeu import paths
    from packtoworkeu.db import database
    import playwright.sync_api as pw_api

    cancel = threading.Event()
    requests_seen = Counter()
    reloads = []
    first = [(f'Fixture restaurant position {i}', LISTING_PATH + f'fixture-role-{i}/')
             for i in range(1, 7)]
    extra = [(f'Fixture restaurant position {i}', LISTING_PATH + f'fixture-role-{i}/')
             for i in (7, 8)]
    listing = '/lavora-con-noi/posizioni-aperte'
    job_body = 'Responsibilities include assisting guests in our restaurant. ' * 12

    class Board(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, html):
            data = ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
                    + html + '</body></html>').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            requested = urlparse(self.path)
            requests_seen[self.path] += 1
            if requested.path.startswith(LISTING_PATH):
                self.respond('<title>Fixture job</title><h1>Fixture job</h1><main>'
                             + job_body + '</main>')
                return
            if requested.path != listing:
                self.respond('<title>Missing</title><h1>Missing</h1>')
                return
            page_number = parse_qs(requested.query).get('p', [''])[0]
            page_number = page_number or parse_qs(requested.query).get('page', [''])[0]
            jobs = first + (extra if board == 'unadvertised_new' and page_number == '2' else [])
            cards = ''.join(f'<li><a href="{path}">{title}</a></li>' for title, path in jobs)
            pager = ("<nav class='pagination'><ul><li class='active'>1</li>"
                     "<li><a href='?page=2'>2</a></li></ul></nav>"
                     if board.startswith('advertised_cancel') and not page_number else '')
            if board == 'unadvertised_pager_on_probe' and page_number:
                pager = "<nav class='pagination'><span>Additional pages</span></nav>"
            self.respond('<title>Fixture positions</title><h2>Open positions</h2>'
                         '<article class="job-card-list"><ul>' + cards + '</ul></article>' + pager)

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Board)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        original_context = local_chromium.new_context

        class BrowserProxy:
            def new_context(self, **kw):
                ctx = original_context(**kw)
                real_new_page = ctx.new_page
                def new_page():
                    page = real_new_page()
                    original_wait = page.wait_for_timeout
                    original_reload = page.reload
                    def wait(ms):
                        if getattr(page, '_stop_on_next_slice', False):
                            page._stop_on_next_slice = False
                            cancel.set()
                        return original_wait(min(ms, 5))
                    def reload(**kw):
                        reloads.append(page.url)
                        return original_reload(**kw)
                    page.wait_for_timeout = wait
                    page.reload = reload
                    return page
                ctx.new_page = new_page
                return ctx
            def close(self):
                pass  # owned by the local_chromium fixture

        proxy = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: BrowserProxy()))
        monkeypatch.setattr(pw_api, 'sync_playwright', lambda: contextlib.nullcontext(proxy))
        real_time = time
        short_clock = SimpleNamespace(monotonic=real_time.monotonic,
                                      sleep=lambda s: real_time.sleep(min(s, 0.002)))
        monkeypatch.setattr(bp, 'time', short_clock)
        from packtoworkeu.core import session_details
        monkeypatch.setattr(session_details, 'time', short_clock)
        # The signature wait is deliberately pessimistic. Pagination acceptance
        # must depend on new validated URLs, not this first-three-anchor signal.
        monkeypatch.setattr(bp, '_wait_for_page_advance', lambda *args, **kw: False)
        real_wait = bp._interruptible_page_wait
        def cooldown_stop(page, seconds, event):
            if ((board in ('advertised_cancel', 'unadvertised_census_gap',
                           'unadvertised_pager_on_probe') and seconds == 15.0)
                    or (board == 'advertised_cancel_dwell' and seconds == 3.0)):
                page._stop_on_next_slice = True
            return real_wait(page, seconds, event)
        monkeypatch.setattr(bp, '_interruptible_page_wait', cooldown_stop)
        if board == 'unadvertised_census_gap':
            # A hypothetical visible link that the harvest failed to account for
            # on the *first* guess must keep the second guess from fast-stopping.
            original_census = bp.admitted_job_links
            def census_with_gap(page, **kw):
                links, meta = original_census(page, **kw)
                if page.url.endswith('?p=1'):
                    root = page.url.split('/lavora-con-noi/', 1)[0]
                    links.append({'url': root + LISTING_PATH + 'missed-role/',
                                  'title': 'Missed fixture role'})
                    meta['visible_job_links'] = len(links)
                return links, meta
            monkeypatch.setattr(bp, 'admitted_job_links', census_with_gap)

        output = tmp_path / 'output'
        output.mkdir()
        monkeypatch.setattr(paths, 'SCAN_OUTPUT_DIR', output)
        monkeypatch.setattr(paths, 'ensure_scan_output_dir', lambda: output)
        db_file = str(tmp_path / 'fixture.db')
        database.initialize(db_file)
        url = 'http://127.0.0.1:%d%s' % (server.server_port, listing)
        with database.get_connection(db_file) as conn:
            count = bp.run_assisted_ingestion(
                url, 'Fixture Restaurant', conn, db_path=db_file,
                clear_wait_s=2.0, cancel_event=cancel,
                progress_callback=lambda message: None)
            titles = {row[0] for row in conn.execute('SELECT title FROM jobs')}
        report = bp.LAST_INGESTION_REPORT.copy()
        if board == 'unadvertised_repeat':
            assert count == 6 and len(titles) == 6
            assert requests_seen[listing + '?p=1'] == 1, requests_seen
            assert requests_seen[listing + '?p=2'] == 1, requests_seen
            assert not reloads, 'two replayed guesses are not a rate-limit signal'
            assert report['pagination_end_evidence']['kind'] == 'unadvertised_synthetic_replay'
            assert report['pagination_end_evidence']['synthetic_replays'] == 2
            assert 'no further pages demonstrated' in report['stop_reason']
            assert report['pages_visited'] == 1 and not report['completed']
        elif board == 'unadvertised_new':
            assert count == 8 and len(titles) == 8, (count, titles)
            assert 'Fixture restaurant position 7' in titles
            assert requests_seen[listing + '?p=1'] == 1, requests_seen
            assert requests_seen[listing + '?p=2'] == 1, requests_seen
            assert 'pagination_end_evidence' not in report
            assert not report['completed'], 'missing pager is not proof of full coverage'
        else:
            assert count == 6 and len(titles) == 6
            assert cancel.is_set() and report['stop_reason'] == 'cancelled by user'
            assert report['cancelled'] and not report['completed']
            assert not reloads, 'Stop during retry/cool-down must preempt page.reload'
            assert 'pagination_end_evidence' not in report, 'an unsafe guess must keep F-38'
            if board.startswith('unadvertised_'):
                assert requests_seen[listing + '?p=1'] == 1, requests_seen
                assert requests_seen[listing + '?p=2'] >= 1, requests_seen
            elif board == 'advertised_cancel_dwell':
                assert requests_seen[listing + '?page=2'] >= 2, requests_seen
            else:
                assert requests_seen[listing + '?page=2'] >= 3, requests_seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
