"""Repeatable offline desktop benchmark, not an old-CPU certification.

Run from project root:
 QT_QPA_PLATFORM=offscreen SPONSORSCOUT_BENCH_BROWSER=1 python -m \
 sponsorscout.tests.benchmark_locale_performance_2026_11_06 --output report.json
Developer-only psutil measures RSS; no network or personal database is used.
"""
import argparse
import json
import os
import statistics
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def run():
    import psutil
    from sponsorscout.db import database as db
    from sponsorscout import i18n
    from sponsorscout.scanning import common
    from sponsorscout.tests._qt_support import application, wait_loaders, wait_until, dispose
    from PySide6.QtCore import QTimer
    t_import = time.perf_counter()
    from sponsorscout.ui.app import SponsorScoutApp
    import_ms = (time.perf_counter() - t_import)*1000
    proc = psutil.Process()
    stop = threading.Event()
    samples = []

    def sample():
        while not stop.wait(.02):
            try:
                rss = proc.memory_info().rss
                tree = rss + sum(p.memory_info().rss for p in proc.children(recursive=True) if p.is_running())
                samples.append((rss, tree))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    original_config = i18n._I18N_CONFIG
    original_locale = i18n.get_locale()
    report = {'host_cpu_count': os.cpu_count(), 'host_ram_gib': psutil.virtual_memory().total/2**30,
              'ui_module_import_ms': import_ms, 'jobs': 20000, 'applications': 1000,
              'browser_workload': 'not requested', 'limitations':
              'Offline Linux offscreen Qt; not a physical older Windows i3/Pentium certification. '
              'Synthetic job data and optional routed Chromium workload, not a live portal stress test.'}
    with tempfile.TemporaryDirectory(prefix='sponsorscout-ui-bench-') as directory:
        path = str(Path(directory)/'fixture.db')
        db.initialize(path)
        conn = db.get_connection(path)
        conn.executemany('INSERT INTO jobs (title,company,country,location,url,is_expired,insights_dirty,verified_active) VALUES (?,?,?,?,?,0,0,1)',
                         ((f'Engineer {i}', f'Company {i%100}', 'Germany', 'Berlin', f'https://fixture.invalid/jobs/{i}')
                          for i in range(report['jobs'])))
        conn.executemany('INSERT INTO applications (job_url,company,title,status,notes) VALUES (?,?,?,?,?)',
                         ((f'https://fixture.invalid/jobs/{i}', f'Company {i%100}', f'Engineer {i}', 'saved', 'Manual note')
                          for i in range(report['applications'])))
        conn.commit()
        conn.close()
        i18n._I18N_CONFIG = Path(directory)/'locale.json'
        app = application()
        i18n.set_locale('it')
        ticks = []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
        timer.start()
        ticks.append(time.perf_counter())
        old_first_run = SponsorScoutApp._check_first_run
        SponsorScoutApp._check_first_run = lambda self: None
        start = time.perf_counter()
        w = SponsorScoutApp(path)
        report['window_constructor_ms'] = (time.perf_counter()-start)*1000
        w.show()
        wait_loaders(w, 30)
        report['initial_load_ms'] = (time.perf_counter()-start)*1000
        tabs = []
        for index in range(w.tabs.count()):
            start = time.perf_counter()
            w.tabs.setCurrentIndex(index)
            app.processEvents()
            wait_loaders(w, 30)
            tabs.append({'tab': w.tabs.tabText(index), 'load_ms': (time.perf_counter()-start)*1000})
        report['tabs'] = tabs
        switches = []
        for locale in ['en', 'it', 'en', 'it']:
            start = time.perf_counter()
            i18n.set_locale(locale)
            w.retranslate()
            switches.append((time.perf_counter()-start)*1000)
            app.processEvents()
        report['locale_switch_ms'] = switches
        report['application_rows_rendered'] = w.applications_tab.table.rowCount()
        report['search_rows_rendered'] = w.search_tab.table.rowCount()
        assert report['search_rows_rendered'] == 500
        assert report['application_rows_rendered'] == 1000
        report['actual_host_budget'] = {kind: common.recommended_workers(kind) for kind in ('browser', 'http', 'company')}
        browser_done = threading.Event()
        failures = []
        if os.environ.get('SPONSORSCOUT_BENCH_BROWSER') == '1':
            def browser_work():
                try:
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as p:
                        browser = p.chromium.launch(headless=True)
                        context = browser.new_context()
                        listing = ''.join(f'<a href="/jobs/{i}">Engineer {i}</a>' for i in range(587))
                        detail = '<h1>Engineer</h1><p>' + 'Synthetic role duties and required skills. '*1000 + '</p>'
                        context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html',
                                      body=listing if route.request.url.endswith('/jobs') else detail))
                        page = context.new_page()
                        page.goto('https://fixture.invalid/jobs')
                        detail_page = context.new_page()
                        for index in range(30):
                            detail_page.goto(f'https://fixture.invalid/jobs/{index}')
                            assert len(detail_page.inner_text('body')) > 10000
                        context.close()
                        browser.close()
                    report['browser_workload'] = '1 real Chromium, 1 listing (587 links), 1 reused detail tab, 30 synthetic detail navigations; all requests intercepted'
                except Exception as exc:
                    failures.append(repr(exc))
                finally:
                    browser_done.set()
            worker = threading.Thread(target=browser_work, name='benchmark-browser')
            worker.start()
            wait_until(browser_done.is_set, 45)
            worker.join()
        # SQLite write contention while the GUI performs another asynchronous search.
        lock_ready = threading.Event()
        def lock_database():
            c = db.get_connection(path)
            c.execute('BEGIN IMMEDIATE')
            lock_ready.set()
            time.sleep(.5)
            c.rollback()
            c.close()
        writer = threading.Thread(target=lock_database)
        writer.start()
        wait_until(lock_ready.is_set)
        w.tabs.setCurrentWidget(w.search_tab)
        w.search_tab.run_search()
        wait_loaders(w, 30)
        wait_until(lambda: not writer.is_alive())
        writer.join()
        stop.set()
        monitor.join()  # exclude RSS-sampling overhead from idle CPU
        idle_start = proc.cpu_times()
        start = time.perf_counter()
        wait_until(lambda: time.perf_counter()-start >= 1)
        idle_end = proc.cpu_times()
        report['idle_process_cpu_percent_one_core'] = ((idle_end.user-idle_start.user)+(idle_end.system-idle_start.system))/(time.perf_counter()-start)*100
        ticks.append(time.perf_counter())
        gaps = [(b-a)*1000 for a, b in zip(ticks, ticks[1:])]
        ordered = sorted(gaps)
        report['heartbeat_interval_ms'] = 10
        report['heartbeat_max_gap_ms'] = max(gaps)
        report['heartbeat_p95_gap_ms'] = ordered[int((len(ordered)-1)*.95)]
        report['heartbeat_ticks'] = len(ticks)
        report['browser_errors'] = failures
        timer.stop()
        wait_loaders(w)
        wait_until(lambda: bool(w.close()), 30)
        dispose(w)
        SponsorScoutApp._check_first_run = old_first_run
        i18n.set_locale(original_locale)
        i18n._I18N_CONFIG = original_config
    stop.set()
    monitor.join()
    report['peak_app_rss_mib'] = max(x[0] for x in samples)/2**20
    report['peak_process_tree_rss_sum_mib'] = max(x[1] for x in samples)/2**20
    # Tree RSS is a conservative sum; shared Chromium memory may be counted twice.
    assert not report['browser_errors'], report['browser_errors']
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps(result, indent=2, ensure_ascii=False))
