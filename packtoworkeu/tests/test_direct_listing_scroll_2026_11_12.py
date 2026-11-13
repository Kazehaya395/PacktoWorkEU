"""Direct-listing lazy scroll (2026-11-12): McDonald's Italia yielded 14
rows in the regular crawl vs 146 in the assisted harvest because a direct
listing with no pager/load-more control was declared complete after the
first screen. The branch now runs one bounded scroll pass and re-extracts
before completing. The branch lives inside crawl_target, so this pins the
wiring (the scroll helper's own cancel behaviour is covered in
test_stop_latency_2026_11_12.py).
"""
from packtoworkeu.scanning.career import career_scanner as cs


def test_direct_listing_branch_scrolls_before_completing():
    src = open(cs.__file__, encoding="utf-8").read()
    branch = src[src.index("elif not _needs_direct_pagination:"):]
    branch = branch[:1200]
    assert "aggressive_infinite_scroll" in branch
    assert "extract_visible_jobs" in branch
    assert "scroll pass" in branch
    # Old give-up message must not survive as the branch outcome.
    assert 'diagnostics.append("direct listing complete' not in src
