import React from 'react';

// --- 1. SHOOTING ZONES (Basketball Court Heat Map) ---
export const ShootingZones = ({ zones, season = "25/26 Season" }) => {
    // Mock data structure if none provided
    const defaultZones = [
        { zone: 'rim', percentage: 49, attempts: 25, color: 'bg-orange-600' },
        { zone: 'paint', percentage: 15, attempts: 2, color: 'bg-orange-500' },
        { zone: 'midrange', percentage: 11, attempts: 26, color: 'bg-lime-600' },
        { zone: 'corner3_left', percentage: 2, attempts: 19, color: 'bg-orange-500' },
        { zone: 'corner3_right', percentage: 2, attempts: 14, color: 'bg-orange-500' },
        { zone: 'arc3_left', percentage: 21, attempts: 10, color: 'bg-orange-500' },
        { zone: 'arc3_center', percentage: 21, attempts: 10, color: 'bg-orange-500' },
    ];

    const zoneData = zones || defaultZones;

    return (
        <div className="bg-black border border-[#27272a] rounded-xl p-5 h-full flex flex-col">
            {/* Header */}
            <div className="flex justify-between items-center mb-4">
                <div>
                    <h3 className="text-white font-bold text-sm tracking-tight">Shooting Zones</h3>
                    <span className="text-[10px] text-gray-500 font-medium">{season}</span>
                </div>

                {/* Toggle: Player vs Defense */}
                <div className="flex bg-[#18181b] rounded-lg p-0.5 border border-[#27272a] shadow-sm">
                    <button className="bg-[#27272a] text-white text-[10px] font-bold px-3 py-1 rounded transition-colors">
                        Player
                    </button>
                    <button className="text-gray-500 text-[10px] font-bold px-3 py-1 hover:text-white transition-colors">
                        vs
                    </button>
                    <button className="text-gray-400 text-[10px] font-bold px-3 py-1 hover:text-white transition-colors">
                        Defense
                    </button>
                </div>
            </div>

            {/* Court Visualization */}
            <div className="relative w-full flex-1 bg-black mx-auto overflow-hidden flex justify-center items-center min-h-[220px]">
                {/* Court SVG/CSS representation */}
                <div className="relative w-[90%] h-full max-w-[400px]">

                    {/* 3PT Arc - Outer (Orange zones) */}
                    <div className="absolute w-full h-[200%] border-2 border-black bg-orange-600/90 rounded-full top-0 left-0 overflow-hidden">
                        {/* Corner 3s */}
                        <div className="absolute top-[30%] left-[2%] bg-white px-1.5 py-0.5 rounded shadow text-[9px] font-bold text-black">
                            2% | 19
                        </div>
                        <div className="absolute top-[30%] right-[2%] bg-white px-1.5 py-0.5 rounded shadow text-[9px] font-bold text-black">
                            2% | 14
                        </div>
                    </div>

                    {/* Mid-range Zone (Green/Lime) */}
                    <div className="absolute w-[70%] h-[160%] border-2 border-black bg-lime-600/80 rounded-full top-0 left-[15%] flex justify-center pt-12">
                        <div className="bg-white px-1.5 py-0.5 rounded shadow text-[9px] font-bold text-black h-fit">
                            11% | 26
                        </div>
                    </div>

                    {/* Paint Area */}
                    <div className="absolute w-[35%] h-[90%] border-2 border-black bg-orange-500/90 top-0 left-[32.5%]">
                        {/* Restricted area label */}
                    </div>

                    {/* Rim Area - Semi-circle */}
                    <div className="absolute w-[35%] h-[45%] border-2 border-black bg-orange-600/90 top-0 left-[32.5%] rounded-b-full flex justify-center items-center pt-4">
                        <div className="bg-white px-2 py-0.5 rounded shadow text-[10px] font-bold text-black">
                            49% | 25
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

// --- 2. SHOT TYPE ANALYSIS (Horizontal Bar Chart) ---
export const ShotTypeAnalysis = ({ shotTypes, season = "25/26 Season" }) => {
    // Mock data if none provided
    const defaultShotTypes = [
        { type: 'C&S', percentage: 14, attempts: 0, width: 15 },
        { type: '< 10 ft', percentage: 52, attempts: 19, width: 50 },
        { type: 'Pull Up', percentage: 34, attempts: 13, width: 35 },
    ];

    const typeData = shotTypes || defaultShotTypes;

    return (
        <div className="bg-black border border-[#27272a] rounded-xl p-5 h-full flex flex-col">
            {/* Header */}
            <div className="mb-5">
                <h3 className="text-white font-bold text-sm tracking-tight">Shot Type Analysis</h3>
                <span className="text-[10px] text-gray-500 font-medium">{season}</span>
            </div>

            {/* Labels */}
            <div className="flex justify-between text-[10px] text-gray-500 mb-2 px-2 font-bold">
                {typeData.map((type, i) => (
                    <span key={i}>{type.type}</span>
                ))}
            </div>

            {/* Stacked Bar Chart */}
            <div className="flex w-full h-14 rounded-lg overflow-hidden border border-[#27272a] shadow-lg">
                {typeData.map((type, i) => (
                    <div
                        key={i}
                        className={`bg-yellow-500 ${i < typeData.length - 1 ? 'border-r border-black' : ''} flex items-center justify-center text-black font-bold text-xs transition-all hover:brightness-110`}
                        style={{ width: `${type.width}%` }}
                    >
                        {type.percentage}%{type.attempts > 0 && ` | ${type.attempts}`}
                    </div>
                ))}
            </div>
        </div>
    );
};

// --- 3. PLAY TYPE ANALYSIS (List with Rank Badges) ---
export const PlayTypeAnalysis = ({ playTypes, season = "25/26 Season" }) => {
    // Mock data if none provided
    const defaultPlayTypes = [
        { type: 'Transition', points: '7', percentage: 27, oppRank: 12, rankColor: 'bg-yellow-500' },
        { type: 'Free Throws', points: '4.1', percentage: 16, oppRank: 16, rankColor: 'bg-yellow-500' },
        { type: 'PNR Ball Handler', points: '2.6', percentage: 10, oppRank: 28, rankColor: 'bg-green-500' },
        { type: 'Spot Up', points: '2.6', percentage: 10, oppRank: 14, rankColor: 'bg-yellow-500' },
        { type: 'Isolation', points: '2.4', percentage: 9, oppRank: 2, rankColor: 'bg-red-500' },
        { type: 'Post Up', points: '2.3', percentage: 9, oppRank: 2, rankColor: 'bg-red-500' },
    ];

    const playData = playTypes || defaultPlayTypes;

    return (
        <div className="bg-black border border-[#27272a] rounded-xl p-5 h-full flex flex-col">
            {/* Header */}
            <div className="mb-4">
                <h3 className="text-white font-bold text-sm tracking-tight">Play Type Analysis</h3>
                <span className="text-[10px] text-gray-500 font-medium">{season}</span>
            </div>

            {/* Table */}
            <div className="space-y-2.5">
                {/* Column Headers */}
                <div className="flex justify-between text-[9px] text-gray-500 font-bold uppercase tracking-wide pb-2 border-b border-[#27272a]">
                    <span className="flex-1">Play Type</span>
                    <span className="w-24 text-center">Points</span>
                    <span className="w-20 text-right">Opp Rank</span>
                </div>

                {/* Rows */}
                {playData.map((play, i) => (
                    <div
                        key={i}
                        className="flex justify-between items-center py-2.5 border-b border-[#27272a]/30 text-xs hover:bg-white/5 transition-colors rounded px-2 -mx-2"
                    >
                        <span className="text-gray-300 font-medium flex-1">{play.type}</span>
                        <span className="text-gray-400 w-24 text-center font-mono">
                            {play.points} ({play.percentage}%)
                        </span>
                        <span className="w-20 text-right">
                            <span className={`${play.rankColor} text-black font-bold text-[10px] px-2 py-1 rounded shadow-sm inline-block min-w-[28px] text-center`}>
                                {play.oppRank}
                            </span>
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
};

// --- 4. SIMILAR PLAYERS (Comparison Table) ---
export const SimilarPlayers = ({ players, avgDiff = "+0.19", avgDiffPct = "1%" }) => {
    // Mock data if none provided
    const defaultPlayers = [
        {
            date: 'Jan 19',
            team: 'Pacers',
            name: 'P. Siakam',
            line: 23.5,
            result: 24,
            diff: '2%',
            isHit: true
        },
        {
            date: 'Jan 12',
            team: 'Raptors',
            name: 'S. Barnes',
            line: 18.5,
            result: 15,
            diff: '-19%',
            isHit: false
        },
        {
            date: 'Jan 11',
            team: 'Raptors',
            name: 'S. Barnes',
            line: 20.5,
            result: 31,
            diff: '51%',
            isHit: true
        },
        {
            date: 'Dec 30',
            team: 'Grizzlies',
            name: 'J. Jackson Jr.',
            line: 19.5,
            result: 15,
            diff: '-23%',
            isHit: false
        },
    ];

    const playerData = players || defaultPlayers;

    return (
        <div className="bg-black border border-[#27272a] rounded-xl p-5 h-full flex flex-col">
            {/* Header */}
            <div className="mb-4 flex justify-between items-start">
                <div>
                    <h3 className="text-white font-bold text-sm tracking-tight">Similar Players</h3>
                    <div className="text-[10px] text-emerald-400 font-bold mt-1.5">
                        AVG DIFF: {avgDiff} ({avgDiffPct})
                    </div>
                </div>

                {/* Filter Toggle */}
                <div className="bg-[#18181b] border border-[#27272a] px-2.5 py-1 rounded text-[10px] text-white font-bold shadow-sm">
                    By Position
                </div>
            </div>

            {/* Table */}
            <div className="flex-1 overflow-x-auto">
                <table className="w-full text-left border-collapse">
                    <thead>
                        <tr className="text-[9px] text-gray-500 uppercase border-b border-[#27272a] tracking-wide">
                            <th className="pb-2.5 font-bold">Date</th>
                            <th className="pb-2.5 font-bold">Team</th>
                            <th className="pb-2.5 font-bold">Player</th>
                            <th className="pb-2.5 font-bold text-center">Line</th>
                            <th className="pb-2.5 font-bold text-center">Res</th>
                            <th className="pb-2.5 font-bold text-right">Diff</th>
                        </tr>
                    </thead>
                    <tbody className="text-xs">
                        {playerData.map((row, i) => (
                            <tr
                                key={i}
                                className="border-b border-[#27272a]/30 hover:bg-white/5 transition-colors"
                            >
                                <td className="py-3 text-gray-400 font-medium">{row.date}</td>
                                <td className="py-3 text-gray-400">{row.team}</td>
                                <td className="py-3 text-white font-medium">{row.name}</td>
                                <td className="py-3 text-center">
                                    <span className="bg-[#27272a] px-2 py-1 rounded text-white text-[11px] font-mono">
                                        {row.line}
                                    </span>
                                </td>
                                <td className="py-3 text-center">
                                    <span className={`${row.isHit ? 'bg-green-600' : 'bg-red-600'} px-2 py-1 rounded text-white text-[11px] font-mono shadow-sm`}>
                                        {row.result}
                                    </span>
                                </td>
                                <td className="py-3 text-right">
                                    <span className={`${row.isHit ? 'bg-green-600' : 'bg-red-600'} px-2 py-1 rounded text-white text-[11px] font-mono shadow-sm`}>
                                        {row.diff}
                                    </span>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
};