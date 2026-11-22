import csv
import hashlib
import json

from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
from packtoworkeu.scanning.pipeline import _ingest_output_csv
from packtoworkeu.core.saved_details import SavedDetailReader, capture_proof
from packtoworkeu.db import database as db
from packtoworkeu.insights.queries import backfill_existing_job_terms

URL='https://ebza.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/6102'


def setup_scanner(tmp_path,monkeypatch,item):
    seed=tmp_path/'seed.csv';seed.write_text('name,careers_url\n');s=CareerPortalScanner(input_csv=str(seed),skip_preflight=True)
    def fetch(api):
        assert 'Id=6102,siteNumber=CX_1001' in api
        return json.dumps({'items':[item]})
    monkeypatch.setattr(s,'_cxs_fetch',fetch)
    return s


def test_full_oracle_to_sqlite_and_insights(tmp_path,monkeypatch):
    short='A short overview. '*19
    item={'Id':6102,'ShortDescriptionStr':short,'ExternalDescriptionStr':'<p>'+'Design systems and work with clients. '*135+'</p>', 'ExternalQualificationsStr':'Required skills: Python.'}
    s=setup_scanner(tmp_path,monkeypatch,item);path=str(tmp_path/'data.db');db.initialize(path)
    s._saved_jd_reader=SavedDetailReader(path);s._saved_jd_reader.observe(URL,title='Strategy Manager')
    text=s._fetch_jd_text(URL,allow_browser=False)[0];assert len(text)>4800 and text.endswith('Python.')
    row={'Job URL':URL,'Job Title':'Strategy Manager','Company Name':'Example','JD Read Status':'readable'};out=tmp_path/'rows.csv'
    with out.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=row);w.writeheader();w.writerow(row)
    # Start with the historical failure shape: a long-enough teaser, no proof.
    first,_=_ingest_output_csv(path,out,'previous','direct',set(),jd_lookup=lambda _:short.strip());assert first==1
    n,_=_ingest_output_csv(path,out,'fixture','direct',set(),jd_lookup=s.get_captured_jd,jd_proof_lookup=lambda u,t:capture_proof(s,u,t),refresh_reader=s._saved_jd_reader);assert n==1
    assert s._saved_jd_reader.summary()['jd_fresh']==1
    backfill_existing_job_terms(path,force_all=True);con=db.get_connection(path)
    try:
        stored,marker=con.execute('SELECT description,support_evidence_type FROM jobs WHERE url=?',(URL,)).fetchone()
        assert stored==text and 'jd_sha256='+hashlib.sha256(stored.encode()).hexdigest() in marker and 'jd_captured_at=' in marker
        assert con.execute("SELECT count(*) FROM job_terms WHERE kind='skill' AND lower(term)='python'").fetchone()[0]==1
    finally:con.close()


def test_teaser_never_receives_full_capture(tmp_path,monkeypatch):
    short='Summary only. '*25;s=setup_scanner(tmp_path,monkeypatch,{'Id':6102,'ShortDescriptionStr':short});assert not s._fetch_jd_text(URL)[0];assert not s.get_captured_jd(URL);assert s._captured_jds[URL]==short.strip();assert not capture_proof(s,URL,short);assert s._detail_state.outcomes[URL]['kind']=='thin'


def test_mismatched_requisition_cannot_supply_text(tmp_path,monkeypatch):
    s=setup_scanner(tmp_path,monkeypatch,{'Id':999,'ExternalDescriptionStr':'Wrong job. '*400});assert not s._fetch_jd_text(URL)[0];assert not s.get_captured_jd(URL)
