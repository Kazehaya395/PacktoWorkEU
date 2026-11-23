"""Conservative Career reliability scorecards; no network, seed edits or guessing.

A first-page fetch, sampled JD, or readable legacy body is not a portal pass.
Use evidence from a completed scanner run and checked SQLite ingestion. Unknown
and blocked configurations remain in the denominator.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,math
from pathlib import Path

KEY_FIELDS=('careers_url','provider','board_slug','target_country','scope_policy')
PASS_STATES=frozenset({'verified_working','verified_empty'})


def configuration_key(row):
    """Preserve URLs/queries and country scope. Only exact configurations merge."""
    return tuple(str(row.get(k) or '') for k in KEY_FIELDS)


def configuration_id(row):
    return hashlib.sha256(json.dumps(configuration_key(row),ensure_ascii=False).encode()).hexdigest()


def inventory(rows):
    configs={}
    for row in rows:
        name=str(row.get('seed_name') or row.get('name') or '').strip()
        if not name or name.startswith('#') or str(row.get('enabled','true')).lower() in ('0','false','no','disabled'):
            continue
        key=configuration_key(row)
        entry=configs.setdefault(key,dict(row,configuration_id=configuration_id(row),seed_names=[]))
        if name not in entry['seed_names']:entry['seed_names'].append(name)
    return list(configs.values())


def assess(evidence):
    e=evidence or {}
    if e.get('block_reason'):return 'blocked'
    if e.get('full_scan_completed') is not True:
        return 'sampled_only' if e.get('sampled_path_pass') is True else 'not_fully_audited'
    if e.get('listing_complete') is not True:return 'incomplete'
    if (e.get('published_total')==0 and type(e.get('published_total')) is int
            and e.get('verified_empty_evidence') is True
            and type(e.get('accepted')) is int and e.get('accepted')==0 and e.get('scan_status')=='empty'):
        return 'verified_empty'
    n=e.get('accepted')
    if type(n) is not int or n<=0:return 'incomplete'
    if e.get('scan_status')!='ok':return 'incomplete'
    if e.get('scope_accounted') is not True or type(e.get('source_observed')) is not int or e.get('source_observed')!=n:return 'needs_review'
    for field in ('description_reads','stored_confirmed'):
        if type(e.get(field)) is not int or e[field]!=n:return 'incomplete'
    if e.get('ingest_errors'):return 'failed'
    return 'verified_working'


def scorecard(configs,observations):
    by_id={e['configuration_id']:e for e in observations if e.get('configuration_id')}
    rows=[]
    for config in configs:
        evidence=by_id.get(config['configuration_id'],{})
        rows.append(dict(configuration_id=config['configuration_id'],seed_names=' | '.join(config['seed_names']),
            **{k:config.get(k,'') for k in KEY_FIELDS},state=assess(evidence),
            listing_observed=evidence.get('source_observed',''),accepted=evidence.get('accepted',''),
            stored_confirmed=evidence.get('stored_confirmed',''),note=evidence.get('note','No completed app-level evidence')))
    n=len(rows);verified=sum(r['state'] in PASS_STATES for r in rows)
    states={state:sum(r['state']==state for r in rows) for state in sorted({r['state'] for r in rows})}
    summary=dict(configurations=n,verified=verified,states=states,
        verified_coverage_percent=round(100*verified/n,2) if n else None,
        required_for_90_percent=math.ceil(.9*n),required_for_95_percent=math.ceil(.95*n),
        target_90_met=bool(n and verified>=math.ceil(.9*n)),target_95_met=bool(n and verified>=math.ceil(.95*n)),
        interpretation='Verified coverage only, NOT an estimated working rate for unaudited portals.')
    return rows,summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',required=True);parser.add_argument('--observations',required=True);parser.add_argument('--output-dir',required=True)
    args=parser.parse_args(argv)
    # Use the application's enabled-row reader, not a different seed parser.
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    scanner=CareerPortalScanner(input_csv=args.seed,skip_preflight=True)
    targets=scanner.read_seed_file();configs=inventory(targets)
    data=json.loads(Path(args.observations).read_text());rows,summary=scorecard(configs,data)
    summary.update(enabled_rows=len(targets),distinct_urls=len({r['careers_url'] for r in targets}),
                   seed_sha256=hashlib.sha256(Path(args.seed).read_bytes()).hexdigest())
    output=Path(args.output_dir);output.mkdir(parents=True,exist_ok=True)
    with (output/'career-reliability-scorecard.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['configuration_id','state']);writer.writeheader();writer.writerows(rows)
    (output/'career-reliability-summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
    return 0 if summary['target_90_met'] else 2


if __name__=='__main__':raise SystemExit(main())
