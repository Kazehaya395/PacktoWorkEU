"""Shared repeat-scan decisions: no silent TTL renewal or force-cache fallback."""
import hashlib
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from packtoworkeu.core.saved_details import SavedDetailReader
from packtoworkeu.tests.test_session_reuse_followup import saved,URL,TEXT


def test_unchanged_observation_reuses_original_capture_and_counts_once(saved):
    path,c=saved;reader=SavedDetailReader(path)
    reader.observe(URL,title='Platform Engineer')
    a=reader.read(URL);b=reader.read(URL)
    assert a['jd_captured_at']==b['jd_captured_at']
    assert reader.summary()['jobs_existing']==1
    assert reader.summary()['jd_reused']==1
    assert reader.summary()['jd_pending']==0


def test_force_refresh_rejects_old_saved_text_without_erasing_it(saved):
    path,c=saved;reader=SavedDetailReader(path,force_refresh=True)
    reader.observe(URL,title='Platform Engineer')
    assert reader.read(URL)is None
    assert c.execute('SELECT description FROM jobs').fetchone()[0]==TEXT
    assert reader.summary()['reasons']['forced']==1


def test_changed_title_is_detected_before_listing_upsert(saved):
    path,c=saved;reader=SavedDetailReader(path)
    reader.observe(URL,title='Senior Platform Engineer')
    c.execute("UPDATE jobs SET title='Senior Platform Engineer'");c.commit()
    assert reader.read(URL)is None
    assert reader.summary()['reasons']['changed']==1


def test_unchanged_or_changed_provider_revision_is_comparable(saved):
    path,c=saved;reader=SavedDetailReader(path)
    c.execute("UPDATE jobs SET support_evidence_type=support_evidence_type||?",('; listing_revision_sha256='+hashlib.sha256(b'v1').hexdigest(),));c.commit()
    reader.observe(URL,title='Platform Engineer',revision='v1')
    assert reader.read(URL)
    reader2=SavedDetailReader(path);reader2.observe(URL,title='Platform Engineer',revision='v2')
    assert reader2.read(URL)is None
    assert reader2.summary()['reasons']['changed']==1


def test_first_revision_requires_a_comparable_read_not_a_fabricated_change(saved):
    path,c=saved;reader=SavedDetailReader(path)
    reader.observe(URL,title='Platform Engineer',revision='v1')
    assert reader.read(URL)is None
    assert reader.summary()['reasons']['unread']==1


def test_rediscovery_does_not_renew_stale_capture(saved):
    path,c=saved;date=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    c.execute('UPDATE jobs SET support_evidence_type=?',('jd_read_status=readable; jd_sha256='+hashlib.sha256(TEXT.encode()).hexdigest()+'; jd_captured_at='+date,));c.commit()
    reader=SavedDetailReader(path);reader.observe(URL,title='Platform Engineer')
    assert reader.read(URL)is None and reader.summary()['reasons']['stale']==1
    assert date in c.execute('SELECT support_evidence_type FROM jobs').fetchone()[0]


def test_current_run_proven_read_can_be_shared_even_when_force_is_enabled(saved):
    path,c=saved;reader=SavedDetailReader(path,force_refresh=True)
    reader.observe(URL,title='Platform Engineer');assert reader.read(URL)is None
    stamp=datetime.now(timezone.utc).isoformat()
    c.execute('UPDATE jobs SET support_evidence_type=?',('jd_read_status=readable; jd_sha256='+hashlib.sha256(TEXT.encode()).hexdigest()+'; jd_captured_at='+stamp,));c.commit()
    assert reader.read(URL)
    assert reader.summary()['jd_fresh']==1 and reader.summary()['jd_reused']==0


def test_session_saved_cache_must_not_bypass_shared_force_policy(saved):
    from packtoworkeu.core.session_details import SessionDetailEnricher
    path,c=saved;obj=SessionDetailEnricher.__new__(SessionDetailEnricher)
    obj.conn=c;obj.scanner=SimpleNamespace(_saved_jd_reader=SavedDetailReader(path,force_refresh=True))
    assert obj._saved(URL)is None


def _proof_job(text,stamp):
    return dict(url=URL,title='Platform Engineer',company='Fixture',description=text,
        verified_active=1,is_expired=0,_description_authority='detail',_support_authoritative=True,
        support_evidence_type='jd_read_status=readable; jd_sha256='+hashlib.sha256(text.encode()).hexdigest()+'; jd_captured_at='+stamp)


def test_new_proven_description_can_replace_longer_old_description(tmp_path):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.persistence import upsert_job
    p=tmp_path/'refresh.db';db.initialize(p)
    with db.get_connection(p)as c:
        upsert_job(c,_proof_job(TEXT*3,(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()))
        upsert_job(c,_proof_job(TEXT,datetime.now(timezone.utc).isoformat()))
        assert c.execute('SELECT description FROM jobs').fetchone()[0]==TEXT


def test_older_proven_read_cannot_replace_newer_read(tmp_path):
    from packtoworkeu.db import database as db
    from packtoworkeu.core.persistence import upsert_job
    p=tmp_path/'refresh.db';db.initialize(p)
    fresh=_proof_job(TEXT,datetime.now(timezone.utc).isoformat())
    with db.get_connection(p)as c:
        upsert_job(c,fresh)
        upsert_job(c,_proof_job(TEXT*3,(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()))
        row=c.execute('SELECT description,support_evidence_type FROM jobs').fetchone()
        assert row[0]==TEXT
        assert fresh['support_evidence_type']in row[1]
