"""Italian career run regression fixtures. No network and no user DB writes."""
import json
import ssl
import time
import pytest
from packtoworkeu.scanning.career import bulgari
from packtoworkeu.core.location_evidence import observed_geography
from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner


def item(rid=123,city='New York'):
    return dict(id=rid,jobFields=dict(id=rid,jobTitle='Northeast Visual Merchandising Specialist',FFIELD005_001=city),customFields=[dict(title='Mission',content='<p>'+'Build the regional merchandising strategy. '*20+'</p>')])


def test_bulgari_uses_distinct_title_location_and_complete_jd():
    row=bulgari.posting(item())
    assert row['job_title']=='Northeast Visual Merchandising Specialist'
    assert row['location_hint']=='New York' and row['posting_location_field']
    assert len(row['jd_text'])>200 and '<p>' not in row['jd_text']
    assert row['job_url'].endswith('details.html?jobId=123')
    assert observed_geography(row['location_hint'],row['location_hint'],'api')[1]=='United States'


def test_unproven_whole_card_does_not_become_iceland():
    raw='Northeast Visual Merchandising Specialist Merchandising Minimum 3 years New York Permanent'
    assert observed_geography('Unknown',raw,'none')==('','')
    assert observed_geography('Shanghai','Shanghai','card')[1]=='China'


def test_bulgari_reconciles_pagination():
    calls=[]
    def fetch(url,headers,body):
        calls.append(url)
        assert headers['username']==bulgari.SITE+':guest:FO'
        assert body=={'searchCriteria':{'criteria':[]}}
        ids=range(1,21) if len(calls)==1 else range(21,24)
        return json.dumps(dict(globals={'jobsCount':23},jobs=[item(i) for i in ids]))
    rows,note=bulgari.jobs(bulgari.BOARD+'index.html?languageSelect=EN',fetch,lambda:None,time.monotonic()+10)
    assert len(rows)==23 and '23 distinct posting IDs reconciled' in note
    assert 'firstResult=20' in calls[1]


def test_bulgari_partial_page_is_not_success():
    def fetch(*args):return json.dumps(dict(globals={'jobsCount':5},jobs=[item()]))
    rows,note=bulgari.jobs(bulgari.BOARD+'index.html',fetch,lambda:None,time.monotonic()+10)
    assert len(rows)==1 and note.startswith('listing incomplete:')


def test_bulgari_cancellation_propagates():
    from packtoworkeu.scanning.common import ScanCancelled
    def check():raise ScanCancelled('stop')
    with pytest.raises(ScanCancelled):bulgari.jobs(bulgari.BOARD+'index.html',lambda *a:None,check,time.monotonic()+10)


@pytest.mark.parametrize('url,rid',[
    ('https://jobs.eni.com/en-US/sites/CX_1001/job/34159','34159'),
    ('https://carriere.posteitaliane.it/en/sites/CX_3001/job/418','418')])
def test_official_oracle_vanity_routes_keep_origin_and_identity(url,rid):
    api,actual=CareerPortalScanner._oracle_detail_route(url)
    assert actual==rid and api.startswith(url.split('/sites/')[0].rsplit('/',1)[0]+'/hcmRestApi/')
    assert f'Id={rid},siteNumber=' in api


@pytest.mark.parametrize('url',[
    'http://jobs.eni.com/en-US/sites/CX_1001/job/34159',
    'https://jobs.eni.com.evil.invalid/en-US/sites/CX_1001/job/34159',
    'https://user@jobs.eni.com/en-US/sites/CX_1001/job/34159'])
def test_oracle_route_does_not_expand_trust(url):
    assert CareerPortalScanner._oracle_detail_route(url) is None


def test_provider_transport_retains_verified_tls(monkeypatch):
    import urllib.request
    from types import SimpleNamespace
    s=CareerPortalScanner(input_csv='unused',skip_preflight=True)
    contexts=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'{"jobs": []}'
    def open_request(req,timeout,context):
        contexts.append(context)
        assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
        return Response()
    monkeypatch.setattr(urllib.request,'urlopen',open_request)
    assert s._http_fetch_with_retry('https://fixture.invalid/api',{})=='{"jobs": []}'
    assert len(contexts)==1


def test_generali_uses_posting_title_not_category():
    from packtoworkeu.scanning.career import generali
    r=generali.posting(dict(contestId='ATS-P-123-abc',title='Regulatory Reporting Maternity leave',primaryLocation='Milano',secondaryLocations=['Milano'],descriptionInternalHTML='<p>Responsibilities</p>',internalQualificationHTML='<p>Qualifications</p>',companyName='Banca Generali S.p.A.'))
    assert r['job_title']=='Regulatory Reporting Maternity leave'
    assert '#job=' not in r['job_url'] and '/home/job/ATS-P-123-abc/' in r['job_url']
    assert r['location_hint']=='Milano' and r['observed_hiring_company']=='Banca Generali S.p.A.'
    assert r['jd_text']=='Responsibilities\n\nQualifications'


@pytest.mark.parametrize('expected,complete',[(1,True),(2,False)])
def test_generali_census_must_reconcile(expected,complete):
    from packtoworkeu.scanning.career import generali
    one=dict(contestId='ATS-P-123-abc',title='Analyst',currentStatus='PUBLISHED')
    def fetch(u):return json.dumps(dict(error=None,response=expected if 'count-available' in u else [one]))
    rows,note=generali.jobs(fetch,lambda:None,time.monotonic()+10)
    assert len(rows)==1
    assert note.startswith('Generali public ATS:' if complete else 'listing incomplete:')


def test_bulgari_dispatch_does_not_use_dom(monkeypatch):
    s=CareerPortalScanner(input_csv='unused',skip_preflight=True)
    def fetch(url,headers,parse_json=True,post_body=None):
        assert url.startswith(bulgari.API) and post_body is not None
        return json.dumps(dict(globals={'jobsCount':1},jobs=[item()]))
    monkeypatch.setattr(s,'_http_fetch_with_retry',fetch)
    rows,note=s._fetch_provider_jobs({'careers_url':bulgari.BOARD+'index.html?languageSelect=EN','provider':'auto','board_slug':'0'})
    assert len(rows)==1 and rows[0]['extraction_method']=='bulgari_talentlink_api'


def test_generali_dispatch_does_not_create_synthetic_fragments(monkeypatch):
    s=CareerPortalScanner(input_csv='unused',skip_preflight=True)
    def fetch(url,headers):
        return json.dumps(dict(error=None,response=1 if 'count-available' in url else [dict(contestId='ATS-P-123-abc',title='Analyst',currentStatus='PUBLISHED')]))
    monkeypatch.setattr(s,'_http_fetch_with_retry',fetch)
    rows,note=s._fetch_provider_jobs({'careers_url':'https://gogenerali.com/home/jobs','provider':'auto','board_slug':'0'})
    assert len(rows)==1 and '/home/job/ATS-P-' in rows[0]['job_url']


@pytest.mark.parametrize('value',[None,'','   '])
def test_search_unknown_location_is_explicit(value):
    from packtoworkeu.ui.tabs.search import _row_values
    row=dict(title='Analyst',company='Fixture',country='',location=value,_exp_display='?',visa_sponsorship='',eu_blue_card_verdict='',relocation_support='',remote_type='',first_seen_at='')
    assert _row_values(row)[3]=='Unknown'


def test_closed_tools_tab_discards_late_lookup_without_masking_other_errors():
    from types import SimpleNamespace
    from packtoworkeu.ui.tabs.tools import ToolsTab
    def deleted(*args):raise RuntimeError('Signal source has been deleted')
    ToolsTab._publish_resume_lookup(SimpleNamespace(_resume_lookup_done=SimpleNamespace(emit=deleted)),('late',))
    def broken(*args):raise RuntimeError('unrelated problem')
    with pytest.raises(RuntimeError,match='unrelated'):
        ToolsTab._publish_resume_lookup(SimpleNamespace(_resume_lookup_done=SimpleNamespace(emit=broken)),('late',))
