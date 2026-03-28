import { getSupabaseAdmin } from './supabaseAdmin.js';
import {
  buildFutureDates,
  getDashboardDate,
  getFastRefreshDates,
} from './dashboardDate.js';
import { rankSimilarPlayers } from '../../utils/similarPlayers.js';
import { playerHasAnyProp } from '../../utils/propResolution.js';
import type { Player, PlayerPropsByDate, SimilarPlayerCandidate } from '../../types.js';

const PLAYER_PROP_SELECT = 'player_id, stat_type, sportsbook, line, over_odds, under_odds, implied, game_date, game_id, updated_at';
const PLAYER_BASE_SELECT = 'id, name, team, position, stats';
const PLAYER_CHART_SELECT = 'game_log';
const PLAYER_DETAIL_SELECT = 'game_log, shooting_zones, assist_zones, opp_def_zones, opp_def_zones_positional, opp_assist_zones, opp_assist_zones_positional, shot_type_analysis, play_type_analysis';
const PLAYER_SIMILAR_SELECT = 'id, name, team, position, stats, play_type_analysis, shot_type_analysis';
const HISTORICAL_ODDS_SELECT = 'game_date, props, source';
const INITIAL_LINE_SELECT = 'game_date, snapshots, updated_at';
const LINE_META_SELECT = 'game_date, updated_at';
const LINE_FALLBACK_SELECT = 'game_date, snapshots';
const SLATE_SELECT = 'home_team_tricode, away_team_tricode';
const PAGE_SIZE = 1000;
const PLAYER_ID_CHUNK_SIZE = 200;

function assertNoError(error: { message?: string } | null, context: string) {
  if (error) {
    throw new Error(`[supabase] ${context}: ${error.message ?? 'Unknown error'}`);
  }
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

function normalizeGameDateKey(value: unknown) {
  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();
  const match = trimmed.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : null;
}

function normalizePropsTree(value: any) {
  if (!value || typeof value !== 'object') {
    return {};
  }

  if (value.props && typeof value.props === 'object') {
    return value.props;
  }

  return value;
}

function mergeHistoricalPropTrees(primaryValue: any, fallbackValue: any) {
  const primary = normalizePropsTree(primaryValue);
  const fallback = normalizePropsTree(fallbackValue);
  const merged: Record<string, any> = { ...primary };
  let changed = false;

  for (const [statKey, fallbackBooks] of Object.entries(fallback)) {
    if (!fallbackBooks || typeof fallbackBooks !== 'object') {
      continue;
    }

    const existingBooks = merged[statKey];
    if (!existingBooks || typeof existingBooks !== 'object') {
      merged[statKey] = fallbackBooks;
      changed = true;
      continue;
    }

    const mergedBooks = { ...existingBooks };
    let statChanged = false;

    for (const [bookKey, fallbackLine] of Object.entries(fallbackBooks as Record<string, any>)) {
      if (!fallbackLine || typeof fallbackLine !== 'object') {
        continue;
      }

      const existingLine = mergedBooks[bookKey];
      if (!existingLine || typeof existingLine !== 'object') {
        mergedBooks[bookKey] = fallbackLine;
        statChanged = true;
        continue;
      }

      const nextLine = {
        ...fallbackLine,
        ...existingLine,
      };

      if (JSON.stringify(nextLine) !== JSON.stringify(existingLine)) {
        mergedBooks[bookKey] = nextLine;
        statChanged = true;
      }
    }

    if (statChanged) {
      merged[statKey] = mergedBooks;
      changed = true;
    }
  }

  return changed ? merged : primary;
}

function getSnapshotPlayerData(snapshot: any, playerId: number) {
  const players = snapshot?.players;
  if (!players || typeof players !== 'object') {
    return null;
  }

  return players[String(playerId)] ?? players[playerId] ?? null;
}

function extractFallbackLineMovementProps(lineRows: any[], playerId: number) {
  const byDate = new Map<string, { game_date: string; props: Record<string, any>; source: string }>();

  for (const row of lineRows ?? []) {
    const gameDate = normalizeGameDateKey(row?.game_date);
    if (!gameDate) {
      continue;
    }

    const snapshots = Array.isArray(row?.snapshots) ? row.snapshots : [];
    let latestMatch: { props: Record<string, any>; source: string } | null = null;
    let preGameMatch: { props: Record<string, any>; source: string } | null = null;

    for (let index = snapshots.length - 1; index >= 0; index -= 1) {
      const snapshot = snapshots[index];
      const playerData = getSnapshotPlayerData(snapshot, playerId);
      const props = normalizePropsTree(playerData?.props);
      if (!Object.keys(props).length) {
        continue;
      }

      const candidate = {
        props,
        source: snapshot?.label === 'pre_game' ? 'pre_game_snapshot_fallback' : 'line_movements_fallback',
      };

      if (!latestMatch) {
        latestMatch = candidate;
      }

      if (snapshot?.label === 'pre_game') {
        preGameMatch = candidate;
        break;
      }
    }

    const chosen = preGameMatch ?? latestMatch;
    if (!chosen) {
      continue;
    }

    byDate.set(gameDate, {
      game_date: gameDate,
      props: chosen.props,
      source: chosen.source,
    });
  }

  return byDate;
}

function mergeHistoricalOddsRows(
  historicalOddsRows: any[],
  lineMovementRows: any[],
  playerId: number,
) {
  const mergedByDate = new Map<string, any>();

  for (const row of historicalOddsRows ?? []) {
    const gameDate = normalizeGameDateKey(row?.game_date);
    if (!gameDate) {
      continue;
    }

    mergedByDate.set(gameDate, {
      ...row,
      game_date: gameDate,
      props: normalizePropsTree(row?.props),
    });
  }

  const fallbackByDate = extractFallbackLineMovementProps(lineMovementRows, playerId);
  for (const [gameDate, fallbackRow] of fallbackByDate.entries()) {
    const existingRow = mergedByDate.get(gameDate);
    if (!existingRow) {
      mergedByDate.set(gameDate, fallbackRow);
      continue;
    }

    mergedByDate.set(gameDate, {
      ...existingRow,
      props: mergeHistoricalPropTrees(existingRow.props, fallbackRow.props),
      source: existingRow.source ?? fallbackRow.source,
    });
  }

  return Array.from(mergedByDate.values()).sort((left, right) => (
    String(left?.game_date ?? '').localeCompare(String(right?.game_date ?? ''))
  ));
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

async function fetchAllPlayerPropsForDates(gameDates: string[], selectClause = PLAYER_PROP_SELECT) {
  const supabase = getSupabaseAdmin();
  const allRows: any[] = [];

  for (let start = 0; ; start += PAGE_SIZE) {
    const { data, error } = await supabase
      .from('player_props')
      .select(selectClause)
      .in('game_date', gameDates)
      .order('game_date', { ascending: true })
      .order('player_id', { ascending: true })
      .order('stat_type', { ascending: true })
      .order('sportsbook', { ascending: true })
      .range(start, start + PAGE_SIZE - 1);

    assertNoError(error, 'player_props');

    if (!data?.length) {
      return allRows;
    }

    allRows.push(...data);

    if (data.length < PAGE_SIZE) {
      return allRows;
    }
  }
}

async function fetchPlayersByIds(playerIds: number[], selectClause = PLAYER_BASE_SELECT) {
  const supabase = getSupabaseAdmin();
  const allRows: any[] = [];

  for (let start = 0; start < playerIds.length; start += PLAYER_ID_CHUNK_SIZE) {
    const chunk = playerIds.slice(start, start + PLAYER_ID_CHUNK_SIZE);
    const { data, error } = await supabase
      .from('players')
      .select(selectClause)
      .in('id', chunk);

    assertNoError(error, 'players');
    if (data?.length) {
      allRows.push(...data);
    }
  }

  return allRows;
}

export async function fetchBootstrapPayload() {
  const supabase = getSupabaseAdmin();
  const today = getDashboardDate();
  const activePropDates = getFastRefreshDates(null);

  const [
    propsRows,
    { data: lineMetaRows, error: lineError },
    { data: gamesRows, error: gamesError },
  ] = await Promise.all([
    fetchAllPlayerPropsForDates(activePropDates),
    supabase.from('line_movements').select(LINE_META_SELECT).in('game_date', activePropDates),
    supabase.from('games').select(SLATE_SELECT).eq('game_date', today),
  ]);

  assertNoError(lineError, 'line_movements');
  assertNoError(gamesError, 'games');

  const playerIds = Array.from(new Set(
    (propsRows ?? [])
      .map((row: any) => Number(row?.player_id))
      .filter((playerId) => Number.isInteger(playerId) && playerId > 0),
  ));
  const playersRows = playerIds.length > 0
    ? await fetchPlayersByIds(playerIds, PLAYER_BASE_SELECT)
    : [];

  return {
    playersRows: (playersRows ?? []).map((row: any) => ({
      ...row,
      play_type_analysis: undefined,
    })),
    propsRows,
    gamesRows: gamesRows ?? [],
    lineRows: [],
    lineVersion: serializeLineMovementVersion(lineMetaRows ?? []),
  };
}

export async function fetchHotPayload(selectedDate: string | null, currentLineVersion: string) {
  const supabase = getSupabaseAdmin();
  const activeDates = getFastRefreshDates(selectedDate);

  const [
    propsRows,
    { data: lineMetaRows, error: lineMetaError },
  ] = await Promise.all([
    fetchAllPlayerPropsForDates(activeDates),
    supabase.from('line_movements').select(LINE_META_SELECT).in('game_date', activeDates),
  ]);

  assertNoError(lineMetaError, 'line_movement metadata');

  const nextVersion = serializeLineMovementVersion(lineMetaRows ?? []);

  if (nextVersion === currentLineVersion) {
    return {
      propsRows,
      lineVersion: nextVersion,
    };
  }

  const { data: lineRows, error: lineRowsError } = await supabase
    .from('line_movements')
    .select(INITIAL_LINE_SELECT)
    .in('game_date', activeDates);

  assertNoError(lineRowsError, 'line_movements');

  return {
    propsRows,
    lineVersion: nextVersion,
    lineRows: lineRows ?? [],
  };
}

export async function fetchGamesPayload(dates: string[]) {
  const supabase = getSupabaseAdmin();
  const { data, error } = await supabase
    .from('games')
    .select('*')
    .in('game_date', dates);

  assertNoError(error, 'games');

  return {
    games: data ?? [],
  };
}

export async function fetchPlayerPayload(playerId: number) {
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

  const playerDetail = detail as Record<string, any>;
  const recentGameDates = Array.from(new Set(
    (playerDetail.game_log ?? [])
      .map((game: any) => normalizeGameDateKey(game?.GAME_DATE))
      .filter(Boolean),
  )).slice(0, 45) as string[];

  let mergedHistoricalOddsRows: any[] = (historicalOddsRows ?? []) as any[];

  if (recentGameDates.length > 0) {
    const { data: lineMovementRows, error: lineMovementError } = await supabase
      .from('line_movements')
      .select(LINE_FALLBACK_SELECT)
      .in('game_date', recentGameDates);

    assertNoError(lineMovementError, 'player line movement fallback');
    mergedHistoricalOddsRows = mergeHistoricalOddsRows(historicalOddsRows ?? [], lineMovementRows ?? [], playerId);
  }

  return {
    detail: playerDetail,
    historicalOddsRows: mergedHistoricalOddsRows,
  };
}

export async function fetchPlayerChartPayload(playerId: number) {
  const supabase = getSupabaseAdmin();
  const { data: detail, error } = await supabase
    .from('players')
    .select(PLAYER_CHART_SELECT)
    .eq('id', playerId)
    .maybeSingle();

  assertNoError(error, 'player chart');

  if (!detail) {
    throw new Error('[supabase] player chart: Player not found.');
  }

  return {
    detail: detail as Record<string, any>,
  };
}

export async function fetchSimilarCandidatesPayload({
  activeSportsbook,
  activeTab,
  playerId,
  selectedGameDate,
}: {
  activeSportsbook: 'dk' | 'fd' | 'mgm' | 'cz';
  activeTab: string;
  playerId: number;
  selectedGameDate?: string | null;
}) {
  const supabase = getSupabaseAdmin();
  const today = getDashboardDate();
  const propDates = selectedGameDate ? [selectedGameDate] : buildFutureDates(today, 14);

  const [
    { data: playersRows, error: playersError },
    propsRows,
  ] = await Promise.all([
    supabase.from('players').select(PLAYER_SIMILAR_SELECT),
    fetchAllPlayerPropsForDates(propDates),
  ]);

  assertNoError(playersError, 'similar players');

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
}

export async function fetchArchivePayload(playerId: number, season: string) {
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
}
