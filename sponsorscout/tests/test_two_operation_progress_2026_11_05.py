"""Two-operation progress: lifecycle, attempts, real Qt and no collection I/O."""
from types import SimpleNamespace
import threading

import pytest

from sponsorscout.application.scan_progress import ScanProgressState, parse_event
from sponsorscout.scanning.progress_events import TaskReporter, task_key, emit
from sponsorscout.scanning.detail_state import DetailRequestState


def configure(total=2, base=0):
    model = ScanProgressState()
    model.apply(dict(kind='configure', total=total, base=base))
    return model


def event(model, kind, key='a', attempt=1, **data):
    return model.apply(dict(kind=kind, task=key, attempt=attempt, **data))


def test_parallel_listing_and_jd_work_have_separate_rows_and_dynamic_denominator():
    m = configure()
    event(m, 'task_begin', name='Alpha')
    event(m, 'task_begin', 'b', name='Beta')
    event(m, 'task_stage', stage='details')
    s = event(m, 'jd_snapshot', coverage=dict(observed=1147, readable=0, failed=540, unattempted=607))
    assert s['settled'] == 0 and s['percent'] == 0
    assert s['listing_active'] == 1 and s['jd_active'] == 1
    assert s['checked'] == 540 and s['coverage']['readable'] == 0
    assert len(s['active']) == 2
    event(m, 'jd_snapshot', 'b', coverage=dict(observed=19, readable=10, thin=2, unattempted=7))
    s = m.snapshot()
    assert s['checked'] == 552 and s['coverage']['observed'] == 1166


def test_retry_reopens_same_identity_and_does_not_double_count():
    m = configure(total=1)
    event(m, 'task_begin', name='Portal')
    s = event(m, 'task_end', status='error')
    assert s['settled'] == 1 and s['percent'] == 99
    event(m, 'task_end', status='error')
    assert m.snapshot()['settled'] == 1
    event(m, 'task_retry')
    assert m.snapshot()['settled'] == 0 and m.snapshot()['queued_retries'] == 1
    event(m, 'task_begin', attempt=2, name='Portal')
    event(m, 'task_end', attempt=1, status='error')
    assert m.snapshot()['settled'] == 0
    s = event(m, 'task_end', attempt=2, status='partial')
    assert s['settled'] == 1 and s['total'] == 1 and s['percent'] == 99
    m.apply(dict(kind='lifecycle', state='finalizing'))
    assert m.snapshot()['percent'] == 99


def test_resume_offset_and_out_of_order_workers():
    m = configure(total=10, base=8)
    event(m, 'task_begin', 'b', name='B')
    event(m, 'task_begin', 'a', name='A')
    assert m.snapshot()['percent'] == 80
    event(m, 'task_end', 'b', status='empty')
    assert m.snapshot()['percent'] == 90
    event(m, 'task_end', 'a', status='ok')
    assert m.snapshot()['percent'] == 99


def test_no_total_and_protocol_robustness():
    m = ScanProgressState()
    assert m.snapshot()['percent'] == 0
    assert parse_event('SCAN_EVENT: invalid') is None
    assert parse_event('SCAN_EVENT: []') is None
    assert parse_event('[detail] 10/10 JDs checked') is None
    assert parse_event('SCAN_EVENT: {"kind":"configure","total":2}')['total'] == 2


def test_reporter_uses_classification_not_enrichment_or_returned_rows():
    state = DetailRequestState()
    scope = 'https://fixture.invalid/jobs'
    urls = [scope + '/' + str(i) for i in range(4)]
    for u in urls:
        state.register(u, scope)
    state.observe(urls[0], kind='readable')
    state.observe(urls[1], kind='blocked')
    state.observe(urls[2], kind='empty')
    scanner = SimpleNamespace(_detail_state=state, get_captured_jd=lambda _: '')
    lines = []
    target = dict(name='Portal', careers_url=scope, target_country='Global')
    with TaskReporter(scanner, 'career', lines.append) as r:
        key = r.begin(target)
        r.stage(key, 'details')
        r.end(key, 'partial')
        r.retry([target])
        key2 = r.begin(target)
        assert key == key2
        r.end(key2, 'partial')
    assert scanner._progress_reporter is None and not r.thread.is_alive()
    model = configure(total=1)
    for line in lines:
        model.apply(parse_event(line))
    s = model.snapshot()
    assert s['settled'] == 1 and s['coverage']['observed'] == 4
    assert s['checked'] == 3 and s['coverage']['readable'] == 1
    assert s['coverage']['unattempted'] == 1
    assert all('https://' not in line for line in lines)


def test_reporter_parallel_serialization_and_safe_observer_failure():
    class BrokenState:
        lock = threading.RLock()
        memberships = {'url': {'scope'}}
        scopes = {}
        def coverage(self, *a):
            raise RuntimeError('observer failed')
    scanner = SimpleNamespace(_detail_state=BrokenState())
    lines = []
    with TaskReporter(scanner, 'ats', lines.append) as r:
        def work(i):
            key = r.begin(dict(name=str(i), url='scope'))
            r.end(key, 'ok')
        threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
        for t in threads: t.start()
        for t in threads: t.join()
    model = configure(total=12)
    for line in lines: model.apply(parse_event(line))
    assert model.snapshot()['settled'] == 12
    assert task_key('career', dict(name='A', careers_url='x')) != task_key('ats', dict(name='A', url='x'))


@pytest.fixture
def tools(tmp_path, monkeypatch):
    from sponsorscout.tests._qt_support import application, wait_loaders, dispose
    from sponsorscout.db import database as db
    from sponsorscout.ui.tabs.tools import ToolsTab
    from sponsorscout.ui.async_load import AsyncLoader
    application()
    path = tmp_path / 'ui.sqlite'
    db.initialize(path)
    # Checkpoint and history refreshes own unrelated raw worker threads;
    # isolate them while testing real progress widgets and finish handlers.
    monkeypatch.setattr(ToolsTab, '_refresh_resume_button', lambda self: None)
    monkeypatch.setattr(ToolsTab, 'refresh', lambda self: None)
    widget = ToolsTab(str(path))
    wait_loaders(widget)
    yield widget
    wait_loaders(widget)
    for loader in widget.findChildren(AsyncLoader): loader.close()
    dispose(widget)


def test_real_qt_never_100_while_running_even_with_stale_legacy_ticks(tools):
    m = configure(total=1)
    event(m, 'task_begin', name='Portal')
    event(m, 'task_end', status='partial')
    tools._set_scan_running_ui('Running')
    tools._on_work_progress(m.snapshot())
    assert tools.scan_bar.value() == 99
    tools._on_scan_progress_tick(1, 1, 'career', 'Career 1/1')
    tools._on_scan_progress('[dns retry] re-crawl: 1 actual failed target(s)')
    assert tools.scan_bar.value() == 99 and tools.scan_bar.maximum() == 100
    event(m, 'task_retry')
    tools._on_work_progress(m.snapshot())
    assert tools.scan_bar.value() == 0 and '1' in tools.portal_progress_label.text()
    event(m, 'task_begin', attempt=2, name='Portal')
    event(m, 'task_end', attempt=2, status='ok')
    m.apply(dict(kind='lifecycle', state='finalizing'))
    tools._on_work_progress(m.snapshot())
    assert tools.scan_bar.value() == 99
    assert 'Finaliz' in tools.scan_phase.text()
    tools._work_finished = True
    tools._work_control = 'partial'
    tools._render_work_progress()
    assert tools.scan_bar.value() == 100


def test_real_qt_dynamic_jds_pause_cancel_and_language_roundtrip(tools, monkeypatch):
    from sponsorscout import i18n
    m = configure(total=3)
    event(m, 'task_begin', name='Alpha')
    event(m, 'task_begin', 'b', name='Beta')
    event(m, 'task_stage', stage='details')
    event(m, 'jd_snapshot', coverage=dict(observed=10, readable=2, failed=3, unattempted=5))
    tools._set_scan_running_ui('Running')
    tools._on_work_progress(m.snapshot())
    assert tools.jd_bar.value() == 50
    assert 'Alpha' in tools.active_scan_work.toPlainText() and 'Beta' in tools.active_scan_work.toPlainText()
    tools._on_coordinator_state('paused')
    assert tools.jd_bar.maximum() != 0
    monkeypatch.setattr(i18n, '_locale', 'it')
    tools.retranslate()
    assert 'Portali conclusi' in tools.portal_progress_label.text()
    assert tools.jd_bar.value() == 50
    monkeypatch.setattr(i18n, '_locale', 'en')
    tools.retranslate()
    assert 'Portals settled' in tools.portal_progress_label.text()
    assert tools.jd_bar.value() == 50
    tools._work_finished = True
    tools._work_control = 'cancelled'
    tools._render_work_progress()
    assert tools.scan_bar.value() < 100 and tools.jd_bar.value() == 50
    assert tools.active_scan_work.toPlainText() == ''


def test_real_coordinator_emits_structured_state_and_finalization(monkeypatch):
    from sponsorscout.tests._qt_support import application, wait_until
    from sponsorscout.application.scan_coordinator import ScanCoordinator
    from sponsorscout.scanning import pipeline
    application()
    def fake_run(**kwargs):
        cb = kwargs['progress']
        emit(cb, 'configure', total=1, base=0)
        emit(cb, 'task_begin', task='a', name='Portal', attempt=1)
        emit(cb, 'task_end', task='a', attempt=1, status='ok')
        emit(cb, 'lifecycle', state='finalizing')
        return dict(status='completed', ingested=0)
    monkeypatch.setattr(pipeline, 'run_scan', fake_run)
    coordinator = ScanCoordinator()
    snapshots, finished = [], []
    coordinator.work_progress.connect(snapshots.append)
    coordinator.finished.connect(finished.append)
    assert coordinator.start()
    wait_until(lambda: bool(finished))
    coordinator._thread.join(timeout=2)
    assert snapshots and snapshots[-1]['lifecycle'] == 'finalizing'
    assert all(s['percent'] < 100 for s in snapshots)
    assert snapshots[-1]['settled'] == 1


def test_live_jd_sampler_publishes_before_portal_finishes():
    state = DetailRequestState()
    state.register('https://fixture.invalid/job/123456', 'https://fixture.invalid/jobs')
    state.observe('https://fixture.invalid/job/123456', kind='readable')
    scanner = SimpleNamespace(_detail_state=state, get_captured_jd=lambda _: '')
    sampled = threading.Event()
    lines = []
    def sink(line):
        lines.append(line)
        if parse_event(line)['kind'] == 'jd_snapshot': sampled.set()
    with TaskReporter(scanner, 'career', sink) as reporter:
        key = reporter.begin(dict(name='Portal', careers_url='https://fixture.invalid/jobs'))
        assert sampled.wait(3), 'Live JD progress did not arrive during the task'
        assert not any(parse_event(line)['kind'] == 'task_end' for line in lines)
        reporter.end(key, 'ok')
    assert not reporter.thread.is_alive()


def test_real_pipeline_emits_unique_ats_and_career_lifecycle(tmp_path, monkeypatch):
    from sponsorscout.application import seed_manager
    from sponsorscout.scanning import pipeline
    from sponsorscout.scanning.ats import ats_scanner as ats
    from sponsorscout.scanning.career import career_scanner as career
    seed = tmp_path / 'seed.csv'
    seed.write_text('name,careers_url,provider,board_slug,source_type,target_country,scope_policy\n'
                    'Fixture,https://fixture.invalid/jobs,greenhouse,fixture,direct_employer,Global,global\n', encoding='utf-8')
    out = tmp_path / 'exports'
    out.mkdir()
    monkeypatch.setattr(seed_manager, 'user_career_path', lambda: seed)
    monkeypatch.setattr(seed_manager, 'user_ats_path', lambda: seed)
    monkeypatch.setattr(pipeline.paths, 'ensure_scan_output_dir', lambda: out)
    monkeypatch.setattr(ats.ATSScanner, 'scan_target', lambda *a: [])
    monkeypatch.setattr(career.CareerPortalScanner, '_fetch_provider_jobs', lambda *a: (
        [dict(job_title='Backend Engineer', job_url='https://fixture.invalid/job/123456',
              location_hint='Berlin, Germany', extraction_method='fixture_provider')], 'fixture provider'))
    lines = []
    summary = pipeline.run_scan(method='quick', db_path=tmp_path/'jobs.sqlite', progress=lines.append)
    model = ScanProgressState()
    events = [e for line in lines for e in [parse_event(line)] if e is not None]
    for value in events: model.apply(value)
    assert summary['ingested'] == 1
    assert {e.get('phase') for e in events if e['kind'] == 'task_begin'} == {'ats', 'career'}
    assert model.snapshot()['settled'] == model.snapshot()['total'] == 2
    assert model.snapshot()['lifecycle'] == 'finalizing'
    assert model.snapshot()['percent'] == 99


@pytest.mark.parametrize('status,expected', [('completed',100),('partial',100),('cancelled',99),('error',99)])
def test_real_finish_handler_only_completes_settled_non_cancelled_runs(tools, status, expected):
    model = configure(total=2)
    for key in ('a', 'b'):
        event(model, 'task_begin', key)
        event(model, 'task_end', key)
    tools._set_scan_running_ui('Running')
    tools._on_work_progress(model.snapshot())
    assert tools.scan_bar.value() == 99
    tools._on_scan_finished(dict(status=status, cancelled=(status=='cancelled')))
    assert tools.scan_bar.value() == expected
    assert tools.jd_bar.maximum() != 0
