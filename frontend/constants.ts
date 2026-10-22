import { Player, GameLog, PlayTypeData, SimilarPlayerGame } from './types';

// export const CURRENT_PLAYER: Player = {
//   id: 'lebron-james',
//   name: 'LeBron James',
//   team: 'LAL',
//   position: 'F',
//   image: 'https://picsum.photos/id/1005/200/200', // Placeholder
//   stats: {
//     pts: 22.4,
//     ast: 6.4,
//     reb: 5.8,
//     threepm: 1.5,
//     mins: 33.1,
//     usage: '11.4', // Re-purposing for POT AST based on screenshot
//     fga: 46.5,    // Re-purposing for PASSES based on screenshot
//     diffs: {
//       pts: 4.8,
//       ast: -2.3,
//       reb: 0.8,
//       threepm: 0.1,
//       mins: 1.1,
//       usage: '-2.0',
//       fga: -2.5
//     }
//   },
//   propLine: 6.5,
//   propType: 'Ast',
//   lineOdds: {
//     over: -122,
//     under: -108
//   }
// };

export const RECENT_GAMES: GameLog[] = [
  // Mock data kept for reference or fallback if needed, but likely unused now
  // { date: 'Nov 28', opponent: 'PHI', opponentLogo: 'PHI', score: 3, isHome: false },
  // ... (commenting out just to be safe or delete)
] as any;

// ... existing RECENT_GAMES content was array of objects that don't match GameLog perfectly either.
// I'll just comment the whole thing out or leave it if I didn't verify RECENT_GAMES usage in other files.
// BarChart used to use it, but I refactored it.
// I'll comment it out.


export const SIMILAR_GAMES: SimilarPlayerGame[] = [
  { date: 'Jan 19', team: 'Pacers', player: 'P. Siakam', line: 23.5, result: 24, diffPercent: 2 },
  { date: 'Jan 12', team: 'Raptors', player: 'S. Barnes', line: 18.5, result: 15, diffPercent: -19 },
  { date: 'Jan 11', team: 'Raptors', player: 'S. Barnes', line: 20.5, result: 31, diffPercent: 51 },
  { date: 'Dec 30', team: 'Grizzlies', player: 'J. Jackson Jr.', line: 19.5, result: 15, diffPercent: -23 },
  { date: 'Dec 14', team: 'Hawks', player: 'J. Johnson', line: 24.5, result: 12, diffPercent: -51 },
  { date: 'Dec 12', team: 'Pacers', player: 'P. Siakam', line: 23.5, result: 20, diffPercent: -15 },
  { date: 'Nov 30', team: 'Hawks', player: 'J. Johnson', line: 23.5, result: 41, diffPercent: 74 },
  { date: 'Nov 25', team: 'Magic', player: 'F. Wagner', line: 25.5, result: 21, diffPercent: -18 },
];

// export const GAME_LIST: GameListItem[] = [
//   { id: '1', homeTeam: { name: 'Nets', logo: 'BKN' }, awayTeam: { name: 'Magic', logo: 'ORL' }, time: '7:00 PM', day: 'Thu' },
//   { id: '2', homeTeam: { name: 'Wizards', logo: 'WAS' }, awayTeam: { name: 'Pistons', logo: 'DET' }, time: '7:00 PM', day: 'Thu' },
//   { id: '3', homeTeam: { name: 'Bulls', logo: 'CHI' }, awayTeam: { name: 'Raptors', logo: 'TOR' }, time: '7:30 PM', day: 'Thu' },
//   { id: '4', homeTeam: { name: 'Jazz', logo: 'UTA' }, awayTeam: { name: 'Hawks', logo: 'ATL' }, time: '7:30 PM', day: 'Thu' },
//   { id: '5', homeTeam: { name: 'Hornets', logo: 'CHA' }, awayTeam: { name: 'Rockets', logo: 'HOU' }, time: '8:00 PM', day: 'Thu' },
//   { id: '6', homeTeam: { name: 'Spurs', logo: 'SAS' }, awayTeam: { name: 'Mavs', logo: 'DAL' }, time: '8:30 PM', day: 'Thu' },
//   { id: '7', homeTeam: { name: 'Warriors', logo: 'GSW' }, awayTeam: { name: 'Suns', logo: 'PHX' }, time: '10:00 PM', day: 'Thu' },
// ... existing code ...

// Map of Tricode to NBA Team ID
export const TEAM_IDS: Record<string, number> = {
  "ATL": 1610612737,
  "BOS": 1610612738,
  "BKN": 1610612751,
  "CHA": 1610612766,
  "CHI": 1610612741,
  "CLE": 1610612739,
  "DAL": 1610612742,
  "DEN": 1610612743,
  "DET": 1610612765,
  "GSW": 1610612744,
  "HOU": 1610612745,
  "IND": 1610612754,
  "LAC": 1610612746,
  "LAL": 1610612747,
  "MEM": 1610612763,
  "MIA": 1610612748,
  "MIL": 1610612749,
  "MIN": 1610612750,
  "NOP": 1610612740,
  "NYK": 1610612752,
  "OKC": 1610612760,
  "ORL": 1610612753,
  "PHI": 1610612755,
  "PHX": 1610612756,
  "POR": 1610612757,
  "SAC": 1610612758,
  "SAS": 1610612759,
  "TOR": 1610612761,
  "UTA": 1610612762,
  "WAS": 1610612764
};

// Map of Tricode to Primary/Secondary Team Colors for Gradient Borders
export const TEAM_COLORS: Record<string, [string, string]> = {
  "ATL": ["#E03A3E", "#C1D32F"],
  "BOS": ["#007A33", "#BA9653"],
  "BKN": ["#000000", "#FFFFFF"],
  "CHA": ["#1D1160", "#00788C"],
  "CHI": ["#CE1141", "#000000"],
  "CLE": ["#860038", "#FDBB30"],
  "DAL": ["#00538C", "#002B5E"],
  "DEN": ["#0E2240", "#FEC524"],
  "DET": ["#C8102E", "#1D428A"],
  "GSW": ["#1D428A", "#FFC72C"],
  "HOU": ["#CE1141", "#000000"],
  "IND": ["#002D62", "#FDBB30"],
  "LAC": ["#C8102E", "#1D428A"],
  "LAL": ["#552583", "#FDB927"],
  "MEM": ["#5D76A9", "#12173F"],
  "MIA": ["#98002E", "#F9A01B"],
  "MIL": ["#00471B", "#EEE1C6"],
  "MIN": ["#0C2340", "#236192"],
  "NOP": ["#0C2340", "#C8102E"],
  "NYK": ["#006BB6", "#F58426"],
  "OKC": ["#007AC1", "#EF3B24"],
  "ORL": ["#0077C0", "#C4CED4"],
  "PHI": ["#006BB6", "#ED174C"],
  "PHX": ["#1D1160", "#E56020"],
  "POR": ["#E03A3E", "#000000"],
  "SAC": ["#5A2D81", "#000000"],
  "SAS": ["#C4CED4", "#000000"],
  "TOR": ["#CE1141", "#000000"],
  "UTA": ["#002B5C", "#00471B"],
  "WAS": ["#002B5C", "#E31837"]
};

// ---------------------------------------------------------------------------
// Seasons
//
// Single source of truth for the season filter. Previously the live season was
// the bare string '25/26' compared inline in ~12 places across App.tsx,
// SimilarPlayers, BarChart and FiltersPanel, so a rollover meant finding every
// literal -- and any one that was missed would silently keep treating last
// season as live.
//
// LIVE_SEASON is served from the live feed (Supabase `players`). Archive seasons
// are served by swapping only the player's game_log, read from `archive_gamelogs`
// keyed by the NBA-format season string below.
// ---------------------------------------------------------------------------

export const LIVE_SEASON = '26/27';

/** Filter label -> the `season` value stored in archive_gamelogs. */
export const ARCHIVE_SEASONS: Record<string, string> = {
  '25/26': '2025-26',
  '24/25': '2024-25',
};

/** Left-to-right order in the filter. 'All' is rendered but not selectable. */
export const SEASON_FILTER_OPTIONS = ['24/25', '25/26', LIVE_SEASON, 'All'];

export type SeasonLabel = string;

export function isArchiveSeason(season: string | null | undefined): boolean {
  return !!season && season in ARCHIVE_SEASONS;
}

/** The archive_gamelogs key for a label, or null if the label is not an archive. */
export function archiveSeasonKey(season: string | null | undefined): string | null {
  return season && season in ARCHIVE_SEASONS ? ARCHIVE_SEASONS[season] : null;
}

/**
 * LIVE_SEASON in NBA format: '26/27' -> '2026-27'.
 *
 * This is the value the backend stamps into players.roster_season (it comes from
 * resolve_season() there), so the live read can filter to players actually on a
 * roster. Derived from LIVE_SEASON rather than hardcoded so a rollover stays a
 * one-line change — and the read that uses it fails open, because resolve_season()
 * rolls over in September and LIVE_SEASON is updated by hand.
 */
export function liveSeasonKey(): string | null {
  const startYear = seasonLabelStartYear(LIVE_SEASON);
  if (startYear === null) {
    return null;
  }
  return `${startYear}-${String((startYear + 1) % 100).padStart(2, '0')}`;
}

export function isSelectableSeason(season: string | null | undefined): boolean {
  return season === LIVE_SEASON || isArchiveSeason(season);
}

/** '26/27' -> 2026. Returns null for anything that is not a season label. */
export function seasonLabelStartYear(season: string | null | undefined): number | null {
  const match = /^(\d{2})\/(\d{2})$/.exec(String(season ?? '').trim());
  return match ? 2000 + Number(match[1]) : null;
}

/**
 * Calendar window for a season label.
 *
 * The September boundary matches the backend's resolve_season(), which rolls over
 * in September so preseason automation targets the upcoming season. Any game in a
 * live row dated before that boundary is therefore last season's residue: the
 * scrapers only ever fetch the resolved season.
 */
export function seasonDateWindow(season: string | null | undefined): { start: string; end: string } | null {
  const startYear = seasonLabelStartYear(season);
  if (startYear === null) {
    return null;
  }
  return { start: `${startYear}-09-01`, end: `${startYear + 1}-08-31` };
}

/** Unknown labels return true, so an unparsed season never silently drops games. */
export function isGameDateInSeason(gameDate: unknown, season: string | null | undefined): boolean {
  const window = seasonDateWindow(season);
  if (!window) {
    return true;
  }
  const date = String(gameDate ?? '').slice(0, 10);
  return Boolean(date) && date >= window.start && date <= window.end;
}

// ---------------------------------------------------------------------------
// Prior-season overlay
//
// A season that has just started has nothing to chart: on 2026-10-21 a typical
// player had one game, so the dashboard looked broken unless the reader knew to
// open the filter panel. The chart therefore backfills the window with the most
// recent season the player actually played, drawn behind a divider, and the
// backfill shrinks on its own as the live season fills in.
//
// These mirror backend/utils/prior_season.py (PRIOR_SEASON_DEPTH,
// PRIOR_SEASON_MIN_GAMES) on purpose. That module feeds the Signal Score from the
// same archive table for the same reason, and if the two disagreed about which
// season counts as a player's prior, the chart and the Edge Board would describe
// the same player differently.
// ---------------------------------------------------------------------------

/**
 * Marks which season a game row came from, once the live log has been blended.
 * Shared so the blend (App) and the divider that explains it (BarChart) cannot
 * drift apart over a string literal.
 */
export const SEASON_LABEL_KEY = '__seasonLabel';

/** How many seasons back to look for a player's most recent real season. */
export const PRIOR_SEASON_DEPTH = 2;

/**
 * A season only counts as a prior if the player logged at least this many games
 * in it. Anchoring a whole chart on a 2-game cup of coffee is worse than
 * reaching back another year.
 */
export const PRIOR_SEASON_MIN_GAMES = 5;

/** '2026-27' -> ['2025-26', '2024-25']. Derived, so a rollover needs no edit. */
export function priorSeasonKeys(
  currentSeasonKey: string | null | undefined,
  depth: number = PRIOR_SEASON_DEPTH,
): string[] {
  const startYear = Number(String(currentSeasonKey ?? '').slice(0, 4));
  if (!Number.isFinite(startYear) || startYear <= 0) {
    return [];
  }
  return Array.from({ length: Math.max(0, depth) }, (_, index) => {
    const year = startYear - (index + 1);
    return `${year}-${String((year + 1) % 100).padStart(2, '0')}`;
  });
}

/** '2025-26' -> '25/26', the form the season filter and chart labels use. */
export function seasonKeyToLabel(seasonKey: string | null | undefined): string | null {
  const match = /^(\d{4})-(\d{2})$/.exec(String(seasonKey ?? '').trim());
  return match ? `${match[1].slice(2)}/${match[2]}` : null;
}

// ---------------------------------------------------------------------------
// Per-panel seasons
//
// The four season-aggregate panels -- shooting zones, assist zones, shot types,
// play types -- each carry their own season, because they are distributions
// rather than game logs and the global season filter has nothing to swap for
// them. Everything below is DERIVED from LIVE_SEASON rather than written out,
// so the October rollover stays the one-line change it already is everywhere
// else in this file.
// ---------------------------------------------------------------------------

/**
 * The season a panel falls back to when the live one is too thin to read.
 *
 * Derived from LIVE_SEASON through the same helpers the chart's prior-season
 * backfill uses, so the two can never disagree about which season is "last
 * season". When LIVE_SEASON becomes '27/28', this becomes '26/27' on its own.
 *
 * What does NOT follow automatically is the data: a panel season is one whose
 * AGGREGATES are stored in player_season_splits / team_season_defense, which
 * only happens once backend/scripts/build_season_splits.py has been run for it.
 * The label rolling over without that script having run leaves the dropdown
 * offering a season the tables have nothing for -- which the panels render as
 * "No data for this season" rather than anything broken, but it is still the
 * yearly chore. See docs/audit/IMPROVEMENT_CHECKLIST.md section 8.
 */
export const PRIOR_PANEL_SEASON = seasonKeyToLabel(priorSeasonKeys(liveSeasonKey(), 1)[0])
  ?? LIVE_SEASON;

/** Seasons offered in each panel's own season dropdown, live first. */
export const PANEL_SEASON_OPTIONS = PRIOR_PANEL_SEASON === LIVE_SEASON
  ? [LIVE_SEASON]
  : [LIVE_SEASON, PRIOR_PANEL_SEASON];

/**
 * The season key the splits tables are keyed by, or null for the live season
 * (which is served off the players row and needs no lookup).
 *
 * Derived from the label rather than looked up in ARCHIVE_SEASONS: that map is
 * a hand-maintained list of seasons whose GAME LOGS exist, and a panel season
 * that had rolled past the last entry would silently resolve to null and
 * quietly stop fetching.
 */
export function panelSeasonKey(season: string | null | undefined): string | null {
  if (!season || season === LIVE_SEASON) {
    return null;
  }
  const startYear = seasonLabelStartYear(season);
  if (startYear === null) {
    return null;
  }
  return `${startYear}-${String((startYear + 1) % 100).padStart(2, '0')}`;
}

/**
 * How many games a season needs before a panel will draw its splits.
 *
 * Not the same question as PRIOR_SEASON_MIN_GAMES (5), which asks whether a
 * season is substantial enough to be treated as a player's prior season. This
 * asks whether a season is stable enough to READ AS A DISTRIBUTION, and that is
 * a harder test: these panels cut one season into six shooting zones, twelve
 * play types or three shot types, so each slice gets a fraction of an already
 * small sample. At five games a rotation player has a handful of attempts per
 * zone and one made shot moves a zone by double digits.
 *
 * Ten is where shot-location mix and play-type frequency settle enough to
 * describe a player. They are rate stats, so they stabilise far sooner than
 * efficiency (FG% needs hundreds of attempts) -- but not in a week.
 *
 * It is one number on purpose. Lower it if blanking real data in November is
 * the bigger annoyance; raise it if early-season splits still read as noise.
 */
export const PANEL_MIN_GAMES = 10;

/** Whether a season has enough games behind it for a panel to draw its splits. */
export function hasEnoughPanelGames(gamesPlayed: unknown): boolean {
  const games = Number(gamesPlayed);
  return Number.isFinite(games) && games >= PANEL_MIN_GAMES;
}

