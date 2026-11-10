"""Pure UI-facing state for parallel task and JD progress; no Qt dependency."""
from __future__ import annotations
import json
from packtoworkeu.scanning.progress_events import PREFIX


def parse_event(line):
    if not str(line).strip().startswith(PREFIX):
        return None
    try:
        value = json.loads(str(line).strip()[len(PREFIX):])
        return value if isinstance(value, dict) and isinstance(value.get('kind'), str) else None
    except (ValueError, TypeError):
        return None


class ScanProgressState:
    def __init__(self):
        self.total = self.base = 0
        self.tasks = {}
        self.lifecycle = 'running'

    def apply(self, event):
        kind = event.get('kind')
        if kind == 'configure':
            self.total = max(0, int(event.get('total', 0)))
            self.base = min(self.total, max(0, int(event.get('base', 0))))
        elif kind == 'lifecycle':
            self.lifecycle = event.get('state', 'running')
        elif kind in {'task_begin', 'task_retry', 'task_stage', 'task_end', 'jd_snapshot'}:
            key = event.get('task')
            if not isinstance(key, str) or not key:
                return self.snapshot()
            task = self.tasks.setdefault(key, dict(name=event.get('name', ''), state='queued',
                                                   stage='listing', attempt=0, coverage={}, jd_inflight=0))
            attempt = int(event.get('attempt', task['attempt']))
            if attempt < task['attempt']:
                return self.snapshot()
            if kind == 'task_begin':
                if attempt > task['attempt']:
                    task.update(coverage={}, stage='listing', jd_inflight=0)
                if attempt == task['attempt'] and task['state'] == 'settled':
                    return self.snapshot()  # Late duplicate start, not a retry.
                task.update(name=event.get('name', task['name']), attempt=attempt, state='active')
            elif kind == 'task_retry':
                task['state'] = 'queued'
            elif kind == 'task_stage':
                task['stage'] = event.get('stage', 'listing')
            elif kind == 'task_end':
                task.update(attempt=attempt, state='settled', status=event.get('status', 'unknown'))
            elif kind == 'jd_snapshot':
                coverage = event.get('coverage')
                if isinstance(coverage, dict):
                    task['jd_inflight'] = max(0, int(event.get('inflight', 0)))
                    task['coverage'] = {k:max(0, int(coverage.get(k, 0))) for k in
                                        ('observed', 'readable', 'thin', 'empty', 'failed', 'blocked', 'unattempted')}
        return self.snapshot()

    def snapshot(self):
        tasks = list(self.tasks.values())
        total = max(self.total, self.base + len(tasks))
        settled = min(total, self.base + sum(t['state'] == 'settled' for t in tasks))
        active = [t for t in tasks if t['state'] == 'active']
        coverage = {k:sum(t['coverage'].get(k, 0) for t in tasks) for k in
                    ('observed', 'readable', 'thin', 'empty', 'failed', 'blocked', 'unattempted')}
        checked = min(coverage['observed'], sum(coverage[k] for k in ('readable', 'thin', 'empty', 'failed', 'blocked')))
        rows = [dict(name=t['name'], stage=t['stage'], **t['coverage']) for t in active]
        return dict(total=total, settled=settled, lifecycle=self.lifecycle,
                    percent=min(99, int(settled*100/total)) if total else 0,
                    queued_retries=sum(t['state'] == 'queued' for t in tasks),
                    listing_active=sum(t['stage'] != 'details' for t in active),
                    jd_active=sum(t['stage'] == 'details' or t['jd_inflight'] > 0 for t in active),
                    checked=checked, coverage=coverage, active=rows)
