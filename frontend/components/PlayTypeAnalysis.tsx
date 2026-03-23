import React from 'react';
import { Info } from 'lucide-react';
import { PLAY_TYPES } from '../constants';
import { PlayTypeData } from '../types';

interface PlayTypeAnalysisProps {
  playTypes?: PlayTypeData[];
}

function getPlayTypeRankColor(rank?: number | string, points?: number) {
  if (rank === 'N/A' || rank === undefined || rank === null) return { bg: '#1C1C1C', text: '#A3A3A3' }; // bgElevation2 / gray-400
  const r = Number(rank);

  let baseScore = 3;
  if (r <= 6) baseScore = 1;
  else if (r <= 10) baseScore = 2;
  else if (r <= 20) baseScore = 3;
  else if (r <= 24) baseScore = 4;
  else baseScore = 5;

  // Apply Volume Gravity
  if (points !== undefined) {
    if (points < 1.0) {
      baseScore = 3; // Force Neutral
    } else if (points < 2.5) {
      // Soften extreme tiers by 1 toward center
      if (baseScore === 1) baseScore = 2;
      if (baseScore === 5) baseScore = 4;
    }
  }

  // Map to final SSOT colors
  switch (baseScore) {
    case 1: return { bg: '#EF4444', text: '#FFFFFF' };      // courtRed
    case 2: return { bg: '#ED8936', text: '#FFFFFF' };      // courtOrange
    case 3: return { bg: '#F4C51E', text: '#000000' };      // courtYellow
    case 4: return { bg: '#B0BB5A', text: '#000000' };      // courtLightGreen
    case 5: return { bg: '#16A34A', text: '#FFFFFF' };      // courtGreen
    default: return { bg: '#F4C51E', text: '#000000' };
  }
}

const RankBadge = ({ rank, points }: { rank: number | string; points?: string | number }) => {
  const colors = getPlayTypeRankColor(rank, points !== undefined ? Number(points) : undefined);

  return (
    <span
      className="text-[10px] font-bold font-chakra px-2 py-0.5 rounded-[4px] min-w-[24px] text-center inline-block"
      style={{ backgroundColor: colors.bg, color: colors.text }}
    >
      {rank}
    </span>
  );
};

export const PlayTypeAnalysis: React.FC<PlayTypeAnalysisProps> = ({ playTypes }) => {
  const data = playTypes ?? PLAY_TYPES;
  const hasData = data.length > 0;

  return (
    <div className="bg-bgElevation0 rounded-lg p-3 lg:p-5 w-full h-full min-w-0">
      <div className="flex items-center gap-2 mb-1 relative z-50">
        <h3 className="text-sm font-bold text-white">Play Type Analysis</h3>
        <div className="relative group flex items-center">
          <Info className="w-3.5 h-3.5 text-gray-400 cursor-pointer hover:text-gray-300 transition-colors" />
          <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 hidden group-hover:block w-[280px] bg-[#1a1a1a] text-[#ededed] text-[13px] leading-relaxed rounded-lg p-3 shadow-2xl z-50 border border-[#333333] pointer-events-none">
            Opponent ranks form a bell curve (1-6 Red/Tough, 25-30 Green/Easy). Colors adjust for Volume Gravity (play types yielding low points are pulled to Yellow/Neutral noise).
          </div>
        </div>
      </div>
      <p className="text-xs text-gray-500 mb-4">25/26 Season</p>

      {hasData ? (
        <div className="w-full">
          <div className="grid grid-cols-[2fr_1fr_1fr] text-[10px] text-fgSubtle font-bold uppercase tracking-wider border-b border-borderMedium pb-2 mb-2">
            <div>Play Type</div>
            <div className="text-center">Player Points</div>
            <div className="text-right">Opp Def Rank</div>
          </div>

          <div className="space-y-3">
            {data.map((item, idx) => (
              <div key={idx} className="grid grid-cols-[2fr_1fr_1fr] text-xs items-center border-b border-borderMedium/40 pb-2 last:border-0 hover:bg-borderMedium/20 rounded px-1 -mx-1 transition-colors">
                <div className="text-white font-medium">{item.type}</div>
                <div className="text-gray-300 text-center font-chakra">{item.points}</div>
                <div className="text-right">
                  <RankBadge rank={item.rank} points={item.points} />
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-center h-[180px] text-center text-xs text-gray-500">
          Play type data loads after the player profile is ready.
        </div>
      )}
    </div>
  );
};
