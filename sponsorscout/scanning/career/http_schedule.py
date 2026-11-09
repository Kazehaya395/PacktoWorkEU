"""Scanner-wide bounded HTTP admission; no I/O, browsers or background threads."""
from __future__ import annotations
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import threading
import math
import time
from urllib.parse import urlsplit
from sponsorscout.scanning.common import check_control


class HttpSchedule:
    def __init__(self, total=3, per_host=2):
        self.total=max(1,int(total));self.per_host=min(self.total,max(1,int(per_host)))
        self.condition=threading.Condition();self.active=0;self.host_active={};self.cooldowns={}
        self.by_scope={};self.waiters=[];self.sequence=0;self.turn=0;self.last_served={}
        self.metrics={'admitted':0,'queue_seconds':0.0,'fetch_parse_seconds':0.0,'peak':0,'rate_limited':0}

    def defer(self,url,retry_after=None):
        delay=30.0
        try:
            delay=max(0.0,float(retry_after))
        except (TypeError,ValueError):
            try:delay=max(0.0,parsedate_to_datetime(str(retry_after)).timestamp()-time.time())
            except (TypeError,ValueError,OverflowError):pass
        if not math.isfinite(delay):delay=30.0
        host=urlsplit(url).netloc.lower()
        with self.condition:
            self.cooldowns[host]=max(self.cooldowns.get(host,0),time.monotonic()+delay)
            self.metrics['rate_limited']+=1;self.condition.notify_all()

    def _bucket(self,scope,phase):
        return self.by_scope.setdefault(scope or "unattributed",{}).setdefault(phase or "other",{})

    @contextmanager
    def slot(self,url,cancel=None,pause=None,deadline=None,blocked=None,*,scope="",phase="other",wait_cooldown=False):
        host=urlsplit(url).netloc.lower();start=time.monotonic();admitted=False
        cooldown_recorded=False
        scope=scope or host
        with self.condition:
            self.sequence+=1
            ticket=(self.sequence,scope,host)
            self.waiters.append(ticket)
        try:
            while not check_control(cancel,pause):
                if blocked is not None and blocked():break
                with self.condition:
                    now=time.monotonic()
                    if deadline is not None and now>=deadline:break
                    if now<self.cooldowns.get(host,0):
                        until=self.cooldowns[host]
                        bucket=self._bucket(scope,phase)
                        if not cooldown_recorded:
                            name='cooldown_retained' if wait_cooldown else 'cooldown_deferred'
                            self.metrics[name]=self.metrics.get(name,0)+1
                            bucket[name]=bucket.get(name,0)+1
                            cooldown_recorded=True
                        # Retain the ticket, not network capacity. A long server
                        # delay is deferred honestly, never waited out overnight.
                        if not wait_cooldown or deadline is None or until>=deadline or until-start>30.0:
                            if wait_cooldown:
                                bucket['cooldown_beyond_wait_budget']=bucket.get('cooldown_beyond_wait_budget',0)+1
                            break
                        self.condition.wait(min(.1,until-now,max(.001,deadline-now)))
                        continue
                    # Round-robin by target, FIFO within a target. Ineligible
                    # hosts never reserve global capacity or block another host.
                    eligible=[w for w in self.waiters
                              if self.host_active.get(w[2],0)<self.per_host
                              and now>=self.cooldowns.get(w[2],0)]
                    chosen=min(eligible,key=lambda w:(self.last_served.get(w[1],0),w[0])) if eligible else None
                    if self.active<self.total and chosen==ticket:
                        self.active+=1;self.host_active[host]=self.host_active.get(host,0)+1
                        self.turn+=1;self.last_served[scope]=self.turn
                        self.metrics['admitted']+=1;self.metrics['peak']=max(self.metrics['peak'],self.active)
                        bucket=self._bucket(scope,phase)
                        bucket['admitted']=bucket.get('admitted',0)+1
                        self.waiters.remove(ticket)
                        admitted=True;break
                    self.condition.wait(min(.05,max(.001,deadline-now)) if deadline is not None else .05)
        finally:
            queued=time.monotonic()-start
            with self.condition:
                if ticket in self.waiters:self.waiters.remove(ticket)
                self.metrics['queue_seconds']+=queued
                bucket=self._bucket(scope,phase)
                bucket['queue_seconds']=bucket.get('queue_seconds',0.0)+queued
                self.condition.notify_all()
        read_start=time.monotonic()
        try:yield admitted
        finally:
            if admitted:
                with self.condition:
                    elapsed=time.monotonic()-read_start
                    self.metrics['fetch_parse_seconds']+=elapsed
                    bucket=self._bucket(scope,phase)
                    bucket['fetch_parse_seconds']=bucket.get('fetch_parse_seconds',0.0)+elapsed
                    self.active-=1;self.host_active[host]-=1;self.condition.notify_all()

    def is_cooling(self,url):
        with self.condition:return time.monotonic()<self.cooldowns.get(urlsplit(url).netloc.lower(),0)

    def note(self,name,seconds,*,scope="",phase="other"):
        with self.condition:
            value=max(0.0,seconds)
            self.metrics[name]=self.metrics.get(name,0.0)+value
            bucket=self._bucket(scope,phase)
            bucket[name]=bucket.get(name,0.0)+value

    def scope_snapshot(self,scope):
        with self.condition:
            return {phase:{k:round(v,3) if isinstance(v,float) else v for k,v in values.items()}
                    for phase,values in self.by_scope.get(scope,{}).items()}

    def snapshot(self):
        with self.condition:
            metrics={k:round(v,3) if isinstance(v,float) else v for k,v in self.metrics.items()}
            return {**metrics,'active':self.active,'global_limit':self.total,'per_host_limit':self.per_host}
