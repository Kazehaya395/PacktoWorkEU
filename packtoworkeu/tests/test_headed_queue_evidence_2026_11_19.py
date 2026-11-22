"""Bundles H + V (2026-11-19), from run 20261119T135951 (E.ON).

Normal career scans must remain headless across all four launch paths.
V: a queued access issue states the scanner-side evidence in plain
language (the scanner is blocked, not people).
"""
import ast
import csv
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packtoworkeu.core.blocked_portals import blocked_portals_from_errors_csv


# ---------- Bundle H ----------

def test_h_all_career_browser_launches_are_headless():
    from packtoworkeu.scanning.career import career_scanner as mod
    src = inspect.getsource(mod)
    tree = ast.parse(src)
    launches = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "launch_chromium"]
    # Exactly one direct launch remains: inside the helper.
    assert len(launches) == 1
    helper = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_launch_scan_browser")
    body = ast.get_source_segment(src, helper)
    assert "headless=True" in body
    assert "--start-minimized" not in body
    callers = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "_launch_scan_browser"]
    assert len(callers) == 4  # worker, scope, JD browser, enrichment


# ---------- Bundle V ----------

def _scan_log(tmp_path, rows):
    path = tmp_path / "scan_log.csv"
    cols = ["Seed Name", "Seed URL", "Jobs Found", "Error", "Diagnostics"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return str(path)


def test_v_queue_reason_states_scanner_side_evidence(tmp_path):
    path = _scan_log(tmp_path, [{
        "Seed Name": "E.ON", "Seed URL": "https://careers.eon.com/search/",
        "Jobs Found": "25",
        "Error": "blocked access: 6 unresolved response(s); Cloudflare (interactive action requested) challenge "
                 "page served instead of the job detail text at https://x/y",
        "Diagnostics": ""}])
    rows = blocked_portals_from_errors_csv(path, phase="career",
                                           include_detail_issues=True)
    assert len(rows) == 1
    reason = rows[0]["reason"]
    assert reason.startswith("blocked access: 6 unresolved")
    assert "Cloudflare served its bot-verification page to the automated scanner" in reason
    assert "the scanner is blocked, not people" in reason
    assert "assisted browser" in reason


def test_v_non_wall_rows_get_no_evidence_note(tmp_path):
    # Bundle Q still applies first: a not-confirmed 429 never queues,
    # so the note can never decorate a non-wall entry.
    path = _scan_log(tmp_path, [{
        "Seed Name": "3M", "Seed URL": "https://3m.wd1.myworkdayjobs.com/de-DE/Search",
        "Jobs Found": "0", "Error": "",
        "Diagnostics": "HTTP 429 access blocked (human verification not "
                       "confirmed) at https://3m.wd1.myworkdayjobs.com/wday/cxs/x"}])
    assert blocked_portals_from_errors_csv(path, phase="career",
                                           include_detail_issues=True) == []


def test_closed_target_with_static_429_is_not_a_listing_wall(tmp_path):
    path = _scan_log(tmp_path, [{"Seed Name": "Personio", "Seed URL": "https://example.test/jobs",
        "Jobs Found": "0", "Error": "TargetClosedError: Target page, context or browser has been closed",
        "Diagnostics": "static: access blocked (HTTP 429); browser fallback will report what it sees"}])
    assert blocked_portals_from_errors_csv(path, phase="career", include_detail_issues=True) == []


def test_closed_target_is_cancelled_only_with_stop_signal():
    from threading import Event
    from types import SimpleNamespace
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    worker = SimpleNamespace(cancel_event=Event())
    exc = type('TargetClosedError', (Exception,), {})('closed')
    classify = CareerPortalScanner._target_exception_message
    assert classify(worker, exc) == 'TargetClosedError: closed'
    worker.cancel_event.set()
    assert classify(worker, exc).startswith('ScanCancelled:')
    assert classify(worker, ValueError('unrelated')) == 'ValueError: unrelated'
