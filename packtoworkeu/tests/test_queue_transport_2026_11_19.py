"""Bundles Q/T/F/W (2026-11-19), from run 20261119T114259.

Q: queue honesty -- no bot/access entry without a confirmed wall, and a
single walled detail does not queue a whole portal.
T: an HTTP-walled host grants the browser 3 bounded probes.
F: the assisted browser launches minimized (background).
W: a Workday CXS 429 honours Retry-After before the board is given up.
"""
import ast
import csv
import inspect
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packtoworkeu.core.blocked_portals import blocked_portals_from_errors_csv


def _scanner(tmp_path):
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    seed = tmp_path / "seed.csv"
    seed.write_text("company,url\nX,https://example.com\n", encoding="utf-8")
    return CareerPortalScanner(input_csv=str(seed), skip_preflight=True)


def _scan_log(tmp_path, rows):
    path = tmp_path / "scan_log.csv"
    cols = ["Seed Name", "Seed URL", "Jobs Found", "Error", "Diagnostics"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return str(path)


# ---------- Bundle Q ----------

def test_q_rate_limited_api_is_not_a_bot_queue_entry(tmp_path):
    # 3M pattern: the only "access" text is the not-confirmed 429 note.
    path = _scan_log(tmp_path, [{
        "Seed Name": "3M", "Seed URL": "https://3m.wd1.myworkdayjobs.com/de-DE/Search",
        "Jobs Found": "0", "Error": "",
        "Diagnostics": "workday API failed: HTTPError: HTTP Error 429 | "
                       "descriptions deferred: 1 detail response(s) refused without "
                       "verification evidence (rate limiting, e.g. HTTP 429 access "
                       "blocked (human verification not confirmed) at "
                       "https://3m.wd1.myworkdayjobs.com/wday/cxs/3m/Search/jobs) | "
                       "dead end: no job cards after 72.4s"}])
    assert blocked_portals_from_errors_csv(path, phase="career",
                                           include_detail_issues=True) == []


def test_q_zero_yield_confirmed_wall_still_queues(tmp_path):
    path = _scan_log(tmp_path, [{
        "Seed Name": "Walled", "Seed URL": "https://walled.example/jobs",
        "Jobs Found": "0",
        "Error": "RuntimeError: listing access blocked: verification unresolved; "
                 "Cloudflare (interactive action requested) challenge page served instead of the job list",
        "Diagnostics": ""}])
    rows = blocked_portals_from_errors_csv(path, phase="career",
                                           include_detail_issues=True)
    assert len(rows) == 1 and rows[0]["access_scope"] == "listing"


def test_q_single_walled_detail_does_not_queue_portal(tmp_path):
    # Airbus pattern: 1 confirmed-wall detail out of 119 postings.
    path = _scan_log(tmp_path, [{
        "Seed Name": "Airbus", "Seed URL": "https://ag.wd3.myworkdayjobs.com/Airbus",
        "Jobs Found": "119",
        "Error": "blocked access: 1 unresolved response(s); Cloudflare (interactive action requested) challenge "
                 "page served instead of the job detail text at https://x/y",
        "Diagnostics": ""}])
    assert blocked_portals_from_errors_csv(path, phase="career",
                                           include_detail_issues=True) == []


def test_q_material_wall_count_queues_details(tmp_path):
    path = _scan_log(tmp_path, [{
        "Seed Name": "Harnham", "Seed URL": "https://www.harnham.com/jobs",
        "Jobs Found": "1131",
        "Error": "blocked access: 3 unresolved response(s); Cloudflare (interactive action requested) challenge "
                 "page served instead of the job detail text at https://x/y",
        "Diagnostics": ""}])
    rows = blocked_portals_from_errors_csv(path, phase="career",
                                           include_detail_issues=True)
    assert len(rows) == 1 and rows[0]["access_scope"] == "details"


# ---------- Bundle T ----------

WALL = "Cloudflare challenge page served instead of the job detail text"


def test_t_browser_gets_three_probes_on_http_walled_host(tmp_path):
    s = _scanner(tmp_path)
    url = "https://careers.eon.com/job/1"
    for i in range(2):
        s._note_detail_failure(f"https://careers.eon.com/job/h{i}", WALL,
                               blocked=True, transport="http", status=403)
    assert s._detail_host_unavailable(url)                 # HTTP closed
    assert not s._detail_host_unavailable(url, browser=True)  # probes remain
    for i in range(3):
        s._note_detail_failure(f"https://careers.eon.com/job/b{i}", WALL,
                               blocked=True, transport="browser", status=403)
    assert s._detail_host_unavailable(url, browser=True)   # probes spent
    s._note_detail_success(url)                            # browser read lands
    assert not s._detail_host_unavailable(url, browser=True)
    assert s._detail_host_unavailable(url)                 # HTTP wall NOT cleared


# ---------- Bundle F ----------

def test_f_assisted_browser_launches_minimized():
    from packtoworkeu.core import blocked_portals as bp
    tree = ast.parse(inspect.getsource(bp.run_assisted_ingestion))
    launch = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "launch_chromium")
    args = ast.literal_eval(launch.keywords[0].value)
    flat = [a for kw in launch.keywords for a in
            (ast.literal_eval(kw.value) if kw.arg == "args" else [])]
    assert "--start-minimized" in flat
    assert "--start-maximized" not in flat
    assert args is not None  # launch call parsed


# ---------- Bundle W ----------

def test_w_workday_429_honours_retry_after_then_succeeds(tmp_path, monkeypatch):
    import json as _json
    from packtoworkeu.scanning.career import career_scanner as mod
    s = _scanner(tmp_path)
    calls = {"jobs": 0}

    def fake_fetch(url, headers, parse_json=True, post_body=None):
        if url.endswith("/jobs"):
            calls["jobs"] += 1
            if calls["jobs"] <= 2:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests",
                                             {"Retry-After": "0"}, None)
            return _json.dumps({"total": 1, "jobPostings": [{
                "title": "Engineer", "externalPath": "/job/Engineer/1",
                "locationsText": "Munich", "bulletFields": []}]})
        return _json.dumps({"jobPostingInfo": {
            "jobDescription": "<p>Build things.</p>", "location": "Munich"}})

    monkeypatch.setattr(s, "_http_fetch_with_retry", fake_fetch)
    monkeypatch.setattr(mod, "sleep_interruptible", lambda *a, **k: True)
    jobs, note = s._fetch_workday_jobs({
        "board_slug": "fixture.wd1.myworkdayjobs.com|fixture|Search",
        "careers_url": "https://fixture.wd1.myworkdayjobs.com/de-DE/Search"})
    assert calls["jobs"] == 3
    assert len(jobs) == 1 and jobs[0]["job_title"] == "Engineer"
