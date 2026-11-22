"""Cooperative background-thread manager for scanner runs.

Decouples the desktop UI event loop from the blocking scanner loops
(Greenhouse/Lever HTTP requests and Playwright browser automations).  Emits
Qt signals for real-time progress and final ingestion summaries.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

try:
    from PySide6.QtCore import QObject, Signal
except ImportError:
    class QObject:
        pass
    class Signal:
        def __init__(self, *args, **kwargs):
            pass
        def emit(self, *args, **kwargs):
            pass
        def connect(self, *args, **kwargs):
            pass


logger = logging.getLogger(__name__)


class ScanCoordinator(QObject):
    """Run `pipeline.run_scan` inside a managed daemon thread.

    Signals:
      * `progress(str)`: streamed scanner log lines (ready for QPlainTextEdit)
      * `progress_tick(int, int, str, str)`: `(done, total, phase, current_company)`
      * `finished(dict)`: summary dictionary returned by `run_scan`
      * `state_changed(str)`: `"idle" | "running" | "paused" | "stopping"`
    """

    progress = Signal(str)
    progress_tick = Signal(int, int, str, str)
    work_progress = Signal(dict)
    finished = Signal(dict)
    state_changed = Signal(str)

    # UI updates are batched to prevent flooding Qt, but flush frequently so
    # the user sees output immediately when a scan begins.
    PROGRESS_FLUSH_SEC = 0.05
    PROGRESS_FLUSH_LINES = 10

    @staticmethod
    def stop_grace_sec() -> float:
        """Graceful-stop budget (2026-11-17, Task 34).

        After Stop is pressed the scanners get this long to unwind
        in-flight work cooperatively; past it the UI may offer a force
        close. Env-overridable for slow machines, clamped to [10, 300].
        """
        try:
            return min(300.0, max(10.0, float(
                os.environ.get("PACKTOWORKEU_STOP_GRACE_SEC") or 75)))
        except (TypeError, ValueError):
            return 75.0

    def __init__(self, db_path: str | None = None):
        super().__init__()
        self.db_path = db_path
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state: str = "idle"
        self._stop_requested_at: Optional[float] = None

    def state(self) -> str:
        """Current scan-control state without touching the worker or database."""
        return self._state

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def _emit_state(self, new_state: str):
        self._state = new_state
        self.state_changed.emit(new_state)

    def pause(self):
        """Cooperative in-memory pause."""
        if not self.is_running() or self._pause.is_set():
            return
        self._pause.set()
        self._emit_state("paused")

    def resume(self):
        """Continue an in-memory paused run."""
        if not self._pause.is_set():
            return
        self._pause.clear()
        self._emit_state("running" if self.is_running() else "idle")

    def start(self, method: str = "full", resume_from: str | None = None,
              scan_scope: dict | None = None, refresh_descriptions: bool = False) -> bool:
        """Start a scan campaign. False if a scan is already running."""
        if self.is_running():
            return False

        self._cancel.clear()
        self._pause.clear()
        self._stop_requested_at = None
        self._emit_state("running")

        def worker():
            from packtoworkeu.scanning import pipeline

            from packtoworkeu.application.scan_progress import ScanProgressState, parse_event
            work_state = ScanProgressState()
            pending: list[str] = []
            lock = threading.Lock()
            dispatch_lock = threading.Lock()
            last = [time.monotonic()]
            stop_flusher = threading.Event()

            def flush_progress():
                # Serialize drain+emit across worker and timer, preserving log
                # order and monotonic progress even during concurrent flushes.
                with dispatch_lock:
                    with lock:
                        if not pending:
                            return
                        chunk = "\n".join(pending)
                        pending.clear()
                        last[0] = time.monotonic()
                    work_changed = False
                    for line in chunk.splitlines():
                        event = parse_event(line)
                        if event is not None:
                            try:
                                work_state.apply(event)
                                work_changed = True
                            except (TypeError, ValueError, KeyError):
                                logger.warning('Ignoring malformed structured progress event')
                            continue
                        tick = _parse_progress_tick(line)
                        if tick is not None:
                            self.progress_tick.emit(*tick)
                    if work_changed:
                        self.work_progress.emit(work_state.snapshot())
                    self.progress.emit(chunk)

            def _periodic_flush():
                while not stop_flusher.wait(0.08):
                    with lock:
                        has_pending = bool(pending) and (
                            time.monotonic() - last[0]
                        ) >= self.PROGRESS_FLUSH_SEC
                    if has_pending:
                        flush_progress()

            flusher = threading.Thread(
                target=_periodic_flush, name="ScanProgressFlusher", daemon=True
            )
            flusher.start()

            def on_progress(message):
                msg_s = str(message)
                stripped = msg_s.lstrip()
                immediate = stripped.startswith((
                    "SCAN_EVENT:", "PROGRESS:", "Scan ", "ATS ", "Career ", "No ATS", "No career",
                    "v7 scan", "host (", "[", "OK", "EMPTY", "ERROR", "PARTIAL",
                )) or "WROTE=" in stripped
                with lock:
                    pending.append(msg_s)
                    due = (immediate
                           or len(pending) >= self.PROGRESS_FLUSH_LINES
                           or (time.monotonic() - last[0]) >= self.PROGRESS_FLUSH_SEC)
                if due:
                    flush_progress()

            try:
                scope = scan_scope if isinstance(scan_scope, dict) else None
                summary = pipeline.run_scan(
                    method=method,
                    db_path=self.db_path,
                    cancel_event=self._cancel,
                    pause_event=self._pause,
                    progress=on_progress,
                    resume_from=resume_from,
                    **(dict(retry_from=scope["retry_from"]) if scope and scope.get("retry_from") else {}),
                    only_ats=scope.get("ats") if scope else None,
                    only_career=scope.get("career") if scope else None,
                    run_ats=bool(scope.get("run_ats", True)) if scope else True,
                    run_career=bool(scope.get("run_career", True))
                    if scope else True,
                    **(dict(refresh_descriptions=True) if refresh_descriptions else {}),
                )
            except Exception as exc:
                logger.exception("Scan worker thread crashed")
                summary = {
                    "run_id": "", "method": method, "status": "error",
                    "cancelled": False, "ingested": 0, "duplicates": 0,
                    "log_rows": 0, "artifacts": {},
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            finally:
                stop_flusher.set()
                flusher.join(timeout=0.3)
                flush_progress()
            self._pause.clear()
            self._emit_state("idle")
            self.finished.emit(summary)

        self._thread = threading.Thread(target=worker, name="ScanWorker", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Cooperative stop: scanners check this between control gates."""
        self._pause.clear()
        self._cancel.set()
        if self._stop_requested_at is None:
            self._stop_requested_at = time.monotonic()
        if self.is_running():
            self._emit_state("stopping")

    def is_stopping(self) -> bool:
        """True between the first Stop request and the worker's exit."""
        return self._stop_requested_at is not None and self.is_running()

    def stopping_elapsed(self) -> float:
        """Seconds since Stop was first requested (0 if never stopped)."""
        if self._stop_requested_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._stop_requested_at)

    def stop_grace_exceeded(self) -> bool:
        """True once in-flight work has had its full graceful budget."""
        return (self.is_stopping()
                and self.stopping_elapsed() >= self.stop_grace_sec())


PROGRESS_PREFIX = "PROGRESS:"


def _parse_progress_tick(line: str):
    """Parse a `PROGRESS: done/total:phase:label` line."""
    try:
        text = str(line).strip()
    except Exception:
        return None
    if not text.startswith(PROGRESS_PREFIX):
        return None
    body = text[len(PROGRESS_PREFIX):].strip()
    parts = body.split(":", 3)
    if len(parts) < 3:
        return None
    counts = parts[0].split("/", 1)
    if len(counts) != 2:
        return None
    try:
        done = int(counts[0])
        total = int(counts[1])
    except ValueError:
        return None
    if total <= 0:
        return None
    done = min(done, total)
    phase = parts[1]
    label = parts[2] if len(parts) >= 3 else ""
    return done, total, phase, label
