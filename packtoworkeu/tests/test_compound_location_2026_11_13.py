"""Compound place names (2026-11-13): 72/144 assisted McDonald's Italia
rows lost Location AND Country because the place existed only inside the
job title as a compound ("Roma Appia", "Sesto San Giovanni Drive") and
country_from_location only matched whole strings. Fix 7c tries venue-
stripped town prefixes resolving to a UNIQUE country; the assisted
harvest also keeps the title's observed place for display (FIX P40
parity) instead of discarding it as Unknown.
"""
from packtoworkeu.core.location_country import country_from_location
from packtoworkeu.core import blocked_portals as bp


def test_mcdonalds_compound_titles_resolve_to_italy():
    for loc in ("Roma Appia", "Padova Guido Reni", "Milano Malpensa",
                "Sesto San Giovanni Drive", "Orio al Serio Aeroporto",
                "Torino Via Pio VII", "Viareggio Drive",
                "Roma Piazzale Flaminio", "Milano Stazione Centrale Int",
                "Aprilia Mascagni Drive", "Serravalle Scrivia Drive"):
        assert country_from_location(loc) == "Italy", loc


def test_exact_behaviour_unchanged():
    assert country_from_location("Desio") == "Italy"
    assert country_from_location("Castelfranco Emilia") == "Italy"
    assert country_from_location("Berlin") == "Germany"
    assert country_from_location("New York") == "United States"
    assert country_from_location("Milan, MI") == "Italy"
    assert country_from_location("Remote - Germany") == "Germany"


def test_unresolvable_stays_empty():
    assert country_from_location("Guido Reni") == ""
    assert country_from_location("Nowhereville Xyz") == ""
    assert country_from_location("") == ""


class _Scanner:
    def _sanitize_job_location(self, line, seed):
        return "Unknown"

    def _location_from_title_text(self, title):
        return "Roma Appia" if "Roma Appia" in title else ""


def test_assisted_card_location_falls_back_to_title():
    got = bp._observed_card_location(
        _Scanner(), "Addetto/a Ristorazione - Crew - Roma Appia",
        "Addetto/a Ristorazione - Crew - Roma Appia", "McDonald's Italia")
    assert got == "Roma Appia"


def test_assisted_card_location_unknown_when_title_has_no_place():
    got = bp._observed_card_location(
        _Scanner(), "Crew Member", "Crew Member", "McDonald's Italia")
    assert got == "Unknown"
