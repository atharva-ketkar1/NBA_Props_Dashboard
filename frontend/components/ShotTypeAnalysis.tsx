import React from 'react';
import { Info } from 'lucide-react';
import { ShotTypeData } from '../types';
import { colors } from '../utils/propsmadness_colors';

function getOppRankColor(rank?: number) {
   if (rank === undefined) return colors.bgElevation2; // default neutral
   if (rank <= 6) return colors.courtRed;
   if (rank <= 12) return colors.courtOrange;
   if (rank <= 18) return colors.courtYellow;
   if (rank <= 24) return colors.courtGreen;
   return colors.courtLightGreen; // mapped to cover rank 29 as requested
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
      <div className="bg-bgElevation0 rounded-lg p-5 w-full">
         <div className="flex items-center gap-2 mb-1">
            <h3 className="text-sm font-bold text-white">Shot Type Analysis</h3>
            <Info className="w-3.5 h-3.5 text-gray-400" />
         </div>
         <p className="text-xs text-gray-500 mb-6">25/26 Season</p>

         {/* Labels */}
         <div className="flex text-xs text-gray-400 font-medium mb-2 px-1">
            {data.map((item, idx) => (
               <div key={idx} className="text-center" style={{ width: `${item.width}%` }}>
                  {item.type}
               </div>
            ))}
         </div>

         {/* Bar */}
         <div className="flex w-full h-14 rounded-lg overflow-hidden border border-black/40 text-xs">
            {data.map((item, idx) => {
               const bgColor = getOppRankColor(item.rank);

               return (
                  <div
                     key={idx}
                     className={`flex items-center justify-center relative ${idx < data.length - 1 ? 'border-r-2 border-black/60' : ''}`}
                     style={{ width: `${item.width}%`, backgroundColor: bgColor }}
                  >
                     <div className="bg-white px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold text-black shadow-sm flex gap-1 items-center whitespace-nowrap">
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