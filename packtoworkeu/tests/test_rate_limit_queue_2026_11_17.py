"""Fix J (2026-11-17): HTTP 429 rate limiting is not a bot wall.

Run 20261117T180324 (AERQ/Workday) saved 1,994 jobs from a fully
readable listing, then 41 detail reads were throttled (HTTP 429).
The summary built from those detail outcomes queued the whole portal
to Assisted as "blocked access", although every reason explicitly
said "human verification not confirmed" -- i.e. no wall was seen.
Bare 429/403 detail refusals are now a deferred-coverage note; only
confirmed verification walls queue a portal with saved jobs.
"""
import csv

from packtoworkeu.core.blocked_portals import (
    _detail_summary_is_wall, blocked_portals_from_errors_csv)
from packtoworkeu.scanning.response_evidence import (
    challenge_reason, is_confirmed_wall)

BARE_429 = ("blocked access: 41 unresolved response(s); "
            "HTTP 429 access blocked (human verification not confirmed) "
            "at https://ag.wd3.myworkdayjobs.com/Airbus/job/Ulm/X_1")
WALL = ("blocked access: 2 unresolved response(s); "
        "Cloudflare (interactive action requested) challenge page served instead of the job detail "
        "text; no job description could be read at https://x.invalid/j/1")
LEGACY = "blocked access: 2 unresolved response(s)"


def test_bare_429_reason_is_not_a_confirmed_wall():
    reason = challenge_reason("<title>Too Many Requests</title>", status=429)
    assert is_confirmed_wall(reason) is False


def test_detail_summary_wall_classification():
    assert _detail_summary_is_wall(BARE_429) is False
    assert _detail_summary_is_wall(WALL) is True
    assert _detail_summary_is_wall(BARE_429 + "; " + WALL.split("; ", 1)[1]) is True
    # No recorded reason means no evidence for assisted admission.
    assert _detail_summary_is_wall(LEGACY) is False


def _write_log(tmp_path, error, jobs="1994"):
    path = tmp_path / "scan_log.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "Seed Name", "Seed URL", "Jobs Found", "Error", "Diagnostics"])
        w.writeheader()
        w.writerow({"Seed Name": "AERQ GmbH",
                    "Seed URL": "https://ag.wd3.myworkdayjobs.com/Airbus",
                    "Jobs Found": jobs, "Error": error, "Diagnostics": error})
    return str(path)


def test_rate_limited_portal_with_saved_jobs_is_not_queued(tmp_path):
    out = blocked_portals_from_errors_csv(
        _write_log(tmp_path, BARE_429), phase="career",
        include_detail_issues=True)
    assert out == []


def test_confirmed_wall_still_queues_as_details(tmp_path):
    out = blocked_portals_from_errors_csv(
        _write_log(tmp_path, WALL), phase="career",
        include_detail_issues=True)
    assert len(out) == 1 and out[0]["access_scope"] == "details"


def test_legacy_count_alone_does_not_queue(tmp_path):
    out = blocked_portals_from_errors_csv(
        _write_log(tmp_path, LEGACY), phase="career",
        include_detail_issues=True)
    assert out == []
