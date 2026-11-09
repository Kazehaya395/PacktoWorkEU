"""Regression: experimental browser controls/API removed, clean outcomes kept.

Legacy filename retained so an edited-files-only update replaces old tests.
"""
import inspect
from pathlib import Path
from types import SimpleNamespace
import pytest
from sponsorscout.tests.test_assisted_clean_ui_2026_11_02 import clean_tab
from sponsorscout.ui.tabs import assisted_ingestion as ui
from sponsorscout.core import blocked_portals as bp
from sponsorscout import i18n


def test_experiments_removed_from_ui_worker_api_and_runtime(clean_tab):
    w=clean_tab
    for attr in ('native_diagnostic_check','doh_diagnostic_check','active_mode_lbl'):
        assert not hasattr(w,attr)
    for name in ('native_identity_diagnostic','cloudflare_doh_diagnostic'):
        assert name not in inspect.signature(bp.run_assisted_ingestion).parameters
        assert name not in inspect.signature(ui.AssistedWorker).parameters
    assert hasattr(bp.run_assisted_ingestion,'__wrapped__')
    for name in ('_assisted_browser_options','_new_doh_diagnostic_profile','_read_doh_diagnostic_preferences'):
        assert not hasattr(bp,name)
    source=Path(inspect.getsourcefile(bp)).read_text()
    assert 'launch_persistent_context'not in source and 'chrome://settings/security'not in source

@pytest.mark.parametrize('locale',['en','it'])
@pytest.mark.parametrize('reason',[
    'access verification remained unresolved; no readable current listing',
    'HTTP 403 access blocked (human verification not confirmed)',
    'unresolved detail verification; saved batch paused for explicit continuation',
])
def test_blocked_notice_is_plain_localized_and_preserves_uncertainty(locale,reason):
    old=i18n._locale;i18n._locale=locale
    try:
        title,text=ui._completion_notice({'stop_reason':reason})
        assert title==i18n._('Website access unavailable')
        assert i18n._('Open website')in text
        assert ('non significa'in text if locale=='it'else'does not mean'in text)
        assert 'DNS'not in text and 'Chrome/126'not in text
    finally:i18n._locale=old


def test_cancelled_is_not_misreported_as_blocked_even_with_old_wall_evidence():
    title,text=ui._completion_notice({'cancelled':True,'stop_reason':'cancelled by user','last_response':{'reason':'Cloudflare challenge page'}})
    assert title==i18n._('Collection stopped')
    assert 'You stopped'in text

@pytest.mark.parametrize('reason',['native load-more unproven; listing incomplete','maximum listing pages reached','Saved JD batch is empty or invalid'])
def test_unrelated_partial_is_not_reported_as_a_security_block(reason):
    assert ui._completion_notice({'stop_reason':reason})[0]==i18n._('Collection incomplete')


def test_actual_completion_popup_uses_helpful_message_not_generic_status(clean_tab,monkeypatch):
    w=clean_tab;seen=[]
    monkeypatch.setattr(ui.QMessageBox,'information',lambda parent,title,text:seen.append((title,text)))
    w._worker=SimpleNamespace(report={'completed':False,'stop_reason':'access verification remained unresolved','jd_coverage':{'observed':0}},queue_rows=[],queue_error='',deleteLater=lambda:None)
    w._on_ingestion_done(0,'')
    assert seen and seen[0][0]==i18n._('Website access unavailable')
    assert i18n._('Open website')in seen[0][1]
    assert seen[0][1]!=w.status_lbl.text()
