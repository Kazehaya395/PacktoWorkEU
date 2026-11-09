"""Narrow card-evidence guards; no employer allowlists or URL fabrication."""
from __future__ import annotations
import re

# Exact industry/facet labels observed as card headings. Do not reject role
# substrings (e.g. Automotive Engineer), or ambiguous job-family role titles.
CATEGORY_LABELS = frozenset({
    'automotive', "commercio all'ingrosso", 'elettronica/ elettrotecnica',
    'turistico/ alberghiero & ristorazione', 'sanitario/biomedicale',
    'trasporti/ logistica', 'edile/ immobiliare', 'consulenza',
    'grande distribuzione organizzata', 'media/editoria/ giornalismo/ spettacolo',
    'agricoltura/ ambiente/ allevamento/ pesca', 'marketing/ advertising/ public relations',
})

def category_label(title: str) -> bool:
    return ' '.join(str(title or '').split()).casefold() in CATEGORY_LABELS


def card_metadata_title(title: str) -> bool:
    return bool(re.fullmatch(r'(?i)associated with\s+\d+\s+categor(?:y|ies)',
                             str(title or '').strip()))

# Family names can also be genuine roles. Only reject them when the supposed
# identity was synthesized FROM that same family/location text, never an
# observed provider ID or individual requisition URL.
FAMILY_LABELS = frozenset({
    'gdo / retail / commessi / scaffalisti', 'magazzino / logistica / trasporti',
    'operai generici', 'commerciale / vendita', 'operai specializzati',
    'bar / catering / personale di sala / cuochi / chef', 'finanza / contabilità',
    'assistenziale / paramedico / tecnico', 'segreteria / servizi generali',
    'installazione / impiantistica / cantieristica', 'manutenzione / riparazione',
    'moda / spettacolo / eventi',
    # Only title-derived stand-ins are rejected below. Real posting IDs and
    # URLs may legitimately carry short role titles such as Intern.
    'business functions, finance', 'business functions, marketing',
    'corporate it & security', 'engineering, data engineering',
    'engineering, software engineering', 'sales, field sales',
    'sales, inside sales', 'sales, key account',
    'fixed term', 'unlimited contract', 'intern', 'working student',
})

def family_label(title):
    return ' '.join(str(title or '').split()).casefold() in FAMILY_LABELS


def generated_family_card(title,url):
    from urllib.parse import urlsplit
    fragment=urlsplit(str(url or '')).fragment
    # Require the exact synthJobUrl title-derived prefix. An observed opaque
    # provider ID containing a double hyphen must not be mistaken for it.
    slug=re.sub(r'[^a-z0-9]+','-', ' '.join(str(title or '').split()).lower()).strip('-')[:70]
    prefix,sep,location=fragment.partition('--')
    return family_label(title) and prefix=='job='+slug and bool(sep and location)


def provider_slug(value,url,provider):
    """Sentinel 0 isn't an account; derive only from an observed provider URL."""
    from urllib.parse import urlsplit
    text=str(value or '').strip()
    if text.casefold() not in {'','0','none','null','n/a','nan'}:return text
    host=(urlsplit(str(url or '')).hostname or '').lower()
    if provider=='personio':
        match=re.fullmatch(r'([a-z0-9][a-z0-9-]*)\.jobs\.personio\.(?:de|com)',host)
        return match[1] if match and match[1]!='0' else ''
    return ''
