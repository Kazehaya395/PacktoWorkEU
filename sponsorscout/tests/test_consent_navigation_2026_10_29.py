"""Consent actions must preserve listing navigation; all routes are local fixtures."""
from __future__ import annotations

import pytest

from sponsorscout.scanning.career.career_scanner import CareerPortalScanner


@pytest.fixture(scope="module")
def browser():
    api = pytest.importorskip("playwright.sync_api")
    with api.sync_playwright() as pw:
        try:
            instance = pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        except api.Error as exc:
            pytest.skip(f"Local Chromium unavailable: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def listing(browser):
    context = browser.new_context()
    content = {"main": "", "frame": ""}

    def respond(route):
        url = route.request.url
        body = content["frame"] if "/consent-frame" in url else content["main"]
        if "/policy" in url:
            body = "<h1>Cookie policy</h1>"
        route.fulfill(status=200, content_type="text/html", body=body)

    context.route("**/*", respond)
    page = context.new_page()
    page.set_default_timeout(1500)

    def load(html, frame=""):
        content.update(main=html, frame=frame)
        page.goto("https://fixture.invalid/listing")
        return page

    yield load
    context.close()


@pytest.fixture
def scanner():
    return CareerPortalScanner(input_csv="__unused__.csv", skip_preflight=True)


@pytest.mark.parametrize("label", ["Cookie Settings", "POLITICA SUI COOKIES", "Cookie Policy"])
@pytest.mark.parametrize("deep", [False, True])
def test_successful_dismissal_never_follows_cookie_footer(listing, scanner, label, deep):
    page = listing(f'''<div id="cookie-banner"><button
        onclick="this.parentElement.remove()">Accept</button></div>
        <main><a href="/job/engineer/12345">Software Engineer</a></main>
        <footer><a href="/policy">{label}</a></footer>''')
    assert scanner.dismiss_initial_blockers(page, deep=deep) == 1
    assert page.url == "https://fixture.invalid/listing"
    assert page.locator("#cookie-banner").count() == 0
    assert page.get_by_text("Software Engineer", exact=True).is_visible()


@pytest.mark.parametrize("label", ["OK", "Continue", "Accept", "Cookie Settings", "Accept offer"])
def test_banner_free_page_never_clicks_unrelated_actions(listing, scanner, label):
    page = listing(f'''<main><button id="application" onclick="window.clicked=true">{label}</button>
        <a href="/policy">{label}</a></main>''')
    assert scanner.dismiss_initial_blockers(page, deep=False) == 0
    assert page.url.endswith("/listing")
    assert page.evaluate("window.clicked === undefined")


@pytest.mark.parametrize("label", ["OK", "Continue", " ACCETTA TUTTI ", "Alle akzeptieren", "Accepter tout"])
def test_exact_multilingual_actions_inside_consent_ui(listing, scanner, label):
    page = listing(f'''<aside class="cookie-banner"><button onclick="this.parentElement.remove()">
        {label}</button></aside>''')
    assert scanner.dismiss_initial_blockers(page, deep=False) == 1
    assert page.locator("aside").count() == 0
    assert page.url.endswith("/listing")


def test_known_cmp_button_does_not_require_generic_context(listing, scanner):
    page = listing('''<button id="onetrust-accept-btn-handler"
        onclick="this.remove()">Accept everything</button>''')
    assert scanner.dismiss_initial_blockers(page, deep=False) == 1
    assert page.locator("button").count() == 0


def test_legitimate_anchor_consent_action_and_stale_footer(listing, scanner):
    page = listing('''<div class="cookie-banner"><a href="#" onclick="event.preventDefault();
        this.parentElement.remove()">Accept all</a></div>
        <footer><a href="/policy">OK</a></footer>''')
    assert scanner.dismiss_initial_blockers(page, deep=False) == 1
    assert page.url.endswith("/listing")
    assert page.locator(".cookie-banner").count() == 0


def test_known_cmp_selector_does_not_authorize_policy_navigation(listing, scanner):
    page = listing('''<div id="cookie-banner"><a id="onetrust-accept-btn-handler"
        href="/policy">Accept</a><button onclick="this.parentElement.remove()">Accept</button></div>''')
    assert scanner.dismiss_initial_blockers(page, deep=False) == 1
    assert page.url.endswith("/listing")
    assert page.locator("#cookie-banner").count() == 0


def test_hidden_duplicate_does_not_hide_visible_control(listing, scanner):
    page = listing('''<div class="cookie-banner"><button style="display:none">Accept</button>
        <button onclick="this.parentElement.remove()">Accept</button></div>''')
    assert scanner.dismiss_initial_blockers(page, deep=False) == 1
    assert page.locator(".cookie-banner").count() == 0


def test_main_and_child_consent_contexts_are_handled_separately(listing, scanner):
    page = listing('''<div class="cookie-banner"><button onclick="this.parentElement.remove()">Accept</button></div>
        <iframe src="/consent-frame"></iframe><a href="/policy">Cookie Policy</a>''',
        frame='''<div role="dialog"><p>Cookie consent</p><button
            onclick="this.parentElement.remove()">Continue</button></div>''')
    assert scanner.dismiss_initial_blockers(page, deep=True) == 2
    assert page.url.endswith("/listing")
    assert page.frames[1].locator("button").count() == 0


def test_stops_after_first_action_in_same_scope(listing, scanner):
    page = listing('''<div class="cookie-banner"><button onclick="this.remove()">Accept</button>
        <button onclick="window.staleClicked=true">Continue</button></div>''')
    assert scanner.dismiss_initial_blockers(page, deep=False) == 1
    assert page.evaluate("window.staleClicked === undefined")


def test_consent_label_with_decorative_icon(listing, scanner):
    page = listing('''<div class="cookie-banner"><div role="button" tabindex="0"
        onclick="this.parentElement.remove()"><span>✓</span> Accetta tutti</div></div>''')
    assert scanner.dismiss_initial_blockers(page, deep=False) == 1
    assert page.locator(".cookie-banner").count() == 0
