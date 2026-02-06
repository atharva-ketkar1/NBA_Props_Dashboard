import React from 'react';
import Sidebar from './components/Sidebar';
import PlayerCard from './components/PlayerCard';
import { ShootingZones, ShotTypeAnalysis, PlayTypeAnalysis, SimilarPlayers } from './components/Widgets';

function App() {
  return (
    <div className="flex min-h-screen bg-[#000000] font-sans overflow-hidden">

      {/* 1. FIXED SIDEBAR */}
      <div className="w-80 flex-shrink-0">
        <Sidebar />
      </div>

      {/* 2. SCROLLABLE DASHBOARD AREA */}
      <div className="flex-1 h-screen overflow-y-auto p-6 scrollbar-hide">
        <div className="max-w-[1400px] mx-auto space-y-4">

          {/* ROW A: Player Card (Full Width) */}
          <section>
            <PlayerCard />
          </section>

          {/* ROW B: Split 50/50 - Zones & Shot Types */}
          <section className="grid grid-cols-1 lg:grid-cols-2 gap-4 h-[320px]">
            <div className="h-full">
              <ShootingZones />
            </div>
            <div className="h-full">
              <ShotTypeAnalysis />
            </div>
          </section>

          {/* ROW C: Split 40/60 - Play Types & Similar Players */}
          <section className="grid grid-cols-1 lg:grid-cols-5 gap-4">
            <div className="lg:col-span-2">
              <PlayTypeAnalysis />
            </div>
            <div className="lg:col-span-3">
              <SimilarPlayers />
            </div>
          </section>

          <div className="h-10"></div> {/* Spacer */}
        </div>
      </div>
    </div>
  );
}

export default App;