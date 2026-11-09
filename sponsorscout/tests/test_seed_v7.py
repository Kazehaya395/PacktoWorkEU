"""Seed validation tests (v7 career seed + ATS seed).

Mirrors the career scanner's seed-reader validation so seed regressions
(wrong-name rows like the Deliveroo/Detectify mix-up, bad URLs, invalid
scope policies, duplicate targets) fail here instead of mid-scan.

Run:  pytest tests/test_seed_v7.py
"""
import csv
from pathlib import Path

import pytest

# Test the actual shipped package, independently of the developer's cwd.
from sponsorscout.application import seed_manager


def _find_seed(filename: str) -> Path:
    paths = {
        "company_ATS_seed.csv": seed_manager.bundled_ats_path(),
        "company_Career_seed.csv": seed_manager.bundled_career_path(),
    }
    path = paths[filename]
    assert path.is_file(), f"Bundled seed missing: {path}"
    return path


def _load(filename: str):
    # Match the production comment policy; group headings are not targets.
    return [row for row in seed_manager.read_seed_rows(_find_seed(filename))["rows"]
            if not seed_manager.is_comment_row(row)]


def test_career_seed_loads_and_validates():
    rows = _load("company_Career_seed.csv")
    assert len(rows) >= 130, f"unexpected row count: {len(rows)}"
    seen = set()
    for n, r in enumerate(rows, 2):
        name = (r.get("name") or r.get("seed_name") or "").strip()
        url = (r.get("careers_url") or "").strip()
        assert name, f"line {n}: missing name"
        assert url.startswith(("http://", "https://")) and "..." not in url, \
            f"line {n} {name}: bad URL {url!r}"
        st = (r.get("source_type") or "direct_employer").strip().lower()
        assert st in seed_manager.SOURCE_TYPES, f"line {n} {name}: bad source_type"
        sp = (r.get("scope_policy") or "global").strip().lower()
        assert sp in ("global", "seed_url", "job_location"), f"line {n} {name}: bad scope"
        for col in ("sponsorship_history", "english_friendly", "remote_score"):
            raw = (r.get(col) or "").strip()
            if raw:
                assert raw.isdigit() and 0 <= int(raw) <= 100, \
                    f"line {n} {name}: bad {col}={raw!r}"
        key = (name.casefold(), (r.get("target_country") or "Global").casefold(),
               url.casefold())
        assert key not in seen, f"line {n} {name}: duplicate target"
        seen.add(key)


def test_career_seed_structural_host_validation_not_brand_substrings():
    # Corporate groups, translated brands and ATS hosts need not contain the
    # first four letters of a seed label. A substring is not identity evidence.
    from urllib.parse import urlsplit
    rows=_load("company_Career_seed.csv")
    for row in rows:
        parsed=urlsplit(row["careers_url"])
        assert parsed.scheme in ("http","https") and parsed.hostname
        assert not parsed.username and not parsed.password
        if row["name"].casefold()=="deliveroo":
            assert "detectify" not in parsed.hostname.casefold()


def test_career_seed_providers_follow_actual_registered_schema():
    # Do not silently repopulate removed catalog rows (e.g. Pitch/Spendesk) or
    # force a user's existing route to a historical provider just for this test.
    rows=_load("company_Career_seed.csv")
    for row in rows:
        assert not seed_manager.validate_row(row), (row["name"],seed_manager.validate_row(row))
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
    file=_find_seed("company_Career_seed.csv");before=file.read_bytes()
    loaded=CareerPortalScanner(input_csv=str(file),skip_preflight=True).read_seed_file()
    expected={(r["name"].strip(),r["careers_url"].strip(),(r.get("target_country")or"Global").strip()) for r in rows}
    assert {(r["seed_name"],r["careers_url"],r["target_country"])for r in loaded}==expected
    assert file.read_bytes()==before


def test_ats_seed_loads():
    rows = _load("company_ATS_seed.csv")
    assert len(rows) >= 40, f"unexpected row count: {len(rows)}"
    for r in rows:
        assert r["name"].strip()
        assert r["careers_url"].strip().startswith(("http://", "https://"))
