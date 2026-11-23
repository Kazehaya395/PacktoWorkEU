"""Verification fix bundles (2026-11-19), from run 20261119T012312."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packtoworkeu.scanning.common import completion_status


def _scanner(tmp_path):
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    seed = tmp_path / "seed.csv"
    seed.write_text("company,url\nX,https://example.com\n", encoding="utf-8")
    return CareerPortalScanner(input_csv=str(seed), skip_preflight=True)


# F-S: honest empty verdicts
def test_dead_end_is_unverified_empty_not_empty():
    assert completion_status(0, "", ["dead end: no job cards after 20.0s (skipping scroll/pagination)"]) == "unverified_empty"


def test_verified_empty_stays_empty():
    assert completion_status(0, "", ["verified empty: page states no vacancies ('no jobs')",
                                     "dead end: no job cards after 9.0s"]) == "empty"


# F-T1: index/channel pages are not vacancies (Absolventen pattern)
def test_channel_index_pages_rejected(tmp_path):
    s = _scanner(tmp_path)
    assert not s.is_valid_job_title("Alle Städte", "Absolventen", "https://www.absolventa.de/jobs/stadt")
    assert not s.is_valid_job_title("IT & Development", "Absolventen", "https://www.absolventa.de/jobs/channel/it")
    assert not s.is_valid_job_title("Personalwesen", "Absolventen", "https://www.absolventa.de/jobs/channel/human-resources")
    # A real posting whose title merely contains the word keeps passing.
    assert s.is_valid_job_title("Channel Manager", "Example", "https://jobs.example.com/jobs/channel-manager-123")
    assert s.is_valid_job_title("Software Engineer", "Example", "https://jobs.example.com/jobs/software-engineer-9")


# F-L: SPA embedded-JSON locations (Oracle HCM class)
def test_saved_detail_location_reads_embedded_json(tmp_path):
    s = _scanner(tmp_path)
    text = 'shell {"jobLocation": "Ghent, Belgium", "title": "Engineer"} tail'
    assert s._saved_detail_location(text) == "Ghent, Belgium"
    labelled = "Some intro\nWork Location: Eindhoven, Netherlands\nMore text"
    assert s._saved_detail_location(labelled) == "Eindhoven, Netherlands"


# F-B: breaker thresholds are pinned contracts (kept at 2 strikes);
# the per-URL block lands on the first confirmed wall.
def test_host_breaker_threshold(tmp_path):
    s = _scanner(tmp_path)
    url = "https://www.harnham.com/job/abc-1"
    wall = "Cloudflare challenge page served instead of the job detail text; no job description could be read"
    s._note_detail_failure(url, wall, blocked=True, transport="http", status=200)
    assert url in s._detail_blocked_urls  # per-URL block lands immediately
    assert "www.harnham.com" not in s._detail_blocked_hosts
    s._note_detail_failure(url, wall, blocked=True, transport="http", status=200)
    assert "www.harnham.com" in s._detail_blocked_hosts
