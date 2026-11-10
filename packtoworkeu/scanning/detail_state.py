"""One run-local detail budget/cache shared by support, location and title reads.

No network activity at construction. Failure caches are run-local (not a
permanent verdict about a host). Memory is bounded; cache hits spend no budget.
"""
from __future__ import annotations

import threading
import time
from collections import Counter, OrderedDict
from urllib.parse import urlsplit
from packtoworkeu.scanning.response_evidence import is_plain_http_refusal


class DetailRequestState:
    def __init__(self,cache_size=512):
        self.lock = threading.RLock()
        self.gates = [threading.RLock() for _ in range(64)]
        self.results = OrderedDict()
        self.metadata = OrderedDict()
        self.failed = set()
        self.browser_attempted = set()
        self.http_retried = set()
        self.owners = {}
        self.memberships = {}
        self.outcomes = {}
        self.scopes = {}
        self.total = 0
        self.cache_size = max(16,int(cache_size))

    def gate(self,url):
        return self.gates[hash(url)%len(self.gates)]

    def register(self,url,scope):
        if url and scope:
            with self.lock:
                self.owners.setdefault(url,scope)
                self.memberships.setdefault(url,set()).add(scope)

    def owner(self,url):
        return self.owners.get(url) or urlsplit(url).netloc.lower()

    @staticmethod
    def _new_scope():
        # Issue 005 P5: `busy` is the time actually spent inside detail fetches, charged
        # by reserve()/observe() pairs. The budget used to be measured from `started` on
        # the wall clock, so a board whose LISTING phase was slow consumed the budget
        # before its details began: Hays Germany made 3 detail requests, skipped 1,896
        # postings and reported `stop=shared detail time budget exhausted`.
        return {'started':None,'requests':0,'browser':0,'counts':Counter(),'stops':set(),
                'busy':0.0,'pending':{},'inflight':0,'span':None,'unattributed':0}

    def _scope(self,url):
        key = self.owner(url)
        data = self.scopes.setdefault(key,self._new_scope())
        # Issue 005 P4/C5: `owner()` falls back to the hostname for a URL that no seed
        # ever registered, so those responses are counted against the *host*, not against
        # a company. Track how many, so summary() can say the bucket is not one seed's
        # postings instead of leaving a reader to divide 712 blocked by 706 jobs found.
        if url and self.owners.get(url) != key:
            data['unattributed'] += 1
        return data

    def count(self,url,kind):
        with self.lock:
            self._scope(url)['counts'][kind] += 1

    def count_of(self,scope,kind):
        """Counter value for a scope's own bucket; 0 when it never had one."""
        with self.lock:
            data = self.scopes.get(scope) or {}
            return int((data.get('counts') or {}).get(kind, 0) or 0)

    def _charge(self,url):
        """Bill the span during which at least one detail read was in flight.

        Issue 005 P8: summing per-URL spans instead over-charges a scope whose reads run
        concurrently — the detail reader opens up to 3 parallel tabs, so run 20261029T134232
        printed `detail busy=3s elapsed=2s` for NH Hotel Group Italy, i.e. more time had been
        billed than had passed. The budget is a wall-clock budget, so the union of the
        in-flight intervals is the quantity it should measure.
        """
        scope=self._scope(url)
        if url not in (scope.get('pending') or {}):
            return                      # an outcome for a URL we never reserved
        scope['pending'].pop(url,None)
        if scope.get('inflight',0)>0:
            scope['inflight']-=1
        if not scope['inflight'] and scope.get('span') is not None:
            scope['busy']=scope.get('busy',0.0)+max(0.0,time.monotonic()-scope['span'])
            scope['span']=None

    def get(self,url):
        with self.lock:
            result = self.results.get(url)
            if result is not None:
                self.results.move_to_end(url)
            return result

    def get_metadata(self,url):
        with self.lock:
            metadata = self.metadata.get(url)
            if metadata is not None:
                self.metadata.move_to_end(url)
            return metadata

    def put(self,url,result,metadata=None):
        with self.lock:
            old = self.results.get(url)
            # Do not replace a full JD with a shell from a later phase.
            if old is None or len(str(result[0] or ''))>=len(str(old[0] or '')):
                self.results[url] = tuple(result)
                self.results.move_to_end(url)
            if metadata is not None:
                self.metadata[url] = metadata
                self.metadata.move_to_end(url)
            for cache in (self.results,self.metadata):
                while len(cache)>self.cache_size:
                    cache.popitem(last=False)

    def failure(self,url,blocked=False,reason="",**evidence):
        with self.lock:
            self._charge(url)
            self.failed.add(url)
            self._scope(url)['counts']['blocked' if blocked else 'failed'] += 1
            self.observe(url,kind='blocked' if blocked else 'failed',reason=reason,**evidence)
            self.put(url,('','',''))

    def remaining_seconds(self, url, config):
        """Remaining active detail time, including reads still in flight."""
        with self.lock:
            scope = self._scope(url)
            limit = float(getattr(config, 'DETAIL_SCAN_TIME_BUDGET_SEC', 0) or 0)
            if limit <= 0:
                return None
            busy = scope.get('busy', 0.0)
            if scope.get('span') is not None:
                busy += max(0.0, time.monotonic() - scope['span'])
            return max(0.0, limit - busy)

    def allowed(self,url,config,browser=False,*,retry=False):
        with self.lock:
            if url in self.failed:
                failure = self.outcomes.get(url, {})
                # One ordinary browser fallback after a failed HTTP read is
                # allowed, never after an actual access wall or 404/410.
                plain = is_plain_http_refusal(failure.get('reason'), failure.get('http_status'))
                http_failure = str(failure.get('transport') or '').casefold() == 'http'
                ordinary_browser = (browser and url not in self.browser_attempted
                    and ((failure.get('kind') == 'failed' and failure.get('http_status') not in (404, 410, 429))
                         or (plain and http_failure and failure.get('http_status') == 403)))
                rate_retry = (retry and not browser and plain and http_failure
                              and failure.get('http_status') == 429 and url not in self.http_retried)
                if not (ordinary_browser or rate_retry):
                    return False
            if browser and url in self.browser_attempted and not retry:
                return False
            scope = self._scope(url)
            reason = ''
            if scope['requests']>=config.MAX_DETAIL_SCAN_PER_COMPANY:
                reason = 'per-target request cap'
            elif self.total>=config.MAX_DETAIL_SCAN_TOTAL and scope['requests']>=config.DETAIL_MIN_PER_COMPANY:
                reason = 'global request cap (reserved floor used)'
            elif self.remaining_seconds(url, config) == 0:
                # Issue 005 P5: charged to time spent fetching details, not to wall clock
                # since the first detail was reserved. A slow listing phase can no longer
                # starve the board's own descriptions; MAX_COMPANY_TIME_SEC still bounds the
                # seed overall.
                reason = 'shared detail time budget exhausted'
            elif browser and scope['browser']>=getattr(config,'DETAIL_BROWSER_FALLBACK_PER_COMPANY',12):
                reason = 'shared browser fallback cap'
            if reason:
                scope['stops'].add(reason)
                scope['counts']['budget_skipped'] += 1
                return False
            return True

    def reserve(self,url,config,browser=False,*,retry=False):
        with self.lock:
            if not self.allowed(url,config,browser=browser,retry=retry):
                return False
            scope = self._scope(url)
            if scope['started'] is None:
                scope['started'] = time.monotonic()
            if retry and not browser:
                self.http_retried.add(url)
                scope['counts']['rate_limit_retried'] += 1
            scope['requests'] += 1
            self.total += 1
            now=time.monotonic()
            pending=scope.setdefault('pending',{})
            if url not in pending:
                scope['inflight']=scope.get('inflight',0)+1
                if scope['inflight']==1:
                    scope['span']=now   # start of the union envelope
            pending[url]=now
            scope['counts']['browser' if browser else 'http'] += 1
            if browser:
                scope['browser'] += 1
                self.browser_attempted.add(url)
            return True

    def summary(self,scope):
        with self.lock:
            data = self.scopes.get(scope)
            if data is None:
                return 'detail activity: no additional requests (saved/provider text only)'
            counts = data['counts']
            stats = '; '.join(f'{name}={counts.get(name,0)}' for name in
                              ('http','browser','reused','fetched','failed','blocked',
                               'host_breaker_deferred','budget_skipped'))
            for name in ('http_cooldown_deferred','queue_deadline_skipped','http_recovery_probe','rate_limit_retried','rate_limit_deferred'):
                if counts.get(name):stats += f'; {name}={counts[name]}'
            stops = ', '.join(sorted(data['stops'])) or 'none'
            # Issue 005 P4: this line is printed per seed, but the bucket is keyed by the
            # URL's owner, so a host shared by several seeds aggregates all of them.
            # Say so rather than let a reader divide `blocked=712` by this seed's 706
            # postings and conclude the board blocked more jobs than it has.
            # Two directions of the same ambiguity (Issue 005 C5): responses counted here
            # that a seed never registered, and responses this host carries in its own
            # fallback bucket that the seed's line does not show. Either way the numbers on
            # this line are not "this seed's postings", and saying so beats a reader doing
            # the subtraction (NH Hotel Group Italy: blocked=712 against found=706).
            notes = []
            extra = data.get('unattributed') or 0
            if extra:
                notes.append('%d counted here belong to no seed of their own (host-keyed fallback)' % extra)
            host = urlsplit(scope).netloc.lower() if '://' in str(scope or '') else ''
            hostdata = self.scopes.get(host) if host and host != scope else None
            if hostdata is not None:
                hc = hostdata['counts']
                held = sum(hc.values())
                if held:
                    notes.append('host %s also holds %d response(s) this seed does not show'
                                 % (host, held))
            shared = ('; ' + '; '.join(notes)) if notes else ''
            timing=''
            if data.get('busy') or data.get('started') is not None:
                timing='; detail busy=%.0fs elapsed=%.0fs' % (
                    data.get('busy',0.0),
                    (time.monotonic()-data['started']) if data.get('started') is not None else 0.0)
            return f'detail activity: {stats}; stop={stops}{shared}{timing}'


    def observe(self,url,text='',*,kind='',reason='',transport='',status=0,title=''):
        """Keep semantic outcomes separately from request/cache counters.

        Outcomes are run-local and bounded by this run's admitted detail URLs.
        A later genuinely readable response resolves a previous challenge;
        cache reuse alone does not fabricate resolution or spend a request.
        """
        with self.lock:
            self._charge(url)
            old=self.outcomes.get(url,{})
            length=len(str(text or '').strip())
            kind=kind or ('readable' if length>=200 else 'thin' if length else 'empty')
            if kind == 'readable' or (kind in ('thin', 'empty') and 200 <= int(status or 0) < 300
                    and is_plain_http_refusal(old.get('reason'), old.get('http_status'))):
                self.failed.discard(url)  # A successful ordinary retry may still need browser rendering.
            if old.get('kind')=='readable' and kind in ('thin','empty'):
                return
            self.outcomes[url]={'url':url,'kind':kind,'reason':str(reason or '')[:800],
                'transport':str(transport),'http_status':int(status or 0),
                'title':str(title or '')[:180],
                'jd_chars':max(length,int(old.get('jd_chars') or 0))}

    def scope_blocked(self,scope):
        with self.lock:
            return [dict(value) for url,value in self.outcomes.items()
                    if scope in self.memberships.get(url,()) and value.get('kind')=='blocked']

    def coverage(self,scope,urls=None,jd_lookup=None):
        with self.lock:
            selected=({url for url,scopes in self.memberships.items() if scope in scopes}
                      if urls is None else set(urls))
            result=Counter(observed=len(selected),readable=0,thin=0,empty=0,failed=0,blocked=0,unattempted=0)
            for url in selected:
                value=self.outcomes.get(url,{})
                saved=str(jd_lookup(url) or '') if callable(jd_lookup) else ''
                if len(saved.strip())>=200 or value.get('kind')=='readable':
                    result['readable']+=1
                elif value.get('kind') in ('thin','empty','failed','blocked'):
                    result[value['kind']]+=1
                else:
                    result['unattempted']+=1
            return dict(result)

    def coverage_summary(self,scope,urls=None,jd_lookup=None):
        data=self.coverage(scope,urls,jd_lookup)
        return 'JD coverage: '+ '; '.join(f'{key}={value}' for key,value in data.items())

    # ── FIX P19c (2026-10-28): transport-level visibility ──────────────────
    # A retry used to leave no trace at all. _response_failure() records an
    # outcome only for statuses that look like a wall, and observe() lets a
    # later readable response outrank an earlier 429, so run 20261028T192816
    # logged its two slowest seeds (Philips 2,959.8 s, Prysmian 2,897.3 s --
    # 72.6% of the whole run) as "failed=0; blocked=0; Attempts=1". Nothing
    # below changes a verdict or a counter that any reader already consumes:
    # these are extra names on the same diagnostics line, printed only when
    # they are non-zero, so a healthy seed's text is byte-identical.
    def note_transport(self, scope, kind, amount=1):
        """Count a transport/detail event against a seed's scope."""
        if not scope or not kind:
            return
        with self.lock:
            # Same factory as _scope(): a bucket created here must not be missing the
            # keys the detail accounting reads, or observe() trips on it.
            data = self.scopes.setdefault(scope, self._new_scope())
            data['counts'][kind] += amount

    #: names shown by transport_summary(), in order
    TRANSPORT_KEYS = ('retries', 'throttled_429', 'server_5xx',
                      'network_errors', 'detail_errors', 'no_id',
                      'empty_listing_item', 'dropped_no_id',
                      'skipped_external_url', 'no_container',
                      'budget_skipped')

    def transport_summary(self, scope):
        """Return 'transport: ...' for this scope, or '' when all zero."""
        with self.lock:
            counts = (self.scopes.get(scope) or {}).get('counts') or {}
            parts = [f'{name}={counts[name]}' for name in self.TRANSPORT_KEYS
                     if counts.get(name)]
            backoff = counts.get('backoff_seconds')
            if backoff:
                position = 1 if counts.get('retries') else 0
                parts.insert(position, f'backoff={round(float(backoff), 1)}s')
            if not parts:
                return ''
            return 'transport: ' + ' '.join(parts)


    def scope_samples(self,scope,limit=4):
        with self.lock:
            samples=[]
            for url,value in self.outcomes.items():
                if scope not in self.memberships.get(url,()) or value.get('kind') not in ('empty','thin','failed','blocked'):
                    continue
                samples.append('response evidence: '+ '; '.join(f'{key}={value.get(key,"")}' for key in
                    ('url','kind','transport','http_status','jd_chars','title','reason')))
                if len(samples)>=limit:break
            return samples
