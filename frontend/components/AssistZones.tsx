import React, { useState } from 'react';
import { Info } from 'lucide-react';
import { Player } from '../types';

function getZoneColor(percentageString: string) {
  const pct = parseInt(percentageString.replace('%', '')) || 0;
  if (pct >= 30) return '#a3c76c'; // Green (High Frequency)
  if (pct >= 10) return '#e09f53'; // Orange (Medium Frequency)
  return '#f2d875'; // Yellow (Low Frequency)
}

function processZoneData(zoneData: any) {
  if (!zoneData) return null;

  const result: any = {};
  for (const key of ['left_corner', 'right_corner', 'restricted_area', 'paint', 'mid_range', 'top_key']) {
    const raw = zoneData[key] || { percentage: '0%', makes: '0' };
    result[key] = {
      percentage: raw.percentage,
      makes: raw.makes,
      color: getZoneColor(raw.percentage)
    };
  }
  return result;
}

const CourtShape = ({ viewData }: { viewData: any }) => (
  <svg viewBox="0 0 500 420" className="w-full h-full overflow-visible">
    <rect x="0" y="0" width="500" height="420" fill="#050505" />

    {/* Top Key / Arc 3 */}
    <rect x="0" y="0" width="500" height="420" fill={viewData.top_key.color} rx="4" />

    {/* Mid Range */}
    <path
      d="M36,0 L36,137 A237.5,237.5 0 0,0 464,137 L464,0 Z"
      fill={viewData.mid_range.color}
      stroke="black"
      strokeWidth="2"
    />

    {/* Paint / Restricted Area combo (No paint zone for assists, RIM covers all) */}
    <path d="M170,0 L330,0 L330,190 L170,190 Z" fill={viewData.restricted_area.color} stroke="black" strokeWidth="2" />
    <path d="M170,190 A80,80 0 0,0 330,190" fill={viewData.restricted_area.color} stroke="black" strokeWidth="2" />

    {/* Corners */}
    <rect x="0" y="0" width="36" height="137" fill={viewData.left_corner.color} stroke="black" strokeWidth="2" />
    <rect x="464" y="0" width="36" height="137" fill={viewData.right_corner.color} stroke="black" strokeWidth="2" />

    {/* Hoop & Lines */}
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
    {stat.makes && stat.makes !== '0' && (
      <div className="px-1 py-0.5 bg-white text-black border-l border-gray-300 min-w-[22px] text-center tracking-tight">
        {stat.makes}
      </div>
    )}
  </div>
);

const CourtView = ({ viewData }: { viewData: any }) => (
  <div className="relative w-full aspect-[1.3] max-w-[340px] mx-auto">
    <CourtShape viewData={viewData} />
    <ZoneLabel top="16%" left="8%" stat={viewData.left_corner} />
    <ZoneLabel top="16%" left="92%" stat={viewData.right_corner} />
    {/* For assists, there is only one big RIM label, let's put it at 45% (where Paint was) */}
    <ZoneLabel top="45%" left="50%" stat={viewData.restricted_area} />
    <ZoneLabel top="68%" left="50%" stat={viewData.mid_range} />
    <ZoneLabel top="88%" left="50%" stat={viewData.top_key} />
  </div>
);

export const AssistZones = ({ player }: { player: Player }) => {
  const [activeTab, setActiveTab] = useState<'player' | 'vs' | 'opp'>('player');

  const rawZones = (player as any)?.assist_zones;
  const isMissing = !rawZones || Object.keys(rawZones).length === 0;

  const playerView = processZoneData(rawZones) || processZoneData({});
  const oppView = processZoneData({}); // placeholders

  return (
    <div className={`bg-[#0a0a0a] rounded-xl p-5 w-full border border-gray-800/50 ${isMissing ? 'opacity-50' : ''}`}>
      <div className="flex justify-between items-start mb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h3 className="text-[15px] font-semibold text-white tracking-wide">Assist Zones</h3>
            <Info className="w-4 h-4 text-gray-400 cursor-pointer hover:text-gray-300 transition-colors" />
          </div>
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

      <div className="mt-8 relative">
        {isMissing && (
          <div className="absolute inset-0 z-20 flex items-center justify-center">
            <div className="bg-black/80 px-4 py-2 rounded-lg text-white font-semibold text-sm border border-gray-700">
              Assist zones aren't available for this player.
            </div>
          </div>
        )}

        {activeTab === 'vs' ? (
          <div className="flex gap-4 items-center">
            <div className="flex-1">
              <div className="text-center text-xs text-gray-400 font-semibold mb-2">PLAYER</div>
              <CourtView viewData={playerView} />
            </div>
            <div className="text-sm font-bold text-gray-500">VS</div>
            <div className="flex-1">
              <div className="text-center text-xs text-gray-400 font-semibold mb-2">OPP DEFENSE</div>
              <CourtView viewData={oppView} />
            </div>
          </div>
        ) : (
          <CourtView viewData={activeTab === 'opp' ? oppView : playerView} />
        )}
      </div>
    </div>
  );
};