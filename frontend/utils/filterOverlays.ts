import { Game, Player } from '../types';

/**
 * 'chartMode' is not an overlay at all: H2H / Home / Away / B2B SUBSET the
 * chart's games rather than drawing a second series. BarChart checks them
 * first (`CHART_MODE_FILTERS`) and sets `overlayDefinition` to null, so these
 * definitions are only ever consulted through `isOverlayFilterSupported`, to
 * decide whether the button is enabled.
 *
 * They were previously typed 'binary', which made `isBinaryOverlay`, the
 * `MATCH RATE` legend and the 0-1 axis branch all unreachable code that looked
 * live. Naming the kind honestly is what let that dead handling be deleted.
 */
export type OverlayKind = 'numeric' | 'rank' | 'chartMode';

export type BaseStatMode = 'average' | 'median';

export interface OverlayFilterContext {
  /** Whether a comparison line reads as the mean or the median. Defaults to mean. */
  baseStat?: BaseStatMode;
  player?: Player;
  game?: any;
  gameIndex?: number;
  games?: any[];
  upcomingGame?: Game | null;
  upcomingOpponent?: string | null;
  graphAverage?: number;
}

export interface OverlayFilterDefinition {
  id: string;
  label: string;
  kind: OverlayKind;
  unit?: string;
  comparisonLabel?: string;
  /** Label used when baseStat is 'median'; falls back to comparisonLabel. */
  medianComparisonLabel?: string;
  fallbackUpcoming?: 'graph_average' | 'none';
  isAvailable: (player?: Player) => boolean;
  getGameValue: (context: OverlayFilterContext) => number | null;
  getComparisonValue?: (context: OverlayFilterContext) => number | null;
  getUpcomingValue?: (context: OverlayFilterContext) => number | null;
}

const toNumber = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const toPercent = (value: unknown): number | null => {
  const parsed = toNumber(value);
  return parsed === null ? null : parsed * 100;
};

const hasGameMetric = (player: Player | undefined, metricKey: string) =>
  Boolean(
    player?.game_log?.some((game: any) => {
      const value = game?.[metricKey];
      return value !== null && value !== undefined && value !== '';
    }),
  );

const hasPlayerStat = (player: Player | undefined, metricKey: string) => {
  const value = player?.stats?.[metricKey];
  return value !== null && value !== undefined && value !== '';
};

const hasAnyMetric = (player: Player | undefined, metricKey: string) =>
  hasGameMetric(player, metricKey) || hasPlayerStat(player, metricKey);

const hasOppRank = (player: Player | undefined, rankKey: string) =>
  Boolean(
    player?.game_log?.some((game: any) => {
      const value = game?.opp_ranks?.[rankKey];
      return value !== null && value !== undefined && value !== '';
    }),
  );

const getGameDate = (game: any) => {
  const raw = game?.GAME_DATE || game?.game_date || null;
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const getGameOpponent = (game: any) => {
  const matchup = String(game?.MATCHUP || '').trim();
  if (!matchup) return null;
  const parts = matchup.split(' ');
  return parts[parts.length - 1] || null;
};

const isHomeGame = (game: any) => String(game?.MATCHUP || '').includes('vs.');
const isAwayGame = (game: any) => String(game?.MATCHUP || '').includes('@');

const averageMetric = (games: any[] | undefined, extractor: (game: any) => number | null) => {
  const values = (games ?? [])
    .map((game) => extractor(game))
    .filter((value): value is number => value !== null && Number.isFinite(value));

  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
};

/**
 * Median of a metric over the loaded games.
 *
 * Worth offering alongside the mean because prop distributions are skewed and
 * bounded at zero: one 42-point night drags a scoring average above the number
 * the player actually hits most of the time, and the mean is what every
 * comparison line in this app used. The median answers "what is the typical
 * game", which is the more useful read for an over/under.
 */
const medianMetric = (games: any[] | undefined, extractor: (game: any) => number | null) => {
  const values = (games ?? [])
    .map((game) => extractor(game))
    .filter((value): value is number => value !== null && Number.isFinite(value))
    .sort((left, right) => left - right);

  if (!values.length) return null;
  const middle = Math.floor(values.length / 2);
  return values.length % 2 === 0
    ? (values[middle - 1] + values[middle]) / 2
    : values[middle];
};

const getBackToBackValue = (context: OverlayFilterContext) => {
  const games = context.games ?? [];
  const gameIndex = context.gameIndex ?? 0;
  if (gameIndex <= 0 || !games[gameIndex]) return 0;

  const currentDate = getGameDate(games[gameIndex]);
  const previousDate = getGameDate(games[gameIndex - 1]);
  if (!currentDate || !previousDate) return null;

  const diffDays = Math.round((currentDate.getTime() - previousDate.getTime()) / 86400000);
  return diffDays === 1 ? 1 : 0;
};

const getUpcomingBackToBackValue = (context: OverlayFilterContext) => {
  const latestLoggedGame = context.player?.game_log?.[0];
  const latestDate = getGameDate(latestLoggedGame);
  const upcomingDate = context.upcomingGame?.game_date ? new Date(context.upcomingGame.game_date) : null;
  if (!latestDate || !upcomingDate || Number.isNaN(upcomingDate.getTime())) return null;
  const diffDays = Math.round((upcomingDate.getTime() - latestDate.getTime()) / 86400000);
  return diffDays === 1 ? 1 : 0;
};

const getComparisonAverage = (
  metricKey: string,
  transform?: (value: unknown) => number | null,
) => (context: OverlayFilterContext) => {
  const player = context.player;
  if (!player) return null;

  // Median is computed from the loaded game log, because `player.stats` only
  // carries season MEANS -- there is no stored median to read. That makes the
  // two modes cover different game sets: the mean is the season average the
  // pipeline published, while the median is over the games on screen. The
  // label changes with the mode so the comparison is never silently mixed.
  if (context.baseStat === 'median') {
    return medianMetric(
      player.game_log,
      (game) => (transform ? transform(game?.[metricKey]) : toNumber(game?.[metricKey])),
    );
  }

  const rawValue = player.stats?.[metricKey];
  return transform ? transform(rawValue) : toNumber(rawValue);
};

const getNumericGameValue = (
  metricKey: string,
  transform?: (value: unknown) => number | null,
) => (context: OverlayFilterContext) => {
  const rawValue = context.game?.[metricKey];
  return transform ? transform(rawValue) : toNumber(rawValue);
};

const getNumericFilter = (
  id: string,
  metricKey: string,
  options?: {
    label?: string;
    unit?: string;
    transform?: (value: unknown) => number | null;
    comparisonLabel?: string;
  },
): OverlayFilterDefinition => ({
  id,
  label: options?.label ?? id,
  kind: 'numeric',
  unit: options?.unit,
  comparisonLabel: options?.comparisonLabel ?? 'SEASON AVG',
  medianComparisonLabel: 'MEDIAN (SHOWN)',
  fallbackUpcoming: 'graph_average',
  isAvailable: (player) => hasAnyMetric(player, metricKey),
  getGameValue: getNumericGameValue(metricKey, options?.transform),
  getComparisonValue: getComparisonAverage(metricKey, options?.transform),
});

/**
 * `play_type_analysis` labels the backend emits ("PNR Ball Handler", "Off
 * Screen", "PNR Roll Man") don't all match the UI's shorter pill labels
 * ("PnR Ball Handler", "Off Scr", "PnR RM") by substring -- "PnR RM" isn't a
 * substring of "PNR Roll Man" either direction -- so this maps explicitly
 * rather than reusing FiltersPanel's fuzzy badge-matching, which already
 * silently misses "PnR RM" for the same reason.
 */
const PLAY_TYPE_BACKEND_LABELS: Record<string, string> = {
  Transition: 'Transition',
  'PnR Ball Handler': 'PNR Ball Handler',
  Isolation: 'Isolation',
  'Spot Up': 'Spot Up',
  'Off Scr': 'Off Screen',
  'Post Up': 'Post Up',
  Handoff: 'Handoff',
  'PnR RM': 'PNR Roll Man',
  Putback: 'Putback',
};

const getPlayTypeRank = (player: Player | undefined, uiLabel: string) => {
  const backendLabel = PLAY_TYPE_BACKEND_LABELS[uiLabel];
  const match = player?.play_type_analysis?.find((p: any) => p?.type === backendLabel);
  return toNumber(match?.rank);
};

const getPlayTypeFilter = (uiLabel: string, rankKey: string) =>
  getRankFilter(uiLabel, uiLabel, rankKey, (context) => getPlayTypeRank(context.player, uiLabel));

const getRankFilter = (
  id: string,
  label: string,
  rankKey: string,
  getUpcomingValue: (context: OverlayFilterContext) => number | null,
): OverlayFilterDefinition => ({
  id,
  label,
  kind: 'rank',
  comparisonLabel: 'AVG OPP RANK',
  fallbackUpcoming: 'none',
  isAvailable: (player) => hasOppRank(player, rankKey) || getUpcomingValue({ player }) !== null,
  getGameValue: (context) => toNumber(context.game?.opp_ranks?.[rankKey]),
  getUpcomingValue,
});

export const OVERLAY_FILTER_DEFINITIONS: Record<string, OverlayFilterDefinition> = {
  Minutes: getNumericFilter('Minutes', 'MIN'),
  Points: getNumericFilter('Points', 'PTS'),
  Assists: getNumericFilter('Assists', 'AST'),
  Rebounds: getNumericFilter('Rebounds', 'REB'),
  'USG%': getNumericFilter('USG%', 'USG_PCT', { unit: '%', transform: toPercent }),
  'FG%': getNumericFilter('FG%', 'FG_PCT', { unit: '%', transform: toPercent }),
  FGA: getNumericFilter('FGA', 'FGA'),
  '3PA': getNumericFilter('3PA', 'FG3A'),
  '3P': getNumericFilter('3P', 'FG3M'),
  FTA: getNumericFilter('FTA', 'FTA'),
  Fouls: getNumericFilter('Fouls', 'PF'),
  H2H: {
    id: 'H2H',
    label: 'H2H',
    kind: 'chartMode',
    fallbackUpcoming: 'none',
    isAvailable: (player) => Boolean(player?.game_log?.length),
    getGameValue: (context) => {
      if (!context.upcomingOpponent) return null;
      const opponent = getGameOpponent(context.game);
      if (!opponent) return null;
      return opponent === context.upcomingOpponent ? 1 : 0;
    },
    getUpcomingValue: (context) => (context.upcomingOpponent ? 1 : null),
  },
  Home: {
    id: 'Home',
    label: 'Home',
    kind: 'chartMode',
    fallbackUpcoming: 'none',
    isAvailable: (player) => Boolean(player?.game_log?.length),
    getGameValue: (context) => (isHomeGame(context.game) ? 1 : 0),
    getUpcomingValue: (context) => {
      if (!context.player || !context.upcomingGame) return null;
      return context.upcomingGame.home_team_tricode === context.player.team ? 1 : 0;
    },
  },
  Away: {
    id: 'Away',
    label: 'Away',
    kind: 'chartMode',
    fallbackUpcoming: 'none',
    isAvailable: (player) => Boolean(player?.game_log?.length),
    getGameValue: (context) => (isAwayGame(context.game) ? 1 : 0),
    getUpcomingValue: (context) => {
      if (!context.player || !context.upcomingGame) return null;
      return context.upcomingGame.away_team_tricode === context.player.team ? 1 : 0;
    },
  },
  B2B: {
    id: 'B2B',
    label: 'B2B',
    kind: 'chartMode',
    fallbackUpcoming: 'none',
    isAvailable: (player) => Boolean(player?.game_log?.length),
    getGameValue: getBackToBackValue,
    getUpcomingValue: getUpcomingBackToBackValue,
  },
  'Win/Loss Margin': {
    id: 'Win/Loss Margin',
    label: 'Win/Loss Margin',
    kind: 'numeric',
    comparisonLabel: 'FULL AVG',
    fallbackUpcoming: 'graph_average',
    isAvailable: (player) => hasGameMetric(player, 'margin'),
    getGameValue: (context) => toNumber(context.game?.margin),
    getComparisonValue: (context) => averageMetric(context.player?.game_log, (game) => toNumber(game?.margin)),
  },
  'Def vs DPT': getRankFilter('Def vs DPT', 'Def vs DPT', 'dpt', (context) => {
    const plays = Array.isArray(context.player?.play_type_analysis) ? [...context.player.play_type_analysis] : [];
    if (!plays.length) return null;
    const sorted = plays.sort((a: any, b: any) => Number(String(b.percent).replace('%', '')) - Number(String(a.percent).replace('%', '')));
    return toNumber(sorted[0]?.rank);
  }),
  'Def vs DSZ': getRankFilter('Def vs DSZ', 'Def vs DSZ', 'dsz', (context) => {
    const zones = Object.entries(context.player?.shooting_zones ?? {})
      .map(([zone, data]: any) => ({ zone, pct: Number(String(data?.percentage ?? '0').replace('%', '')) }))
      .sort((a, b) => b.pct - a.pct);
    const zoneKey = zones[0]?.zone;
    return zoneKey ? toNumber(context.player?.opp_def_zones?.[zoneKey]?.rank) : null;
  }),
  'Def vs DSZ2': getRankFilter('Def vs DSZ2', 'Def vs DSZ2', 'dsz2', (context) => {
    const zones = Object.entries(context.player?.shooting_zones ?? {})
      .map(([zone, data]: any) => ({ zone, pct: Number(String(data?.percentage ?? '0').replace('%', '')) }))
      .sort((a, b) => b.pct - a.pct);
    const zoneKey = zones[1]?.zone;
    return zoneKey ? toNumber(context.player?.opp_def_zones?.[zoneKey]?.rank) : null;
  }),
  'Opp Paint Pts Allowed': getRankFilter('Opp Paint Pts Allowed', 'Opp Paint Pts Allowed', 'paint_allowed', (context) =>
    toNumber(context.player?.opp_def_zones?.paint?.rank),
  ),
  'Def vs Pull Up': getRankFilter('Def vs Pull Up', 'Def vs Pull Up', 'pull_up', (context) =>
    toNumber(context.player?.shot_type_analysis?.opp_def?.pull_up?.rank),
  ),
  'Opp Pace': getRankFilter('Opp Pace', 'Opp Pace', 'pace', (context) =>
    toNumber(context.player?.stats?.opp_pace_rank),
  ),
  'Opp DefRtg': getRankFilter('Opp DefRtg', 'Opp DefRtg', 'def_rating', (context) =>
    toNumber(context.player?.stats?.opp_def_rating_rank),
  ),
  'Def vs Position (PTS)': getRankFilter('Def vs Position (PTS)', 'Def vs Position (PTS)', 'pos_pts', (context) =>
    toNumber(context.player?.stats?.opp_pos_pts_rank),
  ),
  'Catch & Shoot': getRankFilter('Catch & Shoot', 'Catch & Shoot', 'catch_and_shoot', (context) =>
    toNumber(context.player?.shot_type_analysis?.opp_def?.catch_and_shoot?.rank),
  ),
  '3PT Att Allowed': getRankFilter('3PT Att Allowed', '3PT Att Allowed', 'fg3a_allowed', (context) =>
    toNumber(context.player?.stats?.opp_fg3a_rank),
  ),
  'FT Att Allowed': getRankFilter('FT Att Allowed', 'FT Att Allowed', 'fta_allowed', (context) =>
    toNumber(context.player?.stats?.opp_fta_rank),
  ),
  Transition: getPlayTypeFilter('Transition', 'transition'),
  'PnR Ball Handler': getPlayTypeFilter('PnR Ball Handler', 'pnr_ball_handler'),
  Isolation: getPlayTypeFilter('Isolation', 'isolation'),
  'Spot Up': getPlayTypeFilter('Spot Up', 'spot_up'),
  'Off Scr': getPlayTypeFilter('Off Scr', 'off_screen'),
  'Post Up': getPlayTypeFilter('Post Up', 'post_up'),
  Handoff: getPlayTypeFilter('Handoff', 'handoff'),
  'PnR RM': getPlayTypeFilter('PnR RM', 'pnr_roll_man'),
  Putback: getPlayTypeFilter('Putback', 'putback'),
};

export function getOverlayFilterDefinition(filterId?: string | null) {
  if (!filterId) return null;
  return OVERLAY_FILTER_DEFINITIONS[filterId] ?? null;
}

export function isOverlayFilterSupported(filterId: string, player?: Player) {
  const definition = getOverlayFilterDefinition(filterId);
  return Boolean(definition?.isAvailable(player));
}

export function formatOverlayLegend(
  definition: OverlayFilterDefinition | null,
  graphAverage: number,
  comparisonAverage: number | null,
  baseStat: BaseStatMode = 'average',
) {
  if (!definition) return '';

  if (definition.kind === 'rank') {
    return `[AVG OPP RANK: #${Math.round(graphAverage)}]`;
  }

  const valueFormatter = (value: number) => `${value.toFixed(1)}${definition.unit ?? ''}`;
  if (comparisonAverage === null || !Number.isFinite(comparisonAverage)) {
    return `[GRAPH AVG: ${valueFormatter(graphAverage)}]`;
  }

  const comparisonLabel = baseStat === 'median'
    ? (definition.medianComparisonLabel ?? definition.comparisonLabel ?? 'MEDIAN')
    : (definition.comparisonLabel ?? 'AVG');
  return `[GRAPH AVG: ${valueFormatter(graphAverage)} | ${comparisonLabel}: ${valueFormatter(comparisonAverage)}]`;
}

export function formatOverlayAxisValue(definition: OverlayFilterDefinition | null, value: number) {
  if (!definition) return String(Math.round(value));
  if (definition.kind === 'rank') return `#${Math.round(value)}`;
  return `${Math.round(value)}${definition.unit ?? ''}`;
}
