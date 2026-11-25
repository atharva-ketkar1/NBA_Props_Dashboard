import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import os as _hc_os, sys as _hc_sys
_hc_sys.path.insert(0, _hc_os.path.join(_hc_os.path.dirname(_hc_os.path.abspath(__file__)), ".."))
from utils.http_client import proxied_get  # noqa: E402  (needs the path insert above)

try:
    from .season_type import resolve_season, resolve_season_type
except ImportError:
    from season_type import resolve_season, resolve_season_type

URL = "https://stats.nba.com/stats/leaguedashteamstats"

HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
    "dnt": "1",
    "host": "stats.nba.com",
    "origin": "https://www.nba.com",
    "referer": "https://www.nba.com/",
    "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
}

# Shared across the four calls below; only MeasureType/PlayerPosition change.
BASE_PARAMS = dict(
    PerMode='PerGame', PlusMinus='N', PaceAdjust='N', Rank='N',
    LeagueID='00', PORound=0, Outcome='', Location='', Month=0,
    SeasonSegment='', DateFrom='', DateTo='', OpponentTeamID=0,
    VsConference='', VsDivision='', TeamID=0, Conference='', Division='',
    GameSegment='', Period=0, ShotClockRange='', LastNGames=0, GameScope='',
    PlayerExperience='', StarterBench='',
)


def _fetch(measure_type, player_position, season=None):
    if season is None:
        season = resolve_season()
    params = dict(BASE_PARAMS, MeasureType=measure_type, PlayerPosition=player_position,
                  Season=season, SeasonType=resolve_season_type())
    response = proxied_get(URL, headers=HEADERS, params=params, label="opp-team-advanced")
    data = response.json()
    result_set = data["resultSets"][0]
    columns = result_set["headers"]
    rows = result_set["rowSet"]
    return [dict(zip(columns, row)) for row in rows]


def get_opp_team_advanced_data():
    """Per-team pace/defensive-rating rank, plus points-allowed-to-position rank.

    Rank 1 is always the toughest matchup for the stat in question: fastest pace,
    stingiest defensive rating, fewest points allowed to that position -- NBA.com's
    own `*_RANK` columns already encode that direction, so no re-ranking is done
    here, unlike opp_def_zones.py which has to derive ranks itself from raw makes.
    """
    results = {}

    for row in _fetch('Advanced', ''):
        team_name = row.get('TEAM_NAME')
        if not team_name:
            continue
        results[team_name] = {
            'pace_rank': row.get('PACE_RANK'),
            'def_rating_rank': row.get('DEF_RATING_RANK'),
            'pos_pts_rank': {},
            'fg3a_allowed_rank': None,
            'fta_allowed_rank': None,
        }

    for row in _fetch('Opponent', ''):
        team_name = row.get('TEAM_NAME')
        if not team_name:
            continue
        results.setdefault(team_name, {
            'pace_rank': None, 'def_rating_rank': None, 'pos_pts_rank': {},
            'fg3a_allowed_rank': None, 'fta_allowed_rank': None,
        })
        results[team_name]['fg3a_allowed_rank'] = row.get('OPP_FG3A_RANK')
        results[team_name]['fta_allowed_rank'] = row.get('OPP_FTA_RANK')

    for position in ('G', 'F', 'C'):
        try:
            for row in _fetch('Opponent', position):
                team_name = row.get('TEAM_NAME')
                if not team_name:
                    continue
                results.setdefault(team_name, {'pace_rank': None, 'def_rating_rank': None, 'pos_pts_rank': {}})
                results[team_name]['pos_pts_rank'][position] = row.get('OPP_PTS_RANK')
        except Exception as e:
            print(f"Error fetching opponent points allowed for position {position}: {e}")

    return results


def main():
    data = get_opp_team_advanced_data()
    print(f"Fetched team advanced/positional defense for {len(data)} teams.")
    if "Houston Rockets" in data:
        print("Houston Rockets:", data["Houston Rockets"])


if __name__ == "__main__":
    main()
