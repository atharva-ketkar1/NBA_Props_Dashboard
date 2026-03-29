import { getOptionalEnv, getSupabaseAdmin } from './supabaseAdmin.js';
import {
  buildFutureDates,
  getDashboardDate,
  getFastRefreshDates,
} from './dashboardDate.js';
import { rankSimilarPlayers } from '../../utils/similarPlayers.js';
import { playerHasAnyProp } from '../../utils/propResolution.js';
import type { Player, PlayerPropsByDate, SimilarPlayerCandidate, SportsbookId } from '../../types.js';

const PLAYER_PROP_SELECT = 'player_id, stat_type, sportsbook, line, over_odds, under_odds, implied, game_date, game_id, updated_at';
const PLAYER_BASE_SELECT = 'id, name, team, position, stats';
const PLAYER_DETAIL_SELECT = 'game_log, shooting_zones, assist_zones, opp_def_zones, opp_def_zones_positional, opp_assist_zones, opp_assist_zones_positional, shot_type_analysis, play_type_analysis';
const PLAYER_SIMILAR_SELECT = 'id, name, team, position, stats, play_type_analysis, shot_type_analysis';
const HISTORICAL_ODDS_SELECT = 'game_date, props, source';
const INITIAL_LINE_SELECT = 'game_date, snapshots, updated_at';
const LINE_META_SELECT = 'game_date, updated_at';
const SLATE_SELECT = 'home_team_tricode, away_team_tricode';
const PAGE_SIZE = 1000;
const MAX_UPCOMING_PROP_DAYS = 2;
const DEFAULT_BOOTSTRAP_PROP_DAYS = MAX_UPCOMING_PROP_DAYS;
const DEFAULT_SIMILAR_PROP_DAYS = MAX_UPCOMING_PROP_DAYS;
const DEFAULT_PLAYERS_CACHE_MS = 60_000;
const DEFAULT_PROPS_CACHE_MS = 30_000;
const DEFAULT_GAMES_CACHE_MS = 60_000;
const DEFAULT_LINE_META_CACHE_MS = 15_000;
const DEFAULT_LINE_ROWS_CACHE_MS = 30_000;
const DEFAULT_PLAYER_DETAIL_CACHE_MS = 5 * 60_000;
const DEFAULT_ARCHIVE_CACHE_MS = 60 * 60_000;
const DEFAULT_SIMILAR_CACHE_MS = 60_000;

declare global {
  // eslint-disable-next-line no-var
  var __propsmadnessDashboardCache:
    | Map<string, { expiresAt: number; value: unknown }>
    | undefined;
  // eslint-disable-next-line no-var
  var __propsmadnessDashboardInflight:
    | Map<string, Promise<unknown>>
    | undefined;
}

const dashboardCache = globalThis.__propsmadnessDashboardCache ??= new Map();
const dashboardInflight = globalThis.__propsmadnessDashboardInflight ??= new Map();

function parsePositiveInteger(rawValue: string, fallbackValue: number) {
  const parsed = Number(rawValue);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallbackValue;
}

function getServerNumberEnv(name: string, fallbackValue: number) {
  return parsePositiveInteger(getOptionalEnv(name), fallbackValue);
}

function clampPropDayWindow(days: number) {
  return Math.min(MAX_UPCOMING_PROP_DAYS, Math.max(1, days));
}

const BOOTSTRAP_PROP_DAYS = clampPropDayWindow(
  getServerNumberEnv('DASHBOARD_BOOTSTRAP_PROP_DAYS', DEFAULT_BOOTSTRAP_PROP_DAYS),
);
const SIMILAR_PROP_DAYS = clampPropDayWindow(
  getServerNumberEnv('DASHBOARD_SIMILAR_PROP_DAYS', DEFAULT_SIMILAR_PROP_DAYS),
);
const PLAYERS_CACHE_MS = getServerNumberEnv('DASHBOARD_PLAYERS_CACHE_MS', DEFAULT_PLAYERS_CACHE_MS);
const PROPS_CACHE_MS = getServerNumberEnv('DASHBOARD_PROPS_CACHE_MS', DEFAULT_PROPS_CACHE_MS);
const GAMES_CACHE_MS = getServerNumberEnv('DASHBOARD_GAMES_CACHE_MS', DEFAULT_GAMES_CACHE_MS);
const LINE_META_CACHE_MS = getServerNumberEnv('DASHBOARD_LINE_META_CACHE_MS', DEFAULT_LINE_META_CACHE_MS);
const LINE_ROWS_CACHE_MS = getServerNumberEnv('DASHBOARD_LINE_ROWS_CACHE_MS', DEFAULT_LINE_ROWS_CACHE_MS);
const PLAYER_DETAIL_CACHE_MS = getServerNumberEnv('DASHBOARD_PLAYER_DETAIL_CACHE_MS', DEFAULT_PLAYER_DETAIL_CACHE_MS);
const ARCHIVE_CACHE_MS = getServerNumberEnv('DASHBOARD_ARCHIVE_CACHE_MS', DEFAULT_ARCHIVE_CACHE_MS);
const SIMILAR_CACHE_MS = getServerNumberEnv('DASHBOARD_SIMILAR_CACHE_MS', DEFAULT_SIMILAR_CACHE_MS);

function assertNoError(error: { message?: string } | null, context: string) {
  if (error) {
    throw new Error(`[supabase] ${context}: ${error.message ?? 'Unknown error'}`);
  }
}

function buildCacheKey(parts: Array<string | number>) {
  return parts.join('::');
}

async function readCached<T>(key: string, ttlMs: number, loader: () => Promise<T>) {
  const now = Date.now();
  const cachedEntry = dashboardCache.get(key);

  if (cachedEntry && cachedEntry.expiresAt > now) {
    return cachedEntry.value as T;
  }

  const inflightEntry = dashboardInflight.get(key);
  if (inflightEntry) {
    return inflightEntry as Promise<T>;
  }

  const loadPromise = (async () => {
    try {
      const value = await loader();
      dashboardCache.set(key, {
        expiresAt: Date.now() + ttlMs,
        value,
      });
      return value;
    } catch (error) {
      if (cachedEntry) {
        return cachedEntry.value as T;
      }
      throw error;
    } finally {
      dashboardInflight.delete(key);
    }
  })();

  dashboardInflight.set(key, loadPromise as Promise<unknown>);
  return loadPromise;
}

export function serializeLineMovementVersion(rows: Array<{ game_date?: string; updated_at?: string | null }>) {
  return (rows ?? [])
    .map((row) => `${row.game_date ?? ''}:${row.updated_at ?? ''}`)
    .sort()
    .join('|');
}

function buildPropsByDateMap(props: any[]): Record<number, PlayerPropsByDate> {
  const propsByDateMap: Record<number, PlayerPropsByDate> = {};

  for (const row of props ?? []) {
    const gameDateKey = row.game_date || '__undated__';
    if (!propsByDateMap[row.player_id]) propsByDateMap[row.player_id] = {};
    if (!propsByDateMap[row.player_id][row.stat_type]) propsByDateMap[row.player_id][row.stat_type] = {};
    if (!propsByDateMap[row.player_id][row.stat_type][row.sportsbook]) propsByDateMap[row.player_id][row.stat_type][row.sportsbook] = {};
    propsByDateMap[row.player_id][row.stat_type][row.sportsbook][gameDateKey] = {
      line: row.line,
      over: row.over_odds,
      under: row.under_odds,
      implied: row.implied,
      game_date: row.game_date,
      game_id: row.game_id,
      updated_at: row.updated_at,
    };
  }

  return propsByDateMap;
}

function buildSimilarityPlayers(players: any[], props: any[], selectedGameDate?: string | null): Player[] {
  const propsByDateMap = buildPropsByDateMap(props ?? []);

  return (players ?? []).map((player: any) => ({
    id: player.id,
    name: player.name,
    team: player.team,
    position: player.position,
    stats: player.stats ?? {},
    game_log: [],
    props: {},
    props_by_date: propsByDateMap[player.id] ?? {},
    active_game_date: selectedGameDate ?? null,
    historical_odds: {},
    intraday_movements: [],
    detail_loaded: false,
    play_type_analysis: player.play_type_analysis ?? [],
    shot_type_analysis: player.shot_type_analysis ?? null,
  }));
}

async function fetchAllPlayerPropsForDates(
  gameDates: string[],
  selectClause = PLAYER_PROP_SELECT,
  sportsbook?: SportsbookId | null,
) {
  const normalizedDates = Array.from(new Set((gameDates ?? []).filter(Boolean))).sort();
  if (!normalizedDates.length) {
    return [];
  }

  return readCached(
    buildCacheKey(['player_props', selectClause, sportsbook ?? 'all', normalizedDates.join(',')]),
    PROPS_CACHE_MS,
    async () => {
      const supabase = getSupabaseAdmin();
      const allRows: any[] = [];

      for (let start = 0; ; start += PAGE_SIZE) {
        let query = supabase
          .from('player_props')
          .select(selectClause)
          .in('game_date', normalizedDates)
          .order('game_date', { ascending: true })
          .order('player_id', { ascending: true })
          .order('stat_type', { ascending: true })
          .order('sportsbook', { ascending: true });

        if (sportsbook) {
          query = query.eq('sportsbook', sportsbook);
        }

        const { data, error } = await query.range(start, start + PAGE_SIZE - 1);

        assertNoError(error, 'player_props');

        if (!data?.length) {
          return allRows;
        }

        allRows.push(...data);

        if (data.length < PAGE_SIZE) {
          return allRows;
        }
      }
    },
  );
}

async function fetchPlayers(selectClause: string) {
  return readCached(
    buildCacheKey(['players', selectClause]),
    PLAYERS_CACHE_MS,
    async () => {
      const supabase = getSupabaseAdmin();
      const { data, error } = await supabase.from('players').select(selectClause);
      assertNoError(error, 'players');
      return data ?? [];
    },
  );
}

async function fetchGamesForDates(dates: string[], selectClause = '*') {
  const normalizedDates = Array.from(new Set((dates ?? []).filter(Boolean))).sort();
  if (!normalizedDates.length) {
    return [];
  }

  return readCached(
    buildCacheKey(['games', selectClause, normalizedDates.join(',')]),
    GAMES_CACHE_MS,
    async () => {
      const supabase = getSupabaseAdmin();
      const { data, error } = await supabase
        .from('games')
        .select(selectClause)
        .in('game_date', normalizedDates);

      assertNoError(error, 'games');
      return data ?? [];
    },
  );
}

async function fetchLineMovementsForDates(
  dates: string[],
  selectClause: string,
  ttlMs: number,
  context: string,
) {
  const normalizedDates = Array.from(new Set((dates ?? []).filter(Boolean))).sort();
  if (!normalizedDates.length) {
    return [];
  }

  return readCached(
    buildCacheKey(['line_movements', selectClause, normalizedDates.join(',')]),
    ttlMs,
    async () => {
      const supabase = getSupabaseAdmin();
      const { data, error } = await supabase
        .from('line_movements')
        .select(selectClause)
        .in('game_date', normalizedDates);

      assertNoError(error, context);
      return data ?? [];
    },
  );
}

export async function fetchBootstrapPayload(activeSportsbook: SportsbookId = 'dk') {
  const today = getDashboardDate();
  const futureDates = buildFutureDates(today, BOOTSTRAP_PROP_DAYS);
  const fastRefreshDates = getFastRefreshDates(null);
  const includeLineMovements = activeSportsbook !== 'pp';

  const [playersRows, propsRows, lineRows, gamesRows] = await Promise.all([
    fetchPlayers(PLAYER_BASE_SELECT),
    fetchAllPlayerPropsForDates(futureDates, PLAYER_PROP_SELECT, activeSportsbook),
    includeLineMovements
      ? fetchLineMovementsForDates(fastRefreshDates, INITIAL_LINE_SELECT, LINE_ROWS_CACHE_MS, 'line_movements')
      : Promise.resolve([]),
    fetchGamesForDates([today], SLATE_SELECT),
  ]);

  return {
    playersRows: (playersRows ?? []).map((row: any) => ({
      ...row,
      play_type_analysis: undefined,
    })),
    propsRows,
    gamesRows: gamesRows ?? [],
    lineRows: lineRows ?? [],
    lineVersion: serializeLineMovementVersion(lineRows ?? []),
  };
}

export async function fetchHotPayload(
  selectedDate: string | null,
  currentLineVersion: string,
  activeSportsbook: SportsbookId = 'dk',
) {
  const activeDates = getFastRefreshDates(selectedDate);
  const includeLineMovements = activeSportsbook !== 'pp';

  const [propsRows, lineMetaRows] = await Promise.all([
    fetchAllPlayerPropsForDates(activeDates, PLAYER_PROP_SELECT, activeSportsbook),
    includeLineMovements
      ? fetchLineMovementsForDates(
        activeDates,
        LINE_META_SELECT,
        LINE_META_CACHE_MS,
        'line_movement metadata',
      )
      : Promise.resolve([]),
  ]);

  const nextVersion = serializeLineMovementVersion(lineMetaRows ?? []);

  if (!includeLineMovements) {
    return {
      propsRows,
      lineVersion: currentLineVersion,
    };
  }

  if (nextVersion === currentLineVersion) {
    return {
      propsRows,
      lineVersion: nextVersion,
    };
  }

  const lineRows = await fetchLineMovementsForDates(
    activeDates,
    INITIAL_LINE_SELECT,
    LINE_ROWS_CACHE_MS,
    'line_movements',
  );

  return {
    propsRows,
    lineVersion: nextVersion,
    lineRows: lineRows ?? [],
  };
}

export async function fetchGamesPayload(dates: string[]) {
  return {
    games: await fetchGamesForDates(dates),
  };
}

export async function fetchPlayerPayload(playerId: number) {
  return readCached(
    buildCacheKey(['player_detail', playerId]),
    PLAYER_DETAIL_CACHE_MS,
    async () => {
      const supabase = getSupabaseAdmin();

      const [
        { data: detail, error: detailError },
        { data: historicalOddsRows, error: historicalOddsError },
      ] = await Promise.all([
        supabase.from('players').select(PLAYER_DETAIL_SELECT).eq('id', playerId).maybeSingle(),
        supabase.from('historical_odds').select(HISTORICAL_ODDS_SELECT).eq('player_id', playerId),
      ]);

      assertNoError(detailError, 'player detail');
      assertNoError(historicalOddsError, 'historical_odds');

      if (!detail) {
        throw new Error('[supabase] player detail: Player not found.');
      }

      return {
        detail,
        historicalOddsRows: historicalOddsRows ?? [],
      };
    },
  );
}

export async function fetchSimilarCandidatesPayload({
  activeSportsbook,
  activeTab,
  playerId,
  selectedGameDate,
}: {
  activeSportsbook: SportsbookId;
  activeTab: string;
  playerId: number;
  selectedGameDate?: string | null;
}) {
  return readCached(
    buildCacheKey([
      'similar',
      playerId,
      activeTab,
      activeSportsbook,
      selectedGameDate ?? '',
    ]),
    SIMILAR_CACHE_MS,
    async () => {
      const today = getDashboardDate();
      const propDates = selectedGameDate ? [selectedGameDate] : buildFutureDates(today, SIMILAR_PROP_DAYS);

      const [playersRows, propsRows] = await Promise.all([
        fetchPlayers(PLAYER_SIMILAR_SELECT),
        fetchAllPlayerPropsForDates(propDates, PLAYER_PROP_SELECT, activeSportsbook),
      ]);

      const players = buildSimilarityPlayers(playersRows ?? [], propsRows ?? [], selectedGameDate ?? null)
        .filter(playerHasAnyProp);
      const player = players.find((entry) => entry.id === playerId);

      if (!player) {
        throw new Error('[supabase] similar players: Player not found in active prop pool.');
      }

      return {
        similarCandidatesByPosition: rankSimilarPlayers({
          player,
          players,
          activeTab,
          activeSportsbook,
          selectedGameDate: selectedGameDate ?? null,
          mode: 'position',
          limit: 14,
        }) satisfies SimilarPlayerCandidate[],
        similarCandidatesByProp: rankSimilarPlayers({
          player,
          players,
          activeTab,
          activeSportsbook,
          selectedGameDate: selectedGameDate ?? null,
          mode: 'prop',
          limit: 12,
        }) satisfies SimilarPlayerCandidate[],
      };
    },
  );
}

export async function fetchArchivePayload(playerId: number, season: string) {
  return readCached(
    buildCacheKey(['archive', playerId, season]),
    ARCHIVE_CACHE_MS,
    async () => {
      const supabase = getSupabaseAdmin();
      const { data, error } = await supabase
        .from('archive_gamelogs')
        .select('game_log')
        .eq('player_id', playerId)
        .eq('season', season)
        .maybeSingle();

      assertNoError(error, 'archive_gamelogs');
      const archiveRow = (data ?? null) as { game_log?: unknown } | null;
      const gameLog = archiveRow?.game_log;

      return {
        gameLog: Array.isArray(gameLog) ? gameLog : [],
      };
    },
  );
}
