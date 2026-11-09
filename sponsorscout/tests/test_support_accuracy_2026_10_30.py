"""Approved A+B support accuracy fixes. Offline text/CSV/SQLite only."""
from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

import pytest

from sponsorscout.scanning.jd_support import JDSupportDetector

HP_DUTY = ('Manages all aspects of executive travel, including international trips, '
           'visa applications, and managing travel budgets, and addresses last-minute '
           'changes and emergencies.')
AIRBUS_DUTY = ('Forecast, plan ahead and manage all aspects of Employment Pass '
               'applications (Including Dependent Passes) and other work visa processes.')
DEFINITION = ('(Legally authorised = has citizenship or has been granted a valid visa '
              'or work permit.) Fluency in English.')
MICROSOFT_TEXT = DEFINITION + ' You must be legally authorised to work in Germany to be eligible for this role.'


@pytest.fixture
def detector():
    return JDSupportDetector()


@pytest.mark.parametrize('text', [
    'Relocation', 'Relocation -', 'Relocation –', 'Relocation —', 'Relocation --',
    '  Relocation -  ', '• Relocation -', 'Relocation:', 'Relocation: -',
    '<p>Relocation -</p>', '<li>Relocation —</li>',
])
def test_bare_relocation_is_not_a_benefit(detector, text):
    result = detector.detect(text)
    assert result['relocation']['verdict'] == 'Unknown'
    assert not result['relocation']['required']
    assert result['visa']['verdict'] == 'Unknown'
    assert not any(e[0] == 'Yes' for e in result['relocation']['evidence'])


@pytest.mark.parametrize('text', [
    'Relocation assistance', 'A relocation package', 'Relocation available',
    'Relocation package offered.', 'We offer relocation assistance.',
    'Relocation may be available on request.', 'Umzugskosten werden erstattet.',
    'Relocation -\nWe offer relocation assistance.',
    'We offer relocation assistance. Relocation -',
    '<p>Relocation -</p><p>We offer relocation assistance.</p>',
])
def test_real_relocation_offers_survive(detector, text):
    assert detector.detect(text)['relocation']['verdict'] == 'Yes'


@pytest.mark.parametrize('text, verdict', [
    ('Relocation: Yes', 'Yes'), ('Relocation: No', 'No'),
    ('Relocation: -', 'Unknown'), ('Relocation: N/A', 'No'),
    ('We offer relocation assistance. Relocation: -', 'Unknown'),
    ('Relocation -\nWe do not offer relocation assistance.', 'No'),
    ('We do not offer relocation assistance. Relocation -', 'No'),
])
def test_existing_fields_and_refusals_are_not_reinterpreted(detector, text, verdict):
    assert detector.detect(text)['relocation']['verdict'] == verdict


@pytest.mark.parametrize('text', [
    HP_DUTY, AIRBUS_DUTY, DEFINITION, MICROSOFT_TEXT,
    'You will manage visa applications for executives.',
    'Responsibilities: Manage work visa applications for employees.',
    '• Manages all visa applications for executive travel.',
    'Coordinate visa applications for international business trips.',
])
def test_duties_and_definitions_do_not_offer_sponsorship(detector, text):
    assert detector.detect(text)['visa']['verdict'] == 'Unknown'


@pytest.mark.parametrize('text', [
    'We will sponsor your visa application.',
    'We handle your visa application and pay the fees.',
    'We offer visa sponsorship for the HR Associate role.',
    'Visa sponsorship is available for this position.',
    'Wir bieten Visumsponsoring an.', 'Offriamo supporto per il visto.',
    HP_DUTY + ' We offer visa sponsorship for this role.',
    AIRBUS_DUTY + ' We offer visa sponsorship for this role.',
    DEFINITION + ' We offer visa sponsorship for this role.',
    'You will manage visa applications for executives, and we offer visa sponsorship for this role.',
    'You will manage visa applications for executives and visa sponsorship is available for this role.',
    'Visa sponsorship is available for this role, and you will manage visa applications for executives.',
    'We will sponsor your work visa and you will manage visa applications for executives.',
    'Manage visa applications for executives. Visa Sponsorship: Yes',
    'You will manage visa applications and receive visa sponsorship.',
    'You will manage visa applications, with visa sponsorship provided for this role.',
    'You will manage visa applications because we offer visa sponsorship for this role.',
    'You will manage visa applications, and you are eligible for visa sponsorship.',
    'You will manage visa applications for executives, and the company will sponsor your visa.',

])
def test_independent_candidate_offer_is_never_suppressed(detector, text):
    result = detector.detect(text)['visa']
    assert result['verdict'] == 'Yes'
    assert any(e[0] == 'Yes' for e in result['evidence'])
    assert not any('Manages all aspects of executive travel' in e[3]
                   or 'has citizenship or has been granted' in e[3]
                   for e in result['evidence'] if e[0] == 'Yes')


@pytest.mark.parametrize('text', [
    HP_DUTY + ' We do not offer visa sponsorship.',
    AIRBUS_DUTY + ' We do not offer visa sponsorship.',
    DEFINITION + ' We do not offer visa sponsorship.',
    'You will manage visa applications for executives, and we do not offer visa sponsorship.',
])
def test_independent_refusal_survives_non_offer_prose(detector, text):
    assert detector.detect(text)['visa']['verdict'] == 'No'


@pytest.mark.parametrize('text, visa, relocation, required', [
    ('Applicants must already hold a valid work permit.', 'No', 'Unknown', False),
    ('You must be legally authorised to work in Germany to be eligible for this role.', 'Unknown', 'Unknown', False),
    # Approved 2026-11-03: optional mobility is not employer support denial.
    ('You are based in Vienna or open to relocating.', 'Unknown', 'Unknown', False),
    ('Visa sponsorship is not guaranteed.', 'Unknown', 'Unknown', False),
    ('Sponsorship may be available on a case-by-case basis.', 'Yes', 'Unknown', False),
    ('Do you require visa sponsorship?', 'Unknown', 'Unknown', False),
])
def test_eligibility_policies_and_approved_mobility_semantics(detector, text, visa, relocation, required):
    result = detector.detect(text)
    assert result['visa']['verdict'] == visa
    assert result['relocation']['verdict'] == relocation
    assert result['relocation']['required'] is required


@pytest.mark.parametrize('text', [
    'Relocation -', HP_DUTY, AIRBUS_DUTY, MICROSOFT_TEXT,
    'We offer visa sponsorship and relocation support.',
    'We do not offer visa sponsorship. No relocation assistance is available.',
])
def test_existing_shared_consumers_csv_and_sqlite_agree(text):
    from sponsorscout.scanning.ats.ats_scanner import ATSScanner
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
    from sponsorscout.core.support_application import apply_support_to_job
    from sponsorscout.core.persistence import upsert_job

    career = CareerPortalScanner(input_csv='__unused__.csv', skip_preflight=True)
    ats = ATSScanner(skip_preflight=True)
    url = 'https://fixture.invalid/jobs/12345-engineer'
    rec = {'Company Name': 'Fixture', 'Job Title': 'Backend Engineer', 'Job URL': url,
           'Visa Sponsorship': 'Unknown', 'Relocation Support': 'Unknown',
           'EU Blue Card': 'Unknown'}
    career._apply_support_from_text(rec, text, url)
    result = ats.classify_support(text, title='Backend Engineer', company='Fixture')
    assert (rec['Visa Sponsorship'], rec['Relocation Support']) == result[1:3]

    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rec))
    writer.writeheader(); writer.writerow(rec); stream.seek(0)
    assert next(csv.DictReader(stream))['Visa Sponsorship'] == result[1]

    job = apply_support_to_job(career, {
        'company': 'Fixture', 'title': 'Backend Engineer', 'url': url,
        'country': 'Germany', 'location': 'Berlin, Germany', 'country_source': 'job_location',
        'source_type': 'recruiter', 'ats_source': 'assisted_browser'}, text)
    stored = {'Yes': 'Y', 'No': 'N', 'Unknown': 'Unknown'}
    assert (job['visa_sponsorship'], job['relocation_support']) == (
        stored[result[1]], stored[result[2]])
    if result[1] == result[2] == 'Unknown':
        assert not job['support_evidence']
        assert job['support_confidence'] == 0

    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[1] / 'db' / 'schema.sql'
    connection.executescript(schema.read_text())
    try:
        upsert_job(connection, job)
        row = connection.execute('SELECT visa_sponsorship,relocation_support FROM jobs').fetchone()
        assert (row['visa_sponsorship'], row['relocation_support']) == (
            job['visa_sponsorship'], job['relocation_support'])
    finally:
        connection.close()
