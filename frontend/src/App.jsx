import React from 'react';
import Header from './components/Header'; // New Component
import Sidebar from './components/Sidebar';
import PlayerCard from './components/PlayerCard';
import { ShootingZones, ShotTypeAnalysis, PlayTypeAnalysis, SimilarPlayers } from './components/Widgets';

function App() {
  return (
    // CHANGE 1: Main Container is now Flex-COL to stack Header on top
    <div className="flex flex-col h-screen bg-[#000000] font-sans text-slate-200 selection:bg-yellow-500/30 overflow-hidden">

      {/* 1. HEADER (Fixed Height) */}
      <div className="flex-shrink-0">
        <Header />
      </div>

      {/* 2. BODY CONTAINER (Fills remaining height) */}
      <div className="flex flex-1 overflow-hidden">

        {/* Sidebar - Fixed Width, Full Height of parent */}
        <div className="hidden md:block w-64 lg:w-72 flex-shrink-0 border-r border-white/5 h-full">
          <Sidebar />
        </div>

        {/* Main Dashboard - Scrollable */}
        <div className="flex-1 h-full overflow-y-auto overflow-x-hidden scrollbar-hide">
          <div className="w-full max-w-[1920px] mx-auto p-6 lg:p-8 space-y-8">

            {/* ROW A: Player Card */}
            <section className="w-full">
              <PlayerCard />
            </section>

            {/* ROW B: Zones & Shot Types */}
            <section className="grid grid-cols-1 xl:grid-cols-2 gap-8 min-h-[350px]">
              <div className="w-full h-full min-h-[300px]">
                <ShootingZones />
              </div>
              <div className="w-full h-full min-h-[300px]">
                <ShotTypeAnalysis />
              </div>
            </section>

            {/* ROW C: Play Types & Similar Players */}
            <section className="grid grid-cols-1 xl:grid-cols-5 gap-8">
              <div className="xl:col-span-2 w-full">
                <PlayTypeAnalysis />
              </div>
              <div className="xl:col-span-3 w-full">
                <SimilarPlayers />
              </div>
            </section>

            <div className="h-20"></div> {/* Bottom Spacer */}
          </div>
        </div>

      </div>
    </div>
  );
}

export default App;