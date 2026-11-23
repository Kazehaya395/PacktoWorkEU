from types import SimpleNamespace
from uuid import UUID
import time
import pytest
from packtoworkeu.scanning.career.native_boards import AMS,AmsSearch
from packtoworkeu.scanning.response_evidence import stated_job_total


def test_deadline_minutes_and_job_area_not_counts():
    for minute in ('29','59'):
        html=f'<h6>Deadline: 31/12/2026, 22:{minute}</h6><h6>Job area: Restaurant</h6>'
        assert stated_job_total(html) is None
        assert stated_job_total(html+'<p>29 available jobs</p>') == 29
    assert stated_job_total('<div data-total-jobs="29"></div>')==29


def offers(page, total=69):
    return dict(totalResults=total,totalPages=(total+11)//12,page=page,results=[
        dict(objectType='searchJobOffer',uuid=str(UUID(int=i+1)), title='Same title',
             summary=f'Original German text {i}. '*30,workingLocation=dict(freeText='Stuttgart',countryCode='DE'))
        for i in range((page-1)*12,min(page*12,total))])


def response(n,payload):
    return SimpleNamespace(url=AMS+f'api/search?query=A&jobOfferTypes=BA&page={n}',ok=True,json=lambda:payload)


class Page:
    def __init__(self,adapter,payload=offers):
        self.i=1;self.payload=payload;adapter.observe(response(1,payload(1)))
    def expect_response(self,predicate,timeout):
        self.pred=predicate;return self
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def locator(self,selector):return self
    def click(self,timeout):
        self.i+=1;self.value=response(self.i,self.payload(self.i));assert self.pred(self.value)


def run(payload=offers,cap=20):
    a=AmsSearch(AMS+'jobs?query=A&JOB_OFFER_TYPE=BA');p=Page(a,payload);rows=[];ds=[]
    assert a.harvest(p,rows.append,ds,cap,time.monotonic()+20,lambda:None,str)
    return rows,ds


def test_all_69_keep_distinct_uuid_and_original_text_without_fallback():
    rows,ds=run();assert len(set(r['job_url'] for r in rows))==69
    assert rows[-1]['jd_text']==offers(6)['results'][-1]['summary'];assert rows[0]['posting_country_code']=='DE'
    assert ds==['AMS search: 69 distinct posting IDs on 6 page(s)']


def test_partial_repeats_caps_and_wrong_filters():
    assert 'listing incomplete' in run(cap=2)[1][0]
    assert 'listing incomplete' in run(lambda n:dict(offers(1),page=n))[1][0]
    a=AmsSearch(AMS+'jobs?query=Different');a.observe(response(1,offers(1)));assert a.latest is None
    rows,ds=run(lambda n:offers(n,0));assert not rows and ds[0].startswith('verified empty:')


def test_clipped_search_text_does_not_get_capture_proof():
    def clipped(n):
        d=offers(n,1);d['results'][0]['summary']='A preview '*60+'…';return d
    rows,_=run(clipped);assert not rows[0]['jd_text']


def test_ams_real_seed_default_sort_and_multivalue_filter():
    a=AmsSearch(AMS+'jobs?query=Analyst&sortOrder=desc&sortField=_SCORE&JOB_OFFER_TYPE=IJ&JOB_OFFER_TYPE=BA')
    r=SimpleNamespace(url=AMS+'api/search?query=Analyst&sortField=_SCORE&jobOfferTypes=BA&jobOfferTypes=IJ&page=1',ok=True)
    assert a.matches(r,1)
    r.url=r.url.replace('&jobOfferTypes=IJ','');assert not a.matches(r,1)


def test_emply_rendered_grid_and_hidden_empty_placeholder():
    from playwright.sync_api import sync_playwright
    from packtoworkeu.scanning.career.native_boards import emply_grid
    with sync_playwright() as pw:
        import os
        if not os.path.isfile(pw.chromium.executable_path) and not os.getenv('PACKTOWORKEU_TEST_BROWSER_EXECUTABLE'):
            pytest.skip('requires a test Chromium executable')
        browser=pw.chromium.launch(executable_path=os.getenv('PACKTOWORKEU_TEST_BROWSER_EXECUTABLE'),headless=True)
        page=browser.new_page();root='https://metropole.career.emply.com/en/home';page.route(root,lambda r:r.fulfill(body='<html></html>',content_type='text/html'));page.goto(root)
        markup='''<section class="css_section css_jobs"><div class="ui_jobs_grid"><div class="css_jobsCell"><h5 class="css_headline">Chef de rang</h5><small>Deadline: 22:29</small><small> Job area: Restaurant</small><a class="boxlink" href="/en/ad/chef-de-rang/74vh9q"></a></div></div><div class="ui_jobs_empty" hidden><span class="css_jobsCellNone">We currently have no jobs available.</span></div></section><a href="/apply/unsolicited">Send here</a>'''
        page.set_content(markup);rows=[];logs=[];assert emply_grid(page,rows.append,logs)
        assert [r['job_title'] for r in rows]==['Chef de rang'];assert not any('verified empty' in x for x in logs)
        page.locator('.ui_jobs_grid').evaluate('e=>e.remove()');page.locator('.ui_jobs_empty').evaluate('e=>e.hidden=false');assert emply_grid(page,rows.append,logs);assert logs[-1].startswith('verified empty:')
        page.locator('.css_section').evaluate('e=>e.append("29 available jobs")');assert not emply_grid(page,rows.append,logs)
        browser.close()


def test_emply_original_body_and_posting_identity():
    from packtoworkeu.scanning.career.native_boards import emply_description
    url='https://metropole.career.emply.com/en/ad/chef-de-rang/74vh9q'
    h=f'<meta property="og:url" content="{url}"><div class="csa_jobadText"><p>Première ligne.</p><div>Suivant.</div><script>not prose</script></div><footer>Another hotel</footer>'
    assert emply_description(url,h)=='Première ligne.\nSuivant.'
    assert not emply_description(url+'x',h)
    assert not emply_description(url,h.split('</script>')[0])
