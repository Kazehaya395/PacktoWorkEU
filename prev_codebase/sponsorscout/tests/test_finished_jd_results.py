"""Finished JD coverage is a result, not a progress bar stuck below100%."""
import copy
import pytest
from sponsorscout import i18n
from sponsorscout.tests.test_two_operation_progress_2026_11_05 import tools,configure,event


def snapshot(coverage):
    model=configure(total=1)
    event(model,'task_begin',name='Fixture')
    event(model,'jd_snapshot',coverage=coverage)
    event(model,'task_end',status='partial')
    return model.snapshot()


@pytest.mark.parametrize('locale',['en','it'])
@pytest.mark.parametrize('status',['completed','partial','cancelled','error'])
def test_finished_partial_coverage_is_static_and_preserves_counts(tools,monkeypatch,locale,status):
    monkeypatch.setattr(i18n,'_locale',locale)
    data=snapshot(dict(observed=242,readable=78,empty=1,blocked=4,unattempted=159))
    original=copy.deepcopy(data)
    tools._set_scan_running_ui('Running');tools._on_work_progress(data)
    assert not tools.jd_bar.isHidden() and tools.jd_bar.value()==34
    tools._on_scan_finished(dict(status=status,cancelled=status=='cancelled'))
    assert tools.jd_bar.isHidden()
    text=tools.jd_progress_label.text()
    assert '78/242'in text and '164'in text and '32.2%'in text
    assert '159'in tools.jd_outcomes.text() and '4'in tools.jd_outcomes.text()
    assert data==original and tools._work_control==status
    assert tools.active_scan_work.toPlainText()==''
    for language in ('it','en'):
        monkeypatch.setattr(i18n,'_locale',language);tools.retranslate()
        assert tools.jd_bar.isHidden()
        assert '78/242'in tools.jd_progress_label.text()
        assert ('irrisolte'in tools.jd_progress_label.text())==(language=='it')
    tools._set_scan_running_ui('Next run')
    assert not tools.jd_bar.isHidden() and tools.jd_bar.maximum()==0
    assert '78/242'not in tools.jd_progress_label.text()


@pytest.mark.parametrize('locale',['en','it'])
@pytest.mark.parametrize('status',['completed','partial','cancelled','error'])
def test_zero_observation_finished_results_never_look_busy(tools,monkeypatch,locale,status):
    monkeypatch.setattr(i18n,'_locale',locale)
    tools._set_scan_running_ui('Running');tools._on_work_progress(snapshot(dict(observed=0)))
    tools._on_scan_finished(dict(status=status,cancelled=status=='cancelled'))
    assert tools.jd_bar.isHidden()
    assert '0/0'not in tools.jd_progress_label.text()
    assert ('Nessuna descrizione'in tools.jd_progress_label.text())==(locale=='it')


@pytest.mark.parametrize('locale',['en','it'])
@pytest.mark.parametrize('status',['completed','partial','cancelled','error'])
def test_missing_coverage_does_not_invent_zero_results(tools,monkeypatch,locale,status):
    monkeypatch.setattr(i18n,'_locale',locale)
    tools._set_scan_running_ui('Running')
    tools._on_scan_finished(dict(status=status,cancelled=status=='cancelled'))
    assert tools.jd_bar.isHidden()
    assert '0/0'not in tools.jd_progress_label.text()
    tools.retranslate()
    assert tools.jd_bar.isHidden()
    assert ('non disponibili'in tools.jd_progress_label.text())==(locale=='it')


def test_paused_and_stopping_keep_real_live_progress(tools):
    tools._set_scan_running_ui('Running')
    tools._on_work_progress(snapshot(dict(observed=10,readable=2,failed=3,unattempted=5)))
    for status in ('paused','stopping','running'):
        tools._on_coordinator_state(status)
        assert not tools.jd_bar.isHidden() and tools.jd_bar.value()==50
