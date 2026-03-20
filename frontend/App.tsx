import React, { useState, useEffect, useMemo, useRef } from 'react';
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
import { Player, PlayerPropsByDate } from './types';
import { MobileViewSwitcher, MobileView } from './components/MobileViewSwitcher';
import { getDashboardDate, getDashboardScheduleDates } from './utils/dashboardDate';
import {
  getResolvedPlayerGameDate,
  materializePlayerForGameDate,
  playerHasAnyProp,
  playerHasPropForDate,
} from './utils/propResolution';


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

const PLAYER_PROP_SELECT = 'player_id, stat_type, sportsbook, line, over_odds, under_odds, implied, game_date, game_id, updated_at';

function parsePollMs(rawValue: string | undefined, fallbackMs: number) {
  const parsed = Number(rawValue);
  return Number.isFinite(parsed) && parsed >= 60_000 ? parsed : fallbackMs;
}

function isDocumentVisible() {
  return typeof document === 'undefined' || document.visibilityState === 'visible';
}

function buildFutureDates(startDate: string, days: number) {
  return Array.from({ length: days }, (_, i) => {
    const d = new Date(startDate);
    d.setDate(d.getDate() + i);
    return d.toISOString().split('T')[0];
  });
}

function getFastRefreshDates(selectedDate?: string | null) {
  const [today, tomorrow] = getDashboardScheduleDates();
  const dates = new Set<string>([today, tomorrow].filter(Boolean));
  if (selectedDate) {
    dates.add(selectedDate);
  }
  return Array.from(dates);
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

function serializeLineMovementVersion(rows: any[]) {
  return (rows ?? [])
    .map((row: any) => `${row.game_date}:${row.updated_at ?? ''}`)
    .sort()
    .join('|');
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

async function fetchAllPlayerPropsForDates(supabase: any, gameDates: string[], selectClause = PLAYER_PROP_SELECT) {
  const pageSize = 1000;
  const allRows: any[] = [];

  for (let start = 0; ; start += pageSize) {
    const { data, error } = await supabase
      .from('player_props')
      .select(selectClause)
      .in('game_date', gameDates)
      .order('game_date', { ascending: true })
      .order('player_id', { ascending: true })
      .order('stat_type', { ascending: true })
      .order('sportsbook', { ascending: true })
      .range(start, start + pageSize - 1);

    if (error) {
      return { data: allRows, error };
    }

    if (!data?.length) {
      return { data: allRows, error: null };
    }

    allRows.push(...data);

    if (data.length < pageSize) {
      return { data: allRows, error: null };
    }
  }
}

function App() {
  const [rawData, setRawData] = useState<Player[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [selectedGameDate, setSelectedGameDate] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('Points');
  const [activeSportsbook, setActiveSportsbook] = useState<'dk' | 'fd' | 'mgm' | 'cz'>('dk');
  const [customLineValue, setCustomLineValue] = useState<number | null>(null);
  const [isFiltersOpen, setIsFiltersOpen] = useState(false);
  const [activeFilter, setActiveFilter] = useState<string | null>(null);
  const [filterGameCount, setFilterGameCount] = useState<number>(19);
  const [activeSeason, setActiveSeason] = useState<'25/26' | '24/25'>('25/26');
  const [archiveGameLogs, setArchiveGameLogs] = useState<Record<string, any[]>>({});
  const [isLoadingArchive, setIsLoadingArchive] = useState(false);
  const [mobileView, setMobileView] = useState<MobileView>('graph');
  const [currentSlateTeams, setCurrentSlateTeams] = useState<string[]>([]);

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
    const hasProp = playerHasPropForDate(currentPlayer, statKey, resolvedSelectedGameDate);

    if (!hasProp) {
      // Find all players on the SAME TEAM as currentPlayer who HAVE the selected prop
      const teamPlayers = playersWithProps.filter(p => p.team === currentPlayer.team);
      const eligiblePlayers = teamPlayers.filter(p => playerHasPropForDate(p, statKey, resolvedSelectedGameDate));

      if (eligiblePlayers.length > 0) {
        // Sort eligible players by their season average for the requested stat, descending
        eligiblePlayers.sort((a, b) => {
          const statA = (a.stats && a.stats[statKey]) ? Number(a.stats[statKey]) : 0;
          const statB = (b.stats && b.stats[statKey]) ? Number(b.stats[statKey]) : 0;
          return statB - statA;
        });

        const bestPlayer = eligiblePlayers[0];
        const newIndex = playersWithProps.findIndex(p => p.id === bestPlayer.id);
        if (newIndex !== -1) {
          setSelectedIndex(newIndex);
        }
      } else {
        // If NO ONE on the team has the prop, fall back to the old logic of finding a valid tab for the current player
        const firstValidTab = TAB_ORDER.find(tab => {
          const k = STAT_LABELS[tab];
          return playerHasPropForDate(currentPlayer, k, resolvedSelectedGameDate);
        });
        if (firstValidTab) {
          setActiveTab(firstValidTab);
        }
      }
    }
  };

  // ──────────────────────────────────────────────────────────────
  // Data fetching
  // Set VITE_USE_DB=true in Vercel to fetch from Supabase.
  // Set VITE_USE_DB=false (or omit) to fall back to master_feed.json.
  // ──────────────────────────────────────────────────────────────
  const USE_DB = import.meta.env.VITE_USE_DB === 'true';
  const FULL_DB_POLL_MS = parsePollMs(import.meta.env.VITE_FULL_DB_POLL_MS, 30 * 60 * 1000);
  const HOT_DATA_POLL_MS = parsePollMs(import.meta.env.VITE_HOT_DATA_POLL_MS, 5 * 60 * 1000);
  const [isPageVisible, setIsPageVisible] = useState<boolean>(() => isDocumentVisible());
  const lineMovementVersionRef = useRef('');

  /** Reconstruct the Player[] shape from Supabase rows. */
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

          const { supabase } = await import('./utils/supabase');
          const [today] = getDashboardScheduleDates();
          const futureDates = buildFutureDates(today, 14);

          const [
            { data: playersRows, error: e1 },
            { data: propsRows, error: e2 },
            lineMovementResult,
            { data: gamesRows, error: e4 },
          ] = await Promise.all([
            supabase.from('players').select('id, name, team, position, stats, play_type_analysis'),
            fetchAllPlayerPropsForDates(supabase, futureDates),
            isInitial
              ? supabase.from('line_movements').select('game_date, snapshots, updated_at').in('game_date', getFastRefreshDates(null))
              : Promise.resolve({ data: null, error: null }),
            supabase.from('games').select('home_team_tricode, away_team_tricode').eq('game_date', today),
          ]);

          if (cancelled) return;
          if (e1) console.error('[supabase] players error:', e1);
          if (e2) console.error('[supabase] player_props error:', e2);
          if (lineMovementResult?.error && lineMovementResult.error.code !== 'PGRST116') {
            console.error('[supabase] line_movements error:', lineMovementResult.error);
          }
          if (e4) console.error('[supabase] games error:', e4);

          const mergedFeed = mergeFeedFromDB(playersRows ?? [], propsRows ?? []);
          const slateTeams = Array.from(new Set((gamesRows ?? []).flatMap((g: any) => [g.home_team_tricode, g.away_team_tricode]).filter(Boolean)));
          setCurrentSlateTeams(slateTeams);

          const intradayMovements = isInitial ? flattenIntradayMovements(lineMovementResult?.data ?? []) : null;
          if (isInitial) {
            lineMovementVersionRef.current = serializeLineMovementVersion(lineMovementResult?.data ?? []);
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
                props_by_date: p.props_by_date,
                play_type_analysis: p.play_type_analysis,
                intraday_movements: intradayMovements ?? existing?.intraday_movements ?? [],
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
      const apiUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';
      Promise.all([
        fetch(`${apiUrl}/data/current/master_feed.json`).then(res => res.json()),
        fetch(`${apiUrl}/data/archive/historical_odds.json`).then(res => res.json()).catch(() => ({})),
        fetch(`${apiUrl}/data/current/line_movements_today.json`).then(res => res.json()).catch(() => ({ snapshots: [] })),
        fetch(`${apiUrl}/data/current/nba_dashboard_games.json`).then(res => res.json()).catch(() => ([]))
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

        const { supabase } = await import('./utils/supabase');
        const activeDates = getFastRefreshDates(resolvedSelectedGameDate ?? selectedGameDate);

        const [
          { data: propsRows, error: propsError },
          { data: lineMetaRows, error: lineMetaError },
        ] = await Promise.all([
          fetchAllPlayerPropsForDates(supabase, activeDates),
          supabase.from('line_movements').select('game_date, updated_at').in('game_date', activeDates),
        ]);

        if (cancelled) return;
        if (propsError) console.error('[supabase] hot player_props error:', propsError);
        if (lineMetaError && lineMetaError.code !== 'PGRST116') {
          console.error('[supabase] hot line_movements metadata error:', lineMetaError);
        }

        if ((propsRows ?? []).length > 0) {
          setRawData(prev => mergePropsIntoPlayers(prev, propsRows ?? []));
        }

        const nextVersion = serializeLineMovementVersion(lineMetaRows ?? []);
        if (!nextVersion || nextVersion === lineMovementVersionRef.current) {
          return;
        }

        const { data: lineRows, error: lineRowsError } = await supabase
          .from('line_movements')
          .select('game_date, snapshots, updated_at')
          .in('game_date', activeDates);

        if (cancelled) return;
        if (lineRowsError && lineRowsError.code !== 'PGRST116') {
          console.error('[supabase] hot line_movements error:', lineRowsError);
          return;
        }

        lineMovementVersionRef.current = serializeLineMovementVersion(lineRows ?? []);
        const intradayMovements = flattenIntradayMovements(lineRows ?? []);
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
  }, [USE_DB, HOT_DATA_POLL_MS, isPageVisible, loading, resolvedSelectedGameDate, selectedGameDate]);


  const [isInitialized, setIsInitialized] = useState(false);

  // 2. Archive fetching when season filter changes
  useEffect(() => {
    // Only fetch if 24/25 is active, and the current player exists, and we haven't fetched their logs yet
    if (activeSeason === '24/25' && currentPlayer && !archiveGameLogs[currentPlayer.id]) {
      setIsLoadingArchive(true);

      const playerId = currentPlayer.id;

      if (USE_DB) {
        import('./utils/supabase').then(({ supabase }) => {
          supabase.from('archive_gamelogs')
            .select('game_log')
            .eq('player_id', playerId)
            .eq('season', '2024-25')
            .maybeSingle()
            .then(({ data, error }) => {
              setIsLoadingArchive(false);
              if (error) {
                console.error('[supabase] archive error:', error);
                return;
              }
              setArchiveGameLogs(prev => ({
                ...prev,
                [playerId]: data?.game_log || []
              }));
            });
        });
      } else {
        // Fallback logic could go here if `USE_DB` goes false, 
        // but since we are exclusively doing DB limits, we'll keep it empty.
        setIsLoadingArchive(false);
      }
    }
  }, [activeSeason, currentPlayer, archiveGameLogs, USE_DB]);

  useEffect(() => {
    if (!isFiltersOpen) {
      setFilterGameCount(19);
    }
  }, [isFiltersOpen]);

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

    import('./utils/supabase').then(({ supabase }) => {
      // Fetch zones + game_log in one query
      supabase
        .from('players')
        .select('game_log, shooting_zones, assist_zones, opp_def_zones, opp_def_zones_positional, opp_assist_zones, opp_assist_zones_positional, shot_type_analysis')
        .eq('id', playerId)
        .single()
        .then(({ data: detail, error }) => {
          if (error) { console.error('[supabase] player detail error:', error); return; }
          setRawData(prev => prev.map(p => p.id === playerId ? { ...p, ...detail } : p));
        });

      // Fetch historical odds for this player
      supabase
        .from('historical_odds')
        .select('game_date, props, source')
        .eq('player_id', playerId)
        .then(({ data, error }) => {
          if (error) { console.error('[supabase] historical_odds error:', error); return; }
          const map = Object.fromEntries((data ?? []).map(r => [
            r.game_date,
            {
              [String(playerId)]: {
                props: r.props,
                source: r.source,
              }
            }
          ]));
          setRawData(prev => prev.map(p => p.id === playerId ? { ...p, historical_odds: map } : p));
        });
    });
  }, [selectedIndex, USE_DB]);


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
      activeSportsbook: activeSportsbook,
      onSelectPlayer: (id: number, gameDate?: string | null) => {
        const index = playersWithProps.findIndex(p => p.id === id);
        if (index !== -1) {
          setSelectedIndex(index);
          setSelectedGameDate(gameDate ?? null);
          setCustomLineValue(null);
        }
      },
      activeTab: activeTab,
      onTabChange: handleTabChange
    }}>
      <div className="flex flex-col h-full w-full gap-4 relative pb-6">

        {/* Top Row: Merged Top Section + Filters Panel side-by-side */}
        <div className="flex w-full relative z-20">

          {/* Merged Top Section (Header + Chart) */}
          <div className="flex-1 bg-bgElevation0 rounded-xl shadow-lg animate-in fade-in duration-500 min-w-0 flex flex-col relative z-20">
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
              historicalGameCount={isFiltersOpen ? filterGameCount : 29}
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
                historicalGameCount={isFiltersOpen ? filterGameCount : 29}
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
                gameCount={filterGameCount}
                onGameCountChange={setFilterGameCount}
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
              <SimilarPlayers similarGames={undefined} />
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
                    <PlayTypeAnalysis playTypes={displayPlayer?.play_type_analysis} />
                  </div>
                )}
              </div>

              {/* Right Column: Shot Types + Play Types (types tab) + Similar */}
              <div className="xl:col-span-6 flex flex-col gap-4 h-full">
                {!['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab) && (
                  <div className={`${mobileView !== 'shooting' ? 'hidden md:block' : ''}`}>
                    <ShotTypeAnalysis shotTypes={(() => {
                      if (!displayPlayer?.shot_type_analysis) return undefined;
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
                    <PlayTypeAnalysis playTypes={displayPlayer?.play_type_analysis} />
                  </div>
                )}
                <div className={`flex-1 min-h-0 xl:col-span-12 w-full h-full ${mobileView !== 'similar' ? 'hidden md:flex' : ''}`}>
                  <SimilarPlayers similarGames={undefined} />
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
