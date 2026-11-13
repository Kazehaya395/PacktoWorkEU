"""Resume-after-kill (2026-11-13, F1+F2).

Run 20261113T131421 was force-killed mid-phase: its 166 settled companies
existed only in the on-disk scan_log CSV, because DB scan_log rows were
written only when the phase finalized. Resume therefore announced
"0/181 companies already done". F1 live-tails scan_log CSVs into the DB
during the run; F2 reconciles a killed run's settled set from its CSV
artifacts when Resume looks it up.
"""
import csv
import sqlite3

import pytest

from packtoworkeu.db import database as db

HEADER = ["Run ID", "Seed Name", "Company", "Source Type", "Target Country",
          "Status", "Provider", "Jobs Found", "Quarantined", "Duplicates",
          "Rejected Scope", "Error", "Diagnostics", "Duration Sec", "Seed URL"]


def _row(run_id, name, status, error=""):
    return {"Run ID": run_id, "Seed Name": name, "Company": name,
            "Source Type": "direct", "Target Country": "Global",
            "Status": status, "Provider": "auto", "Jobs Found": 0,
            "Quarantined": 0, "Duplicates": 0, "Rejected Scope": 0,
            "Error": error, "Diagnostics": "", "Duration Sec": 1.0,
            "Seed URL": f"https://{name.lower()}.example.com"}


def _write_scan_log(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)


def _scan_log_count(db_path, run_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM scan_log WHERE run_id=?", (run_id,)).fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def killed_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "kill.db")
    db.initialize(db_path)
    out = tmp_path / "scan_output"
    out.mkdir()
    monkeypatch.setattr(db, "SCAN_OUTPUT_DIR", out)
    return db_path, out


def test_f2_reconcile_fills_only_empty_runs(killed_db):
    db_path, out = killed_db
    _write_scan_log(out / "R1_career_jobs_scan_log.csv",
                    [_row("R1", "Done", "ok"), _row("R1", "Killed", "error")])
    assert db.reconcile_scan_log_from_artifacts(db_path, "R1") == 2
    assert _scan_log_count(db_path, "R1") == 2
    # Second call: DB already populated -> no-op, no duplicates.
    assert db.reconcile_scan_log_from_artifacts(db_path, "R1") == 0
    assert _scan_log_count(db_path, "R1") == 2


def test_f2_resume_after_kill_skips_settled(killed_db):
    from packtoworkeu.application.scan_scope import save_plan
    db_path, out = killed_db
    db.start_scan_run(db_path, "R1", "custom", 0, 3)  # stays 'running': killed
    plan = {"version": 1, "method": "full", "ats": [], "career": [
        {"seed_name": n, "target_country": "Global",
         "careers_url": f"https://{n.lower()}.example.com"}
        for n in ("Done", "Killed", "Never")]}
    save_plan(db_path, "R1", plan)
    _write_scan_log(out / "R1_career_jobs_scan_log.csv", [
        _row("R1", "Done", "ok"),
        _row("R1", "Killed", "error",
             "seed page unreachable: no network route from the scanning "
             "machine [net::ERR_INTERNET_DISCONNECTED]"),
    ])
    cp = db.get_resumable_scan(db_path)
    assert cp is not None and cp["run_id"] == "R1"
    # Done stays done; the outage casualty and the never-attempted return.
    assert sorted(cp["remaining_career"]) == ["Killed", "Never"]
    assert cp["done_career"] == 1


def test_f2_chain_reconciles_killed_parent(killed_db):
    from packtoworkeu.application.scan_scope import remaining_targets, save_plan
    db_path, out = killed_db
    plan = {"version": 1, "method": "full", "ats": [], "career": [
        {"seed_name": n, "target_country": "Global",
         "careers_url": f"https://{n.lower()}.example.com"}
        for n in ("Done", "Never")]}
    db.start_scan_run(db_path, "R1", "full", 0, 2)
    save_plan(db_path, "R1", plan)
    _write_scan_log(out / "R1_career_jobs_scan_log.csv",
                    [_row("R1", "Done", "ok")])
    # Child resume run, itself killed before finalizing anything.
    db.start_scan_run(db_path, "R2", "full", 0, 2)
    save_plan(db_path, "R2", plan)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE scan_runs SET error='resumed_from:R1' WHERE run_id='R2'")
    conn.commit()
    conn.close()
    remaining = remaining_targets(db_path, "R2", plan)["career"]
    assert [r["seed_name"] for r in remaining] == ["Never"]


def test_f1_window_tail_and_replace_finalize(tmp_path):
    from packtoworkeu.scanning import pipeline
    db_path = str(tmp_path / "live.db")
    db.initialize(db_path)
    path = tmp_path / "R2_career_jobs_scan_log.csv"
    _write_scan_log(path, [_row("R2", "One", "ok")])
    box, seen = [0], set()
    assert pipeline._ingest_scan_log_window(db_path, path, "R2", "career", box, seen) == 1
    # Append a second company; only the new row is tailed in.
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=HEADER).writerow(_row("R2", "Two", "ok"))
    assert pipeline._ingest_scan_log_window(db_path, path, "R2", "career", box, seen) == 1
    assert _scan_log_count(db_path, "R2") == 2
    # Final replace-style ingest of the same artifact: still exactly 2.
    pipeline._ingest_scan_log(db_path, path, "R2", "career")
    assert _scan_log_count(db_path, "R2") == 2
