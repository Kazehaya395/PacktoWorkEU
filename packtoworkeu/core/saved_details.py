"""Read-only reuse of recent, hash-proven full JDs; never session clearance.

No uploaded-artifact imports, host sweeps, schema edits, verdict copies or network calls.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
import threading
from collections import Counter
from packtoworkeu.core.job_identity import find_existing_posting, posting_identity


_UNAVAILABLE = object()

def saved_detail(conn, url, *, now=None, proofs=None):
    row = find_existing_posting(conn, url)
    if row is None:
        return None
    row = dict(row)
    if (row.get('is_expired') or not row.get('verified_active', 1)
            or str(row.get('source_type') or '').casefold() in ('excluded_nonjob', 'manual_excluded')):
        return None
    return _saved_row(row, url, now=now, proofs=proofs)


def _saved_row(row, url, *, now=None, proofs=None, max_age_seconds=86400):
    if row is None:
        return None
    row = dict(row)
    if (row.get('is_expired') or not row.get('verified_active', 1)
            or str(row.get('source_type') or '').casefold() in ('excluded_nonjob', 'manual_excluded')):
        return None
    # Only a database/exact-scope-validated checkpoint can supply missing proof.
    candidates = [str(row.get('support_evidence_type') or '')]
    if proofs:
        candidates.extend(proofs.get(posting_identity(url), ()))
    for marker in candidates:
        captured = _validated_capture(row, marker, now=now, max_age_seconds=max_age_seconds)
        if captured:
            row['jd_captured_at'] = captured
            return row
    return None


def _validated_capture(row, marker, *, now=None, max_age_seconds=86400):
    def last(name):
        values = re.findall(r'(?:^|;\s*)' + name + r'=([^;\s]+)', marker)
        return values[-1] if values else ''
    captured = last('jd_captured_at')
    text = str(row.get('description') or '')
    if not re.search(r'(?:^|;\s*)jd_read_status=readable(?:;|$|\s)', marker) or len(text.strip()) < 200:
        return None
    if hashlib.sha256(text.encode()).hexdigest() != last('jd_sha256'):
        return None
    try:
        age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(captured)).total_seconds()
        if not 0 <= age < max_age_seconds:
            return None
    except (ValueError, TypeError):
        return None
    return captured


class SavedDetailReader:
    """Per-lookup short read-only connection: safe across scanner worker threads.

    Indexed shared identity lookup, no eager text preload or open idle handle.
    Absent/legacy/locked stores are a cache miss, not scan failure.
    """
    def __init__(self, database, *, force_refresh=False):
        self.uri = Path(database).resolve().as_uri() + '?mode=ro'
        self.proofs = {}
        self.rejected_scopes = 0
        self.force_refresh = bool(force_refresh)
        self.started_at = datetime.now(timezone.utc)
        self._lock = threading.RLock()
        self._observed = {}  # bounded first observations; no JD prose preload
        self._results = {}
        self._outcomes = {}
        self._unavailable_observations = set()
        self.limit = 20000
        self.tracking_complete = True

    @staticmethod
    def _title(value):
        return ' '.join(str(value or '').casefold().split())

    def _lookup(self, url):
        try:
            conn = sqlite3.connect(self.uri, uri=True, timeout=0.25)
            try:
                conn.row_factory = sqlite3.Row
                row = find_existing_posting(conn, url)
                return dict(row) if row is not None else None
            finally:
                conn.close()
        except (sqlite3.Error, ValueError):
            return _UNAVAILABLE

    def observe(self, url, *, title='', revision=''):
        """Observe before upsert; an updated title must not erase its baseline.

        Revision is an actual per-posting provider value, never a page clock,
        list order, missing field, or generated hash of volatile card furniture.
        """
        key = posting_identity(url)
        if not key:
            return
        revision = str(revision or '').strip()[:1000]
        digest = hashlib.sha256(revision.encode()).hexdigest() if revision else ''
        with self._lock:
            if key in self._unavailable_observations:
                return
            existing = self._observed.get(key)
            if existing is not None:
                self._update_observation(existing, title, digest)
                if existing['reason'] in ('changed','unread') and self._results.get(key) == 'reused':
                    self._results[key] = existing['reason']
                return
            if len(self._observed) >= self.limit:
                self.tracking_complete = False
                return
        row = self._lookup(url)
        if row is _UNAVAILABLE:
            with self._lock:
                self.tracking_complete = False
                if len(self._unavailable_observations) < self.limit:
                    self._unavailable_observations.add(key)
            return
        previous_revision = re.findall(r'(?:^|;\s*)listing_revision_sha256=([0-9a-f]{64})',str((row or {}).get('support_evidence_type') or ''))
        observation = dict(existing=row is not None, reason='new' if row is None else '',
            eligible=bool(_saved_row(row,url,proofs=self.proofs)),
            baseline_title=self._title((row or {}).get('title')),
            baseline_revision=previous_revision[-1] if previous_revision else '', revision='')
        self._update_observation(observation, title, digest)
        with self._lock:
            if key in self._observed:
                self._update_observation(self._observed[key], title, digest)
                if self._observed[key]['reason'] in ('changed','unread') and self._results.get(key) == 'reused':
                    self._results[key] = self._observed[key]['reason']
            elif len(self._observed) < self.limit:
                self._observed[key] = observation
            else:
                self.tracking_complete = False

    def _update_observation(self, observation, title, digest):
        current = self._title(title)
        if observation['existing'] and current and observation['baseline_title'] and current != observation['baseline_title']:
            observation['reason'] = 'changed'
        if digest:
            observation['revision'] = digest
            if observation['existing'] and observation['reason'] != 'changed':
                if observation['baseline_revision'] and observation['baseline_revision'] != digest:
                    observation['reason'] = 'changed'
                elif not observation['baseline_revision']:
                    observation['reason'] = 'unread'

    def rejects_previous(self, url):
        with self._lock:
            key = posting_identity(url)
            return self.force_refresh or key not in self._observed or self._observed[key].get('reason') in ('changed','unread')

    def priority(self, url):
        """Read-needed jobs first, without counting eligibility as reuse."""
        with self._lock:
            observation = self._observed.get(posting_identity(url), {})
            return int(not self.force_refresh and not observation.get('reason') and observation.get('eligible',False))

    def note_outcome(self, url, status):
        key = posting_identity(url)
        with self._lock:
            if key in self._observed:
                self._outcomes[key] = str(status or '')

    def note_fresh(self, url, row):
        """Count only hash-proven reads actually captured during this run."""
        valid = _saved_row(row, url)
        if valid and datetime.fromisoformat(valid['jd_captured_at']) >= self.started_at:
            key = posting_identity(url)
            with self._lock:
                if key in self._observed:
                    self._results[key] = 'fresh'

    def bind_revision(self, url, row):
        """Bind a current observed revision only to a proven current-run JD."""
        self.note_fresh(url, row)
        status = row.get('jd_read_status') or (re.findall(r'(?:^|;\s*)jd_read_status=([^;\s]+)', str(row.get('support_evidence_type') or '')) or [''])[-1]
        if status:
            self.note_outcome(url, status)
        key = posting_identity(url)
        with self._lock:
            revision = self._observed.get(key,{}).get('revision','')
            fresh = self._results.get(key) == 'fresh'
        if revision and fresh:
            row = dict(row)
            marker = re.sub(r';?\s*listing_revision_sha256=[0-9a-f]{64}', '',str(row.get('support_evidence_type') or ''))
            row['support_evidence_type'] = marker + '; listing_revision_sha256=' + revision
        return row

    def summary(self):
        with self._lock:
            results = Counter(self._results.values())
            observed = list(self._observed.values())
            unresolved = Counter(status for key,status in self._outcomes.items() if self._results.get(key) not in ('fresh','reused'))
            reasons = Counter(value for value in self._results.values() if value not in ('reused','fresh'))
        return dict(jobs_new=sum(not x['existing'] for x in observed),
                    jobs_existing=sum(x['existing'] for x in observed),
                    jd_reused=results['reused'], jd_fresh=results['fresh'],
                    jd_pending=max(0,len(observed)-results['reused']-results['fresh']),
                    jd_blocked=unresolved['blocked']+unresolved['protected'],
                    jd_inconclusive=sum(unresolved[x] for x in ('failed','thin','empty','truncated')),
                    reasons=dict(reasons), force_refresh=self.force_refresh,
                    tracked=len(observed), tracking_limit=self.limit, tracking_complete=self.tracking_complete)


    def load_scopes(self, targets, cancel_event=None):
        # Exact selected scopes only, including a bounded recent history:
        # a later listing-only checkpoint must not hide an earlier readable JD.
        from packtoworkeu.core.assisted_resume import _path, _read, _validate, _identity, _snapshots
        try:
            conn = sqlite3.connect(self.uri, uri=True, timeout=0.25)
        except sqlite3.Error:
            return
        try:
            conn.row_factory = sqlite3.Row
            for target in targets:
                if cancel_event is not None and cancel_event.is_set():
                    break
                scope = target.get('careers_url') or ''
                try:
                    pointer_path = _path(conn, scope)
                    if not pointer_path.exists() or pointer_path.stat().st_size > 4*1024*1024:
                        continue
                    pointer = _read(pointer_path)
                    _identity(conn, scope, pointer)
                    latest = pointer_path
                    if 'rows' not in pointer:
                        payload = str(pointer.get('payload') or '')
                        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}--[0-9a-f]{20}\.json', payload):
                            raise ValueError('Invalid scoped checkpoint pointer')
                        latest = pointer_path.with_suffix('') / payload
                    if latest.stat().st_size > 4*1024*1024:
                        continue
                    batch = _validate(conn, scope, _read(latest))
                    if batch.get('run_id') != pointer.get('run_id'):
                        raise ValueError('Scoped checkpoint run mismatch')
                    files = sorted(_snapshots(conn, scope), key=lambda p:(p.stat().st_mtime_ns,p.name), reverse=True)[:4]
                    batches = [batch]
                    remaining_bytes = 8*1024*1024-latest.stat().st_size
                    for file in files:
                        if cancel_event is not None and cancel_event.is_set():
                            break
                        if file == latest:
                            continue
                        size = file.stat().st_size
                        if size > min(4*1024*1024, remaining_bytes):
                            continue
                        remaining_bytes -= size
                        try:
                            batches.append(_validate(conn, scope, _read(file)))
                        except (OSError, ValueError):
                            self.rejected_scopes += 1
                    for batch in batches:
                        for row in batch['rows']:
                            key = posting_identity(row.get('url') or '')
                            marker = str(row.get('support_evidence_type') or '')
                            if key and len(marker) <= 4096 and ('jd_sha256=' in marker):
                                if key not in self.proofs and len(self.proofs) >= 4096:
                                    continue
                                entries = self.proofs.setdefault(key, [])
                                if marker not in entries and len(entries) < 2:
                                    entries.append(marker)
                except (OSError, ValueError, sqlite3.Error):
                    self.rejected_scopes += 1
        finally:
            conn.close()

    def read(self, url):
        self.observe(url)
        key = posting_identity(url)
        row = self._lookup(url)
        if row is _UNAVAILABLE:
            self.tracking_complete = False
            return None
        valid = _saved_row(row, url, proofs=self.proofs)
        with self._lock:
            observed = self._observed.get(key, {})
            reason = observed.get('reason', '')
        # A force reread is about PREVIOUS reads, not fresh work just completed
        # by another worker in this same campaign. Never spend that work twice.
        current = bool(valid and datetime.fromisoformat(valid['jd_captured_at']) >= getattr(self, 'policy_started_at', self.started_at))
        if current:
            decision = 'fresh' if datetime.fromisoformat(valid['jd_captured_at']) >= self.started_at else 'reused'
        elif valid and not self.force_refresh and not reason and key in self._observed:
            decision = 'reused'
        else:
            decision = reason or ('forced' if self.force_refresh and valid else
                'stale' if _saved_row(row,url,proofs=self.proofs,max_age_seconds=float('inf')) else 'unread')
            valid = None
        with self._lock:
            if key in self._observed and self._results.get(key) != 'fresh':
                self._results[key] = decision
        return valid

    def __call__(self, url):
        row = self.read(url)
        return str(row['description']) if row else ''


def observed_revision(job):
    """Only explicit per-posting revision fields, never scan/last-seen clocks."""
    for name in ('listing_revision','revision','dateModified','updatedAt','updated_at','lastModified'):
        value=job.get(name)
        if isinstance(value,(str,int)) and str(value).strip():
            return str(value).strip()[:1000]
    return ''


def remember_capture(scanner, url, text, *, captured_at=''):
    """Record provenance only at actual full-detail/API read sites, not cards.

    Bounded metadata only. Never renew timestamps on a saved-text reuse.
    Existing 32k capture clipping is not promoted into proof of completeness.
    """
    text = str(text or '').strip()
    if len(text) < 200 or (len(text) > 32000 and not captured_at):
        return
    key = posting_identity(url)
    lock = getattr(scanner, '_jd_lock', None)
    if not key or lock is None:
        return
    marker = ('jd_read_status=readable; jd_sha256=' + hashlib.sha256(text.encode()).hexdigest()
              + '; jd_captured_at=' + (captured_at or datetime.now(timezone.utc).isoformat()))
    with lock:
        proofs = getattr(scanner, '_capture_proofs', None)
        if proofs is None:
            scanner._capture_proofs = proofs = {}
        if key in proofs or len(proofs) < 20000:
            proofs[key] = marker


def capture_proof(scanner, url, text):
    lock = getattr(scanner, '_jd_lock', None)
    if lock is None:
        return ''
    with lock:
        marker = getattr(scanner, '_capture_proofs', {}).get(posting_identity(url), '')
    return marker if _validated_capture({'description':str(text or '').strip()}, marker) else ''
