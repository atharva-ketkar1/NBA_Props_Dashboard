import React, { useState } from 'react';
import { Info } from 'lucide-react';
import { Player } from '../types';

import { colors } from '../utils/propsmadness_colors';

function getZoneColor(percentageString: string) {
  const pct = parseInt(percentageString.replace('%', '')) || 0;
  if (pct >= 45) return colors.courtGreen;
  if (pct >= 30) return colors.courtLightGreen;
  if (pct >= 15) return colors.courtYellow;
  if (pct >= 5) return colors.courtOrange;
  return colors.courtRed;
}

function getOppZoneColor(rankStr: string | number) {
  const rank = typeof rankStr === 'string' ? parseInt(rankStr) : rankStr;

  if (!rank || rank < 1 || rank > 30) return colors.neutral1; // Fallback for missing/bad data

  // PropsMadness 5-Tier Scale (1-30) / Bell Curve
  if (rank <= 6) return colors.courtRed;         // Red (Very Tough)
  if (rank <= 10) return colors.courtOrange;     // Orange (Tough)
  if (rank <= 20) return colors.courtYellow;     // Yellow (Neutral Noise)
  if (rank <= 24) return colors.courtLightGreen; // Light Green (Favorable)
  return colors.courtGreen;                      // Green (Highly Favorable)
}

function processZoneData(zoneData: any, isOppData: boolean = false) {
  if (!zoneData) {
    return {
      left_corner: { percentage: '0%', makes: '0', rank: '30', color: '#121212' },
      right_corner: { percentage: '0%', makes: '0', rank: '30', color: '#121212' },
      restricted_area: { percentage: '0%', makes: '0', rank: '30', color: '#121212' },
      paint: { percentage: '0%', makes: '0', rank: '30', color: '#121212' },
      mid_range: { percentage: '0%', makes: '0', rank: '30', color: '#121212' },
      top_key: { percentage: '0%', makes: '0', rank: '30', color: '#121212' }
    };
  }

  const result: any = {};
  for (const key of ['left_corner', 'right_corner', 'restricted_area', 'paint', 'mid_range', 'top_key']) {
    const raw = zoneData[key] || { percentage: '0%', makes: '0', rank: '30' };
    result[key] = {
      percentage: raw.percentage,
      makes: raw.makes,
      rank: raw.rank || '30',
      color: isOppData ? getOppZoneColor(raw.rank || '30') : getZoneColor(raw.percentage)
    };
  }
  return result;
}

const CourtShape = ({ viewData }: { viewData: any }) => (
  <svg viewBox="0 0 261 200" className="w-full h-full overflow-visible">
    <path id="zone-top-key" d="M0 0H50L83 48.5H128H175.5L205 0H261V149H0V0Z" fill={viewData.top_key.color} transform="translate(0 52)"></path>
    <path id="zone-mid-range" fillRule="evenodd" clipRule="evenodd" d="M228 2V1H227H157.881H156.881V2V98.9726H70.055V2V1H69.055H2H1V2V50.9863C1 106.785 46.7433 152 103.147 152H125.853C182.257 152 228 106.785 228 50.9863V2Z" fill={viewData.mid_range.color} transform="translate(16 -2)"></path>
    <path id="zone-paint" fillRule="evenodd" clipRule="evenodd" d="M0 96V7.62939e-06H85V96H0ZM54.6429 19.4043H43.5119V22.5313C47.5052 23.0339 50.5952 26.4719 50.5952 30.6383C50.5952 35.1506 46.9709 38.8085 42.5 38.8085C38.0291 38.8085 34.4048 35.1506 34.4048 30.6383C34.4048 26.4719 37.4948 23.0339 41.4881 22.5313V19.4043H30.3571V17.3617H54.6429V19.4043ZM42.5 24.5106C39.1468 24.5106 36.4286 27.2541 36.4286 30.6383C36.4286 34.0225 39.1468 36.766 42.5 36.766C45.8532 36.766 48.5714 34.0225 48.5714 30.6383C48.5714 27.2541 45.8532 24.5106 42.5 24.5106ZM19.2262 0V26.8168C19.2262 39.6336 29.6359 50.0426 42.5 50.0426C55.3641 50.0426 65.7738 39.6336 65.7738 26.8168V7.62939e-06L67.7976 0V26.8168C67.7976 40.7825 56.4612 52.0851 42.5 52.0851C28.5389 52.0851 17.2024 40.7825 17.2024 26.8168V7.62939e-06L19.2262 0Z" fill={viewData.paint.color} transform="translate(87 0)"></path>
    <path id="zone-restricted-area" d="M45.12 0H47V24.7135C47 37.5838 39.5 50 23.5 50C7.5 50 0 37.5838 0 24.7135V7.03101e-06L45.12 0Z" fill={viewData.restricted_area.color} transform="translate(106 0)"></path>
    <path id="zone-corner-left" d="M16 50V0H0V50H16Z" fill={viewData.left_corner.color}></path>
    <path id="zone-corner-right" d="M16 50V0H0V50H16Z" fill={viewData.right_corner.color} transform="translate(245 0)"></path>
    <path id="court-lines" fillRule="evenodd" clipRule="evenodd" d="M228 2V1H227H157.881H156.881V2V98.9726H70.055V2V1H69.055H2H1V2V50.9863C1 106.785 46.7433 152 103.147 152H125.853C182.257 152 228 106.785 228 50.9863V2Z" strokeWidth="2" fill="none" stroke={colors.borderMedium} transform="translate(16 -2)"></path>
  </svg>
);

function getVsZoneColor(pPctStr: string, oRankStr: string | number, pMakesStr: string, playerFgPct: number) {
  const pPct = parseInt(pPctStr.replace('%', '')) || 0; // diet %
  const oRank = typeof oRankStr === 'string' ? parseInt(oRankStr) : oRankStr;

  if (!oRank || oRank < 1 || oRank > 30) return colors.neutral1;

  // 1. Establish the pure defensive Base Score
  let baseScore = 3;
  if (oRank <= 6) baseScore = 1;
  else if (oRank <= 10) baseScore = 2;
  else if (oRank <= 20) baseScore = 3;
  else if (oRank <= 24) baseScore = 4;
  else baseScore = 5;

  // 2. Player Efficiency Modifier (+/- 1 EV)
  const fgPctInt = playerFgPct > 1 ? playerFgPct : playerFgPct * 100;
  if (fgPctInt > 45) {
    baseScore = Math.min(5, baseScore + 1);
  } else if (fgPctInt > 0 && fgPctInt < 30) {
    baseScore = Math.max(1, baseScore - 1);
  }

  // 3. Apply "Gravity to Neutral" based on Player Volume
  if (pPct < 10) {
    baseScore = 3; // Force Neutral
  } else if (pPct <= 25) {
    // Soften extremes by 1 tier
    if (baseScore === 1) baseScore = 2;
    if (baseScore === 5) baseScore = 4;
  }
  // High volume (pPct > 25) keeps the true base score intact

  // 4. Map to final SSOT color
  switch (baseScore) {
    case 1: return colors.courtRed;
    case 2: return colors.courtOrange;
    case 3: return colors.courtYellow;
    case 4: return colors.courtLightGreen;
    case 5: return colors.courtGreen;
    default: return colors.courtYellow;
  }
}

function processVsZoneData(pZoneData: any, oZoneData: any, playerFgPct: number) {
  const pData = pZoneData || {};
  const oData = oZoneData || {};

  const result: any = {};
  for (const key of ['left_corner', 'right_corner', 'restricted_area', 'paint', 'mid_range', 'top_key']) {
    const pRaw = pData[key] || { percentage: '0%', makes: '0', rank: '30' };
    const oRaw = oData[key] || { percentage: '0%', makes: '0', rank: '30' };
    result[key] = {
      color: getVsZoneColor(pRaw.percentage, oRaw.rank, pRaw.makes, playerFgPct)
    };
  }
  return result;
}

const ZoneLabel = ({ top, left, pStat, oStat, activeTab }: { top: string, left: string, pStat: any, oStat: any, activeTab: string }) => {
  if (activeTab === 'player') {
    return (
      <div className="absolute transform -translate-x-1/2 -translate-y-1/2 bg-white flex shadow-[0_2px_4px_rgba(0,0,0,0.3)] rounded-[3px] overflow-hidden text-[11px] font-bold font-chakra border border-white z-10" style={{ top, left }}>
        <div className="px-1 py-0.5 text-black text-center min-w-[40px] tracking-tight">{pStat.percentage}</div>
      </div>
    );
  } else if (activeTab === 'opp') {
    return (
      <div className="absolute transform -translate-x-1/2 -translate-y-1/2 bg-white flex shadow-[0_2px_4px_rgba(0,0,0,0.3)] rounded-[3px] overflow-hidden text-[11px] font-bold font-chakra border border-white z-10" style={{ top, left }}>
        <div className="px-1 py-0.5 text-black text-center min-w-[40px] tracking-tight">{oStat.rank}</div>
      </div>
    );
  } else {
    // VS Tab
    return (
      <div className="absolute transform -translate-x-1/2 -translate-y-1/2 flex shadow-[0_2px_4px_rgba(0,0,0,0.3)] rounded-[3px] overflow-hidden text-[11px] font-bold font-chakra border border-white z-10 bg-white" style={{ top, left }}>
        <div className="px-1 py-0.5 text-black text-center min-w-[32px] tracking-tight">
          {pStat.percentage}
        </div>
        <div className="px-[5px] py-0.5 text-black border-l border-gray-400 min-w-[22px] text-center tracking-tight">
          {oStat.rank}
        </div>
      </div>
    );
  }
};

// Pass activeTab down to CourtView and ZoneLabels
const CourtView = ({ pView, oView, vsView, activeTab }: { pView: any, oView: any, vsView: any, activeTab: string }) => (
  <div className="relative w-full aspect-[1.3] max-w-[340px] mx-auto">
    <CourtShape viewData={activeTab === 'vs' ? vsView : (activeTab === 'opp' ? oView : pView)} />
    <ZoneLabel top="12%" left="3%" pStat={pView.left_corner} oStat={oView.left_corner} activeTab={activeTab} />
    <ZoneLabel top="12%" left="97%" pStat={pView.right_corner} oStat={oView.right_corner} activeTab={activeTab} />
    <ZoneLabel top="12%" left="50%" pStat={pView.restricted_area} oStat={oView.restricted_area} activeTab={activeTab} />
    <ZoneLabel top="38%" left="50%" pStat={pView.paint} oStat={oView.paint} activeTab={activeTab} />
    <ZoneLabel top="62%" left="50%" pStat={pView.mid_range} oStat={oView.mid_range} activeTab={activeTab} />
    <ZoneLabel top="85%" left="50%" pStat={pView.top_key} oStat={oView.top_key} activeTab={activeTab} />
  </div>
);

export const ShootingZones = ({ player }: { player: Player | any }) => {
  const [activeTab, setActiveTab] = useState<'player' | 'vs' | 'opp'>('player');

  const playerView = processZoneData((player as any)?.shooting_zones);
  const oppView = processZoneData((player as any)?.opp_def_zones, true);

  const playerFgPct = player?.stats?.FG_PCT || 0;
  const vsView = processVsZoneData((player as any)?.shooting_zones, (player as any)?.opp_def_zones, playerFgPct);

  return (
    <div className="bg-bgElevation0 rounded-xl p-5 w-full">
      <div className="flex justify-between items-start mb-4">
        <div>
          <div className="flex items-center gap-2 mb-1 relative z-50">
            <h3 className="text-[15px] font-semibold text-white tracking-wide">Shooting Zones</h3>
            <div className="relative group flex items-center">
              <Info className="w-4 h-4 text-gray-400 cursor-pointer hover:text-gray-300 transition-colors" />
              <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 hidden group-hover:block w-[280px] bg-[#1a1a1a] text-[#ededed] text-[13px] leading-relaxed rounded-lg p-3 shadow-2xl z-50 border border-[#333333] pointer-events-none">
                Opponent ranks form a bell curve (1-6 Red/Tough, 25-30 Green/Easy). Colors adjust for Player FG% (+/- 1 EV) and Volume Gravity (low attempts pull to Yellow/Neutral).
              </div>
            </div>
          </div>
          <div className="text-[12px] text-gray-400 font-medium">25/26 Season</div>
        </div>

        <div className="flex bg-bgElevation1 rounded-[10px] p-1 border-transparent items-center">
          <button
            onClick={() => setActiveTab('player')}
            className={`text-[13px] font-semibold px-3 py-1.5 rounded-md transition-all ${activeTab === 'player' ? 'text-white bg-bgElevationAccent shadow-sm' : 'text-fgMuted hover:text-fixedWhite'
              }`}
          >
            Player
          </button>

          <button
            onClick={() => setActiveTab('vs')}
            className={`px-2 py-1 text-[11px] font-bold rounded-[6px] mx-1 h-fit transition-all ${activeTab === 'vs' ? 'text-white bg-bgElevationAccent' : 'text-fgDisabled hover:text-fgMuted'
              }`}
          >
            vs
          </button>

          <button
            onClick={() => setActiveTab('opp')}
            className={`text-[13px] font-semibold px-3 py-1.5 rounded-md transition-all ${activeTab === 'opp' ? 'text-white bg-bgElevationAccent shadow-sm' : 'text-fgMuted hover:text-fixedWhite'
              }`}
          >
            Opp Defense
          </button>
        </div>
      </div>

      <div className="mt-8">
        {/* Simplified block: Always render exactly one CourtView */}
        <CourtView
          pView={playerView}
          oView={oppView}
          vsView={vsView}
          activeTab={activeTab}
        />
      </div>
    </div>
  );
};