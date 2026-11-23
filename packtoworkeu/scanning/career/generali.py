"""Go Generali's public default directional-perimeter listing, no sign-in.

Endpoints and /home/job/:id/:title route are published by the normal frontend.
Never use profile/saved-job or internal-mobility endpoints, or fabricate #job IDs.
"""
import json
import re
import time
from urllib.parse import quote,urlsplit
from packtoworkeu.core.jd_text import normalize_jd_text

BASE='https://gogenerali.com/rest-api/talent-farm-mediator/'
QUERY='?allData=true&onlyDirectionalPerimeter=true'


def matches(seed):
    p=urlsplit(seed)
    return p.scheme=='https' and p.netloc=='gogenerali.com' and p.path.rstrip('/')=='/home/jobs' and not p.query and not p.fragment


def posting(item):
    rid=str(item.get('contestId') or '')
    title=str(item.get('title') or '').strip()
    if not re.fullmatch(r'ATS-P-[A-Za-z0-9-]{1,90}',rid) or not title:
        raise ValueError('Generali posting ID/title missing or unsupported')
    # These sections are served without authentication by the public ATS
    # listing and shown in public job details, despite their legacy field names.
    sections=[normalize_jd_text(item.get(k) or '') for k in ('descriptionInternalHTML','internalQualificationHTML')]
    locations=[str(item.get('primaryLocation') or '').strip()]
    locations.extend(x.strip() for x in item.get('secondaryLocations') or [] if isinstance(x,str))
    location=' | '.join(dict.fromkeys(x for x in locations if x))
    return dict(job_title=title,job_url='https://gogenerali.com/home/job/'+quote(rid,safe='')+'/'+quote(title,safe=''),
                jd_text='\n\n'.join(dict.fromkeys(x for x in sections if x)),
                location_hint=location,posting_location_field=bool(location),
                observed_hiring_company=str(item.get('companyName') or '').strip(),
                extraction_method='generali_public_ats_api')


def jobs(fetch,check,deadline):
    rows=[];expected=None
    try:
        check()
        if time.monotonic()>=deadline:raise TimeoutError('listing deadline reached')
        census=json.loads(fetch(BASE+'count-available-jobs-from-ats'+QUERY))
        if census.get('error'):raise ValueError('public listing census returned an error')
        expected=census['response']
        if not isinstance(expected,int) or isinstance(expected,bool) or expected<0:raise ValueError('invalid public total')
        check()
        if time.monotonic()>=deadline:raise TimeoutError('listing deadline reached')
        payload=json.loads(fetch(BASE+'get-available-jobs-from-ats'+QUERY))
        if payload.get('error') or not isinstance(payload.get('response'),list):raise ValueError('invalid public listing')
        seen=set()
        for item in payload['response']:
            check()
            if time.monotonic()>=deadline:raise TimeoutError('listing deadline reached')
            if item.get('currentStatus')!='PUBLISHED':raise ValueError('unexpected non-published entry')
            row=posting(item)
            rid=str(item['contestId'])
            if rid in seen:raise ValueError('duplicate published ID')
            seen.add(rid);rows.append(row)
        if len(rows)!=expected:raise ValueError('public listing does not reconcile to count')
        return rows,f'Generali public ATS: {expected} distinct IDs reconciled (default directional perimeter)'
    except Exception as exc:
        from packtoworkeu.scanning.common import ScanCancelled
        if isinstance(exc,ScanCancelled):raise
        return rows,f'listing incomplete: Generali {len(rows)}/{expected}; {type(exc).__name__}: {exc}'
