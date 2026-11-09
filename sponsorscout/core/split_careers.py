"""Native split-list discovery through real card clicks, never guessed PIDs.

Only main-list controls on a tenant-bound career route are traversed. Related
cards, private app state and arbitrary PID enumeration are not used. A fresh
normal detail navigation still verifies the selected posting independently.
"""
from __future__ import annotations
from uuid import uuid4
from sponsorscout.core.job_identity import split_career_posting,posting_identity

MAIN_CARDS='.position-cards-container .position-card[role="link"]'
CARD_READY_JS=r"""(expected) => {
 const title=document.querySelector('.position-full-card .position-title');
 const card=document.querySelector('.position-cards-container .position-card.card-selected');
 if(!title||!card||title.innerText.trim()!==expected.title)return false;
 const ct=card.querySelector('.position-title');
 if(!ct||ct.innerText.trim()!==expected.title)return false;
 const cl=card.querySelector('.position-location,.job-location');
 const dl=document.querySelector('.position-full-card .position-location,.position-full-card .job-location');
 if(cl&&dl&&cl.innerText.trim()!==dl.innerText.trim())return false;
 if(location.href!==expected.before)return true;
 // Already-selected initial cards need independently matching schema identity.
 return [...document.querySelectorAll('script[type="application/ld+json"]')].some(e=>{
  try{const j=JSON.parse(e.textContent);return j['@type']==='JobPosting'&&j.title===expected.title&&new URL(j.url,location.href).href===location.href}catch(e){return false}
 });
}"""


def observed_split_cards(page,*,cancel_event=None,limit=1500):
    """Bind each visible main-list card to the URL actually selected by the app.

    DOM tokens avoid reclicking unchanged nodes on append-only load-more.
    Replaced nodes must earn a fresh click, selected title/location and URL
    binding. Some apps leave server JSON-LD unchanged during SPA selection;
    that stale schema is NOT accepted as the newly selected description.
    """
    source=str(getattr(page,'url',''))
    if not split_career_posting(source):return []
    try:cards=page.query_selector_all(MAIN_CARDS)
    except Exception:return []
    if not cards:return []
    from sponsorscout.core.browser_evidence import inspect_page,redact_text
    snapshot=inspect_page(page,kind='listing')
    if not snapshot or snapshot.get('reason')or snapshot.get('document_error'):return []
    cache=getattr(page,'_ss_split_card_cache',{})
    result=[];live={};restore=None;restore_title='';errors=[];seen=set()
    for card in cards[:max(0,min(1500,int(limit)))]:
        if cancel_event is not None and cancel_event.is_set():break
        try:
            if not card.is_visible():continue
            heading=card.query_selector('.position-title')
            title=(heading.inner_text()or'').strip()if heading else''
            if not title:continue
            if card.evaluate("el => el.classList.contains('card-selected')") and restore is None:
                restore=card;restore_title=title
            context=(card.inner_text()or'').strip()[:3000]
            token=card.get_attribute('data-ss-observed-card')
            if token and token in cache and cache[token][0]==title and cache[token][2]==context:
                item=cache[token]
            else:
                before=page.url
                card.click(timeout=4000)
                page.wait_for_function(CARD_READY_JS,arg={'title':title,'before':before},timeout=4000)
                current=inspect_page(page,kind='listing')
                if not current or current.get('reason')or current.get('document_error'):break
                if not card.evaluate("el => el.classList.contains('card-selected')"):continue
                selected=page.url
                if not split_career_posting(selected,source):continue
                item=(title,selected,context)
                token=uuid4().hex
                card.evaluate('(el,token)=>el.setAttribute("data-ss-observed-card",token)',token)
            live[token]=item
            identity=posting_identity(item[1])
            if identity not in seen:result.append(item);seen.add(identity)
        except Exception as exc:
            errors.append({'phase':'native_card_selection','reason':redact_text(type(exc).__name__+': '+str(exc))[:600]})
            # One failed card is not proof of end-of-listing or an excuse to
            # fabricate its URL; other independently selectable cards may work.
            continue
    # A pinned original card may disappear after a normal SPA selection.
    # Never reload the listing merely to restore it (that loses load-more).
    if restore is not None:
        try:
            if not restore.is_visible():restore=None
        except Exception:restore=None
    if restore is not None:
        try:
            restore.click(timeout=3000)
            page.wait_for_function(r'''(original)=>location.href===original.url&&document.querySelector('.position-full-card .position-title')?.innerText.trim()===original.title''',arg={'url':source,'title':restore_title},timeout=3000)
        except Exception as exc:
            errors.append({'phase':'restore_selected_card','reason':redact_text(str(exc))[:600]})
    page._ss_split_card_cache=live
    page._ss_split_observed=result
    page._ss_split_errors=errors[-8:]
    return result
