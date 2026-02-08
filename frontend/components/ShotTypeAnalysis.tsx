import React from 'react';
import { Info } from 'lucide-react';

export const ShotTypeAnalysis: React.FC = () => {
  return (
    <div className="bg-card rounded-lg p-5 w-full">
      <div className="flex items-center gap-2 mb-1">
         <h3 className="text-sm font-bold text-white">Shot Type Analysis</h3>
         <Info className="w-3.5 h-3.5 text-gray-400" />
      </div>
      <p className="text-xs text-gray-500 mb-6">25/26 Season</p>

      {/* Labels */}
      <div className="flex text-xs text-gray-400 font-medium mb-2 px-1">
        <div className="w-[16%] text-center">C&S</div>
        <div className="w-[50%] text-center">&lt; 10 ft</div>
        <div className="w-[34%] text-center">Pull Up</div>
      </div>

      {/* Bar */}
      <div className="flex w-full h-14 rounded-lg overflow-hidden border border-black/20">
         <div className="w-[16%] bg-[#facc15] flex items-center justify-center border-r border-black/10 relative">
            <div className="bg-white px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold text-black shadow-sm flex gap-1 items-center">
                <span>14%</span><span className="text-gray-300 text-[9px]">|</span><span>13</span>
            </div>
         </div>
         <div className="w-[50%] bg-[#facc15] flex items-center justify-center border-r border-black/10 relative">
            <div className="bg-white px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold text-black shadow-sm flex gap-1 items-center">
                <span>52%</span><span className="text-gray-300 text-[9px]">|</span><span>19</span>
            </div>
         </div>
         <div className="w-[34%] bg-[#facc15] flex items-center justify-center relative">
            <div className="bg-white px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold text-black shadow-sm flex gap-1 items-center">
                <span>34%</span><span className="text-gray-300 text-[9px]">|</span><span>13</span>
            </div>
         </div>
      </div>
    </div>
  );
};