"""Bounded same-page access readiness shared by Regular Career/ATS.

Pure observation: no clicks, reloads, solving or new network probes. Assisted
uses the same response evidence, with its existing interactive wait budget.
"""
from __future__ import annotations
import json
import time
from packtoworkeu.scanning.common import check_cancelled
from packtoworkeu.scanning.response_evidence import challenge_reason

def verification_snapshot(page, phase, state):
    """Small redacted CURRENT observation, never cookies or raw HTML."""
    from packtoworkeu.core.browser_evidence import redact_text
    try:
        html = page.content() or ''
        title = page.title() or ''
        body = page.inner_text('body', timeout=200) or ''
        frames = [getattr(f, 'url', '') for f in (getattr(page, 'frames', None) or ())]
        reason = challenge_reason(html, title=title, body=body, frame_urls=frames, status=0)
        return dict(phase=phase, title=redact_text(title)[:160],
                    rendered_chars=len(' '.join(body.split())),
                    state='active_wall' if reason else ('content_present' if len(body.strip()) >= 160 else 'thin_or_empty'),
                    reason=reason, navigation_status=getattr(page, '_last_nav_status', 0),
                    status_source='last_navigation_not_current_content',
                    successful_samples=state.get('samples', 0), read_failures=state.get('read_failures', 0))
    except Exception as exc:
        return dict(phase=phase, state='inspection_failed', error=type(exc).__name__,
                    successful_samples=state.get('samples', 0), read_failures=state.get('read_failures', 0))


def observe_verification(page, reason, diagnostics, started, state, *,
                         max_company_seconds=30.0, verification_seconds=30.0,
                         readiness_seconds=2.0, cancel_event=None,
                         monotonic=None, snapshot=verification_snapshot):
    """Observe initial refusals briefly; positive walls use the shared budget.

    Re-entry cannot extend either deadline. Returned text describes unresolved
    access, not proof of permanent blockage or a human-required CAPTCHA.
    """
    monotonic = monotonic or time.monotonic
    if not reason:
        return ''
    check_cancelled(cancel_event)
    now = monotonic()
    deadline = state.setdefault('deadline', min(
        started + max_company_seconds,
        now + max(0.0, verification_seconds)))
    bare_refusal = (str(reason).startswith('HTTP ')
                    and 'human verification not confirmed' in str(reason))
    if not bare_refusal:
        state['verification_seen'] = True
    readiness_deadline = state.setdefault('readiness_deadline', min(
        deadline, now + max(0.0, readiness_seconds)))
    try:
        initial_html = page.content() or ''
    except Exception:
        initial_html = None  # Navigating is not a terminal refusal or clearance.
        state['read_failures'] = state.get('read_failures', 0) + 1
    diagnostics.append(('verification pending: observing same-page transition; '
                        if state.get('verification_seen') else
                        'access readiness pending: observing initial HTTP refusal; ')
                       + f'remaining={max(0.0, (deadline if state.get("verification_seen") else readiness_deadline)-now):.1f}s')
    diagnostics.append('verification snapshot: ' + json.dumps(snapshot(page, 'initial', state), sort_keys=True))
    current_reason = reason
    while True:
        check_cancelled(cancel_event)
        active_deadline = deadline if state.get('verification_seen') else readiness_deadline
        remaining = active_deadline - monotonic()
        if remaining <= 0:
            diagnostics.append('verification snapshot: ' + json.dumps(snapshot(page, 'terminal', state), sort_keys=True))
            if state.get('verification_seen'):
                state['outcome'] = 'verification_unresolved'
                diagnostics.append('verification unresolved: observation budget exhausted; '
                                   'no automatic clearance confirmed (human action not inferred)')
                return 'verification unresolved: observation budget exhausted; ' + current_reason
            state['outcome'] = 'access_unresolved'
            diagnostics.append('access unresolved: readiness observation exhausted; '
                               'no verification or usable transition confirmed')
            return current_reason
        page.wait_for_timeout(max(1, min(200, int(remaining * 1000))))
        check_cancelled(cancel_event)
        try:
            html = page.content() or ''
            title = page.title() or ''
            body = page.inner_text('body', timeout=max(1, min(
                200, int(max(0, deadline-monotonic())*1000)))) or ''
            frames = [getattr(frame, 'url', '') for frame in
                      (getattr(page, 'frames', None) or ())]
        except Exception:
            state['read_failures'] = state.get('read_failures', 0) + 1
            continue
        state['samples'] = state.get('samples', 0) + 1
        wall = challenge_reason(html, title=title, body=body,
                                frame_urls=frames, status=0)
        if wall:
            current_reason = wall
            refusal_only = ('human verification not confirmed' in wall
                            and wall.startswith(('HTTP ', 'Access blocked:')))
            if not refusal_only and not state.get('verification_seen'):
                state['verification_seen'] = True
                diagnostics.append('verification pending: evidence appeared during access readiness; '
                                   'continuing within the shared observation deadline')
            continue
        if monotonic() >= active_deadline:
            continue
        # Only a changed, nonempty current document without an active wall
        # can retire the stale navigation status. Extraction is still pending.
        if html != initial_html and len(' '.join(body.split())) >= 160:
            old_status = getattr(page, '_last_nav_status', 0)
            page._last_nav_status = 0
            state['outcome'] = 'recovered_content'
            diagnostics.append('verification cleared: changed rendered content observed; '
                               f'previous_status={old_status}; extraction/coverage still pending')
            return ''
