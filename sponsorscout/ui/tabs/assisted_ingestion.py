"""Explicit-only assisted ingestion with the persistent, actually blocked queue."""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, Signal, QThread, Slot, QSettings
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QPlainTextEdit, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from sponsorscout.ui.async_load import AsyncLoader
from sponsorscout.i18n import _, set_table_header_labels
from sponsorscout.db import database as db
from sponsorscout.core.job_identity import portal_queue_key

from sponsorscout.ui.refresh_options import (REFRESH_LABEL, REFRESH_HELP, SUMMARY_HELP, confirm_refresh, refresh_summary)

logger = logging.getLogger(__name__)


def _outcome(count, error, report):
    pages, duplicates = report.get('pages_visited', 0), report.get('duplicates', 0)
    reason = str(report.get('stop_reason') or error or '')
    complete = bool(report.get('completed')) and not error and not report.get('persistence_errors')
    if error:
        return 'failed', _("Failed: {error}").format(error=error), False
    total=report.get('harvested',count+duplicates)
    resolved=report.get('country_resolved',0)
    message = _("Observed {total} valid vacancies: {count} new, {duplicates} refreshed. Listing pages/batches: {pages}; browser read passes: {loads}. Country resolved: {resolved}; unresolved: {unknown}. {reason}").format(
        total=total,count=count,duplicates=duplicates,pages=pages,loads=report.get('loads',pages),
        resolved=resolved,unknown=report.get('country_unresolved',max(0,total-resolved)),reason=reason)
    if report.get('resumed_details'):
        message=_("Resumed JD enrichment for {total} saved vacancies. No listing pages were crawled. {reason}").format(total=total,reason=reason)
    coverage=report.get('jd_coverage')
    if coverage is not None:
        message+='\n'+_("JD text: {readable}/{total} readable; not attempted: {unattempted}; reused: {reused}; blocked: {blocked}; failed/thin/empty: {unreadable}; budget skipped: {skipped}; deferred/protected: {protected}. Readable JDs with Unknown support verdict: {unknown}.").format(
            readable=coverage.get('readable',0),total=coverage.get('observed',0),
            unattempted=coverage.get('unattempted',0),protected=coverage.get('protected',0),
            reused=report.get('session_detail_activity',{}).get('reused',0),blocked=coverage.get('blocked',0),
            unreadable=sum(coverage.get(key,0)for key in ('failed','thin','empty','truncated')),
            skipped=coverage.get('budget_skipped',0),unknown=coverage.get('readable_unknown_support',0))
    activity=report.get('session_detail_activity')
    if activity is not None:
        message+='\n'+_("Detail transport: HTTP requests {http}; HTTP access walls {walls}; browser attempts {browser}; preferred-browser reads {preferred}; browser retries {retries}.").format(
            http=activity.get('http',0),walls=activity.get('http_blocked',0),browser=activity.get('browser',0),
            preferred=activity.get('browser_preferred',0),retries=activity.get('browser_retries',0))
    if 'detail_parallel_tabs' in report:
        message+='\n'+_("Performance: up to {tabs} parallel detail tabs; {prefetched} JDs pre-navigated; HTTP re-probe cleared {cleared} origin(s).").format(
            tabs=report.get('detail_parallel_tabs',3),prefetched=report.get('parallel_prefetch',0),
            cleared=report.get('http_reprobe_cleared',0))
    if report.get('resumed_listing'):
        message+='\n'+_('Listing collection continued from recorded state; earlier pages were not restarted.')
    if 'verification_cleared'in report:
        message+='\n'+_('Accepted clearance transitions: {cleared}; deferred by an access gate: {deferred}. A detector timeout is not a claim that you did not solve a check.').format(cleared=report['verification_cleared'],deferred=report.get('access_deferred',0))
    if report.get('resume_checkpoint_error'):
        message+='\n'+_("Resume checkpoint could not be saved: {error}").format(error=report['resume_checkpoint_error'])
    return ('resolved' if complete else 'partial'), message, complete


def _completion_notice(report,error='',complete=False,missing=0):
    """Plain user action; technical reasons remain in Advanced options/export."""
    reason=str(report.get('stop_reason')or error or'').casefold()
    if report.get('cancelled')or'cancelled by user'in reason:
        return _('Collection stopped'),_('You stopped collection. Any jobs already saved are kept.')
    if complete:
        return _('Collection complete'),_('Collection finished. Saved jobs are available in the app.')
    evidence=str((report.get('last_response')or{}).get('reason')or'').casefold()
    blocked=any(word in reason+' '+evidence for word in (
        'access blocked','access denied','access-denied','challenge page','verification remained unresolved',
        'verification not accepted','unresolved detail verification','bot protection re-engaged'))
    if 'browser closed before verification accepted' in reason:
        return _('Browser closed before access was confirmed'),_('The browser closed before a readable listing was accepted. This does not establish why it closed. Saved jobs are retained; website access is still unverified.')
    if blocked or int((report.get('jd_coverage')or{}).get('blocked')or 0)>0:
        return _('Website access unavailable'),_(
            'The website blocked access or kept asking for verification, so SponsorScout could not finish reading it.\n\n'
            'Use Open website to view jobs and apply manually in your usual browser. If verification keeps looping, stop retrying.\n\n'
            'Any jobs already saved are kept. This does not mean the website has no vacancies.')
    coverage=report.get('jd_coverage')or{}
    observed=int(coverage.get('observed')or 0)
    readable=int(coverage.get('readable')or 0)
    if observed and readable==observed and not report.get('listing_completed'):
        return _('Descriptions read; listing not verified'),_('All {total} collected descriptions are readable, but listing completeness is not verified. Saved jobs are available. Collect / continue checks the remaining listing work and reuses valid saved descriptions.').format(total=observed)
    if report.get('listing_completed') and observed and readable<observed:
        return _('Listing collected; descriptions incomplete'),_('{readable}/{total} descriptions are readable. Saved jobs are available. Collect / continue works on the saved descriptions without restarting the verified listing.').format(readable=readable,total=observed)
    if 'saved partial'in reason:
        return _('Partial results saved'),_('Collection stopped and any saved jobs are kept. You can view them in the app; use your usual browser for pages that remain inaccessible.')
    return _('Collection incomplete'),_('Collection could not finish. Any jobs already saved are kept. See Advanced options for the reason and any available resume actions.')


class AssistedWorker(QThread):
    finished_signal = Signal(int,str)  # retained for integrations; UI waits for QThread.finished
    progress_signal = Signal(str)
    queue_signal = Signal(object)
    attention_signal = Signal(object)

    def __init__(self,url: str,seed_name: str,db_path: str,max_pages: int = 500,parent=None,source_subtype='direct',resume_details=False,resume_listing=False,resume_run_id='',auto_continue=True,refresh_descriptions=False):
        super().__init__(parent)
        self.url,self.seed_name,self.db_path = url,seed_name,db_path
        self.max_pages = max_pages
        self.source_subtype = source_subtype
        self.resume_details = bool(resume_details)
        self.auto_continue = bool(auto_continue)
        self.refresh_descriptions = bool(refresh_descriptions)
        self.resume_listing,self.resume_run_id=bool(resume_listing),str(resume_run_id)
        from sponsorscout.core.assisted_control import AssistedControl
        self.control=AssistedControl(self.attention_signal.emit)
        self.count,self.error,self.report = 0,'',{}
        self.cancel_event = threading.Event()

    def run(self):
        from sponsorscout.core import blocked_portals
        blocked_portals.LAST_INGESTION_REPORT.clear()
        conn = None
        self.queue_error, self.queue_rows = '', None
        try:
            # Registration/status writes must not delay a GUI button callback.
            rows = db.list_blocked_portals(self.db_path, include_resolved=True)
            queued = next((dict(row) for row in rows if row['url_key']==portal_queue_key(self.url)), {})
            from sponsorscout.core.assisted_workflow import required_postings, reconcile_details
            required = required_postings(queued) if queued.get('access_scope')=='details' else set()
            if not any(row['url_key'] == portal_queue_key(self.url) for row in rows):
                db.enqueue_blocked_portals(self.db_path, [{'url': self.url, 'seed_name': self.seed_name,
                    'phase': 'assisted', 'reason': _('Manually requested assisted scan'),
                    'source_subtype': self.source_subtype}])
            db.update_blocked_portal(self.db_path, self.url, status='running', result='')
            self.queue_signal.emit([dict(row) for row in db.list_blocked_portals(self.db_path)])
            if self.cancel_event.is_set():
                self.report = dict(cancelled=True, completed=False, stop_reason='cancelled by user', pages_visited=0, duplicates=0)
            else:
                conn = db.get_connection(self.db_path)
                self.count = blocked_portals.run_assisted_ingestion(
                    self.url, self.seed_name, conn, max_pages=self.max_pages,
                    db_path=self.db_path, progress_callback=self.progress_signal.emit,
                    cancel_event=self.cancel_event, source_subtype=self.source_subtype,
                    **dict(resume_details=self.resume_details,resume_listing=self.resume_listing,resume_run_id=self.resume_run_id,control=self.control,auto_continue=self.auto_continue), **(dict(refresh_descriptions=True) if self.refresh_descriptions else {}))
                self.report = dict(blocked_portals.LAST_INGESTION_REPORT or {})
                if queued.get('access_scope')=='details':
                    self.report['queue_reconciliation'] = reconcile_details(required, self.report.get('posting_outcomes'))
        except Exception as exc:
            logger.exception('Assisted ingestion error')
            self.error = str(exc)
            self.report = dict(blocked_portals.LAST_INGESTION_REPORT or self.report)
        finally:
            if conn is not None:
                conn.close()
            status, message, _complete = _outcome(self.count, self.error, self.report)
            audit = self.report.get('queue_reconciliation') or {}
            if audit and not audit.get('verified'):
                if status=='resolved':status='partial'
                message += '\n' + str(audit.get('reason') or '')
            try:
                db.update_blocked_portal(self.db_path, self.url, status=status,
                    pages=self.report.get('pages_visited', 0), jobs=self.count+self.report.get('duplicates', 0), result=message)
                self.queue_rows = [dict(row) for row in db.list_blocked_portals(self.db_path)]
            except Exception as exc:
                self.queue_error = str(exc)
                logger.exception('Could not save assisted queue outcome')
        self.finished_signal.emit(self.count, self.error)


class AssistedIngestionTab(QWidget):
    """No launch at refresh/startup. Exactly one explicitly requested worker."""
    ingestion_started = Signal()
    ingestion_finished = Signal()

    def __init__(self,db_path: str,parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self._queue_loader = AsyncLoader(self, name='blocked queue read')
        self._queue_repair = AsyncLoader(self, read_only=False, name='saved access diagnostics recovery')
        self._access_backfilled = False
        self._batch_loader=AsyncLoader(self,name='selected saved batches')
        self._batch_scope='';self._batch_rows=[];self._attention_waiting=False
        self._worker: AssistedWorker | None = None
        self._scan_running = False
        self._closing = False
        self._queue_rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12,12,12,12)
        layout.setSpacing(12)

        description = QLabel(_("Select a portal to collect jobs in the assisted browser. Access is not guaranteed."))
        description.setWordWrap(True)
        layout.addWidget(description)

        self.quick_help = QLabel(_("When a normal scan leaves an access issue, use this tab. 1. Select a company in the queue below, or enter its name and careers URL. 2. Click Collect / continue and follow any verification prompt in the app browser. 3. Wait for the saved-job result; use Stop if access keeps failing. Open website is view-only. Advanced contains saved batches and separate listing/JD resume controls."))
        self.quick_help.setWordWrap(True)
        self.quick_help.setObjectName('SectionHelp')
        layout.addWidget(self.quick_help)

        portal_box = QGroupBox(_("Portal"))
        portal_layout = QVBoxLayout(portal_box)
        form = QFormLayout()
        company_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("Company or portal name"))
        company_row.addWidget(self.name_edit, 1)
        self.source_type_combo = QComboBox()
        self.source_type_combo.addItem(_('Direct employer'), 'direct')
        self.source_type_combo.addItem(_('Recruiter / agency'), 'recruiter')
        company_row.addWidget(self.source_type_combo)
        form.addRow(_("Company / Seed Name:"), company_row)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText('https://careers.example/jobs')
        form.addRow(_("Portal Careers URL:"), self.url_edit)
        portal_layout.addLayout(form)

        buttons = QHBoxLayout()
        self.launch_btn = QPushButton(_("Collect / continue"))
        self.launch_btn.setObjectName('Primary')
        self.launch_btn.setToolTip(_("Open the app-controlled browser and collect jobs from this portal."))
        self.launch_btn.clicked.connect(lambda checked=False: self._start_ingestion())
        buttons.addWidget(self.launch_btn)
        self.stop_btn = QPushButton(_("Stop"))
        self.stop_btn.clicked.connect(self.cancel_ingestion)
        self.stop_btn.setEnabled(False)
        buttons.addWidget(self.stop_btn)
        self.open_ext_btn = QPushButton(_("Open website"))
        self.open_ext_btn.setToolTip(_("View only: opening your browser does not start ingestion or share its session with the app."))
        self.open_ext_btn.clicked.connect(self._open_external)
        buttons.addWidget(self.open_ext_btn)
        buttons.addStretch(1)
        portal_layout.addLayout(buttons)
        self._status_key = "Idle — select a portal to begin."
        self._status_args = {}
        self._last_outcome = None
        self.status_lbl = QLabel(_(self._status_key))
        self.status_lbl.setTextFormat(Qt.PlainText)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet('color: #4B5563; font-weight: bold;')
        portal_layout.addWidget(self.status_lbl)
        self.repeat_summary = QLabel()
        self.repeat_summary.setWordWrap(True)
        self.repeat_summary.setTextFormat(Qt.PlainText)
        self.repeat_summary.setToolTip(_(SUMMARY_HELP))
        self.repeat_summary.hide()
        portal_layout.addWidget(self.repeat_summary)
        self.verification_panel = QWidget()
        verification_layout = QVBoxLayout(self.verification_panel)
        verification_layout.setContentsMargins(0, 0, 0, 0)
        self.gate_lbl = QLabel(_('Complete verification in the assisted browser, then continue or save partial results.'))
        self.gate_lbl.setWordWrap(True)
        verification_layout.addWidget(self.gate_lbl)
        gate_row = QHBoxLayout()
        self.show_browser_btn = QPushButton(_('Show verification browser'))
        self.continue_btn = QPushButton(_('Continue after verification'))
        self.save_partial_btn = QPushButton(_('Save partial and close'))
        self.show_browser_btn.clicked.connect(lambda: self._send_gate('show_browser'))
        self.continue_btn.clicked.connect(lambda: self._send_gate('continue_check'))
        self.save_partial_btn.clicked.connect(lambda: self._send_gate('save_partial'))
        for button in (self.show_browser_btn, self.continue_btn, self.save_partial_btn):
            button.setEnabled(False)
            gate_row.addWidget(button)
        gate_row.addStretch(1)
        verification_layout.addLayout(gate_row)
        self.verification_panel.hide()
        portal_layout.addWidget(self.verification_panel)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText(_('Advanced options'))
        self.advanced_toggle.setToolTip(_('Saved batches, resume controls and run details.'))
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.RightArrow)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        portal_layout.addWidget(self.advanced_toggle)
        self.advanced_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.refresh_jd_cb = QCheckBox(_(REFRESH_LABEL))
        self.refresh_jd_cb.setToolTip(_(REFRESH_HELP))
        advanced_layout.addWidget(self.refresh_jd_cb)
        self.refresh_jd_help = QLabel(_(REFRESH_HELP))
        self.refresh_jd_help.setWordWrap(True)
        advanced_layout.addWidget(self.refresh_jd_help)
        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel(_('Saved batch:')))
        self.batch_combo = QComboBox()
        self.batch_combo.addItem(_('Latest saved batch'), '')
        saved_row.addWidget(self.batch_combo, 1)
        self.batch_load_btn = QPushButton(_('Load saved batches'))
        self.batch_load_btn.clicked.connect(self.load_saved_batches)
        saved_row.addWidget(self.batch_load_btn)
        advanced_layout.addLayout(saved_row)
        resume_row = QHBoxLayout()
        self.resume_btn = QPushButton(_('Resume unread JDs'))
        self.resume_btn.setToolTip(_('Continue the saved batch for this exact portal URL; keep fresh proven JDs and do not crawl listing pages again.'))
        self.resume_btn.clicked.connect(lambda checked=False: self._start_ingestion(resume_details=True))
        resume_row.addWidget(self.resume_btn)
        self.resume_listing_btn = QPushButton(_('Resume listing collection'))
        self.resume_listing_btn.setToolTip(_('Continue only from an actually recorded listing URL; this is separate from unread JD continuation.'))
        self.resume_listing_btn.clicked.connect(lambda checked=False: self._start_ingestion(resume_listing=True))
        resume_row.addWidget(self.resume_listing_btn)
        resume_row.addStretch(1)
        advanced_layout.addLayout(resume_row)
        self.show_resolved = QCheckBox(_('Show resolved portals'))
        self.show_resolved.toggled.connect(self.refresh_queue)
        advanced_layout.addWidget(self.show_resolved)
        self.details_text = QPlainTextEdit()
        self.details_text.setReadOnly(True)
        self.details_text.setMaximumBlockCount(150)
        self.details_text.setMaximumHeight(140)
        self.details_text.setPlaceholderText(_('Run details appear here.'))
        advanced_layout.addWidget(self.details_text)
        self.advanced_panel.hide()
        portal_layout.addWidget(self.advanced_panel)
        self.name_edit.textChanged.connect(self._invalidate_batches)
        self.url_edit.textChanged.connect(self._invalidate_batches)
        layout.addWidget(portal_box)

        queue_box = QGroupBox(_("Listing and JD access issues from your scans"))
        queue_layout = QVBoxLayout(queue_box)
        queue_controls = QHBoxLayout()
        self.queue_count_lbl = QLabel()
        queue_controls.addWidget(self.queue_count_lbl)
        queue_controls.addStretch(1)
        refresh = QPushButton(_("Refresh queue"))
        refresh.clicked.connect(self.refresh_queue)
        queue_controls.addWidget(refresh)
        queue_layout.addLayout(queue_controls)
        self.portals_table = QTableWidget(0,5)
        set_table_header_labels(self.portals_table, 
            [_("Company"),_("Target URL"),_("Reason / Phase"),_("Status"),_("Action")])
        self.portals_table.verticalHeader().setVisible(False)
        header = self.portals_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for index,width in enumerate((140,360,420,160,170)):
            header.resizeSection(index,width)
        self._restore_queue_widths()
        self.portals_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.portals_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.portals_table.itemSelectionChanged.connect(self._load_selected_inputs)
        queue_layout.addWidget(self.portals_table)
        self.queue_details = QPlainTextEdit()
        self.queue_details.setReadOnly(True)
        self.queue_details.setMaximumHeight(110)
        self.queue_details.setPlaceholderText(_('Select a queue row to read the full access reason and latest result.'))
        queue_layout.addWidget(self.queue_details)
        layout.addWidget(queue_box,1)
        self.batch_combo.currentIndexChanged.connect(lambda _index: self._set_controls())
        self._populate_known_portals()

    def _queue_widths_key(self):
        import hashlib,os
        return 'assisted/queue_widths/'+hashlib.sha256(os.path.abspath(self.db_path).encode()).hexdigest()[:24]

    def _restore_queue_widths(self):
        import json
        try:
            widths=json.loads(QSettings('SponsorScout','SponsorScout').value(self._queue_widths_key(),'[]'))
            if isinstance(widths,list)and len(widths)==5 and all(type(x)is int and 40<=x<=3000 for x in widths):
                for index,width in enumerate(widths):self.portals_table.setColumnWidth(index,width)
        except (TypeError,ValueError):pass

    def _save_queue_widths(self):
        import json
        widths=[max(40,min(3000,self.portals_table.columnWidth(i)))for i in range(5)]
        QSettings('SponsorScout','SponsorScout').setValue(self._queue_widths_key(),json.dumps(widths))

    def _update_queue_details(self):
        index=self.portals_table.currentRow()
        if not 0<=index<len(self._queue_rows):
            self.queue_details.clear();return
        row=self._queue_rows[index]
        reason=str(row.get('reason')or'')
        if row.get('access_scope')=='details':
            reason=_('Job descriptions: access unresolved. Saved records do not prove listing completeness. {reason}').format(reason=reason)
        else:
            reason=_('Listing access unresolved. This does not mean there are no vacancies. {reason}').format(reason=reason)
        self.queue_details.setPlainText(str(row.get('seed_name')or'')+'\n'+str(row.get('url')or'')+'\n'+reason+'\n\n'+str(row.get('result')or''))

    def _populate_known_portals(self):
        # Compatibility method name, but there are NO static demo portals.
        self.refresh_queue()

    def refresh_queue(self, *_args):
        if not self._access_backfilled:
            self._access_backfilled = True
            from sponsorscout.core.blocked_portals import backfill_recent_access_issues
            self._queue_repair.request(
                lambda: backfill_recent_access_issues(self.db_path),
                lambda _count: self.refresh_queue(), self._queue_repair_error)
            return
        path, include = self.db_path, self.show_resolved.isChecked()
        self._queue_loader.request(
            lambda: [dict(row) for row in db.list_blocked_portals(path, include_resolved=include)],
            self._apply_queue, self._queue_read_error)

    def _queue_repair_error(self, exc):
        logger.warning('Could not recover older access diagnostics: %s', exc)
        self.refresh_queue()  # Existing persistent queue must still be visible.

    def _queue_read_error(self, exc):
        self._set_status("Could not load blocked queue: {error}", error=str(exc))
        logger.warning('Could not load blocked queue: %s', exc)

    def _accept_worker_queue(self, rows):
        self._queue_loader.invalidate()  # older pending/read snapshots must not regress running/result status
        self._apply_queue(rows)

    def _apply_queue(self, rows):
        selected = self.portals_table.currentRow()
        selected_key = self._queue_rows[selected].get('url_key') if 0 <= selected < len(self._queue_rows) else None
        self._queue_rows = [row for row in rows if self.show_resolved.isChecked() or row['status'] not in ('resolved', 'dismissed')]
        self.portals_table.setRowCount(len(self._queue_rows))
        status_labels = {'pending':_("Pending"),'running':_("Running"),'partial':_("Partial"),
                         'resolved':_("Resolved"),'failed':_("Failed"),'dismissed':_("Dismissed")}
        for index,row in enumerate(self._queue_rows):
            reason = f"{row['phase'].upper()}: {row['reason']}".strip(': ')
            status = status_labels.get(row['status'],row['status'])
            if row['status']=='running' and self._worker is None:
                status = _('Interrupted — retry explicitly')
            if row.get('pages') or row.get('jobs'):
                status += ' — ' + _("{jobs} jobs / {pages} pages").format(jobs=row['jobs'],pages=row['pages'])
            values = [row['seed_name'],row['url'],reason,status]
            for column,value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(row.get('result') or value) if column==3 else str(value))
                self.portals_table.setItem(index,column,item)
            detail_only = row.get('access_scope') == 'details'
            if detail_only:
                reason = _('Job descriptions: access unresolved. Saved records do not prove listing completeness. {reason}').format(reason=reason)
                self.portals_table.item(index, 2).setText(reason)
                self.portals_table.item(index, 2).setToolTip(reason)
            button = QPushButton(_("Collect / continue"))
            fm = QFontMetrics(button.font())
            button.setMinimumWidth(fm.horizontalAdvance(button.text())+28)
            button.clicked.connect(lambda checked=False,r=dict(row): self._select_portal(r['seed_name'],r['url'],r.get('source_subtype','direct')))
            self.portals_table.setCellWidget(index,4,button)
        if selected_key is not None:
            for index, row in enumerate(self._queue_rows):
                if row.get('url_key') == selected_key:
                    self.portals_table.selectRow(index)
                    break
        self._update_queue_details()
        self.queue_count_lbl.setText(_("{count} queued portals").format(count=len(self._queue_rows)))
        self._set_controls()

    def _toggle_advanced(self, expanded):
        self.advanced_panel.setVisible(expanded)
        self.advanced_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)

    def _set_status(self, key, **values):
        self._status_key, self._status_args = key, values
        self.status_lbl.setText(_(key).format(**values))

    def set_scan_running(self,running: bool):
        self._scan_running = bool(running)
        self._set_controls()

    def _set_controls(self):
        busy = self._worker is not None
        enabled = not busy and not self._scan_running
        self.launch_btn.setEnabled(enabled)
        self.refresh_jd_cb.setEnabled(enabled)
        self.open_ext_btn.setEnabled(enabled)
        batch=self._selected_batch()
        resumable=bool(batch and int(batch.get('rows')or 0)>0)
        self.resume_btn.setEnabled(enabled and resumable)
        self.resume_btn.setToolTip(_('Resume unread job descriptions from this saved batch.')if resumable else
            _('No saved jobs to resume. Load a non-empty saved batch, or use Start collection.'))
        self.resume_listing_btn.setEnabled(enabled)
        self.batch_combo.setEnabled(enabled);self.batch_load_btn.setEnabled(enabled)
        for button in(self.show_browser_btn,self.continue_btn,self.save_partial_btn):button.setEnabled(self._worker is not None and self._attention_waiting)
        self.name_edit.setEnabled(not busy)
        self.source_type_combo.setEnabled(not busy)
        self.url_edit.setEnabled(not busy)
        self.stop_btn.setEnabled(self._worker is not None)
        self.verification_panel.setVisible(busy and self._attention_waiting)
        for index in range(self.portals_table.rowCount()):
            button = self.portals_table.cellWidget(index,4)
            if button is not None:
                button.setEnabled(enabled)

    def _selected_batch(self):
        if self._batch_scope!=portal_queue_key(self.url_edit.text().strip()):return None
        run=self.batch_combo.currentData()
        return next((row for row in self._batch_rows if row.get('run_id')==run),None)if run else next(iter(self._batch_rows),None)

    def _invalidate_batches(self,*_args):
        self._batch_loader.invalidate();self._batch_scope='';self._batch_rows=[]
        self.batch_combo.clear();self.batch_combo.addItem(_('Latest saved batch'),'')
        self._set_controls()

    def load_saved_batches(self):
        if self._worker is not None or self._scan_running:return
        url,name,path=self.url_edit.text().strip(),self.name_edit.text().strip(),self.db_path
        key=portal_queue_key(url)
        if not key:return
        def load():
            from sponsorscout.core.assisted_resume import list_batches
            with db.get_connection(path)as conn:return list_batches(conn,url,name)
        def apply(rows):
            if key!=portal_queue_key(self.url_edit.text().strip())or name!=self.name_edit.text().strip():return
            self._batch_scope=key;self._batch_rows=rows;self._render_batches()
        self._batch_loader.request(load,apply,lambda exc:self._set_status('Could not load saved batches: {error}', error=str(exc)))

    def _render_batches(self):
        selected=self.batch_combo.currentData()
        self.batch_combo.clear();self.batch_combo.addItem(_('Latest saved batch'),'')
        for row in self._batch_rows:
            state=_('Listing complete')if row['listing_completed']else _('Listing incomplete')
            self.batch_combo.addItem(_('{run}: {rows} saved jobs / {pages} pages — {state}').format(run=row['run_id'],rows=row['rows'],pages=row['pages'],state=state),row['run_id'])
        index=self.batch_combo.findData(selected)
        if index>=0:self.batch_combo.setCurrentIndex(index)
        self._set_controls()

    def _send_gate(self,command):
        worker=self._worker
        if worker is None or not self._attention_waiting:return
        getattr(worker.control,command)()
        if command=='continue_check':self.gate_lbl.setText(_('Checking current visible content; a button click does not certify verification.'))
        elif command=='save_partial':self.gate_lbl.setText(_('Saving partial results and closing the verification session...'))

    def _on_attention(self,info):
        if self._worker is None:return
        self._attention_waiting=bool(info.get('waiting'))
        if self._attention_waiting:
            label=_('Complete verification in the assisted browser, then continue or save partial results.')
        elif info.get('url'):
            label=_('Current content check finished; authentic primary content is still required.')
        else:
            # close() sends an empty URL; do not leave a stale "still required"
            # warning on a session that has already finished.
            label=_('Verification controls are inactive.')
        self.gate_lbl.setText(label)
        self.gate_lbl.setStyleSheet('color: #D97706; font-weight: bold;'if self._attention_waiting else'')
        self._set_controls()

    def _load_selected_inputs(self):
        self._update_queue_details()
        if self._worker is not None or self._scan_running:return
        index=self.portals_table.currentRow()
        if 0<=index<len(self._queue_rows):
            row=self._queue_rows[index]
            self.name_edit.setText(row['seed_name']);self.url_edit.setText(row['url'])
            self.source_type_combo.setCurrentIndex(1 if row.get('source_subtype')=='recruiter'else 0)

    def _view_detail_issue(self, row):
        """Read-only queue action; never re-crawl a listing to fix a JD gap."""
        if self._worker is not None or self._scan_running:
            return
        self.name_edit.setText(row['seed_name'])
        self.url_edit.setText(row['url'])
        self.source_type_combo.setCurrentIndex(1 if row.get('source_subtype') == 'recruiter' else 0)
        message = _("Listing jobs are already saved. Some job-detail pages could not be read. This action does not start collection or bypass verification. See this run's scan log for the access evidence.")
        self.details_text.setPlainText(message + '\n\n' + str(row.get('reason') or ''))
        self.advanced_toggle.setChecked(True)
        self._toggle_advanced(True)
        self._set_status(message)

    def _select_portal(self,comp: str,url: str,source_subtype='direct'):
        if self._worker is not None or self._scan_running:
            return
        self.name_edit.setText(comp)
        self.source_type_combo.setCurrentIndex(1 if source_subtype=='recruiter' else 0)
        self.url_edit.setText(url)
        self._start_ingestion()  # explicit row action only

    def _open_external(self):
        if self._worker is not None or self._scan_running:
            return
        url = self.url_edit.text().strip()
        if not portal_queue_key(url):
            QMessageBox.warning(self,_("Missing URL"),_("Please provide a valid HTTP or HTTPS portal URL."))
            return
        from sponsorscout.core.blocked_portals import open_in_default_browser
        open_in_default_browser(url)
        self._set_status("Website opened for viewing only. Collection has not started.")

    def _start_ingestion(self,resume_details=False,resume_listing=False):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._worker is not None or self._scan_running:
            return
        if resume_details and not (self._selected_batch() and int(self._selected_batch().get('rows')or 0)>0):
            self._set_status('No saved jobs to resume. Load a non-empty saved batch, or use Start collection.')
            return
        url = self.url_edit.text().strip()
        name = self.name_edit.text().strip() or _("Career Portal")
        if not portal_queue_key(url):
            QMessageBox.warning(self,_("Missing URL"),_("Please provide a valid HTTP or HTTPS portal URL."))
            return
        from sponsorscout.ui.browser_setup import ensure_browser_ready
        if not ensure_browser_ready(self):
            return
        # Preserve filters and user-entered provenance; never infer a company
        # from its hostname or silently reuse a selected row's old name.
        index=self.portals_table.currentRow()
        if 0<=index<len(self._queue_rows):
            selected=self._queue_rows[index]
            if (name==selected.get('seed_name') and
                    portal_queue_key(url)!=portal_queue_key(selected.get('url',''))):
                answer=QMessageBox.question(self,_("Confirm portal identity"),
                    _("The URL differs from the selected queued portal, but the company name is unchanged. Continue with this exact name and URL?"),
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer!=QMessageBox.StandardButton.Yes:return
        refresh = confirm_refresh(self, self.refresh_jd_cb.isChecked() and not (resume_details or resume_listing))
        if refresh is None:
            return
        self.repeat_summary.hide()
        self.status_lbl.setStyleSheet('color: #2563EB; font-weight: bold;')
        self._set_status("Opening the assisted browser…")
        self._last_outcome = None
        self.details_text.clear()
        worker = AssistedWorker(url,name,self.db_path,max_pages=500,parent=self,
                                source_subtype=self.source_type_combo.currentData(),resume_details=bool(resume_details),
                                resume_listing=bool(resume_listing),resume_run_id=(self.batch_combo.currentData()or'')if self._batch_scope==portal_queue_key(url)else'',
                                **(dict(refresh_descriptions=True) if refresh else {}))
        if refresh:
            self.refresh_jd_cb.setChecked(False)
        self._worker = worker
        worker.progress_signal.connect(self._on_ingestion_progress)
        worker.attention_signal.connect(self._on_attention)
        worker.queue_signal.connect(self._accept_worker_queue)
        worker.finished.connect(self._on_worker_finished)
        self._set_controls()
        self.refresh_queue()
        self.ingestion_started.emit()
        worker.start()

    def cancel_ingestion(self):
        if self._worker is not None:
            self._worker.cancel_event.set()
            self.stop_btn.setEnabled(False)
            self._set_status("Stopping collection… Saved jobs are retained.")

    def _on_ingestion_progress(self,msg: str):
        attention = any(token in msg.casefold() for token in ('action needed','azione richiesta','verification','verifica'))
        self.status_lbl.setStyleSheet('color: ' + ('#D97706' if attention else '#2563EB') + '; font-weight: bold;')
        self._status_key = None
        self.details_text.appendPlainText(msg)
        self.status_lbl.setText(str(msg).partition('\n')[0][:180])

    @Slot()
    def _on_worker_finished(self):
        worker = self._worker
        if worker is not None:
            self._on_ingestion_done(worker.count, worker.error)

    def _on_ingestion_done(self, count: int, err: str):
        worker = self._worker
        if worker is None:
            return
        report = dict(worker.report or {})
        _status, message, complete = _outcome(count, err, report)
        queue_error = getattr(worker, 'queue_error', '')
        if queue_error:
            message += '\n' + _("Could not save queue outcome: {error}").format(error=queue_error)
        self._last_outcome = (count, err, report)
        self.repeat_summary.setText(refresh_summary(report.get('repeat_scan')))
        self.repeat_summary.setVisible(isinstance(report.get('repeat_scan'), dict))
        self.details_text.setPlainText(message)
        self._set_status("Collection failed. Saved jobs are retained." if err else
                         "Listing: {listing}; descriptions: {readable}/{observed} readable. {queue_note}",
                         **({} if err else dict(listing=_('verified') if report.get('listing_completed') else _('not verified'),
                             readable=(report.get('jd_coverage')or{}).get('readable',0),
                             observed=(report.get('jd_coverage')or{}).get('observed',0),
                             queue_note=_('Earlier saved-job gap not reconciled.') if report.get('queue_reconciliation') and not report['queue_reconciliation'].get('verified') else '')))
        rows = getattr(worker, 'queue_rows', None)
        self._worker = None
        self._attention_waiting=False
        worker.deleteLater()
        self._set_controls()
        if rows is not None:
            self._accept_worker_queue(rows)
        else:
            self.refresh_queue()
        # Also emit for 0-new refreshes, partial writes and failures, so the
        # other tabs unlock and show every successfully committed job.
        self.ingestion_finished.emit()
        if not self._closing:
            coverage = report.get('jd_coverage') or {}
            missing = sum(coverage.get(key, 0) for key in (
                'unattempted', 'empty', 'thin', 'failed', 'blocked',
                'budget_skipped', 'truncated', 'protected'))
            if not complete and not err and missing:
                message += '\n' + _("Some full job descriptions were not read. Use Resume unread JDs to continue; no Unknown support verdict has been converted to No.")
            self.details_text.setPlainText(message)
            title,notice=_completion_notice(report,err,complete,missing)
            QMessageBox.information(self,title,notice)

    def retranslate(self):
        self.refresh_jd_cb.setText(_(REFRESH_LABEL))
        self.refresh_jd_cb.setToolTip(_(REFRESH_HELP))
        self.refresh_jd_help.setText(_(REFRESH_HELP))
        self.repeat_summary.setToolTip(_(SUMMARY_HELP))
        if self._last_outcome:
            self.repeat_summary.setText(refresh_summary(self._last_outcome[2].get('repeat_scan')))
        self.queue_details.setPlaceholderText(_('Select a queue row to read the full access reason and latest result.'))
        from sponsorscout.i18n import retranslate_widget_literals
        retranslate_widget_literals(self)
        # Historical '&'/'and' aliases share an Italian value; keep this
        # control's exact canonical key instead of reverse-inferring it.
        self.launch_btn.setText(_('Collect / continue'))
        self.launch_btn.setToolTip(_('Open the app-controlled browser and collect jobs from this portal.'))
        self.stop_btn.setText(_('Stop'))
        self.open_ext_btn.setText(_('Open website'))
        self.advanced_toggle.setText(_('Advanced options'))
        self.advanced_toggle.setToolTip(_('Saved batches, resume controls and run details.'))
        self.details_text.setPlaceholderText(_('Run details appear here.'))
        if self._status_key: self._set_status(self._status_key, **self._status_args)
        if self._last_outcome: self.details_text.setPlainText(_outcome(*self._last_outcome)[1])
        self.resume_btn.setText(_('Resume unread JDs'))
        self.resume_listing_btn.setText(_('Resume listing collection'))
        self.batch_load_btn.setText(_('Load saved batches'))
        self.show_browser_btn.setText(_('Show verification browser'))
        self.continue_btn.setText(_('Continue after verification'))
        self.save_partial_btn.setText(_('Save partial and close'))
        self._render_batches()
        self._set_controls()
        self.name_edit.setPlaceholderText(_('Company or portal name'))
        self.source_type_combo.setItemText(0,_('Direct employer'))
        self.source_type_combo.setItemText(1,_('Recruiter / agency'))
        set_table_header_labels(self.portals_table, 
            [_('Company'),_('Target URL'),_('Reason / Phase'),_('Status'),_('Action')])
        self.refresh_queue()  # data only; never starts a worker
        self._update_queue_details()

    def shutdown(self,wait_ms=3000):
        self._save_queue_widths()
        self._closing = True
        self._queue_repair.close()
        self._queue_loader.close()
        self._batch_loader.close()
        self.cancel_ingestion()
        idle = self._worker is None or self._worker.wait(wait_ms)
        if idle and self._worker is not None:
            # Persist the queue outcome even if the event loop has not yet
            # delivered QThread.finished while the window is closing.
            self._on_ingestion_done(self._worker.count,self._worker.error)
        return idle
