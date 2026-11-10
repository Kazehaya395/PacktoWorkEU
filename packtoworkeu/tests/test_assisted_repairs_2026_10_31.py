"""Approved A–E: offline artifact cases and synthetic safety controls, no live portals."""
import csv
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote
from types import SimpleNamespace
import pytest
from packtoworkeu.core import job_identity as ji, blocked_portals as bp
from packtoworkeu.core import browser_evidence as be
from packtoworkeu.core.assisted_resume import observed_url_in_scope, listing_continuation
from packtoworkeu.scanning.jd_support import JDSupportDetector

BASE='https://careers.fixture.invalid/global/en'
SLUGS = ['Pharmazie‑Praktikum-gem-§4-Apotheken‑Approbationsverordnung', '高性能封装（先进封装）首席集成技术专家', '应用销售专家-Protein-and-Cell-Biology', '业务分析师-IT-上海', '助理客户经理', '客户主任-甲状腺-深圳', 'DxRM-관련-경력-8년-이상', 'Field-Marketing-분석화학-서울', 'Technical-Sourcing-관련-경력-15년', '分析师', '高级区域医学顾问-肿瘤-北京', 'Apprenti-Opérateur-trice-sur-machine-automatisées', '-高级-产品经理', 'Field-Service-新竹preferred', '售后服务副经理', '应用销售专家-药物早研产品，广州']

@pytest.mark.parametrize('slug',SLUGS)
def test_unicode_posting_identity(slug):
    raw=BASE+'/job/301789/'+slug;encoded=BASE+'/job/301789/'+quote(slug)
    assert ji.posting_identity(raw)==ji.posting_identity(encoded)
    assert ji.posting_identity(ji.posting_identity(raw))==ji.posting_identity(raw)

@pytest.mark.parametrize('a,b',[
 ('/job/1/a%2Fb','/job/1/a/b'),('/job/1/a%252Fb','/job/1/a%2Fb'),
 ('/job/1/Foo','/job/1/foo'),('/job/1/a','/job/2/a'),('/tenant-a/job/1/a','/tenant-b/job/1/a'),
 ('/job/1/a?req=1','/job/1/a?req=2'),('/job/1/a#A','/job/1/a#a'),
 ('/job/1/a%3Fb','/job/1/a?b'),('/job/1/%FF','/job/1/ÿ'),
])
def test_identity_does_not_merge_routing_boundaries(a,b):
    assert ji.posting_identity(BASE+a)!=ji.posting_identity(BASE+b)

@pytest.mark.parametrize('a,b',[('/job/1/caf%c3%a9','/job/1/caf%C3%A9'),('/job/1/%61','/job/1/a')])
def test_identity_safe_escape_equivalence(a,b):
    assert ji.posting_identity(BASE+a)==ji.posting_identity(BASE+b)

def test_identity_different_hosts_and_malformed():
    assert ji.posting_identity(BASE+'/job/1/a')!=ji.posting_identity('https://other.invalid/global/en/job/1/a')
    assert ji.posting_identity(BASE+'/job/1/%zz')!=ji.posting_identity(BASE+'/job/1/%25zz')

@pytest.mark.parametrize('encoded_first',[True,False])
def test_find_legacy_alias_without_migration(encoded_first):
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY,url TEXT,posting_key TEXT,title TEXT)')
    raw=BASE+'/job/1/工程师';enc=BASE+'/job/1/'+quote('工程师')
    first,second=(enc,raw)if encoded_first else(raw,enc)
    conn.execute('INSERT INTO jobs VALUES(1,?,?,?)',(first,first,'Engineer'))
    row=ji.find_existing_posting(conn,second)
    assert row['id']==1
    assert ji.find_existing_posting(conn,second.replace('/job/1/','/job/2/'))is None
    conn.close()

class HrefPage:
    def __init__(self,url,hrefs):self.url=url;self.hrefs=hrefs
    def eval_on_selector_all(self,*a):return [{'h':h,'rel':''}for h in self.hrefs]

@pytest.mark.parametrize('step',[7,10,25])
def test_offset_forward_href_and_scope(step):
    root=BASE+'/search-results?country=DE';cur=root+'&from='+str(step*9)
    nxt=root+'&from='+str(step*10)
    page=HrefPage(cur,[cur,nxt,root+'&from='+str(step*8),root.replace('DE','US')+'&from='+str(step*10)])
    assert bp.paging_href_candidates(page,cur,set())==[nxt]
    assert bp.next_page_url(cur,10)is None,'an offset step must be observed, never guessed as +1'
    assert observed_url_in_scope(root,nxt)
    assert not observed_url_in_scope(root,nxt.replace('DE','US'))
    assert bp._paging_value(nxt)==step*10

def test_offset_window_traversal_and_no_repeats():
    root=BASE+'/search-results?country=DE';cur=root;seen={root}
    for n in range(1,12):
        nxt=root+'&from='+str(n*10)
        p=HrefPage(cur,[cur,nxt]);c=bp.paging_href_candidates(p,cur,seen)
        assert c==[nxt];cur=c[0];seen.add(cur)
    assert len(seen)==12
    assert bp.paging_href_candidates(HrefPage(cur,[cur]),cur,seen)==[]

def test_listing_continuation_retains_filters():
    root=BASE+'/search-results?country=DE';key=ji.portal_queue_key(root)
    state={'url':root+'&from=100','page':11,'provenance':'observed_forward_link','portal_key':key}
    batch={'url':root,'portal_key':key,'pages':10,'listing_completed':False,'continuation':state}
    assert listing_continuation(batch)==state
    batch['continuation']={**state,'url':state['url'].replace('DE','US')}
    with pytest.raises(ValueError):listing_continuation(batch)

def payload(**kw):
    p=dict(version=1,url=BASE+'/home',title='Careers',headings=['Careers'],anchors=[],primary_text='',main_text='Career information',body_preview='Career information',overlays=[],active_ids=[],foreground_frames=[])
    p.update(kw);return p

class RenderPage:
    frames=[]
    def __init__(self,data,status=200):self.data=data;self.url=data['url'];self._assisted_nav_status=status;self.reloaded=False
    def evaluate(self,*a):return dict(self.data)
    def content(self):return '<main>'+self.data['body_preview']+'</main>'
    def title(self):return self.data['title']
    def reload(self,**kw):self.reloaded=True


def test_error_page_is_not_a_listing():
    page=RenderPage(payload(title='',body_preview="404 - Oh no! The page you're looking for disappeared",main_text='404 - Oh no!'),404)
    s=be.inspect_page(page)
    assert s['document_error']and '404'in s['document_error']
    assert not s['ready'] and not s['listing_ready']
    assert not s['reason'],'404 must not be reclassified as a human-verification wall'

def test_applause_rendered_unextractable_not_reloaded():
    text='Explore Our Openings '+'Strategic Account Manager US-Remote Job Details '*4
    page=RenderPage(payload(headings=['Job Openings','Explore Our Openings'],body_preview=text,main_text=text))
    s=be.inspect_page(page)
    assert s['rendered_unextractable'] and not s['ready'] and s['visible_job_links']==0
    assert bp._recover_blank_page(page,page.url,1,wait_s=.01)=='unextractable'
    assert not page.reloaded

@pytest.mark.parametrize('text',['Read more about our culture','Privacy policy Job Details','Explore Our Openings','Job Details'])
def test_marketing_text_does_not_imply_rendered_jobs(text):
    assert not be.inspect_page(RenderPage(payload(body_preview=text,main_text=text)))['rendered_unextractable']

def test_career_entry_hops_are_bounded_and_evidence_based():
    root=BASE+'/home';search=BASE+'/search-results'
    p=payload(anchors=[{'url':search,'title':'Search jobs'},{'url':'https://evil.invalid/login','title':'Jobs'},{'url':BASE+'/culture','title':'Our culture'}])
    assert be.career_listing_links(p)==[search]
    assert be.career_listing_links({**p,'anchors':[{'url':'https://evil.invalid/jobs','title':'Jobs'}]})==[]
    assert be.career_listing_links({**p,'anchors':[{'url':BASE+'/privacy','title':'Search jobs'}]})==[]

@pytest.mark.parametrize('text',[
 'Support equipment installations, qualifications, relocations, decommissions, and upgrade projects.',
 'Manage equipment installations, relocations and maintenance.',
 'Coordinate laboratory equipment relocation projects.',
])
def test_equipment_duties_do_not_offer_employee_relocation(text):
    r=JDSupportDetector().detect(text)['relocation'];assert r['verdict']=='Unknown'
    assert not any(e[0]=='Yes'for e in r['evidence'])

@pytest.mark.parametrize('joiner',['. ', '; ', ', and ', ' and '])
def test_equipment_duty_preserves_independent_offer(joiner):
    text='Support equipment installations, qualifications, relocations and upgrade projects'+joiner+'we offer relocation assistance to employees.'
    r=JDSupportDetector().detect(text)['relocation'];assert r['verdict']=='Yes'
    assert all('equipment'not in e[3]for e in r['evidence']if e[0]=='Yes')

@pytest.mark.parametrize('text,expected',[
 ('We offer relocation assistance and provide equipment.','Yes'),
 ('Relocation: Yes. Support equipment installations and relocations.','Yes'),
 ('Relocation: No. Support equipment installations and relocations.','No'),
 ('We do not provide relocation assistance.','No'),('Relocation -','Unknown'),
])
def test_employee_benefits_refusals_and_placeholders_preserved(text,expected):
    assert JDSupportDetector().detect(text)['relocation']['verdict']==expected

@pytest.mark.parametrize('http_readable',[True,False])
def test_http_and_browser_use_same_encoding_identity(monkeypatch,http_readable):
    from packtoworkeu.tests.test_assisted_transport_2026_10_25 import fake_reader
    from packtoworkeu.tests.test_assisted_support_2026_10_24 import html_ld,POS
    raw=BASE+'/job/301789/工程师';enc=BASE+'/job/301789/'+quote('工程师')
    reader,state,requests,visits,page=fake_reader(monkeypatch)
    page.goto=lambda url,**kw:setattr(page,'url',enc)or SimpleNamespace(status=200)
    page.content=lambda:html_ld(POS,enc)
    if http_readable:
        reader.context.request.get=lambda u,**kw:SimpleNamespace(status=200,url=enc,text=lambda:html_ld(POS,enc),dispose=lambda:None)
    result=reader.read(raw)
    assert result.status=='readable' and result.text
    assert result.transport==('HTTP (assisted session)'if http_readable else'browser (assisted session)')
    reader.close()

@pytest.mark.parametrize('encoded_first',[True,False])
def test_real_upsert_and_resume_do_not_duplicate_alias(tmp_path,encoded_first):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.persistence import upsert_job
    from packtoworkeu.core.assisted_resume import existing_records
    path=tmp_path/'alias.db';db.initialize(path);conn=db.get_connection(path)
    raw=BASE+'/job/301789/工程师';enc=BASE+'/job/301789/'+quote('工程师')
    first,second=(enc,raw)if encoded_first else(raw,enc)
    record=dict(url=first,title='Engineer',company='Fixture',source_type='direct_employer',source_subtype='direct',source_name='Fixture',ats_source='assisted_browser')
    assert not bp._persist_assisted_job(conn,record)
    # Simulate the OLD key, not the freshly canonicalized key.
    conn.execute('UPDATE jobs SET posting_key=?',(first,));conn.commit()
    assert bp._persist_assisted_job(conn,{**record,'url':second})
    assert conn.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
    pairs=existing_records(conn,{'rows':[{'url':second}]})
    assert pairs[0][0]is not None
    upsert_job(conn,{**record,'url':second.replace('/301789/','/301790/')})
    assert conn.execute('SELECT count(*) FROM jobs').fetchone()[0]==2
    conn.close()

def test_fresh_read_can_retire_old_equipment_false_positive(tmp_path):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.persistence import upsert_job
    from packtoworkeu.core.support_application import apply_support_to_job
    from packtoworkeu.tests.test_assisted_support_2026_10_24 import scanner,job,BASE as JD_BASE
    text='Support equipment installations, qualifications, relocations, decommissions, and upgrade projects.'
    path=tmp_path/'equipment.db';db.initialize(path);conn=db.get_connection(path)
    original=job(relocation_support='Y',support_evidence=text,support_evidence_type='explicit_detail_sentence; jd_read_status=readable')
    upsert_job(conn,original)
    fresh=apply_support_to_job(scanner(),job(),JD_BASE+text)
    assert fresh['relocation_support']=='Unknown'
    upsert_job(conn,fresh)
    assert conn.execute('SELECT relocation_support FROM jobs').fetchone()[0]=='Unknown'
    conn.close()

@pytest.mark.parametrize('text',[
 'We offer our office employees relocation assistance.',
 'We support laboratory staff with relocation assistance.',
 'We provide relocation support and equipment to employees.',
])
def test_employee_benefit_not_an_object_duty(text):
    assert JDSupportDetector().detect(text)['relocation']['verdict']=='Yes'

def test_entry_navigation_stops_after_observed_hop_budget(monkeypatch):
    root=BASE+'/home';first=BASE+'/search-results';second=BASE+'/jobs';third=BASE+'/vacancies'
    pages={root:payload(url=root,anchors=[{'url':first,'title':'Search jobs'}]),first:payload(url=first,anchors=[{'url':second,'title':'Search jobs'}]),second:payload(url=second,anchors=[{'url':third,'title':'Search jobs'}])}
    page=RenderPage(pages[root]);visited=[]
    def goto(url,**kw):visited.append(url);page.url=url;page.data=pages[url]
    page.goto=goto;page.wait_for_timeout=lambda ms:None
    monkeypatch.setattr(bp,'_record_assisted_evidence',lambda *a,**kw:{})
    bp._discover_listing_entry(page,max_hops=2)
    assert visited==[first,second]

def test_error_snapshot_never_admits_recommended_postings():
    links=[{'url':BASE+'/job/'+str(n)+'/engineer','title':'Engineer','in_card':True}for n in range(3)]
    s=be.inspect_page(RenderPage(payload(anchors=links),404))
    assert s['admitted_anchors']==[] and s['visible_job_links']==0

def test_error_recovery_does_not_reload_or_prompt():
    page=RenderPage(payload(),404)
    assert bp._recover_blank_page(page,page.url,1,wait_s=.01)=='error'
    assert not page.reloaded

def test_entry_link_does_not_discard_selected_filter():
    p=payload(url=BASE+'/home?country=DE',anchors=[{'url':BASE+'/jobs','title':'Search jobs'}])
    assert be.career_listing_links(p)==[]
    p['anchors'][0]['url']+='?country=DE'
    assert be.career_listing_links(p)==[BASE+'/jobs?country=DE']

def test_saved_evidence_retains_offset_continuation():
    root=BASE+'/search-results?country=DE';current=root+'&from=90';nxt=root+'&from=100'
    page=HrefPage(current,[nxt]);bp.LAST_INGESTION_REPORT.clear();bp.LAST_INGESTION_REPORT['url']=root
    try:
        bp._record_assisted_evidence(page,'page_saved',10,count=10)
        c=bp.LAST_INGESTION_REPORT['continuation']
        assert c['url']==nxt and c['page']==11 and c['provenance']=='observed_forward_link'
        assert listing_continuation({'url':root,'portal_key':ji.portal_queue_key(root),'pages':10,'continuation':c})==c
    finally:bp.LAST_INGESTION_REPORT.clear()

def test_rel_next_is_not_authority_to_change_filters_or_routes():
    cur=BASE+'/search-results?country=DE&from=90'
    class Page(HrefPage):
        def eval_on_selector_all(self,*args):return [{'h':BASE+'/search-results?country=US&from=100','rel':'next'},{'h':BASE+'/blog?page=11','rel':'next'}]
    assert bp.paging_href_candidates(Page(cur,[]),cur,set())==[]

@pytest.mark.parametrize('marker,expected',[('listing_card_sentence','Y'),('explicit_detail_sentence; manual','Y'),('explicit_detail_sentence','Unknown')])
def test_retiring_equipment_evidence_requires_fresh_nonmanual_read(tmp_path,marker,expected):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.persistence import upsert_job
    from packtoworkeu.tests.test_assisted_support_2026_10_24 import job
    path=tmp_path/'preserve.db';db.initialize(path);conn=db.get_connection(path)
    evidence='Support equipment installations, qualifications, relocations, decommissions, and upgrade projects.'
    upsert_job(conn,job(relocation_support='Y',support_evidence=evidence,support_evidence_type=marker))
    fresh=job(relocation_support='Unknown',support_evidence='',support_evidence_type='explicit_detail_sentence',_support_authoritative=not marker.startswith('listing'))
    upsert_job(conn,fresh)
    assert conn.execute('SELECT relocation_support FROM jobs').fetchone()[0]==expected
    conn.close()

@pytest.fixture
def repair_browser():
    import os
    if os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1':pytest.skip('opt-in local Chromium fixtures')
    pw=pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright()as api:
        browser=api.chromium.launch(headless=True,args=['--no-sandbox'])
        context=browser.new_context()
        try:yield context
        finally:context.close();browser.close()


def test_local_browser_offset_window_and_saved_checkpoint(repair_browser):
    from urllib.parse import urlsplit,parse_qs
    ctx=repair_browser;root='https://fixture.invalid/search-results?country=DE'
    def serve(route):
        q=parse_qs(urlsplit(route.request.url).query);start=int(q.get('from',['0'])[0])
        nxt=root+'&from='+str(start+10)
        cards=''.join(f'<article><h3><a href="/job/{start+n}/engineer">Software Engineer {start+n}</a></h3></article>'for n in range(10))
        route.fulfill(status=200,content_type='text/html',body='<main><h1>150 jobs</h1>'+cards+f'<nav class="pagination"><a href="{nxt}">Next batch</a></nav></main>')
    ctx.route('**/*',serve);page=ctx.new_page();be.watch_page_responses(page)
    page.goto(root);visited={root};bp.LAST_INGESTION_REPORT.clear();bp.LAST_INGESTION_REPORT['url']=root
    try:
        for page_no in range(1,13):
            snapshot=be.inspect_page(page);assert snapshot['listing_ready']and snapshot['visible_job_links']==10
            record=bp._record_assisted_evidence(page,'page_saved',page_no,count=10)
            assert record['forward_page_links']==[root+'&from='+str(page_no*10)]
            if page_no<12:
                nxt=bp.paging_href_candidates(page,page.url,visited)[0];visited.add(nxt);page.goto(nxt)
        assert len(visited)==12 and 'from=110'in page.url
        c=bp.LAST_INGESTION_REPORT['continuation'];assert c['page']==13 and 'from=120'in c['url']
    finally:bp.LAST_INGESTION_REPORT.clear();page.close()


def test_local_browser_404_is_not_a_listing_or_verification(repair_browser):
    ctx=repair_browser
    ctx.route('**/*',lambda r:r.fulfill(status=404,content_type='text/html',body='<main><h1>404 - Oh no!</h1><p>Page not found</p><button>Accept Cookies</button></main>'))
    page=ctx.new_page();be.watch_page_responses(page);page.goto('https://fixture.invalid/en/career/en/career')
    s=be.inspect_page(page);assert '404'in s['document_error'] and not s['ready']and not s['reason']
    assert bp._recover_blank_page(page,page.url,1,wait_s=.01)=='error';page.close()


def test_local_browser_entry_search_navigation(repair_browser):
    ctx=repair_browser
    def serve(route):
        url=route.request.url
        if url.endswith('/home'):
            html='<main><h1>Careers</h1><p>Start your journey</p></main><nav><a href="/search-results">Search jobs</a><a href="https://evil.invalid/jobs">Jobs</a></nav>'
        else:
            assert url=='https://fixture.invalid/search-results'
            html='<main><h1>Open positions</h1>'+''.join(f'<article><a href="/job/{n}/engineer">Software Engineer</a></article>'for n in range(3))+'</main>'
        route.fulfill(status=200,content_type='text/html',body=html)
    ctx.route('**/*',serve);page=ctx.new_page();be.watch_page_responses(page);page.goto('https://fixture.invalid/home')
    bp._discover_listing_entry(page)
    assert page.url=='https://fixture.invalid/search-results'and be.inspect_page(page)['listing_ready'];page.close()


def test_local_browser_unidentified_job_controls_preserved(repair_browser):
    # Synthetic controls only: NOT claimed as the actual Applause control shape.
    ctx=repair_browser;loads=[]
    def serve(route):
        loads.append(route.request.url)
        html='<main><h1>Job Openings</h1><h2>Explore Our Openings</h2>'+''.join('<article><h3>Strategic Account Manager</h3><p>US-Remote</p><button>Job Details</button></article>'for _ in range(4))+'</main>'
        route.fulfill(status=200,content_type='text/html',body=html)
    ctx.route('**/*',serve);page=ctx.new_page();be.watch_page_responses(page);page.goto('https://fixture.invalid/jobs')
    before=page.content();assert bp._recover_blank_page(page,page.url,1,wait_s=.01)=='unextractable'
    assert len(loads)==1 and page.content()==before and be.inspect_page(page)['admitted_anchors']==[];page.close()

def test_legacy_mixed_escape_case_lookup_is_record_bounded():
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY,url TEXT UNIQUE,posting_key TEXT)')
    raw=BASE+'/job/301789/工程师';old=BASE+'/job/301789/%e5%B7%a5%E7%a8%8B%E5%b8%88'
    conn.execute('INSERT INTO jobs VALUES(1,?,?)',(old,old))
    conn.execute('INSERT INTO jobs VALUES(2,?,?)',(BASE+'/job/301790/工程师',''))
    assert ji.find_existing_posting(conn,raw)['id']==1
    assert ji.posting_identity(old)==ji.posting_identity(raw)
    conn.close()

def test_manual_source_equipment_value_is_not_automatically_retired(tmp_path):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.persistence import upsert_job
    from packtoworkeu.tests.test_assisted_support_2026_10_24 import job
    path=tmp_path/'manual-source.db';db.initialize(path);conn=db.get_connection(path)
    evidence='Support equipment installations, qualifications, relocations, decommissions, and upgrade projects.'
    upsert_job(conn,job(source_type='manual',relocation_support='Y',support_evidence=evidence,support_evidence_type='explicit_detail_sentence'))
    upsert_job(conn,job(relocation_support='Unknown',support_evidence='',_support_authoritative=True))
    assert conn.execute('SELECT relocation_support FROM jobs').fetchone()[0]=='Y';conn.close()

@pytest.mark.parametrize('text',[
 'We provide equipment and relocation support.',
 'Our office provides relocation assistance.',
 'Our equipment team provides relocation assistance.',
 'We offer relocation assistance, and equipment installations and relocations are part of the job.',
])
def test_object_word_alone_does_not_cancel_an_independent_benefit(text):
    assert JDSupportDetector().detect(text)['relocation']['verdict']=='Yes'
