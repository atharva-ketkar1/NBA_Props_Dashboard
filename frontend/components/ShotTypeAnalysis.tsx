import React from 'react';
import { Info } from 'lucide-react';
import { ShotTypeData } from '../types';
import { colors } from '../utils/propsmadness_colors';

function getOppRankColor(rank?: number, percentage?: number) {
   if (rank === undefined) return colors.bgElevation2; // default neutral

   let baseScore = 3;
   if (rank <= 6) baseScore = 1;
   else if (rank <= 10) baseScore = 2;
   else if (rank <= 20) baseScore = 3;
   else if (rank <= 24) baseScore = 4;
   else baseScore = 5;

   // Volume Gravity based on Frequency / Diet Percentage
   if (percentage !== undefined) {
      if (percentage < 10) {
         baseScore = 3; // Neutral
      } else if (percentage <= 25) {
         if (baseScore === 1) baseScore = 2;
         if (baseScore === 5) baseScore = 4;
      }
   }

   switch (baseScore) {
      case 1: return colors.courtRed;
      case 2: return colors.courtOrange;
      case 3: return colors.courtYellow;
      case 4: return colors.courtLightGreen;
      case 5: return colors.courtGreen;
      default: return colors.courtYellow;
   }
}

interface ShotTypeAnalysisProps {
   shotTypes?: ShotTypeData[];
}

const DEFAULT_SHOT_TYPES: ShotTypeData[] = [
   { type: 'C&S', percentage: 14, attempts: 13, width: 16 },
   { type: '< 10 ft', percentage: 52, attempts: 19, width: 50 },
   { type: 'Pull Up', percentage: 34, attempts: 13, width: 34 },
];

export const ShotTypeAnalysis: React.FC<ShotTypeAnalysisProps> = ({ shotTypes }) => {
   const data = shotTypes || DEFAULT_SHOT_TYPES;

   return (
      <div className="bg-bgElevation0 rounded-lg p-3 lg:p-5 w-full min-w-0">
         <div className="flex items-center gap-2 mb-1 relative z-50">
            <h3 className="text-sm font-bold text-white">Shot Type Analysis</h3>
            <div className="relative group flex items-center">
               <Info className="w-3.5 h-3.5 text-gray-400 cursor-pointer hover:text-gray-300 transition-colors" />
               <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 hidden group-hover:block w-[280px] bg-[#1a1a1a] text-[#ededed] text-[13px] leading-relaxed rounded-lg p-3 shadow-2xl z-50 border border-[#333333] pointer-events-none">
                  Opponent ranks form a bell curve (1-6 Red/Tough, 25-30 Green/Easy). Colors adjust for Volume Gravity (shot types with very low frequency are pulled to Yellow/Neutral).
               </div>
            </div>
         </div>
         <p className="text-xs text-gray-500 mb-6">25/26 Season</p>

         {/* Labels */}
         <div className="flex text-xs text-gray-400 font-medium mb-2 w-full">
            {data.map((item, idx) => (
               <div key={idx} className="flex justify-center min-w-0" style={{ width: `${item.width}%` }}>
                  <span className="whitespace-nowrap text-center tracking-tight">{item.type}</span>
               </div>
            ))}
         </div>

         {/* Bar */}
         <div className="flex w-full h-14 rounded-lg border border-black/40 text-xs">
            {data.map((item, idx) => {
               const dietPercentage = item.frequency !== undefined ? item.frequency : item.percentage;
               const bgColor = getOppRankColor(item.rank, dietPercentage);

               return (
                  <div
                     key={idx}
                     className={`flex items-center justify-center relative min-w-0 ${idx === 0 ? 'rounded-l-[7px]' : ''
                        } ${idx === data.length - 1 ? 'rounded-r-[7px]' : ''
                        } ${idx < data.length - 1 ? 'border-r-2 border-black/60' : ''}`}
                     style={{ width: `${item.width}%`, backgroundColor: bgColor }}
                  >
                     <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 z-10 bg-white px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold font-chakra text-black shadow-sm flex gap-1 items-center whitespace-nowrap">
                        <span>{item.frequency !== undefined ? item.frequency : item.percentage}%</span>
                        {(item.rank !== undefined || item.attempts > 0) && (
                           <>
                              <span className="text-gray-300 text-[9px]">|</span>
                              <span>{item.rank !== undefined ? item.rank : item.attempts}</span>
                           </>
                        )}
                     </div>
                  </div>
               );
            })}
         </div>
      </div>
   );
};