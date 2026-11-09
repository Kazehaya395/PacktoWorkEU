"""Automated and assisted ingestion pipeline for bot-blocked portals.

Provides full browser crawling, human-in-the-loop CAPTCHA solving,
and multi-page vacancy harvesting across all direct employers and aggregators.
"""

from __future__ import annotations

import logging
import json
import sqlite3
import os
import random
import re
import sys
import time
import webbrowser
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from sponsorscout.scanning.response_evidence import challenge_reason, stated_job_total, visible_text
from sponsorscout.core.browser_evidence import inspect_page,public_snapshot,redact_url,redact_text,sensitive_url,watch_page_responses,admitted_job_links,career_listing_links
from sponsorscout.i18n import _ as _translate
from sponsorscout.core.activity import ingestion_task
from sponsorscout.core.job_identity import portal_queue_key, merge_sources, explicit_detail_url, administrative_action, posting_candidate, posting_identity

logger = logging.getLogger(__name__)

#: F-22 (2026-10-22): honest completion report of the last assisted run. The
#: UI reads this so a partial harvest can never be presented as a full one:
#: it carries the pages visited, the listings harvested and WHY the
#: pagination loop stopped.
LAST_INGESTION_REPORT: dict = {}

_LOCALE_SEGMENT_RE = re.compile(r"^[A-Za-z]{2}(?:-[A-Za-z]{2,4})?$")


def strip_locale_segment(url: str) -> str | None:
    """Return ``url`` without a leading locale path segment, else ``None``.

    Generic 404 recovery (F-25): boards that answer at ``/jobs.html`` but 404
    at ``/en/jobs.html`` — any locale, any host. Replaces the old host-name
    rewrite, which guide §15.9 forbids.
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts or not _LOCALE_SEGMENT_RE.match(parts[0]):
        return None
    new_path = "/" + "/".join(parts[1:]) if len(parts) > 1 else "/"
    candidate = urlunparse(parsed._replace(path=new_path))
    return candidate if candidate != url else None


_PAGING_PARAMS = ("p", "page", "pg", "pagenumber", "pagenum", "currentpage", "offset", "start", "from", "pagina", "seite")

#: F-38 (round 13): escalating polite cool-down steps (seconds) spent on
#: repeated refused advances over a FULL page - a bot edge rate-limit window
#: can outlast a single 15 s pause. Bounded: three pauses, 150 s total.
_COOLDOWN_STEPS_S = (15.0, 45.0, 90.0)


def _interruptible_page_wait(page, seconds: float, cancel_event=None) -> bool:
    """Wait politely in browser-time slices, but let Stop interrupt each slice.

    In particular, a single F-38 cool-down must not strand the user's Stop
    button for 15/45/90 seconds. Using the browser's clock also keeps Playwright
    responsive to a closed page. False means cancellation, not a paging error.
    """
    remaining_ms = max(0, round(float(seconds) * 1000))
    while remaining_ms:
        if cancel_event is not None and cancel_event.is_set():
            return False
        chunk_ms = min(500, remaining_ms)
        page.wait_for_timeout(chunk_ms)
        remaining_ms -= chunk_ms
    return cancel_event is None or not cancel_event.is_set()


def paging_href_candidates(page, current_url: str, tried: set) -> list:
    """Next-page URLs the board itself links to, ascending by paging value.

    Guide §12.14: follow the page's OWN hrefs. Numbered pagers only render a
    window of slots (Robert Walters shows 1..10 plus an arrow), so once the
    window ends the exact-number click finds nothing; the board's own anchors
    still carry the reachable next values. Only same-path URLs whose paging
    parameter moves forward are returned, and anything already tried is
    skipped. No host-name rules (guide §15.9).
    """
    cur = urlparse(current_url)
    cur_q = {k.casefold(): v[0] for k, v in parse_qs(cur.query).items()}
    cur_val = None
    for k in _PAGING_PARAMS:
        if k in cur_q:
            try:
                cur_val = int(cur_q[k])
            except ValueError:
                cur_val = None
            break
    out = []
    try:
        hrefs = page.eval_on_selector_all(
            "a[href], link[rel='next'][href]",
            "els => els.map(e => ({h: e.getAttribute('href'), rel: (e.getAttribute('rel') || '').toLowerCase(), active: [e,e.closest('li')].some(n=>n && (n.getAttribute('aria-current')==='page'||/\\b(active|current|selected)\\b/i.test(n.className||''))), disabled: [e,e.closest('li')].some(n=>n && (n.hasAttribute('disabled')||n.getAttribute('aria-disabled')==='true'))}))"
        )
    except Exception:
        return out
    rel_next = []
    active_positions = {_paging_position(urljoin(current_url,item.get('h')or''))
                        for item in hrefs or [] if isinstance(item,dict) and item.get('active')}
    active_positions.discard(None)
    if len(active_positions)>1:return []
    current_position = _paging_position(current_url) or next(iter(active_positions),None)
    cur_path_m = re.search(r"/page/(\d+)(?=/|$)", cur.path, re.I)
    cur_path_page = int(cur_path_m.group(1)) if cur_path_m else None
    for item in hrefs or []:
        if isinstance(item, dict):
            h = item.get("h") or ""
            rel = item.get("rel") or ""
            if item.get('active') or item.get('disabled'):continue
        else:
            h = str(item or "")
            rel = ""
        if not h or h.startswith(("javascript:", "#", "mailto:")):
            continue
        u = urljoin(current_url, h)
        if u in tried:
            continue
        pu = urlparse(u)
        if (pu.scheme, pu.netloc) != (cur.scheme, cur.netloc):
            continue
        candidate_query={k.casefold():v for k,v in parse_qs(pu.query,keep_blank_values=True).items()}
        if any(k not in _PAGING_PARAMS and candidate_query.get(k)!=v
               for k,v in ((k.casefold(),v)for k,v in parse_qs(cur.query,keep_blank_values=True).items())):
            continue
        target_position = _paging_position(u)
        if current_position and target_position and (current_position[0]!=target_position[0] or target_position[1]<=current_position[1]):
            continue
        if not _forward_paging_url(current_url, u):
            continue  # A rel=next label cannot make page3 forward from ?Page=13.
        base_path = re.sub(r"/page/\d+(?=/|$)", "", cur.path, flags=re.I).rstrip("/")
        candidate_base = re.sub(r"/page/\d+(?=/|$)", "", pu.path, flags=re.I).rstrip("/")
        if base_path == candidate_base and "next" in rel.split():
            rel_next.append(u)
        if base_path == candidate_base:
            pm = re.search(r"/page/(\d+)(?=/|$)", pu.path, re.I)
            if pm:
                pval = int(pm.group(1))
                if cur_path_page is None or pval > cur_path_page:
                    out.append((pval, u))
                    continue
        if base_path != candidate_base:
            continue
        q = {k.casefold(): v[0] for k, v in parse_qs(pu.query).items()}
        if any(k not in _PAGING_PARAMS and k in q and q[k]!=v for k,v in cur_q.items()):
            continue
        for k in _PAGING_PARAMS:
            if k not in q:
                continue
            try:
                val = int(q[k])
            except ValueError:
                break
            if u not in tried and (cur_val is None or val > cur_val):
                out.append((val, u))
            break
    out.sort()
    return list(dict.fromkeys(rel_next + [u for _, u in out]))


def _paging_position(url):
    parsed = urlparse(url)
    query = {k.casefold(): v[0] for k,v in parse_qs(parsed.query).items()}
    for key in _PAGING_PARAMS:
        if key in query:
            try:
                return (key if key in ('offset','start','from') else 'page', int(query[key]))
            except ValueError:
                return None
    match = re.search(r'/page/(\d+)(?=/|$)', parsed.path, re.I)
    return ('page', int(match[1])) if match else None


def _forward_paging_url(current, target):
    before, after = _paging_position(current), _paging_position(target)
    if before and after:
        return before[0] == after[0] and after[1] > before[1]
    return True  # Non-numeric Next remains governed by observed scope/DOM.


def _paging_value(u: str):
    """The paging value carried by a query or /page/N path, else None."""
    parsed = urlparse(u)
    q = {k.casefold(): v[0] for k, v in parse_qs(parsed.query).items()}
    for k in _PAGING_PARAMS:
        if k in q:
            try:
                return int(q[k])
            except ValueError:
                return None
    match = re.search(r"/page/(\d+)(?=/|$)", parsed.path, re.I)
    return int(match.group(1)) if match else None


_VISIBLE_LISTING_COUNT_RE = re.compile(
    r"\b[1-9]\d*\s+(?:job|jobs|job\s+results?|vacanc(?:y|ies)|vacatures?|"
    r"stellen(?:angebote)?|offres?|offerte|ofertas?|openings?|positions?|treffer|resultaten)\b",
    re.I,
)
_VISIBLE_JOB_HREF_RE = re.compile(
    r'href=["\'][^"\']*(?:/j/|/jobs?/|/vacanc(?:y|ies)/|/vacatures?/|/stellenangebote/|/offres?/)',
    re.I,
)


def _strip_invisible_markup(raw_html: str) -> str:
    """Remove <script>, <style>, and <noscript> blocks so passive anti-bot SDKs
    embedded in <head> (e.g. PerimeterX / Turnstile telemetry on normal listing
    pages) never masquerade as active interstitial challenge walls."""
    if not raw_html:
        return ""
    return re.sub(r"<(script|style|noscript)\b[^>]*>.*?</\1>", " ", raw_html, flags=re.I | re.S)


def _visible_document(raw: str) -> tuple[str, str]:
    markup = re.sub(r"<!--.*?-->", " ", _strip_invisible_markup(raw), flags=re.S).casefold()
    text = " ".join(unescape(re.sub(r"<[^>]+>", " ", markup)).split())
    return markup, text


def _html_has_live_listing(markup: str, text: str) -> bool:
    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', markup)
    jobs = {h for h in hrefs if re.search(
        r"/(?:j|jobs?|vacanc(?:y|ies)|vacatures?|stellenangebote|offres?|offerte|ofertas?)/[^/?#]+", h
    ) and not _is_save_action_url(h)}
    return len(jobs) >= 3


def _redact_evidence_url(url):
    return redact_url(url)


def _page_response_evidence(page):
    try:
        html, title = page.content() or '', page.title() or ''
    except Exception:
        html, title = '', ''
    try:
        status = int(getattr(page, '_assisted_nav_status', 0) or 0)
    except (TypeError, ValueError):
        status = 0
    try:
        frames = [str(frame.url) for frame in page.frames][:8]
    except Exception:
        frames = []
    dom_reason = challenge_reason(html, title=title, frame_urls=frames)
    if dom_reason:
        try:
            page._assisted_active_wall_seen = True
        except Exception:
            pass
    # A previous403 navigation is not the status of fresh DOM supplied after
    # genuine human clearance. Active DOM evidence always remains authoritative.
    hrefs = {href for href in re.findall(r'''(?i)href\s*=\s*["']([^"']+)''', html)
             if re.search(r'/(?:jobs?|vacancies|vacatures?)/[^/?#]+', href, re.I) and not _is_save_action_url(href)}
    cleared_dom = getattr(page, '_assisted_active_wall_seen', False) is True and len(hrefs) >= 3 and not dom_reason
    rendered=inspect_page(page,kind='listing',status=status,validator=getattr(page,'_assisted_job_validator',None))
    reason = rendered['reason']if rendered is not None else dom_reason or ('' if cleared_dom else challenge_reason(html, title=title, status=status, frame_urls=frames))
    return dict(html=html,title=title,status=status,frame_urls=frames,reason=reason,rendered=rendered)


def _page_is_challenge(page) -> bool:
    """Use the same active/passive response authority as regular Career/ATS."""
    return bool(_page_response_evidence(page)['reason'])


def _record_assisted_evidence(page, phase, page_num, *, count=None):
    evidence = _page_response_evidence(page)
    total = stated_job_total(evidence['html'])
    from datetime import datetime, timezone
    try:
        paging_links = [_redact_evidence_url(value) for value in paging_href_candidates(page, getattr(page, 'url', ''), set())[:8]]
    except Exception:
        paging_links = []
    pager = re.search(r'(?is)<(?:nav|div)\b[^>]*class=["\'][^"\']*pagination[^"\']*["\'][^>]*>(.*?)</(?:nav|div)>', evidence['html'])
    record = dict(captured_at=datetime.now(timezone.utc).isoformat(),
        pagination_text=redact_text(visible_text(pager[1]))[:400] if pager else '', forward_page_links=paging_links,
        page_parameter=_paging_value(getattr(page, 'url', '')), http_status_source='last_navigation',
        phase=phase, page=page_num,
        url=_redact_evidence_url(getattr(page, 'url', '')), title=redact_text(evidence['title'])[:200],
        http_status=evidence['status'], transport='browser', reason=redact_text(evidence['reason']),
        stated_total=total, visible_sample=redact_text(str(evidence['rendered'].get('body_preview')or'')if evidence.get('rendered')else visible_text(evidence['html']))[:600],
        visible_sample_source='computed_visible_dom'if evidence.get('rendered')else'html_stripped_fallback',
        rendered_state=public_snapshot(evidence.get('rendered')),
        frame_urls=[urlunparse(urlparse(redact_url(url))._replace(query='',fragment='')) for url in evidence['frame_urls']],
        failed_data_responses=list(getattr(page, '_assisted_failed_responses', [])),
        elapsed_seconds=round(time.monotonic()-LAST_INGESTION_REPORT.get('_started_monotonic', time.monotonic()),3))
    if count is not None:
        record['parsed_rows'] = count
    if phase.startswith('initial_'):
        LAST_INGESTION_REPORT.setdefault('initial_response',record)
    if record['reason']:
        LAST_INGESTION_REPORT['failure_response']=record
    if phase=='page_saved':
        from sponsorscout.core.assisted_resume import observed_url_in_scope
        candidates=[v for v in paging_links if observed_url_in_scope(LAST_INGESTION_REPORT.get('url',''),v)and _paging_value(v)is not None and (_paging_value(record['url'])is None or _paging_value(v)>_paging_value(record['url']))]
        if count:
            # Zero newly admitted rows cannot promote a replay to page N+1.
            LAST_INGESTION_REPORT.pop('continuation', None)
            if candidates:
                LAST_INGESTION_REPORT['continuation']=dict(url=candidates[0],page=page_num+1,source_url=record['url'],provenance='observed_forward_link',portal_key=portal_queue_key(LAST_INGESTION_REPORT.get('url','')),captured_at=record['captured_at'])
        summaries=LAST_INGESTION_REPORT.setdefault('page_summaries',[])
        if len(summaries)<1000:
            summaries.append({key:record.get(key)for key in ('captured_at','elapsed_seconds','page','page_parameter','url','parsed_rows','stated_total','http_status')})
        else:LAST_INGESTION_REPORT['page_summaries_truncated']=True
    history = LAST_INGESTION_REPORT.setdefault('response_evidence', [])
    history.append(record)
    del history[:-16]
    if total is not None:
        LAST_INGESTION_REPORT['stated_total'] = total
    LAST_INGESTION_REPORT['last_response'] = record
    return record


def _watch_assisted_responses(page):
    watch_page_responses(page)


def _verification_event(page,phase,reason=''):
    evidence=_page_response_evidence(page)
    from datetime import datetime,timezone
    record=dict(captured_at=datetime.now(timezone.utc).isoformat(),elapsed_seconds=round(time.monotonic()-LAST_INGESTION_REPORT.get('_started_monotonic',time.monotonic()),3),
        phase=phase,url=_redact_evidence_url(getattr(page,'url','')),status=evidence['status'],title=redact_text(evidence['title']),reason=redact_text(reason or evidence['reason']),evidence=public_snapshot(evidence.get('rendered')))
    counts=LAST_INGESTION_REPORT.setdefault('verification_counts',{});counts[phase]=counts.get(phase,0)+1
    events=LAST_INGESTION_REPORT.setdefault('verification_events',[]);events.append(record)
    if len(events)>256:events[:]=events[:32]+events[-224:]
    return record


def _gate_attention(page,waiting,reason=''):
    control=getattr(page,'_assisted_control',None)
    if control is not None:control.attention(dict(waiting=waiting,phase='listing',url=getattr(page,'url',''),reason=reason))


def _remember_blocked_listing(page,page_num):
    # Exactly the URL/state reached by the browser, not an inferred page query.
    from datetime import datetime,timezone
    target=getattr(page,'url','')
    from sponsorscout.core.assisted_resume import observed_url_in_scope
    prior_urls={item.get('url')for item in LAST_INGESTION_REPORT.get('page_summaries',[])}
    if is_web_url(target)and observed_url_in_scope(LAST_INGESTION_REPORT.get('url',''),target)and target not in prior_urls:
        LAST_INGESTION_REPORT['continuation']=dict(url=target,page=int(page_num),provenance='observed_browser',portal_key=portal_queue_key(LAST_INGESTION_REPORT.get('url','')),captured_at=datetime.now(timezone.utc).isoformat())


def _page_has_listing(page) -> bool:
    """A rendered listing, based on visible content rather than script payloads."""
    try:
        raw = page.content() or ""
        title = (page.title() or "").casefold()
    except Exception:
        return False
    evidence=_page_response_evidence(page)
    if evidence['reason']:return False
    if evidence.get('rendered')is not None:return bool(evidence['rendered'].get('listing_ready'))
    markup, text = _visible_document(raw)
    if _html_has_live_listing(markup, text) or _jobish_anchor_count(page) >= 3:
        return True
    # A genuine single-vacancy card is usable; a lone navigation anchor is not.
    try:
        for card in page.query_selector_all('article, .job-card, [class*=job-result]')[:5]:
            anchor = _card_detail_anchor(card)
            href = anchor.get_attribute('href') if anchor else ''
            if href and re.search(r'/(?:jobs?|vacancies|vacatures?)/[^/?#]+|/\d+-[^/?#]+\.html', href, re.I):
                title = (anchor.inner_text() or '').strip()
                if len(title) >= 3 and not is_noise_cta_title(title):
                    return True
    except Exception:
        pass
    return False


def _wait_for_clearance(page, wait_s: float = 180.0, cancel_event=None) -> bool:
    """Bounded current-DOM proof; commands never certify or extend a check."""
    announced=getattr(page,'_assisted_clearance_wait',None);page._assisted_clearance_wait=None
    deadline=announced if announced is not None else time.monotonic()+max(.1,wait_s)
    control=getattr(page,'_assisted_control',None)
    if announced is None:_verification_event(page,'verification_waiting');_gate_attention(page,True)
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():return False
            try:
                if page.is_closed():return False
            except Exception:return False
            command=control.poll(page)if control is not None else''
            if command=='save':_verification_event(page,'verification_saved_partial');return False
            if command=='check':_verification_event(page,'verification_check_requested')
            if not _page_is_challenge(page)and _page_has_listing(page):
                _verification_event(page,'verification_cleared');return True
            if time.monotonic()>=deadline:
                _verification_event(page,'verification_unresolved');return False
            delay=min(.5,max(.001,deadline-time.monotonic()))
            if callable(getattr(page,'wait_for_timeout',None)):
                page.wait_for_timeout(max(1,int(delay*1000)))
            else:
                time.sleep(delay)  # Non-browser adapters only.
    finally:_gate_attention(page,False)


def _recover_blank_page(page, current_url: str, page_num: int,
                        wait_s: float = 180.0,
                        progress_cb: Any = None, cancel_event=None) -> str:
    """F-31: a load produced *no* listing content at all - classify it.

    Mid-run this is what a re-engaging bot wall or a failed render looks like
    (your round-6 run: 70 jobs, then page 8 returned nothing and the harvest
    reported "no next-page control recognised", which describes the code, not
    the board). The assisted window is interactive, so a challenge gets the
    same human-clearance wait as the first page; anything else gets one honest
    reload. Returns "cleared" | "challenge" | "access" | "blank" | "cancelled" | "saved_partial".
    """
    if cancel_event is not None and cancel_event.is_set():
        return 'cancelled'
    evidence=_page_response_evidence(page)
    rendered=evidence.get('rendered')or{}
    if rendered.get('document_error'):
        _record_assisted_evidence(page,'document_error',page_num,count=0)
        return 'error'
    if rendered.get('rendered_unextractable'):
        _record_assisted_evidence(page,'rendered_unextractable',page_num,count=0)
        return 'unextractable'
    if _page_is_challenge(page):
        _record_assisted_evidence(page, 'waiting_for_clearance', page_num, count=0)
        logger.info("Bot protection re-engaged on page %d; waiting for human "
                    "clearance in the assisted window", page_num)
        try:
            page.bring_to_front()
        except Exception:
            pass
        page._assisted_clearance_wait=time.monotonic()+max(.1,wait_s)
        _verification_event(page,'verification_waiting');_gate_attention(page,True)
        if callable(progress_cb):
            try:
                reason = _page_response_evidence(page)['reason']
                message = (_translate("Access unavailable on page %d; waiting for current page recovery. No CAPTCHA was confirmed.")
                           if 'human verification not confirmed' in reason else
                           _translate("Verification pending on page %d — waiting for the page to clear. Interact only if the page requests it."))
                progress_cb(message % page_num)
            except Exception:
                pass
        if (_wait_for_clearance(page,wait_s,cancel_event=cancel_event) if cancel_event is not None
                else _wait_for_clearance(page,wait_s)):
            return "cleared"
        control=getattr(page,'_assisted_control',None)
        if control is not None and control.save_requested:return 'saved_partial'
        return "access" if 'human verification not confirmed' in _page_response_evidence(page)['reason'] else "challenge"
    _record_assisted_evidence(page, 'blank_before_reload', page_num, count=0)
    try:
        logger.info("Page %d rendered no listing content; reloading once", page_num)
        page.reload(wait_until="domcontentloaded", timeout=30000)
        time.sleep(3.0)
    except Exception:
        return "blank"
    if _page_is_challenge(page):
        _record_assisted_evidence(page, 'waiting_after_reload', page_num, count=0)
        logger.info("Reload of page %d surfaced a bot challenge; waiting for "
                    "human clearance", page_num)
        try:
            page.bring_to_front()
        except Exception:
            pass
        page._assisted_clearance_wait=time.monotonic()+max(.1,wait_s)
        _verification_event(page,'verification_waiting');_gate_attention(page,True)
        if callable(progress_cb):
            try:
                reason = _page_response_evidence(page)['reason']
                message = (_translate("Access unavailable on page %d; waiting for current page recovery. No CAPTCHA was confirmed.")
                           if 'human verification not confirmed' in reason else
                           _translate("Verification pending on page %d — waiting for the page to clear. Interact only if the page requests it."))
                progress_cb(message % page_num)
            except Exception:
                pass
        if (_wait_for_clearance(page,wait_s,cancel_event=cancel_event) if cancel_event is not None
                else _wait_for_clearance(page,wait_s)):
            return "cleared"
        control=getattr(page,'_assisted_control',None)
        if control is not None and control.save_requested:return 'saved_partial'
        return "access" if 'human verification not confirmed' in _page_response_evidence(page)['reason'] else "challenge"
    if _page_has_listing(page):
        return "cleared"
    _record_assisted_evidence(page, 'unreadable_after_reload', page_num, count=0)
    return "blank"


def _discover_listing_entry(page, *, max_hops=2, cancel_event=None):
    """Only observed same-origin search navigation; no live work outside Assisted."""
    visited={getattr(page,'url','')}
    for hop in range(max_hops):
        if cancel_event is not None and cancel_event.is_set():return
        evidence=_page_response_evidence(page);snapshot=evidence.get('rendered')or{}
        if (evidence.get('reason')or snapshot.get('document_error')or
                snapshot.get('listing_ready')or snapshot.get('rendered_unextractable')):return
        targets=[u for u in career_listing_links(snapshot)if u not in visited]
        if not targets:return
        target=targets[0];visited.add(target)
        _record_assisted_evidence(page,'career_entry_before_navigation',1,count=0)
        try:
            page.goto(target,wait_until='domcontentloaded',timeout=20000)
            page.wait_for_timeout(750)
        except Exception:
            return
        _record_assisted_evidence(page,'career_entry_navigation',1)


def _jobish_anchor_count(page) -> int:
    """Count listing-shaped anchors on the page.

    A bot-denial or consent shell carries at most one navigation anchor, so a
    bare ``a[href*='/jobs/']`` probe false-positives as "real job listings"
    (F-22, live-verified on a PerimeterX denial page 2026-10-22). Requiring
    several listing-shaped anchors keeps the check a universal failure class.
    """
    try:
        return len(page.query_selector_all(
            "article a[href], a[href*='/jobs/'], a[href*='/job/'], "
            "a[href*='/vacancies/'], a[href*='/vacancy/'], "
            "a[href*='/vacatures/'], a[href*='/vacature/'], "
            "a[href*='/stellenangebote/']"))
    except Exception:
        return 0


def next_page_url(current_url: str, page_num: int) -> str | None:
    """Generic query-parameter paging step (F-22 / F-25).

    Increments whichever paging parameter the board already uses, preserving
    that board's indexing scheme; only *probes* ``p`` when the URL carries no
    scheme of its own. A board that ignores the synthesised parameter is
    caught by the caller's no-progress stop — never by a host-name rule.
    """
    from sponsorscout.core.job_identity import split_career_posting
    if split_career_posting(current_url):return None
    parsed = urlparse(current_url)
    q = parse_qs(parsed.query)
    matched_key = next((k for k in q if k.casefold() in _PAGING_PARAMS), None)
    if matched_key is not None and matched_key.casefold() in {"offset", "start", "from"}:
        return None
    if matched_key is not None and matched_key.casefold() == "p":
        try:
            q[matched_key] = [str(int(q[matched_key][0]) + 1)]
        except ValueError:
            q[matched_key] = [str(page_num)]
    elif matched_key is not None:
        try:
            q[matched_key] = [str(int(q[matched_key][0]) + 1)]
        except ValueError:
            q[matched_key] = [str(page_num + 1)]
    else:
        pm = re.search(r"(/page/)(\d+)(?=/|$)", parsed.path, re.I)
        if pm:
            nxt_p = int(pm.group(2)) + 1
            new_path = parsed.path[:pm.start(2)] + str(nxt_p) + parsed.path[pm.end(2):]
            return urlunparse(parsed._replace(path=new_path))
        q["p"] = [str(page_num)]
    candidate = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
    return candidate if candidate != current_url else None


#: F-28 (2026-10-22): generic call-to-action / footer chrome that boards render
#: as anchors and that a listing harvest must never ingest as a vacancy
#: ("Create job alert", "Register your CV", "Salary Calculator", ... seen live
#: in a Robert Walters footer). Universal patterns, no host rules (guide 15.9).
_NOISE_CTA_RE = re.compile(
    r"^(create (a )?job alerts?|job alerts?|subscribe( to.*)?|register( your)? cv|"
    r"submit( your)? (cv|resume)|refer a friend|salary calculator|careers advice|"
    r"quick links|read more|more information|looking to hire\??|"
    r"looking for a job\??|save job|search jobs|view all (jobs|vacancies)|"
    r"load more|show more|cookies?( policy)?|privacy( policy)?|terms( of use)?|"
    r"imprint|contact( us)?|sitemap|newsletter|key contact|offices|our locations)"
    r"[\s!.?]*$",
    re.I)


#: F-33 (2026-10-22): URL-shape guards. A vacancy title on a chrome URL is still
#: chrome (your round-7 run: 172/172 complete but one row whose title read like a
#: job while its URL was not a job page). Generic patterns only - no host names
#: (guide §15.9).
_CHROME_PATH_RE = re.compile(
    r"/(job-alerts?|register-cv|submit-cv|upload-cv|refer-a-friend|"
    r"salary-calculator|careers-advice|quick-links|offices|our-locations|"
    r"about(-us)?|contact(-us)?|imprint|privacy|terms|sitemap|newsletter|"
    r"my-account|login|logout|register|signin|signup)(/|$|\.)", re.I)


def is_chrome_url(url: str) -> bool:
    """True when the path is portal chrome, whatever the anchor text says."""
    if explicit_detail_url(url):
        return False
    try:
        path = urlparse(url).path
    except Exception:
        return False
    return administrative_action(url) or bool(_CHROME_PATH_RE.search(path))


_JOBISH_QUERY_KEYS = {"id", "job", "jobid", "job_id", "vacancy", "vacancyid",
                      "req", "reqid", "posting", "postingid", "slug"}


def is_listing_self_link(url: str, listing_path: str) -> bool:
    """A same-path link is paging/filter chrome - unless a job-id query says
    otherwise (some ATSes serve details at the listing path with ``?jobId=``)."""
    try:
        pu = urlparse(url)
    except Exception:
        return False
    if (pu.path.rstrip("/") or "/") != listing_path:
        return False
    from sponsorscout.core.job_identity import explicit_detail_url
    if explicit_detail_url(url):return False
    keys = {k.lower() for k in parse_qs(pu.query)}
    return not (keys & _JOBISH_QUERY_KEYS)


_LOCALE_SEG_RE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$", re.I)


def normalize_job_url(url: str) -> str:
    """Use trusted posting identity; locale text or title alone is NOT proof."""
    from sponsorscout.core.job_identity import posting_identity
    return posting_identity(url) or url


def is_noise_cta_title(title: str) -> bool:
    """True for navigation/CTA chrome harvested by mistake as a vacancy."""
    return bool(title) and (administrative_action('',title) or bool(_NOISE_CTA_RE.match(title.strip())))


def is_filter_or_facet_title(title: str, url: str) -> bool:
    """Detect filter tags, facet headers, and category counts (e.g. 'Rest of Ireland (19)', 'Dublin North (3)').
    
    Portals render sidebar links with count badges like `Category (N)` or `Location (N)`
    which are facets, not job vacancies.
    """
    if not title:
        return True
    t = title.strip()

    # Match patterns like: "Rest of Ireland (19)", "Dublin North (3)", "Permanent (12)", "Full-Time (45)"
    if re.search(r"\(\d+\)$", t):
        return True

    # Check for known facet keywords
    low = t.lower()
    facet_words = {
        "rest of ireland", "dublin north", "dublin south", "dublin city centre",
        "filter results", "search results", "refine search", "view all", "load more",
        "show more", "select location", "all locations", "all jobs", "cookie policy",
        "privacy policy", "terms of use", "contact us", "save job", "view vacancy",
        "read more", "apply now", "share this job", "back to search",
    }
    if low in facet_words:
        return True

    # If URL is purely a facet query (e.g. `specialization=jobsroot&location=...` without a detail job slug)
    parsed = urlparse(url)
    q = parsed.query.lower()
    if ("location=" in q or "specialization=" in q or "category=" in q or "facet=" in q) and not any(
        seg in parsed.path.lower()
        for seg in ("/job/", "/jobs/", "/vacancy/", "/vacancies/", "/vacature/", "/vacatures/")
    ):
        return True

    return False


def _extract_title_from_url_slug(url: str) -> str:
    """Derive job title from clean URL slug."""
    try:
        path = urlparse(url).path.rstrip("/")
        slug = path.split("/")[-1]
        slug = re.sub(r"\.html?$", "", slug)
        slug = re.sub(r"^[0-9\-]+", "", slug)
        slug = re.sub(r"-[0-9]+$", "", slug)
        tokens = [t.capitalize() for t in slug.split("-") if t and not t.isdigit()]
        return " ".join(tokens)
    except Exception:
        return ""


_GENERIC_CARD_CTA_TITLES = frozenset({
    "search", "save job", "view vacancy", "view job", "details", "personal details",
    "read more", "apply", "apply now", "vacature bekijken", "bekijk vacature",
    "meer bekijken", "lees meer", "vacature opslaan", "stelle ansehen", "job ansehen",
    "mehr erfahren", "voir l'offre", "voir le poste", "ver oferta", "vedi offerta",
})


def _is_save_action_url(url: str) -> bool:
    if explicit_detail_url(url):
        return False
    path = urlparse(url).path.casefold()
    return administrative_action(url) or any(part in path for part in (
        "vacature-opslaan", "save-job", "save-vacancy", "job-opslaan", "/saved-jobs", "/bookmark",
    ))


def _harvest_utility_url(url):
    """Use the same strong-requisition precedence as the rendered census."""
    if administrative_action(url):return True
    if explicit_detail_url(url):return False
    return any(token in str(url).casefold()for token in (
        '/my-account/','/privacy','/terms','/contact','/about','personal-details',
        '/login','/register','specialization=jobsroot','vacature-opslaan',
        'save-job','save-vacancy','job-opslaan'))


def _card_detail_anchor(card):
    """Prefer the title's link over CTA/action anchors, irrespective of DOM order."""
    selectors = (
        "h2 a[href], h3 a[href], h4 a[href], a[class*='title'][href], [class*='title'] a[href]",
        "a[href*='/jobs/'], a[href*='/job/'], a[href*='/vacancies/'], a[href*='/vacancy/'], "
        "a[href*='/vacatures/'], a[href*='/vacature/']",
        "a[href]",
    )
    for selector in selectors:
        try:
            for anchor in card.query_selector_all(selector):
                href = anchor.get_attribute("href") or ""
                if href and not _is_save_action_url(href) and not is_chrome_url(href):
                    return anchor
        except Exception:
            continue
    return None


def _card_title(card, raw_title: str, full_url: str, scanner) -> str:
    title = scanner.clean_job_title(raw_title)
    if title and title.casefold() not in _GENERIC_CARD_CTA_TITLES:
        return title
    if card is not None:
        try:
            for heading in card.query_selector_all("h1, h2, h3, h4, [class*='title']"):
                candidate = scanner.clean_job_title((heading.inner_text() or "").strip())
                if candidate and candidate.casefold() not in _GENERIC_CARD_CTA_TITLES:
                    return candidate
        except Exception:
            pass
    return _extract_title_from_url_slug(full_url)


def _bounded_card_text(anchor, admitted_urls=()):
    """Return the individual job-card text containing ``anchor``.

    Some portals wrap every card in an outer element whose class also contains
    ``card``/``result``.  Using that outer element's ``inner_text()`` used to
    store the whole 10-card listing page in ``jobs.description`` for each job.
    Prefer a real per-posting container first; fail closed if none is found.
    """
    # One evaluated read is equivalent to the legacy handle/count/text
    # sequence below, but avoids four renderer round trips per posting.
    # Keep the handle path for adapters lacking evaluate(), not for empty
    # results: an empty string is the same deliberate fail-closed decision.
    try:
        text = anchor.evaluate("""(node, urls) => {
            const detail = "a[href*='/job'],a[href*='/jobs/'],a[href*='/vacanc']," +
                           "a[href*='/vacatur'],a[href*='/position']," +
                           "a[href*='/opportunit'],a[href*='/career/']";
            const admitted = new Set(urls);
            let current = node.parentElement;
            for (let depth = 0; current && depth < 12; depth++) {
                const found = current.querySelectorAll(detail);
                if (found.length > 1) return "";
                const text = (current.innerText || "").trim();
                if (found.length === 1 && text.length >= 25) {
                    if (urls.length && Array.from(current.querySelectorAll('a[href]'))
                        .filter(a => admitted.has(a.href)).length > 1) return "";
                    return text.length <= 4000 ? text : "";
                }
                current = current.parentElement;
            }
            return "";
        }""", list(admitted_urls))
        if isinstance(text, str):
            return text.strip() if len(text) <= 4000 else ""
    except Exception:
        pass
    try:
        handle = anchor.evaluate_handle("""node => {
            const detail = "a[href*='/job'],a[href*='/jobs/'],a[href*='/vacanc']," +
                           "a[href*='/vacatur'],a[href*='/position']," +
                           "a[href*='/opportunit'],a[href*='/career/']";
            let current = node.parentElement;
            for (let depth = 0; current && depth < 12; depth++) {
                const found = current.querySelectorAll(detail);
                if (found.length > 1) return null;   // outer multi-card listing
                const text = (current.innerText || "").trim();
                if (found.length === 1 && text.length >= 25) return current;
                current = current.parentElement;
            }
            return null;
        }""")
        element = handle.as_element() if handle is not None else None
        if element is None:
            return ""
        if admitted_urls:
            # The old selector counts only a few English-style detail paths.
            # A mixed-path wrapper can contain ONE /job/ link but many other
            # census-admitted postings. It must not leak them into this card.
            if element.evaluate("""(node, urls) => {
                const admitted = new Set(urls);
                return Array.from(node.querySelectorAll('a[href]'))
                    .filter(a => admitted.has(a.href)).length > 1;
            }""", list(admitted_urls)):
                return ""
        text = (element.inner_text() or "").strip()
        # Extra guard against a portal-specific outer container that slipped
        # through the single-detail-anchor check above.
        return text if len(text) <= 4000 else ""
    except Exception:
        pass
    return ""


def _bounded_admitted_card_text(anchor, admitted_urls):
    """Read a *single* rendered job card when the legacy path selectors miss it.

    `admitted_urls` is the current page's visible posting census, which has
    already passed the shared posting test/portal validator. A wrapper with
    two admitted postings cannot supply context to either job; a lone title
    without a per-posting container supplies no implied location. This is a
    bounded, page-local DOM read, not a new network request or URL guess.
    """
    if not admitted_urls:
        return ""
    try:
        handle = anchor.evaluate_handle(r"""(node, urls) => {
            const admitted = new Set(urls);
            if (!admitted.has(node.href) || node.closest('header, nav, footer, aside'))
                return null;
            const own = (node.innerText || "").trim();
            let current = node;
            // Some boards make the entire card one link. Its separately
            // rendered lines are still bounded to this one admitted posting,
            // even if the parent is an unsafe multi-posting wrapper.
            let fallback = own.length >= 25 && own.length <= 1200 && /\n/.test(own)
                ? node : null;
            for (let depth = 0; current && current !== document.body && depth < 10; depth++) {
                const found = Array.from(current.querySelectorAll('a[href]'))
                    .filter(a => admitted.has(a.href));
                if (found.length > 1) break; // never take a multi-posting wrapper
                if (found.length === 1 && found[0] === node) {
                    const text = (current.innerText || "").trim();
                    if (text.length > 4000) break;
                    // One long job title is not evidence for the place in a
                    // sibling card. Require text beyond the link before we
                    // accept a parent as the posting's own context.
                    if (text.length >= 25 && text.length > own.length + 4) {
                        if (current.matches('li, article, [role="listitem"], ' +
                                '[class*="card"], [class*="job"], [class*="result"], ' +
                                '[class*="vacancy"], [class*="position"], ' +
                                '[class*="offer"], [class*="row"]')) return current;
                        if (fallback === null && text.length <= 1200)
                            fallback = current; // nearest augmented ancestor only
                    }
                }
                current = current.parentElement;
            }
            return fallback;
        }""", list(admitted_urls))
        element = handle.as_element() if handle is not None else None
        if element is None:
            return ""
        text = (element.inner_text() or "").strip()
        return text if len(text) <= 4000 else ""
    except Exception:
        return ""


def _observed_card_location(scanner, card_text, title, seed_name):
    """Apply the existing card-place policy only to *this* posting's text.

    An unlabelled line must pass the scanner's established location sanitizer.
    Do not infer a country from the employer, URL, or a different posting.
    """
    loc_match = re.search(r"(?:Location|Locatie|Ort|Lieu|Ubicación|Localisation):\s*([^\n\r]+)",
                          card_text, re.I)
    if loc_match:
        return loc_match.group(1).strip()
    for line in card_text.splitlines():
        line_s = line.strip()
        if line_s and line_s != title:
            candidate = scanner._sanitize_job_location(line_s, seed_name)
            if candidate and candidate != "Unknown":
                return candidate
    return "Unknown"


def _defer_unattempted_details(rows, reason):
    """Mark not-yet-read JD rows with an auditable session-closed deferral.

    The Assisted JD loop can break when the listing browser page is closed.
    Rows after that break must not silently remain ``unattempted`` with no
    reason in the saved batch / CSV.  Existing explicit statuses are kept.
    """
    deferred = 0
    for row in rows:
        status = str(row.get("jd_read_status") or "").strip()
        if status and status != "unattempted":
            continue
        row["jd_read_status"] = "unattempted"
        row["jd_reason"] = str(reason)
        row["jd_defer_kind"] = "session_closed"
        deferred += 1
    return deferred


def _trusted_click_element(el) -> bool:
    """Use Playwright's trusted pointer input, never a synthetic DOM click."""
    try:
        el.scroll_into_view_if_needed(timeout=3000)
        try:
            el.hover(timeout=1500)
        except Exception:
            pass
        time.sleep(random.uniform(0.08, 0.20))
        el.click(timeout=5000, delay=random.randint(35, 95))
        return True
    except Exception:
        return False


def _trusted_forward_click(page, element):
    try:
        href = element.get_attribute('href') or ''
    except Exception:
        href = ''
    if href and not href.startswith(('#','javascript:')):
        target = urljoin(page.url, href)
        from sponsorscout.core.assisted_resume import observed_url_in_scope
        if not observed_url_in_scope(page.url, target) or not _forward_paging_url(page.url, target):
            return False
    return _trusted_click_element(element)


def _trusted_click_tagged(page) -> bool:
    try:
        el = page.query_selector("[data-ss-next='1']")
        return bool(el and _trusted_forward_click(page, el))
    except Exception:
        return False


def _listing_identity_snapshot(page):
    # Newly appended split cards need the existing trusted selection observer
    # before they can contribute URL identities (never use card text as an ID).
    from sponsorscout.core.split_careers import observed_split_cards
    observed_split_cards(page)
    snapshot=inspect_page(page,kind='listing',validator=getattr(page,'_assisted_job_validator',None)) or {}
    if not snapshot.get('ready') or snapshot.get('reason') or snapshot.get('document_error'):
        return snapshot,set()
    return snapshot,set(snapshot.get('posting_identities') or ())


def _first_listing_signature(page) -> str:
    try:
        _snapshot,identities=_listing_identity_snapshot(page)
        return '|'.join(sorted(identities))
    except Exception:
        return ''


_NATIVE_APPEND_JS = r"""() => {
 document.querySelectorAll('[data-ss-append]').forEach(e=>e.removeAttribute('data-ss-append'));
 const label=/^(?:mehr laden|meer laden|(?:load|show)(?: [1-9]\d{0,3})? more(?: positions| jobs| results)?)$/i;
 const visible=e=>{for(let n=e;n;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0)return false;}return Array.from(e.getClientRects()).some(r=>r.width>1&&r.height>1);};
 const candidates=[];
 for(const e of Array.from(document.querySelectorAll('button,a,[role="button"],input[type="button"],input[type="submit"],div,span')).slice(0,12000)){
  if(e.closest('header,nav,footer,aside,[class*="related" i],[class*="recommend" i]')||!visible(e))continue;
  if(!label.test((e.value||e.textContent||'').replace(/\s+/g,' ').trim()))continue;
  // Match the actual actionable node, not every ancestor/span with the label.
  const action=e.matches('button,a,[role="button"],input')||/button|load[-_]?more|more[-_]?jobs/i.test((e.className||'')+' '+(e.id||''));
  if(!action||e.querySelector('button,a,[role="button"],input'))continue;
  if(e.hasAttribute('disabled')||e.getAttribute('aria-disabled')==='true'||/\b(?:disabled|is-disabled)\b/.test(e.className||''))continue;
  const ownerForm=e.closest('form');
  // Search/listing forms are primary content too. Application/contact forms
  // remain excluded, and the bound may never escape a form to borrow cards.
  if(ownerForm&&ownerForm.querySelector('input[type="email"],input[type="file"],input[type="password"],textarea'))continue;
  let bounded=false;
  for(let n=e.parentElement,d=0;n&&n!==document.body&&d<7;n=n.parentElement,d++){
   if(n.querySelectorAll('[data-ss-primary-posting="1"]').length>=2){bounded=true;break;}
   if(n===ownerForm)break;
  }
  if(bounded)candidates.push(e);
 }
 if(candidates.length!==1)return {found:candidates.length>0,ambiguous:candidates.length>1};
 candidates[0].setAttribute('data-ss-append','1');return {found:true};
}"""


def _native_append_advance(page):
    """None: absent; False: precise unproven/denied result; True: retained-ID growth."""
    def finish(value,reason):
        page._ss_native_append_diagnostic=reason
        return value
    try:
        cancel = getattr(page, '_assisted_cancel_event', None)
        if cancel is not None and cancel.is_set():return finish(False,'cancelled before native append')
        snapshot,before_ids=_listing_identity_snapshot(page)
        state=page.evaluate(_NATIVE_APPEND_JS) or {}
        if not state.get('found'):return finish(None,'no enabled bounded primary append control')
        if state.get('ambiguous'):return finish(False,'ambiguous primary append controls')
        if snapshot.get('reason'):return finish(False,'current listing access wall')
        if snapshot.get('census_truncated'):return finish(False,'primary identity census truncated')
        if not before_ids:return finish(False,'empty admitted primary identity census')
        before='|'.join(sorted(before_ids))
        before_total=stated_job_total(page.content())
        button=page.query_selector('[data-ss-append="1"]')
        if button is None or not _trusted_click_element(button):return finish(False,'trusted append click failed')
        deadline=time.monotonic()+8.0
        while time.monotonic()<deadline:
            if cancel is not None and cancel.is_set():return finish(False,'cancelled during native append')
            if page.is_closed():return finish(False,'browser closed during native append')
            snapshot,after_ids=_listing_identity_snapshot(page)
            if snapshot.get('reason'):return finish(False,'current listing access wall after append')
            if snapshot.get('document_error'):return finish(False,'current listing document error after append')
            if snapshot.get('census_truncated'):return finish(False,'primary identity census truncated after append')
            if before_ids < after_ids:
                page._ss_native_append_proof=dict(url=page.url,stated_total=before_total,
                    before_signature=before,after_signature='|'.join(sorted(after_ids)))
                return finish(True,'retained primary identities and new posting URL observed')
            page.wait_for_timeout(100)
        return finish(False,'native append timed out without retained primary identity growth')
    except Exception:
        return finish(False,'native append observation failed')


def _partial_listing_detail_gate(page,known_gate=False,control=None,cancel_event=None):
    """Fresh current authority required; an append failure is not access clearance."""
    if known_gate:return 'listing verification not accepted; no automatic JD hand-off; resume explicitly'
    if control is not None and control.save_requested:return 'save partial requested; resume explicitly'
    if cancel_event is not None and cancel_event.is_set():return 'cancelled by user'
    try:
        if page.is_closed():return 'assisted browser closed; resume explicitly'
        snapshot=inspect_page(page,kind='listing',validator=getattr(page,'_assisted_job_validator',None)) or {}
        if snapshot.get('reason'):return 'current listing verification not accepted; no automatic JD hand-off; resume explicitly'
        if snapshot.get('document_error')or not snapshot.get('listing_ready'):
            return 'current listing readability unproven; resume explicitly'
    except Exception:return 'current listing readability unproven; resume explicitly'
    return ''


def _late_listing_advance(page,previous_signature):
    """A timed-out click/goto may have delivered real fresh cards meanwhile.

    Never trust URL-only/window-only changes or active challenge recommendations.
    Inspect actual new listing content before incorrectly declaring no Next.
    """
    try:
        signature=_first_listing_signature(page)
        return bool(signature and previous_signature and signature!=previous_signature
                    and not _page_is_challenge(page) and _page_has_listing(page))
    except Exception:
        return False


def _wait_for_page_advance(page, prev_url: str, prev_sig: str, max_wait_s: float = 8.0) -> bool:
    """Do not read a stale AJAX card list just because its URL already changed."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    deadline = time.monotonic() + max_wait_s
    advanced = False
    while time.monotonic() < deadline:
        try:
            cur_url = page.url
            cur_sig = _first_listing_signature(page)
            if cur_sig and cur_sig != prev_sig:
                advanced = True
                break
            if not prev_sig and cur_url != prev_url:
                advanced = True
                break
            if page.is_closed():
                break
        except Exception:
            break
        time.sleep(0.35)
    # Keep the deliberate human-like dwell on every page, including fast APIs.
    time.sleep(random.uniform(2.2, 3.8))
    return advanced


_PAGINATION_TARGET_JS = r"""(targetPageNum) => {
    document.querySelectorAll('[data-ss-next]').forEach(n => n.removeAttribute('data-ss-next'));
    const visible = el => !!el && !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    const disabled = el => {
        if (!el || !visible(el)) return true;
        return [el, el.closest('li, [role="listitem"]')].some(n => n && (
            n.hasAttribute('disabled') || n.getAttribute('aria-disabled') === 'true' ||
            /\b(disabled|is-disabled)\b/i.test(typeof n.className === 'string' ? n.className : '')
        ));
    };
    const active = el => [el, el.closest('li, [role="listitem"]')].some(n => n && (
        n.getAttribute('aria-current') === 'page' ||
        /\b(active|current|selected|is-active|page-current)\b/i.test(typeof n.className === 'string' ? n.className : '')
    ));
    const mark = el => { el.setAttribute('data-ss-next', '1'); return true; };
    const scopes = Array.from(document.querySelectorAll(
        '[class*="paginat" i], [class*="pager" i], nav[aria-label*="pag" i], ' +
        '[role="navigation"][aria-label*="pag" i], nav:has([aria-current="page"])'
    )).filter(visible);
    const isNext = el => {
        const label = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '') + ' ' +
                       (el.textContent || '') + ' ' + (typeof el.className === 'string' ? el.className : '')).trim();
        return el.getAttribute('rel') === 'next' || el.getAttribute('data-page-dir') === 'next' ||
            /\b(next|volgende|weiter|nächste|suivant|siguiente|successiv[aoe]?)\b/i.test(label) || /^[>›»→]$/.test(label);
    };
    const matchesPageNum = (el, zeroIndexedDataPage) => {
        const text = (el.textContent || '').trim();
        if (/^\d+$/.test(text)) return Number(text) === targetPageNum; // text is authoritative
        const dp = el.getAttribute('data-page');
        return dp !== null && /^\d+$/.test(dp) && Number(dp) === targetPageNum - (zeroIndexedDataPage ? 1 : 0);
    };
    for (const scope of scopes) {
        const links = Array.from(scope.querySelectorAll('a, button, [role="button"]'));
        const zeroIndexedDataPage = links.some(el => {
            const dp = el.getAttribute('data-page');
            const text = (el.textContent || '').trim();
            return dp === '0' || (dp !== null && /^\d+$/.test(dp) && /^\d+$/.test(text) && Number(dp) + 1 === Number(text));
        });
        for (const el of links) {
            if (!disabled(el) && !active(el) && matchesPageNum(el, zeroIndexedDataPage)) return mark(el);
        }
        for (const el of links) {
            if (!disabled(el) && !active(el) && isNext(el)) return mark(el);
        }
        // An unlabeled trailing SVG chevron in a windowed numbered pager.
        // Never treat a numeric last-page shortcut or ellipsis as Next.
        const last = links[links.length - 1];
        if (last && !disabled(last) && !active(last) && last.querySelector('svg') &&
            !/\d|previous|prev|vorige|zurück|précédent/i.test((last.textContent || '') + (last.getAttribute('aria-label') || '')) &&
            links.some(el => /^\d+$/.test((el.textContent || '').trim()))) return mark(last);
    }
    // Fallbacks must carry explicit navigation semantics or a paging href;
    // arbitrary numeric facet/count links are never candidates.
    for (const el of document.querySelectorAll('a[rel="next"], a[data-page-dir="next"], button[data-page-dir="next"]')) {
        if (!disabled(el) && !active(el)) return mark(el);
    }
    for (const el of document.querySelectorAll('a[href], button[data-page], a[data-page]')) {
        if (disabled(el) || active(el) || el.closest('aside, [class*="facet" i], [class*="filter" i]')) continue;
        const href = el.getAttribute('href') || '';
        if (!/[?&](p|page|pg|pagenumber|pagenum|currentpage|pagina|seite)=/i.test(href) &&
            !/\/page\/\d+/i.test(href) && !el.hasAttribute('data-page')) continue;
        if (matchesPageNum(el, false)) return mark(el);
    }
    return false;
}"""


def _advertised_forward_page(page, next_number: int) -> bool | None:
    """Whether the last real listing offered Next (None means unknown).

    A guessed query parameter is not a board-owned paging affordance. Check
    both numbered/JS controls and the board's own hrefs; a failed DOM read
    must preserve the original conservative retry policy, not imply no pager.
    """
    try:
        target = page.evaluate(_PAGINATION_TARGET_JS, next_number)
        if target:
            return True
        if paging_href_candidates(page, page.url, set()):
            return True
        # Some explicit Next buttons live outside recognised pager containers.
        controls = page.query_selector_all(
            "a[rel='next'], a[data-page-dir='next'], button[data-page-dir='next'], "
            "a[aria-label*='next' i], button[aria-label*='next' i], "
            "a[aria-label*='successiv' i], button[aria-label*='successiv' i], "
            "a[aria-label*='weiter' i], button[aria-label*='weiter' i], "
            "a[aria-label*='suivant' i], button[aria-label*='suivant' i]"
        )
        return any(control.is_visible() for control in controls)
    except Exception:
        return None


def _unadvertised_probe_replayed(*, synthetic: bool, replayed: bool,
                                  two_index_guesses: bool, census_complete: bool,
                                  forward_control: bool | None,
                                  last_page_evidence: dict | None,
                                  current_total: int | None, ingested: int,
                                  unmet_total_seen: bool = False,
                                  evidence_readable: bool = True,
                                  probe_controls_absent: bool = True) -> bool:
    """Both index-ambiguous guesses replayed an accounted page with no Next.

    `?p=1` may itself be page 1 on a one-indexed board; only `?p=2` after
    that replay can certify the *probe* was exhausted. New validated URLs,
    incomplete/capped censuses, a pager, an unreadable page or unmet totals
    preserve the original retry policy. This stops as partial, not complete.
    """
    if not (synthetic and replayed and two_index_guesses and census_complete
            and forward_control is False and not unmet_total_seen
            and evidence_readable and probe_controls_absent):
        return False
    if not isinstance(last_page_evidence, dict):
        return False
    if last_page_evidence.get('pagination_text') or last_page_evidence.get('forward_page_links'):
        return False
    return not any(isinstance(total, int) and total > ingested
                   for total in (last_page_evidence.get('stated_total'), current_total))


_PAGINATION_END_JS = r"""() => {
    const scopes = [...document.querySelectorAll('[class*="paginat" i], [class*="pager" i], nav[aria-label*="pag" i]'), ...Array.from(document.querySelectorAll('button,a,[role=button]')).filter(el=>/^(?:mehr laden|meer laden|(?:load|show)(?: [1-9]\d{0,3})? more(?: positions| jobs| results)?)$/i.test((el.textContent||'').trim())).map(el=>el.parentElement)];
    const nexts = [];
    const visible=el=>{for(let n=el;n;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}return Array.from(el.getClientRects()).some(r=>r.width>1&&r.height>1);};
    for (const scope of scopes) {
        for (const el of scope.querySelectorAll('a, button, [role="button"]')) {
            const text = (el.textContent || '').trim();
            const label = (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '') + ' ' +
                (typeof el.className === 'string' ? el.className : '');
            if (!visible(el)) continue;
            if (el.getAttribute('rel') === 'next' || /^[>›»→]$/.test(text) ||
                /\b(next|volgende|weiter|nächste|suivant|siguiente|successiv[aoe]?)\b/i.test(label) || /^(?:(?:load|show)(?: [1-9]\d{0,3})? more(?: positions| jobs| results)?|mehr laden|meer laden)$/i.test(text)) nexts.push(el);
        }
    }
    return nexts.length > 0 && nexts.every(el => [el, el.closest('li, [role="listitem"]')].some(n => n && (
        n.hasAttribute('disabled') || n.getAttribute('aria-disabled') === 'true' ||
        /\b(disabled|is-disabled)\b/i.test(typeof n.className === 'string' ? n.className : '')
    )));
}"""


_PAGER_STATE_JS = r"""() => {
    const roots = [...document.querySelectorAll('[class*="paginat" i], [class*="pager" i], nav[aria-label*="pag" i]')];
    const states = [];
    const disabled = el => [el, el.closest('li')].some(n => n && (n.hasAttribute('disabled') || n.getAttribute('aria-disabled')==='true' || /\b(disabled|is-disabled)\b/i.test(n.className||'')));
    const active = el => [el, el.closest('li')].some(n => n && (n.getAttribute('aria-current')==='page' || /(?:^|[^a-z])(active|current|selected)(?:$|[^a-z])/i.test(n.className||'')));
    for (const root of roots) {
        const nodes=[...root.querySelectorAll('a,button,[role="button"],span,li')];
        const numeric=nodes.filter(n => /^\d+$/.test((n.textContent||'').trim()));
        const numbers=[...new Set(numeric.map(n=>Number(n.textContent.trim())))];
        let current=[...new Set(numeric.filter(active).map(n=>Number(n.textContent.trim())))];
        // Some accessible pagers have no active/current CSS class. A board-
        // owned numeric href mapping to the actual page parameter is evidence,
        // unlike simply assuming the largest visible number is current.
        if (!current.length) {
            const here=new URL(location.href);
            const keys=['p','page','pg','pagenumber','pagenum','currentpage','pagina','seite'];
            for(const key of keys) {
                if(!here.searchParams.has(key))continue;
                const value=here.searchParams.get(key);
                const matches=numeric.filter(n=>{
                    const href=n.getAttribute('href');
                    if(!href)return false;
                    try {const target=new URL(href,location.href);return target.origin===here.origin && target.pathname===here.pathname && target.searchParams.get(key)===value;}catch(e){return false;}
                }).map(n=>Number(n.textContent.trim()));
                current=[...new Set(matches)];
                if(current.length)break;
            }
        }
        const buttons=[...root.querySelectorAll('a,button,[role="button"]')];
        const forward=buttons.filter(n=>{
            const text=(n.textContent||'').trim();
            const label=(n.getAttribute('aria-label')||'')+' '+(n.getAttribute('title')||'')+' '+(n.className||'');
            return n.getAttribute('rel')==='next' || /^[>›»→]$/.test(text) || /\b(next|volgende|weiter|nächste|suivant|siguiente|successiv[aoe]?)\b/i.test(label);
        });
        const last=buttons[buttons.length-1];
        const unidentifiedArrow=last && last.querySelector('svg') && !/\d|previous|prev|vorige|zurück|précédent|^[<‹«←]$/i.test((last.textContent||'').trim()+' '+(last.getAttribute('aria-label')||'')) && !disabled(last);
        if(numbers.length && current.length===1)states.push({current:current[0],maximum:Math.max(...numbers),numbers,enabled_forward:forward.some(n=>!disabled(n))||!!unidentifiedArrow,text:(root.innerText||'').slice(0,500)});
    }
    return states.find(s=>s.current===s.maximum && !s.enabled_forward) || states[0] || null;
}"""


def reconciled_offset_end(page, observed, previous, summaries, census_complete):
    """Certify an ordinary empty observed offset AFTER a fully accounted census."""
    if not census_complete or not isinstance(previous,dict) or len(summaries)<2:
        return None
    total=previous.get('stated_total')
    if not isinstance(total,int) or total<=0 or total!=observed:
        return None
    last=summaries[-1]
    if any(s.get('stated_total')!=total for s in summaries):return None
    if page.url not in (previous.get('forward_page_links') or []):return None
    try:
        from urllib.parse import urlsplit,parse_qs
        from sponsorscout.core.assisted_resume import observed_url_in_scope
        here=parse_qs(urlsplit(page.url).query);before=parse_qs(urlsplit(last['url']).query)
        if set(here.get('from',[]))!={str(total)}:return None
        offset=int(before.get('from',['0'])[0]);size=int(last.get('parsed_rows')or 0)
        if size<=0 or offset+size!=total or not observed_url_in_scope(last['url'],page.url):return None
        e=_page_response_evidence(page);r=e.get('rendered') or {}
        shell=' '.join(str(x)for x in r.get('headings',[]))+' '+str(e.get('title')or'')
        if (e.get('status')!=200 or e.get('reason') or not r or r.get('document_error')
                or r.get('visible_job_links') or r.get('failed_data_responses')
                or getattr(page,'_assisted_failed_responses',[]) or r.get('rendered_unextractable')
                or not re.search(r'(?i)search|jobs|stellenangebote|vacancies',shell)):
            return None
        current=stated_job_total(e.get('html')or'')
        if current is not None and current!=total:return None
        return dict(kind='observed_offset_boundary',stated_total=total,offset=total,last_complete_page=last.get('page'))
    except (ValueError,TypeError,KeyError):
        return None


def _verification_exit_reason(page,exit_kind='deadline',read_failures=0):
    try:
        if page.is_closed():return 'browser closed before verification accepted'
        snapshot=inspect_page(page,kind='listing',validator=getattr(page,'_assisted_job_validator',None))
    except Exception:snapshot=None
    if not snapshot:return 'current document observation unavailable; '+exit_kind
    if exit_kind=='readiness_without_listing':return 'readiness ended without a readable listing'
    if read_failures:return 'observation deadline reached with document read failures'
    return 'observation deadline reached with current verification unresolved'


def _blocking_listing_failures(values):
    """Only demonstrably event-collection fetches are non-job telemetry.

    Unknown failed requests remain blocking. No host allowlist and no blanket
    exemption for advertising domains or failed fetches.
    """
    from urllib.parse import urlsplit,parse_qs
    blocking=[]
    for item in values or []:
        try:
            p=urlsplit(str(item.get('url')or''));q=parse_qs(p.query)
            telemetry=(item.get('resource_type')in('fetch','xhr') and
                bool(re.search(r'/collect(?:/[0-9]+)?/?$',p.path)) and
                q.get('en')in(['page_view'],['gtag.config']))
        except Exception:telemetry=False
        if not telemetry:blocking.append(item)
    return blocking


def reconciled_native_append_end(page, observed, census_complete, harvested_urls):
    """Require a proved native append, current identity coverage AND terminal UI.

    Count equality alone, a failed census, a hidden listing or a merely failed
    click never proves an end. Missing native controls count only after a real
    successful append in this same listing document.
    """
    proof=getattr(page,'_ss_native_append_proof',None)
    if not census_complete or not isinstance(proof,dict) or proof.get('url')!=page.url:
        return None
    try:
        evidence=_page_response_evidence(page);rendered=evidence.get('rendered') or {}
        if (evidence.get('reason') or not rendered.get('listing_ready')
                or rendered.get('document_error') or rendered.get('census_truncated') or _blocking_listing_failures(rendered.get('failed_data_responses'))
                or _blocking_listing_failures(getattr(page,'_assisted_failed_responses',[])) or getattr(page,'_ss_split_errors',[])):
            return None
        total=stated_job_total(evidence.get('html')or'')
        ids={posting_identity(a.get('url','')) for a in rendered.get('admitted_anchors',[]) if a.get('url')}
        saved={posting_identity(value) for value in harvested_urls}
        if (not isinstance(total,int) or total<=0 or total!=observed or proof.get('stated_total')!=total
                or not ids or len(ids)>=180 or len(ids)!=total or ids!=saved
                or _first_listing_signature(page)!=proof.get('after_signature')):
            return None
        native=page.evaluate(_NATIVE_APPEND_JS) or {}
        if native.get('found') or paging_href_candidates(page,page.url,set()):return None
        state=page.evaluate(_PAGER_STATE_JS)
        if isinstance(state,dict) and (state.get('enabled_forward') or state.get('current')!=state.get('maximum')):
            return None
        return dict(kind='native_append_reconciled',stated_total=total,visible_identities=len(ids),
                    successful_native_append=True,native_control='absent_or_disabled')
    except Exception:
        return None


def terminal_listing_evidence(page,observed,genuine_on_page,*,census_complete=False,harvested_urls=(),current_urls=()):
    if not genuine_on_page or getattr(page,'_ss_split_errors',[]) or _page_is_challenge(page):
        return None
    native=reconciled_native_append_end(page,observed,census_complete,harvested_urls)
    if native:return native
    # A disappearing forward control can also be terminal, but only after
    # disjoint pages, stable totals and all posting identities reconcile.
    if census_complete and current_urls:
        from sponsorscout.scanning.listing_completion import (
            TERMINAL_CONTROLS_JS, record_census_page, reconcile_disappeared_pager)
        try:
            evidence = _page_response_evidence(page)
            rendered = evidence.get('rendered') or {}
            samples = getattr(page, '_ss_terminal_census', [])
            record_census_page(samples, current_urls, stated_job_total(evidence.get('html') or ''))
            page._ss_terminal_census = samples
            controls = page.evaluate(TERMINAL_CONTROLS_JS)
            candidate = reconcile_disappeared_pager(
                samples, harvested_urls, current_urls, controls,
                census_complete=census_complete,
                failed=bool(evidence.get('reason') or not rendered.get('listing_ready')
                            or rendered.get('document_error') or rendered.get('census_truncated')
                            or rendered.get('failed_data_responses')))
            if candidate:
                page.wait_for_timeout(250)
                # Both UI state and currently admitted identities must remain
                # stable; settling may reveal a spinner or another forward link.
                fresh = _page_response_evidence(page)
                fresh_rendered = fresh.get('rendered') or {}
                current_ids = {posting_identity(u) for u in current_urls}
                fresh_ids = {posting_identity(a.get('url', '')) for a in
                             fresh_rendered.get('admitted_anchors', []) if a.get('url')}
                if (page.evaluate(TERMINAL_CONTROLS_JS) == controls
                        and fresh_ids == current_ids and not fresh.get('reason')
                        and fresh_rendered.get('listing_ready')
                        and not fresh_rendered.get('document_error')
                        and not fresh_rendered.get('failed_data_responses')
                        and stated_job_total(fresh.get('html') or '') == candidate['stated_total']):
                    return candidate
        except Exception:
            pass  # Insufficient evidence remains partial, never completed.
    if _pagination_end_reached(page):
        total=stated_job_total(page.content())
        if total is not None and total!=observed:return None
        return {'kind':'disabled_next'}
    try:
        state=page.evaluate(_PAGER_STATE_JS)
        total=stated_job_total(page.content())
        forward=paging_href_candidates(page,page.url,set())
        if (isinstance(state,dict) and state.get('current')==state.get('maximum')
                and not state.get('enabled_forward') and not forward
                and total is not None and observed==total):
            return dict(state,kind='terminal_pager_reconciled',stated_total=total)
    except Exception:
        pass
    return None


def _pagination_end_reached(page) -> bool:
    try:
        return page.evaluate(_PAGINATION_END_JS) is True
    except Exception:
        return False


def _persist_assisted_job(conn,record,max_attempts=3):
    """Retry actual locked writes, promote to seen only AFTER a successful commit."""
    from sponsorscout.core.persistence import upsert_job
    from sponsorscout.core.job_identity import posting_identity
    for attempt in range(max_attempts):
        try:
            from sponsorscout.core.job_identity import find_existing_posting
            already = find_existing_posting(conn, record['url'])
            upsert_job(conn,record,commit=False)
            conn.commit()
            return bool(already)
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if 'locked' not in str(exc).casefold() or attempt+1 >= max_attempts:
                raise
            logger.warning('Assisted write is busy; retrying %s (%d/%d)',record['url'],attempt+1,max_attempts)
            time.sleep(0.15*(attempt+1))
        except Exception:
            conn.rollback()
            raise


def _assisted_error_rows(persistence_errors, browser_error, url, stop_reason, completed, cancelled):
    """Serialize actual incidents; cancellation is not a database/browser error."""
    rows = [{'error': 'database_write', 'url': item.get('url') or url,
             'detail': str(item.get('error') or '')} for item in persistence_errors]
    if browser_error:
        rows.append({'error': 'browser_session', 'url': url, 'detail': browser_error})
    if not completed and not cancelled and not rows:
        rows.append({'error': 'incomplete_session', 'url': url, 'detail': stop_reason})
    return rows


@ingestion_task
def run_assisted_ingestion(url: str, seed_name: str, conn, max_pages: int = 500,
                         clear_wait_s: float = 180.0,
                         db_path: str | None = None,
                         progress_callback: Any = None, cancel_event=None,
                         source_subtype: str = 'direct', resume_details: bool = False,
                         resume_listing: bool = False, resume_run_id: str = '', control=None, auto_continue=False,
                         refresh_descriptions: bool = False) -> int:
    """Launch an interactive (visible) browser session for the user to solve any
    human-verification challenge, then automatically harvest all job listings
    across pagination and ingest them directly into the SQLite database.

    Uses generic card and pagination rules. Genuine security challenges are
    left for the human in the visible browser; uncleared blocks stop honestly.
    Returns the number of newly ingested jobs.
    """
    LAST_INGESTION_REPORT.clear()
    LAST_INGESTION_REPORT.update(url=url,completed=False,persistence_errors=[])
    from sponsorscout.core.persistence import upsert_job
    from sponsorscout.core.location_country import country_from_location
    from sponsorscout.scanning.jd_support import (
        JDSupportDetector,
        detect_blue_card,
        VERDICT_YES,
        VERDICT_NO,
        VERDICT_UNKNOWN,
    )
    from sponsorscout.scanning.career.career_scanner import (
        CareerPortalScanner,
        extract_experience,
        apply_support_fp_guards,
    )

    LAST_INGESTION_REPORT.clear()
    LAST_INGESTION_REPORT.update(url=url,completed=False,persistence_errors=[])
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError('Playwright is not installed; assisted browser cannot start.') from exc

    source_subtype = 'recruiter' if source_subtype == 'recruiter' else 'direct'
    persistence_errors = []
    scanner = CareerPortalScanner(skip_preflight=True)
    scanner._current_seed_url=url
    detector = JDSupportDetector()
    ingested_count = 0
    new_count = 0             # F-36: rows this run actually inserted
    dup_count = 0             # F-36: re-seen rows (refreshed, not new)
    harvest_rows = []         # F-36: per-run job list for CSV parity
    last_good_count = 0       # F-35: jobs on the last complete page
    page_size_max = 0         # F-35: fullest page seen (full-page test)
    probes_left = 4           # F-35/F-37: shared end-of-board probe budget
    cooldowns_used = 0        # F-38: escalating cool-downs per stall
    fresh_streak = 0          # consecutive fresh pages to replenish stall budget on 100+ page boards
    recover_cleared = 0       # F-35b: bounded blank-page recoveries
    probe_pending_end = False
    from datetime import datetime as _dt, timezone as _tz
    run_started = _dt.now(_tz.utc)
    run_id = run_started.strftime('%Y%m%dT%H%M%S%f') + '_assisted'
    from sponsorscout.core.assisted_resume import load_batch,save_batch,existing_records,listing_continuation,observed_url_in_scope
    if auto_continue and not refresh_descriptions and not (resume_details or resume_listing):
        from sponsorscout.core.assisted_workflow import plan_collection
        plan = plan_collection(conn, url, seed_name, resume_run_id)
        resume_details, resume_listing = plan['mode']=='details', plan['mode']=='listing'
        resume_run_id = plan.get('run_id','')
        LAST_INGESTION_REPORT['workflow_plan'] = plan
        if callable(progress_callback):progress_callback(_translate(plan['reason']))
    if resume_details and resume_listing:raise ValueError('Choose one continuation mode')
    resume_batch=load_batch(conn,url,seed_name,run_id=resume_run_id or None)if(resume_details or resume_listing)else None
    if resume_batch is not None:
        refresh_descriptions = bool(resume_batch.get('refresh_descriptions', False))
    if db_path:
        from sponsorscout.core.saved_details import SavedDetailReader
        scanner._saved_jd_reader = SavedDetailReader(db_path, force_refresh=refresh_descriptions)
        scanner._saved_jd_reader.load_scopes([dict(careers_url=url,name=seed_name)], cancel_event)
    reader = getattr(scanner, '_saved_jd_reader', None)
    if resume_batch and resume_batch.get('refresh_started_at') and reader is not None:
        reader.policy_started_at = _dt.fromisoformat(resume_batch['refresh_started_at'])
    refresh_started_at = getattr(reader, 'policy_started_at', getattr(reader, 'started_at', None))
    refresh_started_at = refresh_started_at.isoformat() if refresh_started_at else ''
    continuation_state=listing_continuation(resume_batch)if resume_listing else None
    known_listing_gate=False
    checkpoint_error = ''
    saved_listing_complete = False
    LAST_INGESTION_REPORT['_started_monotonic'] = time.monotonic()
    seen_urls = set()
    seen_norm = set()          # F-33: locale-variant-insensitive dedup key
    qualified_pages = set()   # genuine snapshots, never a probe/window-only move
    country_resolver = None
    pending_details = []
    detail_interrupted = False
    detail_interrupt_reason = ''
    detail_interrupted_count = 0
    detail_enricher = None
    from sponsorscout.core.session_details import SessionDetailEnricher,FLOW_REVISION
    from sponsorscout.core.support_application import apply_support_to_job
    LAST_INGESTION_REPORT['support_flow_revision']=FLOW_REVISION
    LAST_INGESTION_REPORT['export_schema']=5
    LAST_INGESTION_REPORT['detail_parallel_tabs']=min(3,max(1,int(getattr(scanner.config,'ASSISTED_DETAIL_TABS',3)or 3)))
    LAST_INGESTION_REPORT['http_reprobe_enabled']=bool(getattr(scanner.config,'ASSISTED_HTTP_REPROBE',True))
    LAST_INGESTION_REPORT['resumed_details']=bool(resume_details)
    LAST_INGESTION_REPORT['resumed_listing']=bool(resume_listing)
    listing_path = urlparse(url).path.rstrip("/") or "/"  # F-33

    class _AssistedSessionStop(Exception):
        """Expected early stop; use the same durable finalization as a harvest."""

    browser = context = None
    browser_error = ''
    pages_visited = loads = last_good_page = 0
    stop_reason = 'browser stopped before a complete listing'
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=False,args=[
                    '--disable-blink-features=AutomationControlled','--disable-gpu','--start-maximized'])
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                    no_viewport=True)
                page = context.new_page()
                _watch_assisted_responses(page)
                page._assisted_control=control
                page._assisted_cancel_event=cancel_event
                page._assisted_job_validator=getattr(scanner,'is_valid_job_url',None)
                LAST_INGESTION_REPORT['browser_version']=str(getattr(browser,'version',''))[:120]
                from sponsorscout.core.country_details import SessionCountryResolver
                country_resolver = SessionCountryResolver(context,scanner,url)
                detail_enricher = SessionDetailEnricher(context,scanner,url,conn=conn,cancel_event=cancel_event,
                    progress=progress_callback,clear_wait_s=clear_wait_s,control=control)

                if resume_batch is not None:
                    pending_details=existing_records(conn,resume_batch)
                    reader = getattr(scanner, '_saved_jd_reader', None)
                    if callable(getattr(reader, 'observe', None)):
                        for _record, _row in pending_details:
                            reader.observe(_row['url'], title=_row.get('title',''))
                    harvest_rows=[row for _record,row in pending_details]
                    ingested_count=len(harvest_rows);dup_count=ingested_count
                    source_subtype=resume_batch.get('source_subtype',source_subtype)
                    last_good_page=pages_visited=int(resume_batch.get('pages',0))
                    qualified_pages.update(range(1,pages_visited+1))
                    stop_reason='saved listing complete'if resume_batch.get('listing_completed')else'saved listing remains incomplete'
                    seen_urls.update(row['url']for row in harvest_rows)
                    seen_norm.update(normalize_job_url(row['url'])for row in harvest_rows)
                    if resume_batch.get('stated_total')is not None:LAST_INGESTION_REPORT['stated_total']=resume_batch['stated_total']
                    LAST_INGESTION_REPORT['continuation']=dict(resume_batch.get('continuation')or{})
                    if resume_batch.get('listing_completed'):LAST_INGESTION_REPORT['stated_total']=ingested_count
                if not resume_details:
                    try:
                        page.goto(continuation_state['url']if continuation_state else url, wait_until="domcontentloaded", timeout=60000)
                    except Exception as e:
                        logger.warning("Initial navigation error in assisted session: %s", e)

                    # Observe automatic or interactive verification within the existing budget.
                    poll_start = time.monotonic()
                    challenge_seen = False
                    initial_block_reason = ''
                    cleared = False
                    verification_exit='deadline'
                    verification_read_failures=0
                    while time.monotonic() - poll_start < max(0.1, clear_wait_s):
                        if cancel_event is not None and cancel_event.is_set():
                            break
                        command=control.poll(page)if control is not None else''
                        if command=='save':break
                        if command=='check':_verification_event(page,'verification_check_requested')
                        try:
                            if page.is_closed():
                                verification_exit='browser_closed'
                                break
                            content = (page.content() or "").casefold()
                            page_title = (page.title() or "").casefold()
                        except Exception:
                            verification_read_failures+=1
                            time.sleep(1)
                            continue

                        initial_snapshot=inspect_page(page,kind='listing',validator=getattr(page,'_assisted_job_validator',None))or{}
                        if initial_snapshot.get('document_error'):
                            _record_assisted_evidence(page,'initial_document_error',1,count=0)
                            raise _AssistedSessionStop(initial_snapshot['document_error']+'; requested='+url+'; final='+page.url)
                        if initial_snapshot.get('rendered_unextractable'):
                            break
                        if career_listing_links(initial_snapshot) and not initial_snapshot.get('listing_ready'):
                            break
                        # Legacy non-browser adapters: explicit error title fallback.
                        # Handle 404
                        is_404 = (
                            "404 not found" in page_title or
                            ("not found" in page_title and "404" in content) or
                            "the requested url was not found on this server" in content
                        )
                        if is_404:
                            # Generic failure class: a locale-prefixed path that 404s while
                            # the same board answers without the locale segment. No
                            # host-name rules here (guide §15.9, F-25).
                            fixed_url = strip_locale_segment(page.url)
                            if fixed_url:
                                try:
                                    page.goto(fixed_url, wait_until="domcontentloaded", timeout=45000)
                                    time.sleep(2)
                                    continue
                                except Exception:
                                    pass

                        # Detect anti-bot challenges
                        is_challenge = _page_is_challenge(page)

                        if is_challenge:
                            if not challenge_seen:
                                initial_block_reason = _record_assisted_evidence(page, 'initial_verification', int(continuation_state['page'])if continuation_state else 1, count=0)['reason']
                                try:
                                    page.bring_to_front()
                                except Exception:
                                    pass
                                _verification_event(page,'verification_waiting');_gate_attention(page,True,initial_block_reason)
                                if callable(progress_callback):
                                    try:
                                        progress_callback(_translate("Access or verification pending in Chromium. Allow automatic checks to finish; interact only if the page requests it. An Access Denied page may offer no check."))
                                    except Exception:
                                        pass
                            challenge_seen = True
                            time.sleep(.5)
                            continue

                        # A blank/rendering failure is not a180-second human wait.
                        if not challenge_seen and time.monotonic()-poll_start >= min(20.0, max(.1, clear_wait_s)):
                            verification_exit='readiness_without_listing'
                            break
                        # Real job listings present
                        has_jobs = _page_has_listing(page)

                        if has_jobs:
                            cleared = True
                            break

                        time.sleep(1.0)

                    if cancel_event is not None and cancel_event.is_set():
                        raise _AssistedSessionStop('cancelled by user')
                    # The final polling sleep can cross the deadline after a
                    # human already cleared the document. Inspect the current DOM
                    # once, rather than throwing away a real last-interval clearance.
                    if challenge_seen and not cleared:
                        try:
                            cleared = not page.is_closed() and not _page_is_challenge(page) and _page_has_listing(page)
                        except Exception:
                            cleared = False
                    if cleared and challenge_seen:_verification_event(page,'verification_cleared');_gate_attention(page,False)
                    if control is not None and control.save_requested:raise _AssistedSessionStop('saved partial during verification; resume explicitly')
                    if challenge_seen and not cleared:
                        exit_reason=_verification_exit_reason(page,verification_exit,verification_read_failures)
                        LAST_INGESTION_REPORT['verification_exit']=dict(reason=exit_reason,
                            elapsed_seconds=round(time.monotonic()-poll_start,3),configured_wait_seconds=clear_wait_s,
                            document_read_failures=verification_read_failures)
                        terminal_block_reason = _record_assisted_evidence(page, 'terminal_verification', int(continuation_state['page']) if continuation_state else 1, count=0)['reason']
                        _verification_event(page,'verification_unresolved');_gate_attention(page,False)
                        reason = (('access denied in the current browser document; no readable listing recovered within the wait window; a solvable human-verification challenge was not confirmed')
                                  if ('access denied' in terminal_block_reason.lower() or 'access-denied' in terminal_block_reason.lower() or 'human verification not confirmed' in terminal_block_reason) else
                                  'access verification remained unresolved; no readable current listing accepted within the wait window')
                        reason+='; verification exit: '+exit_reason+('; observed=%.2fs; configured=%.2fs; document_read_failures=%d' %
                            (time.monotonic()-poll_start,clear_wait_s,verification_read_failures))
                        logger.info('access verification unresolved: %s (%s)',reason,url)
                        raise _AssistedSessionStop(reason)
                    if page.is_closed():
                        raise _AssistedSessionStop('browser closed by user before listings appeared')

                    if not cleared and not resume_listing:
                        _discover_listing_entry(page,cancel_event=cancel_event)
                        entry_snapshot=inspect_page(page,kind='listing',validator=getattr(page,'_assisted_job_validator',None))or{}
                        if entry_snapshot.get('document_error'):
                            raise _AssistedSessionStop(entry_snapshot['document_error']+'; requested='+url+'; final='+page.url)
                        listing_path=urlparse(page.url).path.rstrip('/')or'/'
                    if resume_listing and not observed_url_in_scope(url,page.url):
                        raise _AssistedSessionStop(_translate('Listing continuation left the selected portal filters; out-of-scope rows were not accepted.'))
                    _record_assisted_evidence(page, 'initial_listing', int(continuation_state['page'])if continuation_state else 1)
                    # Multi-page vacancy crawler across all pagination
                    page_num = int(continuation_state['page'])if continuation_state else 1
                    current_page_url = page.url
                    tried_urls = {page.url}
                    recent_raws = []          # F-33b: last few listing snapshots, to catch
                                              # clamp oscillation (A,B,A,B...) as re-serves
                    mis_advances = 0
                    last_good_page = int(resume_batch.get('pages',0))if resume_listing else 0 # highest qualified page
                    # C17: a *guessed* ?p=N is not a site-provided paging link. Remember
                    # the last real page's evidence across refused loads; only an exact
                    # replay of a completely accounted listing can short-stop the guess.
                    last_good_census_complete = False
                    last_good_paging_evidence = None
                    last_good_forward_control = None
                    last_advance_was_synthetic = False
                    synthetic_replays = 0
                    synthetic_chain_unmet_total = False
                    synthetic_chain_readable = True
                    synthetic_chain_census_complete = True
                    synthetic_chain_no_forward = True
                    force_url_advance = False
                    consecutive_empty_pages = 0
                    pages_visited = len(qualified_pages)
                    loads = 0                 # F-34: raw browser loads, incl. retry/
                                              # oscillation phantoms; reported separately
                    stop_reason = "page cap reached"

                    while page_num <= max_pages and not page.is_closed():
                        if cancel_event is not None and cancel_event.is_set():
                            stop_reason = 'cancelled by user'
                            break
                        if control is not None and control.save_requested:
                            stop_reason='saved partial during verification; resume explicitly';break
                        loads += 1
                        try:
                            page.wait_for_timeout(random.randint(1600, 2900))
                        except Exception:
                            break

                        # Classify BEFORE extracting cards: active walls often
                        # contain recommended jobs, which must not be harvested.
                        if _page_is_challenge(page):
                            _remember_blocked_listing(page,page_num)
                            options = {'progress_cb': progress_callback}
                            if cancel_event is not None:
                                options['cancel_event'] = cancel_event
                            outcome = _recover_blank_page(page, current_page_url, page_num, clear_wait_s, **options)
                            _record_assisted_evidence(page, 'foreground_' + outcome, page_num, count=0)
                            if outcome == 'cleared':
                                recover_cleared += 1
                                if recover_cleared <= 2:
                                    continue
                            known_listing_gate=True
                            if control is not None and control.save_requested:
                                stop_reason='saved partial during verification; resume explicitly'
                            elif outcome == 'cancelled' or (cancel_event is not None and cancel_event.is_set()):
                                stop_reason = 'cancelled by user'
                            elif outcome == 'access':
                                stop_reason = 'access blocked on page %d; no readable listing recovered (last complete page %d)' % (page_num, last_good_page)
                            else:
                                stop_reason = 'bot protection re-engaged on page %d; no readable current listing accepted within the wait window (last complete page %d)' % (page_num, last_good_page)
                            break
                        if resume_listing and not observed_url_in_scope(url,page.url):
                            stop_reason=_translate('Listing continuation left the selected portal filters; out-of-scope rows were not accepted.');break
                        # Issue 005 P1: ask the same page what IT thinks the postings are
                        # before harvesting. `inspect_page` already admits visible anchors
                        # through the shared posting test; that census is the only evidence
                        # able to tell "the board has no listings" apart from "our filters
                        # rejected every link on a rendered board".
                        from sponsorscout.core.split_careers import observed_split_cards
                        native_jobs=observed_split_cards(page,cancel_event=cancel_event)
                        _census_links, _census_meta = [], {}
                        try:
                            _census_links, _census_meta = admitted_job_links(
                                page, validator=getattr(scanner, 'is_valid_job_url', None))
                        except Exception:
                            _census_links, _census_meta = [], {}
                        _census_ids = {posting_identity(str(a.get('url') or ''))
                                       for a in _census_links if isinstance(a, dict)}
                        # C19: carry only links the rendered page actually
                        # admitted, never infer another posting from a host or
                        # an English-only detail-path selector.
                        _census_urls = tuple(str(a.get('url') or '') for a in _census_links
                                             if isinstance(a, dict) and a.get('url'))
                        _rej = {'chrome': 0, 'self_link': 0, 'save_action': 0, 'blocklist': 0,
                                'facet_or_cta_title': 0, 'no_title': 0, 'not_a_posting': 0,
                                'duplicate': 0, 'not_census_no_path_token': 0,
                                'no_detail_anchor': 0, 'no_href': 0, 'chrome_or_self_link': 0,
                                'card_error': 0}

                        def _census_note(accepted):
                            _tally = ', '.join('%s=%d' % (k, v)
                                               for k, v in sorted(_rej.items()) if v)
                            return ('harvest census: cards=%d visible_posting_links=%d '
                                    'accepted=%d%s; page rendered as %s%s'
                                    % (len(cards), len(_census_links), accepted,
                                       ', rejected ' + _tally if _tally else '',
                                       _census_meta.get('kind') or 'unknown',
                                       ' — ' + str(_census_meta.get('reason'))[:120]
                                       if _census_meta.get('reason') else '')+'; census stages='+str(_census_meta.get('census_stages')or{})+
                                    '; admitted sample='+str([{'url':redact_url(a.get('url','')),'title':redact_text(a.get('title',''))[:120]}for a in _census_links[:3]]))

                        try:
                            cards = page.query_selector_all(
                                "article, .job-result, [class*='job-result'], [class*='search-result'], "
                                "[class*='card-job'], [class*='job-card'], [class*='vacancy-card'], "
                                "li:has(a[href*='/jobs/']), li:has(a[href*='/job/']), "
                                "li:has(a[href*='/vacancies/']), li:has(a[href*='/vacancy/']), "
                                "li:has(a[href*='/vacatures/']), li:has(a[href*='/vacature/'])"
                            )
                        except Exception:
                            cards = []

                        # Explicit URL-bearing non-anchor cards admitted by the
                        # current primary census are real observed links too.
                        # Never manufacture URLs from their title or event code.
                        for item in _census_links:
                            title=str(item.get('title')or'').strip()
                            if (item.get('dom_url_card') and not _census_meta.get('reason')
                                    and len(title)>=3 and not is_noise_cta_title(title)
                                    and not is_filter_or_facet_title(title,item['url'])):
                                native_jobs.append((title,item['url'],title))
                        page_jobs = [item for item in native_jobs if normalize_job_url(item[1]) not in seen_norm and item[1] not in seen_urls]
                        page_seen_raw = {item[1] for item in native_jobs}
                        page_candidate_norm = {normalize_job_url(item[1]) for item in page_jobs}  # in-page extraction dedup, not persisted yet

                        if cards:
                            for card in cards:
                                try:
                                    a_tag = _card_detail_anchor(card)
                                    if not a_tag:
                                        # Issue 005 P6: counted, not silent — "cards matched but
                                        # nothing was taken" must say which gate stopped it.
                                        _rej['no_detail_anchor'] += 1
                                        continue
                                    href = a_tag.get_attribute("href")
                                    if not href:
                                        _rej['no_href'] += 1
                                        continue
                                    full_url = urljoin(current_page_url, href)
                                    # F-33: chrome URLs and paging/self links are never jobs;
                                    # locale variants of an already-seen job are duplicates.
                                    norm_url = normalize_job_url(full_url)
                                    if (is_chrome_url(full_url)
                                            or is_listing_self_link(full_url, listing_path)):
                                        _rej['chrome_or_self_link'] += 1
                                        continue

                                    # Disqualify non-job portal links before recording page_seen_raw
                                    if _harvest_utility_url(full_url):
                                        _rej['blocklist'] += 1
                                        continue

                                    # Only skip URLs this very page has already
                                    # admitted; historical/uncensused links still
                                    # pass the full title/context admission path.
                                    if full_url in page_seen_raw:
                                        _rej['duplicate'] += 1
                                        continue

                                    # Bound context to this posting's own card.
                                    # An outer listing container can otherwise
                                    # leak unrelated jobs into description and
                                    # support detection for every row.
                                    card_text = (_bounded_card_text(a_tag, _census_urls)
                                                 or _bounded_admitted_card_text(a_tag, _census_urls))
                                    raw_title = (a_tag.inner_text() or "").strip()
                                    title = _card_title(card, raw_title, full_url, scanner)

                                    # Filter tags/count badges/facet buttons after CTA-title
                                    # recovery, not before it can read the actual heading.
                                    if is_filter_or_facet_title(title, full_url):
                                        _rej['facet_or_cta_title'] += 1
                                        continue

                                    if (not title or len(title) < 3
                                            or is_filter_or_facet_title(title, full_url)
                                            or is_noise_cta_title(title)):
                                        _rej['no_title'] += 1
                                        continue

                                    if not posting_candidate(full_url,title,getattr(scanner,'is_valid_job_url',None)):
                                        _rej['not_a_posting'] += 1
                                        continue
                                    page_seen_raw.add(full_url)
                                    if norm_url in seen_norm or full_url in seen_urls or norm_url in page_candidate_norm:
                                        _rej['duplicate'] += 1
                                        continue
                                    page_candidate_norm.add(norm_url)
                                    page_jobs.append((title, full_url, card_text))
                                except Exception:
                                    _rej['card_error'] += 1
                                    continue
                        # Issue 005 P6 (C14): the sweep used to be the `else` of `if cards`,
                        # so a page whose *whole list* sat inside one element matching the
                        # container selector never reached it — McDonald's Italia on 2026-10-29
                        # reported `harvest census: cards=1 visible_posting_links=164
                        # accepted=0`: one card yields one anchor, 163 links went unread, and the
                        # board was still written off as a render failure. Trigger the sweep on
                        # what the render admitted versus what we took, not on whether a
                        # container matched. A board Strategy 1 already satisfied is untouched.
                        # Strategy 2: Fallback to all detail anchors
                        try:
                            anchors = page.query_selector_all("a[href]")
                        except Exception:
                            break

                        for a in anchors:
                            try:
                                href = a.get_attribute("href")
                                if not href:
                                    continue
                                full_url = urljoin(current_page_url, href)
                                # F-33: chrome URLs and paging/self links are never jobs;
                                # locale variants of an already-seen job are duplicates.
                                norm_url = normalize_job_url(full_url)
                                if is_chrome_url(full_url):
                                    _rej['chrome'] += 1
                                    continue
                                if is_listing_self_link(full_url, listing_path):
                                    _rej['self_link'] += 1
                                    continue

                                if _is_save_action_url(full_url):
                                    _rej['save_action'] += 1
                                    continue
                                if _harvest_utility_url(full_url):
                                    _rej['blocklist'] += 1
                                    continue

                                # The card strategy may already have admitted
                                # this exact URL. Re-reading its title, ancestor
                                # and context in the fallback sweep is redundant.
                                if full_url in page_seen_raw:
                                    _rej['duplicate'] += 1
                                    continue

                                is_detail = (
                                    "/jobs/" in full_url or "/vacancies/" in full_url or
                                    "/vacancy/" in full_url or "/vacatures/" in full_url or
                                    "/vacature/" in full_url or "/job/" in full_url or
                                    "/stellenangebote/" in full_url or "/offres/" in full_url or
                                    "/offerte/" in full_url or "/ofertas/" in full_url or
                                    bool(re.search(r"/\d+-[a-z0-9\-]+\.html", full_url, re.I)) or
                                    bool(re.search(r"-[a-z0-9]+-\d+-\d+$", full_url, re.I))
                                )

                                # Issue 005 P1: `is_detail` above is a ten-token path list
                                # (/jobs/, /vacancies/, /offerte/ …), so a board posting
                                # under, e.g., /lavora-con-noi/posizioni-aperte/ lost every
                                # visible link and was reported as "render failure or
                                # undetected bot wall". The render census already admitted
                                # those links with the shared posting test, so accept on
                                # census admission as well; the token list stays as a second
                                # path, which keeps every board that used to work working.
                                if not (is_detail
                                        or posting_identity(full_url) in _census_ids
                                        or posting_identity(norm_url) in _census_ids):
                                    _rej['not_census_no_path_token'] += 1
                                    continue

                                raw_text = (a.inner_text() or "").strip()
                                parent_card = None
                                try:
                                    handle = a.evaluate_handle("node => node.closest('article, li, [class*=card], [class*=result]') || node.parentElement")
                                    parent_card = handle.as_element() if handle else None
                                except Exception:
                                    pass
                                title = _card_title(parent_card, raw_text, full_url, scanner)

                                if is_filter_or_facet_title(title, full_url):
                                    _rej['facet_or_cta_title'] += 1
                                    continue

                                if not title or len(title) < 3:
                                    title = _extract_title_from_url_slug(full_url)

                                if (not title or len(title) < 3
                                        or is_filter_or_facet_title(title, full_url)
                                        or is_noise_cta_title(title)):
                                    _rej['no_title'] += 1
                                    continue

                                card_text = (_bounded_card_text(a, _census_urls)
                                             or _bounded_admitted_card_text(a, _census_urls))

                                if not posting_candidate(full_url,title,getattr(scanner,'is_valid_job_url',None)):
                                    _rej['not_a_posting'] += 1
                                    continue
                                page_seen_raw.add(full_url)
                                if norm_url in seen_norm or full_url in seen_urls or norm_url in page_candidate_norm:
                                    _rej['duplicate'] += 1
                                    continue
                                page_candidate_norm.add(norm_url)
                                page_jobs.append((title, full_url, card_text))
                            except Exception:
                                continue

                        _census_record = {
                            'page': page_num, 'cards_seen': len(cards),
                            'visible_posting_links': len(_census_links),
                            'accepted': len(page_jobs),
                            'rejected': {k: v for k, v in sorted(_rej.items()) if v},
                            'render_kind': _census_meta.get('kind') or '',
                            'render_reason': str(_census_meta.get('reason') or '')[:200],
                            'note': _census_note(len(page_jobs)),
                        }
                        # Keep every page's tally, and headline the page whose links went
                        # unexplained: visible postings minus what was accepted minus what
                        # was accounted for as a duplicate. A page of duplicates must not
                        # push the record that says "the render admitted 164, we took 0"
                        # out of the headline.
                        _census_pages = LAST_INGESTION_REPORT.setdefault('harvest_census_pages', [])
                        _census_pages.append(_census_record)
                        if len(_census_pages) > 24:
                            del _census_pages[:-24]

                        def _census_unexplained(record):
                            rejected = record.get('rejected') or {}
                            accounted = (int(record.get('accepted') or 0)
                                         + int(rejected.get('duplicate') or 0))
                            return int(record.get('visible_posting_links') or 0) - accounted

                        _prev = LAST_INGESTION_REPORT.get('harvest_census')
                        if not isinstance(_prev, dict) or \
                                _census_unexplained(_census_record) > _census_unexplained(_prev):
                            LAST_INGESTION_REPORT['harvest_census'] = _census_record
                        # Identity coverage for THIS render, not merely count equality:
                        # 180 admitted links is the evidence cap, not the board size.
                        _census_complete = (
                            bool(_census_ids) and not _census_meta.get('census_truncated') and len(_census_links) < 180
                            and _census_ids.issubset({posting_identity(value)
                                                      for value in page_seen_raw}))
                        if len(page_jobs) == 0 and not page_seen_raw:
                            terminal=reconciled_offset_end(page,ingested_count,last_good_paging_evidence,
                                LAST_INGESTION_REPORT.get('page_summaries',[]),last_good_census_complete)
                            if terminal:
                                LAST_INGESTION_REPORT['terminal_evidence']=terminal
                                stop_reason='end confirmed by an out-of-range probe; observed offset boundary and prior complete census reconciled'
                                break
                            # F-31: nothing at all on this page. Classify it (challenge /
                            # render failure) and recover or stop honestly - a page that was
                            # never a listing page must not be reported as "pagination
                            # control not recognised".
                            recover_options = {'progress_cb':progress_callback}
                            if cancel_event is not None:
                                recover_options['cancel_event'] = cancel_event
                            outcome = ('unharvestable' if _census_links and not _census_meta.get('reason') else
                                _recover_blank_page(page,current_page_url,page_num,clear_wait_s,**recover_options))
                            _record_assisted_evidence(page, 'recovery_' + outcome, page_num, count=0)
                            if outcome == "cleared":
                                recover_cleared += 1
                                if recover_cleared <= 2:
                                    logger.info("Page %d recovered; re-harvesting it",
                                                page_num)
                                    continue
                                # F-35b: bounded recovery - a page that still renders
                                # empty after two recoveries is the end of the board (or
                                # a hard wall), not a transient glitch.
                                logger.info("Page %d stayed empty after %d recoveries; "
                                            "treating it as the end", page_num,
                                            recover_cleared)
                            if outcome in ('access','challenge','saved_partial'):known_listing_gate=True
                            if control is not None and control.save_requested:
                                stop_reason='saved partial during verification; resume explicitly'
                            elif outcome == 'cancelled' or (cancel_event is not None and cancel_event.is_set()):
                                stop_reason = 'cancelled by user'
                            elif outcome == 'error':
                                rendered=(_page_response_evidence(page).get('rendered')or{})
                                stop_reason=str(rendered.get('document_error')or'career document error')+'; requested='+url+'; final='+page.url
                            elif outcome == 'unextractable':
                                stop_reason=("page %d rendered vacancy controls/text but no validated posting identities were extracted; "
                                             "listing extraction incomplete, not a confirmed bot wall (last complete page %d) — %s"
                                             % (page_num,last_good_page,_census_note(0)))
                            elif outcome == "access":
                                stop_reason = "access blocked on page %d; no readable listing recovered (last complete page %d)" % (page_num, last_good_page)
                            elif outcome == "challenge":
                                stop_reason = ("bot protection re-engaged on page %d; no readable "
                                               "current listing accepted within the wait window "
                                               "(last complete page %d)"
                                               % (page_num, last_good_page))
                            elif probe_pending_end:
                                stop_reason = ("no next-page control recognised on page %d "
                                               "(last complete page %d); end confirmed by an "
                                               "out-of-range probe" % (page_num, last_good_page))
                            elif _census_links:
                                # The page DID render postings. Reporting "render failure or
                                # undetected bot wall" here sent users to verify a check that
                                # had already passed, and hid an extraction gap behind a
                                # bot-protection story.
                                stop_reason = ("page %d rendered %d visible posting link(s) that "
                                               "the harvest did not accept; page read as %s "
                                               "(last complete page %d) — %s"
                                               % (page_num, len(_census_links),
                                                  _census_meta.get('kind') or 'unknown',
                                                  last_good_page, _census_note(0)))
                            else:
                                stop_reason = ("page %d returned no listing content and no "
                                               "pagination even after a reload (render failure "
                                               "or undetected bot wall; last complete page %d) — %s"
                                               % (page_num, last_good_page, _census_note(0)))
                            logger.info("Harvest stopped: %s", stop_reason)
                            break

                        logger.info("Page %d found %d job listing(s)", page_num, len(page_jobs))

                        # F-27: a paging step that lands back on the identical listing means
                        # the board refused to advance (a click that did nothing, a clamped
                        # parameter, a JS-only pager). That is a FAILED ADVANCE, not the end
                        # of the board: retry with a different mechanism - URL-based paging
                        # only, skipping the DOM clicks that just failed - and only call it
                        # the end after three such attempts.
                        re_served = bool(page_seen_raw) and any(
                            page_seen_raw == r for r in recent_raws)
                        recent_raws.append(page_seen_raw)
                        del recent_raws[:-4]
                        if page_jobs:
                            synthetic_replays = 0
                            synthetic_chain_unmet_total = False
                            synthetic_chain_readable = True
                            synthetic_chain_census_complete = True
                            synthetic_chain_no_forward = True
                            last_good_page = page_num
                            last_good_count = len(page_jobs)
                            page_size_max = max(page_size_max, len(page_jobs))
                            probe_pending_end = False
                            recover_cleared = 0
                            fresh_streak += 1
                            # On 100+ page portals, replenish transient edge-throttle budgets
                            # once sustained progress (10+ consecutive fresh pages) resumes.
                            if fresh_streak >= 10:
                                cooldowns_used = 0
                                probes_left = 4
                        if re_served:
                            fresh_streak = 0
                            mis_advances += 1
                            # C17 / P11 + C18: no pager does NOT mean page 1 is
                            # everything. On one-indexed sites our first guessed
                            # ?p=1 can still be page 1, with new jobs at ?p=2.
                            # Try both guesses; only two identical replays of a
                            # completely accounted census can avoid F-38's reloads.
                            current_total = None
                            if last_advance_was_synthetic:
                                synthetic_replays += 1
                                synthetic_chain_census_complete &= _census_complete
                                try:
                                    # Keep the evidence from BOTH guesses; a visible
                                    # pager, unmet total or unexplained posting on EITHER
                                    # one means the short stop is unsafe.
                                    probe_evidence = _record_assisted_evidence(
                                        page, 'pagination_probe', page_num, count=len(page_jobs))
                                    current_total = probe_evidence.get('stated_total')
                                    rendered = probe_evidence.get('rendered_state') or {}
                                    if not rendered.get('ready'):
                                        synthetic_chain_readable = False
                                    if (probe_evidence.get('forward_page_links')
                                            or probe_evidence.get('pagination_text')
                                            or _advertised_forward_page(page, page_num + 1) is not False):
                                        synthetic_chain_no_forward = False
                                except Exception:
                                    synthetic_chain_readable = False
                                    synthetic_chain_no_forward = False
                                if isinstance(current_total, int) and current_total > ingested_count:
                                    synthetic_chain_unmet_total = True
                            if _unadvertised_probe_replayed(
                                    synthetic=last_advance_was_synthetic,
                                    replayed=re_served,
                                    two_index_guesses=(synthetic_replays >= 2
                                        and _paging_value(current_page_url) == last_good_page + 1),
                                    census_complete=(last_good_census_complete
                                                     and synthetic_chain_census_complete),
                                    forward_control=last_good_forward_control,
                                    last_page_evidence=last_good_paging_evidence,
                                    current_total=current_total, ingested=ingested_count,
                                    unmet_total_seen=synthetic_chain_unmet_total,
                                    evidence_readable=synthetic_chain_readable,
                                    probe_controls_absent=synthetic_chain_no_forward):
                                LAST_INGESTION_REPORT['pagination_end_evidence'] = {
                                    'kind': 'unadvertised_synthetic_replay',
                                    'last_complete_page': last_good_page,
                                    'probe_url': _redact_evidence_url(current_page_url),
                                    'census_accounted': True, 'forward_control': False,
                                    'synthetic_replays': synthetic_replays,
                                    'stated_total': last_good_paging_evidence.get('stated_total'),
                                }
                                stop_reason = (
                                    'two synthesised next-page probes re-served the same '
                                    'listing with no advertised Next (last complete page %d); '
                                    'no further pages demonstrated' % last_good_page)
                                logger.info('Harvest stopped: %s', stop_reason)
                                break
                            # F-37b: a landing that re-served the previous listing was a
                            # DENIED advance, not a visited page - un-try it so the board's
                            # own href (the only key a signed pager accepts) stays
                            # retryable after a cool-down or a mis-advance retry.
                            tried_urls.discard(current_page_url)
                            if mis_advances >= 3:
                                # F-37: three refused advances right after a long request
                                # burst is also what a bot edge's soft denial / stale edge
                                # cache looks like (your round-12 evidence: the live RW NL
                                # pager runs to 60 pages while rev-10 stopped at 49). One
                                # polite cool-down per run: pause, reload, retry the whole
                                # advance chain before spending probes or accepting the end.
                                if (cooldowns_used < len(_COOLDOWN_STEPS_S)
                                        and page_size_max >= 5
                                        and last_good_count >= page_size_max - 1):
                                    # F-38: a bot edge's rate-limit window can outlast one
                                    # 15 s pause (your round-13 run: re-serve stop at page
                                    # 47 while the live pager runs to 60). Escalate
                                    # politely - 15 s, then 45 s, then 90 s, at most three
                                    # pauses per run, all of it pure waiting - and retry
                                    # the whole advance chain after each pause before
                                    # spending probes or accepting the end.
                                    delay = _COOLDOWN_STEPS_S[cooldowns_used]
                                    cooldowns_used += 1
                                    mis_advances = 0
                                    force_url_advance = False
                                    logger.info("Assisted pagination: 3 refused advances on a "
                                                "full page %d - cool-down %d/3: waiting %.0fs "
                                                "then retrying the advance chain",
                                                page_num, cooldowns_used, delay)
                                    try:
                                        if not _interruptible_page_wait(page, delay, cancel_event):
                                            stop_reason = 'cancelled by user'
                                            break
                                        page.reload(wait_until="domcontentloaded", timeout=30000)
                                        if not _interruptible_page_wait(page, 3.0, cancel_event):
                                            stop_reason = 'cancelled by user'
                                            break
                                    except Exception:
                                        pass
                                    if cancel_event is not None and cancel_event.is_set():
                                        stop_reason = 'cancelled by user'
                                        break
                                    continue
                                # F-35c: same false-end logic as the no-next branch - a
                                # FULL last page plus three refused advances can still be
                                # a board glitch (your round-11 run: 490 jobs, re-serve
                                # stop at page 49 on a board you believe holds 500+).
                                # Spend one synthesised-URL probe before accepting the
                                # end; new jobs resume the harvest, a re-serve or blank
                                # confirms it.
                                if (probes_left
                                        and page_size_max >= 5
                                        and last_good_count >= page_size_max - 1):
                                    probe_url = next_page_url(page.url, page_num)
                                    if probe_url and probe_url not in tried_urls:
                                        probes_left -= 1
                                        tried_urls.add(probe_url)
                                        probe_pending_end = True
                                        logger.info("Assisted pagination: end-of-board probe %d/4 -> %s",
                                                            4 - probes_left, probe_url)
                                        try:
                                            page.goto(probe_url, wait_until="domcontentloaded",
                                                      timeout=20000)
                                            time.sleep(3.0)
                                            current_page_url = page.url
                                            tried_urls.add(current_page_url)
                                            force_url_advance = True
                                            mis_advances = 0
                                            continue
                                        except Exception:
                                            pass
                                # page_num is the NEXT-page counter, so quoting it here read
                                # one (or with clamp oscillation, two) past the true end and
                                # looked like a hidden cap (your round-8 question). Quote the
                                # last page that actually delivered jobs instead.
                                stop_reason = ("board re-served the same listing after %d paging "
                                               "attempts (last complete page %d); no further "
                                               "pages reachable" % (mis_advances, last_good_page))
                                logger.info("Harvest stopped: %s", stop_reason)
                                break
                            # F-37d: when the denied landing URL IS the advance target
                            # (its paging value equals the counter), re-request that exact
                            # URL politely before the retry chain drifts to synthesised
                            # stale-token URLs - a signed board clamps those to its
                            # visible window, while a bot edge's soft denial (round-12 RW
                            # NL evidence: pager runs to 60 while rev-10 stopped at 49)
                            # typically releases on the next same-href request.
                            if _paging_value(current_page_url) == page_num:
                                logger.info(
                                    "Page %d re-served the previous listing; re-requesting the "
                                    "denied href %s politely (attempt %d/3)",
                                    page_num, current_page_url, mis_advances)
                                try:
                                    page.goto(current_page_url,
                                              wait_until="domcontentloaded", timeout=20000)
                                    if not _interruptible_page_wait(page, 3.0, cancel_event):
                                        stop_reason = 'cancelled by user'
                                        break
                                    current_page_url = page.url
                                    tried_urls.add(current_page_url)
                                    force_url_advance = True
                                    continue
                                except Exception:
                                    pass
                            force_url_advance = True
                            logger.info(
                                "Page %d re-served the previous listing; retrying with URL-based "
                                "paging (attempt %d/3)", page_num, mis_advances)
                        else:
                            mis_advances = 0

                        if len(page_jobs) == 0 and re_served:
                            pass                      # handled by the failed-advance retry below
                        elif len(page_jobs) == 0:
                            # F-27: before calling the board empty, try another board-owned
                            # paging href. A failed/synthesised advance re-serves an old
                            # page, which would otherwise read as two empty pages and stop
                            # a harvest that still has pages left.
                            cands = paging_href_candidates(
                                page, current_page_url,
                                tried_urls)
                            if cands:
                                nxt = cands[0]
                                tried_urls.add(nxt)
                                logger.info("Page %d empty; retrying with the board's own href %s",
                                            page_num, nxt)
                                try:
                                    page.goto(nxt, wait_until="domcontentloaded", timeout=20000)
                                    time.sleep(3.0)
                                    current_page_url = page.url
                                    tried_urls.add(current_page_url)
                                    continue
                                except Exception:
                                    pass
                            consecutive_empty_pages += 1
                            if consecutive_empty_pages >= 2:
                                # F-22: repeated empty pages mean the advance strategy never
                                # reached new content (the board ignored the synthesised
                                # parameter, or the click hit a dead control). Say so
                                # instead of letting a 1-page harvest read as a full one.
                                stop_reason = ("no new listings on %d consecutive pages - "
                                               "pagination control not recognised "
                                               "(last complete page %d)"
                                               % (consecutive_empty_pages, last_good_page))
                                logger.info("Early stopping pagination: %s", stop_reason)
                                break
                        else:
                            consecutive_empty_pages = 0

                        # Ingest harvested jobs
                        for title, job_url, card_text in page_jobs:
                            if cancel_event is not None and cancel_event.is_set():
                                stop_reason = 'cancelled by user'
                                break
                            if not posting_candidate(job_url,title,getattr(scanner,'is_valid_job_url',None)):
                                continue
                            if is_filter_or_facet_title(title, job_url):
                                continue
                            if is_noise_cta_title(title):
                                continue
                            if is_chrome_url(job_url):
                                continue

                            reader = getattr(scanner, '_saved_jd_reader', None)
                            if callable(getattr(reader, 'observe', None)):
                                reader.observe(job_url, title=title)
                            remote_type = "Unknown"
                            combined_context = f"{title}\n{card_text}"

                            # C19: only the bounded card may supply a place;
                            # the existing sanitizer leaves ambiguous place
                            # strings Unknown rather than guessing a country.
                            extracted_loc = _observed_card_location(scanner, card_text,
                                                                    title, seed_name)

                            # Detect Remote
                            if re.search(r"\b(hybrid|flexible remote)\b", card_text, re.I):
                                remote_type = "hybrid"
                            elif re.search(r"\b(remote|work from home|fully remote|100% remote)\b", card_text, re.I):
                                remote_type = "remote"
                            elif re.search(r"\b(on[- ]?site|in[- ]office|vor Ort)\b", card_text, re.I):
                                remote_type = "onsite"

                            # Country only from observed place or explicit job country.
                            country, country_source, country_evidence = country_resolver.resolve(job_url,extracted_loc,allow_request=False)

                            # Initial card evidence is provisional and passes the
                            # SAME context/false-positive policy as regular details.
                            exp_info = extract_experience(combined_context, title)
                            exp_level = exp_info.get("level", "Unknown")
                            exp_req = exp_info.get("required", "Unknown")
                            exp_min = exp_info.get("min_years") or None
                            exp_src = exp_info.get("source", "none")

                            job_record = {
                                "title": title,
                                "company": seed_name,
                                "country": country,
                                "country_source": country_source,
                                "location": extracted_loc,
                                "raw_location": extracted_loc,
                                "url": job_url,
                                "ats_source": "assisted_browser",
                                "source_type": 'recruiter' if source_subtype=='recruiter' else 'direct_employer',
                                "source_subtype": source_subtype,
                                "source_name": seed_name,
                                "description": card_text or "",
                                "remote_type": remote_type,
                                "experience_level": exp_level,
                                "experience_required": exp_req,
                                "experience_min_years": exp_min,
                                "experience_source": exp_src,
                                "run_id": run_id,
                                "category": "Other",
                                "domain": "Unknown",
                                "visa_sponsorship": "Unknown",
                                "relocation_support": "Unknown",
                                "eu_blue_card_verdict": "Unknown",
                                "support_confidence": 0.0,
                                "support_evidence": "",
                                "support_evidence_url": job_url,
                                "support_evidence_type": 'job_posting',
                            }

                            job_record = apply_support_to_job(scanner,job_record,combined_context,
                                kind='card',read_status='unattempted',method='listing_card')
                            try:
                                already = _persist_assisted_job(conn,job_record)
                                seen_urls.add(job_url)
                                seen_norm.add(normalize_job_url(job_url))
                                ingested_count += 1
                                if already:
                                    dup_count += 1
                                else:
                                    new_count += 1
                                harvest_rows.append({
                                    "title": title, "company": seed_name,
                                    "country": country, "location": extracted_loc,
                                    "country_source": country_source, "country_evidence": country_evidence,
                                    "url": job_url, "run_id": job_record["run_id"],
                                    "category": job_record["category"],
                                    "domain": job_record["domain"],
                                    "source_type": job_record["source_type"],
                                    "source_subtype": job_record["source_subtype"],
                                    "source_name": seed_name,
                                    "ats_source": "assisted_browser",
                                    "remote_type": remote_type,
                                    "experience_level": exp_level,
                                    "visa_sponsorship": job_record["visa_sponsorship"],
                                    "relocation_support": job_record["relocation_support"],
                                })
                                for key in ('eu_blue_card_verdict','relocation_required','support_confidence','support_evidence',
                                            'support_evidence_url','support_evidence_type','blue_card_evidence',
                                            'jd_read_status','jd_source','jd_chars','support_guard_notes'):
                                    harvest_rows[-1][key]=job_record.get(key,'')
                                pending_details.append((dict(job_record),harvest_rows[-1]))
                            except Exception as ex:
                                logger.exception('Could not save assisted job %s',job_url)
                                persistence_errors.append({'url':job_url,'error':str(ex)})
                                stop_reason = 'database write failed; previously committed jobs retained: ' + str(ex)
                                if callable(progress_callback):
                                    progress_callback(_translate('Database write failed: {error}. Saved jobs are retained.').format(error=ex))
                                break

                        conn.commit()
                        page_saved_evidence = _record_assisted_evidence(
                            page, 'page_saved', last_good_page, count=len(page_jobs))
                        if page_jobs and not re_served:
                            # All URLs admitted by the last real page are accounted for.
                            last_good_census_complete = _census_complete
                            last_good_paging_evidence = {
                                key: page_saved_evidence.get(key)
                                for key in ('pagination_text', 'forward_page_links', 'stated_total')
                            }
                        if page_jobs and page_seen_raw:
                            qualified_pages.add(tuple(sorted(normalize_job_url(value)for value in page_seen_raw)))
                        if page_jobs:
                            recover_cleared = 0
                        if persistence_errors:
                            break
                        if callable(progress_callback) and last_good_page > 0:
                            try:
                                progress_callback(
                                    _translate("Harvesting page %d — %d jobs ingested (%d new, %d refreshed)...")
                                    % (last_good_page, ingested_count, new_count, dup_count)
                                )
                            except Exception:
                                pass

                        if cancel_event is not None and cancel_event.is_set():
                            stop_reason = 'cancelled by user'
                            break

                        # Polite human breather every 20 harvested pages so 60-200+ page
                        # sessions never trip edge velocity / burst thresholds.
                        if page.is_closed():
                            stop_reason = 'browser closed after last complete page %d' % last_good_page
                            break

                        if page_jobs and last_good_page > 0 and last_good_page % 20 == 0:
                            try:
                                page.wait_for_timeout(random.randint(6000, 9000))
                            except Exception:
                                pass

                        terminal = terminal_listing_evidence(page,ingested_count,len(page_seen_raw),
                            census_complete=_census_complete,harvested_urls=seen_urls,current_urls=page_seen_raw)
                        if cancel_event is not None and cancel_event.is_set():
                            stop_reason = 'cancelled by user'
                            break
                        if terminal:
                            LAST_INGESTION_REPORT['terminal_evidence']=terminal
                            if terminal['kind']=='disappeared_pager_reconciled':
                                stop_reason = f"last page reached (page {last_good_page}; terminal pager and observed total reconciled; forward control absent)"
                            elif terminal['kind']=='native_append_reconciled':
                                stop_reason = f"last page reached (page {last_good_page}; native append terminal and observed identities reconciled)"
                            elif terminal['kind']=='disabled_next':
                                stop_reason = f"last page reached (page {last_good_page}; next-page control disabled)"
                            else:
                                stop_reason = f"last page reached (visible page {terminal['current']}; terminal pager and observed total reconciled)"
                            break

                        # Attempt pagination to next page
                        next_clicked = False
                        continuing_synthetic_guess = last_advance_was_synthetic and re_served
                        last_advance_was_synthetic = False
                        prev_advance_url = page.url
                        prev_advance_sig = _first_listing_signature(page)
                        # After a failed advance, skip the DOM-click checks (1-3) that just
                        # produced nothing and go straight to URL-based paging (3.5 / 4).
                        skip_dom = force_url_advance
                        force_url_advance = False
                        # F-32: some boards render the pager (and late cards) only once it
                        # scrolls into view. One cheap scroll-to-bottom before the DOM checks
                        # keeps a lazy pager from looking like "no pagination control".
                        if not skip_dom:
                            try:
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                time.sleep(random.uniform(0.65, 1.15))
                            except Exception:
                                pass

                        # Observe the last real page AFTER scrolling the lazy pager into
                        # view, BEFORE leaving it. Unknown evidence retains F-38 retries.
                        if page_jobs and not re_served:
                            last_good_forward_control = _advertised_forward_page(
                                page, page_num + 1)

                        # Check 1: Locate pagination target in DOM (scoped to pager containers
                        # first so sidebar facet counts are never mis-clicked), tag it, and
                        # dispatch a trusted Playwright mouse click (isTrusted === true).
                        try:
                            native_advance = None if skip_dom else _native_append_advance(page)
                            if native_advance is False:
                                stop_reason='native load-more unproven: '+getattr(page,'_ss_native_append_diagnostic','unknown append result')+'; listing incomplete'
                                break
                            if native_advance is True:
                                next_clicked = True
                            js_click_result = None
                            if not next_clicked:
                                js_click_result = None if skip_dom else page.evaluate(_PAGINATION_TARGET_JS, page_num + 1)
                            if js_click_result and _trusted_click_tagged(page):
                                next_clicked = True
                        except _AssistedSessionStop:
                            raise
                        except Exception:
                            pass

                        # Check 2: Target specific next page number link (e.g. "2", "3", "6", "11", etc.)
                        # Note: never query `a[data-page='{page_num}']` blindly on 1-indexed pagers
                        # because on window boundaries (e.g. page 50 -> 51) that clicks active page 50!
                        if not next_clicked and not skip_dom:
                            target_page_str = str(page_num + 1)
                            try:
                                has_zero_dp = page.query_selector("[class*='paginat'] [data-page='0'], [class*='pager'] [data-page='0']") is not None
                                scopes = ("nav[aria-label*='pag' i]", "[class*='paginat' i]", "[class*='pager' i]")
                                num_btn = None
                                for scope in scopes:
                                    num_btn = page.query_selector(f"{scope} :is(a, button):text-is('{target_page_str}')")
                                    if num_btn:
                                        break
                                if num_btn is None and has_zero_dp:
                                    num_btn = page.query_selector(f"[class*='paginat'] [data-page='{page_num}']:not([aria-current='page'])")
                                if num_btn and num_btn.is_visible() and _trusted_forward_click(page, num_btn):
                                    next_clicked = True
                            except Exception:
                                pass

                        # Check 3: Next chevron / button / arrow
                        if not next_clicked and not skip_dom:
                            next_selectors = [
                                "a[rel='next']",
                                "a[aria-label*='Next' i]",
                                "button[aria-label*='Next' i]",
                                "a[aria-label*='Volgende' i]",
                                "button[aria-label*='Volgende' i]",
                                "a[class*='pagination__arrow--next']",
                                "button[class*='pagination__arrow--next']",
                                "a.next",
                                "button.next",
                                ":is(nav, [class*='paginat'], [class*='pager']) :is(a,button):text-is('›')",
                                ":is(nav, [class*='paginat'], [class*='pager']) :is(a,button):text-is('»')",
                                ":is(nav, [class*='paginat'], [class*='pager']) :is(a,button):text-is('>')",
                                "button:has-text('Next')",
                                "a:has-text('Next')",
                                "a:has-text('Volgende')",
                                "button:has-text('Volgende')",
                                "button:has-text('Load More')",
                                "button:has-text('Show more')",
                                "button:has-text('Mehr laden')",
                                "a:has-text('Mehr laden')",
                                "[role='button']:text-is('Mehr laden')",
                                "button:has-text('Meer laden')",
                                "a:has-text('Meer laden')",
                                "a:has-text('Weiter')",
                                "button:has-text('Weiter')",
                                "a[aria-label*='Weiter' i]",
                                "button[aria-label*='Weiter' i]",
                                "a[aria-label*='Nächste' i]",
                                "button[aria-label*='Nächste' i]",
                                "a[aria-label*='Suivant' i]", "button[aria-label*='Suivant' i]",
                                "a[aria-label*='Siguiente' i]", "button[aria-label*='Siguiente' i]",
                                "a[aria-label*='Successiv' i]", "button[aria-label*='Successiv' i]",
                            ]
                            for sel in next_selectors:
                                try:
                                    btn = page.query_selector(sel)
                                    if btn and btn.is_visible():
                                        disabled = btn.evaluate("el => [el, el.closest('li')].some(n => n && (n.hasAttribute('disabled') || n.getAttribute('aria-disabled') === 'true' || /\\b(disabled|active|current|selected)\\b/i.test(n.className || '')))")
                                        if not disabled and _trusted_forward_click(page, btn):
                                            next_clicked = True
                                            break
                                except Exception:
                                    continue

                        # Check 3.5 (F-27): the board's own paging hrefs, ascending. This is
                        # what carries a harvest past a numbered pager's visible window.
                        if not next_clicked:
                            cands = paging_href_candidates(
                                page, current_page_url,
                                tried_urls)
                            if cands:
                                nxt = cands[0]
                                tried_urls.add(nxt)
                                try:
                                    page.goto(nxt, wait_until="domcontentloaded", timeout=20000)
                                    next_clicked = True
                                except Exception:
                                    pass

                        # Check 4: generic query-parameter navigation. Increments the
                        # board's own paging parameter when the URL already carries one;
                        # otherwise probes `p`. No host-name rules (F-25, guide §15.9): a
                        # board that ignores the synthesised parameter is caught by the
                        # no-progress stop above, not special-cased here.
                        if not next_clicked and (len(page_jobs) >= 5 or skip_dom):
                            try:
                                before_url = page.url
                                next_url = next_page_url(before_url, page_num)
                                if next_url:
                                    page.goto(next_url, wait_until="domcontentloaded", timeout=20000)
                                    next_clicked = True
                                    # Carry the unadvertised-guess state through the
                                    # repeated ?p=1 -> ?p=2 step, but never through
                                    # genuine new rows or a board-owned pager.
                                    last_advance_was_synthetic = (
                                        continuing_synthetic_guess
                                        or _paging_value(before_url) is None)
                            except Exception:
                                pass

                        if not next_clicked and _late_listing_advance(page,prev_advance_sig):
                            next_clicked=True
                        if not next_clicked:
                            # F-35: a FULL last-complete page plus a missing next control is
                            # suspicious - boards sometimes render a false end mid-listing
                            # (stale result count, JS pager glitch). Probe the synthesised
                            # next URL (budget: 2 loads per run) before accepting the end:
                            # new jobs resume the harvest, nothing confirms the end.
                            if (probes_left
                                    and last_good_page >= page_num - 1
                                    and page_size_max >= 5
                                    and last_good_count >= page_size_max - 1):
                                probe_url = next_page_url(page.url, page_num)
                                if probe_url and probe_url not in tried_urls:
                                    probes_left -= 1
                                    tried_urls.add(probe_url)
                                    probe_pending_end = True
                                    logger.info("Assisted pagination: end-of-board probe %d/4 -> %s",
                                                        4 - probes_left, probe_url)
                                    try:
                                        page.goto(probe_url, wait_until="domcontentloaded",
                                                  timeout=20000)
                                        time.sleep(3.0)
                                        current_page_url = page.url
                                        tried_urls.add(current_page_url)
                                        force_url_advance = True
                                        continue
                                    except Exception:
                                        pass
                            stop_reason = ("no next-page control recognised on page %d "
                                           "(last complete page %d)"
                                           % (page_num, last_good_page))
                            logger.info("Assisted pagination: %s", stop_reason)
                            break

                        advanced = _wait_for_page_advance(page, prev_advance_url, prev_advance_sig)
                        if not advanced and not skip_dom and page.url == prev_advance_url:
                            # Some SVG arrows only move the number window (1..10 -> 11..20),
                            # without changing the cards. Select the now-visible exact page
                            # once before treating this as a refused advance.
                            try:
                                if page.evaluate(_PAGINATION_TARGET_JS, page_num + 1) and _trusted_click_tagged(page):
                                    _wait_for_page_advance(page, prev_advance_url, prev_advance_sig)
                            except Exception:
                                pass

                        current_page_url = page.url
                        tried_urls.add(current_page_url)
                        # A re-served listing is a wasted load, not a page: keeping the
                        # counter on the real page number is what lets the mis-advance limit
                        # (3) fire before the page cap masks the honest stop reason.
                        if not re_served:
                            page_num += 1


                    # F-34: "Pages read" must mean complete listing pages, not browser
                    # loads. Clamp/redirect phantoms stay visible as `loads` in the log
                    # and report, never as the user-facing page count.
                    pages_visited = len(qualified_pages)
                    if page.is_closed():
                        stop_reason = 'browser closed after last complete page %d' % last_good_page

                saved_listing_complete=(bool(resume_batch.get('listing_completed'))if resume_details else
                    bool(ingested_count)and not persistence_errors and not browser_error and
                    any(token in stop_reason for token in('next-page control disabled','end confirmed by an out-of-range probe','terminal pager and observed total reconciled','native append terminal and observed identities reconciled'))and
                    LAST_INGESTION_REPORT.get('stated_total',ingested_count)==ingested_count)
                try:
                    save_batch(conn,url,seed_name,harvest_rows,run_id=run_id,
                        listing_completed=saved_listing_complete,pages=pages_visited,loads=loads,
                        source_subtype=source_subtype,stop_reason=stop_reason,
                        refresh_descriptions=refresh_descriptions,refresh_started_at=refresh_started_at,
                        root_run_id=resume_batch.get('root_run_id','')if resume_batch else'',
                        continuation=LAST_INGESTION_REPORT.get('continuation')if not saved_listing_complete else{},stated_total=LAST_INGESTION_REPORT.get('stated_total'))
                except Exception as exc:
                    checkpoint_error=str(exc);logger.warning('Could not checkpoint Assisted JD batch: %s',exc)

                # Full job descriptions use this explicitly human-cleared
                # session, not listing teasers. Country and support share each
                # fetched response, one budget/cache and one reused detail tab.
                detail_gate=''
                if stop_reason.startswith('native load-more unproven:'):
                    detail_gate=_partial_listing_detail_gate(page,known_listing_gate,control,cancel_event)
                elif known_listing_gate or(control is not None and control.save_requested):
                    detail_gate='listing verification not accepted; no automatic JD hand-off; resume explicitly'
                if detail_gate:
                    detail_enricher.defer_access(detail_gate)
                reader = getattr(scanner, '_saved_jd_reader', None)
                if callable(getattr(reader,'priority',None)):
                    pending_details.sort(key=lambda pair:reader.priority(pair[1]['url']))
                detail_enricher.prime_lookahead([record['url']for record,_row in pending_details if record is not None])
                for index,(record,row) in enumerate(pending_details,1):
                    if record is None:
                        continue  # resume never resurrects missing/inactive/protected jobs
                    if (cancel_event is not None and cancel_event.is_set()) or page.is_closed():
                        if cancel_event is None or not cancel_event.is_set():
                            remaining_rows = [row for _record, row in pending_details[index - 1:] if _record is not None]
                            deferred = _defer_unattempted_details(
                                remaining_rows,
                                'assisted browser closed during JD enrichment; resume explicitly')
                            detail_interrupted = True
                            detail_interrupted_count = deferred
                            detail_interrupt_reason = _translate(
                                'assisted browser closed during JD enrichment; {count} JDs unattempted; resume explicitly'
                            ).format(count=deferred)
                            stop_reason = detail_interrupt_reason
                        break
                    if callable(progress_callback):
                        progress_callback(_translate("Reading full job descriptions {done}/{total}; saved vacancies are retained.").format(done=index,total=len(pending_details)))
                    result=detail_enricher.read(record['url'])
                    if getattr(detail_enricher,'batch_interrupted',False):
                        reason=detail_enricher.batch_interrupt_reason or'assisted browser closed during JD enrichment; resume explicitly'
                        if cancel_event is not None and cancel_event.is_set():reason='cancelled by user'
                        remaining_rows=[row for _record,row in pending_details[index:]if _record is not None]
                        deferred=_defer_unattempted_details(remaining_rows,reason)
                        detail_interrupted=True;detail_interrupted_count=deferred
                        detail_interrupt_reason=(_translate('assisted browser closed during JD enrichment; {count} JDs unattempted; resume explicitly').format(count=deferred)
                            if'browser closed'in reason else reason)
                        stop_reason=detail_interrupt_reason
                        # Persist the current attempted read's actual outcome;
                        # only later rows were genuinely unattempted.
                    updated=dict(record)
                    if result.status in ('readable','truncated') and result.location:
                        from sponsorscout.core.location_evidence import detail_location
                        location=detail_location(record.get('location',''),result.location,result.country,
                            source=record.get('location_source')or record.get('country_source',''))
                        updated.update(location=location,raw_location=location)
                    if result.country:
                        updated.update(country=result.country,country_source=result.country_source or 'structured_job_country')
                        row.update(country=result.country,country_source=result.country_source or 'structured_job_country',country_evidence=('observed primary detail location'if result.country_source=='observed_detail_location'else'JobPosting.jobLocation.addressCountry')+(' (stored detail)'if result.reused else''))
                    if result.status in ('readable','truncated') and result.text:
                        updated=apply_support_to_job(scanner,updated,result.text,kind='detail',
                            read_status=result.status,method=result.method,captured_at=result.captured_at)
                        updated['description']=result.text
                    else:
                        # Keep authentic card/prior evidence. A failure is a read
                        # outcome, never an employer refusal or fabricated No.
                        updated.update(jd_read_status=result.status,jd_source=result.method or result.transport,jd_chars=len(result.text))
                        updated['support_evidence_type']=str(updated.get('support_evidence_type')or'')+'; jd_read_status='+result.status+'; jd_reason='+result.reason
                    updated.update(jd_http_final_url=redact_url(result.http_final_url),
                        jd_browser_status=result.http_status if result.transport.startswith('browser')else 0,
                        jd_browser_title=result.title if result.transport.startswith('browser')else'',
                        jd_browser_final_url=redact_url(result.final_url)if result.transport.startswith('browser')else'',
                        jd_browser_reason=result.reason if result.transport.startswith('browser')else'',
                        jd_browser_phase=result.phase if result.transport.startswith('browser')else'',
                        jd_browser_error=result.error if result.transport.startswith('browser')else'',
                        jd_http_reason=result.http_reason or(result.reason if result.transport.startswith('HTTP')else''),
                        jd_http_phase=result.http_phase or(result.phase if result.transport.startswith('HTTP')else''),
                        jd_http_error=result.http_error or(result.error if result.transport.startswith('HTTP')else''),
                        jd_browser_evidence=json.dumps(result.browser_evidence or{},ensure_ascii=False),
                        jd_defer_kind='access_gate'if result.status=='budget_skipped'and('verification'in result.reason or'wall'in result.reason or'hand-off'in result.reason)else'shared_budget'if result.status=='budget_skipped'else'')
                    updated.update(jd_reason=result.reason,jd_transport=result.transport,
                        jd_http_status=result.http_code or(result.http_status if result.transport.startswith('HTTP')else 0),
                        jd_http_title=result.http_title or(result.title if result.transport.startswith('HTTP')else''),
                        jd_final_url=redact_url(result.final_url),jd_phase=result.phase,jd_error=result.error)
                    row.update({key:updated[key]for key in('jd_reason','jd_transport','jd_http_status','jd_http_title','jd_final_url','jd_phase','jd_error','jd_http_final_url','jd_browser_status','jd_browser_title','jd_browser_final_url','jd_browser_reason','jd_browser_evidence','jd_defer_kind','jd_browser_phase','jd_browser_error','jd_http_reason','jd_http_phase','jd_http_error')})
                    try:
                        reader = getattr(scanner, '_saved_jd_reader', None)
                        if callable(getattr(reader, 'bind_revision', None)):
                            updated = reader.bind_revision(updated['url'], updated)
                        upsert_job(conn,updated)
                        row['observed_country']=updated.get('country','')
                        # Effective CSV values must agree with actual manual,
                        # richer-data and country-integrity persistence guards.
                        # Issue 005 P1: this local import used to shadow the module-level
                        # `posting_identity` for the WHOLE function, so any use of it earlier
                        # in the harvest raised UnboundLocalError and was swallowed as a
                        # stopped run. The name is already imported at the top of this file.
                        saved=conn.execute('SELECT location,country,country_source,visa_sponsorship,relocation_support,eu_blue_card_verdict,relocation_required,support_confidence,support_evidence,support_evidence_url,support_evidence_type,blue_card_evidence FROM jobs WHERE url=? OR posting_key=? ORDER BY (url=?) DESC LIMIT 1',
                            (updated['url'],posting_identity(updated['url']),updated['url'])).fetchone()
                        effective=dict(updated)
                        if saved is not None:effective.update(dict(saved))
                        for key in ('visa_sponsorship','relocation_support','eu_blue_card_verdict','relocation_required',
                                    'support_confidence','support_evidence','support_evidence_url','support_evidence_type',
                                    'blue_card_evidence','jd_read_status','jd_source','jd_chars','support_guard_notes'):
                            row[key]=effective.get(key,'')
                        row['location']=effective.get('location')or'Unknown'
                        row['country']=effective.get('country','')or''
                        row['country_source']=effective.get('country_source','none')
                    except Exception as exc:
                        conn.rollback()
                        persistence_errors.append({'url':record['url'],'error':str(exc)})
                        stop_reason='job-description enrichment write failed; saved vacancies retained'
                        break
                    if getattr(detail_enricher,'batch_interrupted',False):
                        break
            finally:
                # Closing an externally closed browser must not skip history,
                # result emission or CSVs for jobs already committed.
                if detail_enricher is not None:
                    LAST_INGESTION_REPORT['detail_verification_events']=list(detail_enricher.events)
                    LAST_INGESTION_REPORT['detail_verification_counts']=dict(detail_enricher.event_counts)
                    detail_enricher.close()
                if control is not None:control.attention(dict(waiting=False,phase='listing',url='',reason=''))
                for resource in (context,browser):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception:
                            logger.debug('Assisted browser resource already closed',exc_info=True)
    except _AssistedSessionStop as exc:
        stop_reason = str(exc)
    except Exception as exc:
        browser_error = type(exc).__name__+': '+str(exc)
        stop_reason = 'browser session ended after last complete page %d: %s' % (last_good_page,browser_error)
        logger.warning('Assisted browser stopped: %s',browser_error)
    pages_visited = len(qualified_pages)

    cancelled = cancel_event is not None and cancel_event.is_set()
    if cancelled:
        stop_reason = 'cancelled by user'
    if control is not None and control.save_requested:stop_reason='saved partial during verification; resume explicitly'
    # Listing reconciliation is a completed phase, not undone by a later
    # detail Stop/close/error. Keep it resumable without another listing crawl.
    listing_completed=bool(saved_listing_complete)
    declared_total = LAST_INGESTION_REPORT.get('stated_total')
    if listing_completed and declared_total is not None and ingested_count != declared_total:
        listing_completed=False
        stop_reason += '; stated total %s not reconciled with %s unique observations' % (declared_total, ingested_count)
    completed=(listing_completed and not browser_error and not persistence_errors
               and not cancelled and not detail_interrupted)
    detail_activity,detail_samples=detail_enricher.report()if detail_enricher else ({},[])
    from collections import Counter as _Counter
    jd_coverage=dict(_Counter(str(row.get('jd_read_status')or'unattempted')for row in harvest_rows))
    jd_coverage['observed']=len(harvest_rows)
    jd_coverage['readable_unknown_support']=sum(row.get('jd_read_status')=='readable'and all(row.get(key)in('','Unknown',None)for key in('visa_sponsorship','relocation_support','eu_blue_card_verdict'))for row in harvest_rows)
    enrichment_enabled=bool(scanner.config.ENABLE_DETAIL_SCAN)
    missing_jd=sum(jd_coverage.get(kind,0)for kind in('unattempted','empty','thin','failed','blocked','budget_skipped','truncated','protected'))
    if listing_completed and enrichment_enabled and missing_jd:
        completed=False
        stop_reason+='; listing traversal complete, JD coverage incomplete: %s/%s readable; Unknown is not No' % (jd_coverage.get('readable',0),len(harvest_rows))
    if not enrichment_enabled:
        stop_reason+='; JD enrichment explicitly disabled'
    # Compatibility field now describes country evidence sharing the JD reads.
    country_activity=dict(shared_http=detail_activity.get('http',0),shared_browser=detail_activity.get('browser',0),
                          blocked=detail_activity.get('blocked',0),resolved_country_fields=sum(bool(row.get('country'))for row in harvest_rows))
    country_samples=detail_samples
    try:
        save_batch(conn,url,seed_name,harvest_rows,run_id=run_id,listing_completed=listing_completed,
            pages=pages_visited,loads=loads,source_subtype=source_subtype,stop_reason=stop_reason,
                        refresh_descriptions=refresh_descriptions,refresh_started_at=refresh_started_at,
            root_run_id=resume_batch.get('root_run_id','')if resume_batch else'',
            continuation=LAST_INGESTION_REPORT.get('continuation')if not listing_completed else{},stated_total=LAST_INGESTION_REPORT.get('stated_total'))
    except Exception as exc:
        checkpoint_error=str(exc);logger.warning('Could not save Assisted JD continuation: %s',exc)
    run_status = 'completed' if completed else ('cancelled' if cancelled else 'partial')
    target_status = 'ok' if completed else ('partial' if ingested_count else 'error')

    # Log execution in scan_runs and scan_log so the Tools tab and DB history track it
    try:
        from datetime import datetime, timezone
        from sponsorscout.db.database import (
            start_scan_run,
            finish_scan_run,
            record_scan_log_rows,
            record_scan_event,
        )
        run_id = run_started.strftime("%Y%m%dT%H%M%S%f") + "_assisted"
        db_path = db_path or getattr(conn, "_db_path", None)
        # F-39: the format string repeated the month and dropped the day
        # ("%Y-%m-%m"), so every run row claimed e.g. 2026-10-10 for a
        # 2026-10-23 scan (your round-13 CSVs). Real calendar day now, on the
        # same UTC clock as run_id, the event timeline and the DB defaults.
        started_s = run_started.strftime("%Y-%m-%d %H:%M:%S")
        finished_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        duration_s = (datetime.now(timezone.utc) - run_started).total_seconds()
        # Check if conn has path attribute or fallback to direct execute
        conn.execute(
            """INSERT INTO scan_runs (run_id, method, ats_companies, career_companies, status, started_at, finished_at,
                                     targets_ok, targets_empty, targets_error, jobs_found, jobs_quarantined, jobs_duplicates, error)
               VALUES (?, 'assisted', 0, 1, ?, ?, ?,
                       ?, ?, ?, ?, 0, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                 status=excluded.status, jobs_found=excluded.jobs_found,
                 jobs_duplicates=excluded.jobs_duplicates,
                 finished_at=excluded.finished_at""",
            (run_id,run_status,started_s,finished_s,
             1 if completed else 0,0,1 if target_status=='error' else 0,
             ingested_count,dup_count,stop_reason if not completed else ''),
        )
        conn.execute(
            """INSERT INTO scan_log
               (run_id, scanner, seed_name, company, source_type, target_country,
                status, provider, jobs_found, quarantined, duplicates,
                rejected_scope, error, diagnostics, duration_sec, seed_url)
               VALUES (?, 'assisted_browser', ?, ?, ?, ?,
                       ?, 'AssistedBrowser', ?, 0, ?, 0, '', ?, ?, ?)""",
            (
                run_id,
                seed_name,
                seed_name,
                'recruiter' if source_subtype=='recruiter' else 'direct_employer',
                country if 'country' in locals() else '',
                target_status,
                ingested_count,
                dup_count,
                f"Harvested {ingested_count} jobs ({new_count} new, {dup_count} "
                f"already present) across {pages_visited} page(s) "
                f"({loads} browser read pass(es) incl. retries); stop: {stop_reason}; "
                f"session detail activity: {json.dumps(detail_activity)}; JD coverage: {json.dumps(jd_coverage)}; detail samples: {json.dumps(detail_samples, ensure_ascii=False)}; "
                f"response evidence: {json.dumps(LAST_INGESTION_REPORT.get('last_response', {}), ensure_ascii=False)}",
                duration_s,
                url,
            ),
        )
        conn.execute('UPDATE scan_runs SET targets_partial=? WHERE run_id=?',
                     (int(target_status=='partial'),run_id))
        conn.execute("UPDATE scan_log SET jobs_retained=?,jobs_merged=0,attempts=1 WHERE run_id=? AND scanner='assisted_browser'", 
                     (ingested_count,run_id))
        special=[LAST_INGESTION_REPORT.get(key) for key in ('initial_response','failure_response')]
        for evidence in [item for item in special if item]+LAST_INGESTION_REPORT.get('response_evidence', []):
            conn.execute("INSERT INTO scan_events (run_id, level, phase, company, message) VALUES (?, ?, ?, ?, ?)",
                (run_id, 'warning' if evidence.get('reason') else 'info', 'assisted_response', seed_name,
                 json.dumps(evidence, ensure_ascii=False)))
        conn.execute('INSERT INTO scan_events(run_id,level,phase,company,message) VALUES(?,?,?,?,?)',
                     (run_id,'warning'if country_activity.get('blocked')else'info','assisted_country',seed_name,
                      json.dumps({'activity':country_activity,'samples':country_samples},ensure_ascii=False)))
        conn.execute('INSERT INTO scan_events(run_id,level,phase,company,message) VALUES(?,?,?,?,?)',
                     (run_id,'info','runtime_info',seed_name,json.dumps({'support_flow_revision':FLOW_REVISION,'export_schema':5,'jd_enrichment_enabled':enrichment_enabled,'detail_parallel_tabs':LAST_INGESTION_REPORT.get('detail_parallel_tabs',3),'http_reprobe_enabled':LAST_INGESTION_REPORT.get('http_reprobe_enabled',True),'resumed_details':bool(resume_details),'resumed_listing':bool(resume_listing),'browser_version':LAST_INGESTION_REPORT.get('browser_version',''),'effective_detail_time_budget_sec':getattr(scanner.config,'DETAIL_SCAN_TIME_BUDGET_SEC',None),'effective_detail_max_total':getattr(scanner.config,'MAX_DETAIL_SCAN_TOTAL',None),'effective_detail_max_per_company':getattr(scanner.config,'MAX_DETAIL_SCAN_PER_COMPANY',None)})))
        conn.execute('INSERT INTO scan_events(run_id,level,phase,company,message) VALUES(?,?,?,?,?)',
                     (run_id,'warning'if missing_jd else'info','assisted_jd_coverage',seed_name,
                      json.dumps({'coverage':jd_coverage,'activity':detail_activity,'samples':detail_samples},ensure_ascii=False)))
        conn.execute('INSERT INTO scan_events(run_id,level,phase,company,message) VALUES(?,?,?,?,?)',
                     (run_id,'info','assisted_batch',seed_name,json.dumps(dict(
                         listing_completed=listing_completed,pages=pages_visited,portal_key=portal_queue_key(url),
                         resumed_details=bool(resume_details),resumed_from=resume_batch.get('run_id','')if resume_batch else'',
                         root_run_id=resume_batch.get('root_run_id',run_id)if resume_batch else run_id,
                         checkpoint_error=checkpoint_error,resumed_listing=bool(resume_listing),
                         continuation=LAST_INGESTION_REPORT.get('continuation')if not listing_completed else{},stated_total=declared_total))))
        conn.execute('INSERT INTO scan_events(run_id,level,phase,company,message)VALUES(?,?,?,?,?)',
            (run_id,'info','assisted_verification_summary',seed_name,json.dumps(dict(
                listing=LAST_INGESTION_REPORT.get('verification_counts',{}),
                detail=LAST_INGESTION_REPORT.get('detail_verification_counts',{}),
                retained_listing=len(LAST_INGESTION_REPORT.get('verification_events',[])),
                retained_detail=len(LAST_INGESTION_REPORT.get('detail_verification_events',[]))))))
        reader = getattr(scanner, '_saved_jd_reader', None)
        if callable(getattr(reader, 'summary', None)):
            conn.execute('INSERT INTO scan_events(run_id,level,phase,company,message) VALUES(?,?,?,?,?)',
                (run_id,'info','repeat_scan',seed_name,json.dumps(reader.summary())))
        for event in LAST_INGESTION_REPORT.get('verification_events',[])+LAST_INGESTION_REPORT.get('detail_verification_events',[]):
            conn.execute('INSERT INTO scan_events(run_id,level,phase,company,message) VALUES(?,?,?,?,?)',
                (run_id,'info','assisted_verification',seed_name,json.dumps(event,ensure_ascii=False)))
        for evidence in LAST_INGESTION_REPORT.get('page_summaries',[]):
            conn.execute('INSERT INTO scan_events(run_id,level,phase,company,message) VALUES(?,?,?,?,?)',
                         (run_id,'info','assisted_page',seed_name,json.dumps(evidence,ensure_ascii=False)))
        conn.execute(
            """INSERT INTO scan_events (run_id, level, phase, company, message)
               VALUES (?, ?, 'assisted_ingestion', ?, ?)""",
            (run_id, 'info' if completed or cancelled else 'warning', seed_name,
             f"Assisted browser session {run_status}. Harvested {ingested_count} job vacancies from {url}; pages: {pages_visited}; stop: {stop_reason}."),
        )
        conn.commit()

        # F-36: CSV parity - an assisted run leaves the same file set behind as
        # a regular scan run (job list, recruiter view, quarantine, scan log,
        # errors), utf-8-sig so Excel/LibreOffice open them correctly.
        try:
            import csv as _csv
            from sponsorscout import paths as _paths
            out_dir = _paths.ensure_scan_output_dir()
            job_fields = ["title", "company", "country", "location", "url", "run_id",
                          "category", "domain", "source_type",
                          "source_subtype", "source_name", "ats_source",
                          "remote_type", "experience_level", "country_source", "country_evidence",
                          "visa_sponsorship", "relocation_support", "eu_blue_card_verdict", "relocation_required",
                          "support_confidence", "support_evidence", "support_evidence_url", "support_evidence_type", "blue_card_evidence",
                          "jd_read_status", "jd_source", "jd_chars", "support_guard_notes", "observed_country",
                          "jd_reason", "jd_transport", "jd_http_status", "jd_http_title", "jd_final_url", "jd_phase", "jd_error",
                          "jd_http_final_url", "jd_browser_status", "jd_browser_title", "jd_browser_final_url", "jd_browser_reason", "jd_browser_evidence", "jd_defer_kind", "jd_browser_phase", "jd_browser_error", "jd_http_reason", "jd_http_phase", "jd_http_error"]
            base = out_dir / f"{run_id}_career_jobs.csv"
            with open(base,'w',encoding='utf-8-sig',newline='') as f:
                wcsv = _csv.DictWriter(f,fieldnames=job_fields)
                wcsv.writeheader(); wcsv.writerows({key:row.get(key,'')for key in job_fields}for row in harvest_rows)
            with open(base.with_name(base.stem + "_quarantine.csv"), "w",
                      encoding="utf-8-sig", newline="") as f:
                _csv.DictWriter(f, fieldnames=["title", "company", "url",
                                               "reason"]).writeheader()
            with open(base.with_name(base.stem + "_errors.csv"), "w",
                      encoding="utf-8-sig", newline="") as f:
                errors_writer = _csv.DictWriter(f, fieldnames=["error", "url", "detail"])
                errors_writer.writeheader()
                errors_writer.writerows(_assisted_error_rows(
                    persistence_errors, browser_error, url, stop_reason, completed, cancelled))
            if db_path:
                from sponsorscout.db import database as _dbmod
                with open(base.with_name(base.stem + "_scan_log.csv"), "w",
                          encoding="utf-8-sig", newline="") as f:
                    f.write(_dbmod.export_scan_run_csv(db_path, run_id))
            logger.info("Assisted-run CSV set written to %s", out_dir)
        except Exception as ex:
            logger.warning("Could not write assisted-run CSVs: %s", ex)
    except Exception as ex:
        logger.warning("Could not record scan_runs / scan_log for assisted ingestion: %s", ex)

    reader = getattr(scanner, '_saved_jd_reader', None)
    if callable(getattr(reader, 'summary', None)):
        LAST_INGESTION_REPORT['repeat_scan'] = reader.summary()
    LAST_INGESTION_REPORT.update(
        url=url,
        ingested=new_count,
        harvested=ingested_count,
        duplicates=dup_count,
        pages_visited=pages_visited,
        loads=loads,
        stop_reason=stop_reason,
        completed=completed,
        listing_completed=listing_completed,
        resumed_details=bool(resume_details),
        resumed_listing=bool(resume_listing),
        selected_batch=resume_run_id,
        continuation=LAST_INGESTION_REPORT.get('continuation')if not listing_completed else{},
        resumed_from=resume_batch.get('run_id','')if resume_batch else'',
        resume_checkpoint_error=checkpoint_error,
        country_resolved=sum(bool(row['country'])for row in harvest_rows),
        country_unresolved=sum(not bool(row['country'])for row in harvest_rows),
        country_detail_activity=country_activity,
        country_detail_samples=country_samples,
        session_detail_activity=detail_activity,
        detail_samples=detail_samples,
        verification_cleared=LAST_INGESTION_REPORT.get('verification_counts',{}).get('verification_cleared',0)+LAST_INGESTION_REPORT.get('detail_verification_counts',{}).get('verification_cleared',0),
        access_deferred=sum(row.get('jd_defer_kind')=='access_gate'for row in harvest_rows),
        jd_coverage=jd_coverage,
        posting_outcomes=[dict(url=row['url'],jd_read_status=row.get('jd_read_status','unattempted'))for row in harvest_rows],
        jd_enrichment_enabled=enrichment_enabled,
        detail_parallel_tabs=LAST_INGESTION_REPORT.get('detail_parallel_tabs',3),
        http_reprobe_enabled=LAST_INGESTION_REPORT.get('http_reprobe_enabled',True),
        http_reprobe_cleared=detail_activity.get('http_reprobe_cleared',0),
        parallel_prefetch=detail_activity.get('parallel_prefetch',0),
        jd_interrupted=detail_interrupted,
        jd_interrupted_reason=detail_interrupt_reason,
        jd_interrupted_count=detail_interrupted_count,
        last_listing_page=last_good_page,
        cancelled=cancelled,
        persistence_errors=persistence_errors,
        browser_error=browser_error,
    )
    logger.info("Assisted ingestion finished. Harvested %d (%d new, %d already "
                "present); stop: %s", ingested_count, new_count, dup_count,
                stop_reason)
    return new_count


def is_web_url(url: str | None) -> bool:
    """Return True if url is an http/https URL."""
    if not url or not isinstance(url, str):
        return False
    try:
        p = urlparse(url.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


# Markers that describe the LISTING itself. A job-detail (JD) read that hit a
# wall is reported as `blocked access: ...` and deliberately stays out of the
# queue: the board was readable, so handing the portal to a browser would
# re-crawl a listing whose jobs are already saved. Keying on that generic
# prefix is what queued 20 portals for a run that had already kept 3,478 of
# their jobs (issue 2026-10-28, run 20261027T231035).
_LISTING_BLOCK_MARKERS = (
    'blocked by bot protection:',
    'listing access blocked:',
    'human-verification challenge',
    'no human clearance observed',
    'no readable current listing accepted',
    'client challenge interstitial',
)


# The aggregate line the scanners build out of the per-detail outcome cache.
# It says "N job-detail responses stayed unread"; it does NOT say the listing
# was refused. Keying the queue on it is what handed back 10 portals whose
# listings had already been read in full (run 20261027T231035 kept 3,469 jobs
# from them while 49 detail pages behind click-outs stayed unread).
# Access-failure wording of any kind. On its own it does not say WHICH request
# was refused, so it only queues a portal when the listing also kept nothing.
_ANY_ACCESS_RE = re.compile(
    r'blocked access:|challenge page served instead of|access blocked|'
    r'no listing could be read', re.I)


def _is_block_message(message: str, jobs_found=None) -> bool:
    """Listing-level access walls only; an absorbed JD failure is not a block.

    Two cases, decided by what the listing itself did:

    * the listing saved rows (or its outcome is unknown) -> only an explicit
      listing-wall marker queues it. A `blocked access: N unresolved response(s)`
      summary over a readable board is a JD-coverage gap, and handing that portal
      to a browser re-crawls a listing whose jobs are already saved (10 portals,
      3,469 jobs in run 20261027T231035).
    * the listing provably saved nothing -> any access failure is worth a human
      look, because the board genuinely never answered.
    """
    value = str(message or '').casefold()
    if not value:
        return False
    if any(marker in value for marker in _LISTING_BLOCK_MARKERS):
        return True
    if jobs_found is None:
        # Nothing proves the listing stayed unreadable, so an ambiguous
        # "blocked access" summary is not enough to ask a human to re-crawl it.
        return False
    try:
        saved = int(jobs_found)
    except (TypeError, ValueError):
        saved = 0
    return saved <= 0 and bool(_ANY_ACCESS_RE.search(value))


def blocked_portals_from_errors_csv(csv_path, phase: str = "", yields=None, include_detail_issues=False) -> list[dict[str, str]]:
    """Actual blocked targets only, never a DNS failure or a fixed host list.

    `yields` maps a normalised listing URL to the jobs that listing already
    saved, so a JD-coverage summary on a readable board is not queued.
    """
    if not csv_path or not os.path.exists(csv_path):
        return []
    import csv
    yields = yields or {}
    found = []
    try:
        with open(csv_path, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                message = str(row.get('Message') or row.get('Error') or row.get('error') or '')
                diagnostics = str(row.get('Diagnostics') or row.get('diagnostics') or '')
                url = str(row.get('Seed URL') or row.get('seed_url') or '').strip()
                key = portal_queue_key(url)
                # A row's own count is the best evidence; the yields map comes
                # from the run's scan log. Neither present means the listing's
                # outcome is UNKNOWN, which must not be read as zero.
                raw_jobs = str(row.get('Jobs Found') or row.get('Jobs Retained') or '').strip()
                if raw_jobs:
                    try:
                        jobs = int(raw_jobs)
                    except ValueError:
                        jobs = None
                else:
                    jobs = yields.get(key) if yields else None
                combined = message + ' ' + diagnostics
                listing_blocked = _is_block_message(combined, jobs)
                detail_blocked = bool(include_detail_issues and jobs is not None and jobs > 0
                                      and re.search(r'blocked access:\s*[1-9]\d*\s+unresolved', combined, re.I))
                if key and (listing_blocked or detail_blocked):
                    found.append({'access_scope': 'details' if detail_blocked and not listing_blocked else 'listing',
                                  'seed_name': str(row.get('Seed Name') or row.get('seed_name') or row.get('Company') or '').strip(),
                                  'url':url,'reason':message or diagnostics,'phase':phase,
                                  'source_subtype':('recruiter' if str(row.get('Source Type') or row.get('source_type') or '').casefold() in ('recruiter','agency','aggregator') else '')})
    except Exception as exc:
        logger.warning('Error reading blocked-target artifact %s: %s',csv_path,exc)
    unique = {}
    for row in found:
        key = portal_queue_key(row['url'])
        if key in unique:
            unique[key]['seed_name'] = merge_sources(unique[key]['seed_name'],row['seed_name'])
        else:
            unique[key] = row
    return list(unique.values())


def listing_yield_by_url(scan_log_csv) -> dict:
    """Jobs Found per listing URL in one scan log, for queue honesty.

    A queued row that already produced rows is a partial board, not an empty
    one; the Assisted table says so instead of implying the scan failed.
    """
    if not scan_log_csv or not os.path.exists(scan_log_csv):
        return {}
    import csv as _csv
    out = {}
    try:
        with open(scan_log_csv, newline='', encoding='utf-8-sig') as f:
            for row in _csv.DictReader(f):
                key = portal_queue_key(str(row.get('Seed URL') or row.get('seed_url') or ''))
                if not key:
                    continue
                try:
                    found = int(float(row.get('Jobs Found') or row.get('jobs_found') or 0))
                except (TypeError, ValueError):
                    found = 0
                out[key] = max(found, out.get(key, 0))
    except Exception as exc:
        logger.warning('Could not read scan-log yields for the blocked queue: %s', exc)
    return out


def blocked_portals_from_summary(summary: dict, include_detail_issues=False) -> list[dict[str, str]]:
    """Both ATS and Career, including mid-listing partial blocks. No launch."""
    if not isinstance(summary, dict):
        return []
    found = list(summary.get('blocked_portals') or [])
    artifacts = summary.get('artifacts') or {}
    yields = {}
    for phase in ('ats','career'):
        phase_artifacts = artifacts.get(phase) or {}
        # Yields first: the filter below needs to know whether the listing
        # itself was readable before deciding if a wall message is queueable.
        for key, value in listing_yield_by_url(phase_artifacts.get('scan_log')).items():
            yields[key] = max(value, yields.get(key, 0))
        # CSVs are already produced as normal scan evidence. This only reads
        # the completed run, never seeds or connectivity before a scan.
        for kind in ('errors','scan_log'):
            found.extend(blocked_portals_from_errors_csv(phase_artifacts.get(kind),phase=phase,yields=yields,include_detail_issues=include_detail_issues))
    unique = {}
    for row in found:
        key = portal_queue_key(row.get('url'))
        if not key:
            continue
        if key not in unique:
            unique[key] = dict(row)
        else:
            unique[key]['seed_name'] = merge_sources(unique[key].get('seed_name'),row.get('seed_name'))
            unique[key]['phase'] = merge_sources(unique[key].get('phase'),row.get('phase'))
            if row.get('access_scope', 'listing') == 'listing':
                unique[key]['access_scope'] = 'listing'
            if row.get('source_subtype'):
                unique[key]['source_subtype'] = row['source_subtype']
        saved = int(yields.get(key) or 0)
        if saved > 0:
            reason = str(unique[key].get('reason') or '')
            note = f'{saved} job row(s) were already saved from this listing'
            if note not in reason:
                unique[key]['reason'] = (reason + '; ' + note).strip('; ')
    return list(unique.values())


def open_in_default_browser(url: str) -> bool:
    """Open a blocked portal URL directly in the user's default system browser."""
    if not is_web_url(url):
        return False
    try:
        return bool(webbrowser.open_new_tab(url))
    except Exception as e:
        logger.warning("Could not open URL in external browser: %s", e)
        return False


def backfill_recent_access_issues(db_path):
    """Recover omitted JD diagnostics from existing exports; no scan/network.

    Read the latest available artifact for each phase (bounded history). Do
    not re-open resolved/dismissed rows or overwrite a user's queue decisions.
    Intended for a write-capable background task, never the UI thread.
    """
    from pathlib import Path
    from sponsorscout import paths
    from sponsorscout.db import database as database
    existing = {row['url_key'] for row in database.list_blocked_portals(db_path, include_resolved=True)}
    output = Path(paths.ensure_scan_output_dir())
    phases = {'ats', 'career'}
    added = 0
    for run in database.list_scan_runs(db_path, limit=25):
        run_id = run[0]
        for phase in tuple(sorted(phases)):
            base = output / f'{run_id}_{phase}_jobs'
            scan_log = base.with_name(base.name + '_scan_log.csv')
            if not scan_log.is_file():
                continue
            phases.remove(phase)
            summary = {'artifacts': {phase: {'scan_log': str(scan_log),
                       'errors': str(base.with_name(base.name + '_errors.csv'))}}}
            for portal in blocked_portals_from_summary(summary, include_detail_issues=True):
                key = portal_queue_key(portal.get('url'))
                if key and key not in existing:
                    added += database.enqueue_blocked_portals(db_path, [portal], run_id=run_id)
                    existing.add(key)
        if not phases:
            break
    return added
