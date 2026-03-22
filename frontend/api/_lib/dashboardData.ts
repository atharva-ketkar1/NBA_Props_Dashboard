import { getSupabaseAdmin } from './supabaseAdmin.js';
import {
  buildFutureDates,
  getDashboardDate,
  getFastRefreshDates,
} from './dashboardDate.js';

const PLAYER_PROP_SELECT = 'player_id, stat_type, sportsbook, line, over_odds, under_odds, implied, game_date, game_id, updated_at';
const PLAYER_BASE_SELECT = 'id, name, team, position, stats, play_type_analysis';
const PLAYER_DETAIL_SELECT = 'game_log, shooting_zones, assist_zones, opp_def_zones, opp_def_zones_positional, opp_assist_zones, opp_assist_zones_positional, shot_type_analysis';
const HISTORICAL_ODDS_SELECT = 'game_date, props, source';
const INITIAL_LINE_SELECT = 'game_date, snapshots, updated_at';
const LINE_META_SELECT = 'game_date, updated_at';
const SLATE_SELECT = 'home_team_tricode, away_team_tricode';
const PAGE_SIZE = 1000;

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

export async function fetchBootstrapPayload() {
  const supabase = getSupabaseAdmin();
  const today = getDashboardDate();
  const futureDates = buildFutureDates(today, 14);
  const fastRefreshDates = getFastRefreshDates(null);

  const [
    { data: playersRows, error: playersError },
    propsRows,
    { data: lineRows, error: lineError },
    { data: gamesRows, error: gamesError },
  ] = await Promise.all([
    supabase.from('players').select(PLAYER_BASE_SELECT),
    fetchAllPlayerPropsForDates(futureDates),
    supabase.from('line_movements').select(INITIAL_LINE_SELECT).in('game_date', fastRefreshDates),
    supabase.from('games').select(SLATE_SELECT).eq('game_date', today),
  ]);

  assertNoError(playersError, 'players');
  assertNoError(lineError, 'line_movements');
  assertNoError(gamesError, 'games');

  return {
    playersRows: playersRows ?? [],
    propsRows,
    gamesRows: gamesRows ?? [],
    lineRows: lineRows ?? [],
    lineVersion: serializeLineMovementVersion(lineRows ?? []),
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

  return {
    detail,
    historicalOddsRows: historicalOddsRows ?? [],
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
