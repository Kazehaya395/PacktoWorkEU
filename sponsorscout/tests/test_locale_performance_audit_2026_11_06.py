"""Offline whole-window locale and low-spec budget regression contracts."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture
def locale_config(tmp_path, monkeypatch):
    from sponsorscout import i18n
    old = i18n.get_locale()
    monkeypatch.setattr(i18n, '_I18N_CONFIG', tmp_path / 'locale.json')
    yield i18n
    i18n.set_locale(old)


@pytest.mark.parametrize('saved', ['unsupported', None, [], 23])
def test_invalid_saved_locale_falls_back_to_english(locale_config, saved):
    import json
    locale_config._I18N_CONFIG.write_text(json.dumps({'locale': saved}))
    assert locale_config.load_saved_locale() == 'en'


def _residual_english(root):
    from PySide6.QtWidgets import QWidget, QLabel, QAbstractButton, QGroupBox, QComboBox, QTableWidget
    from sponsorscout.i18n import LANGUAGES
    gaps = []
    for obj in root.findChildren(QWidget):
        texts = [obj.toolTip()]
        if isinstance(obj, (QLabel, QAbstractButton)):
            texts.append(obj.text())
        if isinstance(obj, QGroupBox):
            texts.append(obj.title())
        placeholder = getattr(obj, 'placeholderText', None)
        if callable(placeholder):
            texts.append(placeholder())
        if isinstance(obj, QComboBox):
            texts.extend(obj.itemText(i) for i in range(obj.count()))
        if isinstance(obj, QTableWidget):
            texts.extend(obj.horizontalHeaderItem(i).text()
                         for i in range(obj.columnCount()) if obj.horizontalHeaderItem(i))
        for text in texts:
            translated = LANGUAGES['it'].get(text)
            if translated and translated != text:
                gaps.append((type(obj).__name__, text))
    return gaps


@pytest.mark.parametrize('startup', ['en', 'it'])
def test_all_eight_tabs_cold_start_and_round_trip(locale_config, tmp_path, monkeypatch, startup):
    pytest.importorskip('PySide6')
    from sponsorscout.tests._qt_support import application, wait_loaders, dispose
    from sponsorscout.ui.app import SponsorScoutApp
    application()
    monkeypatch.setattr(SponsorScoutApp, '_check_first_run', lambda self: None)
    locale_config.set_locale(startup)
    w = SponsorScoutApp(str(tmp_path / 'window.db'))
    try:
        wait_loaders(w)
        assert w.tabs.count() == 8
        for index in range(w.tabs.count()):
            w.tabs.setCurrentIndex(index)
            application().processEvents()
            wait_loaders(w)
        w.tailoring_tab.url_edit.setText('Save')  # a user input equal to a UI key
        for locale in ['it', 'en', 'it']:
            w.lang_combo.setCurrentIndex(w.lang_combo.findData(locale))
            assert locale_config.get_locale() == locale
            w.retranslate()  # a second pass must remain safe/idempotent
            wait_loaders(w)
            if locale == 'it':
                assert not _residual_english(w)
            assert w.tailoring_tab.url_edit.text() == 'Save'
            combo = w.applications_tab.status_combo
            assert [combo.itemData(i) for i in range(combo.count())] == [
                'saved', 'applied', 'interview', 'offer', 'rejected']
    finally:
        wait_loaders(w)
        w.close()
        dispose(w)


def test_standard_message_and_file_dialog_buttons_follow_app_locale(locale_config):
    pytest.importorskip('PySide6')
    from sponsorscout.tests._qt_support import application, dispose
    from PySide6.QtWidgets import QMessageBox, QFileDialog, QDialogButtonBox
    application()
    for locale, yes, cancel, save in [('it', 'Sì', 'Annulla', 'Salva'),
                                     ('en', 'Yes', 'Cancel', 'Save')]:
        locale_config.set_locale(locale)
        box = QMessageBox()
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel | QMessageBox.Save)
        assert box.button(QMessageBox.Yes).text().replace('&', '') == yes
        assert box.button(QMessageBox.Cancel).text().replace('&', '') == cancel
        picker = QFileDialog()
        picker.setAcceptMode(QFileDialog.AcceptSave)
        button_box = picker.findChild(QDialogButtonBox)
        assert button_box.button(QDialogButtonBox.Save).text().replace('&', '') == save
        assert button_box.button(QDialogButtonBox.Cancel).text().replace('&', '') == cancel
        dispose(picker)
        dispose(box)


def test_translated_application_status_stays_canonical_in_database(locale_config, tmp_path):
    pytest.importorskip('PySide6')
    from sponsorscout.tests._qt_support import application, wait_loaders, dispose
    from sponsorscout.ui.tabs.applications import ApplicationsTab, COL_STATUS
    from sponsorscout.db import database as db
    from PySide6.QtCore import Qt
    application()
    path = str(tmp_path / 'applications.db')
    db.initialize(path)
    url = 'https://fixture.invalid/job/123'
    db.upsert_application(path, url, 'Fixture', 'Engineer', status='saved')
    locale_config.set_locale('it')
    w = ApplicationsTab(path)
    try:
        w.load_applications()
        wait_loaders(w)
        assert w.table.item(0, COL_STATUS).text() == locale_config._('saved')
        assert w.table.item(0, COL_STATUS).data(Qt.UserRole) == 'saved'
        w.table.selectRow(0)
        w.status_combo.setCurrentIndex(w.status_combo.findData('interview'))
        w.notes_edit.setText('Keep this manual English note')
        locale_config.set_locale('en')
        w.retranslate()
        assert w.status_combo.currentData() == 'interview'
        assert w.notes_edit.text() == 'Keep this manual English note'
        locale_config.set_locale('it')
        w.retranslate()
        w._update_application()
        wait_loaders(w)
        row = db.list_applications(path)[0]
        assert row['status'] == 'interview'
        assert row['notes'] == 'Keep this manual English note'
        assert locale_config._('interview') in w.log.toPlainText()
    finally:
        wait_loaders(w)
        dispose(w)


def test_scan_log_quarantine_and_seed_dialogs_localize_without_mutating_codes(locale_config, tmp_path):
    pytest.importorskip('PySide6')
    from sponsorscout.tests._qt_support import application, dispose
    from sponsorscout.ui.tabs.tools import ScanLogDialog, QuarantineDialog, HEADERS_LOG
    from sponsorscout.ui.tabs.data_management import SeedRowDialog
    from sponsorscout.db import database as db
    application()
    locale_config.set_locale('it')
    path = str(tmp_path / 'dialogs.db')
    db.initialize(path)
    log = ScanLogDialog(path, 'fixture')
    quarantine = QuarantineDialog(path)
    seed = SeedRowDialog({'source_type': 'recruiter', 'scope_policy': 'job_location'}, locale_config._('Edit'))
    try:
        from PySide6.QtWidgets import QTableWidget
        table = log.findChild(QTableWidget)
        assert table.horizontalHeaderItem(11).text() == locale_config._('Diagnostics')
        assert log.windowTitle() == 'Registro scansione — fixture'
        assert quarantine.table.horizontalHeaderItem(3).text() == locale_config._('Reason')
        assert seed.row()['source_type'] == 'recruiter'
        assert seed.row()['scope_policy'] == 'job_location'
        assert not _residual_english(seed)
    finally:
        for widget in (log, quarantine, seed):
            dispose(widget)


@pytest.mark.parametrize('cpu,total,free,expected_http,expected_company', [
    (2, 8, 4, 3, 2), (4, 8, 4, 8, 4), (8, 32, .5, 3, 2)])
@pytest.mark.parametrize('turbo', [False, True])
@pytest.mark.parametrize('override', [None, '999', '1', 'invalid', '-3'])
def test_worker_overrides_never_bypass_low_spec_hardware(
        monkeypatch, cpu, total, free, expected_http, expected_company, turbo, override):
    from sponsorscout.scanning import common
    from sponsorscout.scanning.career import career_scanner as career
    from sponsorscout.scanning.ats import ats_scanner as ats
    gib = 1024 ** 3
    monkeypatch.setattr(common, 'host_workers_limits', lambda: (cpu, total*gib, free*gib))
    monkeypatch.setattr(common, 'is_high_performance', lambda: turbo)
    for name in ('SPONSORSCOUT_MAX_WORKERS', 'SPONSORSCOUT_HTTP_WORKERS'):
        if override is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, override)
    for module in (common, career, ats):
        assert module.recommended_workers('browser') == 1
        assert module.recommended_workers('http') <= (10 if turbo and cpu == 4 else expected_http)
        assert module.recommended_workers('company') <= (5 if turbo and cpu == 4 else expected_company)
        for kind in ('browser', 'http', 'company'):
            assert module.recommended_workers(kind, requested=1) == 1
            assert module.recommended_workers(kind) == common.recommended_workers(kind)
    assert career.CareerPortalScanner._company_pool(SimpleNamespace()) == common.recommended_workers('company')


@pytest.mark.parametrize('cpu,total,free,expected', [(2, 8, 4, 1), (4, 8, 4, 1), (8, 32, 20, 3)])
def test_assisted_detail_tabs_use_hardware_budget(monkeypatch, cpu, total, free, expected):
    from sponsorscout.scanning import common
    from sponsorscout.core.session_details import SessionDetailEnricher
    monkeypatch.delenv('SPONSORSCOUT_MAX_WORKERS', raising=False)
    monkeypatch.setattr(common, 'host_workers_limits', lambda: (cpu, total*2**30, free*2**30))
    scanner = SimpleNamespace(config=SimpleNamespace(ASSISTED_DETAIL_TABS=9))
    assert SessionDetailEnricher(None, scanner, 'scope').max_tabs == expected


def test_header_translation_batches_notifications_and_is_idempotent():
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QTableWidget
    from sponsorscout.tests._qt_support import application, dispose
    from sponsorscout.i18n import set_table_header_labels
    application()
    table = QTableWidget(2, 3)
    events = []
    table.model().headerDataChanged.connect(lambda *args: events.append(args))
    try:
        set_table_header_labels(table, ['A', 'B', 'C'])
        assert len(events) == 1
        set_table_header_labels(table, ['A', 'B', 'C'])
        assert len(events) == 1
        set_table_header_labels(table, ['Uno', 'Due', 'Tre'])
        assert len(events) == 2
        assert table.horizontalHeaderItem(2).text() == 'Tre'
        assert table.rowCount() == 2
    finally:
        dispose(table)
