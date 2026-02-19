import React, { useState } from 'react';
import { Info } from 'lucide-react';

// MOCK PAYLOAD: Mimics the exact JSON structure your Python backend will return.
// Using the calculated Sengun percentages: 48%, 34%, 8%, 1%, 1%, 9%
const MOCK_SENGUN_DATA = {
  player: {
    left_corner: { percentage: '1%', color: '#e45b5b' }, // Red (Cold)
    right_corner: { percentage: '1%', color: '#e45b5b' },
    restricted_area: { percentage: '48%', color: '#bdcc66' }, // Green (Hot)
    paint: { percentage: '34%', color: '#facc15' }, // Yellow (Average)
    mid_range: { percentage: '8%', color: '#e79036' }, // Orange (Warm)
    top_key: { percentage: '9%', color: '#e79036' }
  },
  vs: {
    // Placeholder data for other tabs to prevent crashes
    left_corner: { percentage: '0%', makes: '0', color: '#121212' },
    right_corner: { percentage: '0%', makes: '0', color: '#121212' },
    restricted_area: { percentage: '0%', makes: '0', color: '#121212' },
    paint: { percentage: '0%', makes: '0', color: '#121212' },
    mid_range: { percentage: '0%', makes: '0', color: '#121212' },
    top_key: { percentage: '0%', makes: '0', color: '#121212' }
  },
  opp: {
    left_corner: { percentage: '0%', color: '#121212' },
    right_corner: { percentage: '0%', color: '#121212' },
    restricted_area: { percentage: '0%', color: '#121212' },
    paint: { percentage: '0%', color: '#121212' },
    mid_range: { percentage: '0%', color: '#121212' },
    top_key: { percentage: '0%', color: '#121212' }
  }
};

const CourtShape = ({ viewData }: { viewData: any }) => (
  <svg viewBox="0 0 500 420" className="w-full h-full overflow-visible">
    <rect x="0" y="0" width="500" height="420" fill="#050505" />

    {/* Top Key */}
    <rect x="0" y="0" width="500" height="420" fill={viewData.top_key.color} rx="4" />

    {/* Mid Range */}
    <path
      d="M36,0 L36,137 A237.5,237.5 0 0,0 464,137 L464,0 Z"
      fill={viewData.mid_range.color}
      stroke="black"
      strokeWidth="2"
    />

    {/* Paint */}
    <path d="M170,0 L330,0 L330,190 L170,190 Z" fill={viewData.paint.color} stroke="black" strokeWidth="2" />
    <path d="M170,190 A80,80 0 0,0 330,190" fill={viewData.paint.color} stroke="black" strokeWidth="2" />

    {/* Corners */}
    <rect x="0" y="0" width="36" height="137" fill={viewData.left_corner.color} stroke="black" strokeWidth="2" />
    <rect x="464" y="0" width="36" height="137" fill={viewData.right_corner.color} stroke="black" strokeWidth="2" />

    {/* Hoop & Lines */}
    <path d="M220,47 A30,30 0 0,0 280,47" fill={viewData.restricted_area.color} stroke="black" strokeWidth="2" />
    <path d="M220,47 A30,30 0 0,0 280,47" fill="none" stroke="black" strokeWidth="2" />
    <line x1="220" y1="35" x2="220" y2="47" stroke="black" strokeWidth="2" />
    <line x1="280" y1="35" x2="280" y2="47" stroke="black" strokeWidth="2" />
    <line x1="220" y1="40" x2="280" y2="40" stroke="black" strokeWidth="3" />
    <circle cx="250" cy="47.5" r="7.5" fill="none" stroke="black" strokeWidth="2" />

    <path d="M170,190 L330,190" stroke="black" strokeWidth="1" />
  </svg>
);

const ZoneLabel = ({ top, left, stat }: { top: string, left: string, stat: any }) => (
  <div className="absolute transform -translate-x-1/2 -translate-y-1/2 bg-white flex shadow-[0_2px_4px_rgba(0,0,0,0.3)] rounded-[3px] overflow-hidden text-[11px] font-bold border border-white z-10" style={{ top, left }}>
    <div className={`px-1 py-0.5 text-black text-center tracking-tight ${!stat.makes ? 'min-w-[40px]' : 'min-w-[32px]'}`}>
      {stat.percentage}
    </div>
    {stat.makes && (
      <div className="px-1 py-0.5 bg-white text-black border-l border-gray-300 min-w-[22px] text-center tracking-tight">
        {stat.makes}
      </div>
    )}
  </div>
);

export const ShootingZones = ({ data = MOCK_SENGUN_DATA }) => {
  const [activeTab, setActiveTab] = useState<'player' | 'vs' | 'opp'>('player');

  const currentView = data[activeTab];

  return (
    <div className="bg-[#0a0a0a] rounded-xl p-5 w-full border border-gray-800/50">
      <div className="flex justify-between items-start mb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h3 className="text-[15px] font-semibold text-white tracking-wide">Shooting Zones</h3>
            <Info className="w-4 h-4 text-gray-400 cursor-pointer hover:text-gray-300 transition-colors" />
          </div>
          <p className="text-sm text-gray-400 font-medium">Alperen Sengun</p>
        </div>

        <div className="flex bg-[#0f0f11] rounded-[10px] p-1 border border-gray-800 items-center">
          <button
            onClick={() => setActiveTab('player')}
            className={`text-[13px] font-semibold px-3 py-1.5 rounded-md transition-all ${activeTab === 'player' ? 'text-white bg-[#27272a] shadow-sm border border-[#3b82f6]' : 'text-gray-400 hover:text-white border border-transparent'
              }`}
          >
            Player
          </button>

          <button
            onClick={() => setActiveTab('vs')}
            className={`px-2 py-1 text-[11px] font-bold rounded-[6px] mx-1 h-fit transition-all ${activeTab === 'vs' ? 'text-white bg-[#475569]' : 'text-gray-500 bg-[#27272a]/50 hover:text-gray-300'
              }`}
          >
            vs
          </button>

          <button
            onClick={() => setActiveTab('opp')}
            className={`text-[13px] font-semibold px-3 py-1.5 rounded-md transition-all ${activeTab === 'opp' ? 'text-white bg-[#27272a] shadow-sm border border-[#3b82f6]' : 'text-gray-400 hover:text-white border border-transparent'
              }`}
          >
            Opp Defense
          </button>
        </div>
      </div>

      <div className="relative w-full aspect-[1.3] max-w-[340px] mx-auto mt-8">
        <CourtShape viewData={currentView} />

        <ZoneLabel top="16%" left="8%" stat={currentView.left_corner} />
        <ZoneLabel top="16%" left="92%" stat={currentView.right_corner} />
        <ZoneLabel top="16%" left="50%" stat={currentView.restricted_area} />
        <ZoneLabel top="45%" left="50%" stat={currentView.paint} />
        <ZoneLabel top="68%" left="50%" stat={currentView.mid_range} />
        <ZoneLabel top="88%" left="50%" stat={currentView.top_key} />
      </div>
    </div>
  );
};