from packtoworkeu.db import database as db
from packtoworkeu.application.scan_scope import retry_plan,save_plan

CENSUS='AMS search: 431 distinct posting IDs on 36 page(s)'
COVER='JD coverage: observed=431; readable=428; thin=1; empty=0; failed=2; blocked=0; unattempted=0'
EVID='response evidence: url=https://jobs.ams.at/public/emps/jobs/example; kind=failed; reason=AMS lists the ID but returns no posting description and displays notFound'
DIAG=' | '.join((CENSUS,COVER,EVID,'JD coverage incomplete: 3 observed posting(s) without a usable full JD'))

def test_completed_listing_is_review_not_retry_and_mixed_route_is_retryable(tmp_path):
    path=str(tmp_path/'data.db');db.initialize(path)
    rows=[dict(name=n,careers_url='https://example.test/'+n,target_country='Global') for n in ('ams','broken')]
    db.start_scan_run(path,'run','quick',0,2);save_plan(path,'run',dict(version=1,method='quick',ats=[],career=rows))
    def log(r,diag,found=431):
        db.record_scan_log_rows(path,'run','career',[dict(seed_name=r['name'],seed_url=r['careers_url'],target_country='Global',status='partial',jobs_found=found,diagnostics=diag)])
    log(rows[0],DIAG);db.finish_scan_run(path,'run',status='partial');reviews={}
    assert retry_plan(path,'run',reviews=reviews) is None
    assert list(reviews.values())[0][1:4]==(431,428,{'thin':1,'failed':2})
    assert db.get_scan_log(path,'run')[0]['status']=='partial'
    log(rows[1],'listing incomplete: no next page');assert retry_plan(path,'run')['career']==rows[1:]
    for diag in (DIAG+' | listing incomplete: page cap reached',DIAG.replace('notFound','TimeoutError'),DIAG.replace('431 distinct','500 distinct'),COVER):
        log(rows[0],diag);assert retry_plan(path,'run',reviews={})['career']==rows
