import { Game, Player } from '../types';

export type OverlayKind = 'numeric' | 'rank' | 'binary';

export interface OverlayFilterContext {
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
  fallbackUpcoming: 'graph_average',
  isAvailable: (player) => hasAnyMetric(player, metricKey),
  getGameValue: getNumericGameValue(metricKey, options?.transform),
  getComparisonValue: getComparisonAverage(metricKey, options?.transform),
});

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
    kind: 'binary',
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
    kind: 'binary',
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
    kind: 'binary',
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
    kind: 'binary',
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
) {
  if (!definition) return '';

  if (definition.kind === 'rank') {
    return `[AVG OPP RANK: #${Math.round(graphAverage)}]`;
  }

  if (definition.kind === 'binary') {
    return `[MATCH RATE: ${(graphAverage * 100).toFixed(0)}%]`;
  }

  const valueFormatter = (value: number) => `${value.toFixed(1)}${definition.unit ?? ''}`;
  if (comparisonAverage === null || !Number.isFinite(comparisonAverage)) {
    return `[GRAPH AVG: ${valueFormatter(graphAverage)}]`;
  }

  return `[GRAPH AVG: ${valueFormatter(graphAverage)} | ${definition.comparisonLabel ?? 'AVG'}: ${valueFormatter(comparisonAverage)}]`;
}

export function formatOverlayAxisValue(definition: OverlayFilterDefinition | null, value: number) {
  if (!definition) return String(Math.round(value));
  if (definition.kind === 'rank') return `#${Math.round(value)}`;
  if (definition.kind === 'binary') return `${Math.round(value * 100)}%`;
  return `${Math.round(value)}${definition.unit ?? ''}`;
}
