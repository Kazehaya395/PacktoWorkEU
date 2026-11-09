"""One explicit collection action; exact-scope planning and identity reconciliation."""
import csv
from pathlib import Path
import re
from sponsorscout.core.job_identity import posting_identity, portal_queue_key


def plan_collection(conn, url, name, run_id=''):
    from sponsorscout.core.assisted_resume import _path, load_batch, listing_continuation
    if not _path(conn,url).exists() and not run_id:
        return dict(mode='fresh', reason='No saved batch; collecting this exact portal.')
    batch=load_batch(conn,url,name,run_id=run_id or None)
    if batch.get('listing_completed'):
        return dict(mode='details',run_id=batch['run_id'],reason='Listing already verified; continuing saved descriptions only.')
    try:
        listing_continuation(batch)
        return dict(mode='listing',run_id=batch['run_id'],reason='Continuing the recorded listing; valid saved descriptions will be reused.')
    except ValueError:
        return dict(mode='fresh',reason='Rechecking the incomplete listing; valid saved descriptions will be reused.')


def required_postings(queued):
    """Original exported scope, never a company/DB sweep or latest-count guess.

    Return None when the original inventory cannot be audited. Caller must not
    retire a JD-only issue on a smaller unrelated successful batch.
    """
    if queued.get('access_scope')!='details':return set()
    run=str(queued.get('run_id')or'')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',run):return None
    from sponsorscout import paths
    try:
        file=Path(paths.SCAN_OUTPUT_DIR)/(run+'_career_jobs.csv')
        if not file.exists() or file.stat().st_size>25_000_000:return None
        found=set();scope=portal_queue_key(queued.get('url',''))
        with file.open(encoding='utf-8-sig',newline='')as f:
            reader=csv.DictReader(f)
            if not {'Seed URL','Job URL'}<=set(reader.fieldnames or()):return None
            for row in reader:
                if portal_queue_key(row.get('Seed URL',''))==scope:
                    key=posting_identity(row.get('Job URL',''))
                    # An unidentified original posting is an unresolved gap,
                    # not permission to audit only the remaining smaller set.
                    if not key:return None
                    found.add(key)
                    if len(found)>20000:return None
        return found or None
    except (OSError, UnicodeError, csv.Error, ValueError):
        # A legacy export is supporting evidence, not permission to stop a new
        # explicit collection or clear its original unresolved posting gap.
        return None



def reconcile_details(required, outcomes):
    if required is None:return dict(verified=False,remaining=None,reason='Original saved posting inventory unavailable; earlier JD issue not automatically cleared.')
    readable={posting_identity(row.get('url',''))for row in outcomes or()if row.get('jd_read_status')=='readable'}
    remaining=required-readable
    return dict(verified=not remaining,remaining=len(remaining),reason=(
        'Original saved posting identities reconciled.'if not remaining else
        f'{len(remaining)} original saved posting identities still lack readable evidence; earlier JD issue retained.'))
