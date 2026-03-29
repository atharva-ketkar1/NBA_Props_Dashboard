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
import { Player, PlayerPropsByDate, SimilarPlayerCandidate, SportsbookId } from './types';
import { MobileViewSwitcher, MobileView } from './components/MobileViewSwitcher';
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
  const [selectedGameDate, setSelectedGameDate] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('Points');
  const [activeSportsbook, setActiveSportsbook] = useState<SportsbookId>(DEFAULT_SPORTSBOOK);
  const [customLineValue, setCustomLineValue] = useState<number | null>(null);
  const [isFiltersOpen, setIsFiltersOpen] = useState(false);
  const [activeFilter, setActiveFilter] = useState<string | null>(null);
  const [filterGameCount, setFilterGameCount] = useState<number>(19);
  const [activeSeason, setActiveSeason] = useState<'25/26' | '24/25'>('25/26');
  const [archiveGameLogs, setArchiveGameLogs] = useState<Record<string, any[]>>({});
  const [isLoadingArchive, setIsLoadingArchive] = useState(false);
  const [mobileView, setMobileView] = useState<MobileView>('graph');
  const [currentSlateTeams, setCurrentSlateTeams] = useState<string[]>([]);
  const [similarCandidatesByProp, setSimilarCandidatesByProp] = useState<SimilarPlayerCandidate[]>([]);
  const [similarCandidatesByPosition, setSimilarCandidatesByPosition] = useState<SimilarPlayerCandidate[]>([]);
  const [isSimilarCandidatesLoading, setIsSimilarCandidatesLoading] = useState(false);
  const [similarCandidatesKey, setSimilarCandidatesKey] = useState('');
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

  const currentPlayer = playersWithProps[selectedIndex];
  const resolvedSelectedGameDate = useMemo(() => {
    if (!currentPlayer) return null;
    return getResolvedPlayerGameDate(currentPlayer, selectedGameDate);
  }, [currentPlayer, selectedGameDate]);

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
  const FULL_DB_POLL_MS = parsePollMs(import.meta.env.VITE_FULL_DB_POLL_MS, 30 * 60 * 1000);
  const HOT_DATA_POLL_MS = parsePollMs(import.meta.env.VITE_HOT_DATA_POLL_MS, 5 * 60 * 1000);
  const SIMILAR_PREFETCH_PROP_LIMIT = parsePositiveInt(import.meta.env.VITE_SIMILAR_PREFETCH_PROP_LIMIT, 2);
  const SIMILAR_PREFETCH_POSITION_LIMIT = parsePositiveInt(import.meta.env.VITE_SIMILAR_PREFETCH_POSITION_LIMIT, 3);
  const [isPageVisible, setIsPageVisible] = useState<boolean>(() => isDocumentVisible());
  const lineMovementVersionRef = useRef('');
  const rawDataRef = useRef<Player[]>([]);
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

  /** Reconstruct the Player[] shape from API rows. */
  function mergeFeedFromDB(
    players: any[],
    props: any[],
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

          const snapshot = await fetchDashboardBootstrap(DEFAULT_SPORTSBOOK);

          if (cancelled) return;

          const mergedFeed = mergeFeedFromDB(snapshot.playersRows ?? [], snapshot.propsRows ?? []);
          const slateTeams = Array.from(new Set((snapshot.gamesRows ?? []).flatMap((g: any) => [g.home_team_tricode, g.away_team_tricode]).filter(Boolean)));
          setCurrentSlateTeams(slateTeams);

          const intradayMovements = isInitial ? flattenIntradayMovements(snapshot.lineRows ?? []) : null;
          if (isInitial) {
            lineMovementVersionRef.current = snapshot.lineVersion ?? '';
          }

          setRawData(prev => {
            const prevMap = new Map((prev ?? []).map(p => [String(p.id), p]));
            return mergedFeed.map(p => {
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
                play_type_analysis: existing?.play_type_analysis ?? p.play_type_analysis ?? [],
                intraday_movements: intradayMovements ?? existing?.intraday_movements ?? [],
                detail_loaded: existing?.detail_loaded ?? p.detail_loaded ?? false,
              };
            });
          });

          if (isInitial) {
            setLoading(false);
          }
        } catch (err) {
          if (cancelled) return;
          console.error('Supabase fetch failed:', err);
          if (isInitial) {
            setLoading(false);
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
      ])
        .then(([masterFeed, historicalOdds, lineMovements, games]) => {
          const today = getDashboardDate();
          const slateTeams = Array.from(new Set((Array.isArray(games) ? games : [])
            .filter((g: any) => g?.game_date === today)
            .flatMap((g: any) => [g.home_team_tricode, g.away_team_tricode])
            .filter(Boolean)));
          setCurrentSlateTeams(slateTeams);
          const enhancedFeed = (Array.isArray(masterFeed) ? masterFeed : []).map((player: Player) => ({
            ...materializePlayerForGameDate(player, getDashboardDate()),
            historical_odds: historicalOdds,
            intraday_movements: lineMovements?.snapshots || [],
            detail_loaded: true,
          }));
          setRawData(enhancedFeed);
          setLoading(false);
        })
        .catch(err => {
          console.error('Failed to load data:', err);
          setLoading(false);
        });
    }
  }, [USE_DB, FULL_DB_POLL_MS]);

  // 1b. Hot refresh for live lines + history metadata
  useEffect(() => {
    if (!USE_DB || !isPageVisible || loading) return undefined;

    let cancelled = false;

    const fetchHotSnapshot = async () => {
      try {
        if (!isPageVisible) {
          return;
        }

        const hotSnapshot = await fetchDashboardHot(
          resolvedSelectedGameDate ?? selectedGameDate,
          lineMovementVersionRef.current,
          activeSportsbook,
        );

        if (cancelled) return;

        if ((hotSnapshot.propsRows ?? []).length > 0) {
          setRawData(prev => mergePropsIntoPlayers(prev, hotSnapshot.propsRows ?? []));
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
  }, [selectedIndex, USE_DB, currentPlayer]);

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


  if (loading) {
    return (
      <Layout sidebarProps={{ players: [], activePlayerId: null, onSelectPlayer: () => { } }}>
        <DashboardSkeleton />
      </Layout>
    );
  }

  if (playersWithProps.length === 0) {
    return (
      <Layout sidebarProps={{ players: [], activePlayerId: null, onSelectPlayer: () => { } }}>
        <div className="flex items-center justify-center h-full text-white">
          No active props found.
        </div>
      </Layout>
    );
  }

  return (
    <Layout sidebarProps={{
      players: playersWithProps,
      activePlayerId: displayPlayer?.id,
      activeGameDate: resolvedSelectedGameDate,
      pendingPlayerId: pendingSelection?.id,
      pendingGameDate: pendingSelection?.gameDate,
      activeSportsbook: activeSportsbook,
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
              activeTab={activeTab}
              onTabChange={handleTabChange}
              activeSportsbook={activeSportsbook}
              onSportsbookChange={(sb) => {
                setActiveSportsbook(sb);
                setCustomLineValue(null);
              }}
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
                    isLoadingCandidates={shouldShowSimilarLoading}
                    similarCandidatesByProp={readySimilarCandidatesByProp}
                    similarCandidatesByPosition={readySimilarCandidatesByPosition}
                  />
                </div>
              </div>
            </>
          )}

        </div>

      </div>
    </Layout>
  );
}

export default App;
