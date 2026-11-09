"""Tools layout must scroll, never collapse readable scanner controls."""
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont
from sponsorscout import i18n
from sponsorscout.tests._qt_support import application, wait_until
from sponsorscout.tests.test_two_operation_progress_2026_11_05 import tools
from sponsorscout.application.scan_progress import ScanProgressState
from sponsorscout.ui.style import build_qss, build_light_palette


@pytest.fixture
def styled_tools(tools, monkeypatch):
    app = application()
    old_qss, old_palette = app.styleSheet(), app.palette()
    app.setStyleSheet(build_qss())
    app.setPalette(build_light_palette())
    monkeypatch.setattr(i18n, '_locale', 'en')
    yield tools
    app.setStyleSheet(old_qss)
    app.setPalette(old_palette)


def active_snapshot():
    state = ScanProgressState()
    state.apply(dict(kind='configure',total=5,resume_base=0))
    state.apply(dict(kind='task_begin',task='one',name='Carrefour Italia Negozi',phase='career',attempt=1))
    return state.snapshot()


@pytest.mark.parametrize('width,height', [(1366,620),(900,520)])
@pytest.mark.parametrize('locale', ['en','it'])
@pytest.mark.parametrize('points', [9,12])
def test_geometry_is_readable_at_short_and_narrow_sizes(styled_tools, width, height, locale, points):
    widget=styled_tools
    widget.setFont(QFont('Sans Serif',points))
    widget.resize(width,height)
    i18n._locale=locale
    widget.show()
    widget.retranslate()
    application().processEvents()
    wait_until(lambda:widget.tools_scroll.verticalScrollBar().maximum()>0)
    assert widget.size().width()==width and widget.size().height()==height
    assert widget.tools_scroll.horizontalScrollBar().maximum()==0
    controls=(widget.scan_btn,widget.custom_btn,widget.resume_btn,widget.pause_btn,widget.stop_btn)
    for button in controls:
        assert button.height()>=32
        assert button.width()>=button.minimumSizeHint().width()
    for left,right in zip(controls,controls[1:]):
        assert left.geometry().right()<right.geometry().left()
    # Approved simple workflow: performance/maintenance live in collapsed
    # Advanced, not in the scanner box's local coordinate system.
    assert not widget.advanced_toggle.isChecked()
    assert widget.advanced_panel.isHidden()
    assert widget.turbo_cb.parentWidget() is widget.advanced_panel
    assert widget.scan_bar.height()>=22 and widget.jd_bar.height()>=22
    assert widget.scan_bar.width()==widget.jd_bar.width()
    assert widget.active_scan_work.height()>=72
    assert widget.scan_log.height()>=150
    assert widget.runs_table.height()>=150
    assert widget.portal_progress_label.y()<widget.scan_bar.y()<widget.jd_progress_label.y()<widget.jd_bar.y()
    assert widget.active_work_heading.text()==i18n._('Active portal workers')
    assert widget.scan_output_heading.text()==i18n._('Live scan output')
    assert widget.turbo_cb.text()==i18n._('High-Performance Mode')
    # Advanced stays opt-in, but expanded controls must still be reachable.
    collapsed_max=widget.tools_scroll.verticalScrollBar().maximum()
    performance_state=widget.turbo_cb.isChecked()
    widget.advanced_toggle.click()
    wait_until(lambda:widget.advanced_panel.isVisible()
               and widget.tools_scroll.verticalScrollBar().maximum()>collapsed_max)
    turbo_top=widget.turbo_cb.mapTo(widget.tools_content,QPoint(0,0)).y()
    scan_bottom=widget.scan_btn.mapTo(widget.tools_content,QPoint(0,widget.scan_btn.height())).y()
    assert turbo_top>scan_bottom
    assert widget.tools_scroll.horizontalScrollBar().maximum()==0
    # Offscreen sections remain reachable; no clipped bottom-of-tab controls.
    widget.tools_scroll.ensureWidgetVisible(widget.fresh_btn)
    application().processEvents()
    point=widget.fresh_btn.mapTo(widget.tools_scroll.viewport(),QPoint(0,0))
    assert 0<=point.y()<widget.tools_scroll.viewport().height()
    assert point.y()+widget.fresh_btn.height()<=widget.tools_scroll.viewport().height()
    widget.advanced_toggle.click()
    wait_until(lambda:widget.advanced_panel.isHidden())
    assert widget.turbo_cb.isChecked()==performance_state


def test_active_retranslation_resize_and_log_preserve_progress(styled_tools):
    widget=styled_tools
    widget.resize(1366,620);widget.show()
    widget._set_scan_running_ui('Running')
    widget._on_work_progress(active_snapshot())
    widget.scan_log.setPlainText('Existing output\nListing navigation in progress')
    saved=widget._work_snapshot
    for locale,width in [('it',900),('en',1366),('it',900),('en',1366)]:
        i18n._locale=locale;widget.resize(width,520);widget.retranslate()
        application().processEvents()
        assert widget._work_snapshot==saved
        assert 'Carrefour Italia Negozi' in widget.active_scan_work.toPlainText()
        assert 'Existing output' in widget.scan_log.toPlainText()
        assert widget.scan_bar.value()<100
        assert widget.active_scan_work.height()>=72 and widget.scan_log.height()>=150
        assert widget.tools_scroll.horizontalScrollBar().maximum()==0
