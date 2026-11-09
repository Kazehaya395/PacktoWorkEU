"""Keep useful empty-batch guards after browser experiments are removed."""
from types import SimpleNamespace
import pytest
from sponsorscout.tests.test_assisted_clean_ui_2026_11_02 import clean_tab
from sponsorscout.ui.tabs import assisted_ingestion as ui
from sponsorscout.core.job_identity import portal_queue_key
from sponsorscout import i18n

def batches(w):
    w.url_edit.setText('https://fixture.invalid/search')
    w._batch_scope=portal_queue_key(w.url_edit.text())
    w._batch_rows=[dict(run_id='empty',rows=0,pages=0,listing_completed=False),dict(run_id='saved',rows=3,pages=1,listing_completed=False)]
    w._render_batches()


def test_empty_latest_and_explicit_batches_disable_jd_resume_but_not_fresh_start(clean_tab):
    w=clean_tab;batches(w)
    assert not w.resume_btn.isEnabled() and w.launch_btn.isEnabled()
    assert 'No saved jobs'in w.resume_btn.toolTip()
    w.batch_combo.setCurrentIndex(w.batch_combo.findData('saved'))
    assert w.resume_btn.isEnabled()
    w.batch_combo.setCurrentIndex(w.batch_combo.findData('empty'))
    assert not w.resume_btn.isEnabled()
    w._start_ingestion(resume_details=True)
    assert w._worker is None and 'No saved jobs'in w.status_lbl.text()


def test_unknown_or_changed_scope_requires_explicit_batch_load(clean_tab):
    w=clean_tab
    assert not w.resume_btn.isEnabled()
    batches(w);w.batch_combo.setCurrentIndex(w.batch_combo.findData('saved'))
    assert w.resume_btn.isEnabled()
    w.url_edit.setText('https://fixture.invalid/search?region=DE')
    assert not w.resume_btn.isEnabled()
    assert w.launch_btn.isEnabled()

