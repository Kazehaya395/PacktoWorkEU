"""Approved minimal audit repairs: no seed mutations, network or browser use."""
import hashlib
import shutil

import pytest

from sponsorscout.application import seed_manager as sm
from sponsorscout.core.persistence import upsert_job
from sponsorscout.db import database as db
from sponsorscout import i18n
from sponsorscout.tests._qt_support import application, dispose


def test_bundled_seeds_validate_with_existing_government_category():
    assert sm.SOURCE_TYPES == ('direct_employer', 'recruiter', 'government')
    for path in (sm.bundled_ats_path(), sm.bundled_career_path()):
        ok, errors = sm.validate_file(path)
        assert ok, errors
    rows = sm.read_seed_rows(sm.bundled_career_path())['rows']
    assert len([row for row in rows if row.get('source_type') == 'government']) == 6
    government = next(row for row in rows if row.get('source_type') == 'government')
    assert sm.validate_row(dict(government, source_type='goverment'))


@pytest.mark.parametrize('locale', ['en', 'it'])
def test_career_editor_saves_copy_and_retains_categories_headers_and_rows(tmp_path, monkeypatch, locale):
    from sponsorscout.ui.tabs.data_management import SeedEditor, QMessageBox
    application()
    original = sm.bundled_career_path()
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    copy = tmp_path / 'career.csv'
    shutil.copyfile(original, copy)
    expected = sm.read_seed_rows(copy)
    warnings, successes = [], []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: successes.append(args[2]))
    monkeypatch.setattr(i18n, '_locale', locale)
    editor = SeedEditor(copy, 'Audit regression')
    try:
        editor.save()
        assert not warnings, warnings
        assert len(successes) == 1
        result = sm.read_seed_rows(copy)
        assert result['columns'] == expected['columns']
        assert result['rows'] == expected['rows']
        assert sm.validate_file(copy)[0]
        assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
    finally:
        dispose(editor)


@pytest.mark.parametrize('locale', ['en', 'it'])
def test_government_dialog_roundtrip_is_not_silently_reclassified(monkeypatch, locale):
    from sponsorscout.ui.tabs.data_management import SeedRowDialog
    application()
    monkeypatch.setattr(i18n, '_locale', locale)
    row = next(row for row in sm.read_seed_rows(sm.bundled_career_path())['rows']
               if row.get('source_type') == 'government')
    dialog = SeedRowDialog(row, 'Audit regression')
    try:
        assert dialog.adv_combos['source_type'].currentText() == i18n._('government')
        assert dialog.adv_combos['source_type'].currentData() == 'government'
        assert dialog.row()['source_type'] == 'government'
    finally:
        dispose(dialog)


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / 'manual.db'
    db.initialize(path)
    connection = db.get_connection(path)
    try:
        yield connection
    finally:
        connection.close()


def _job(**kwargs):
    return dict(dict(url='https://fixture.invalid/job/42', title='Software engineer',
                     company='Audit company', location='Remote'), **kwargs)


def _country(conn):
    return tuple(conn.execute('SELECT country,country_source FROM jobs').fetchone())


@pytest.mark.parametrize('old_country,new_country', [('Germany', 'Italy'), ('Italy', 'Germany')])
def test_explicit_manual_country_can_be_corrected_again(conn, old_country, new_country):
    upsert_job(conn, _job(country=old_country, country_source='manual'))
    upsert_job(conn, _job(country=new_country, country_source='manual'))
    assert _country(conn) == (new_country, 'manual')
    assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1


@pytest.mark.parametrize('unknown', ['', 'Unknown'])
@pytest.mark.parametrize('location', ['', 'Remote', 'Berlin, Germany'])
def test_explicit_manual_unknown_is_not_reinferred_or_restored(conn, unknown, location):
    upsert_job(conn, _job(country='Germany', country_source='manual', location='Berlin, Germany'))
    upsert_job(conn, _job(country=unknown, country_source='manual', location=location))
    assert _country(conn) == (unknown, 'manual')
    upsert_job(conn, _job(country='Italy', country_source='provider_country', location='Rome, Italy'))
    assert _country(conn) == (unknown, 'manual')


@pytest.mark.parametrize('old_country', ['Germany', '', 'Unknown'])
@pytest.mark.parametrize('source', ['provider_country', 'job_location', ''])
def test_automatic_write_never_replaces_manual_country(conn, old_country, source):
    upsert_job(conn, _job(country=old_country, country_source='manual'))
    upsert_job(conn, _job(country='Italy', country_source=source, location='Rome, Italy'))
    assert _country(conn) == (old_country, 'manual')


def test_new_manual_unknown_does_not_use_observed_location(conn):
    upsert_job(conn, _job(country='', country_source='manual', location='Berlin, Germany'))
    assert _country(conn) == ('', 'manual')


def test_automatic_country_can_still_be_refreshed(conn):
    upsert_job(conn, _job(country='Germany', country_source='provider_country'))
    upsert_job(conn, _job(country='Italy', country_source='provider_country'))
    assert _country(conn) == ('Italy', 'provider_country')


def test_integrity_guard_uses_the_new_explicit_manual_country(conn):
    upsert_job(conn, _job(country='Germany', country_source='manual', eu_blue_card_verdict='Yes'))
    upsert_job(conn, _job(country='United States', country_source='manual',
                          eu_blue_card_verdict='Yes', _support_authoritative=True))
    row = conn.execute('SELECT country,country_source,eu_blue_card_verdict,eu_blue_card FROM jobs').fetchone()
    assert tuple(row) == ('United States', 'manual', 'Unknown', 0)


def test_automatic_integrity_guard_still_uses_protected_manual_country(conn):
    upsert_job(conn, _job(country='United States', country_source='manual'))
    upsert_job(conn, _job(country='Germany', country_source='provider_country',
                          eu_blue_card_verdict='Yes', _support_authoritative=True))
    row = conn.execute('SELECT country,country_source,eu_blue_card_verdict,eu_blue_card FROM jobs').fetchone()
    assert tuple(row) == ('United States', 'manual', 'Unknown', 0)


def test_seed_test_discovery_uses_shipped_paths_from_any_cwd(tmp_path, monkeypatch):
    from sponsorscout.tests import test_seed_v7 as v7
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'company_Career_seed.csv').write_text('unrelated cwd file')
    assert v7._find_seed('company_Career_seed.csv') == sm.bundled_career_path()
    assert v7._find_seed('company_ATS_seed.csv') == sm.bundled_ats_path()


def test_missing_bundled_seed_is_failure_not_skip(tmp_path, monkeypatch):
    from sponsorscout.tests import test_seed_v7 as v7
    monkeypatch.setattr(v7.seed_manager, 'bundled_career_path', lambda: tmp_path / 'missing.csv')
    with pytest.raises(AssertionError, match='Bundled seed missing'):
        v7._find_seed('company_Career_seed.csv')
