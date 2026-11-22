from types import SimpleNamespace
from packtoworkeu.scanning.career.posting_fields import listing_locations, confirmed_place
from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner, _static_next_page_url, sniff_provider
from packtoworkeu.core.location_evidence import observed_geography


def test_no_card_cross_contamination():
    base='https://englishjobsearch.at/jobs/Analyst'
    card='<div class="js-job" id="one"><a href="/clickout/one?q=a">Job</a><li><svg><path d="M17.657 16.657l-4.243"/></svg>Vienna</li></div>'
    assert listing_locations(card+card.replace('one','two').replace('M17.657','not-a-pin'),base)=={'https://englishjobsearch.at/clickout/one?q=a':'Vienna'}


def test_randstad_pager_and_province():
    base='https://www.randstad.be/nl/jobs/'
    assert _static_next_page_url('<a href="/werknemers/jobs/page-2/">2</a>',base).endswith('/werknemers/jobs/page-2/')
    assert not _static_next_page_url('<a href="https://other.test/jobs/page-2/">2</a>',base)
    assert confirmed_place('Halle, Vlaams-Brabant')=='Halle, Vlaams-Brabant, Belgium'
    assert confirmed_place('Halle')=='Halle'
    assert observed_geography('Are','','address')==('','')


def test_oracle_job_identity_and_filtered_route():
    url='https://tenant.oraclecloud.com/hcmUI/CandidateExperience/nl/sites/CX_1001/jobs'
    assert sniff_provider('',url)[:2]==('oracle','CX_1001')
    assert sniff_provider('',url+'?keyword=test')[0]!='oracle'
    fake=SimpleNamespace(cancel_event=None,pause_event=None,_http_fetch_with_retry=lambda *_:'{"items":[{"requisitionList":[{"Id":5925,"Title":"Example","PrimaryLocation":"Waterloo","PrimaryLocationCountry":"BE"}]}]}')
    rows,_=CareerPortalScanner._fetch_oracle_jobs(fake,{'careers_url':url})
    assert rows[0]['job_url']==url.removesuffix('/jobs')+'/job/5925' and rows[0]['posting_country_code']=='BE'
