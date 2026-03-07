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

  // 1. Fetch data from backend
  useEffect(() => {
    const apiUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';

    Promise.all([
      fetch(`${apiUrl}/data/current/master_feed.json`).then(res => res.json()),
      fetch(`${apiUrl}/data/archive/historical_odds.json`).then(res => res.json()).catch(() => ({})),
      fetch(`${apiUrl}/data/current/line_movements_today.json`).then(res => res.json()).catch(() => ({ snapshots: [] }))
    ])
      .then(([masterFeed, historicalOdds, lineMovements]) => {
        // Map historical & intraday odds into the master feed
        const enhancedFeed = (Array.isArray(masterFeed) ? masterFeed : []).map((player: Player) => {
          return {
            ...player,
            historical_odds: historicalOdds, // Using the full map so children can look up dates safely
            intraday_movements: lineMovements?.snapshots || [] // Array of snapshots
          };
        });

        setRawData(enhancedFeed);
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to load data:", err);
        setLoading(false);
      });
  }, []);

  const [isInitialized, setIsInitialized] = useState(false);

  useEffect(() => {
    if (!isFiltersOpen) {
      setFilterGameCount(19);
    }
  }, [isFiltersOpen]);

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
      activePlayerId: currentPlayer?.id,
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
          <div className="flex-1 bg-bgElevation0 rounded-xl shadow-lg animate-in fade-in duration-500 min-w-0">
            <Header
              player={currentPlayer}
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
            />

            {/* Subtle separator removed */}

            <div className="p-0">
              <BarChart
                player={currentPlayer}
                activeTab={activeTab}
                activeSportsbook={activeSportsbook}
                customLine={customLineValue}
                onCustomLineChange={setCustomLineValue}
                activeFilterOverlay={activeFilter}
                isFiltersOpen={isFiltersOpen}
                historicalGameCount={isFiltersOpen ? filterGameCount : 29}
              />
            </div>
          </div>

          {/* The Split Screen Filter Panel (Absolute inside relative to maintain left-column height) */}
          <div className={`transition-all duration-300 ease-in-out shrink-0 relative ${isFiltersOpen ? 'w-[320px] ml-4' : 'w-0 ml-0'}`}>
            <div className={`absolute inset-0 bg-bgElevation0 rounded-xl shadow-lg border border-borderMedium/40 overflow-hidden transition-opacity duration-300 ${isFiltersOpen ? 'opacity-100' : 'opacity-0 border-none'}`}>
              <FiltersPanel
                isOpen={isFiltersOpen}
                onClose={() => setIsFiltersOpen(false)}
                activeFilter={activeFilter}
                onFilterChange={setActiveFilter}
                player={currentPlayer}
                gameCount={filterGameCount}
                onGameCountChange={setFilterGameCount}
              />
            </div>
          </div>
        </div>

        {/* Bottom Grid Section */}
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-10 gap-4">

          {['Rebounds', '1Q Rebounds', 'Double Double', 'Triple Double', 'Blocks', 'Steals', 'Turnovers', 'Fantasy'].includes(activeTab) ? (
            <div className="xl:col-span-6 flex flex-col gap-4 h-full">
              <SimilarPlayers similarGames={undefined} />
            </div>
          ) : (
            <>
              {/* Left Column in Grid */}
              <div className="xl:col-span-4 flex flex-col gap-4 h-full">
                {['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab) ? (
                  <AssistZones player={currentPlayer} />
                ) : (
                  <ShootingZones player={currentPlayer} />
                )}
                
                {!['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab) && (
                  <div className="flex-1 min-h-0">
                    <PlayTypeAnalysis playTypes={currentPlayer?.play_type_analysis} />
                  </div>
                )}
              </div>

              {/* Right Column in Grid */}
              <div className="xl:col-span-6 flex flex-col gap-4 h-full">
                {!['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab) && (
                    <ShotTypeAnalysis shotTypes={(() => {
                      if (!currentPlayer?.shot_type_analysis) return undefined;
                      const sta = currentPlayer.shot_type_analysis;
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
                )}
                
                <div className="flex-1 min-h-0 xl:col-span-12 w-full h-full">
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