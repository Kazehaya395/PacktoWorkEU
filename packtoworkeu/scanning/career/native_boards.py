"""Read published listing data from the ordinary browser session; no API replay."""
import re
import time
from urllib.parse import parse_qs, urlsplit

from packtoworkeu.scanning.response_evidence import stated_job_total

AMS = 'https://jobs.ams.at/public/emps/'
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I)


class AmsSearch:
    """Accept only the visible search's filter set, then follow its own next button."""
    def __init__(self, seed):
        self.filters = parse_qs(urlsplit(seed).query)
        self.latest = None

    def matches(self, response, page=None):
        if response.url.split('?')[0] != AMS + 'api/search' or not response.ok:
            return False
        q = parse_qs(urlsplit(response.url).query)
        # AMS omits its default descending sort on the wire. It does not omit filters.
        if self.filters.get('sortOrder') == ['desc'] and 'sortOrder' not in q:
            q['sortOrder'] = ['desc']
        aliases = {'JOB_OFFER_TYPE': 'jobOfferTypes'}
        return (all(sorted(q.get(aliases.get(k,k), [])) == sorted(v)
                    for k,v in self.filters.items() if k not in ('page','pageSize'))
                and (page is None or q.get('page') == [str(page)]))

    def observe(self, response):
        if self.matches(response, 1):
            self.latest = response

    def harvest(self, page, process, diagnostics, cap, deadline, check, clean):
        response = self.latest
        if response is None:
            return False  # allow ordinary DOM extraction if no matching search arrived
        seen = set()
        first_total = None
        for number in range(1, cap + 1):
            check()
            try:
                data = response.json()
                total, pages = data['totalResults'], data['totalPages']
                if (type(total) is not int or total < 0 or type(pages) is not int
                        or data['page'] != number or not isinstance(data['results'], list)):
                    raise ValueError('invalid page metadata')
                if first_total is None:
                    first_total = total
                if total != first_total:
                    raise ValueError('published total changed during pagination')
                before = len(seen)
                for item in data['results']:
                    check()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('listing time budget')
                    uid = str(item.get('uuid') or '').lower()
                    if item.get('objectType') != 'searchJobOffer' or not UUID.fullmatch(uid) or not item.get('title'):
                        continue
                    if uid in seen:
                        continue
                    seen.add(uid)
                    loc = item.get('workingLocation') or {}
                    city = loc.get('town') or loc.get('freeText') or loc.get('municipality') or ''
                    code = loc.get('countryCode') or ''
                    jd = item.get('summary') or ''
                    if re.search(r'<(?:p|div|br|span|ul|li|strong)\b', jd, re.I):
                        jd = clean(jd)
                    if jd.rstrip().endswith(('...', '…')):
                        jd = ''  # never stamp a clipped preview as a fresh full capture
                    process(dict(job_url=AMS+'jobs/'+uid, job_title=item['title'],
                                 jd_text=jd, location_hint=city or code, posting_location_field=True,
                                 posting_country_code=code, listing_revision=item.get('lastUpdatedAt'),
                                 ams_employer=(item.get('company') or {}).get('name'),
                                 extraction_method='ams_search_api'))
                if number >= pages:
                    if len(seen) != total:
                        raise ValueError('distinct posting count differs from published total')
                    diagnostics.append(f'AMS search: {len(seen)} distinct posting IDs on {number} page(s)' if total else 'verified empty: AMS search reports zero results')
                    return True
                if len(seen) == before or number >= cap:
                    raise ValueError('no new IDs or page cap reached')
                wait_ms = min(20000, int((deadline-time.monotonic())*1000))
                if wait_ms <= 0:
                    raise TimeoutError('listing time budget')
                check()
                with page.expect_response(lambda r: self.matches(r, number+1), timeout=wait_ms) as next_page:
                    page.locator('button[aria-label="Gehe zur nächsten Seite"]').click(timeout=min(5000,wait_ms))
                response = next_page.value
            except Exception as exc:
                check()  # never convert user cancellation to a successful listing
                diagnostics.append(f'listing incomplete: AMS search page {number}; {len(seen)} distinct IDs of {first_total}; {type(exc).__name__}: {str(exc)[:120]}')
                return True
        return False


def emply_grid(page, process, diagnostics):
    """This tenant's deadline and Job area label are NOT a vacancy count."""
    if urlsplit(page.url).hostname != 'metropole.career.emply.com':
        return False
    root = page.locator('.css_section.css_jobs')
    if root.count() != 1:
        return False
    cells = root.locator('.ui_jobs_grid:visible .css_jobsCell:visible')
    empty = root.locator('.ui_jobs_empty:visible .css_jobsCellNone:visible')
    count = cells.count()
    if root.locator('.ui_jobs_loading:visible').count():
        return False
    expected = stated_job_total(root.inner_text())
    if not count:
        if (empty.count()==1 and empty.inner_text().strip()=='We currently have no jobs available.'
                and expected in (0,None)):
            diagnostics.append('verified empty: visible Emply vacancy grid explicitly reports no jobs')
            return True
        return False
    for cell in cells.all():
        link = cell.locator('a.boxlink[href]'); heading = cell.locator('.css_headline')
        if link.count()!=1 or heading.count()!=1:
            return False
        url = link.evaluate('(a) => a.href'); parsed = urlsplit(url)
        if parsed.hostname != 'metropole.career.emply.com' or not re.fullmatch(r'/[a-z]{2}/ad/[^/]+/[\w-]+/?',parsed.path):
            return False
        process(dict(job_url=url, job_title=heading.inner_text().strip(), extraction_method='emply_grid'))
    if ((expected is not None and expected > count) or root.locator('nav:visible,[rel=next]:visible,button:visible,[class*=pagin i]:visible').count()):
        return False  # let generic traversal handle a future paginated grid
    diagnostics.append(f'Emply vacancy grid: {count} visible posting cards (before generic-application filtering)')
    return True


def emply_ad(url):
    return bool(re.fullmatch(r'https://metropole\.career\.emply\.com/[a-z]{2}/ad/[^/?#]+/[\w-]+/?', str(url)))


def emply_description(url, html):
    """Only the ad body, never JS, hotel footers, other ads or a login form."""
    from html.parser import HTMLParser

    class Ad(HTMLParser):
        def __init__(self):
            super().__init__(); self.depth=0; self.blocks=0; self.og=[]; self.text=[]; self.hidden=None

        def handle_starttag(self, tag, attrs):
            attrs=dict(attrs)
            if tag=='meta' and attrs.get('property')=='og:url':
                self.og.append(attrs.get('content',''))
            if tag=='div' and 'csa_jobadText' in (attrs.get('class') or '').split() and not self.depth:
                self.depth=1; self.blocks+=1
            elif self.depth:
                if tag=='div': self.depth+=1
                if tag in ('script','style'): self.hidden=tag
                if tag in ('br','p','li','div','h2','h3'): self.text.append('\n')

        def handle_endtag(self,tag):
            if tag==self.hidden: self.hidden=None
            if self.depth and tag=='div': self.depth-=1
            if self.depth and tag in ('p','li','h2','h3'): self.text.append('\n')

        def handle_data(self,data):
            if self.depth and not self.hidden: self.text.append(data)

    parser=Ad(); parser.feed(html); parser.close()
    if not emply_ad(url) or parser.blocks!=1 or parser.depth or parser.og!=[url]:
        return ''
    return '\n'.join(' '.join(line.split()) for line in ''.join(parser.text).splitlines() if line.strip())
