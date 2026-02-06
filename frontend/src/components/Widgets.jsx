import React from 'react';

// --- 1. SHOOTING ZONES (Mock Court) ---
export const ShootingZones = () => (
    <div className="bg-[#09090b] border border-[#27272a] rounded-xl p-5 h-full">
        <div className="flex justify-between items-center mb-4">
            <div>
                <h3 className="text-white font-bold text-sm">Shooting Zones</h3>
                <span className="text-[10px] text-gray-500">25/26 Season</span>
            </div>
            <div className="flex bg-[#18181b] rounded-lg p-1 border border-[#27272a]">
                <div className="bg-[#27272a] text-white text-[10px] font-bold px-3 py-1 rounded">Player</div>
                <div className="text-gray-500 text-[10px] font-bold px-3 py-1">vs</div>
                <div className="text-gray-400 text-[10px] font-bold px-3 py-1">Defense</div>
            </div>
        </div>

        {/* CSS Court Graphic */}
        <div className="relative w-full h-48 bg-black mx-auto mt-4 overflow-hidden flex justify-center">
            {/* Outer Arch */}
            <div className="absolute w-[80%] h-[180%] border-2 border-black bg-[#d97706] rounded-full top-0"></div>
            {/* Inner Zone (Green) */}
            <div className="absolute w-[60%] h-[140%] border-2 border-black bg-[#a3b18a] rounded-full top-0 flex justify-center pt-8">
                <div className="bg-white px-1.5 py-0.5 rounded shadow text-[10px] font-bold text-black h-fit">11% | 26</div>
            </div>
            {/* Paint (Orange) */}
            <div className="absolute w-[30%] h-[80%] border-2 border-black bg-[#d97706] top-0"></div>
            {/* Rim Area */}
            <div className="absolute w-[30%] h-[40%] border-2 border-black bg-[#d97706] top-0 rounded-b-full flex justify-center items-center">
                <div className="bg-white px-1.5 py-0.5 rounded shadow text-[10px] font-bold text-black">49% | 25</div>
            </div>
        </div>
    </div>
);

// --- 2. SHOT TYPE ANALYSIS (Yellow Bars) ---
export const ShotTypeAnalysis = () => (
    <div className="bg-[#09090b] border border-[#27272a] rounded-xl p-5 h-full flex flex-col justify-center">
        <div className="mb-6">
            <h3 className="text-white font-bold text-sm">Shot Type Analysis</h3>
            <span className="text-[10px] text-gray-500">25/26 Season</span>
        </div>

        <div className="flex justify-between text-xs text-gray-400 mb-2 px-2">
            <span>C&S</span>
            <span>&lt; 10 ft</span>
            <span>Pull Up</span>
        </div>

        <div className="flex w-full h-12 rounded-lg overflow-hidden border border-[#27272a]">
            <div className="w-[15%] bg-[#fbbf24] border-r border-black flex items-center justify-center text-black font-bold text-xs">14%</div>
            <div className="w-[50%] bg-[#fbbf24] border-r border-black flex items-center justify-center text-black font-bold text-xs">52% | 19</div>
            <div className="w-[35%] bg-[#fbbf24] flex items-center justify-center text-black font-bold text-xs">34%</div>
        </div>
    </div>
);

// --- 3. PLAY TYPE (List with badges) ---
export const PlayTypeAnalysis = () => {
    const plays = [
        { type: 'Transition', pts: '7 (27%)', rank: 12, color: 'bg-yellow-500' },
        { type: 'Free Throws', pts: '4.1 (16%)', rank: 16, color: 'bg-yellow-500' },
        { type: 'PNR Ball Handler', pts: '2.6 (10%)', rank: 28, color: 'bg-green-500' },
        { type: 'Spot Up', pts: '2.6 (10%)', rank: 14, color: 'bg-yellow-500' },
        { type: 'Isolation', pts: '2.4 (9%)', rank: 2, color: 'bg-red-500' },
    ];
    return (
        <div className="bg-[#09090b] border border-[#27272a] rounded-xl p-5 h-full">
            <div className="mb-4">
                <h3 className="text-white font-bold text-sm">Play Type Analysis</h3>
                <span className="text-[10px] text-gray-500">25/26 Season</span>
            </div>
            <div className="space-y-3">
                <div className="flex justify-between text-[10px] text-gray-500 font-bold uppercase">
                    <span>Play Type</span>
                    <span>Points</span>
                    <span>Opp Rank</span>
                </div>
                {plays.map((p, i) => (
                    <div key={i} className="flex justify-between items-center py-2 border-b border-[#27272a]/50 text-sm">
                        <span className="text-gray-300 font-medium">{p.type}</span>
                        <span className="text-gray-400">{p.pts}</span>
                        <span className={`${p.color} text-black font-bold text-xs px-2 py-0.5 rounded`}>{p.rank}</span>
                    </div>
                ))}
            </div>
        </div>
    );
};

// --- 4. SIMILAR PLAYERS (Table) ---
export const SimilarPlayers = () => {
    const players = [
        { date: 'Jan 19', team: 'Pacers', name: 'P. Siakam', line: 23.5, res: 24, diff: '2%', color: 'bg-green-600' },
        { date: 'Jan 12', team: 'Raptors', name: 'S. Barnes', line: 18.5, res: 15, diff: '-19%', color: 'bg-red-600' },
        { date: 'Jan 11', team: 'Raptors', name: 'S. Barnes', line: 20.5, res: 31, diff: '51%', color: 'bg-green-600' },
        { date: 'Dec 30', team: 'Grizzlies', name: 'J. Jackson Jr.', line: 19.5, res: 15, diff: '-23%', color: 'bg-red-600' },
    ];
    return (
        <div className="bg-[#09090b] border border-[#27272a] rounded-xl p-5 h-full">
            <div className="mb-4 flex justify-between items-start">
                <div>
                    <h3 className="text-white font-bold text-sm">Similar Players</h3>
                    <div className="text-[10px] text-green-400 font-bold mt-1">AVG DIFF: +0.19 (1%)</div>
                </div>
                <div className="bg-[#18181b] border border-[#27272a] px-2 py-1 rounded text-[10px] text-white font-bold">
                    By Position
                </div>
            </div>
            <table className="w-full text-left border-collapse">
                <thead>
                    <tr className="text-[10px] text-gray-500 uppercase border-b border-[#27272a]">
                        <th className="pb-2 font-bold">Date</th>
                        <th className="pb-2 font-bold">Team</th>
                        <th className="pb-2 font-bold">Player</th>
                        <th className="pb-2 font-bold text-center">Line</th>
                        <th className="pb-2 font-bold text-center">Res</th>
                        <th className="pb-2 font-bold text-right">Diff</th>
                    </tr>
                </thead>
                <tbody className="text-xs">
                    {players.map((row, i) => (
                        <tr key={i} className="border-b border-[#27272a]/30 group hover:bg-[#18181b]">
                            <td className="py-3 text-gray-400">{row.date}</td>
                            <td className="py-3 text-gray-400">{row.team}</td>
                            <td className="py-3 text-white font-medium">{row.name}</td>
                            <td className="py-3 text-center"><span className="bg-[#27272a] px-1.5 py-0.5 rounded text-white">{row.line}</span></td>
                            <td className="py-3 text-center"><span className={`${row.color} px-1.5 py-0.5 rounded text-white`}>{row.res}</span></td>
                            <td className="py-3 text-right"><span className={`${row.color} px-1.5 py-0.5 rounded text-white`}>{row.diff}</span></td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};