"""Direct-listing census diagnostics (2026-11-12): McDonald's Italia
regular crawl extracted 14 rows vs 146 assisted; a window scroll pass
changed nothing, so instead of guessing the lazy-load mechanism the
direct-listing branch now logs a DOM census (job-like anchors total /
distinct / visible, inner scrollable containers) after the scroll pass.
"""
from packtoworkeu.scanning.career import career_scanner as cs


class _Target:
    def __init__(self, out=None, boom=False):
        self.out, self.boom, self.js = out, boom, None

    def evaluate(self, js):
        self.js = js
        if self.boom:
            raise RuntimeError("evaluate failed")
        return self.out


def test_census_returns_dom_evidence():
    t = _Target({"anchors": 160, "job_like": 146, "job_like_distinct": 146,
                 "job_like_visible": 14, "inner_scrollers": [],
                 "doc_sh": 4000, "win_ih": 800})
    out = cs.CareerPortalScanner._direct_listing_census(t)
    assert out["job_like_distinct"] == 146
    assert "querySelectorAll('a[href]')" in t.js
    assert "overflowY" in t.js  # inner-scroller detection present


def test_census_failure_is_empty_not_fatal():
    assert cs.CareerPortalScanner._direct_listing_census(_Target(boom=True)) == {}


def test_census_wired_into_direct_listing_branch():
    src = open(cs.__file__, encoding="utf-8").read()
    i = src.index("direct listing: no pagination/load-more")
    assert "_direct_listing_census(target)" in src[i:i + 1500]
    assert "direct listing census:" in src
