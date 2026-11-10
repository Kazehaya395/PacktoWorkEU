"""Immutable run targets. Resume never consults or expands the current seeds."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from packtoworkeu.db import database as db


def target_key(row):
    return ((row.get('seed_name') or row.get('name') or row.get('company') or '').strip().casefold(),
            (row.get('target_country') or 'Global').strip().casefold(),
            (row.get('careers_url') or row.get('seed_url') or '').strip())


def read_targets(path, selected=None):
    wanted = None if selected is None else {str(x).strip().casefold() for x in selected}
    out, seen = [], set()
    with open(path, encoding='utf-8-sig', newline='') as fh:
        sample = fh.read(4096)
        fh.seek(0)
        for row in csv.DictReader(fh, delimiter='\t' if sample.count('\t') > sample.count(',') else ','):
            row = {k: str(v or '') for k, v in row.items() if k is not None}
            key = target_key(row)
            if not key[0] or key[0].startswith('#') or key in seen:
                continue
            if row.get('enabled', 'true').strip().casefold() in {'0', 'false', 'no', 'disabled'}:
                continue
            canonical = (row.get('canonical_name') or row.get('name') or '').strip().casefold()
            if wanted is not None and not {key[0], canonical} & wanted:
                continue
            seen.add(key)
            out.append(row)
    return out


def save_plan(db_path, run_id, plan):
    conn = db.get_connection(db_path)
    try:
        with conn:
            conn.execute('CREATE TABLE IF NOT EXISTS scan_run_scopes (run_id TEXT PRIMARY KEY, scope_json TEXT NOT NULL)')
            conn.execute('INSERT INTO scan_run_scopes(run_id,scope_json) VALUES (?,?)',
                         (run_id, json.dumps(plan, ensure_ascii=False)))
    finally:
        conn.close()


def load_plan(db_path, run_id):
    conn = db.get_connection(db_path)
    try:
        try:
            row = conn.execute('SELECT scope_json FROM scan_run_scopes WHERE run_id=?', (run_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc).lower():
                raise
            return None
        if row is None:
            return None
        plan = json.loads(row[0])
        if plan.get('version') != 1 or plan.get('method') not in ('full', 'quick'):
            raise ValueError('Invalid saved scan scope')
        if 'refresh_descriptions' in plan and not isinstance(plan['refresh_descriptions'], bool):
            raise ValueError('Invalid saved description refresh policy')
        for phase in ('ats', 'career'):
            if not isinstance(plan.get(phase), list) or any(not isinstance(r, dict) for r in plan[phase]):
                raise ValueError('Invalid saved scan targets')
        return plan
    finally:
        conn.close()


def settled_targets(db_path, run_id):
    """Exact identities across a resume chain; phases and same-name routes stay distinct."""
    done = {'ats': set(), 'career': set()}
    seen = set()
    conn = db.get_connection(db_path)
    try:
        while run_id and run_id not in seen:
            seen.add(run_id)
            for row in conn.execute("SELECT scanner,seed_name,company,target_country,seed_url FROM scan_log WHERE run_id=? AND status IN ('ok','partial','empty','error')", (run_id,)):
                phase = (row['scanner'] or '').lower()
                if phase in done:
                    done[phase].add(target_key(dict(row)))
            parent = conn.execute('SELECT error FROM scan_runs WHERE run_id=?', (run_id,)).fetchone()
            run_id = db.parse_resume_link(parent[0]) if parent else ''
    finally:
        conn.close()
    return done


def remaining_targets(db_path, run_id, plan):
    done = settled_targets(db_path, run_id)
    return {phase: [r for r in plan[phase] if target_key(r) not in done[phase]]
            for phase in ('ats', 'career')}


def checkpoint(db_path, run_id, plan):
    remaining = remaining_targets(db_path, run_id, plan)
    result = {'run_id': run_id, 'method': plan['method'], 'added_since_stop': 0}
    for phase in ('ats', 'career'):
        result['remaining_' + phase] = [r.get('seed_name') or r.get('name') for r in remaining[phase]]
        result['total_' + phase] = len(plan[phase])
        result['done_' + phase] = len(plan[phase]) - len(remaining[phase])
    return result if any(remaining.values()) else None


def write_seed(path, rows):
    """Private per-run input, not a rewrite of the user's seeds."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=fields or ['name', 'careers_url'])
        writer.writeheader()
        writer.writerows(rows)
    return path
