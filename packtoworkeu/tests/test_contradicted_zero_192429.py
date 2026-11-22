from packtoworkeu.scanning.common import completion_status
from packtoworkeu.application.scan_scope import retry_plan, save_plan
from packtoworkeu.db import database as db

CONFLICT='pagination incomplete: stated total 29 exceeds harvested 0; job count is a lower bound'

def test_conflict_outweighs_zero_but_not_cancellation():
    assert completion_status(0,diagnostics=['verified empty: page states no vacancies',CONFLICT])=='unverified_empty'
    assert completion_status(0,diagnostics=['verified empty: page states no vacancies'])=='empty'
    assert completion_status(0,diagnostics=['pagination listing completeness unverified'])=='unverified_empty'
    assert completion_status(0,'ScanCancelled: stopped',[CONFLICT])=='cancelled'

def test_legacy_conflict_is_retryable_without_mutating_history(tmp_path):
    path=str(tmp_path/'db');db.initialize(path)
    rows=[dict(name=n,careers_url='https://example.test/'+n,target_country='Global') for n in ('conflict','actualzero')]
    db.start_scan_run(path,'run','quick',0,2);save_plan(path,'run',dict(version=1,method='quick',ats=[],career=rows))
    for r,diag in zip(rows,[CONFLICT,'verified empty: page states no vacancies']):
        db.record_scan_log_rows(path,'run','career',[dict(seed_name=r['name'],seed_url=r['careers_url'],target_country='Global',status='empty',diagnostics=diag)])
    db.finish_scan_run(path,'run',status='completed'); reasons={}
    assert retry_plan(path,'run',reasons=reasons)['career']==rows[:1]
    assert all(r['status']=='empty' for r in db.get_scan_log(path,'run'))
