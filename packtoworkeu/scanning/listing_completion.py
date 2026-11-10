"""Conservative, transport-neutral reconciliation of a disappearing pager.

No employer rules. Equality with a headline count alone never certifies an end.
"""
from packtoworkeu.core.job_identity import posting_identity

# Only terminal evidence, not a click selector. Unknown icon controls fail closed.
TERMINAL_CONTROLS_JS = r"""() => {
 const visible = n => !!(n.getClientRects().length) && getComputedStyle(n).visibility !== 'hidden';
 const enabled = n => !n.disabled && n.getAttribute('aria-disabled') !== 'true' && !n.classList.contains('disabled');
 const roots = [...document.querySelectorAll('nav,[role="navigation"],[class*="pagin"],[class*="pager"]')].filter(visible);
 const controls = [...new Set([...roots.flatMap(r => [...r.querySelectorAll('a,button,[role="button"]')]), ...document.querySelectorAll('a[rel="next"],button,[role="button"]')])].filter(visible);
 const label = n => ((n.innerText||'')+' '+(n.getAttribute('aria-label')||'')+' '+(n.getAttribute('title')||'')).trim();
 const next = /\b(next|weiter|nächste|suivant|siguiente|successiv[aoe]?|volgende|load more|show more|mehr laden|mostra altri|carica altri)\b|^[>›»→]$/i;
 const prev = /\b(previous|prev|back|zurück|vorige|précédent|precedente|anterior)\b|^[<‹«←]$/i;
 const forward = controls.some(n => enabled(n) && (n.getAttribute('rel') === 'next' || next.test(label(n))));
 const backward = controls.some(n => enabled(n) && (n.getAttribute('rel') === 'prev' || prev.test(label(n))));
 const unknown = roots.some(r => [...r.querySelectorAll('button,a,[role="button"]')].some(n => visible(n) && enabled(n) && n.querySelector('svg') && !label(n)));
 const busy = [...document.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible);
 return {ready:document.readyState === 'complete', busy, forward, backward, unknown};
}"""


def reconcile_disappeared_pager(samples, saved_urls, current_urls, controls, *, census_complete=False, failed=False):
    """Require disjoint, fully saved pages, stable totals and backward evidence.

    samples contain {identities, total}; identical re-reads are not new pages.
    Caller must establish stable current controls and current-page identities.
    """
    if failed or not census_complete or len(samples) < 2:
        return None
    if (not isinstance(controls, dict) or controls.get('ready') is not True
            or controls.get('busy') is not False or controls.get('forward') is not False
            or controls.get('unknown') is not False or controls.get('backward') is not True):
        return None
    total = samples[0].get('total')
    if type(total) is not int or total <= 0:
        return None
    union = set()
    for sample in samples:
        ids = set(sample.get('identities') or ())
        if sample.get('total') != total or not ids or union.intersection(ids):
            return None
        union.update(ids)
    saved = {posting_identity(u) for u in saved_urls if u}
    current = {posting_identity(u) for u in current_urls if u}
    if (len(union) != total or saved != union or not current
            or current != set(samples[-1]['identities'])):
        return None
    return {'kind': 'disappeared_pager_reconciled', 'stated_total': total,
            'pages': len(samples), 'unique_postings': len(union), 'backward_control': True}


def record_census_page(samples, urls, total):
    identities = sorted({posting_identity(u) for u in urls if u})
    if not identities:
        return
    sample = {'identities': identities, 'total': total}
    if samples and samples[-1]['identities'] == identities:
        # A changed total invalidates previously consistent evidence.
        samples[-1]['total'] = total
    elif len(samples) < 1000:
        samples.append(sample)
