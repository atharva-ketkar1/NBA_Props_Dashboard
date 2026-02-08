import React, { useState, useMemo, useEffect, useContext } from 'react';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import PlayerCard from './components/PlayerCard';
import { ShootingZones, ShotTypeAnalysis, PlayTypeAnalysis, SimilarPlayers } from './components/Widgets';

function App() {
  const [rawData, setRawData] = useState([]);
  const [loading, setLoading] = useState(true);

  // 1. Fetch data from your local "backend" server
  useEffect(() => {
    // In the future, you just change this URL to your Cloud API URL
    fetch('http://localhost:5000/data/current/master_feed.json')
      .then(res => res.json())
      .then(data => {
        setRawData(data);
        setLoading(false);
      })
      .catch(err => console.error("Failed to load data:", err));
  }, []);

  // 2. Filter data (same logic as before)
  const playersWithProps = useMemo(() => {
    if (!rawData) return [];
    // Ensure rawData is an array before filtering
    const feed = Array.isArray(rawData) ? rawData : [];
    return feed.filter(p => p.props && Object.keys(p.props).length > 0);
  }, [rawData]);

  const [selectedIndex, setSelectedIndex] = useState(0);

  // 3. Loading State
  if (loading) return <div className="bg-black h-screen text-white p-10">Loading Data...</div>;

  const currentPlayer = playersWithProps[selectedIndex];

  // Optional: Safety check if NO players have props today
  if (playersWithProps.length === 0) {
    return (
      <div className="flex h-screen items-center justify-center bg-black text-white">
        No active props found for today.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-black font-sans text-slate-200 selection:bg-yellow-500/30 overflow-hidden">

      <div className="flex-shrink-0">
        <Header />
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <div className="hidden md:block w-56 lg:w-64 flex-shrink-0 h-full">
          <Sidebar
            players={playersWithProps}
            activePlayerId={currentPlayer?.id}
            onSelectPlayer={(playerId) => {
              const index = playersWithProps.findIndex(p => p.id === playerId);
              if (index !== -1) setSelectedIndex(index);
            }}
          />
        </div>

        <div className="flex-1 h-full overflow-y-auto overflow-x-hidden">
          <div className="w-full max-w-[1920px] mx-auto p-4 lg:p-6 space-y-6">

            {/* PLAYER SELECTOR (Temporary Navigation) */}
            <div className="flex items-center gap-4 bg-[#18181b] p-3 rounded-lg border border-white/10 w-fit">
              <div className="flex flex-col">
                <span className="text-[10px] text-gray-400 font-bold uppercase tracking-wider">
                  Active Players ({playersWithProps.length})
                </span>
                <select
                  className="bg-black text-white text-sm border border-gray-700 rounded p-1.5 mt-1 min-w-[200px] outline-none focus:border-blue-500 transition-colors"
                  value={selectedIndex}
                  onChange={(e) => setSelectedIndex(Number(e.target.value))}
                >
                  {playersWithProps.map((p, index) => (
                    <option key={p.id} value={index}>
                      {p.name} ({p.team})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* MAIN PLAYER CARD */}
            <section className="w-full">
              <PlayerCard player={currentPlayer} />
            </section>

            {/* WIDGETS */}
            <section className="grid grid-cols-1 xl:grid-cols-2 gap-6">
              <div className="w-full h-full min-h-[320px]">
                <ShootingZones />
              </div>
              <div className="w-full h-full min-h-[320px]">
                <ShotTypeAnalysis />
              </div>
            </section>

            <section className="grid grid-cols-1 xl:grid-cols-5 gap-6">
              <div className="xl:col-span-2 w-full min-h-[400px]">
                <PlayTypeAnalysis />
              </div>
              <div className="xl:col-span-3 w-full min-h-[400px]">
                <SimilarPlayers />
              </div>
            </section>

            <div className="h-16"></div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;