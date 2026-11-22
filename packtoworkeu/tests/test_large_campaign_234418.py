import csv,json,threading
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from packtoworkeu.core.saved_details import SavedDetailReader,remember_capture,capture_proof
from packtoworkeu.core.run_ledger import RunLedger
from packtoworkeu.scanning.ats.verified_routes import ROUTES,verified_target
from packtoworkeu.scanning.detail_backlog import load_pending,cursor_io
from packtoworkeu.scanning.career.large_boards import lux_listing,LUX,lidl_jobs,proven_detail


def test_actual_campaign_scale_counts_and_proofs_after_20000(tmp_path):
    r=SavedDetailReader(tmp_path/'missing');r._lookup=lambda _:None
    s=SimpleNamespace(_jd_lock=threading.Lock());text='Original description. '*20
    for i in range(25401):
        u=f'https://test.example/jobs/{i}';r.observe(u,title='A');remember_capture(s,u,text)
    assert r.summary()['tracked']==25401 and r.summary()['tracking_complete']
    assert capture_proof(s,u,text) and not capture_proof(s,u,text+'changed')
    m=RunLedger(1);m['a']='a';m['b']='b';m['a']='updated';assert (m.pop('a'),len(m))==('updated',1)


def test_exact_legacy_seed_triples_only():
    for (u,p,b),fix in ROUTES.items():
        row={'url':u,'ats_type':p,'board_slug':b};out=verified_target(row)
        assert (out['ats_type'],out['board_slug'])==fix and row['board_slug']==b
        custom=dict(row,board_slug='my-edit');assert verified_target(custom) is custom
    with pytest.raises(ValueError,match='identity conflict'):
        verified_target(dict(url='https://itonics-gmbh.jobs.personio.de/?language=en',name='IT-tronics (IT Infrastructure)'))


def test_backlog_must_match_run_seed_country_and_status(tmp_path):
    run='20261120T234418';seed='https://test.example/jobs';targets=[dict(name='A',careers_url=seed,target_country='Global')]
    with (tmp_path/(run+'_career_jobs.csv')).open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['Seed Name','Seed URL','Target Country','Run ID','Job URL','JD Read Status']);w.writerows([('A',seed,'Global',run,'u','unattempted'),('A',seed,'Global',run,'other','readable'),('A',seed,'Italy',run,'wrong','unattempted'),('A',seed,'Global','older','old','unattempted')]);
    assert list(load_pending(tmp_path,run,targets).values())==[{'u'}]
    assert not load_pending(tmp_path,'../'+run,targets)


def _page(n,total):
    return (f'<span class="paginationLabel">Results <b>{n} – {n}</b> of <b>{total}</b></span>'
        +f'<tr class="data-row"><td><a class="jobTitle-link" href="/job/Chef/{n}/">Chef</a></td><td class="colLocation">Rome, IT</td></tr>'
        +f'<a href="?startrow={total-1}">»</a><a href="?startrow={n}">{n+1}</a>')

def test_range_checkpoint_revalidates_prefix_and_rejects_cross_origin_cursor(tmp_path):
    seed=f'https://{LUX}/search/';calls=[]
    def fetch(u):
        calls.append(u);return _page(int(u.split('=')[-1])+1,5)
    base=(seed,_page(1,5),fetch,lambda:None)
    a=cursor_io(tmp_path/'out.csv','20261120T111111',None,seed)
    part,diag=lux_listing(*base,cap=3,**a);assert diag.complete is False and len(part)==3
    b=cursor_io(tmp_path/'out.csv','20261120T222222','20261120T111111',seed);assert b['cursor']['next'].endswith('startrow=3');calls.clear()
    rows,diag=lux_listing(*base,**b);assert len(rows)==5 and diag.complete;assert [x.split('=')[-1] for x in calls]==['3','4','1','2']
    b['cursor']['next']='https://other.example/search/?startrow=3';calls.clear();lux_listing(*base,**b);assert calls[0].endswith('startrow=1')


def test_page_identity_and_provenance_fail_closed():
    u='https://www.hamilton-barnes.com/candidates/job/test/'
    h=f'<link rel="canonical" href="{u}"><script type="application/ld+json">'+json.dumps({'@type':'JobPosting','description':'Duties and requirements. '*40})+'</script>'
    assert proven_detail(u,u,h);assert not proven_detail(u,u+'other',h);assert not proven_detail(u,u,h+h);assert not proven_detail(u,u,h.replace('rel="canonical"','rel="other"'))


def test_lidl_does_not_accept_certificate_of_completion_from_wrong_id():
    import time
    d=dict(meta=dict(page=1,totalCount=1),jobs=[dict(requisitionId='42',jobDetailUrl='https://lavoro.lidl.it/jobs/title-43',title='Title')]);rows,diag=lidl_jobs(lambda u:json.dumps(d),lambda:None,lambda s,limit:s,time.monotonic()+10);assert not rows and 'listing incomplete' in diag
