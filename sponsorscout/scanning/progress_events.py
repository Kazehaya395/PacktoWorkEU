"""Structured, observational scan progress. Never changes collection decisions."""
from __future__ import annotations
import hashlib
import json
import threading

PREFIX = 'SCAN_EVENT:'


def emit(callback, kind, **data):
    try:
        callback(PREFIX + json.dumps(dict(kind=kind, **data), ensure_ascii=False))
    except Exception:
        pass  # Telemetry must never fail a scan.


def task_key(phase, target):
    parts = [phase, target.get('seed_name') or target.get('name') or '',
             target.get('careers_url') or target.get('url') or '',
             target.get('target_country') or 'Global']
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


def final_diagnostics(diagnostics, coverage, extra=(), limit=4000):
    """Keep the authoritative final census even when technical notes are long."""
    parts = [str(line) for line in diagnostics if not str(line).startswith('JD coverage: ')]
    parts.extend(map(str, extra))
    parts.append('JD coverage: ' + '; '.join(f'{key}={value}' for key, value in coverage.items()))
    return ' | '.join(parts)[-limit:]


class TaskReporter:
    """Observe registered JD candidates at most twice/second, including API reads.

    Snapshot classifications come from DetailRequestState, NOT enrichment counts.
    No network/browser/DB operations. Copy scope memberships under its own lock;
    never hold that lock while invoking a progress callback.
    """
    def __init__(self, scanner, phase, callback):
        self.scanner, self.phase, self.callback = scanner, phase, callback
        self.lock = threading.RLock()
        self.tasks = {}
        self.attempts = {}
        self.stop_event = threading.Event()
        self.thread = None
        self.sequence = 0
        self.last_coverage = {}

    def __enter__(self):
        self.scanner._progress_reporter = self
        self.thread = threading.Thread(target=self._poll, name='ScanJDProgress', daemon=True)
        try:
            self.thread.start()
        except RuntimeError:
            self.thread = None  # Lifecycle events still work without sampling.
        return self

    def __exit__(self, *args):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        self.scanner._progress_reporter = None

    def _send(self, kind, **data):
        self.sequence += 1
        emit(self.callback, kind, sequence=self.sequence, **data)

    def begin(self, target):
        key = task_key(self.phase, target)
        with self.lock:
            attempt = self.attempts.get(key, 0) + 1
            self.attempts[key] = attempt
            data = dict(task=key, phase=self.phase,
                        name=target.get('seed_name') or target.get('name') or '',
                        attempt=attempt, scope=target.get('careers_url') or target.get('url') or '')
            self.tasks[key] = data
            self._send('task_begin', **{k:v for k,v in data.items() if k != 'scope'})
        return key

    def retry(self, targets):
        with self.lock:
            for target in targets:
                self._send('task_retry', task=task_key(self.phase, target), phase=self.phase,
                           name=target.get('seed_name') or target.get('name') or '')

    def stage(self, key, stage):
        with self.lock:
            if key in self.tasks:
                self._send('task_stage', task=key, attempt=self.tasks[key]['attempt'], stage=stage)

    def _snapshot(self, key, coverage=None):
        data = self.tasks.get(key)
        if not data:
            return
        state = getattr(self.scanner, '_detail_state', None)
        if state is None:
            return
        if coverage is None:
            with state.lock:
                urls = [u for u, scopes in state.memberships.items() if data['scope'] in scopes and u != data['scope']]
                inflight = int((state.scopes.get(data['scope']) or {}).get('inflight', 0))
            if not urls:
                return
            coverage = state.coverage(data['scope'], urls, getattr(self.scanner, 'get_captured_jd', None))
        else:
            # Final per-target census from the SAME candidate set as exports,
            # replacing live auxiliary/pre-prune observations (including zero).
            coverage = dict(coverage)
            inflight = 0
        signature = (data['attempt'], coverage, inflight)
        if self.last_coverage.get(key) != signature:
            self.last_coverage[key] = signature
            self._send('jd_snapshot', task=key, attempt=data['attempt'], coverage=coverage, inflight=inflight)

    def end(self, key, status, *, coverage=None):
        with self.lock:
            if key not in self.tasks:
                return
            try:
                self._snapshot(key, coverage)
            except Exception:
                pass
            self._send('task_end', task=key, attempt=self.tasks[key]['attempt'], status=status)
            del self.tasks[key]

    def _poll(self):
        while not self.stop_event.wait(.5):
            with self.lock:
                for key in list(self.tasks):
                    try:
                        self._snapshot(key)
                    except Exception:
                        pass
