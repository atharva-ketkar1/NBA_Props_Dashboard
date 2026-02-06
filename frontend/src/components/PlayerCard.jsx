import React, { useState, useMemo } from 'react';
import { Filter, Info, ChevronRight, TrendingUp, TrendingDown, SlidersHorizontal } from 'lucide-react';

// --- 1. MOCK DATA (Expanded to match the screenshot) ---
const MOCK_PLAYER = {
    "id": 2544,
    "name": "LeBron James",
    "team": "LAL",
    "position": "F",
    "stats": {
        "PTS": 22.5, "AST": 6.4, "REB": 5.8, "FG3M": 1.5, "MIN": 33.1, "USAGE": 27.6, "FGA": 16.4
    },
    // We simulate the "Last 32 games" shown in your screenshot
    "game_log": Array.from({ length: 32 }, (_, i) => ({
        "GAME_DATE": "2026-02-05", "MATCHUP": "vs NYK", "WL": i % 3 === 0 ? "L" : "W",
        "PTS": Math.floor(Math.random() * (36 - 15) + 15), // Random stats for visuals
        "AST": Math.floor(Math.random() * (12 - 4) + 4),
        "REB": Math.floor(Math.random() * (10 - 2) + 2),
        "FG3M": Math.floor(Math.random() * 5),
    })),
    "props": {
        "PTS": { "dk": { "line": 20.5, "over": -120, "under": -130 } },
        "AST": { "dk": { "line": 6.5, "over": -122, "under": -108 } },
        "REB": { "dk": { "line": 5.5, "over": -110, "under": -110 } },
        "PTS+REB+AST": { "dk": { "line": 35.5, "over": -115, "under": -115 } } // Added this example
    }
};

const PlayerCard = ({ player = MOCK_PLAYER }) => {
    const [activeTab, setActiveTab] = useState('PTS');

    // --- 2. CONFIGURATION ---
    // The exact tab list from your screenshot
    const TABS = [
        { label: 'Points', key: 'PTS' },
        { label: 'Assists', key: 'AST' },
        { label: 'Rebounds', key: 'REB' },
        { label: 'Threes', key: 'FG3M' },
        { label: 'Pts+Ast', key: 'PTS+AST' }, // derived logic would be needed for real data
        { label: 'Pts+Reb', key: 'PTS+REB' },
        { label: 'Reb+Ast', key: 'REB+AST' },
        { label: 'Pts+Reb+Ast', key: 'PTS+REB+AST' },
        { label: 'Double Double', key: 'DD2' },
        { label: 'Triple Double', key: 'TD3' },
    ];

    // The 7 Stat Columns from the screenshot
    const HEADER_STATS = [
        { label: 'PTS', key: 'PTS', diff: '+8.5', color: 'text-emerald-500' },
        { label: 'AST', key: 'AST', diff: '-3.6', color: 'text-rose-500' },
        { label: 'REB', key: 'REB', diff: '+1.3', color: 'text-emerald-500' },
        { label: '3PM', key: 'FG3M', diff: '+0.5', color: 'text-emerald-500' },
        { label: 'MINS', key: 'MIN', diff: '+1.1', color: 'text-emerald-500' },
        { label: 'USAGE', key: 'USAGE', diff: '+7.5%', color: 'text-emerald-500', isPercent: true },
        { label: 'FGA', key: 'FGA', diff: '+3.9', color: 'text-emerald-500' },
    ];

    // --- 3. DATA PROCESSING ---
    const propData = player.props?.[activeTab] || {};
    // Fallback logic: if prop doesn't exist, just show a placeholder line
    const line = propData.dk?.line || 20.5;
    const overOdds = propData.dk?.over || -110;
    const underOdds = propData.dk?.under || -110;

    // Generate Graph Data
    const graphData = useMemo(() => {
        const logs = player.game_log || [];
        return logs.slice(0, 32).reverse().map((game, i) => {
            const val = game[activeTab] || game.PTS; // Fallback to PTS for derived tabs in mock
            return {
                ...game,
                val,
                isHit: val >= line,
                id: i
            };
        });
    }, [player, activeTab, line]);

    const hitCount = graphData.filter(g => g.isHit).length;
    const hitRate = ((hitCount / graphData.length) * 100).toFixed(1);
    const maxVal = Math.max(...graphData.map(g => g.val), line) * 1.3;

    return (
        <div className="w-full bg-[#09090b] text-white font-sans rounded-xl overflow-hidden border border-[#27272a] shadow-2xl p-0">

            {/* --- A. TABS ROW (Scrollable, Darker Background) --- */}
            <div className="flex items-center gap-1 overflow-x-auto bg-[#09090b] border-b border-[#27272a] px-2 pt-2 scrollbar-hide">
                {TABS.map((tab) => {
                    const isActive = activeTab === tab.key;
                    return (
                        <button
                            key={tab.key}
                            onClick={() => setActiveTab(tab.key)}
                            className={`
                                px-4 py-3 text-xs font-bold uppercase tracking-wide whitespace-nowrap transition-all border-b-2
                                ${isActive
                                    ? 'text-white border-gray-400'
                                    : 'text-gray-500 border-transparent hover:text-gray-300 hover:border-gray-800'}
                            `}
                        >
                            {tab.label}
                        </button>
                    )
                })}
                {/* Fade effect on the right if scrolling needed */}
                <div className="flex-1 min-w-[20px]"></div>
            </div>

            <div className="p-6">
                {/* --- B. HERO SECTION --- */}
                <div className="flex justify-between items-start mb-6">
                    {/* 1. Player Identity */}
                    <div className="flex gap-4 items-center">
                        <div className="w-14 h-14 rounded-full border-2 border-yellow-500 overflow-hidden bg-gray-800">
                            {/* Use a real image tag here if you have URLs, otherwise initial */}
                            <div className="w-full h-full flex items-center justify-center font-bold text-xl bg-purple-900 text-yellow-400">LBJ</div>
                        </div>
                        <div>
                            <div className="flex items-center gap-2">
                                <h1 className="text-xl font-bold text-gray-100">{player.name}</h1>
                                <span className="text-gray-500 font-bold text-sm">{player.position}</span>
                            </div>

                            {/* The Betting Line Badge (Exact visual match) */}
                            <div className="mt-2 flex items-center bg-[#18181b] rounded overflow-hidden border border-[#27272a] h-8">
                                <div className="bg-blue-600 h-full px-2 flex items-center justify-center">
                                    {/* Mock Fanduel/DK Logo */}
                                    <span className="text-[10px] font-bold text-white">FD</span>
                                </div>
                                <div className="px-3 flex items-center gap-2">
                                    <span className="font-bold text-white text-sm">{line} <span className="text-[10px] text-gray-400 uppercase">{activeTab.replace('FG3M', '3PM')}</span></span>
                                    <span className="text-[10px] text-emerald-400 font-mono">O {overOdds}</span>
                                    <span className="text-[10px] text-rose-400 font-mono">U {underOdds}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* 2. Hit Rate (Center) */}
                    <div className="text-center flex flex-col items-center">
                        <div className="text-[10px] text-gray-500 font-bold uppercase tracking-wider mb-1">Hit Rate</div>
                        <div className={`text-2xl font-black ${Number(hitRate) > 50 ? 'text-[#22c55e]' : 'text-rose-500'}`}>
                            {hitRate}% <span className="text-lg text-gray-600 font-bold">({hitCount}/{graphData.length})</span>
                        </div>
                        <div className="text-[10px] text-gray-600 font-medium mt-0.5">{hitCount} of {graphData.length} games</div>
                    </div>

                    {/* 3. Stats Grid (Right) */}
                    <div className="flex gap-5 text-center">
                        {HEADER_STATS.map(stat => (
                            <div key={stat.key} className="flex flex-col items-center">
                                <div className="text-[10px] text-gray-500 font-bold mb-1">{stat.label}</div>
                                <div className="text-lg font-bold text-white leading-none">
                                    {player.stats[stat.key]}{stat.isPercent ? '%' : ''}
                                </div>
                                <div className={`text-[10px] font-bold mt-1 ${stat.color}`}>
                                    {stat.diff}
                                </div>
                            </div>
                        ))}
                        {/* Filters Button */}
                        <div className="h-full flex items-start pt-1 pl-2">
                            <button className="flex items-center gap-2 px-3 py-1.5 rounded border border-[#27272a] bg-[#18181b] text-xs font-bold text-white hover:bg-[#27272a] transition">
                                <SlidersHorizontal size={14} /> Filters
                            </button>
                        </div>
                    </div>
                </div>

                {/* --- C. CHART SECTION (Pixel Perfect Bars) --- */}
                <div className="relative h-64 w-full mt-8 select-none">

                    {/* Y-Axis Labels (Left) */}
                    <div className="absolute left-0 top-0 bottom-6 flex flex-col justify-between text-[10px] text-gray-600 font-mono pointer-events-none z-10">
                        <span>{Math.round(maxVal)}</span>
                        <span>{Math.round(maxVal * 0.66)}</span>
                        <span>{Math.round(maxVal * 0.33)}</span>
                        <span>0</span>
                    </div>

                    {/* The Yellow Line (Target) */}
                    <div
                        className="absolute left-6 right-0 border-t border-yellow-500/80 z-20 shadow-[0_0_10px_rgba(234,179,8,0.2)]"
                        style={{ bottom: `${(line / maxVal) * 100}%` }}
                    >
                        {/* Line Tag */}
                        <div className="absolute left-1/2 -translate-x-1/2 -bottom-6 bg-[#18181b] border border-yellow-500/30 text-yellow-500 text-[9px] font-bold px-1.5 py-0.5 rounded flex items-center gap-1">
                            <div className="w-1.5 h-1.5 bg-yellow-500 rounded-sm"></div> LINE
                        </div>
                    </div>

                    {/* Bars Container */}
                    <div className="absolute inset-0 left-6 flex items-end justify-between gap-1">
                        {graphData.map((game, i) => {
                            const heightPct = Math.min((game.val / maxVal) * 100, 100);
                            const isHit = game.val >= line;
                            const isLoss = game.WL === "L"; // For visual differentiation (optional)

                            return (
                                <div key={i} className="flex-1 flex flex-col justify-end h-full group relative">

                                    {/* Tooltip */}
                                    <div className="absolute -top-12 left-1/2 -translate-x-1/2 bg-[#18181b] border border-[#27272a] text-white text-xs p-2 rounded shadow-xl opacity-0 group-hover:opacity-100 z-50 pointer-events-none whitespace-nowrap transition-opacity">
                                        <div className="font-bold">{game.val} {activeTab}</div>
                                        <div className="text-gray-400 text-[10px]">{game.MATCHUP} • {game.GAME_DATE}</div>
                                    </div>

                                    {/* The Bar */}
                                    <div
                                        className={`
                                            w-full rounded-t-[2px] relative transition-all duration-300
                                            ${isHit ? 'bg-[#22c55e]' : 'bg-[#ef4444]'}
                                            hover:brightness-110
                                        `}
                                        style={{ height: `${heightPct}%` }}
                                    >
                                        {/* Value Label INSIDE Bar */}
                                        <div className="absolute bottom-1 w-full text-center text-white text-[9px] font-bold leading-none mix-blend-overlay">
                                            {game.val}
                                        </div>
                                    </div>

                                    {/* X-Axis Logos */}
                                    <div className="mt-2 flex flex-col items-center gap-1 h-8 justify-start">
                                        <div className="w-4 h-4 rounded-full bg-gray-800 flex items-center justify-center text-[6px] text-white border border-gray-700 overflow-hidden">
                                            {/* Mock Logo */}
                                            {game.MATCHUP.split(' ')[1].substring(0, 3)}
                                        </div>
                                        <div className="text-[7px] text-gray-500 font-mono leading-tight text-center">
                                            {/* Mock Date format: "Nov 28" -> "Nov\n28" */}
                                            <div className="uppercase">Jan</div>
                                            <div>{i + 1}</div>
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                        {/* The '?' Bar (Prediction Placeholder) */}
                        <div className="flex-1 border-l border-dashed border-gray-700 ml-1 flex flex-col justify-end h-full items-center opacity-50">
                            <div className="mb-2 text-white font-bold text-sm">?</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default PlayerCard;