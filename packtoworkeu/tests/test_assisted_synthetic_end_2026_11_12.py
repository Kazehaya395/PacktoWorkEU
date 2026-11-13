"""Assisted single-page completion (2026-11-12): McDonald's Italia could
never complete — both synthesised probes replayed a fully accounted
listing with no Next and no stated total, the harvest recorded
`unadvertised_synthetic_replay` end evidence, but the completion gate
only accepted older stop phrases, so every re-run re-crawled the listing
and ended "incomplete". The gate now honours that evidence; the guards
in _unadvertised_probe_replayed (which must pass before the evidence is
recorded at all) are pinned here.
"""
from packtoworkeu.core import blocked_portals as bp


def _evidence(**over):
    ev = {"pagination_text": "", "forward_page_links": [],
          "stated_total": None}
    ev.update(over)
    return ev


def _call(**over):
    kw = dict(synthetic=True, replayed=True, two_index_guesses=True,
              census_complete=True, forward_control=False,
              last_page_evidence=_evidence(), current_total=None,
              ingested=144, unmet_total_seen=False,
              evidence_readable=True, probe_controls_absent=True)
    kw.update(over)
    return bp._unadvertised_probe_replayed(**kw)


def test_mcdonalds_shape_certifies_end():
    assert _call() is True


def test_forward_link_in_evidence_blocks_certification():
    assert _call(last_page_evidence=_evidence(
        forward_page_links=["https://x.example/jobs?p=2"])) is False


def test_pagination_text_blocks_certification():
    assert _call(last_page_evidence=_evidence(
        pagination_text="1 2 3")) is False


def test_unmet_stated_total_blocks_certification():
    assert _call(last_page_evidence=_evidence(stated_total=300)) is False
    assert _call(unmet_total_seen=True) is False


def test_incomplete_census_blocks_certification():
    assert _call(census_complete=False) is False


def test_single_guess_does_not_certify():
    # ?p=1 alone may itself be page 1 on a one-indexed board.
    assert _call(two_index_guesses=False) is False


def test_completion_gate_honours_synthetic_end_evidence():
    src = open(bp.__file__, encoding="utf-8").read()
    assert "_end_evidence.get('kind') == 'unadvertised_synthetic_replay'" in src
    assert "or _synthetic_end" in src
    # Stated-total guard retained (None = board never advertised one).
    assert "in (None,ingested_count)" in src
