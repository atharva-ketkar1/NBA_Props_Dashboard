import React, { useState, useMemo } from 'react';
import { SlidersHorizontal } from 'lucide-react';

const MOCK_PLAYER = {
    "id": 2544,
    "name": "LeBron James",
    "team": "LAL",
    "position": "F",
    "stats": {
        "PTS": 22.5, "AST": 6.4, "REB": 5.8, "FG3M": 1.5, "MIN": 33.1, "USAGE": 27.6, "FGA": 16.4
    },
    "game_log": Array.from({ length: 32 }, (_, i) => ({
        "GAME_DATE": "2026-02-05", "MATCHUP": "vs NYK", "WL": i % 3 === 0 ? "L" : "W",
        "PTS": Math.floor(Math.random() * (36 - 15) + 15),
        "AST": Math.floor(Math.random() * (12 - 4) + 4),
        "REB": Math.floor(Math.random() * (10 - 2) + 2),
        "FG3M": Math.floor(Math.random() * 5),
    })),
    "props": {
        "PTS": { "dk": { "line": 20.5, "over": -120, "under": -130 } },
        "AST": { "dk": { "line": 6.5, "over": -122, "under": -108 } }
    }
};

const PlayerCard = ({ player = MOCK_PLAYER }) => {
    const [activeTab, setActiveTab] = useState('PTS');

    const TABS = [
        { label: 'Points', key: 'PTS' },
        { label: 'Assists', key: 'AST' },
        { label: 'Rebounds', key: 'REB' },
        { label: 'Threes', key: 'FG3M' },
        { label: 'Pts+Ast', key: 'PTS+AST' },
        { label: 'Pts+Reb', key: 'PTS+REB' },
        { label: 'Reb+Ast', key: 'REB+AST' },
        { label: 'Pts+Reb+Ast', key: 'PTS+REB+AST' },
        { label: 'Double Double', key: 'DD2' },
        { label: 'Triple Double', key: 'TD3' },
    ];

    const HEADER_STATS = [
        { label: 'PTS', key: 'PTS', diff: '+8.5', color: 'text-emerald-400' },
        { label: 'AST', key: 'AST', diff: '-3.6', color: 'text-red-400' },
        { label: 'REB', key: 'REB', diff: '+1.3', color: 'text-emerald-400' },
        { label: '3PM', key: 'FG3M', diff: '+0.5', color: 'text-emerald-400' },
        { label: 'MINS', key: 'MIN', diff: '+1.1', color: 'text-emerald-400' },
        { label: 'USAGE', key: 'USAGE', diff: '+7.5%', color: 'text-emerald-400', isPercent: true },
        { label: 'FGA', key: 'FGA', diff: '+3.9', color: 'text-emerald-400' },
    ];

    const propData = player.props?.[activeTab] || {};
    const line = propData.dk?.line || 20.5;
    const overOdds = propData.dk?.over || -110;
    const underOdds = propData.dk?.under || -110;

    const graphData = useMemo(() => {
        const logs = player.game_log || [];
        return logs.slice(0, 32).reverse().map((game, i) => {
            const val = game[activeTab] || game.PTS;
            return { ...game, val, isHit: val >= line, id: i };
        });
    }, [player, activeTab, line]);

    const hitCount = graphData.filter(g => g.isHit).length;
    const hitRate = graphData.length ? ((hitCount / graphData.length) * 100).toFixed(1) : 0;
    const maxVal = Math.max(...graphData.map(g => g.val), line) * 1.3;

    return (
        <div className="w-full bg-[#121212] text-white font-sans rounded-2xl overflow-hidden shadow-lg flex flex-col ring-1 ring-white/5 relative">

            {/* Force Hide Scrollbar Style */}
            <style>{`
                .scrollbar-hide::-webkit-scrollbar { display: none; }
                .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            `}</style>

            {/* A. TABS - Compact & Clean */}
            <div className="flex items-center gap-1 overflow-x-auto bg-[#0a0a0a] px-4 scrollbar-hide border-b border-white/5 h-12 flex-shrink-0">
                {TABS.map((tab) => {
                    const isActive = activeTab === tab.key;
                    return (
                        <button
                            key={tab.key}
                            onClick={() => setActiveTab(tab.key)}
                            className={`
                                h-full px-4 text-xs font-bold uppercase tracking-wide whitespace-nowrap transition-all border-b-2 flex items-center
                                ${isActive
                                    ? 'text-white border-white'
                                    : 'text-gray-500 border-transparent hover:text-gray-300'}
                            `}
                        >
                            {tab.label}
                        </button>
                    )
                })}
            </div>

            {/* B. MAIN CONTENT - Reduced Padding */}
            <div className="p-5 flex-1 flex flex-col">

                {/* 1. HERO HEADER - Single Row Layout */}
                <div className="flex flex-col xl:flex-row justify-between items-center gap-6 mb-6">

                    {/* Left: Player Identity */}
                    <div className="flex gap-4 items-center self-start xl:self-center">
                        <div className="w-14 h-14 rounded-full border-2 border-yellow-500 bg-gray-800 flex items-center justify-center font-bold text-xl bg-purple-900 text-yellow-400 shadow-lg flex-shrink-0">
                            LBJ
                        </div>
                        <div>
                            <div className="flex items-center gap-2">
                                <h1 className="text-xl font-bold text-white tracking-tight">{player.name}</h1>
                                <span className="text-gray-500 font-bold text-xs bg-white/5 px-1.5 py-0.5 rounded">{player.position}</span>
                            </div>

                            {/* Betting Badge */}
                            <div className="mt-1.5 flex items-center bg-[#18181b] rounded-md border border-white/10 h-7 w-fit shadow-sm overflow-hidden">
                                <div className="bg-[#3b82f6] h-full px-2 flex items-center justify-center">
                                    <span className="text-[9px] font-black text-white">FD</span>
                                </div>
                                <div className="px-2.5 flex items-center gap-2">
                                    <span className="font-bold text-white text-xs">{line} <span className="text-[9px] text-gray-400 uppercase">{activeTab.replace('FG3M', '3PM')}</span></span>
                                    <span className="text-[9px] text-emerald-400 font-bold font-mono">O {overOdds}</span>
                                    <span className="text-[9px] text-red-400 font-bold font-mono">U {underOdds}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Right: Stats Cluster - FORCED Single Row */}
                    <div className="flex flex-1 items-center justify-between xl:justify-end gap-4 w-full xl:w-auto">

                        {/* Hit Rate Group */}
                        <div className="text-center flex flex-col items-center px-4 border-r border-white/10">
                            <div className="text-[9px] text-gray-500 font-bold uppercase tracking-widest mb-0.5">Hit Rate</div>
                            <div className={`text-2xl font-black leading-none ${Number(hitRate) > 50 ? 'text-emerald-400' : 'text-red-400'}`}>
                                {hitRate}%
                            </div>
                            <div className="text-[10px] text-gray-500 font-medium">({hitCount}/{graphData.length})</div>
                        </div>

                        {/* Season Stats Group */}
                        <div className="flex gap-4 sm:gap-6 overflow-x-auto scrollbar-hide items-center">
                            {HEADER_STATS.map(stat => (
                                <div key={stat.key} className="flex flex-col items-center min-w-[30px]">
                                    <div className="text-[9px] text-gray-500 font-bold mb-0.5 tracking-wider">{stat.label}</div>
                                    <div className="text-lg font-bold text-white leading-none">
                                        {player.stats ? player.stats[stat.key] : '-'}{stat.isPercent ? '%' : ''}
                                    </div>
                                    <div className={`text-[9px] font-bold mt-0.5 ${stat.color}`}>{stat.diff}</div>
                                </div>
                            ))}
                        </div>

                        {/* Filter Button (Desktop Only) */}
                        <button className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded border border-white/10 bg-[#18181b] text-xs font-bold text-white hover:bg-[#27272a] transition ml-2">
                            <SlidersHorizontal size={12} /> <span className="hidden lg:inline">Filters</span>
                        </button>
                    </div>
                </div>

                {/* 2. CHART - Optimized Height */}
                <div className="relative w-full flex-1 min-h-[220px] select-none">
                    {/* Y-Axis */}
                    <div className="absolute left-0 top-0 bottom-6 flex flex-col justify-between text-[9px] text-gray-600 font-mono pointer-events-none z-10">
                        <span>{Math.round(maxVal)}</span>
                        <span>{Math.round(maxVal * 0.5)}</span>
                        <span>0</span>
                    </div>

                    {/* Yellow Line */}
                    <div className="absolute left-6 right-0 border-t border-yellow-400 z-20 shadow-[0_0_8px_rgba(250,204,21,0.4)]" style={{ bottom: `${(line / maxVal) * 100}%` }}>
                        <div className="absolute left-1/2 -translate-x-1/2 -bottom-5 bg-[#18181b] border border-yellow-500/30 text-yellow-400 text-[8px] font-bold px-1.5 py-0.5 rounded flex items-center gap-1">LINE</div>
                    </div>

                    {/* Bars */}
                    <div className="absolute inset-0 left-6 flex items-end justify-between gap-1">
                        {graphData.map((game, i) => {
                            const heightPct = Math.min((game.val / maxVal) * 100, 100);
                            return (
                                <div key={i} className="flex-1 flex flex-col justify-end h-full group relative min-w-[5px]">
                                    {/* Tooltip */}
                                    <div className="absolute -top-10 left-1/2 -translate-x-1/2 bg-[#18181b] border border-white/10 text-white text-[10px] p-2 rounded shadow-xl opacity-0 group-hover:opacity-100 z-50 pointer-events-none whitespace-nowrap transition-all">
                                        <div className="font-bold">{game.val} {activeTab}</div>
                                        <div className="text-gray-400">{game.MATCHUP}</div>
                                    </div>

                                    {/* Bar */}
                                    <div className={`w-full rounded-t-[2px] transition-all duration-300 ${game.isHit ? 'bg-[#22c55e]' : 'bg-[#ef4444]'} hover:brightness-110 shadow-sm`} style={{ height: `${heightPct}%` }}>
                                        <div className="absolute bottom-1 w-full text-center text-white text-[8px] font-bold leading-none mix-blend-overlay hidden sm:block">
                                            {game.val}
                                        </div>
                                    </div>

                                    {/* Logos */}
                                    <div className="mt-2 flex flex-col items-center gap-1 h-6 justify-start opacity-70 group-hover:opacity-100">
                                        <div className="w-4 h-4 rounded-full bg-[#18181b] flex items-center justify-center text-[6px] text-white border border-white/10 overflow-hidden hidden sm:flex">
                                            {game.MATCHUP.split(' ')[1].substring(0, 3)}
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default PlayerCard;