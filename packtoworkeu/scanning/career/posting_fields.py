"""Read sibling location labels only inside a positively identified posting card."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import re


class _Cards(HTMLParser):
    void = set('area base br col embed hr img input link meta param source track wbr'.split())

    def __init__(self, base):
        super().__init__(); self.base = base; self.stack = []; self.card = None; self.result = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs); classes = (a.get('class') or '').split(); locator = a.get('data-locator-id') or ''
        if tag in self.void: return
        self.stack.append(tag); depth = len(self.stack)
        if ('js-job' in classes or 'card-with-logo-wrapper' in classes or
                (tag == 'div' and re.fullmatch(r'joblisting_jobcard_[0-9a-fA-F-]{36}', locator))):
            self.card = dict(root=depth, links=set(), locations=set(), li=0, text=[], marked=False,
                             identity=a.get('id') if 'js-job' in classes else locator.removeprefix('joblisting_jobcard_'))
        c = self.card
        if not c: return
        if tag == 'a' and a.get('href') and c['identity']:
            u = urljoin(self.base, a['href']); path = urlsplit(u).path.rstrip('/')
            if urlsplit(u).netloc == urlsplit(self.base).netloc and (path.endswith('/clickout/'+c['identity']) or path.endswith('_'+c['identity'])): c['links'].add(u)
        if tag == 'li': c.update(li=depth,text=[],marked=locator=='joblisting_jobcard_location')
        if tag == 'path' and c['li'] and (a.get('d') or '').startswith('M17.657 16.657l-4.243'): c['marked']=True

    def handle_data(self, text):
        if self.card and self.card['li']: self.card['text'].append(text)

    def handle_endtag(self, tag):
        if tag not in self.stack: return
        idx = len(self.stack)-1-self.stack[::-1].index(tag); c=self.card
        if c and c['li'] and idx < c['li']:
            text=' '.join(' '.join(c['text']).split())
            if c['marked'] and 1 < len(text) <= 150: c['locations'].add(text)
            c['li']=0
        if c and idx < c['root']:
            if len(c['locations']) == len(c['links']) == 1:
                self.result[next(iter(c['links']))]=next(iter(c['locations']))
            self.card=None
        del self.stack[idx:]


def listing_locations(html, base):
    if 'card-with-logo-wrapper' not in html and 'js-job' not in html: return {}
    parser=_Cards(base); parser.feed(html); parser.close(); return parser.result


def confirmed_place(raw):
    # Region, not the board host, disambiguates Halle, Herne and other names.
    parts = [p.strip() for p in raw.split(',')]
    if len(parts)==2 and parts[-1].casefold() in {'vlaams-brabant','oost-vlaanderen','west-vlaanderen','brabant wallon','hainaut','henegouwen','brabant flamand'}:
        return raw + ', Belgium'
    return raw
