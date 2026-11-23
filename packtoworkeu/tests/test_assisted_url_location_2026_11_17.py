"""Fix K (2026-11-17): Assisted harvest rows get own-URL locations.

The AERQ assisted run 20261117T214536 saved 204 of 221 rows with
Location Unknown although the posting's own URL named the place
("/job/Tianjin-Area/...", "/job/Grand-Prairie-TX/..."). The regular
scanner has read those slots since G1/G2; the assisted harvest in
blocked_portals never did. _assisted_url_location applies the same
passes at save time, marked country_source='url' by the caller.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packtoworkeu.core.blocked_portals import _assisted_url_location


def _scanner(tmp_path):
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    seed = tmp_path / "seed.csv"
    seed.write_text("company,url\nAERQ,https://ag.wd3.myworkdayjobs.com/Airbus\n",
                    encoding="utf-8")
    return CareerPortalScanner(input_csv=str(seed), skip_preflight=True)


def test_slug_place_recovered_when_card_has_none(tmp_path):
    s = _scanner(tmp_path)
    loc, country = _assisted_url_location(
        s, "https://ag.wd3.myworkdayjobs.com/en-US/Airbus/job/Tianjin-Area/Mechanic_JR1",
        "Unknown", "AERQ GmbH")
    assert (loc, country) == ("Tianjin, China", "China")
    loc, country = _assisted_url_location(
        s, "https://ag.wd3.myworkdayjobs.com/en-US/Airbus/job/Grand-Prairie-TX/Inspector_JR2",
        "Unknown", "AERQ GmbH")
    assert (loc, country) == ("Grand Prairie, United States", "United States")


def test_workday_alpha2_slot_recovered(tmp_path):
    s = _scanner(tmp_path)
    loc, country = _assisted_url_location(
        s, "https://x.wd5.myworkdayjobs.com/A/job/US-CA-Santa-Clara/Eng_1",
        "Unknown", "Agilent")
    assert (loc, country) == ("Santa Clara, United States", "United States")


def test_observed_place_is_never_replaced(tmp_path):
    s = _scanner(tmp_path)
    # Card place with a resolvable country: untouched, no URL country.
    loc, country = _assisted_url_location(
        s, "https://x.wd5.myworkdayjobs.com/A/job/US-CA-Santa-Clara/Eng_1",
        "Milan, MI", "Agilent")
    assert (loc, country) == ("Milan, MI", "")
    # Card place without a resolvable country: country appended only.
    loc, country = _assisted_url_location(
        s, "https://x.wd5.myworkdayjobs.com/A/job/US-CA-Santa-Clara/Eng_1",
        "Santa Clara", "Agilent")
    assert (loc, country) == ("Santa Clara, United States", "United States")


def test_url_without_place_changes_nothing(tmp_path):
    s = _scanner(tmp_path)
    loc, country = _assisted_url_location(
        s, "https://example.com/careers/12345", "Unknown", "Example")
    assert (loc, country) == ("Unknown", "")
