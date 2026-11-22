"""Read-only, title-scoped JD analysis, independent of the mixed-source job_terms index."""
from collections import Counter, defaultdict, OrderedDict
from pathlib import Path
from time import monotonic
import hashlib
import re
import unicodedata

from packtoworkeu.core.db_tasks import DatabaseReadTask, current_database_task
from packtoworkeu.core.jd_text import competency_text, normalize_jd_text
from packtoworkeu.core.saved_details import _validated_capture
from packtoworkeu.insights import terms

ALIASES = {'sde': ('software development engineer', 'software engineer', 'swe'),
           'swe': ('software engineer', 'software development engineer', 'sde'),
           'software engineer': ('swe','sde','software development engineer'),
           'software development engineer': ('swe','sde','software engineer')}


def title_matcher(query, mode):
    q = query.strip()
    if not q or len(q)>160: raise ValueError('Enter a title or pattern of 1–160 characters.')
    norm = lambda s: unicodedata.normalize('NFKC', s).casefold()
    if mode == 'role':
        choices=(norm(q), *ALIASES.get(norm(q), ()))
        patterns=[r'(?<!\w)'+r'[\s\-_/]+'.join(map(re.escape,s.split()))+r'(?!\w)' for s in choices]
        pat=re.compile('|'.join(patterns));return lambda s: bool(pat.search(norm(s)))
    if mode not in ('wildcard','regex'):raise ValueError('Unknown title match mode.')
    if mode=='wildcard':
        # Standard-library glob matching; only * and ? are special in this UI.
        from fnmatch import fnmatchcase
        glob=norm(q).replace('[','[[]')
        return lambda title: fnmatchcase(norm(title),glob)
    try:
        import regex
    except ImportError as exc:
        raise ValueError('Regex mode needs the regex package. Use Role match or Wildcard, or install it with the Python interpreter that launches the app.') from exc
    try: pat=regex.compile(q, regex.IGNORECASE)
    except (regex.error,OverflowError) as exc:raise ValueError('Invalid title pattern.') from exc
    budget=2.0
    def match(title):
        nonlocal budget
        start=monotonic()
        try:
            if budget<=0:raise TimeoutError
            return bool(pat.search(title, timeout=min(budget,.025)))
        except TimeoutError as exc:raise ValueError('Pattern took too long. Simplify it.') from exc
        finally:budget-=monotonic()-start
    return match


class RoleIndex:
    """One serial AsyncLoader owns this bounded session cache. No user-DB writes."""
    def __init__(self, limit=32768):
        self.cache=OrderedDict();self.limit=limit

    def analyze(self,path,query,mode='role',country='',source='',legacy=False):
        task=current_database_task()
        if task is None:
            with DatabaseReadTask():return self.analyze(path,query,mode,country,source,legacy)
        match=title_matcher(query,mode);terms._load_data();version=terms.taxonomy_version()
        conn=task.connect(Path(path));conn.execute('BEGIN')
        where='verified_active=1 AND is_expired=0';params=[]
        for col,val in [('country',country),('source_subtype',source)]:
            if val:where+=' AND '+col+'=?';params.append(val)
        ids=[];titles=Counter();counts=Counter();examples=defaultdict(list)
        totals=dict(matched=0,proven=0,legacy=0,excluded=0,analyzed=0)
        for jid,title in conn.execute('SELECT id,title FROM jobs WHERE '+where,params):
            if task.cancelled.is_set():raise InterruptedError
            if match(title): ids.append(jid);titles[title]+=1
        totals['matched']=len(ids)
        for i in range(0,len(ids),256):
            chunk=ids[i:i+256];marks=','.join('?' for _ in chunk)
            for jid,title,company,body,marker in conn.execute(f'SELECT id,title,company,description,support_evidence_type FROM jobs WHERE id IN ({marks}) ORDER BY id',chunk):
                if task.cancelled.is_set():raise InterruptedError
                body=body or '';marker=marker or ''
                # Age governs scanner reuse, not whether previously saved bytes match their proof.
                proven=_validated_capture({'description':body},marker,max_age_seconds=float('inf'))
                if proven:totals['proven']+=1
                elif ('jd_sha256=' not in marker and not re.search(r'jd_read_status=(?!readable\b)',marker)
                      and len(normalize_jd_text(body))>=200):
                    totals['legacy']+=1
                    if not legacy:continue
                else:continue
                totals['analyzed']+=1
                key=(version,hashlib.sha256(body.encode()).digest());hits=self.cache.get(key)
                if hits is None:
                    text=competency_text(body);hits={}
                    for item in terms._iter_matched_skills(text):
                        raw=item['term'];kind=terms.skill_kind(raw)
                        # Both dictionary entries recognize communication skills; keep
                        # their multilingual patterns, but count a posting only once.
                        term='Communication' if raw=='Communication skills' else raw
                        if kind not in ('technical_skill','soft_skill') or term in hits:continue
                        m=item['regex'].search(text);hits[term]=(kind,' '.join(text[max(0,m.start()-55):m.end()+85].split()))
                    self.cache[key]=hits
                    if len(self.cache)>self.limit:self.cache.popitem(last=False)
                else:self.cache.move_to_end(key)
                for term,(kind,snippet) in hits.items():
                    counts[kind,term]+=1
                    if len(examples[term])<5:examples[term].append((title,company,snippet))
        totals['excluded']=len(ids)-totals['analyzed']
        return dict(**totals,titles=titles.most_common(12),examples=dict(examples),
                    ranks={kind:sorted(((term,n) for (k,term),n in counts.items() if k==kind),key=lambda p:(-p[1],p[0].casefold()))[:25] for kind in ('technical_skill','soft_skill')})
