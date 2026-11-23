"""Resolve only the public Oracle API explicitly published by a vanity shell."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

VANITY_HOSTS = {'jobs.eni.com', 'carriere.posteitaliane.it'}


def published_api(html):
    class Bases(HTMLParser):
        def __init__(self):
            super().__init__(); self.bases = []
        def handle_starttag(self, tag, attrs):
            if tag == 'base': self.bases.append(dict(attrs))
    p = Bases(); p.feed(html); p.close()
    if len(p.bases) != 1:
        raise ValueError('Oracle shell must publish exactly one base')
    a = p.bases[0]; raw = a.get('data-apibaseurl') or ''; site = a.get('data-sitenumber') or ''
    u = urlsplit(raw)
    if not (u.scheme == 'https' and (u.hostname or '').endswith('.oraclecloud.com')
            and not u.username and not u.password and u.port in (None, 443)
            and u.path in ('', '/') and not u.query and not u.fragment
            and re.fullmatch(r'[A-Za-z0-9_]+', site)
            and re.fullmatch(r'/[\w-]+/sites/' + re.escape(site) + r'/?', a.get('href') or '')):
        raise ValueError('Oracle shell did not publish a valid public API/site pair')
    return 'https://' + u.hostname, site
