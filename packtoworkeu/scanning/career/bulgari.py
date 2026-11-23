"""Published Bulgari Talentlink board data, not hotel-only inferred vacancies.

The exact configured board is preserved. Guest request headers and endpoint
are those published by its ordinary Talentlink widget. No credentials/session,
challenge workarounds, invented job IDs or guessed geography are used.
"""
import json
import time
from urllib.parse import urlsplit
from packtoworkeu.core.jd_text import normalize_jd_text

BOARD = 'https://bulgari.recruitmentplatform.com/FO/PCAFK026203F3VBQB798N7V11/'
SITE = 'PCAFK026203F3VBQB798N7V11'
API = 'https://emea3.recruitmentplatform.com/fo/rest/jobs'


def matches(seed):
    p=urlsplit(seed)
    return p.scheme=='https' and p.netloc=='bulgari.recruitmentplatform.com' and p.path==f'/FO/{SITE}/index.html'


def posting(item):
    fields=item.get('jobFields') or {}
    rid=str(fields.get('id') or '')
    if not rid.isdigit() or str(item.get('id',rid))!=rid:
        raise ValueError('Bulgari posting ID missing or inconsistent')
    title=str(fields.get('jobTitle') or fields.get('sJobTitle') or '').strip()
    if not title:raise ValueError('Bulgari posting title missing')
    sections=[]
    for section in item.get('customFields') or []:
        body=normalize_jd_text(section.get('content') or '')
        if body:
            sections.extend([str(section.get('title') or '').strip(),body])
    location=str(fields.get('FFIELD005_001') or '').strip()
    country=str(fields.get('SLOVLIST44') or '').strip()
    if country and country.casefold() not in location.casefold():
        location=', '.join(filter(None,[location,country]))
    return dict(job_title=title,job_url=BOARD+'details.html?jobId='+rid,
                location_hint=location,posting_location_field=bool(location),
                jd_text='\n\n'.join(x for x in sections if x),
                card_context=' | '.join(str(fields.get(k) or '') for k in ('SLOVLIST14','SLOVLIST7')),
                extraction_method='bulgari_talentlink_api')


def jobs(seed,fetch,check,deadline,cap=250):
    """Reconcile all IDs to the published total; retain partial observations."""
    if not matches(seed):raise ValueError('Not the configured Bulgari board')
    headers={'User-Agent':'Mozilla/5.0','Accept':'application/json',
             'lumesse-language':'en','username':SITE+':guest:FO','password':'guest'}
    records={};total=None;offset=0;error=''
    try:
        for _ in range(cap):
            check()
            if time.monotonic()>=deadline:raise TimeoutError('listing deadline reached')
            url=API+f'?firstResult={offset}&maxResults=20&sortBy=Date&sortOrder=DESC'
            data=json.loads(fetch(url,headers,{'searchCriteria':{'criteria':[]}}))
            count=int(data['globals']['jobsCount'])
            if total is None:total=count
            if total!=count:raise ValueError('published listing total changed')
            batch=data['jobs']
            before=len(records)
            for item in batch:
                row=posting(item)
                if row['job_url'] in records:raise ValueError('repeated posting ID during pagination')
                records[row['job_url']]=row
            if len(records)==total:
                note=(f'Bulgari Talentlink: {total} distinct posting IDs reconciled; '
                      'configured board is brand-wide, not a hotel-only filter')
                return list(records.values()),note
            if len(records)>total or len(records)==before or not batch:
                raise ValueError('listing does not reconcile to published total')
            offset+=len(batch)
        else:raise ValueError('listing page cap reached')
    except Exception as exc:
        # Cancellation must still escape to the caller's checkpoint handler.
        from packtoworkeu.scanning.common import ScanCancelled
        if isinstance(exc,ScanCancelled):raise
        error=f'{type(exc).__name__}: {exc}'
    return list(records.values()),f'listing incomplete: Bulgari {len(records)}/{total}; {error}'
