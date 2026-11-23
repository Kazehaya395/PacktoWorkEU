"""Offline controls for run 182452: user action, transport, retirement and scope."""
import csv
import json
from packtoworkeu.application.scan_scope import share_career_targets
from packtoworkeu.core.blocked_portals import _is_block_message, _detail_summary_is_wall, retire_closed_listings
from packtoworkeu.scanning.response_evidence import challenge_reason, listing_block_message
from packtoworkeu.scanning.detail_state import DetailRequestState
from packtoworkeu.db import database as db


def test_action_not_vendor_drives_admission():
    for body, action in [('Performing security verification. This website is verifying you are not a bot.',False), ('Check the box to verify you are human.', True)]:
        html = '<title>Just a moment...</title><h1>'+body+'</h1>'
        assert _is_block_message(listing_block_message(challenge_reason(html))) is action
        reason = challenge_reason(html,subject='detail')
        assert _detail_summary_is_wall('blocked access: 2 unresolved response(s); '+reason) is action
    assert not _is_block_message('blocked by bot protection: Cloudflare challenge page served instead of the job list')


def test_transport_outcomes_are_not_counted_twice():
    state = DetailRequestState()
    for url,reason in [('a','HTTP 429 access blocked (human verification not confirmed)'),('b','Cloudflare challenge page served instead of the job detail text; automatic verification pending (human action not inferred)')]:
        state.observe(url,kind='blocked',reason=reason)
    result = state.coverage('test',['a','b','c'])
    assert (result['observed'],result['rate_limited'],result['automatic_check'],result['blocked'],result['unattempted']) == (3,1,1,0,1)


def test_share_only_equivalent_selected_routes():
    a = dict(name='First',careers_url='https://example.org/Case',provider='auto',target_country='Germany',scope_policy='seed_url')
    rows=share_career_targets([a,dict(a,name='Alias'),dict(a,name='Global',target_country='Global',scope_policy='global'),dict(a,provider='other'),dict(a,careers_url=a['careers_url'].lower())])
    assert len(rows)==4 and json.loads(rows[0]['shared_seed_names'])==['First','Alias']
    assert 'shared_seed_names' not in a


def test_retirement_only_for_older_untouched_matching_closure(db_path,tmp_path):
    reason='TargetClosedError: Page.wait_for_timeout: Target page, context or browser has been closed'
    log=tmp_path/'log.csv'
    with log.open('w',newline='') as f:
        writer=csv.writer(f); writer.writerow(['Seed URL','Jobs Found'])
        for name,phase,scope,run in [('old','career','listing','20261119T170652'),('manual','assisted','listing','20261119T170652'),('detail','career','details','20261119T170652'),('same','career','listing','20261119T182452'),('active','career','listing','20261119T170652')]:
            url='https://example.org/'+name
            writer.writerow([url,74]);db.enqueue_blocked_portals(db_path,[dict(url=url,seed_name=name,reason=reason,access_scope=scope,phase=phase)],run_id=run)
    retire_closed_listings(db_path,'20261119T182452','career',log,[{'url':'https://example.org/active'}]);assert {r['seed_name'] for r in db.list_blocked_portals(db_path)}=={'manual','detail','same','active'}
