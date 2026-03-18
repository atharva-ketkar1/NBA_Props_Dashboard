import React, { useState, useEffect, useMemo } from 'react';
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
import { Player } from './types';
import { MobileViewSwitcher, MobileView } from './components/MobileViewSwitcher';


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

function App() {
  const [rawData, setRawData] = useState<Player[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(0);
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

  const playersWithProps = useMemo(() => {
    if (!rawData) return [];
    const feed = Array.isArray(rawData) ? rawData : [];
    return feed.filter(p => p.props && Object.keys(p.props).length > 0);
  }, [rawData]);

  const currentPlayer = playersWithProps[selectedIndex];

  const handleTabChange = (newTab: string) => {
    setActiveTab(newTab);
    setCustomLineValue(null);
    if (!currentPlayer || !currentPlayer.props) return;

    const statKey = STAT_LABELS[newTab] || 'PTS';
    const hasProp = currentPlayer.props[statKey] && Object.keys(currentPlayer.props[statKey]).length > 0;

    if (!hasProp) {
      // Find all players on the SAME TEAM as currentPlayer who HAVE the selected prop
      const teamPlayers = playersWithProps.filter(p => p.team === currentPlayer.team);
      const eligiblePlayers = teamPlayers.filter(p => p.props && p.props[statKey] && Object.keys(p.props[statKey]).length > 0);

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
          return currentPlayer.props[k] && Object.keys(currentPlayer.props[k]).length > 0;
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
  const DB_POLL_MS = 10 * 60 * 1000;

  function getDashboardDate() {
    const date = new Date();
    // NBA day rollover: Keep showing yesterday's data until 9 AM local time
    // to allow the 6 AM cron jobs to fully populate Supabase for the new day.
    if (date.getHours() < 7) {
      date.setDate(date.getDate() - 1);
    }
    const yyyy = date.getFullYear();
    const mm = String(date.getMonth() + 1).padStart(2, '0');
    const dd = String(date.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  }

  /** Reconstruct the Player[] shape from Supabase rows. */
  function mergeFeedFromDB(
    players: any[],
    props: any[],
  ): Player[] {
    // Build props lookup: player_id → stat_type → sportsbook → {line, over, under}
    const propsMap: Record<number, Record<string, Record<string, any>>> = {};
    for (const row of props ?? []) {
      if (!propsMap[row.player_id]) propsMap[row.player_id] = {};
      if (!propsMap[row.player_id][row.stat_type]) propsMap[row.player_id][row.stat_type] = {};
      propsMap[row.player_id][row.stat_type][row.sportsbook] = {
        line: row.line,
        over: row.over_odds,
        under: row.under_odds,
        implied: row.implied,
      };
    }

    return (players ?? []).map((p: any) => ({
      id: p.id,
      name: p.name,
      team: p.team,
      position: p.position,
      stats: p.stats ?? {},
      game_log: p.game_log ?? [],   // null until lazy-loaded
      props: propsMap[p.id] ?? {},
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
    }));
  }

  // 1. Initial data fetch
  useEffect(() => {
    if (USE_DB) {
      let cancelled = false;

      const fetchDbSnapshot = (isInitial = false) => {
        import('./utils/supabase').then(({ supabase }) => {
          const today = getDashboardDate();

          Promise.all([
            // Lightweight: skip heavy JSONB on initial load
            supabase.from('players').select('id, name, team, position, stats, play_type_analysis'),
            supabase.from('player_props').select('*').eq('game_date', today).limit(5000),
            supabase.from('line_movements').select('snapshots').eq('game_date', today).maybeSingle(),
          ])
            .then(([{ data: playersRows, error: e1 }, { data: propsRows, error: e2 }, { data: lmRow, error: e3 }]) => {
              if (cancelled) return;
              if (e1) console.error('[supabase] players error:', e1);
              if (e2) console.error('[supabase] player_props error:', e2);
              if (e3 && e3.code !== 'PGRST116') console.error('[supabase] line_movements error:', e3);

              const intraday_movements = lmRow?.snapshots || [];
              const mergedFeed = mergeFeedFromDB(playersRows ?? [], propsRows ?? []);

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
                    play_type_analysis: p.play_type_analysis,
                    intraday_movements,
                  };
                });
              });

              if (isInitial) {
                setLoading(false);
              }
            })
            .catch(err => {
              if (cancelled) return;
              console.error('Supabase fetch failed:', err);
              if (isInitial) {
                setLoading(false);
              }
            });
        });
      };

      fetchDbSnapshot(true);
      const intervalId = window.setInterval(() => fetchDbSnapshot(false), DB_POLL_MS);
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
        fetch(`${apiUrl}/data/current/line_movements_today.json`).then(res => res.json()).catch(() => ({ snapshots: [] }))
      ])
        .then(([masterFeed, historicalOdds, lineMovements]) => {
          const enhancedFeed = (Array.isArray(masterFeed) ? masterFeed : []).map((player: Player) => ({
            ...player,
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
  }, []);


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
    if (activeSeason === '24/25') {
      return {
        ...currentPlayer,
        game_log: archiveGameLogs[String(currentPlayer.id)] || []
      } as Player;
    }
    return currentPlayer;
  }, [currentPlayer, activeSeason, archiveGameLogs]);

  // 3. Smart Default Selection (Run once on data load)
  useEffect(() => {
    if (playersWithProps.length > 0 && !isInitialized) {
      // Find the earliest date any player plays
      let earliestDateStr = "9999-99-99";
      playersWithProps.forEach(p => {
        const gameDate = p.game_log?.[0]?.GAME_DATE; // Logs are usually sorted descending, so [0] might be last game, but wait, we need the *next* game. 
        // Typically in master_feed, 'props' implies they play soon. 
        // We can just sort all players with props by Season PTS to find a "Star Player"
      });

      // Simple robust approach: Out of all players with props today/tomorrow, find the one with highest season PTS
      let bestIndex = 0;
      let maxPts = -1;

      playersWithProps.forEach((p, idx) => {
        const pts = (p.stats && p.stats.PTS) ? Number(p.stats.PTS) : 0;
        if (pts > maxPts) {
          maxPts = pts;
          bestIndex = idx;
        }
      });

      setSelectedIndex(bestIndex);
      setIsInitialized(true);
    }
  }, [playersWithProps, isInitialized]);

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
          No active props found for today.
        </div>
      </Layout>
    );
  }

  return (
    <Layout sidebarProps={{
      players: playersWithProps,
      activePlayerId: displayPlayer?.id,
      onSelectPlayer: (id: number) => {
        const index = playersWithProps.findIndex(p => p.id === id);
        if (index !== -1) {
          setSelectedIndex(index);
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
