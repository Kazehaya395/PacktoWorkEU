"""Bounded, observed primary-job metadata; no hostname or employer inference."""
from __future__ import annotations
import re
from sponsorscout.core.location_country import country_from_location


def structured_job_locations(job):
    """Accept Schema.org Place/address lists and Country objects without coercing dicts."""
    def scalar(value):
        if isinstance(value,str):return value.strip()
        if isinstance(value,dict):return scalar(value.get('name') or value.get('value') or '')
        return ''
    raw=job.get('jobLocation') if isinstance(job,dict) else None
    places=raw if isinstance(raw,list) else [raw]
    result=[]
    for place in places[:32]:
        if not isinstance(place,dict):continue
        addresses=place.get('address') or (place if place.get('@type')=='PostalAddress' else {})
        if not isinstance(addresses,list):addresses=[addresses]
        for address in addresses[:32]:
            if not isinstance(address,dict):continue
            fields=[scalar(address.get(k)) for k in ('addressLocality','addressRegion','addressCountry')]
            value=', '.join(dict.fromkeys(x for x in fields if x))
            if value and value not in result:result.append(value)
    return result


def title_job_location(title, iso3=None):
    """An explicit `role in City, COUNTRY | brand` title, not arbitrary prose."""
    match=re.search(r'\s+in\s+([^|\n]{2,160},\s*[^|\n,]{2,50})\s*\|',title or '')
    if not match:return ''
    value=match[1].strip();last=value.rsplit(',',1)[-1].strip()
    if isinstance(iso3,dict) and last in iso3:
        value=value.rsplit(',',1)[0]+', '+iso3[last]
    return value if country_from_location(value) else ''


def load_job_json(text):
    """Decode JSON data, tolerating literal whitespace inside publisher strings.

    strict=False accepts literal control characters (e.g. a multiline HTML JD),
    not JavaScript, trailing commas, repaired quotes, or guessed metadata.
    """
    import json
    return json.loads(str(text).lstrip('\ufeff'), strict=False)


def header_metadata_locations(html):
    """Explicit Location-labelled social metadata and vacancy-header badges only."""
    from html import unescape
    from html.parser import HTMLParser

    class Metadata(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.values = {}
        def handle_starttag(self, tag, attrs):
            if tag.lower() == 'meta':
                attrs = dict(attrs)
                self.values[(attrs.get('name') or attrs.get('property') or '').casefold()] = attrs.get('content') or ''

    parser = Metadata()
    # Social metadata belongs to <head>; do not parse an entire large JD DOM
    # just to discover there are no labelled metadata pairs.
    text = str(html or '')
    head = re.split(r'(?i)</head\s*>', text, maxsplit=1)[0][:120_000]
    if re.search(r'(?i)twitter:(?:label|data)[12]', head):
        parser.feed(head)
    out = []
    for index in ('1', '2'):
        label = parser.values.get('twitter:label' + index, '').strip().casefold()
        value = parser.values.get('twitter:data' + index, '').strip()
        if label in {'location', 'standort', 'luogo', 'sede', 'locatie', 'ubicación'} and 2 <= len(value) <= 100:
            out.append(value)
    h1 = re.search(r'(?is)<h1\b[^>]*>.*?</h1>', str(html or ''))
    if h1:
        header = str(html)[h1.end():h1.end() + 2500]
        header = re.split(r'(?is)<(?:h[1-6]|article|footer|section)\b', header, maxsplit=1)[0]
        for match in re.finditer(r'''(?is)<(li|span|p|div)\b[^>]*class=["'][^"']*\b(?:location|job-location)\b[^"']*["'][^>]*>(.*?)</\1>''', header):
            value = re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', match[2]))).strip()
            if 2 <= len(value) <= 100:
                out.append(value)
    return list(dict.fromkeys(out))
