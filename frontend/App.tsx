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
import { Player } from './types';

function App() {
  const [rawData, setRawData] = useState<Player[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [activeTab, setActiveTab] = useState('Points');
  const [activeSportsbook, setActiveSportsbook] = useState<'dk' | 'fd' | 'mgm' | 'cz'>('dk');

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

  // 2. Filter data
  const playersWithProps = useMemo(() => {
    if (!rawData) return [];
    const feed = Array.isArray(rawData) ? rawData : [];
    return feed.filter(p => p.props && Object.keys(p.props).length > 0);
  }, [rawData]);

  const currentPlayer = playersWithProps[selectedIndex];

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
        if (index !== -1) setSelectedIndex(index);
      },
      statFilter: activeTab // Optional: pass stat filter to sidebar if sidebar supports it
    }}>
      <div className="flex flex-col gap-4">

        {/* Merged Top Section (Header + Chart) */}
        <div className="bg-bgElevation0 rounded-xl shadow-lg animate-in fade-in duration-500 relative z-20">
          <Header
            player={currentPlayer}
            activeTab={activeTab}
            onTabChange={setActiveTab}
            activeSportsbook={activeSportsbook}
            onSportsbookChange={setActiveSportsbook}
          />

          {/* Subtle separator */}
          <div className="h-px w-full bg-border/50"></div>

          <div className="p-0">
            <BarChart
              player={currentPlayer}
              activeTab={activeTab}
              activeSportsbook={activeSportsbook}
            />
          </div>
        </div>

        {/* Bottom Grid Section */}
        <section className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-10 gap-4">

          {/* Left Column in Grid */}
          <div className="xl:col-span-4 flex flex-col gap-4 h-full">
            {activeTab === 'Assists' ? (
              <AssistZones player={currentPlayer} />
            ) : (
              <ShootingZones player={currentPlayer} />
            )}
            <div className="flex-1 min-h-0">
              <PlayTypeAnalysis playTypes={currentPlayer?.play_type_analysis} />
            </div>
          </div>

          {/* Right Column in Grid */}
          <div className="xl:col-span-6 flex flex-col gap-4 h-full">
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
            <div className="flex-1 min-h-0">
              <SimilarPlayers similarGames={undefined} />
            </div>
          </div>

        </section>

      </div>
    </Layout>
  );
}

export default App;