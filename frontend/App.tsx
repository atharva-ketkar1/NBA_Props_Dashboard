import React, { startTransition, useState, useEffect, useMemo, useRef } from 'react';
import { Layout } from './components/Layout';
import { Header } from './components/Header';
import { BarChart } from './components/BarChart';
import { DashboardSkeleton } from './components/ui/DashboardSkeleton';
import { ShootingZones } from './components/ShootingZones';
import { ShotTypeAnalysis } from './components/ShotTypeAnalysis';
import { PlayTypeAnalysis } from './components/PlayTypeAnalysis';
import { SimilarPlayers } from './components/SimilarPlayers';
import { AssistZones } from './components/AssistZones';
import { FiltersPanel } from './components/FiltersPanel';
import {
  EdgeScorePayload,
  EdgeScoreRecommendation,
  Player,
  PlayerPropsByDate,
  SimilarPlayerCandidate,
  SportsbookId,
  TeamInjuryReport,
  TeammateInjuryCard,
} from './types';
import { MobileViewSwitcher, MobileView } from './components/MobileViewSwitcher';
import { EdgeBoardPanel } from './components/EdgeBoardPanel';
import { getDashboardDate } from './utils/dashboardDate';
import {
  getResolvedPlayerGameDate,
  materializePlayerForGameDate,
  playerHasAnyProp,
  playerHasSportsbookPropForDate,
} from './utils/propResolution';
import { fetchApiJson } from './utils/network';
import { rankSimilarPlayers } from './utils/similarPlayers';
import {
  fetchDashboardAccess,
  fetchDashboardArchive,
  fetchDashboardBootstrap,
  fetchDashboardBookPreview,
  fetchDashboardEdge,
  fetchDashboardHot,
  fetchDashboardPlayer,
  fetchDashboardSimilar,
} from './utils/dashboardApi';


const STAT_LABELS: Record<string, string> = {
  'Points': 'PTS',
  'Assists': 'AST',
  'Rebounds': 'REB',
  'Threes': 'FG3M',
  'Pts+Ast': 'PTS+AST',
  'Pts+Reb': 'PTS+REB',
  'Reb+Ast': 'REB+AST',
  'Pts+Reb+Ast': 'PTS+REB+AST',
  'Fantasy': 'FAN',
  'Blocks': 'BLK',
  'Steals': 'STL',
  'Turnovers': 'TOV'
};

const EDGE_STAT_TO_TAB: Record<string, string> = {
  PTS: 'Points',
  AST: 'Assists',
  REB: 'Rebounds',
  FG3M: 'Threes',
  'PTS+AST': 'Pts+Ast',
  'PTS+REB': 'Pts+Reb',
  'REB+AST': 'Reb+Ast',
  'PTS+REB+AST': 'Pts+Reb+Ast',
  BLK: 'Blocks',
  STL: 'Steals',
  'STL+BLK': 'Stl+Blk',
  TOV: 'Turnovers',
  PF: 'Fouls',
  FTA: 'FT Attempted',
};

const TAB_ORDER = ['Points', 'Assists', 'Rebounds', 'Threes', 'Pts+Ast', 'Pts+Reb', 'Reb+Ast', 'Pts+Reb+Ast', 'Double Double', 'Triple Double', '1Q Points', '1Q Assists', '1Q Rebounds', '1H Points'];
const DEFAULT_SPORTSBOOK: SportsbookId = 'dk';

function parsePollMs(rawValue: string | undefined, fallbackMs: number) {
  const parsed = Number(rawValue);
  return Number.isFinite(parsed) && parsed >= 60_000 ? parsed : fallbackMs;
}

function parsePositiveInt(rawValue: string | undefined, fallbackValue: number) {
  const parsed = Number(rawValue);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallbackValue;
}

function isDocumentVisible() {
  return typeof document === 'undefined' || document.visibilityState === 'visible';
}

function formatEdgeSummaryTime(value?: string | null) {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;

  return parsed.toISOString();
}

function getLatestIsoTimestamp(values: Array<string | null | undefined>) {
  let latestMs = Number.NEGATIVE_INFINITY;
  let latestValue: string | null = null;

  values.forEach((value) => {
    if (!value) return;
    const parsed = new Date(value);
    const ms = parsed.getTime();
    if (Number.isNaN(ms)) return;
    if (ms > latestMs) {
      latestMs = ms;
      latestValue = parsed.toISOString();
    }
  });

  return latestValue;
}

function getLatestUpdatedAtFromRows(rows: any[]) {
  return getLatestIsoTimestamp((rows ?? []).map((row: any) => row?.updated_at ?? null));
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

type PlayerPropAvailabilityByDate = Record<number, Record<string, Record<string, Record<string, boolean>>>>;
type SlateOpponentByTeamDate = Record<string, string>;
type TeamInjuryReportByTeamDate = Record<string, TeamInjuryReport>;

function buildAvailabilityByDateMap(rows: any[]): PlayerPropAvailabilityByDate {
  const availabilityMap: PlayerPropAvailabilityByDate = {};

  for (const row of rows ?? []) {
    const gameDateKey = row.game_date || '__undated__';
    availabilityMap[row.player_id] ??= {};
    availabilityMap[row.player_id][row.stat_type] ??= {};
    availabilityMap[row.player_id][row.stat_type][row.sportsbook] ??= {};
    availabilityMap[row.player_id][row.stat_type][row.sportsbook][gameDateKey] = true;
  }

  return availabilityMap;
}

function mergeAvailabilityMaps(
  existing: PlayerPropAvailabilityByDate = {},
  incoming: PlayerPropAvailabilityByDate = {},
): PlayerPropAvailabilityByDate {
  const merged: PlayerPropAvailabilityByDate = { ...existing };

  Object.entries(incoming).forEach(([playerId, statMap]) => {
    merged[playerId as unknown as number] ??= {};
    Object.entries(statMap ?? {}).forEach(([statType, sportsbookMap]) => {
      merged[playerId as unknown as number][statType] ??= {};
      Object.entries(sportsbookMap ?? {}).forEach(([sportsbook, dateMap]) => {
        merged[playerId as unknown as number][statType][sportsbook] = {
          ...(merged[playerId as unknown as number][statType][sportsbook] ?? {}),
          ...(dateMap ?? {}),
        };
      });
    });
  });

  return merged;
}

function mergePropsByDateMaps(existing: PlayerPropsByDate = {}, incoming: PlayerPropsByDate = {}): PlayerPropsByDate {
  const merged: PlayerPropsByDate = { ...existing };

  Object.entries(incoming).forEach(([statType, sportsbookMap]) => {
    merged[statType] ??= {};
    Object.entries(sportsbookMap ?? {}).forEach(([sportsbook, datedProps]) => {
      merged[statType][sportsbook] = {
        ...(merged[statType][sportsbook] ?? {}),
        ...(datedProps ?? {}),
      };
    });
  });

  return merged;
}

function flattenIntradayMovements(rows: any[]) {
  return (rows ?? [])
    .flatMap((row: any) => Array.isArray(row?.snapshots) ? row.snapshots : [])
    .sort((a: any, b: any) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
}

function buildGameStatusById(games: any[]) {
  return Object.fromEntries(
    (games ?? [])
      .filter((game: any) => game?.game_id)
      .map((game: any) => [
        String(game.game_id),
        {
          is_live: Boolean(game.is_live),
          is_final: Boolean(game.is_final),
        },
      ]),
  ) as Record<string, { is_live: boolean; is_final: boolean }>;
}

function buildSlateOpponentByTeamDate(games: any[]) {
  const opponentMap: SlateOpponentByTeamDate = {};

  for (const game of games ?? []) {
    const gameDate = String(game?.game_date ?? '').trim();
    const homeTeam = String(game?.home_team_tricode ?? '').trim();
    const awayTeam = String(game?.away_team_tricode ?? '').trim();

    if (!gameDate || !homeTeam || !awayTeam) {
      continue;
    }

    opponentMap[`${gameDate}:${homeTeam}`] = awayTeam;
    opponentMap[`${gameDate}:${awayTeam}`] = homeTeam;
  }

  return opponentMap;
}

function normalizePersonName(value?: string | null) {
  return String(value ?? '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');
}

function formatCompactPlayerName(value?: string | null) {
  const name = String(value ?? '').trim();
  if (!name) {
    return 'N/A';
  }

  const parts = name.split(/\s+/).filter(Boolean);
  if (parts.length < 2) {
    return parts[0] ?? name;
  }

  return `${parts[0][0]}. ${parts.slice(1).join(' ')}`;
}

function getStatValueFromGameLog(game: Record<string, any>, statKey: string) {
  const directValue = game?.[statKey];
  if (directValue !== undefined && directValue !== null && directValue !== '') {
    const parsedDirectValue = Number(directValue);
    return Number.isFinite(parsedDirectValue) ? parsedDirectValue : null;
  }

  if (statKey === 'PTS+REB+AST') {
    return Number(game?.PTS || 0) + Number(game?.REB || 0) + Number(game?.AST || 0);
  }
  if (statKey === 'PTS+REB') {
    return Number(game?.PTS || 0) + Number(game?.REB || 0);
  }
  if (statKey === 'PTS+AST') {
    return Number(game?.PTS || 0) + Number(game?.AST || 0);
  }
  if (statKey === 'REB+AST') {
    return Number(game?.REB || 0) + Number(game?.AST || 0);
  }
  if (statKey === 'STL+BLK') {
    return Number(game?.STL || 0) + Number(game?.BLK || 0);
  }

  return null;
}

function calculateTeammateStatImpact(
  selectedPlayer: Player,
  teammatePlayer: Player | null,
  statKey: string,
  gameCount: number,
) {
  if (!teammatePlayer?.game_log?.length || !selectedPlayer?.game_log?.length) {
    return {
      statImpact: null,
      impactSampleLabel: null,
    };
  }

  const selectedTeam = String(selectedPlayer.team ?? '').trim();
  const selectedLogs = selectedPlayer.game_log
    .slice(0, Math.max(1, gameCount))
    .filter((game) => {
      const teamAbbreviation = String(game?.TEAM_ABBREVIATION ?? selectedTeam).trim();
      return !selectedTeam || teamAbbreviation === selectedTeam;
    });
  const teammateActiveGameIds = new Set(
    teammatePlayer.game_log
      .filter((game) => {
        const teammateTeam = String(game?.TEAM_ABBREVIATION ?? teammatePlayer.team ?? '').trim();
        const gameId = String(game?.GAME_ID ?? '').trim();
        return (
          gameId
          && Number(game?.MIN ?? 0) > 0
          && (!selectedTeam || teammateTeam === selectedTeam)
        );
      })
      .map((game) => String(game.GAME_ID).trim()),
  );

  let teammateOnSum = 0;
  let teammateOnCount = 0;
  let teammateOffSum = 0;
  let teammateOffCount = 0;

  selectedLogs.forEach((game) => {
    const gameId = String(game?.GAME_ID ?? '').trim();
    const statValue = getStatValueFromGameLog(game, statKey);
    if (!gameId || statValue === null) {
      return;
    }

    if (teammateActiveGameIds.has(gameId)) {
      teammateOnSum += statValue;
      teammateOnCount += 1;
    } else {
      teammateOffSum += statValue;
      teammateOffCount += 1;
    }
  });

  if (!teammateOnCount || !teammateOffCount) {
    return {
      statImpact: null,
      impactSampleLabel: `On ${teammateOnCount} / Off ${teammateOffCount}`,
    };
  }

  const teammateOnAvg = teammateOnSum / teammateOnCount;
  const teammateOffAvg = teammateOffSum / teammateOffCount;

  return {
    statImpact: teammateOffAvg - teammateOnAvg,
    impactSampleLabel: `On ${teammateOnCount} / Off ${teammateOffCount}`,
  };
}

function buildTeamInjuryByTeamDate(games: any[]) {
  const injuryMap: TeamInjuryReportByTeamDate = {};

  for (const game of games ?? []) {
    const gameDate = String(game?.game_date ?? '').trim();
    const teams = (
      game?.injury_teams && typeof game.injury_teams === 'object'
        ? game.injury_teams
        : game?.teams && typeof game.teams === 'object'
          ? game.teams
          : {}
    ) as Record<string, any>;

    if (!gameDate) {
      continue;
    }

    Object.values(teams).forEach((teamPayload: any) => {
      const teamTricode = String(teamPayload?.team_tricode ?? '').trim();
      if (!teamTricode) {
        return;
      }

      injuryMap[`${gameDate}:${teamTricode}`] = {
        team_tricode: teamTricode,
        team_name: teamPayload?.team_name ?? null,
        report_status: teamPayload?.report_status ?? null,
        report_timestamp_et: game?.injury_report_timestamp_et ?? null,
        source_generated_at: game?.injury_source_generated_at ?? null,
        updated_at: game?.injury_updated_at ?? null,
        players: Array.isArray(teamPayload?.players) ? teamPayload.players : [],
      };
    });
  }

  return injuryMap;
}

function mergeTeamInjuryByTeamDate(
  existing: TeamInjuryReportByTeamDate = {},
  incoming: TeamInjuryReportByTeamDate = {},
) {
  return {
    ...existing,
    ...incoming,
  };
}

function buildTeammateInjuryCards(
  selectedPlayer: Player | null,
  teamPlayers: Player[],
  teamInjuryReport: TeamInjuryReport | null,
  statKey: string,
  gameCount: number,
) {
  if (!selectedPlayer?.team) {
    return [];
  }

  const reportPlayersByName = new Map(
    (teamInjuryReport?.players ?? [])
      .map((reportPlayer) => {
        const key = normalizePersonName(reportPlayer.player_name ?? reportPlayer.report_player_name);
        return key ? [key, reportPlayer] as const : null;
      })
      .filter((entry): entry is readonly [string, any] => Boolean(entry)),
  );
  const selectedNameKey = normalizePersonName(selectedPlayer.name);
  const seenNameKeys = new Set<string>();
  const cards: TeammateInjuryCard[] = [];
  const submittedReport = teamInjuryReport?.report_status === 'submitted';

  teamPlayers
    .filter((player) => player.team === selectedPlayer.team && player.id !== selectedPlayer.id)
    .forEach((teamPlayer) => {
      const nameKey = normalizePersonName(teamPlayer.name);
      if (!nameKey || seenNameKeys.has(nameKey)) {
        return;
      }

      const reportPlayer = reportPlayersByName.get(nameKey) ?? null;
      const teammateImpact = calculateTeammateStatImpact(
        selectedPlayer,
        teamPlayer,
        statKey,
        gameCount,
      );

      seenNameKeys.add(nameKey);
      cards.push({
        playerId: teamPlayer.id ?? null,
        playerName: teamPlayer.name,
        displayName: formatCompactPlayerName(teamPlayer.name),
        currentStatus: reportPlayer?.current_status ?? (submittedReport ? 'Available' : null),
        reportStatus: teamInjuryReport?.report_status ?? null,
        reason: reportPlayer?.reason ?? null,
        statImpact: teammateImpact.statImpact,
        impactSampleLabel: teammateImpact.impactSampleLabel,
      });
    });

  (teamInjuryReport?.players ?? []).forEach((reportPlayer) => {
    const playerName = String(reportPlayer?.player_name ?? reportPlayer?.report_player_name ?? '').trim();
    const nameKey = normalizePersonName(playerName);
    if (!playerName || !nameKey || nameKey === selectedNameKey || seenNameKeys.has(nameKey)) {
      return;
    }

    seenNameKeys.add(nameKey);
    cards.push({
      playerId: null,
      playerName,
      displayName: formatCompactPlayerName(playerName),
      currentStatus: reportPlayer?.current_status ?? (submittedReport ? 'Available' : null),
      reportStatus: teamInjuryReport?.report_status ?? null,
      reason: reportPlayer?.reason ?? null,
      statImpact: null,
      impactSampleLabel: null,
    });
  });

  const statusPriority: Record<string, number> = {
    Out: 0,
    Doubtful: 1,
    Questionable: 2,
    Probable: 3,
    Available: 4,
  };

  return cards.sort((left, right) => {
    const leftRank = statusPriority[String(left.currentStatus ?? '')] ?? 5;
    const rightRank = statusPriority[String(right.currentStatus ?? '')] ?? 5;
    if (leftRank !== rightRank) {
      return leftRank - rightRank;
    }
    return left.playerName.localeCompare(right.playerName);
  });
}

function resolveSlateOpponent(
  player: Player | null | undefined,
  selectedGameDate: string | null,
  opponentByTeamDate: SlateOpponentByTeamDate,
) {
  if (!player?.team) {
    return null;
  }

  const gameDate = selectedGameDate ?? player.active_game_date ?? getDashboardDate();
  return opponentByTeamDate[`${gameDate}:${player.team}`] ?? null;
}

function mergePropsIntoPlayers(players: Player[], propsRows: any[]) {
  if (!players.length || !(propsRows ?? []).length) {
    return players;
  }

  const incomingPropsByPlayer = buildPropsByDateMap(propsRows);

  return players.map((player) => {
    const incoming = incomingPropsByPlayer[player.id];
    if (!incoming) {
      return player;
    }

    return {
      ...player,
      props_by_date: mergePropsByDateMaps(player.props_by_date, incoming),
    };
  });
}

function buildHistoricalOddsMap(playerId: number, historicalOddsRows: any[]) {
  return Object.fromEntries((historicalOddsRows ?? []).map((row: any) => [
    row.game_date,
    {
      [String(playerId)]: {
        props: row.props,
        source: row.source,
      }
    }
  ]));
}

function hasLoadedPlayerDetail(player?: Player | null) {
  return Boolean(player?.detail_loaded);
}

function App() {
  const [rawData, setRawData] = useState<Player[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [selectedPlayerId, setSelectedPlayerId] = useState<number | null>(null);
  const [selectedGameDate, setSelectedGameDate] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('Points');
  const [activeSportsbook, setActiveSportsbook] = useState<SportsbookId>(DEFAULT_SPORTSBOOK);
  const [customLineValue, setCustomLineValue] = useState<number | null>(null);
  const [isFiltersOpen, setIsFiltersOpen] = useState(false);
  const [activeFilter, setActiveFilter] = useState<string | null>(null);
  const [filterGameCount, setFilterGameCount] = useState<number>(19);
  const [activeSeason, setActiveSeason] = useState<'25/26' | '24/25'>('25/26');
  const [archiveGameLogs, setArchiveGameLogs] = useState<Record<string, any[]>>({});
  const [propsAvailabilityByDate, setPropsAvailabilityByDate] = useState<PlayerPropAvailabilityByDate>({});
  const [isLoadingArchive, setIsLoadingArchive] = useState(false);
  const [mobileView, setMobileView] = useState<MobileView>('graph');
  const [currentSlateTeams, setCurrentSlateTeams] = useState<string[]>([]);
  const [slateOpponentByTeamDate, setSlateOpponentByTeamDate] = useState<SlateOpponentByTeamDate>({});
  const [teamInjuryByTeamDate, setTeamInjuryByTeamDate] = useState<TeamInjuryReportByTeamDate>({});
  const [gameStatusById, setGameStatusById] = useState<Record<string, { is_live: boolean; is_final: boolean }>>({});
  const [similarCandidatesByProp, setSimilarCandidatesByProp] = useState<SimilarPlayerCandidate[]>([]);
  const [similarCandidatesByPosition, setSimilarCandidatesByPosition] = useState<SimilarPlayerCandidate[]>([]);
  const [isSimilarCandidatesLoading, setIsSimilarCandidatesLoading] = useState(false);
  const [similarCandidatesKey, setSimilarCandidatesKey] = useState('');
  const [edgePayload, setEdgePayload] = useState<EdgeScorePayload | null>(null);
  const [isEdgeLoading, setIsEdgeLoading] = useState(true);
  const [isEdgeBoardOpen, setIsEdgeBoardOpen] = useState(false);
  const [dashboardUpdatedAt, setDashboardUpdatedAt] = useState<string | null>(null);
  const [pendingSelection, setPendingSelection] = useState<{
    id: number;
    gameDate: string | null;
    name: string;
    requestId: number;
  } | null>(null);

  const playersWithProps = useMemo(() => {
    if (!rawData) return [];
    const feed = Array.isArray(rawData) ? rawData : [];
    return feed.filter(playerHasAnyProp);
  }, [rawData]);

  const currentPlayer = useMemo(() => {
    if (!playersWithProps.length) {
      return undefined;
    }

    if (selectedPlayerId != null) {
      return playersWithProps.find((player) => player.id === selectedPlayerId)
        ?? playersWithProps[selectedIndex]
        ?? playersWithProps[0];
    }

    return playersWithProps[selectedIndex] ?? playersWithProps[0];
  }, [playersWithProps, selectedIndex, selectedPlayerId]);
  const activeStatKey = useMemo(() => STAT_LABELS[activeTab] || 'PTS', [activeTab]);
  const resolvedSelectedGameDate = useMemo(() => {
    if (!currentPlayer) return null;
    return getResolvedPlayerGameDate(currentPlayer, selectedGameDate);
  }, [currentPlayer, selectedGameDate]);

  useEffect(() => {
    if (!playersWithProps.length) {
      if (selectedPlayerId !== null) {
        setSelectedPlayerId(null);
      }
      return;
    }

    if (selectedPlayerId != null && playersWithProps.some((player) => player.id === selectedPlayerId)) {
      return;
    }

    const fallbackPlayer = playersWithProps[selectedIndex] ?? playersWithProps[0];
    if (fallbackPlayer && fallbackPlayer.id !== selectedPlayerId) {
      setSelectedPlayerId(fallbackPlayer.id);
    }
  }, [playersWithProps, selectedIndex, selectedPlayerId]);

  const handleTabChange = (newTab: string) => {
    setActiveTab(newTab);
    setCustomLineValue(null);
    if (!currentPlayer || !currentPlayer.props) return;

    const statKey = STAT_LABELS[newTab] || 'PTS';
    const hasProp = playerHasSportsbookPropForDate(
      currentPlayer,
      statKey,
      activeSportsbook,
      resolvedSelectedGameDate,
    );

    if (!hasProp) {
      // Find all players on the SAME TEAM as currentPlayer who HAVE the selected prop
      const teamPlayers = playersWithProps.filter(p => p.team === currentPlayer.team);
      const eligiblePlayers = teamPlayers.filter((p) => playerHasSportsbookPropForDate(
        p,
        statKey,
        activeSportsbook,
        resolvedSelectedGameDate,
      ));

      if (eligiblePlayers.length > 0) {
        // Sort eligible players by their season average for the requested stat, descending
        eligiblePlayers.sort((a, b) => {
          const statA = (a.stats && a.stats[statKey]) ? Number(a.stats[statKey]) : 0;
          const statB = (b.stats && b.stats[statKey]) ? Number(b.stats[statKey]) : 0;
          return statB - statA;
        });

        const bestPlayer = eligiblePlayers[0];
        selectPlayerForView(bestPlayer.id, resolvedSelectedGameDate);
      } else {
        // If NO ONE on the team has the prop, fall back to the old logic of finding a valid tab for the current player
        const firstValidTab = TAB_ORDER.find(tab => {
          const k = STAT_LABELS[tab];
          return playerHasSportsbookPropForDate(
            currentPlayer,
            k,
            activeSportsbook,
            resolvedSelectedGameDate,
          );
        });
        if (firstValidTab) {
          setActiveTab(firstValidTab);
        }
      }
    }
  };

  // ──────────────────────────────────────────────────────────────
  // Data fetching
  // Set VITE_USE_DB=true in Vercel to fetch from the server-side Vercel API.
  // Set VITE_USE_DB=false (or omit) to fall back to master_feed.json.
  // ──────────────────────────────────────────────────────────────
  const USE_DB = import.meta.env.VITE_USE_DB === 'true';
  const FULL_DB_POLL_MS = parsePollMs(import.meta.env.VITE_FULL_DB_POLL_MS, 60 * 60 * 1000);
  const HOT_DATA_POLL_MS = parsePollMs(import.meta.env.VITE_HOT_DATA_POLL_MS, 2 * 60 * 1000);
  const SIMILAR_PREFETCH_PROP_LIMIT = parsePositiveInt(import.meta.env.VITE_SIMILAR_PREFETCH_PROP_LIMIT, 2);
  const SIMILAR_PREFETCH_POSITION_LIMIT = parsePositiveInt(import.meta.env.VITE_SIMILAR_PREFETCH_POSITION_LIMIT, 3);
  const [isPageVisible, setIsPageVisible] = useState<boolean>(() => isDocumentVisible());
  const lineMovementVersionRef = useRef('');
  const rawDataRef = useRef<Player[]>([]);
  const selectionAnchorRef = useRef<{ playerId: number | null; gameDate: string | null }>({
    playerId: null,
    gameDate: null,
  });
  const playerDetailRequestsRef = useRef(new Map<number, Promise<void>>());
  const archiveRequestsRef = useRef(new Map<string, Promise<void>>());
  const selectionRequestRef = useRef(0);
  const accessCacheRef = useRef(new Map<string, {
    archiveToken: string | null;
    expiresAt: number;
    playerToken: string;
  }>());

  useEffect(() => {
    rawDataRef.current = rawData;
  }, [rawData]);

  useEffect(() => {
    selectionAnchorRef.current = {
      playerId: currentPlayer?.id ?? null,
      gameDate: resolvedSelectedGameDate ?? selectedGameDate ?? null,
    };
  }, [currentPlayer?.id, resolvedSelectedGameDate, selectedGameDate]);

  /** Reconstruct the Player[] shape from API rows. */
  function mergeFeedFromDB(
    players: any[],
    props: any[],
    nextGameStatusById: Record<string, { is_live: boolean; is_final: boolean }> = {},
  ): Player[] {
    const propsByDateMap = buildPropsByDateMap(props ?? []);

    return (players ?? []).map((p: any) => materializePlayerForGameDate({
      id: p.id,
      name: p.name,
      team: p.team,
      position: p.position,
      stats: p.stats ?? {},
      game_log: p.game_log ?? [],   // null until lazy-loaded
      props: {},
      props_by_date: propsByDateMap[p.id] ?? {},
      game_status_by_id: nextGameStatusById,
      historical_odds: {},                       // null until lazy-loaded on player select
      intraday_movements: [],
      detail_loaded: false,
      // Zone fields — null until lazy-loaded
      shooting_zones: null,
      assist_zones: null,
      opp_def_zones: null,
      opp_def_zones_positional: null,
      opp_assist_zones: null,
      opp_assist_zones_positional: null,
      shot_type_analysis: null,
      play_type_analysis: p.play_type_analysis ?? [],
    }, getDashboardDate()));
  }

  useEffect(() => {
    if (!USE_DB) return undefined;

    const handleVisibilityChange = () => {
      setIsPageVisible(isDocumentVisible());
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleVisibilityChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleVisibilityChange);
    };
  }, [USE_DB]);

  // 1. Base feed fetch
  useEffect(() => {
    if (USE_DB) {
      let cancelled = false;

      const fetchDbSnapshot = async (isInitial = false) => {
        try {
          if (!isInitial && !isDocumentVisible()) {
            return;
          }

          const [snapshot, edgeSnapshot] = await Promise.all([
            fetchDashboardBootstrap(activeSportsbook),
            fetchDashboardEdge().catch((error) => {
              console.error('[api] edge bootstrap error:', error);
              return null;
            }),
          ]);

          if (cancelled) return;

          const nextGameStatusById = buildGameStatusById(snapshot.gamesRows ?? []);
          const nextOpponentByTeamDate = buildSlateOpponentByTeamDate(snapshot.gamesRows ?? []);
          const nextTeamInjuryByTeamDate = buildTeamInjuryByTeamDate(snapshot.gamesRows ?? []);
          const mergedFeed = mergeFeedFromDB(
            snapshot.playersRows ?? [],
            snapshot.propsRows ?? [],
            nextGameStatusById,
          );
          const availabilityMap = buildAvailabilityByDateMap(snapshot.availabilityRows ?? []);
          const slateTeams = Array.from(new Set((snapshot.gamesRows ?? []).flatMap((g: any) => [g.home_team_tricode, g.away_team_tricode]).filter(Boolean)));
          setCurrentSlateTeams(slateTeams);
          setSlateOpponentByTeamDate((prev) => ({ ...prev, ...nextOpponentByTeamDate }));
          setTeamInjuryByTeamDate((prev) => mergeTeamInjuryByTeamDate(prev, nextTeamInjuryByTeamDate));
          setGameStatusById((prev) => ({ ...prev, ...nextGameStatusById }));
          setPropsAvailabilityByDate(prev => mergeAvailabilityMaps(prev, availabilityMap));

          const intradayMovements = isInitial ? flattenIntradayMovements(snapshot.lineRows ?? []) : null;
          if (isInitial) {
            lineMovementVersionRef.current = snapshot.lineVersion ?? '';
          }
          if (edgeSnapshot) {
            setEdgePayload(edgeSnapshot);
          } else if (isInitial) {
            setEdgePayload(null);
          }
          setDashboardUpdatedAt((current) => getLatestIsoTimestamp([
            current,
            getLatestUpdatedAtFromRows(snapshot.propsRows ?? []),
            edgeSnapshot?.generated_at ?? null,
          ]));
          if (isInitial) {
            setIsEdgeLoading(false);
          }

          const selectionAnchor = selectionAnchorRef.current;

          setRawData(prev => {
            const prevMap = new Map((prev ?? []).map(p => [String(p.id), p]));
            const nextRawData = mergedFeed.map(p => {
              const existing = prevMap.get(String(p.id));
              return {
                ...p,
                ...existing,
                id: p.id,
                name: p.name,
                team: p.team,
                position: p.position,
                stats: p.stats,
                props: p.props,
                props_by_date: mergePropsByDateMaps(existing?.props_by_date, p.props_by_date),
                game_status_by_id: {
                  ...(existing?.game_status_by_id ?? {}),
                  ...nextGameStatusById,
                },
                play_type_analysis: existing?.play_type_analysis ?? p.play_type_analysis ?? [],
                intraday_movements: intradayMovements ?? existing?.intraday_movements ?? [],
                detail_loaded: existing?.detail_loaded ?? p.detail_loaded ?? false,
              };
            });

            const nextPlayersWithProps = nextRawData.filter(playerHasAnyProp);
            if (!nextPlayersWithProps.length) {
              setSelectedIndex(0);
              setSelectedPlayerId(null);
              return nextRawData;
            }

            const anchoredIndex = selectionAnchor.playerId != null
              ? nextPlayersWithProps.findIndex((player) => player.id === selectionAnchor.playerId)
              : -1;

            if (anchoredIndex >= 0) {
              setSelectedIndex(anchoredIndex);
              setSelectedPlayerId(nextPlayersWithProps[anchoredIndex].id);
            } else {
              if (selectionAnchor.gameDate) {
                setSelectedGameDate(null);
              }
              const nextIndex = selectedPlayerId != null
                ? nextPlayersWithProps.findIndex((player) => player.id === selectedPlayerId)
                : -1;
              const resolvedIndex = nextIndex >= 0
                ? nextIndex
                : (selectedIndex < nextPlayersWithProps.length ? selectedIndex : 0);
              setSelectedIndex(resolvedIndex);
              setSelectedPlayerId(nextPlayersWithProps[resolvedIndex].id);
            }

            return nextRawData;
          });

          if (isInitial) {
            setLoading(false);
          }
        } catch (err) {
          if (cancelled) return;
          console.error('Supabase fetch failed:', err);
          if (isInitial) {
            setLoading(false);
            setIsEdgeLoading(false);
          }
        }
      };

      void fetchDbSnapshot(true);
      const intervalId = window.setInterval(() => fetchDbSnapshot(false), FULL_DB_POLL_MS);
      return () => {
        cancelled = true;
        window.clearInterval(intervalId);
      };
    } else {
      // ── JSON fallback (original behavior, unchanged) ──
      Promise.all([
        fetchApiJson<Player[]>('/data/current/master_feed.json'),
        fetchApiJson<Record<string, any>>('/data/archive/historical_odds.json').catch(() => ({})),
        fetchApiJson<{ snapshots?: any[] }>('/data/current/line_movements_today.json').catch(() => ({ snapshots: [] })),
        fetchApiJson<any[]>('/data/current/nba_dashboard_games.json').catch(() => ([])),
        fetchApiJson<{ games?: any[] }>('/data/current/nba_injury_report.json').catch(() => ({ games: [] })),
        fetchApiJson<EdgeScorePayload>('/data/current/edge_scores_top15.json').catch(() => null),
      ])
        .then(([masterFeed, historicalOdds, lineMovements, games, injuryReport, edgeSnapshot]) => {
          const today = getDashboardDate();
          const nextGames = Array.isArray(games) ? games : [];
          const nextGameStatusById = buildGameStatusById(Array.isArray(games) ? games : []);
          const slateTeams = Array.from(new Set(nextGames
            .filter((g: any) => g?.game_date === today)
            .flatMap((g: any) => [g.home_team_tricode, g.away_team_tricode])
            .filter(Boolean)));
          setCurrentSlateTeams(slateTeams);
          setSlateOpponentByTeamDate(buildSlateOpponentByTeamDate(nextGames));
          setTeamInjuryByTeamDate(buildTeamInjuryByTeamDate(injuryReport?.games ?? []));
          setGameStatusById(nextGameStatusById);
          const enhancedFeed = (Array.isArray(masterFeed) ? masterFeed : []).map((player: Player) => ({
            ...materializePlayerForGameDate({
              ...player,
              game_status_by_id: nextGameStatusById,
            }, getDashboardDate()),
            game_status_by_id: nextGameStatusById,
            historical_odds: historicalOdds,
            intraday_movements: lineMovements?.snapshots || [],
            detail_loaded: true,
          }));
          setRawData(enhancedFeed);
          setEdgePayload(edgeSnapshot);
          setDashboardUpdatedAt(getLatestIsoTimestamp([
            edgeSnapshot?.generated_at ?? null,
          ]));
          setIsEdgeLoading(false);
          setLoading(false);
        })
        .catch(err => {
          console.error('Failed to load data:', err);
          setIsEdgeLoading(false);
          setLoading(false);
        });
    }
  }, [USE_DB, FULL_DB_POLL_MS, activeSportsbook]);

  // 1b. Hot refresh for live lines + history metadata
  useEffect(() => {
    if (!USE_DB || !isPageVisible || loading) return undefined;

    let cancelled = false;

    const fetchHotSnapshot = async () => {
      try {
        if (!isPageVisible) {
          return;
        }

        const [hotSnapshot, edgeSnapshot] = await Promise.all([
          fetchDashboardHot(
            resolvedSelectedGameDate ?? selectedGameDate,
            lineMovementVersionRef.current,
            activeSportsbook,
          ),
          fetchDashboardEdge().catch((error) => {
            console.error('[api] edge hot refresh error:', error);
            return null;
          }),
        ]);

        if (cancelled) return;

        if ((hotSnapshot.propsRows ?? []).length > 0) {
          setRawData(prev => mergePropsIntoPlayers(prev, hotSnapshot.propsRows ?? []));
        }

        if ((hotSnapshot.availabilityRows ?? []).length > 0) {
          const availabilityMap = buildAvailabilityByDateMap(hotSnapshot.availabilityRows ?? []);
          setPropsAvailabilityByDate(prev => mergeAvailabilityMaps(prev, availabilityMap));
        }

        if ((hotSnapshot.gamesRows ?? []).length > 0) {
          const nextGameStatusById = buildGameStatusById(hotSnapshot.gamesRows ?? []);
          const nextOpponentByTeamDate = buildSlateOpponentByTeamDate(hotSnapshot.gamesRows ?? []);
          const nextTeamInjuryByTeamDate = buildTeamInjuryByTeamDate(hotSnapshot.gamesRows ?? []);
          setGameStatusById((prev) => ({ ...prev, ...nextGameStatusById }));
          setSlateOpponentByTeamDate((prev) => ({ ...prev, ...nextOpponentByTeamDate }));
          setTeamInjuryByTeamDate((prev) => mergeTeamInjuryByTeamDate(prev, nextTeamInjuryByTeamDate));
          setRawData(prev => prev.map((player) => ({
            ...player,
            game_status_by_id: {
              ...(player.game_status_by_id ?? {}),
              ...nextGameStatusById,
            },
          })));
        }

        if (!Object.prototype.hasOwnProperty.call(hotSnapshot, 'lineRows')) {
          return;
        }

        lineMovementVersionRef.current = hotSnapshot.lineVersion ?? '';
        const intradayMovements = flattenIntradayMovements(hotSnapshot.lineRows ?? []);
        setRawData(prev => prev.map((player) => ({
          ...player,
          intraday_movements: intradayMovements,
        })));
        if (edgeSnapshot) {
          setEdgePayload(edgeSnapshot);
        }
        setDashboardUpdatedAt((current) => getLatestIsoTimestamp([
          current,
          getLatestUpdatedAtFromRows(hotSnapshot.propsRows ?? []),
          edgeSnapshot?.generated_at ?? null,
        ]));
      } catch (err) {
        if (!cancelled) {
          console.error('Supabase hot refresh failed:', err);
        }
      }
    };

    void fetchHotSnapshot();
    const intervalId = window.setInterval(() => fetchHotSnapshot(), HOT_DATA_POLL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [USE_DB, HOT_DATA_POLL_MS, isPageVisible, loading, resolvedSelectedGameDate, selectedGameDate, activeSportsbook]);


  const [isInitialized, setIsInitialized] = useState(false);

  const ensurePlayerAccess = async (playerId: number, archiveSeason?: string | null) => {
    const cacheKey = `${playerId}:${archiveSeason ?? ''}`;
    const cached = accessCacheRef.current.get(cacheKey);

    if (cached && cached.expiresAt > Date.now() + 15_000) {
      return cached;
    }

    const access = await fetchDashboardAccess(playerId, archiveSeason);
    accessCacheRef.current.set(cacheKey, access);
    return access;
  };

  const ensurePlayerDetailLoaded = async (playerId: number) => {
    const cachedPlayer = rawDataRef.current.find((player) => player.id === playerId);
    if (hasLoadedPlayerDetail(cachedPlayer)) {
      return;
    }

    const existingRequest = playerDetailRequestsRef.current.get(playerId);
    if (existingRequest) {
      return existingRequest;
    }

    const request = ensurePlayerAccess(playerId)
      .then((access) => fetchDashboardPlayer(access.playerToken))
      .then(({ detail, historicalOddsRows }) => {
        const historicalOddsMap = buildHistoricalOddsMap(playerId, historicalOddsRows);

        setRawData(prev => prev.map((player) => (
          player.id === playerId
            ? { ...player, ...detail, historical_odds: historicalOddsMap, detail_loaded: true }
            : player
        )));
      })
      .finally(() => {
        playerDetailRequestsRef.current.delete(playerId);
      });

    playerDetailRequestsRef.current.set(playerId, request);
    return request;
  };

  const ensureArchiveLoaded = async (playerId: number, season: string) => {
    if (archiveGameLogs[String(playerId)]) {
      return;
    }

    const cacheKey = `${playerId}:${season}`;
    const existingRequest = archiveRequestsRef.current.get(cacheKey);
    if (existingRequest) {
      return existingRequest;
    }

    const request = ensurePlayerAccess(playerId, season)
      .then((access) => {
        if (!access.archiveToken) {
          throw new Error('Missing archive access token.');
        }

        return fetchDashboardArchive(access.archiveToken);
      })
      .then(({ gameLog }) => {
        setArchiveGameLogs(prev => ({
          ...prev,
          [playerId]: gameLog || []
        }));
      })
      .finally(() => {
        archiveRequestsRef.current.delete(cacheKey);
      });

    archiveRequestsRef.current.set(cacheKey, request);
    return request;
  };

  const preparePlayerForSelection = async (playerId: number, season?: string | null) => {
    await ensurePlayerDetailLoaded(playerId);
    if (season) {
      await ensureArchiveLoaded(playerId, season);
    }
  };

  const selectPlayerForView = (id: number, gameDate?: string | null) => {
    const index = playersWithProps.findIndex((player) => player.id === id);
    if (index === -1) {
      return;
    }

    const nextGameDate = gameDate ?? null;
    if (currentPlayer?.id === id && resolvedSelectedGameDate === nextGameDate) {
      selectionRequestRef.current += 1;
      setPendingSelection(null);
      setCustomLineValue(null);
      return;
    }

    const nextPlayer = playersWithProps[index];
    const shouldPreloadArchive = USE_DB && activeSeason === '24/25';
    const needsDetailLoad = USE_DB && !hasLoadedPlayerDetail(nextPlayer);
    const needsArchiveLoad = shouldPreloadArchive && !archiveGameLogs[String(id)];
    const requestId = selectionRequestRef.current + 1;
    selectionRequestRef.current = requestId;

    setSelectedIndex(index);
    setSelectedPlayerId(id);
    setSelectedGameDate(nextGameDate);
    setCustomLineValue(null);

    if (!needsDetailLoad && !needsArchiveLoad) {
      setPendingSelection(null);
      return;
    }

    setPendingSelection({
      id,
      gameDate: nextGameDate,
      name: nextPlayer.name,
      requestId,
    });

    void preparePlayerForSelection(id, shouldPreloadArchive ? '2024-25' : null)
      .then(() => {
        if (selectionRequestRef.current === requestId) {
          setPendingSelection((current) => (
            current?.requestId === requestId ? null : current
          ));
        }
      })
      .catch((error) => {
        console.error('[api] preselect player load error:', error);
        if (selectionRequestRef.current === requestId) {
          setPendingSelection((current) => (
            current?.requestId === requestId ? null : current
          ));
        }
      });
  };

  const handleSelectEdgeRecommendation = (recommendation: EdgeScoreRecommendation) => {
    const nextTab = EDGE_STAT_TO_TAB[recommendation.stat_type];

    if (nextTab) {
      setActiveTab(nextTab);
    }

    if (
      recommendation.sportsbook === 'dk'
      || recommendation.sportsbook === 'fd'
      || recommendation.sportsbook === 'pp'
    ) {
      setActiveSportsbook(recommendation.sportsbook);
    }

    setCustomLineValue(null);
    setIsEdgeBoardOpen(false);
    selectPlayerForView(recommendation.player_id, recommendation.game_date ?? null);
  };

  // 2. Archive fetching when season filter changes
  useEffect(() => {
    // Only fetch if 24/25 is active, and the current player exists, and we haven't fetched their logs yet
    if (activeSeason === '24/25' && currentPlayer && !archiveGameLogs[currentPlayer.id]) {
      setIsLoadingArchive(true);

      const playerId = currentPlayer.id;

      if (USE_DB) {
        ensureArchiveLoaded(playerId, '2024-25')
          .catch((error) => {
            console.error('[api] archive error:', error);
          })
          .finally(() => {
            setIsLoadingArchive(false);
          });
      } else {
        // Fallback logic could go here if `USE_DB` goes false, 
        // but since we are exclusively doing DB limits, we'll keep it empty.
        setIsLoadingArchive(false);
      }
    }
  }, [activeSeason, currentPlayer, archiveGameLogs, USE_DB]);

  const displayPlayer = useMemo(() => {
    if (!currentPlayer) return null;
    const materializedPlayer = materializePlayerForGameDate(currentPlayer, resolvedSelectedGameDate);
    if (activeSeason === '24/25') {
      return {
        ...materializedPlayer,
        game_log: archiveGameLogs[String(currentPlayer.id)] || []
      } as Player;
    }
    return materializedPlayer;
  }, [currentPlayer, resolvedSelectedGameDate, activeSeason, archiveGameLogs]);
  const displayPlayerOpponent = useMemo(() => (
    resolveSlateOpponent(displayPlayer, resolvedSelectedGameDate, slateOpponentByTeamDate)
  ), [displayPlayer, resolvedSelectedGameDate, slateOpponentByTeamDate]);
  const displayPlayerAvailability = useMemo(() => {
    if (!displayPlayer) {
      return {};
    }

    return propsAvailabilityByDate[displayPlayer.id] ?? {};
  }, [displayPlayer, propsAvailabilityByDate]);
  const maxAvailableHistoricalGames = displayPlayer?.game_log?.length ?? 0;
  const defaultFilterGameCount = maxAvailableHistoricalGames > 0
    ? Math.min(19, maxAvailableHistoricalGames)
    : 19;
  const defaultHistoricalGameCount = maxAvailableHistoricalGames > 0
    ? Math.min(29, maxAvailableHistoricalGames)
    : 29;
  const effectiveFilterGameCount = maxAvailableHistoricalGames > 0
    ? Math.min(filterGameCount, maxAvailableHistoricalGames)
    : filterGameCount;
  const appliedHistoricalGameCount = isFiltersOpen
    ? effectiveFilterGameCount
    : defaultHistoricalGameCount;
  const displayTeamInjuryReport = useMemo(() => {
    if (!displayPlayer?.team) {
      return null;
    }

    const gameDate = resolvedSelectedGameDate ?? displayPlayer.active_game_date ?? getDashboardDate();
    return teamInjuryByTeamDate[`${gameDate}:${displayPlayer.team}`] ?? null;
  }, [displayPlayer, resolvedSelectedGameDate, teamInjuryByTeamDate]);
  const displayTeammateInjuryCards = useMemo(() => (
    buildTeammateInjuryCards(
      displayPlayer,
      rawData,
      displayTeamInjuryReport,
      activeStatKey,
      effectiveFilterGameCount,
    )
  ), [
    displayPlayer,
    rawData,
    displayTeamInjuryReport,
    activeStatKey,
    effectiveFilterGameCount,
  ]);
  const activeEdgeRecommendationKey = useMemo(() => {
    if (!displayPlayer || !edgePayload?.recommendations?.length) {
      return null;
    }

    const currentGameDate = resolvedSelectedGameDate ?? null;

    return edgePayload.recommendations.find((recommendation) => (
      recommendation.player_id === displayPlayer.id
      && recommendation.stat_type === activeStatKey
      && recommendation.sportsbook === activeSportsbook
      && (recommendation.game_date ?? null) === currentGameDate
    ))?.recommendation_key ?? null;
  }, [
    displayPlayer,
    edgePayload,
    resolvedSelectedGameDate,
    activeStatKey,
    activeSportsbook,
  ]);
  const edgeBoardSummary = useMemo(() => {
    const leader = edgePayload?.recommendations?.[0] ?? null;

    return {
      recommendationCount: edgePayload?.recommendations?.length ?? 0,
      changeCount: Number(edgePayload?.notification?.change_count ?? 0),
      leaderLabel: leader ? `${leader.player_name} ${leader.pick_label} ${leader.line.toFixed(1)}` : null,
      updatedAt: formatEdgeSummaryTime(edgePayload?.generated_at),
    };
  }, [edgePayload]);

  useEffect(() => {
    if (!USE_DB || !displayPlayer || !activeStatKey) {
      return undefined;
    }

    let cancelled = false;

    const loadBookPreview = async () => {
      try {
        const preview = await fetchDashboardBookPreview(
          displayPlayer.id,
          activeStatKey,
          resolvedSelectedGameDate,
        );

        if (cancelled || !(preview.propsRows ?? []).length) {
          return;
        }

        setRawData((prev) => mergePropsIntoPlayers(prev, preview.propsRows ?? []));
        const availabilityMap = buildAvailabilityByDateMap(preview.propsRows ?? []);
        setPropsAvailabilityByDate((prev) => mergeAvailabilityMaps(prev, availabilityMap));
      } catch (error) {
        if (!cancelled) {
          console.error('[api] sportsbook preview error:', error);
        }
      }
    };

    void loadBookPreview();

    return () => {
      cancelled = true;
    };
  }, [USE_DB, displayPlayer?.id, activeStatKey, resolvedSelectedGameDate]);
  const isSelectionPending = Boolean(pendingSelection);
  const similarRankingKey = `${displayPlayer?.id ?? 'none'}:${activeSeason}:${activeTab}:${activeSportsbook}:${resolvedSelectedGameDate ?? ''}`;
  const areSimilarCandidatesCurrent = similarCandidatesKey === similarRankingKey;
  const readySimilarCandidatesByProp = areSimilarCandidatesCurrent ? similarCandidatesByProp : [];
  const readySimilarCandidatesByPosition = areSimilarCandidatesCurrent ? similarCandidatesByPosition : [];
  const shouldShowSimilarLoading = activeSeason === '25/26'
    && Boolean(displayPlayer)
    && (!areSimilarCandidatesCurrent || isSimilarCandidatesLoading);

  useEffect(() => {
    if (!isFiltersOpen) {
      setFilterGameCount(defaultFilterGameCount);
      return;
    }

    if (maxAvailableHistoricalGames > 0 && filterGameCount > maxAvailableHistoricalGames) {
      setFilterGameCount(maxAvailableHistoricalGames);
    }
  }, [isFiltersOpen, defaultFilterGameCount, filterGameCount, maxAvailableHistoricalGames]);

  // 3. Smart Default Selection (Run once on data load)
  useEffect(() => {
    if (playersWithProps.length > 0 && !isInitialized) {
      const eligiblePlayers = currentSlateTeams.length > 0
        ? playersWithProps.filter(p => currentSlateTeams.includes(p.team))
        : playersWithProps;

      const pool = eligiblePlayers.length > 0 ? eligiblePlayers : playersWithProps;
      let bestIndex = 0;
      let maxPts = -1;

      pool.forEach((p) => {
        const pts = (p.stats && p.stats.PTS) ? Number(p.stats.PTS) : 0;
        if (pts > maxPts) {
          maxPts = pts;
          bestIndex = playersWithProps.findIndex(candidate => candidate.id === p.id);
        }
      });

      setSelectedIndex(bestIndex);
      setSelectedPlayerId(playersWithProps[bestIndex]?.id ?? null);
      setIsInitialized(true);
    }
  }, [playersWithProps, currentSlateTeams, isInitialized]);

  // 4. Lazy-load heavy JSONB + historical odds when player changes (DB mode only)
  useEffect(() => {
    if (!USE_DB || !currentPlayer) return;
    const playerId = currentPlayer.id;
    if (hasLoadedPlayerDetail(currentPlayer)) {
      return;
    }

    ensurePlayerDetailLoaded(playerId)
      .catch((error) => {
        console.error('[api] player detail error:', error);
      });
  }, [selectedPlayerId, USE_DB, currentPlayer]);

  useEffect(() => {
    if (
      !USE_DB
      || !isFiltersOpen
      || activeSeason !== '25/26'
      || !displayPlayer?.team
      || !rawDataRef.current.length
    ) {
      return undefined;
    }

    const teammateIds = rawDataRef.current
      .filter((player) => (
        player.team === displayPlayer.team
        && player.id !== displayPlayer.id
        && !hasLoadedPlayerDetail(player)
      ))
      .map((player) => player.id);

    if (!teammateIds.length) {
      return undefined;
    }

    let cancelled = false;

    const loadTeammateDetails = async () => {
      for (const teammateId of teammateIds) {
        if (cancelled) {
          return;
        }

        try {
          await ensurePlayerDetailLoaded(teammateId);
        } catch (error) {
          if (!cancelled) {
            console.error('[api] teammate detail error:', error);
          }
        }
      }
    };

    const timeoutId = window.setTimeout(() => {
      void loadTeammateDetails();
    }, 120);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [USE_DB, isFiltersOpen, activeSeason, displayPlayer?.id, displayPlayer?.team]);

  useEffect(() => {
    let cancelled = false;

    if (activeSeason !== '25/26' || !displayPlayer) {
      setSimilarCandidatesByProp([]);
      setSimilarCandidatesByPosition([]);
      setSimilarCandidatesKey('');
      setIsSimilarCandidatesLoading(false);
      return undefined;
    }

    setIsSimilarCandidatesLoading(true);
    setSimilarCandidatesByProp([]);
    setSimilarCandidatesByPosition([]);

    let timeoutId = 0;
    const frameId = window.requestAnimationFrame(() => {
      timeoutId = window.setTimeout(() => {
        const loadSimilarCandidates = async () => {
          try {
            const payload = USE_DB
              ? await fetchDashboardSimilar(
                displayPlayer.id,
                activeTab,
                activeSportsbook,
                resolvedSelectedGameDate,
              )
              : {
                similarCandidatesByProp: rankSimilarPlayers({
                  player: displayPlayer,
                  players: playersWithProps,
                  activeTab,
                  activeSportsbook,
                  selectedGameDate: resolvedSelectedGameDate,
                  mode: 'prop',
                  limit: 12,
                }),
                similarCandidatesByPosition: rankSimilarPlayers({
                  player: displayPlayer,
                  players: playersWithProps,
                  activeTab,
                  activeSportsbook,
                  selectedGameDate: resolvedSelectedGameDate,
                  mode: 'position',
                  limit: 14,
                }),
              };

            if (cancelled) {
              return;
            }

            startTransition(() => {
              setSimilarCandidatesByProp(payload.similarCandidatesByProp);
              setSimilarCandidatesByPosition(payload.similarCandidatesByPosition);
              setSimilarCandidatesKey(similarRankingKey);
              setIsSimilarCandidatesLoading(false);
            });
          } catch (error) {
            if (!cancelled) {
              console.error('[api] similar players error:', error);
              startTransition(() => {
                setSimilarCandidatesByProp([]);
                setSimilarCandidatesByPosition([]);
                setSimilarCandidatesKey(similarRankingKey);
                setIsSimilarCandidatesLoading(false);
              });
            }
          }
        };

        void loadSimilarCandidates();
      }, 0);
    });

    return () => {
      cancelled = true;
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(timeoutId);
    };
  }, [similarRankingKey]);

  useEffect(() => {
    const similarPrefetchIds = Array.from(new Set(
      [
        ...readySimilarCandidatesByProp.slice(0, SIMILAR_PREFETCH_PROP_LIMIT),
        ...readySimilarCandidatesByPosition.slice(0, SIMILAR_PREFETCH_POSITION_LIMIT),
      ]
        .map((candidate) => candidate.id),
    )).filter((id) => id !== displayPlayer?.id);

    if (
      !USE_DB
      || !isPageVisible
      || activeSeason !== '25/26'
      || !displayPlayer
      || similarPrefetchIds.length === 0
    ) {
      return;
    }

    let cancelled = false;

    // Fetch these sequentially so the signed session cookie is established before
    // we ask for more player-scoped tokens.
    const prefetchSimilarDetails = async () => {
      try {
        await ensurePlayerDetailLoaded(displayPlayer.id);
      } catch (error) {
        console.error('[api] selected player detail error:', error);
      }

      for (const playerId of similarPrefetchIds) {
        if (cancelled) {
          return;
        }

        try {
          await ensurePlayerDetailLoaded(playerId);
        } catch (error) {
          if (!cancelled) {
            console.error('[api] similar player detail error:', error);
          }
        }
      }
    };

    const timeoutId = window.setTimeout(() => {
      void prefetchSimilarDetails();
    }, 120);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [
    USE_DB,
    isPageVisible,
    activeSeason,
    displayPlayer?.id,
    readySimilarCandidatesByProp,
    readySimilarCandidatesByPosition,
    SIMILAR_PREFETCH_PROP_LIMIT,
    SIMILAR_PREFETCH_POSITION_LIMIT,
  ]);

  const topNavProps = {
    dashboardUpdatedAt,
    edgeSummary: edgeBoardSummary,
    isEdgeBoardOpen,
    onOpenEdgeBoard: () => setIsEdgeBoardOpen(true),
  };


  if (loading) {
    return (
      <Layout sidebarProps={{ players: [], activePlayerId: null, onSelectPlayer: () => { } }} topNavProps={topNavProps}>
        <DashboardSkeleton />
      </Layout>
    );
  }

  if (playersWithProps.length === 0) {
    return (
      <Layout sidebarProps={{ players: [], activePlayerId: null, onSelectPlayer: () => { } }} topNavProps={topNavProps}>
        <div className="flex items-center justify-center h-full text-white">
          No active props found.
        </div>
      </Layout>
    );
  }

  return (
    <Layout topNavProps={topNavProps} sidebarProps={{
      players: playersWithProps,
      activePlayerId: displayPlayer?.id,
      activeGameDate: resolvedSelectedGameDate,
      pendingPlayerId: pendingSelection?.id,
      pendingGameDate: pendingSelection?.gameDate,
      activeSportsbook: activeSportsbook,
      propsAvailabilityByDate: propsAvailabilityByDate,
      onSelectPlayer: selectPlayerForView,
      onPrefetchPlayer: (id: number) => {
        if (!USE_DB) {
          return;
        }

        void ensurePlayerDetailLoaded(id).catch(() => {});
      },
      activeTab: activeTab,
      onTabChange: handleTabChange
    }}>
      <div className="flex flex-col h-full w-full gap-4 relative pb-6">

        {/* Top Row: Merged Top Section + Filters Panel side-by-side */}
        <div className="flex w-full relative z-20">

          {/* Merged Top Section (Header + Chart) */}
          <div className={`flex-1 bg-bgElevation0 rounded-xl shadow-lg animate-in fade-in duration-500 min-w-0 flex flex-col relative z-20 transition-opacity duration-200 ${isSelectionPending ? 'opacity-90' : 'opacity-100'}`}>
            {isSelectionPending && pendingSelection && (
              <div className="absolute top-4 right-4 z-30 flex items-center gap-2 rounded-full border border-borderMedium bg-bgElevation1/95 px-3 py-1.5 shadow-lg pointer-events-none">
                <div className="w-3.5 h-3.5 rounded-full border-2 border-borderMedium border-t-white animate-spin" aria-hidden="true" />
                <span className="text-xs font-medium text-white whitespace-nowrap">
                  Loading {pendingSelection.name}...
                </span>
              </div>
            )}
            <Header
              player={displayPlayer}
              playerAvailabilityByDate={displayPlayerAvailability}
              activeTab={activeTab}
              onTabChange={handleTabChange}
              activeSportsbook={activeSportsbook}
              onSportsbookChange={(sb) => {
                setActiveSportsbook(sb);
                setCustomLineValue(null);
              }}
              activeGameDate={resolvedSelectedGameDate}
              customLine={customLineValue}
              onToggleFilters={() => setIsFiltersOpen(!isFiltersOpen)}
              isFiltersOpen={isFiltersOpen}
              historicalGameCount={appliedHistoricalGameCount}
              mobileView={mobileView}
              onMobileViewChange={setMobileView}
            />

            {/* Subtle separator removed */}
            <div className={`p-0 ${mobileView !== 'graph' ? 'hidden md:block' : ''}`}>
              <BarChart
                player={displayPlayer}
                activeTab={activeTab}
                activeSportsbook={activeSportsbook}
                customLine={customLineValue}
                onCustomLineChange={setCustomLineValue}
                activeFilterOverlay={activeFilter}
                isFiltersOpen={isFiltersOpen}
                historicalGameCount={appliedHistoricalGameCount}
                activeSeason={activeSeason}
              />
            </div>
          </div>

          {/* The Split Screen Filter Panel (Responsive logic applied) */}
          <div className={`transition-all duration-300 ease-in-out shrink-0 absolute lg:relative right-0 top-0 bottom-0 z-50 lg:z-auto ${isFiltersOpen ? 'w-[320px] ml-4 opacity-100 pointer-events-auto' : 'w-0 ml-0 opacity-0 pointer-events-none'}`}>
            <div className={`absolute inset-0 bg-bgElevation0 rounded-xl shadow-2xl lg:shadow-lg border border-borderMedium/40 overflow-hidden transition-opacity duration-300 ${isFiltersOpen ? 'opacity-100' : 'opacity-0 border-none'}`}>
              <FiltersPanel
                isOpen={isFiltersOpen}
                onClose={() => setIsFiltersOpen(false)}
                activeFilter={activeFilter}
                onFilterChange={setActiveFilter}
                player={displayPlayer}
                teammateInjuryCards={displayTeammateInjuryCards}
                teamInjuryReport={displayTeamInjuryReport}
                gameCount={effectiveFilterGameCount}
                onGameCountChange={(count) => {
                  const maxGameCount = maxAvailableHistoricalGames || count;
                  setFilterGameCount(Math.max(1, Math.min(maxGameCount, count)));
                }}
                activeSeason={activeSeason}
                onSeasonChange={(s) => {
                  if (s === '25/26' || s === '24/25') {
                    setActiveSeason(s as any);
                  }
                }}
              />
            </div>
          </div>
        </div>

        {/* Backdrop for mobile filters */}
        {isFiltersOpen && (
          <div
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[45] lg:hidden"
            onClick={() => setIsFiltersOpen(false)}
          />
        )}

        {/* Bottom Grid Section */}
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-10 gap-4">

          {['Rebounds', '1Q Rebounds', 'Double Double', 'Triple Double', 'Blocks', 'Steals', 'Turnovers', 'Fantasy'].includes(activeTab) ? (
            <div className={`xl:col-span-6 flex flex-col gap-4 h-full ${mobileView !== 'similar' ? 'hidden md:flex' : ''}`}>
              <SimilarPlayers
                player={displayPlayer}
                players={playersWithProps}
                activeTab={activeTab}
                activeSportsbook={activeSportsbook}
                activeSeason={activeSeason}
                targetOpponent={displayPlayerOpponent}
                isLoadingCandidates={shouldShowSimilarLoading}
                similarCandidatesByProp={readySimilarCandidatesByProp}
                similarCandidatesByPosition={readySimilarCandidatesByPosition}
              />
            </div>
          ) : (
            <>
              {/* Left Column: Shooting/Assist Zones + Play Types */}
              <div className={`xl:col-span-4 flex flex-col gap-4 h-full ${['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab)
                  ? mobileView !== 'assists' ? 'hidden md:flex' : ''
                  : mobileView !== 'shooting' ? 'hidden md:flex' : ''
                }`}>
                {['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab) ? (
                  <AssistZones player={displayPlayer} />
                ) : (
                  <ShootingZones player={displayPlayer} />
                )}
                {!['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab) && (
                  <div className="flex-1 min-h-0 hidden md:block">
                    <PlayTypeAnalysis playTypes={displayPlayer?.play_type_analysis ?? []} />
                  </div>
                )}
              </div>

              {/* Right Column: Shot Types + Play Types (types tab) + Similar */}
              <div className="xl:col-span-6 flex flex-col gap-4 h-full">
                {!['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab) && (
                  <div className={`${mobileView !== 'shooting' ? 'hidden md:block' : ''}`}>
                    <ShotTypeAnalysis shotTypes={(() => {
                      if (!displayPlayer?.shot_type_analysis) return [];
                      const sta = displayPlayer.shot_type_analysis;
                      const p = sta.player || {};
                      const d = sta.opp_def || {};
                      const cs = p.catch_and_shoot;
                      const pu = p.pull_up;
                      const lt10 = p.less_than_10_ft;
                      if (!cs && !pu && !lt10) return undefined;
                      return [
                        {
                          type: 'C&S',
                          percentage: cs?.percentage || 0,
                          attempts: Math.round(cs?.points || 0),
                          frequency: cs?.percentage || 0,
                          width: cs?.percentage || 33.3,
                          rank: d.catch_and_shoot?.rank
                        },
                        {
                          type: '< 10 ft',
                          percentage: lt10?.percentage || 0,
                          attempts: Math.round(lt10?.points || 0),
                          frequency: lt10?.percentage || 0,
                          width: lt10?.percentage || 33.3,
                          rank: d.less_than_10_ft?.rank
                        },
                        {
                          type: 'Pull Up',
                          percentage: pu?.percentage || 0,
                          attempts: Math.round(pu?.points || 0),
                          frequency: pu?.percentage || 0,
                          width: pu?.percentage || 33.3,
                          rank: d.pull_up?.rank
                        }
                      ];
                    })()} />
                  </div>
                )}
                {!['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab) && (
                  <div className={`md:hidden ${mobileView !== 'types' ? 'hidden' : ''}`}>
                    <PlayTypeAnalysis playTypes={displayPlayer?.play_type_analysis ?? []} />
                  </div>
                )}
                <div className={`flex-1 min-h-0 xl:col-span-12 w-full h-full ${mobileView !== 'similar' ? 'hidden md:flex' : ''}`}>
                  <SimilarPlayers
                    player={displayPlayer}
                    players={playersWithProps}
                    activeTab={activeTab}
                    activeSportsbook={activeSportsbook}
                    activeSeason={activeSeason}
                    targetOpponent={displayPlayerOpponent}
                    isLoadingCandidates={shouldShowSimilarLoading}
                    similarCandidatesByProp={readySimilarCandidatesByProp}
                    similarCandidatesByPosition={readySimilarCandidatesByPosition}
                  />
                </div>
              </div>
            </>
          )}

        </div>

        <EdgeBoardPanel
          isOpen={isEdgeBoardOpen}
          payload={edgePayload}
          isLoading={isEdgeLoading}
          activeRecommendationKey={activeEdgeRecommendationKey}
          onClose={() => setIsEdgeBoardOpen(false)}
          onSelectRecommendation={handleSelectEdgeRecommendation}
        />

      </div>
    </Layout>
  );
}

export default App;
