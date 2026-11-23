"""The 92 settled tasks in 233237 are not 92 successful portals."""
import csv
from packtoworkeu.db import database as db
from packtoworkeu.application.scan_scope import save_plan, retry_plan
from packtoworkeu.scanning.pipeline import _ingest_scan_log


def test_finished_campaign_retry_is_exact_subset_and_old_checkpoint_survives(tmp_path):
    path=str(tmp_path/'test.db');db.initialize(path)
    route=lambda i: {'name':'Portal', 'target_country':'Global', 'careers_url':f'https://example.test/jobs/{i}'}
    rows=[route(i) for i in range(92)]
    plan=dict(version=1,method='quick',refresh_descriptions=True,ats=[],career=rows)
    db.start_scan_run(path,'old','quick',0,1);save_plan(path,'old',dict(plan,career=rows[:1]));db.finish_scan_run(path,'old',status='cancelled')
    db.start_scan_run(path,'finished','quick',0,92);save_plan(path,'finished',plan)
    csvpath=tmp_path/'scan.csv';statuses=['ok']*9+['empty']*6+['unverified_empty']*17+['partial']*52+['error']*8
    with csvpath.open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['Status','Seed Name','Seed URL','Target Country']);w.writerows((status,r['name'],r['careers_url'],'Global') for r,status in zip(rows,statuses))
    _,counts=_ingest_scan_log(path,csvpath,'finished','career');assert [counts[k] for k in ('ok','empty','unverified_empty','partial','error')]==[9,6,17,52,8]
    db.finish_scan_run(path,'finished',status='partial',targets_ok=9,targets_unverified_empty=17)
    assert db.get_scan_run(path,'finished')['targets_unverified_empty']==17
    assert db.get_resumable_scan(path)['run_id']=='old'
    assert retry_plan(path,'finished')==dict(plan,career=rows[15:],retry_of='finished')
    assert retry_plan(path,'old') is None
