import React from 'react';
import { Info } from 'lucide-react';
import { ShotTypeData } from '../types';

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
      <div className="bg-card rounded-lg p-5 w-full">
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
         <div className="flex w-full h-14 rounded-lg overflow-hidden border border-black/20 text-xs">
            {data.map((item, idx) => (
               <div
                  key={idx}
                  className={`bg-[#facc15] flex items-center justify-center relative ${idx < data.length - 1 ? 'border-r border-black/10' : ''}`}
                  style={{ width: `${item.width}%` }}
               >
                  <div className="bg-white px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold text-black shadow-sm flex gap-1 items-center whitespace-nowrap">
                     <span>{item.percentage}%</span>
                     {item.attempts > 0 && (
                        <>
                           <span className="text-gray-300 text-[9px]">|</span>
                           <span>{item.attempts}</span>
                        </>
                     )}
                  </div>
               </div>
            ))}
         </div>
      </div>
   );
};