"""Explicit, localized browser selection. Network/probes never run on the GUI thread."""
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,QMessageBox
from packtoworkeu.i18n import _
from packtoworkeu.services import browser_policy as policy
from packtoworkeu.ui.async_load import AsyncLoader

HELP = 'Choose an installed Chromium-family browser. Validation briefly opens an isolated window and also checks headless mode. Personal profiles are never used. Firefox is available for manual browsing only.'
DOWNLOAD = 'No compatible browser? Download one full Chromium into PacktoWorkEU storage? This uses network and disk space. No separate headless shell is installed. The download is removed with PacktoWorkEU data.'


class BrowserSetupDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle(_('Browser setup'));self.resize(700,300)
        self.loader=AsyncLoader(self,read_only=False,name='browser setup')
        self.validated=None
        layout=QVBoxLayout(self)
        help_label=QLabel(_(HELP));help_label.setWordWrap(True);layout.addWidget(help_label)
        self.choices=QComboBox();layout.addWidget(self.choices)
        row=QHBoxLayout();layout.addLayout(row)
        self.rescan=QPushButton(_('Detect browsers'));self.probe=QPushButton(_('Validate selected browser'))
        self.download=QPushButton(_('Download app-managed Chromium'))
        for button in (self.rescan,self.probe,self.download):row.addWidget(button)
        self.status=QLabel(_('Select a browser, then validate both modes.'));self.status.setWordWrap(True);layout.addWidget(self.status)
        bottom=QHBoxLayout();layout.addLayout(bottom)
        self.save=QPushButton(_('Save browser'));self.save.setEnabled(False)
        self.close_button=QPushButton(_('Close'));bottom.addStretch();bottom.addWidget(self.save);bottom.addWidget(self.close_button)
        self.rescan.clicked.connect(self.detect);self.probe.clicked.connect(self.validate_choice)
        self.download.clicked.connect(self.download_browser);self.save.clicked.connect(self.save_choice);self.close_button.clicked.connect(self.reject)
        self.choices.currentIndexChanged.connect(self.invalidate)
        self.detect()

    def invalidate(self,*_args):
        self.validated=None;self.save.setEnabled(False)

    def detect(self):
        self.choices.clear();values=policy.discover();selected=policy.read_selection() or policy.saved_candidate()
        if selected and not any(x['path']==selected['path']for x in values):values.insert(0,selected)
        for candidate in values:
            self.choices.addItem(candidate['family']+' — '+candidate['path'],candidate)
        if selected:
            for i in range(self.choices.count()):
                if self.choices.itemData(i)['path']==selected['path']:self.choices.setCurrentIndex(i);break
        self.probe.setEnabled(bool(values));self.invalidate()
        self.status.setText(_('Select a browser, then validate both modes.') if values else _('No supported browser detected. Install a Chromium-family browser or explicitly download Chromium.'))

    def busy(self,value):
        for w in (self.choices,self.rescan,self.probe,self.download,self.close_button):w.setEnabled(not value)
        self.save.setEnabled(not value and self.validated is not None)

    def run(self,action):
        self.busy(True);self.status.setText(_('Working... Browser validation/download runs in the background.'))
        self.loader.request(action,self.success,self.failure)

    def success(self,candidate):
        self.validated=candidate
        if not any(self.choices.itemData(i)['path']==candidate['path']for i in range(self.choices.count())):
            self.choices.addItem(candidate['family']+' — '+candidate['path'],candidate)
            self.choices.setCurrentIndex(self.choices.count()-1)
        self.validated=candidate;self.busy(False)
        self.status.setText(_('Both browser modes passed. Click Save browser to use this selection.'))

    def failure(self,error):
        self.validated=None;self.busy(False)
        self.status.setText(_('Browser setup failed: %s') % str(error))

    def validate_choice(self):
        candidate=self.choices.currentData()
        if candidate:self.run(lambda:policy.validate(candidate))

    def download_browser(self):
        if QMessageBox.question(self,_('Download Chromium?'),_(DOWNLOAD),QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.run(lambda:policy.validate(policy.install_managed(consent=True)))

    def save_choice(self):
        if self.validated is None:return
        try:policy.save_selection(self.validated)
        except Exception as exc:self.failure(exc);return
        from packtoworkeu.services import browser_fetcher
        browser_fetcher._ensure_playwright_browsers._done=False
        self.accept()

    def reject(self):
        if self.loader.busy:return
        self.loader.close();super().reject()

    def closeEvent(self,event):
        if self.loader.busy:event.ignore()
        else:self.loader.close();super().closeEvent(event)


def ensure_browser_ready(parent):
    if policy.read_selection() is not None:return True
    dialog=BrowserSetupDialog(parent)
    return dialog.exec()==QDialog.Accepted and policy.read_selection() is not None
