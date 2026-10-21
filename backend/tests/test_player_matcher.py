import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils.player_matcher import PlayerMatcher


def build_matcher(*players):
    """players: (player_id, name, team) tuples."""
    return PlayerMatcher([
        {"PLAYER_ID": player_id, "PLAYER_NAME": name, "TEAM_ABBREVIATION": team}
        for player_id, name, team in players
    ])


class NameNormalizationTests(unittest.TestCase):
    def test_exact_match(self):
        matcher = build_matcher((201939, "Stephen Curry", "GSW"))

        self.assertEqual(matcher.match_player("Stephen Curry", "GSW", []), 201939)

    def test_accents_and_punctuation_are_normalized(self):
        matcher = build_matcher((203999, "Nikola Jokić", "DEN"))

        self.assertEqual(matcher.match_player("Nikola Jokic", "DEN", []), 203999)

    def test_suffix_is_ignored_in_both_directions(self):
        matcher = build_matcher((1628991, "Jaren Jackson Jr.", "MEM"))

        self.assertEqual(matcher.match_player("Jaren Jackson", "MEM", []), 1628991)
        self.assertEqual(matcher.match_player("Jaren Jackson Jr", "MEM", []), 1628991)


class AliasDirectionTests(unittest.TestCase):
    """The alias table lists nickname pairs in both directions.

    Resolution must not depend on which spelling the NBA stats feed happens to
    treat as canonical, because substituting unconditionally can map an
    already-canonical name away from the roster -- and the fuzzy tier does not
    rescue it (fuzz.ratio("nicolas claxton", "nic claxton") is ~84.6, under the
    85 cutoff).
    """

    def test_short_canonical_roster_accepts_both_spellings(self):
        # Matches the 2025-26 feed, which spells this player "Nic Claxton".
        matcher = build_matcher((1629651, "Nic Claxton", "CHI"))

        self.assertEqual(matcher.match_player("Nic Claxton", "CHI", []), 1629651)
        self.assertEqual(matcher.match_player("Nicolas Claxton", "CHI", []), 1629651)

    def test_long_canonical_roster_accepts_both_spellings(self):
        # The opposite direction, which regressed before: the alias mapped the
        # canonical spelling away from the roster and nothing matched.
        matcher = build_matcher((1629651, "Nicolas Claxton", "BKN"))

        self.assertEqual(matcher.match_player("Nicolas Claxton", "BKN", []), 1629651)
        self.assertEqual(matcher.match_player("Nic Claxton", "BKN", []), 1629651)

    def test_cam_thomas_pair_resolves_both_ways(self):
        short_roster = build_matcher((1630560, "Cam Thomas", "BKN"))
        long_roster = build_matcher((1630560, "Cameron Thomas", "BKN"))

        self.assertEqual(short_roster.match_player("Cameron Thomas", "BKN", []), 1630560)
        self.assertEqual(long_roster.match_player("Cam Thomas", "BKN", []), 1630560)


class SameNameDisambiguationTests(unittest.TestCase):
    def test_exact_duplicate_names_are_split_by_team(self):
        matcher = build_matcher(
            (1631114, "Jalen Williams", "OKC"),
            (1630598, "Jalen Williams", "DET"),
        )

        self.assertEqual(matcher.match_player("Jalen Williams", "OKC", []), 1631114)
        self.assertEqual(matcher.match_player("Jalen Williams", "DET", []), 1630598)

    def test_team_options_can_disambiguate(self):
        matcher = build_matcher(
            (1631114, "Jalen Williams", "OKC"),
            (1630598, "Jalen Williams", "DET"),
        )

        self.assertEqual(
            matcher.match_player("Jalen Williams", "UNK", ["DET", "PHX"]),
            1630598,
        )


class FuzzyTierTeamGuardTests(unittest.TestCase):
    """Tier-2 fuzzy matching must not attach a prop to the wrong player.

    fuzz.ratio("jaden smith", "jalen smith") is ~90.9, so it clears the 85
    cutoff while still being a different person.
    """

    def test_fuzzy_match_is_rejected_when_team_disagrees(self):
        matcher = build_matcher((1630188, "Jalen Smith", "PHX"))

        self.assertIsNone(matcher.match_player("Jaden Smith", "LAL", []))

    def test_fuzzy_match_is_rejected_when_team_options_disagree(self):
        matcher = build_matcher((1630188, "Jalen Smith", "PHX"))

        self.assertIsNone(matcher.match_player("Jaden Smith", "UNK", ["LAL", "BOS"]))

    def test_fuzzy_match_is_accepted_when_team_agrees(self):
        matcher = build_matcher((1630188, "Jalen Smith", "PHX"))

        self.assertEqual(matcher.match_player("Jaden Smith", "PHX", []), 1630188)

    def test_fuzzy_match_is_kept_when_no_team_info_is_available(self):
        # Deliberate: with nothing to contradict the match, rejecting here would
        # silently drop legitimate props from rows that carry no team column.
        matcher = build_matcher((1630188, "Jalen Smith", "PHX"))

        self.assertEqual(matcher.match_player("Jaden Smith", "UNK", []), 1630188)
        self.assertEqual(matcher.match_player("Jaden Smith", None, None), 1630188)

    def test_fuzzy_match_with_duplicate_names_also_requires_team_agreement(self):
        # Routing duplicates through _disambiguate_by_team would fall back to
        # the first candidate and reintroduce the wrong-player bug.
        matcher = build_matcher(
            (1630188, "Jalen Smith", "PHX"),
            (1630189, "Jalen Smith", "DET"),
        )

        self.assertIsNone(matcher.match_player("Jaden Smith", "LAL", []))
        self.assertEqual(matcher.match_player("Jaden Smith", "DET", []), 1630189)

    def test_unrelated_name_does_not_match(self):
        matcher = build_matcher((1630188, "Jalen Smith", "PHX"))

        self.assertIsNone(matcher.match_player("Victor Wembanyama", "SAS", []))


if __name__ == "__main__":
    unittest.main()
