"""Pending automatic verification vs terminal access; offline clock/DOM fixtures."""
from types import SimpleNamespace
import threading
import pytest
from sponsorscout.scanning.career import career_scanner as cs
from sponsorscout.scanning.response_evidence import challenge_reason, listing_block_message
from sponsorscout.core import blocked_portals as bp, browser_evidence as be
from sponsorscout.tests.test_career_verification_readiness_2026_11_05 import Clock, Page, POSTING, WALL, LANDING
from sponsorscout.scanning.common import ScanCancelled

AUTO = ('<title>Careers</title><h1>careers.fixture.invalid</h1>'
        '<h2>Performing security verification</h2><p>This website uses a security service '
        'while this website verifies you are not a bot.</p><span>Verifying...</span>')
BARE = 'HTTP 403 access blocked (human verification not confirmed)'
SHELL = '<title>Loading</title><main>Please wait</main>'

@pytest.fixture
def runtime(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cs, 'time', SimpleNamespace(monotonic=clock.monotonic))
    scanner = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    scanner.config = SimpleNamespace(MAX_COMPANY_TIME_SEC=60, VERIFICATION_OBSERVE_SEC=3,
                                     ACCESS_READINESS_OBSERVE_SEC=.6)
    scanner.cancel_event = threading.Event()
    return scanner, clock

@pytest.mark.parametrize('status',[0,200,403,503])
def test_automatic_verification_without_mounted_widget(status):
    reason = challenge_reason(AUTO, status=status)
    assert 'automatic verification pending' in reason
    assert 'human action not inferred' in reason
    assert 'no verification challenge observed' not in listing_block_message(reason)
    assert 'job detail' in challenge_reason(AUTO, status=status, subject='detail')

@pytest.mark.parametrize('markup',[
    '<script>Performing security verification</script>',
    '<!-- Performing security verification -->',
    POSTING + '<script src="https://challenges.cloudflare.com/sdk.js"></script>',
    POSTING.replace('Responsibilities and requirements:', 'Responsibilities and requirements: verifying your connection.'),
])
def test_passive_or_real_posting_text_is_not_an_auto_wall(markup):
    assert not challenge_reason(markup)


def test_delayed_widget_promotes_short_readiness_to_shared_verification_budget(runtime):
    scanner, clock = runtime
    page = Page(clock, [SHELL, SHELL, AUTO, AUTO, POSTING])
    state, diagnostics = {}, []
    assert scanner._observe_seed_verification(page, BARE, diagnostics, 100, state) == ''
    assert clock.now > 100.6
    assert state['outcome'] == 'recovered_content' and state['verification_seen']
    assert page._last_nav_status == 0
    assert any('evidence appeared during access readiness' in d for d in diagnostics)


def test_permanent_403_is_not_called_cloudflare_or_given_full_challenge_budget(runtime):
    scanner, clock = runtime
    page = Page(clock, [SHELL])
    state, diagnostics = {}, []
    assert scanner._observe_seed_verification(page, BARE, diagnostics, 100, state) == BARE
    assert 100.6 <= clock.now <= 100.601
    assert state['outcome'] == 'access_unresolved'
    assert not state.get('verification_seen')
    assert 'no verification challenge observed' in listing_block_message(BARE)
    count = len(page.waits)
    scanner._observe_seed_verification(page, BARE, [], 100, state)
    assert len(page.waits) == count  # Re-entry cannot restart the budget.


def test_pending_wall_at_expiry_is_unresolved_not_permanent_or_human_required(runtime):
    scanner, clock = runtime
    page = Page(clock, [SHELL, AUTO])
    state, diagnostics = {}, []
    result = scanner._observe_seed_verification(page, BARE, diagnostics, 100, state)
    assert result.startswith('verification unresolved:')
    assert listing_block_message(result).startswith('listing access blocked: verification unresolved:')
    assert state['outcome'] == 'verification_unresolved'
    assert 103 <= clock.now <= 103.001
    count = len(page.waits)
    scanner._observe_seed_verification(page, result, [], 100, state)
    assert len(page.waits) == count


def test_navigation_read_failures_are_retried_not_called_clearance(runtime):
    scanner, clock = runtime
    page = Page(clock, [AUTO, POSTING])
    original = page.content
    calls = []
    def content():
        calls.append(1)
        if len(calls) <= 2: raise RuntimeError('document navigating')
        return original()
    page.content = content
    state = {}
    assert scanner._observe_seed_verification(page, challenge_reason(AUTO), [], 100, state) == ''
    assert state['read_failures'] >= 1 and state['outcome'] == 'recovered_content'


def test_initial_refusal_stop_is_responsive(runtime):
    scanner, clock = runtime
    page = Page(clock, [SHELL]); page.cancel = scanner.cancel_event
    with pytest.raises(ScanCancelled):
        scanner._observe_seed_verification(page, BARE, [], 100, {})
    assert len(page.waits) == 1


def test_company_deadline_still_caps_initial_refusal(runtime):
    scanner, clock = runtime
    page = Page(clock, [SHELL, AUTO])
    assert scanner._observe_seed_verification(page, BARE, [], 40.3, {})
    assert clock.now <= 100.301


def test_assisted_foreground_auto_wall_outranks_underlying_readable_listing():
    data = dict(version=1,url='https://fixture.invalid/jobs',title='Careers',headings=['Vacancies'],
                anchors=[dict(url=f'https://fixture.invalid/job/{123456+i}',title='Backend Engineer') for i in range(3)],
                primary_text='',main_text='',body_preview='',active_ids=[],
                overlays=['Performing security verification'],foreground_frames=[])
    snapshot = be.inspect_page(SimpleNamespace(evaluate=lambda _: data),kind='listing')
    assert 'automatic verification pending' in snapshot['reason']
    assert not snapshot['listing_ready']


def test_assisted_shared_detector_recognizes_unmounted_auto_wall():
    page = SimpleNamespace(content=lambda:AUTO,title=lambda:'Careers',frames=[],_assisted_nav_status=403)
    assert 'automatic verification pending' in bp._page_response_evidence(page)['reason']


@pytest.mark.parametrize('clears',[True,False])
def test_assisted_wait_uses_current_page_and_reports_unresolved_without_claiming_rejection(monkeypatch,clears):
    clock = Clock(); events=[]; ticks=[]
    page = SimpleNamespace(is_closed=lambda:False)
    def wait(ms):clock.now+=ms/1000; ticks.append(ms)
    page.wait_for_timeout=wait
    monkeypatch.setattr(bp,'time',SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(bp,'_verification_event',lambda page,phase,*a:events.append(phase))
    monkeypatch.setattr(bp,'_gate_attention',lambda *a:None)
    monkeypatch.setattr(bp,'_page_is_challenge',lambda _:not(clears and ticks))
    monkeypatch.setattr(bp,'_page_has_listing',lambda _:bool(clears and ticks))
    assert bp._wait_for_clearance(page,.8) is clears
    assert ('verification_cleared' if clears else 'verification_unresolved') in events
    assert 'verification_not_accepted' not in events
    assert ticks and clock.now <= 100.801


def test_assisted_wait_does_not_equate_wall_disappearance_with_listing(monkeypatch):
    clock=Clock();events=[]
    page=SimpleNamespace(is_closed=lambda:False,wait_for_timeout=lambda ms:setattr(clock,'now',clock.now+ms/1000))
    monkeypatch.setattr(bp,'time',SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(bp,'_verification_event',lambda p,phase,*a:events.append(phase))
    monkeypatch.setattr(bp,'_gate_attention',lambda *a:None)
    monkeypatch.setattr(bp,'_page_is_challenge',lambda _:False)
    monkeypatch.setattr(bp,'_page_has_listing',lambda _:False)
    assert not bp._wait_for_clearance(page,.4)
    assert 'verification_cleared' not in events


def test_assisted_pending_messages_en_it():
    from sponsorscout.i18n import LANGUAGES
    key='Verification pending on page %d — waiting for the page to clear. Interact only if the page requests it.'
    assert key in LANGUAGES['en'] and key in LANGUAGES['it']
    assert '3' in (LANGUAGES['it'][key] % 3)


def test_regular_foreground_auto_wall_outranks_real_posting_without_widget():
    markup=POSTING+'<div style="position:fixed">Performing security verification</div>'
    assert 'automatic verification pending' in challenge_reason(markup)


@pytest.fixture
def actual_page():
    import os
    if os.environ.get('SPONSORSCOUT_TEST_BROWSER') != '1':
        pytest.skip('opt-in intercepted local Chromium; no live verification')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page()
        page.route('**/*',lambda route:route.fulfill(status=403,body=SHELL,content_type='text/html'))
        page.goto('https://fixture.invalid/jobs',wait_until='domcontentloaded')
        yield page
        browser.close()


@pytest.mark.parametrize('mode',['clears','pending','refusal'])
def test_real_browser_initial_403_delayed_verification_and_terminal_states(actual_page, mode):
    import time
    page=actual_page
    if mode!='refusal':
        page.evaluate('''([wall,posting,clears]) => {
            setTimeout(()=>document.documentElement.innerHTML=wall,150);
            if(clears)setTimeout(()=>document.documentElement.innerHTML=posting,650);
        }''',[AUTO,POSTING,mode=='clears'])
    page._last_nav_status=403
    scanner=cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    scanner.config=SimpleNamespace(MAX_COMPANY_TIME_SEC=10,VERIFICATION_OBSERVE_SEC=1.4,ACCESS_READINESS_OBSERVE_SEC=.4)
    scanner.cancel_event=threading.Event()
    state,diagnostics={},[]
    result=scanner._observe_seed_verification(page,BARE,diagnostics,time.monotonic(),state)
    assert state['outcome']=={'clears':'recovered_content','pending':'verification_unresolved','refusal':'access_unresolved'}[mode]
    assert bool(result) is (mode!='clears')
    assert page._last_nav_status==(0 if mode=='clears' else 403)
    if mode=='pending':assert 'verification unresolved:' in listing_block_message(result)


def test_real_assisted_rendered_auto_overlay_and_clearance(actual_page):
    page=actual_page
    anchors=''.join(f'<article><a href="https://fixture.invalid/job/{123456+i}">Backend Engineer {i}</a></article>'for i in range(3))
    page.set_content('<main><h1>Vacancies</h1>'+anchors+'</main><div id="overlay" style="position:fixed;inset:0;background:white">Performing security verification</div>')
    page._assisted_nav_status=403
    evidence=bp._page_response_evidence(page)
    assert 'automatic verification pending' in evidence['reason']
    assert not evidence['rendered']['listing_ready']
    page.evaluate('document.querySelector("#overlay").remove()')
    recovered=bp._page_response_evidence(page)
    assert not recovered['reason'] and recovered['rendered']['listing_ready']


def test_unresolved_listing_stays_recoverable_but_history_does_not_queue_readable_board():
    message=listing_block_message('verification unresolved: observation budget exhausted; '+challenge_reason(AUTO))
    assert bp._is_block_message(message,jobs_found=0)
    assert not bp._is_block_message('verification pending: '+challenge_reason(AUTO)+' | verification cleared: changed document',jobs_found=5)


def test_real_ats_fallback_uses_shared_readiness_and_retires_stale_403(actual_page,monkeypatch,tmp_path):
    import contextlib,json
    from sponsorscout.scanning.ats import ats_scanner as ats
    from sponsorscout.scanning import access_readiness
    page=actual_page;context=page.context;browser=context.browser
    posting=POSTING+'<article><a href="https://fixture.invalid/job/123456">Backend Engineer</a></article>'
    safe_posting=json.dumps(posting).replace('</', '<\\/')
    script=('<script>setTimeout(()=>document.documentElement.innerHTML='+json.dumps(AUTO)+',100);'
            'setTimeout(()=>document.documentElement.innerHTML='+safe_posting+',500);</script>')
    page.unroute('**/*')
    page.route('**/*',lambda route:route.fulfill(status=403,body=SHELL+script,content_type='text/html'))
    monkeypatch.setattr(browser,'new_context',lambda **kw:context)
    monkeypatch.setattr(context,'new_page',lambda:page)
    monkeypatch.setattr(ats,'sync_playwright',lambda:contextlib.nullcontext(SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw:browser))))
    original=access_readiness.observe_verification;outcomes=[]
    def observed(*args,**kw):
        result=original(*args,**kw)
        outcomes.append((result,args[4].get('outcome'),getattr(args[0],'_last_nav_status')))
        return result
    monkeypatch.setattr(access_readiness,'observe_verification',observed)
    scanner=ats.ATSScanner(output_file=str(tmp_path/'ats.csv'))
    monkeypatch.setattr(scanner,'make_row',lambda target,title,url,*a,**kw:dict(title=title,url=url))
    rows=scanner.browser_fallback(dict(name='Fixture',url='https://fixture.invalid/jobs'))
    assert rows==[dict(title='Backend Engineer',url='https://fixture.invalid/job/123456')]
    assert outcomes[0]==('', 'recovered_content', 0)
    assert len(outcomes)==2 and outcomes[1][0]==''
    assert any('verification cleared' in note for note in scanner._target_notes)
