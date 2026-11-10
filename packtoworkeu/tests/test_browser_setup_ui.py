"""Browser setup is explicit, bilingual and non-blocking; no live portal scan."""
import threading
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QMessageBox
from packtoworkeu import i18n
from packtoworkeu.services import browser_policy as policy
from packtoworkeu.ui.browser_setup import BrowserSetupDialog,ensure_browser_ready
from packtoworkeu.tests._qt_support import application,wait_loaders,dispose


@pytest.fixture
def dialog(monkeypatch):
    application();monkeypatch.setattr(policy,'discover',lambda:[])
    widget=BrowserSetupDialog();widget.show();application().processEvents()
    try:yield widget
    finally:
        wait_loaders(widget);widget.reject();dispose(widget)


@pytest.mark.parametrize('locale',['en','it'])
def test_setup_controls_localized_and_requires_validation(monkeypatch,locale):
    application();monkeypatch.setattr(i18n,'_locale',locale)
    monkeypatch.setattr(policy,'discover',lambda:[])
    widget=BrowserSetupDialog()
    try:
        assert widget.windowTitle()==i18n._('Browser setup')
        assert widget.download.text()==i18n._('Download app-managed Chromium')
        assert widget.probe.text()==i18n._('Validate selected browser')
        assert not widget.save.isEnabled()
    finally:widget.reject();dispose(widget)


def test_declining_download_does_not_start_network(dialog,monkeypatch):
    install=Mock();monkeypatch.setattr(policy,'install_managed',install)
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.No)
    dialog.download.click();assert not dialog.loader.busy;install.assert_not_called()


def test_probe_is_worker_and_close_waits_for_completion(dialog,monkeypatch):
    event=threading.Event();threads=[];main=threading.get_ident()
    selected=policy.read_selection()
    def validate(candidate):
        threads.append(threading.get_ident());event.wait(3);return selected
    monkeypatch.setattr(policy,'validate',validate)
    dialog.probe.click()
    assert dialog.loader.busy and not dialog.close_button.isEnabled()
    dialog.reject();assert dialog.isVisible()
    event.set();wait_loaders(dialog)
    assert threads and threads[0]!=main and dialog.save.isEnabled()
    dialog.choices.setCurrentIndex(-1);assert not dialog.save.isEnabled()


def test_probe_failure_does_not_overwrite_previous_selection(dialog,monkeypatch):
    before=policy.config_path().read_bytes()
    def fail(candidate):raise RuntimeError('headed validation failed')
    monkeypatch.setattr(policy,'validate',fail);dialog.probe.click();wait_loaders(dialog)
    assert not dialog.save.isEnabled() and 'headed validation failed'in dialog.status.text()
    assert policy.config_path().read_bytes()==before


def test_existing_selection_does_not_open_dialog(monkeypatch):
    assert policy.read_selection()
    monkeypatch.setattr(BrowserSetupDialog,'exec',Mock(side_effect=AssertionError('must not open')))
    assert ensure_browser_ready(None)


def test_changed_saved_browser_can_be_revalidated_without_redownload(monkeypatch,tmp_path):
    application();monkeypatch.setattr(policy,'discover',lambda:[])
    path=tmp_path/'previous-chromium';path.write_text('old executable')
    choice=dict(family='Chromium',kind='installed',path=str(path),validated=True,fingerprint=policy.fingerprint(str(path)))
    policy.save_selection(choice);path.write_text('updated executable')
    assert policy.read_selection() is None
    widget=BrowserSetupDialog()
    try:
        assert widget.choices.count()==1 and widget.choices.currentData()['path']==str(path)
        assert widget.probe.isEnabled() and not widget.save.isEnabled()
    finally:widget.reject();dispose(widget)
