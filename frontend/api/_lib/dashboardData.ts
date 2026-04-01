import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { getOptionalEnv, getSupabaseAdmin } from './supabaseAdmin.js';
import {
  buildFutureDates,
  getDashboardDate,
  getFastRefreshDates,
} from './dashboardDate.js';
import { rankSimilarPlayers } from '../../utils/similarPlayers.js';
import { playerHasAnyProp } from '../../utils/propResolution.js';
import type {
  EdgeScorePayload,
  Player,
  PlayerPropsByDate,
  SimilarPlayerCandidate,
  SportsbookId,
} from '../../types.js';

const PLAYER_PROP_SELECT = 'player_id, stat_type, sportsbook, line, over_odds, under_odds, implied, game_date, game_id, updated_at';
const PLAYER_PROP_AVAIL_SELECT = 'player_id, stat_type, sportsbook, game_date';
const PLAYER_BASE_SELECT = 'id, name, team, position, stats';
const PLAYER_DETAIL_SELECT = 'game_log, shooting_zones, assist_zones, opp_def_zones, opp_def_zones_positional, opp_assist_zones, opp_assist_zones_positional, shot_type_analysis, play_type_analysis';
const PLAYER_SIMILAR_SELECT = 'id, name, team, position, stats, play_type_analysis, shot_type_analysis';
const HISTORICAL_ODDS_SELECT = 'game_date, props, source';
const HISTORICAL_PLAYER_PROP_SELECT = 'game_date, stat_type, sportsbook, line, over_odds, under_odds, implied, source, captured_at, is_closing_line';
const INITIAL_LINE_SELECT = 'game_date, snapshots, updated_at';
const LINE_META_SELECT = 'game_date, updated_at';
const SLATE_SELECT = 'game_id, game_date, home_team_tricode, away_team_tricode, is_live, is_final';
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
const DEFAULT_EDGE_CACHE_MS = 15_000;
const DEFAULT_HISTORICAL_LEGACY_FALLBACK = true;
const HISTORICAL_SOURCE_PRIORITY: Record<string, number> = {
  closing_line: 3,
  pre_game_snapshot_fallback: 2,
  last_snapshot_fallback: 1,
  line_movements_fallback: 1,
};
const LEGACY_HISTORICAL_BOOK_MAP: Record<string, string> = {
  dk: 'draftkings',
  fd: 'fanduel',
  pp: 'pp',
  draftkings: 'draftkings',
  fanduel: 'fanduel',
  prizepicks: 'pp',
};

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
const currentFilePath = fileURLToPath(import.meta.url);
const currentDir = path.dirname(currentFilePath);
const LOCAL_EDGE_SCORE_PATH = path.resolve(currentDir, '..', '..', '..', 'backend', 'data', 'current', 'edge_scores_top15.json');

function parsePositiveInteger(rawValue: string, fallbackValue: number) {
  const parsed = Number(rawValue);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallbackValue;
}

function getServerNumberEnv(name: string, fallbackValue: number) {
  return parsePositiveInteger(getOptionalEnv(name), fallbackValue);
}

function getServerBooleanEnv(name: string, fallbackValue: boolean) {
  const rawValue = getOptionalEnv(name);
  if (!rawValue) return fallbackValue;

  const normalized = rawValue.trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(normalized)) {
    return true;
  }
  if (['0', 'false', 'no', 'off'].includes(normalized)) {
    return false;
  }
  return fallbackValue;
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
const EDGE_CACHE_MS = getServerNumberEnv('DASHBOARD_EDGE_CACHE_MS', DEFAULT_EDGE_CACHE_MS);
const HISTORICAL_ODDS_LEGACY_FALLBACK = getServerBooleanEnv(
  'HISTORICAL_ODDS_LEGACY_FALLBACK',
  DEFAULT_HISTORICAL_LEGACY_FALLBACK,
);

function assertNoError(error: { message?: string } | null, context: string) {
  if (error) {
    throw new Error(`[supabase] ${context}: ${error.message ?? 'Unknown error'}`);
  }
}

function isMissingRelationError(error: { message?: string } | null, tableName: string) {
  const message = String(error?.message ?? '').toLowerCase();
  const tableToken = tableName.toLowerCase();
  return (
    (message.includes('relation') && message.includes('does not exist') && message.includes(tableToken))
    || (message.includes('could not find the table') && message.includes(tableToken))
    || (message.includes('schema cache') && message.includes(tableToken))
  );
}

function buildCacheKey(parts: Array<string | number>) {
  return parts.join('::');
}

function readLocalEdgePayloadFallback(): EdgeScorePayload | null {
  if (!fs.existsSync(LOCAL_EDGE_SCORE_PATH)) {
    return null;
  }

  try {
    const rawPayload = JSON.parse(fs.readFileSync(LOCAL_EDGE_SCORE_PATH, 'utf8')) as Partial<EdgeScorePayload> | null;
    if (!rawPayload || typeof rawPayload !== 'object') {
      return null;
    }

    return {
      generated_at: String(rawPayload.generated_at ?? ''),
      refresh_label: String(rawPayload.refresh_label ?? 'local'),
      game_dates: Array.isArray(rawPayload.game_dates) ? rawPayload.game_dates : [],
      summary: rawPayload.summary && typeof rawPayload.summary === 'object' ? rawPayload.summary : {},
      recommendations: Array.isArray(rawPayload.recommendations) ? rawPayload.recommendations : [],
      notification: rawPayload.notification && typeof rawPayload.notification === 'object' ? rawPayload.notification : {},
    } satisfies EdgeScorePayload;
  } catch (error) {
    console.error('[edge fallback] Could not read local edge score artifact.', error);
    return null;
  }
}

function getHistoricalSourcePriority(source?: string | null) {
  if (!source) return 0;
  return HISTORICAL_SOURCE_PRIORITY[source] ?? 0;
}

function reshapeHistoricalOddsRows(rows: any[]) {
  const byDate = new Map<string, {
    props: Record<string, any>;
    source: string | null;
    capturedAt: string | null;
  }>();

  for (const row of rows ?? []) {
    const gameDate = String(row?.game_date ?? '').trim();
    const statType = String(row?.stat_type ?? '').trim();
    const sportsbook = LEGACY_HISTORICAL_BOOK_MAP[String(row?.sportsbook ?? '').trim()] ?? String(row?.sportsbook ?? '').trim();

    if (!gameDate || !statType || !sportsbook) {
      continue;
    }

    const existing = byDate.get(gameDate) ?? {
      props: {},
      source: null,
      capturedAt: null,
    };

    existing.props[statType] ??= {};
    existing.props[statType][sportsbook] = {
      line: row.line,
      over: row.over_odds ?? null,
      under: row.under_odds ?? null,
      implied: row.implied ?? null,
    };

    if (getHistoricalSourcePriority(row.source) >= getHistoricalSourcePriority(existing.source)) {
      existing.source = row.source ?? existing.source;
    }

    if (row.captured_at && (!existing.capturedAt || String(row.captured_at) > existing.capturedAt)) {
      existing.capturedAt = String(row.captured_at);
    }

    byDate.set(gameDate, existing);
  }

  return Array.from(byDate.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([game_date, entry]) => ({
      game_date,
      props: entry.props,
      source: entry.source,
      captured_at: entry.capturedAt,
    }));
}

function unwrapHistoricalPropsTree(rawProps: any) {
  if (rawProps && typeof rawProps === 'object' && rawProps.props && typeof rawProps.props === 'object') {
    return rawProps.props;
  }
  return rawProps ?? {};
}

function mergeHistoricalPropsTrees(preferredProps: any, fallbackProps: any) {
  const normalizedFallbackProps = unwrapHistoricalPropsTree(fallbackProps);
  const normalizedPreferredProps = unwrapHistoricalPropsTree(preferredProps);
  const merged: Record<string, any> = {
    ...(normalizedFallbackProps ?? {}),
  };

  Object.entries(normalizedPreferredProps ?? {}).forEach(([statType, preferredBooks]) => {
    const normalizedPreferredBooks = preferredBooks && typeof preferredBooks === 'object'
      ? preferredBooks
      : {};

    merged[statType] = {
      ...(normalizedFallbackProps?.[statType] ?? {}),
      ...normalizedPreferredBooks,
    };
  });

  return merged;
}

function mergeHistoricalOddsRows(preferredRows: any[], fallbackRows: any[]) {
  const byDate = new Map<string, any>();

  for (const row of fallbackRows ?? []) {
    const gameDate = String(row?.game_date ?? '').trim();
    if (!gameDate) continue;
    byDate.set(gameDate, row);
  }

  for (const row of preferredRows ?? []) {
    const gameDate = String(row?.game_date ?? '').trim();
    if (!gameDate) continue;
    const fallbackRow = byDate.get(gameDate);
    if (!fallbackRow) {
      byDate.set(gameDate, row);
      continue;
    }

    byDate.set(gameDate, {
      ...fallbackRow,
      ...row,
      props: mergeHistoricalPropsTrees(row?.props, fallbackRow?.props),
      source: getHistoricalSourcePriority(row?.source) >= getHistoricalSourcePriority(fallbackRow?.source)
        ? row?.source ?? fallbackRow?.source ?? null
        : fallbackRow?.source ?? row?.source ?? null,
      captured_at: row?.captured_at ?? fallbackRow?.captured_at ?? null,
    });
  }

  return Array.from(byDate.values()).sort((a, b) => (
    String(a?.game_date ?? '').localeCompare(String(b?.game_date ?? ''))
  ));
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

async function fetchAllHistoricalPlayerPropsForPlayer(playerId: number) {
  return readCached(
    buildCacheKey(['historical_player_props', playerId]),
    ARCHIVE_CACHE_MS,
    async () => {
      const supabase = getSupabaseAdmin();
      const allRows: any[] = [];

      for (let start = 0; ; start += PAGE_SIZE) {
        const { data, error } = await supabase
          .from('historical_player_props')
          .select(HISTORICAL_PLAYER_PROP_SELECT)
          .eq('player_id', playerId)
          .order('game_date', { ascending: true })
          .order('stat_type', { ascending: true })
          .order('sportsbook', { ascending: true })
          .range(start, start + PAGE_SIZE - 1);

        assertNoError(error, 'historical_player_props');

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

  const [playersRows, propsRows, availabilityRows, lineRows, gamesRows] = await Promise.all([
    fetchPlayers(PLAYER_BASE_SELECT),
    fetchAllPlayerPropsForDates(futureDates, PLAYER_PROP_SELECT, activeSportsbook),
    fetchAllPlayerPropsForDates(futureDates, PLAYER_PROP_AVAIL_SELECT),
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
    availabilityRows,
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

  const [propsRows, availabilityRows, lineMetaRows, gamesRows] = await Promise.all([
    fetchAllPlayerPropsForDates(activeDates, PLAYER_PROP_SELECT, activeSportsbook),
    fetchAllPlayerPropsForDates(activeDates, PLAYER_PROP_AVAIL_SELECT),
    includeLineMovements
      ? fetchLineMovementsForDates(
        activeDates,
        LINE_META_SELECT,
        LINE_META_CACHE_MS,
        'line_movement metadata',
      )
      : Promise.resolve([]),
    fetchGamesForDates(activeDates, SLATE_SELECT),
  ]);

  const nextVersion = serializeLineMovementVersion(lineMetaRows ?? []);

  if (!includeLineMovements) {
    return {
      propsRows,
      availabilityRows,
      gamesRows,
      lineVersion: currentLineVersion,
    };
  }

  if (nextVersion === currentLineVersion) {
    return {
      propsRows,
      availabilityRows,
      gamesRows,
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
    availabilityRows,
    gamesRows,
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
    buildCacheKey([
      'player_detail',
      playerId,
      HISTORICAL_ODDS_LEGACY_FALLBACK ? 'legacy-fallback' : 'normalized-only',
    ]),
    PLAYER_DETAIL_CACHE_MS,
    async () => {
      const supabase = getSupabaseAdmin();
      const detailPromise = supabase.from('players').select(PLAYER_DETAIL_SELECT).eq('id', playerId).maybeSingle();
      const normalizedHistoricalPromise = fetchAllHistoricalPlayerPropsForPlayer(playerId);
      const legacyFallbackPromise = HISTORICAL_ODDS_LEGACY_FALLBACK
        ? supabase.from('historical_odds').select(HISTORICAL_ODDS_SELECT).eq('player_id', playerId)
        : Promise.resolve({ data: [], error: null });

      const [
        { data: detail, error: detailError },
        normalizedHistoricalRows,
        { data: fallbackHistoricalOddsRows, error: fallbackHistoricalOddsError },
      ] = await Promise.all([
        detailPromise,
        normalizedHistoricalPromise,
        legacyFallbackPromise,
      ]);

      assertNoError(detailError, 'player detail');
      if (HISTORICAL_ODDS_LEGACY_FALLBACK) {
        assertNoError(fallbackHistoricalOddsError, 'historical_odds');
      }

      if (!detail) {
        throw new Error('[supabase] player detail: Player not found.');
      }

      const normalizedHistoricalOddsRows = reshapeHistoricalOddsRows(normalizedHistoricalRows ?? []);

      return {
        detail,
        historicalOddsRows: HISTORICAL_ODDS_LEGACY_FALLBACK
          ? mergeHistoricalOddsRows(
            normalizedHistoricalOddsRows,
            fallbackHistoricalOddsRows ?? [],
          )
          : normalizedHistoricalOddsRows,
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

export async function fetchPlayerSportsbookPreviewPayload({
  playerId,
  statType,
  gameDate,
}: {
  playerId: number;
  statType: string;
  gameDate?: string | null;
}) {
  const normalizedDates = gameDate ? [gameDate] : getFastRefreshDates(null);

  return readCached(
    buildCacheKey(['player_props_preview', playerId, statType, normalizedDates.join(',')]),
    PROPS_CACHE_MS,
    async () => {
      const supabase = getSupabaseAdmin();
      let query = supabase
        .from('player_props')
        .select(PLAYER_PROP_SELECT)
        .eq('player_id', playerId)
        .eq('stat_type', statType)
        .in('game_date', normalizedDates)
        .order('game_date', { ascending: true })
        .order('sportsbook', { ascending: true });

      const { data, error } = await query;
      assertNoError(error, 'player_props preview');

      return {
        propsRows: data ?? [],
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

export async function fetchEdgePayload(): Promise<EdgeScorePayload> {
  return readCached(
    buildCacheKey(['edge_scores_current', 'current']),
    EDGE_CACHE_MS,
    async () => {
      const localFallback = readLocalEdgePayloadFallback();

      try {
        const supabase = getSupabaseAdmin();
        const { data, error } = await supabase
          .from('edge_scores_current')
          .select('generated_at, refresh_label, game_dates, summary, top_recommendations, notification')
          .eq('ranking_key', 'current')
          .maybeSingle();

        if (error) {
          if (isMissingRelationError(error, 'edge_scores_current') && localFallback) {
            return localFallback;
          }
          assertNoError(error, 'edge_scores_current');
        }

        if (!data && localFallback) {
          return localFallback;
        }

        const row = (data ?? {}) as {
          generated_at?: string | null;
          refresh_label?: string | null;
          game_dates?: unknown;
          summary?: unknown;
          top_recommendations?: unknown;
          notification?: unknown;
        };

        return {
          generated_at: String(row.generated_at ?? ''),
          refresh_label: String(row.refresh_label ?? 'unknown'),
          game_dates: Array.isArray(row.game_dates) ? row.game_dates : [],
          summary: row.summary && typeof row.summary === 'object' ? row.summary as Record<string, any> : {},
          recommendations: Array.isArray(row.top_recommendations) ? row.top_recommendations : [],
          notification: row.notification && typeof row.notification === 'object' ? row.notification as Record<string, any> : {},
        } satisfies EdgeScorePayload;
      } catch (error) {
        if (localFallback) {
          return localFallback;
        }
        throw error;
      }
    },
  );
}
