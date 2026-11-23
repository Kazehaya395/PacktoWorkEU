"""Immutable run targets. Resume never consults or expands the current seeds."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path

from packtoworkeu.db import database as db
from packtoworkeu.scanning.common import is_transient_transport_failure


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


def share_career_targets(rows):
    """Share exact equivalent routes after selection, before saving a new plan.

    Include every non-display field: never let one alias donate another's
    filters, employer type, scores, notes, or provider configuration.
    """
    out, groups = [], {}
    for source in rows:
        row = dict(source)
        alias = row.get('seed_name') or row.get('name')
        key = tuple(sorted((k,v) for k,v in row.items() if k not in ('name','seed_name')))
        if not row.get('careers_url') or key not in groups:
            out.append(row)
            groups[key] = row
            continue
        first = groups[key]
        aliases = json.loads(first.get('shared_seed_names') or '[]') or [first.get('seed_name') or first.get('name')]
        if alias not in aliases:
            first['shared_seed_names'] = json.dumps(aliases + [alias],ensure_ascii=False)
    return out


def _completed_listing_review(row):
    """A reconciled search with missing descriptions is not a failed portal crawl."""
    from packtoworkeu.scanning.common import completion_status
    if row['status'] != 'partial' or row['error'] or row['scanner'] not in ('career','ats'):
        return None
    pieces = [p.strip() for p in (row['diagnostics'] or '').split(' | ')]
    census = [m for p in pieces if (m := re.fullmatch(r'AMS search: (\d+) distinct posting IDs on (\d+) page\(s\)', p))]
    coverage = [dict(re.findall(r'(\w+)=(\d+)', p)) for p in pieces if p.startswith('JD coverage:')]
    if len(coverage)!=1:
        return None
    ats_source_short = False
    if row['scanner'] == 'ats':
        # Only direct nonempty Workday API descriptions with a reconciled
        # listing census qualify. Failed/unattempted reads remain retryable.
        listings = [re.fullmatch(r'Workday listing complete: observed=(\d+); total=(\d+)', p) for p in pieces]
        listings = [m for m in listings if m]
        captures = [re.fullmatch(r'Workday descriptions captured: nonempty=(\d+)', p) for p in pieces]
        captures = [m for m in captures if m]
        observed = int(coverage[0].get('observed',0))
        if (len(listings)!=1 or listings[0][1]!=listings[0][2] or
                len(captures)!=1 or int(captures[0][1])!=observed or
                any(int(v) for k,v in coverage[0].items() if k not in ('observed','readable','thin'))):
            return None
        ats_source_short = True
    elif len(census)!=1:
        return None
    counts = {k:int(v) for k,v in coverage[0].items()}
    observed = counts.pop('observed',0); readable = counts.pop('readable',0)
    if (not observed or (not ats_source_short and int(census[0][1]) != observed) or row['jobs_found'] != observed
            or observed-readable != sum(counts.values()) or observed <= readable):
        return None
    if completion_status(observed, diagnostics=[p for p in pieces if not p.startswith('JD coverage incomplete:')]) != 'ok':
        return None
    # Keep explicit transport problems, access walls and unfinished work retryable.
    if any(v for k,v in counts.items() if k not in ('thin','empty','failed')) or any(
            re.search(r'timeout|timed out|connection reset|http_status=(?:429|5\d\d)\b',p,re.I)
            for p in pieces if p.startswith('response evidence:')):
        return None
    return (row['seed_name'] or row['company'], observed, readable,
            {k:v for k,v in counts.items() if v},
            [p.partition(': ')[2] for p in pieces if p.startswith('response evidence:') or (ats_source_short and p.startswith('Workday descriptions captured:'))])


def retry_plan(db_path, run_id, *, reasons=None, reviews=None):
    """New campaign using only final unsuccessful routes of a finished run."""
    run = db.get_scan_run(db_path, run_id)
    if not run or run['status'] not in ('completed','partial','error'):
        return None
    plan = load_plan(db_path, run_id)
    if plan is None:
        return None
    conn = db.get_connection(db_path)
    try:
        from packtoworkeu.scanning.common import contradicted_zero
        import re
        states = {}
        for r in conn.execute('SELECT scanner,seed_name,company,target_country,seed_url,status,jobs_found,error,diagnostics FROM scan_log WHERE run_id=? ORDER BY id',(run_id,)):
            key = (r['scanner'],target_key(dict(r)))
            status = r['status']; diag = r['diagnostics'] or ''
            conflict = not r['jobs_found'] and contradicted_zero(diag)
            if conflict and status == 'empty': status = 'unverified_empty'
            review = _completed_listing_review(r)
            if reviews is not None:
                reviews.pop(key, None)
                if review: reviews[key] = review
            states[key] = 'detail_review' if review else status
            if reasons is not None:
                coverage = re.findall(r'JD coverage: ([^|\n]+)',diag)
                parts = dict(re.findall(r'(\w+)=(\d+)',coverage[-1])) if coverage else {}
                if conflict: why = 'zero harvested; reported total is positive'
                elif status=='unverified_empty': why = 'zero harvested; empty listing not verified'
                elif r['error']: why = str(r['error']).splitlines()[0][:180]
                elif parts and int(parts.get('observed',0))>int(parts.get('readable',0)):
                    why = ', '.join(f'{k}: {v}' for k,v in parts.items() if k not in ('observed','readable') and int(v))
                else: why = 'listing or detail coverage incomplete'
                notes = []
                if 'shared detail time budget exhausted' in diag: notes.append('detail time limit reached')
                if 'LIST budget reached' in diag: notes.append('listing also stopped before its last page')
                if parts.get('unattempted','0') != '0' and 'budget_skipped=' in diag: notes.append('previously unread postings remain')
                if notes: why += ' — ' + '; '.join(notes)
                reasons[key] = (status,why)

    finally:
        conn.close()
    result = dict(plan, retry_of=run_id)
    for phase in ('ats','career'):
        result[phase] = [r for r in plan[phase] if states.get((phase,target_key(r))) in ('error','partial','unverified_empty')]
    return result if result['ats'] or result['career'] else None


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
    """Exact identities across a resume chain; phases and same-name routes stay distinct.

    Universal network resilience (2026-11-13, audit G2): a target whose only
    outcome is a transient transport error (lost route, DNS failure, timeout,
    connection reset) is NOT settled -- it says the machine could not reach
    the portal on that attempt, so Resume must offer it again. Run
    20261113T131421 recorded 80 such errors for one internet outage and the
    old rule (any 'error' is settled) made every one of them unresumable.
    Genuine portal outcomes (walls, 4xx, blocks -- see
    is_transient_transport_failure's exclusions) stay settled."""
    done = {'ats': set(), 'career': set()}
    seen = set()
    conn = db.get_connection(db_path)
    try:
        while run_id and run_id not in seen:
            seen.add(run_id)
            # F2 (2026-11-13): every run in the chain reconciles its own
            # on-disk scan_log artifacts first -- a killed parent (whose
            # rows never reached the DB) must not read as "0 done" when a
            # resume child is the run being examined.
            try:
                db.reconcile_scan_log_from_artifacts(db_path, run_id)
            except Exception:
                pass
            for row in conn.execute("SELECT scanner,seed_name,company,target_country,seed_url,status,error FROM scan_log WHERE run_id=? AND status IN ('ok','partial','empty','error','unverified_empty')", (run_id,)):
                if (row['status'] or '').strip().casefold() == 'error' \
                        and is_transient_transport_failure(row['error'] or ''):
                    continue
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
