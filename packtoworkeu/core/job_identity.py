"""Conservative posting identity and source-preserving CSV finalization.

No hostname rules. A numeric token anywhere in a URL is NOT a job identity:
query requisitions, tenants, fragments and scoped boards stay distinct.
"""
from __future__ import annotations

import csv
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit, quote, unquote, urlencode

from packtoworkeu.core.url_normalizer import normalize_url

# Two observed generic endpoint forms for the SAME explicitly numbered job.
# The board/locale/tenant prefix is retained; arbitrary last-number heuristics
# would merge different requisitions sharing a site ID or postcode.
_NUMBERED_JOB = re.compile(r'^(.*?)/(?:job-search/)?jobs/(?:[^/]+-)?(\d{6,})/?$', re.I)
_METADATA_TITLE = re.compile(
    r'^(?:permanent|full[- ]time|part[- ]time|contract|temporary|'
    r'go to job site|view job|view details|apply(?: now)?|read more|learn more)$', re.I)
_UNKNOWN = {'', 'unknown', 'not specified', 'n/a', '?', 'none'}


def merge_sources(*values) -> str:
    seen, result = set(), []
    for value in values:
        for name in str(value or '').split(';'):
            name = name.strip()
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                result.append(name)
    return '; '.join(result)


def portal_queue_key(url: str) -> str:
    """Only normalize scheme/host: preserve path, query order, filters and hash."""
    try:
        p = urlsplit(str(url or '').strip())
        if p.scheme.lower() not in ('http', 'https') or not p.hostname or p.username or p.password:
            return ''
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, p.fragment))
    except ValueError:
        return ''


# Recognised transient access proofs, not job/tenant/content selectors. This
# equivalence is identity-only: original navigation URLs and portal scopes are
# retained, and a readable primary JD must still be independently established.
_ACCESS_PROOF_PARAMETERS = frozenset({'afd_azwaf_tok','__cf_chl_tk','__cf_chl_rt_tk'})


def is_access_proof_parameter(key):
    return str(key or '').casefold() in _ACCESS_PROOF_PARAMETERS


_UNRESERVED = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')


def _canonical_posting_path(path):
    """Encode raw UTF-8; fold escape case/unreserved bytes, never routing delimiters.

    %2F is NOT a slash and %252F is NOT %2F. Query/fragment semantics are
    intentionally outside this path-only representation equivalence.
    """
    encoded = quote(path, safe="/%:@!$&'()*+,;=-._~")
    return re.sub(r'%([0-9a-fA-F]{2})', lambda m: chr(int(m[1],16))
                  if chr(int(m[1],16)) in _UNRESERVED else '%' + m[1].upper(), encoded)


def _posting_url_forms(url):
    normalized = normalize_url(str(url or ''))
    p = urlsplit(normalized)
    canonical = _canonical_posting_path(p.path)
    # Compatibility for old keys/URLs with raw non-ASCII slugs. Only decode
    # valid UTF-8 and unreserved ASCII; keep reserved delimiters escaped.
    def readable(m):
        try:
            text = unquote(m[0], errors='strict')
        except UnicodeDecodeError:
            return m[0]
        return ''.join(c if ord(c)>127 or c in _UNRESERVED else quote(c,safe='') for c in text)
    raw = re.sub(r'(?:%[0-9A-F]{2})+', readable, canonical)
    lower = lambda path: re.sub(r'%[0-9A-F]{2}',lambda m:m[0].lower(),path)
    return tuple(dict.fromkeys([normalized] + [urlunsplit(p._replace(path=path))
                                for path in (canonical,raw,lower(canonical),lower(raw))]))


def find_existing_posting(conn, url):
    """Indexed, bounded compatibility lookup; no database rewrite or table scan.

    Legacy raw/encoded forms are verified with the shared identity before use.
    Existing duplicates, if any, are not silently merged or deleted.
    """
    forms = _posting_url_forms(url)
    key = posting_identity(url)
    keys = tuple(dict.fromkeys((key,) + forms))
    placeholders = ','.join('?' for _ in keys)
    try:
        rows = conn.execute('SELECT * FROM jobs WHERE url IN ('+placeholders+
            ') OR posting_key IN ('+placeholders+') ORDER BY id LIMIT 16',keys+keys).fetchall()
    except Exception as exc:
        if 'no such column: posting_key' not in str(exc):
            raise
        rows = conn.execute('SELECT * FROM jobs WHERE url IN ('+placeholders+
                            ') ORDER BY id LIMIT 16',keys).fetchall()
    matches = [row for row in rows if posting_identity(row['url'])==key]
    if not matches:
        # Mixed-case legacy escapes cannot be enumerated in a fixed IN list.
        # An explicit /job/<numeric-id>/ prefix permits a bounded indexed
        # lookup of that one observed record's aliases, never a host sweep.
        p=urlsplit(normalize_url(url))
        record=re.match(r'^(.*?/(?:job|jobs)/[0-9]+/)',p.path,re.I)
        if record:
            prefix=urlunsplit(p._replace(path=record[1],query='',fragment=''))
            upper=prefix[:-1]+chr(ord(prefix[-1])+1)
            candidates=conn.execute('SELECT * FROM jobs WHERE url>=? AND url<? ORDER BY url LIMIT 65',
                                    (prefix,upper)).fetchall()
            if len(candidates)>64:
                raise ValueError('Posting alias lookup exceeded its bounded record scope; explicit review required')
            matches=[row for row in candidates if posting_identity(row['url'])==key]
    return next((row for row in matches if row['url']==normalize_url(url)), matches[0] if matches else None)


def posting_identity(url: str) -> str:
    normalized = normalize_url(str(url or ''))
    if not normalized:
        return ''
    p = urlsplit(normalized)
    pairs=parse_qsl(p.query,keep_blank_values=True)
    if any(is_access_proof_parameter(k)for k,v in pairs):
        p=p._replace(query=urlencode([(k,v)for k,v in pairs if not is_access_proof_parameter(k)]))
    p = p._replace(path=_canonical_posting_path(p.path))
    normalized = urlunsplit(p)
    # Any hash may select different jobs. Keep it (and its case) intact.
    if p.fragment:
        return normalized
    m = _NUMBERED_JOB.fullmatch(p.path)
    # Explicit query identifiers/filters are not erased in the alias rescue.
    if m and not p.query:
        leaf = p.path.rstrip('/').rsplit('/', 1)[-1]
        slug = leaf[:-len(m[2])].rstrip('-')
        # Multiple requisition/routing numbers (or a date + another number)
        # make the trailing token ambiguous. A shared postcode/site number
        # must NEVER collapse distinct explicit requisitions.
        if re.search(r'\d{4,}', slug):
            return normalized
        return f'{p.scheme.lower()}://{p.netloc.lower()}{m[1]}/jobs/@id:{m[2]}'
    return normalized


_NONPOSTING_TITLE = re.compile(
    r"^(?:location_on|place|expand_more|arrow_forward|google tag manager|tag manager|"
    r"tag manager(?: \d+)? \(new\)|and more|job type|list all|(?:0|no) (?:jobs?|vacancies|positions)(?: available| found)?|"
    r"vai alle offerte salvate|oferty pracy(?: adecco)?|"
    r"(?:view|show|see|browse|search|find|save) (?:all |our |saved )?(?:jobs|vacancies|offers)|"
    r"industria (?:metalmeccanica|manifatturiera)|servizi\s*/\s*terziario|"
    r"[€£$]\s*[\d.,]+(?:\s*[-–]\s*[€£$]?\s*[\d.,]+)?(?:\s*(?:per |/)?(?:hour|month|year|hr|h|pa))?)$", re.I)


def nonposting_title(title: str) -> bool:
    text=' '.join(str(title or '').split())
    # Uppercase Italian industry labels are taxonomy, not occupational titles.
    # Preserve an actual role following the sector, rather than blacklisting sectors.
    sector=bool(re.fullmatch(r'INDUSTRIA [A-ZÀ-ÖØ-Þ /-]+',text))
    role=bool(re.search(r'(?i)\b(?:engineer|manager|director|analyst|specialist|technician|operator|supervisor|operai[oa]|operatore|operatrice|tecnic[oa]|ingegner[ea]|responsabile|addett[oa]|impiegat[oa]|saldatore|magazziniere|carrellista|manutentore|elettricista|consulente)\b',text))
    return bool(_NONPOSTING_TITLE.fullmatch(text) or (sector and not role))


def metadata_title(title: str) -> bool:
    return bool(_METADATA_TITLE.fullmatch(str(title or '').strip()) or nonposting_title(title))


def weak_title(title: str) -> bool:
    """Relative evidence quality ONLY, never an acceptance/identity rule.

    Long first/second-person recruiting sentences should not overwrite an
    actual role heading on the same trusted posting. Keep their fuller JD.
    """
    text=str(title or '').strip()
    if metadata_title(text) or not text:
        return True
    return (len(re.findall(r'\b\w+\b',text))>=8
            and bool(re.search(r'\b(?:we|our|your|you)\b',text,re.I))
            and bool(re.search(r'\b(?:join|grow|build|lead|manage|looking|seeking|hiring|offer)\b',text,re.I)))


_EVERGREEN_TITLE = re.compile(
    r'(?:\b(?:general|open|speculative|proactive|spontaneous|unsolicited|initiative)\s+application\b|'
    r'\btalent\s+(?:pool|community|network)\b|\bcandidate database\b|'
    r'\bcandidatur[ae]\s+spontan(?:e[aeé]|é[e]?)\b|'
    r'\binitiativbewerbung\b|\bopen sollicitatie\b|\bspontane sollicitatie\b)',re.I)
POSTING_CONTEXT_PATTERN = (
    r'^(?:jobs?|job-?details?|find-job|(?:open[-_])?positions?|vacanc(?:y|ies)|'
    r'requisitions?|postings?|openings?|job[-_](?:openings?|offers?)|'
    r'open[-_](?:roles?|vacanc(?:y|ies))|stellenangebote|vacatures?|offres?|offerte|ofertas?)$')
_POSTING_SEGMENT = re.compile(POSTING_CONTEXT_PATTERN,re.I)
_OPAQUE_POSTING_ID = re.compile(r'^(?:[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}|[0-9a-f]{16,64})$',re.I)


def evergreen_title(title: str) -> bool:
    return bool(_EVERGREEN_TITLE.search(str(title or '')))


def split_career_posting(url: str, source_url: str = '') -> bool:
    """Observed /careers?pid= route, tenant bound to this exact career origin.

    Not a global pid allowance. Native discovery still requires an actual
    clicked main-list control and matching selected JobPosting identity.
    """
    try:
        p=urlsplit(str(url or ''));pairs=parse_qsl(p.query);q=dict(pairs)
        if any(sum(k==key for k,v in pairs)!=1 for key in ('pid','domain')):return False
        domain=q.get('domain','').lower().rstrip('.')
        if (p.scheme not in ('http','https') or p.username or p.password or
                p.path.rstrip('/')!='/careers' or not re.fullmatch(r'\d{6,20}',q.get('pid','')) or
                not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+',domain) or
                not (p.hostname==domain or (p.hostname or '').endswith('.'+domain))):return False
        if source_url:
            s=urlsplit(source_url);sq=dict(parse_qsl(s.query))
            return ((p.scheme,p.netloc,p.path)==(s.scheme,s.netloc,s.path) and
                    sorted((k,v)for k,v in pairs if k!='pid')==sorted((k,v)for k,v in parse_qsl(s.query)if k!='pid'))
        return True
    except (ValueError,TypeError):return False


def posting_utility_url(url: str) -> bool:
    """A child location chooser is not another requisition, even under an ID.

    Only a terminal utility segment is rejected; a genuine role whose slug
    mentions locations, or a job with a location query, remains untouched.
    """
    try:
        parts = urlsplit(str(url or ''))
        path = parts.path.rstrip('/')
        if not re.search(r'(?i)/(?:locationpicker|location-picker|location_picker)$', path):
            return False
        parent = parts._replace(path=path.rsplit('/', 1)[0], query='', fragment='')
        # Require a posting-shaped parent, rather than banning a legitimate
        # role called Location Picker directly below /jobs/.
        return (explicit_detail_url(urlunsplit(parent)) or bool(re.search(
            r'(?i)/(?:details|job-?details?)/\d{4,}(?:/[^/]+)?$', parent.path)))
    except ValueError:
        return False


def explicit_detail_url(url: str) -> bool:
    """Acceptance evidence, not a dedupe key. Never an arbitrary site number."""
    try:
        if posting_utility_url(url):return False
        if split_career_posting(url):return True
        parts = urlsplit(url)
        segments = [part for part in parts.path.split('/') if part]
        has_context = any(_POSTING_SEGMENT.fullmatch(part) for part in segments[:-1])
        query_context = any(_POSTING_SEGMENT.fullmatch(part) for part in segments)
        identifiers = {'jobid','job_id','gh_jid','reqid','requisitionid','career_job_req_id','postingid','vacancyid'}
        if any(value.strip() and (key.lower() in identifiers or
               (key.lower() in {'id','jid'} and query_context)) for key,value in parse_qsl(parts.query)):
            return True
        if not has_context:
            return False
        # A numeric-prefix/title.html or title-numeric.html LEAF in posting
        # context is acceptance evidence, not a dedupe/canonicalization rule.
        leaf = segments[-1]
        if re.fullmatch(r'(?:[A-Za-z]{0,3}[-_]?)?\d{4,}[-_][^/]+\.html?|[^/]+[-_]\d{4,}\.html?', leaf, re.I):
            return True
        # A context-bound UUID/hash OR explicit numbered requisition slug.
        return (any(_OPAQUE_POSTING_ID.fullmatch(part) for part in segments[1:])
                or bool(re.search(r'(?:^|[-_])[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$', leaf, re.I))
                or bool(re.search(r'(?:[-_/])(?:[A-Za-z]{0,3}[-_]?)?\d{4,}(?:/|$)',parts.path)))
    except ValueError:
        return False


_ADMIN_ACTION_PATH = re.compile(
    r'(?i)(?:^|/)(?:saved[-_]search(?:es)?|save[-_]search|create[-_]alert|'
    r'job[-_]alerts?|alert[-_]subscription|saved[-_]jobs|save[-_]job|'
    r'newsletter|subscribe|register[-_]cv|submit[-_]cv|salary[-_]calculator)(?:/|$|\.)')
_ACTION_TITLE = re.compile(
    r'(?i)^(?:create(?: a)? job alerts?|save (?:this )?search|subscribe(?: to (?:job )?alerts)?|'
    r'maak (?:een )?vacaturemelding aan|stel (?:een )?vacaturealert in|'
    r'cr[ée]er une alerte(?: emploi)?|crear (?:una )?alerta(?: de empleo)?|'
    r'crea (?:un )?avviso(?: di lavoro)?|jobalarm erstellen)[.! ]*$')


def source_bound_external_posting(url, source_url):
    """Observed Work in Lithuania → cvbankas posting contract, not a global allowlist.

    Admission requires the explicitly selected source board and the provider's
    actual numbered posting route. Neither title nor redirect targets are guessed.
    """
    try:
        source=urlsplit(str(source_url or ''));target=urlsplit(str(url or ''))
        return (source.scheme=='https' and source.hostname=='jobs.workinlithuania.com'
            and target.scheme=='https' and target.hostname in ('cvbankas.lt','www.cvbankas.lt')
            and not target.username and not target.password
            and bool(re.fullmatch(r'/[^/]+/1-[1-9][0-9]{3,}/?',target.path)))
    except ValueError:return False


_INFORMATION_TITLE = re.compile(
    r"^(?:go to jobs|please contact us[! ]*|get on board|meine job alerts|"
    r"your career at .+|your path to .+|"
    r"grow with us and be part of our success story[.! ]*|"
    r"reasons to join .+|hol' dir alle infos[! ]*|"
    r"erfahre, was es bedeutet, bei uns zu arbeiten[.! ]*|"
    r"entdecke deine möglichkeiten[! ]*|entdecke spannende karrierewege .+|"
    r"teil unseres teams werden|für erfahrene|detail screen(?: - .+)?|"
    r"career [i|] .+|jobs at .+|[\w &.-]+ group)$", re.I)


def nonposting_record(url, title='', raw_title=''):
    """Information/analytics cannot become a vacancy via recovered page titles.

    Strong actual requisitions win. Generic-application postings (proactive/
    initiative/unsolicited/speculative/open/talent-pool…) are NOT vacancies
    (user decision 2026-11-09) and never win via a recovered page title.
    This does not infer posting existence from a synthetic fragment alone.
    """
    if posting_utility_url(url):return True
    try:
        fragment=urlsplit(str(url or '')).fragment
        if re.fullmatch(r'(?i)job=tag-manager(?:-[a-z0-9]+)*-?',fragment):return True
    except ValueError:return True
    if str(title or '').strip().casefold() == 'order now':return True
    if evergreen_title(title) or evergreen_title(raw_title):return True
    if explicit_detail_url(url):return False
    return bool(nonposting_url(url) or _INFORMATION_TITLE.fullmatch(' '.join(str(title or '').split()))
                or nonposting_title(title) or nonposting_title(raw_title))


def nonposting_url(url):
    """Structural profiles/taxonomies and identifier-free home pages, not roles."""
    try:
        p=urlsplit(str(url or ''))
        if p.scheme not in ('http','https') or not p.netloc:return False
        if posting_utility_url(url):return True
        if explicit_detail_url(url):return False
        # Generic career-information branches and board/category endpoints.
        if re.search(r'(?i)/(?:career|careers|karriere)/why-[^/]+/(?:development|benefits|culture|values)/?$',p.path):return True
        if re.search(r'(?i)/media/(?:media-library|media-center)(?:/|$)',p.path):return True
        # These are route semantics, not employer-specific URL blacklists.
        if re.search(r'(?i)/(?:career|careers|karriere)/(?:students|career-paths|areas-of-work|inside-[^/]+-careers|[^/]+-top-employer|[^/]+-als-arbeitgeber)(?:/|$)',p.path):return True
        if re.search(r'(?i)/(?:career|careers|karriere)/(?:career-contact|application-process|what-we-offer|schuelerinnen|studierende|berufseinsteiger|berufserfahrene|arbeiten-bei-[^/]+|job-openings)/?$',p.path):return True
        if re.search(r'(?i)/page/[0-9a-f]{24,64}/?$',p.path):return True
        if re.search(r'(?i)/(?:whybayer(?:/|$)|working-at-bayer(?:/|$)|how-to-join-us(?:/|$))',p.path):return True
        if re.search(r'(?i)/careers?/(?:benefits-rewards|development-teamwork|meet_our_employees)/?$',p.path):return True
        # Generic careers-information leaves, not requisitions (Eni evidence
        # run 20261109T211510: welfare / fraud-guidance / opportunities
        # landing pages shipped as jobs under /en-IT/careers/).
        if re.search(r'(?i)/(?:careers?)/[^/]*(?:welfare|job-offers-fraud|fraud-alert|job-opportunities)(?:\.html?)?/?$',p.path):return True
        if not p.path.strip('/') and not p.query and not p.fragment:return True
        return bool(re.search(r'(?i)^/@|/(?:go/[^/]+/\d+|careers?/[^/]+/profile|careers?/jobs_by_[^/]+|lp/[^/]+/[^/]+?)/?$|(?:^|/)(?:working-on-(?:land|board)|overview-apprentices|professionals|vacancies-job-opportunities)(?:\.html?)?/?$',p.path))
    except ValueError:return True


def administrative_action(url, title='') -> bool:
    """Subscription/account furniture and generic applications, not a role.

    No hostname/locale presets; a genuine role can contain `legal`, `about`,
    `saved-search` or `job-alert` in its title/path. IDs are never deduped here.
    """
    if nonposting_url(url):
        return True
    # Generic-application postings are not vacancies (user decision
    # 2026-11-09); checked BEFORE explicit-detail evidence so a numbered
    # aggregator URL (e.g. jobdetail/3992/initiative-application) cannot
    # resurrect one.
    if evergreen_title(title):
        return True
    if explicit_detail_url(url):
        return False
    try:
        return bool(_ADMIN_ACTION_PATH.search(urlsplit(str(url or '')).path) or _ACTION_TITLE.fullmatch(str(title or '').strip()))
    except ValueError:
        return False


def posting_candidate(url, title='', validator=None) -> bool:
    """Common card/anchor acceptance BEFORE observation and page accounting."""
    if not str(url or '').startswith(('https://','http://')):
        return False
    if re.search(r'(?i)\.(?:pdf|png|jpe?g|gif|svg|css|js|zip)(?:$|[?#])', str(url)):
        return False
    if nonposting_record(url,title):
        return False
    if administrative_action(url, title):
        return False
    if explicit_detail_url(url):
        return True
    if callable(validator):
        return bool(validator(url))
    # Lightweight adapters must still carry a detail context, not an arbitrary
    # anchor merely because an outer search-results wrapper looked like a card.
    try:
        parts=[x for x in urlsplit(url).path.split('/') if x]
        return len(parts)>1 and any(_POSTING_SEGMENT.fullmatch(x) for x in parts[:-1])
    except ValueError:
        return False


def record_quality(row) -> tuple:
    """Prefer actual employer detail records over CTA/contract/aggregator copies."""
    title = row.get('Job Title') or row.get('title') or ''
    source = row.get('Source Type') or row.get('source_subtype') or ''
    location = row.get('Job Location') or row.get('location') or ''
    evidence = row.get('Support Evidence') or row.get('support_evidence') or ''
    description = row.get('Raw Description') or row.get('description') or ''
    return (not weak_title(title), source in ('direct_employer', 'direct'),
            bool(evidence), str(location).casefold() not in _UNKNOWN, len(description), len(str(title)))


def merge_job_rows(rows, quality=None):
    """Return (kept, removed_by_seed). Only explicit identity, never title alone."""
    groups, order = {}, []
    for row in rows:
        row = dict(row)
        url = row.get('Job URL') or row.get('url') or ''
        key = posting_identity(url)
        if not key:
            key = f'__unkeyed__:{len(order)}'
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    kept, removed = [], Counter()
    for key in order:
        group = groups[key]
        best = max(group, key=lambda r: (record_quality(r), quality(r) if quality else 0))
        merged = dict(best)
        # Preserve encounter order, not whichever thread happened to win quality.
        source_field = 'Seed Name' if any('Seed Name' in r for r in group) else 'source_name'
        merged[source_field] = merge_sources(*(r.get(source_field, '') for r in group))
        for row in group:
            if row is not best:
                removed[row.get('Seed Name') or row.get('source_name') or row.get('Company Name') or ''] += 1
            for field, value in row.items():
                if str(merged.get(field) or '').strip().casefold() in _UNKNOWN and str(value or '').strip().casefold() not in _UNKNOWN:
                    merged[field] = value
            # A shorter refresh never throws away a full description.
            for field in ('Raw Description', 'description'):
                if len(str(row.get(field) or '')) > len(str(merged.get(field) or '')):
                    merged[field] = row[field]
        if 'Record Status' in merged:
            merged['Record Status'] = 'accepted'
            if 'Quarantine Reason' in merged:
                merged['Quarantine Reason'] = ''
        kept.append(merged)
    return kept, dict(removed)


def read_job_csv(path):
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def write_job_csv(path, rows, columns):
    """Atomic BOM CSV replacement, preserving the previous file on failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def finalize_job_csvs(main_csv, recruiter_csv, columns, quality=None):
    """One main file, all source types. Merges are NEVER sent to quarantine."""
    rows = read_job_csv(main_csv) + read_job_csv(recruiter_csv)
    kept, removed = merge_job_rows(rows, quality=quality)
    write_job_csv(main_csv, kept, columns)
    # The separate file was only an in-flight spool, not a second deliverable.
    Path(recruiter_csv).unlink(missing_ok=True)
    return {'written': len(kept), 'merged': len(rows) - len(kept), 'by_seed': removed}
