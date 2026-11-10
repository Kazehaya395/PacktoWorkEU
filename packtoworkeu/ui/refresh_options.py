"""Deliberate one-shot JD reread controls, separate from availability checks."""
from packtoworkeu.i18n import _

REFRESH_LABEL = 'Read saved descriptions again (next new run only)'
REFRESH_HELP = ('Usually leave this off. Repeat scans revisit listings and reuse confirmed descriptions from the last 24 hours unless observed job details changed. This option requests new detail reads within the selected scope; access checks and limits still apply. Resume keeps the original campaign policy.')
REFRESH_CONFIRM = ('Read saved descriptions again for this new run? This can take longer. Saved text is retained if a fresh read fails. Availability checks are separate.')
SUMMARY_HELP = ('Counts cover observed jobs, not all jobs on inaccessible listings. Blocked and inconclusive are subsets of unread/unresolved; unattempted jobs are not called blocked.')


def confirm_refresh(parent, requested):
    if not requested:
        return False
    from PySide6.QtWidgets import QMessageBox
    answer = QMessageBox.question(parent, _('Read descriptions again?'), _(REFRESH_CONFIRM),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
    return True if answer == QMessageBox.StandardButton.Yes else None


def refresh_summary(counts):
    if not isinstance(counts, dict):
        return ''
    text = _('Observed jobs — new: {new}; already saved: {existing}.').format(
        new=counts.get('jobs_new',0),existing=counts.get('jobs_existing',0))
    text += '\n' + _('Descriptions — reused: {reused}; read afresh: {fresh}; unread/unresolved: {pending} (blocked: {blocked}; inconclusive: {inconclusive}).').format(
        reused=counts.get('jd_reused',0),fresh=counts.get('jd_fresh',0),pending=counts.get('jd_pending',0),
        blocked=counts.get('jd_blocked',0),inconclusive=counts.get('jd_inconclusive',0))
    if not counts.get('tracking_complete',True):
        text += '\n' + _('Counts are partial: some observations could not be tracked.')
    return text
