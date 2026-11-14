"""FanDuel team resolution and the matchup fallback.

FanDuel does not publish a team field -- the team is scraped out of a runner's
`secondaryLogo` URL. That logo is missing for some players (79 of 511 rows on
2026-11-08, and per-player rather than per-market, so it takes out every prop
for that player at once), which leaves `team` as UNK and strips the row of the
only thing the matcher's fuzzy tiers can verify against. These tests pin the
matchup fallback that fills the gap, and the ordering rule that keeps Charlotte
from being handed to Brooklyn.
"""

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scrapers.fetch_odds_fanduel import extract_team_name, extract_team_options
from utils.odds_csv import parse_team_options


class ExtractTeamNameTests(unittest.TestCase):
    def test_logo_url_resolves_to_tricode(self):
        self.assertEqual(
            extract_team_name("https://x/teams/cleveland_cavaliers.png"), "CLE"
        )
        self.assertEqual(extract_team_name("https://x/teams/cavaliers.png"), "CLE")

    def test_hornets_is_not_misread_as_nets(self):
        # TEAM_MAP iterates 'nets' ahead of 'hornets', so a substring-only pass
        # would give Charlotte's props to Brooklyn.
        self.assertEqual(
            extract_team_name("https://x/teams/charlotte_hornets.png"), "CHA"
        )
        self.assertEqual(extract_team_name("https://x/teams/brooklyn_nets.png"), "BKN")

    def test_missing_logo_is_unk(self):
        self.assertEqual(extract_team_name(""), "UNK")
        self.assertEqual(extract_team_name(None), "UNK")

    def test_unrecognizable_logo_is_unk(self):
        self.assertEqual(extract_team_name("https://x/teams/placeholder.png"), "UNK")


class ExtractTeamOptionsTests(unittest.TestCase):
    def test_matchup_label_yields_both_clubs(self):
        self.assertEqual(
            extract_team_options("Utah Jazz @ Minnesota Timberwolves"), ["UTA", "MIN"]
        )
        self.assertEqual(
            extract_team_options("Boston Celtics @ Milwaukee Bucks"), ["BOS", "MIL"]
        )

    def test_multi_word_and_numeric_nicknames_resolve(self):
        self.assertEqual(
            extract_team_options("Portland Trail Blazers @ Philadelphia 76ers"),
            ["POR", "PHI"],
        )

    def test_hornets_nets_matchup_keeps_both_clubs_distinct(self):
        self.assertEqual(
            extract_team_options("Charlotte Hornets @ Brooklyn Nets"), ["CHA", "BKN"]
        )

    def test_unparseable_label_yields_no_options(self):
        # Degrading to [] leaves the matcher exactly as unconstrained as it was
        # before the column existed, rather than inventing a team.
        self.assertEqual(extract_team_options(""), [])
        self.assertEqual(extract_team_options(None), [])
        self.assertEqual(extract_team_options("TBD @ TBD"), [])

    def test_one_sided_label_yields_the_one_club_it_names(self):
        self.assertEqual(extract_team_options("Denver Nuggets"), ["DEN"])


class ParseTeamOptionsTests(unittest.TestCase):
    """The column is read back both in-process and after a CSV round trip."""

    def test_native_list_passes_through(self):
        self.assertEqual(parse_team_options(["UTA", "MIN"]), ["UTA", "MIN"])

    def test_csv_repr_is_parsed(self):
        self.assertEqual(parse_team_options("['UTA', 'MIN']"), ["UTA", "MIN"])

    def test_empty_shapes_yield_empty_list(self):
        for raw in (None, "", "[]", [], float("nan")):
            self.assertEqual(parse_team_options(raw), [])

    def test_bare_tricode_is_one_option(self):
        self.assertEqual(parse_team_options("DEN"), ["DEN"])

    def test_malformed_text_degrades_to_empty(self):
        self.assertEqual(parse_team_options("['UTA', "), [])


if __name__ == "__main__":
    unittest.main()
