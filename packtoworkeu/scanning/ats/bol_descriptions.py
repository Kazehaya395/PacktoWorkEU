"""Bol Greenhouse placeholders: resolve only observed sitemap IDs on its own site.

Never manufacture a slug, follow third-party links, or use related-job cards
as the description. A matching JobPosting identifier AND title is required.
"""
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from packtoworkeu.core.jd_text import normalize_jd_text


def sitemap_jobs(xml):
    result = {}
    for element in ET.fromstring(xml).iter():
        if element.tag.rsplit('}', 1)[-1] != 'loc':
            continue
        url = (element.text or '').strip()
        parsed = urlparse(url)
        match = re.fullmatch(r'/en/jobs/[^/]+/(\d+)/?', parsed.path)
        if parsed.scheme == 'https' and parsed.netloc == 'careers.bol.com' and match:
            result[match[1]] = url
    return result


def posting_description(html, job_id, title):
    expected = ' '.join(title.split()).casefold()
    for raw in re.findall(r'<script\b[^>]*\btype\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>', html, re.I | re.S):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        queue = list(data) if isinstance(data, list) else [data]
        for node in queue:
            if not isinstance(node, dict):
                continue
            queue.extend(node.get('@graph') or [])
            if node.get('@type') != 'JobPosting':
                continue
            identifier = node.get('identifier') or {}
            value = identifier.get('value') if isinstance(identifier, dict) else identifier
            if (str(value) != str(job_id) or
                    ' '.join(str(node.get('title') or '').split()).casefold() != expected):
                continue
            return normalize_jd_text(node.get('description') or '')
    return ''
