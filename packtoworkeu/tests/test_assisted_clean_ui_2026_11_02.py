"""Final company-neutral UI cleanup: no experimental runtime, both locales."""
import os
from pathlib import Path
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QLabel
from packtoworkeu.tests._qt_support import application,wait_loaders,dispose
from packtoworkeu.db import database as db
from packtoworkeu import i18n
from packtoworkeu.ui.tabs import assisted_ingestion as ui

ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture
def clean_tab(tmp_path):
    application();p=tmp_path/'ui.db';db.initialize(p)
    old=i18n.get_locale();i18n._locale='en'
    widget=ui.AssistedIngestionTab(str(p))
    wait_loaders(widget)
    try:yield widget
    finally:
        widget._worker=None;widget.shutdown();dispose(widget);i18n._locale=old


def test_idle_surface_is_small_and_has_no_experimental_controls(clean_tab):
    w=clean_tab
    assert not hasattr(w,'browser_bridge')
    assert w.advanced_panel.isHidden() and w.verification_panel.isHidden()
    assert not w.advanced_toggle.isChecked()
    assert [w.launch_btn.text(),w.stop_btn.text(),w.open_ext_btn.text()]==['Collect / continue','Stop','Open website']
    assert not w.stop_btn.isEnabled()
    assert not any('pairing'in (label.text().lower()) or 'handoff'in label.text().lower()for label in w.findChildren(QLabel))
    assert w.minimumSizeHint().height()<550
    w.advanced_toggle.click();assert not w.advanced_panel.isHidden()
    w.advanced_toggle.click();assert w.advanced_panel.isHidden()


def test_verification_only_appears_for_actual_attention(clean_tab):
    w=clean_tab;w._worker=SimpleNamespace()
    w._on_attention({'waiting':True,'url':'https://fixture.invalid/jobs'})
    assert not w.verification_panel.isHidden()
    assert all(b.isEnabled()for b in (w.show_browser_btn,w.continue_btn,w.save_partial_btn))
    w._on_attention({'waiting':False,'url':''})
    assert w.verification_panel.isHidden()
    assert not any(b.isEnabled()for b in (w.show_browser_btn,w.continue_btn,w.save_partial_btn))
    w._worker=None


def test_language_roundtrip_retains_company_url_batch_and_collapsed_state(clean_tab):
    w=clean_tab;w.name_edit.setText('User company');w.url_edit.setText('https://fixture.invalid/jobs?country=DE')
    w.source_type_combo.setCurrentIndex(1);w.batch_combo.addItem('run label','run-1');w.batch_combo.setCurrentIndex(1)
    w._batch_rows=[dict(run_id='run-1',listing_completed=False,rows=2,pages=1)]
    for locale in ('it','en','it','en'):
        i18n._locale=locale;w.retranslate();wait_loaders(w)
        assert w.launch_btn.text()==i18n._('Collect / continue')
        assert w.advanced_toggle.text()==i18n._('Advanced options')
        assert w.open_ext_btn.text()==i18n._('Open website')
        assert w.details_text.placeholderText()==i18n._('Run details appear here.')
        assert w.name_edit.text()=='User company' and w.url_edit.text().endswith('country=DE')
        assert w.source_type_combo.currentData()=='recruiter' and w.batch_combo.currentData()=='run-1'
        assert w._worker is None and w.advanced_panel.isHidden()


def test_start_requires_explicit_action_language_and_toggles_do_not_launch(clean_tab,monkeypatch):
    w=clean_tab;calls=[]
    monkeypatch.setattr(ui.AssistedWorker,'start',lambda self:calls.append(self))
    w.advanced_toggle.click();w.advanced_toggle.click();w.retranslate();wait_loaders(w)
    assert calls==[]
    w.name_edit.setText('Any company');w.url_edit.setText('https://fixture.invalid/jobs?region=EU')
    w.launch_btn.click()
    assert len(calls)==1 and calls[0].url=='https://fixture.invalid/jobs?region=EU'
    worker=w._worker;w._worker=None;worker.deleteLater()


def test_no_extension_local_server_or_paste_runtime_remains():
    for path in ['browser_assisted','browser_pilots','packtoworkeu/core/browser_bridge.py',
        'packtoworkeu/core/browser_adapters.py','packtoworkeu/ui/browser_bridge_panel.py',
        'packtoworkeu/core/manual_import.py','packtoworkeu/ui/manual_import.py']:
        assert not(ROOT/path).exists(),path
    source=(ROOT/'packtoworkeu/ui/tabs/assisted_ingestion.py').read_text()
    assert 'browser_bridge'not in source and '53647'not in source


def test_all_new_static_keys_have_italian_translations():
    for key in i18n._ASSISTED_CLEAN_UI:
        assert i18n.LANGUAGES['it'].get(key) and i18n.LANGUAGES['it'][key]!=key


def test_dynamic_error_and_results_retranslate_without_overwriting_inputs(clean_tab):
    w=clean_tab;w._set_status('Could not load saved batches: {error}',error='fixture')
    i18n._locale='it';w.retranslate();wait_loaders(w)
    assert 'fixture'in w.status_lbl.text() and 'Impossibile'in w.status_lbl.text()
    w._last_outcome=(2,'',dict(completed=False,pages_visited=1,harvested=2,duplicates=0))
    w._set_status('Partial result. See Advanced options for details.')
    i18n._locale='en';w.retranslate();wait_loaders(w)
    assert w.status_lbl.text()==i18n._('Partial result. See Advanced options for details.')
    assert '2' in w.details_text.toPlainText()


def test_empty_or_verbose_progress_does_not_expand_idle_surface(clean_tab):
    w=clean_tab;w._on_ingestion_progress('')
    assert w.status_lbl.text()==''
    w._on_ingestion_progress('x'*500+'\nsecond line')
    assert len(w.status_lbl.text())==180
    assert 'second line'in w.details_text.toPlainText()
    assert w.advanced_panel.isHidden()
