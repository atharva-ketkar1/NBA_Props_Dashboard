import React from 'react';
import { Info } from 'lucide-react';
import { PLAY_TYPES } from '../constants';

const RankBadge = ({ rank }: { rank: number }) => {
    // 1-10 Red (Hard), 11-20 Yellow (Mid), 21+ Green (Easy)
    let colorClass = "bg-[#facc15] text-black"; 
    if (rank <= 10) colorClass = "bg-[#ef4444] text-white"; 
    if (rank > 20) colorClass = "bg-[#22c55e] text-white"; 

    return (
        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-[4px] ${colorClass} min-w-[24px] text-center inline-block`}>
            {rank}
        </span>
    )
}

export const PlayTypeAnalysis: React.FC = () => {
  return (
    <div className="bg-card rounded-lg p-5 w-full">
      <div className="flex items-center gap-2 mb-1">
         <h3 className="text-sm font-bold text-white">Play Type Analysis</h3>
         <Info className="w-3.5 h-3.5 text-gray-400" />
      </div>
      <p className="text-xs text-gray-500 mb-4">25/26 Season</p>

      <div className="w-full">
         <div className="grid grid-cols-[2fr_1fr_1fr] text-[10px] text-[#71717a] font-bold uppercase tracking-wider border-b border-[#27272a] pb-2 mb-2">
            <div>Play Type</div>
            <div className="text-center">Player Points</div>
            <div className="text-right">Opp Def Rank</div>
         </div>
         
         <div className="space-y-3">
            {PLAY_TYPES.map((item, idx) => (
                <div key={idx} className="grid grid-cols-[2fr_1fr_1fr] text-xs items-center border-b border-[#27272a]/40 pb-2 last:border-0 hover:bg-[#27272a]/20 rounded px-1 -mx-1 transition-colors">
                    <div className="text-white font-medium">{item.type}</div>
                    <div className="text-gray-300 text-center">{item.points}</div>
                    <div className="text-right">
                        <RankBadge rank={item.rank} />
                    </div>
                </div>
            ))}
         </div>
      </div>
    </div>
  );
};