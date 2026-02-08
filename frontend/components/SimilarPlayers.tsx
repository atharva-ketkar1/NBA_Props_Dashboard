import React from 'react';
import { Info, ChevronLeft, ChevronRight } from 'lucide-react';
import { SIMILAR_GAMES } from '../constants';

export const SimilarPlayers: React.FC = () => {
  return (
    <div className="bg-card rounded-lg p-5 w-full flex flex-col h-full">
      <div className="flex justify-between items-start mb-4">
         <div>
            <div className="flex items-center gap-2 mb-1">
               <h3 className="text-sm font-bold text-white">Similar Players Pts vs 76ers</h3>
               <Info className="w-3.5 h-3.5 text-gray-400" />
            </div>
            <p className="text-xs text-gray-500">25/26 Season</p>
         </div>

         <div className="flex bg-[#121214] rounded-lg p-1 border border-border shrink-0 ml-2">
            <button className="text-xs font-bold px-3 py-1.5 rounded-md text-white bg-[#27272a] shadow-sm border border-border/50 whitespace-nowrap">By PropsMadness</button>
            <button className="text-xs font-bold px-3 py-1.5 rounded-md text-gray-400 hover:text-white transition-colors whitespace-nowrap">By Position</button>
         </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-3 mb-6 gap-2">
         <div className="text-center">
            <div className="text-[10px] text-[#71717a] font-bold uppercase mb-1 whitespace-nowrap">Avg Diff</div>
            <div className="text-[#22c55e] font-bold text-lg">0.19</div>
         </div>
         <div className="text-center">
            <div className="text-[10px] text-[#71717a] font-bold uppercase mb-1 whitespace-nowrap">Avg Diff %</div>
            <div className="text-[#22c55e] font-bold text-lg">1%</div>
         </div>
         <div className="text-center">
            <div className="text-[10px] text-[#71717a] font-bold uppercase mb-1 whitespace-nowrap">Hit Rate</div>
            <div className="text-[#ef4444] font-bold text-lg whitespace-nowrap">38% (5/13)</div>
         </div>
      </div>

      {/* Table */}
      <div className="w-full overflow-x-auto custom-scrollbar pb-2">
         <div className="min-w-[600px]">
             <div className="grid grid-cols-[1fr_1.5fr_2fr_1fr_1fr_1fr] text-[10px] text-[#71717a] font-bold uppercase mb-3 px-2">
                <div>Date</div>
                <div>Team</div>
                <div>Player</div>
                <div className="text-center">Line</div>
                <div className="text-center">Result</div>
                <div className="text-right">Diff %</div>
             </div>

             <div className="space-y-1">
                {SIMILAR_GAMES.map((game, idx) => (
                    <div key={idx} className="grid grid-cols-[1fr_1.5fr_2fr_1fr_1fr_1fr] text-xs items-center py-2.5 px-2 hover:bg-[#121214] rounded transition-colors border-b border-[#27272a]/40 last:border-0">
                        <div className="text-gray-300 font-medium">{game.date}</div>
                        <div className="text-gray-300">{game.team}</div>
                        <div className="text-white font-medium truncate pr-2">{game.player}</div>
                        <div className="text-center">
                            <span className={`px-1.5 py-0.5 rounded text-white font-bold bg-[#27272a] border border-[#3f3f46] text-[11px] ${game.line > 24 ? 'text-red-400' : 'text-green-400'}`}>
                                {game.line}
                            </span>
                        </div>
                        <div className="text-center">
                            <span className={`px-1.5 py-0.5 rounded-[4px] text-white font-bold text-[11px] min-w-[30px] inline-block ${game.result > game.line ? 'bg-[#16a34a]' : 'bg-[#dc2626]'}`}>
                                {game.result}
                            </span>
                        </div>
                        <div className="text-right">
                             <span className={`px-1.5 py-0.5 rounded-[4px] text-white font-bold text-[11px] min-w-[36px] inline-block text-center ${game.diffPercent > 0 ? 'bg-[#16a34a]' : 'bg-[#dc2626]'}`}>
                                {game.diffPercent}%
                            </span>
                        </div>
                    </div>
                ))}
             </div>
         </div>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-center gap-2 mt-auto pt-4 text-xs font-bold text-gray-400">
         <div className="w-6 h-6 bg-[#27272a] rounded flex items-center justify-center text-blue-500 cursor-pointer hover:bg-gray-700">
            <ChevronLeft className="w-3 h-3" />
         </div>
         <span>1 / 2</span>
         <ChevronRight className="w-3 h-3 cursor-pointer hover:text-white" />
      </div>

    </div>
  );
};