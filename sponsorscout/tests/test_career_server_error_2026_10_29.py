"""Issue 005 C13 E1: a served temporary error is not an empty job board.

Only local strings and a local HTTP/Chromium fixture are used. This does not
identify the actual response that Unieuro gave the earlier headless scan and
does not approve P9, loosen URL admission or bypass any verification wall.
"""
from __future__ import annotations

import csv
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sponsorscout.scanning.career import career_scanner as career
from sponsorscout.scanning.common import completion_status

ERROR_PAGE = ("<!doctype html><title>Error: Server Error</title><body>"
              "<h1>Error: Server Error</h1>"
              "<p>The server encountered a temporary error and could not "
              "complete your request.</p>"
              "<p>Please try again in 30 seconds.</p></body>")


class _FakePage:
    def __init__(self, status, body="", fail=False):
        self._last_nav_status, self.body, self.fail = status, body, fail

    def evaluate(self, _script):
        if self.fail:
            raise RuntimeError("page closed")
        return self.body


@pytest.mark.parametrize("status,body,expected", [
    (500, "", "HTTP 500 server error"),
    (502, "", "HTTP 502 server error"),
    (503, "", "HTTP 503 server error"),
    (200, "Error: Server Error\nThe server encountered a temporary error and "
          "could not complete your request.\nPlease try again in 30 seconds.",
     "temporary server error page"),
    (0, "Error: Server Error\nThe server encountered a temporary error and "
        "could not complete your request.", "temporary server error page"),
    (200, "Careers\nNo open positions at this time.", ""),
    (403, "Error: Server Error\nThe server encountered a temporary error and "
          "could not complete your request.", ""),
    (429, "Error: Server Error\nThe server encountered a temporary error and "
          "could not complete your request.", ""),
    (200, "Error: Server Error\nTemporary outage, please retry later.", ""),
    (200, "Open jobs\nError: Server Error\nThe server encountered a temporary error "
          "and could not complete your request.\n" + "job description text " * 60, ""),
])
def test_e1_bounded_server_error_requires_served_failure_or_exact_template(status, body, expected):
    got = career.CareerPortalScanner._served_listing_error(_FakePage(status, body))
    assert got.startswith(expected) if expected else got == ""
    if got:
        assert "blocked by bot protection" not in got.lower()
        assert completion_status(0, got, ()) == "error"


def test_e1_page_inspection_failure_never_invents_an_error():
    assert career.CareerPortalScanner._served_listing_error(_FakePage(200, fail=True)) == ""


@pytest.mark.parametrize("mode,expect", [
    ("http500", "error"),
    ("soft200", "error"),
    ("http503", "error"),
    ("empty200", "empty"),
    ("listing200", "partial"),  # fixture has no JD; existing coverage policy stays
    ("listing500", "partial"),  # an actual visible job must not be thrown away
    ("wall403", "error"),
])
def test_e1_local_browser_run_writes_truthful_scan_log(tmp_path, monkeypatch, mode, expect):
    if os.environ.get("SPONSORSCOUT_TEST_BROWSER") != "1":
        pytest.skip("opt-in local Chromium fixture; no public portal requests")
    pytest.importorskip("playwright.sync_api")

    class Board(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if mode == "http500":
                status, html = 500, "<title>Internal Server Error</title><body>Server unavailable</body>"
            elif mode == "http503":
                status, html = 503, "<title>Service unavailable</title><body>Service unavailable</body>"
            elif mode == "soft200":
                status, html = 200, ERROR_PAGE
            elif mode == "empty200":
                status, html = 200, "<title>Careers</title><body><h1>Open positions</h1>No open positions at this time.</body>"
            elif mode == "wall403":
                status, html = 403, ("<title>Forbidden</title><body><h1>Access denied</h1>"
                                      "Please verify you are human.</body>")
            else:
                status, html = (500 if mode == "listing500" else 200), ("<title>Careers</title><body><h1>Open positions</h1>"
                                     "<article><a href='/jobs/backend-engineer-123456'>"
                                     "Backend Engineer</a><p>Berlin, Germany</p></article></body>")
            raw = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Board)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        seed = tmp_path / "seeds.csv"
        seed.write_text(
            "name,careers_url,provider,source_type,target_country,scope_policy,enabled\n"
            f"Fixture Board,http://127.0.0.1:{server.server_port}/careers,auto,"
            "direct_employer,Global,global,true\n", encoding="utf-8-sig")
        dest = tmp_path / "jobs.csv"
        scanner = career.CareerPortalScanner(input_csv=str(seed), output_csv=str(dest),
                                              max_workers=1, detail_scan=False,
                                              skip_preflight=True)
        # All six paths must reach the same local Chromium fallback. The real
        # static path is intentionally not under test, and is not relaxed.
        monkeypatch.setattr(career, "fetch_static_jobs",
                            lambda *_args, **_kw: ([], "fixture: browser-only"))
        scanner.execute_crawler()
        with (tmp_path / "jobs_scan_log.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["Status"] == expect, row
        with (tmp_path / "jobs_errors.csv").open(encoding="utf-8-sig", newline="") as f:
            errors = list(csv.DictReader(f))
        if mode in ("http500", "soft200", "http503"):
            assert row["Jobs Found"] == "0" and row["Error"], row
            assert errors, "a served failure must appear in errors.csv"
            assert "bot protection" not in row["Error"].lower(), row
            if mode == "soft200":
                assert "temporary server error page" in row["Error"], row
            if mode == "http500":
                assert "HTTP 500" in row["Error"], row
        elif mode == "empty200":
            assert row["Jobs Found"] == "0" and row["Error"] == "" and not errors, row
        elif mode == "wall403":
            assert row["Jobs Found"] == "0" and errors, row
            assert "temporary server error page" not in row["Error"], row
        else:
            assert int(row["Jobs Found"]) >= 1 and row["Error"] == "" and not errors, row
            assert "JD coverage incomplete:" in row["Diagnostics"], row
            assert "listing server failure:" not in row["Diagnostics"], row
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
