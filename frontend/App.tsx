import React, { useState, useEffect, useMemo } from 'react';
import { Layout } from './components/Layout';
import { Header } from './components/Header';
import { BarChart } from './components/BarChart';
import { DashboardSkeleton } from './components/ui/DashboardSkeleton';
import { ShootingZones } from './components/ShootingZones';
import { ShotTypeAnalysis } from './components/ShotTypeAnalysis';
import { PlayTypeAnalysis } from './components/PlayTypeAnalysis';
import { SimilarPlayers } from './components/SimilarPlayers';
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
    fetch(`${apiUrl}/data/current/master_feed.json`)
      .then(res => res.json())
      .then(data => {
        setRawData(data);
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
      <div className="flex flex-col gap-6">

        {/* Merged Top Section (Header + Chart) */}
        <div className="bg-card rounded-xl border border-border shadow-lg animate-in fade-in duration-500 relative z-20">
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
        <section className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-10 gap-6">

          {/* Left Column in Grid */}
          <div className="xl:col-span-4 flex flex-col gap-6 h-full">
            <ShootingZones player={currentPlayer} />
            <div className="flex-1 min-h-0">
              <PlayTypeAnalysis />
            </div>
          </div>

          {/* Right Column in Grid */}
          <div className="xl:col-span-6 flex flex-col gap-6 h-full">
            <ShotTypeAnalysis />
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