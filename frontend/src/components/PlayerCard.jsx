import React, { useState, useMemo } from 'react';
import { SlidersHorizontal } from 'lucide-react';

const PlayerCard = ({ player }) => {
    const [activeTab, setActiveTab] = useState('PTS');
    const [activeSportsbook, setActiveSportsbook] = useState('dk'); // 'dk' or 'fd'

    // Tab configuration matching PropsMadness
    const TABS = [
        { label: 'Points', key: 'PTS' },
        { label: 'Assists', key: 'AST' },
        { label: 'Rebounds', key: 'REB' },
        { label: 'Threes', key: 'FG3M' },
        { label: 'Pts+Ast', key: 'PTS+AST' },
        { label: 'Pts+Reb', key: 'PTS+REB' },
        { label: 'Reb+Ast', key: 'REB+AST' },
        { label: 'Pts+Reb+Ast', key: 'PTS+REB+AST' },
    ];

    // Header stats with calculated differences from season average
    const HEADER_STATS = [
        { label: 'PTS', key: 'PTS' },
        { label: 'AST', key: 'AST' },
        { label: 'REB', key: 'REB' },
        { label: '3PM', key: 'FG3M' },
        { label: 'MINS', key: 'MIN' },
    ];

    // Get prop line and odds for active tab and sportsbook
    const propData = player?.props?.[activeTab]?.[activeSportsbook] || {};
    const line = propData.line || 0;
    const overOdds = propData.over || -110;
    const underOdds = propData.under || -110;

    // Calculate combo stat values for game log
    const calculateComboStat = (game, statKey) => {
        if (game[statKey] !== undefined) return game[statKey];

        // Calculate combined stats if not present
        const statMap = {
            'PTS+AST': (game.PTS || 0) + (game.AST || 0),
            'PTS+REB': (game.PTS || 0) + (game.REB || 0),
            'REB+AST': (game.REB || 0) + (game.AST || 0),
            'PTS+REB+AST': (game.PTS || 0) + (game.REB || 0) + (game.AST || 0),
        };

        return statMap[statKey] || game.PTS || 0;
    };

    // Process game log data for chart
    const graphData = useMemo(() => {
        if (!player?.game_log) return [];

        return player.game_log.slice(0, 32).reverse().map((game, i) => {
            const val = calculateComboStat(game, activeTab);
            const date = new Date(game.GAME_DATE);
            const monthShort = date.toLocaleDateString('en-US', { month: 'short' });
            const day = date.getDate();

            return {
                ...game,
                val,
                isHit: val >= line,
                id: i,
                dateLabel: `${monthShort} ${day}`,
                // Extract opponent from MATCHUP (e.g., "UTA @ ATL" -> "ATL")
                opponent: game.MATCHUP.includes('@')
                    ? game.MATCHUP.split('@')[1].trim()
                    : game.MATCHUP.split('vs.')[1]?.trim() || game.MATCHUP.split('vs')[1]?.trim() || 'OPP'
            };
        });
    }, [player, activeTab, line]);

    // Calculate hit rate
    const hitCount = graphData.filter(g => g.isHit).length;
    const hitRate = graphData.length ? ((hitCount / graphData.length) * 100).toFixed(1) : 0;

    // Calculate max value for chart scaling
    const maxVal = Math.max(...graphData.map(g => g.val), line) * 1.25;

    // Calculate stat differences (last 5 games vs season average)
    const calculateDiff = (statKey) => {
        if (!player?.game_log || player.game_log.length < 5) return { diff: '+0.0', color: 'text-gray-400' };

        const recent5 = player.game_log.slice(0, 5);
        const recentAvg = recent5.reduce((sum, game) => sum + (game[statKey] || 0), 0) / 5;
        const seasonAvg = player.stats?.[statKey] || 0;
        const diff = recentAvg - seasonAvg;

        return {
            diff: `${diff >= 0 ? '+' : ''}${diff.toFixed(1)}`,
            color: diff >= 0 ? 'text-emerald-400' : 'text-red-400'
        };
    };

    if (!player) return null;

    return (
        <div className="w-full bg-[#0a0a0a] text-white font-sans rounded-2xl overflow-hidden shadow-2xl flex flex-col ring-1 ring-white/10 relative">

            {/* Hide scrollbar */}
            <style>{`
                .scrollbar-hide::-webkit-scrollbar { display: none; }
                .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            `}</style>

            {/* A. TABS - Uppercase, tight spacing */}
            <div className="flex items-center gap-0 overflow-x-auto bg-[#09090b] scrollbar-hide border-b border-white/10 h-11 flex-shrink-0">
                {TABS.map((tab) => {
                    const isActive = activeTab === tab.key;
                    return (
                        <button
                            key={tab.key}
                            onClick={() => setActiveTab(tab.key)}
                            className={`
                                h-full px-6 text-[10px] font-bold uppercase tracking-widest whitespace-nowrap transition-all border-b-[3px]
                                ${isActive
                                    ? 'text-white border-white bg-black/30'
                                    : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-white/5'}
                            `}
                        >
                            {tab.label}
                        </button>
                    );
                })}
            </div>

            {/* B. MAIN CONTENT */}
            <div className="p-6 flex-1 flex flex-col bg-black">

                {/* 1. HERO HEADER */}
                <div className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-4 mb-6">

                    {/* Left: Player Identity */}
                    <div className="flex gap-4 items-center">
                        {/* Player Avatar */}
                        <div className="relative">
                            <div className="w-16 h-16 rounded-full border-2 border-orange-500/80 bg-gradient-to-br from-purple-900 to-purple-700 flex items-center justify-center font-black text-xl text-yellow-400 shadow-lg flex-shrink-0">
                                {player.name.split(' ').map(n => n[0]).join('').slice(0, 3).toUpperCase()}
                            </div>
                            {/* Team logo placeholder - top right corner */}
                            <div className="absolute -top-1 -right-1 w-6 h-6 rounded-full bg-orange-500 border-2 border-black flex items-center justify-center text-[8px] font-black text-white">
                                {player.team}
                            </div>
                        </div>

                        <div>
                            {/* Player Name & Position */}
                            <div className="flex items-center gap-2 mb-1">
                                <h1 className="text-xl font-bold text-white tracking-tight">{player.name}</h1>
                                <span className="text-gray-400 font-bold text-[10px] bg-white/10 px-2 py-0.5 rounded uppercase">
                                    {player.position || 'F'}
                                </span>
                            </div>

                            {/* Betting Badge - Sportsbook tabs */}
                            <div className="flex items-center gap-2">
                                {/* Sportsbook Badge */}
                                <div className="flex items-center bg-[#18181b] rounded-md border border-white/10 h-7 shadow-sm overflow-hidden">
                                    {/* FD Tab */}
                                    <button
                                        onClick={() => setActiveSportsbook('fd')}
                                        className={`h-full px-2 flex items-center justify-center transition-colors ${activeSportsbook === 'fd'
                                                ? 'bg-[#3b82f6]'
                                                : 'bg-[#1e293b] hover:bg-[#334155]'
                                            }`}
                                    >
                                        <span className="text-[9px] font-black text-white">FD</span>
                                    </button>

                                    {/* DK Tab */}
                                    <button
                                        onClick={() => setActiveSportsbook('dk')}
                                        className={`h-full px-2 flex items-center justify-center transition-colors ${activeSportsbook === 'dk'
                                                ? 'bg-[#3b82f6]'
                                                : 'bg-[#1e293b] hover:bg-[#334155]'
                                            }`}
                                    >
                                        <span className="text-[9px] font-black text-white">DK</span>
                                    </button>

                                    {/* Line & Odds */}
                                    <div className="px-3 flex items-center gap-2">
                                        <span className="font-bold text-white text-xs">
                                            {line} <span className="text-[9px] text-gray-400 uppercase">{activeTab.replace('FG3M', '3PM')}</span>
                                        </span>
                                        <span className="text-[9px] text-emerald-400 font-bold font-mono">
                                            O {overOdds > 0 ? '+' : ''}{overOdds}
                                        </span>
                                        <span className="text-[9px] text-red-400 font-bold font-mono">
                                            U {underOdds > 0 ? '+' : ''}{underOdds}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Right: Stats Cluster */}
                    <div className="flex flex-1 items-center justify-between xl:justify-end gap-6 w-full xl:w-auto">

                        {/* Hit Rate - Prominent */}
                        <div className="text-center flex flex-col items-center px-6 border-r border-white/10">
                            <div className="text-[9px] text-gray-500 font-bold uppercase tracking-widest mb-1">Hit Rate</div>
                            <div className={`text-3xl font-black leading-none ${Number(hitRate) >= 50 ? 'text-emerald-400' : 'text-red-400'
                                }`}>
                                {hitRate}%
                            </div>
                            <div className="text-[10px] text-gray-400 font-medium mt-0.5">
                                ({hitCount}/{graphData.length})
                            </div>
                        </div>

                        {/* Season Stats */}
                        <div className="flex gap-6 overflow-x-auto scrollbar-hide items-center">
                            {HEADER_STATS.map(stat => {
                                const diffData = calculateDiff(stat.key);
                                return (
                                    <div key={stat.key} className="flex flex-col items-center min-w-[40px]">
                                        <div className="text-[9px] text-gray-500 font-bold mb-1 tracking-wider uppercase">
                                            {stat.label}
                                        </div>
                                        <div className="text-xl font-bold text-white leading-none">
                                            {player.stats?.[stat.key]?.toFixed(1) || '-'}
                                        </div>
                                        <div className={`text-[9px] font-bold mt-1 ${diffData.color}`}>
                                            {diffData.diff}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>

                        {/* Filter Button */}
                        <button className="hidden lg:flex items-center gap-2 px-4 py-2 rounded-md border border-white/10 bg-[#18181b] text-xs font-bold text-white hover:bg-[#27272a] transition">
                            <SlidersHorizontal size={14} />
                            <span>Filters</span>
                        </button>
                    </div>
                </div>

                {/* 2. PERFORMANCE CHART */}
                <div className="relative w-full flex-1 min-h-[280px] select-none mt-4">

                    {/* Y-Axis Labels */}
                    <div className="absolute left-0 top-0 bottom-8 flex flex-col justify-between text-[9px] text-gray-600 font-mono pointer-events-none z-10 pr-2">
                        <span>{Math.round(maxVal)}</span>
                        <span>{Math.round(maxVal * 0.5)}</span>
                        <span>0</span>
                    </div>

                    {/* Prop Line - Yellow */}
                    <div
                        className="absolute left-8 right-0 border-t-2 border-yellow-400 z-20 pointer-events-none"
                        style={{ bottom: `${((line / maxVal) * 100) + 8}%` }}
                    >
                        <div className="absolute left-0 -top-6 bg-yellow-400 text-black text-[9px] font-black px-2 py-0.5 rounded">
                            LINE
                        </div>
                    </div>

                    {/* Chart Bars */}
                    <div className="absolute inset-0 left-8 bottom-8 flex items-end justify-between gap-[2px] pb-2">
                        {graphData.map((game, i) => {
                            const heightPct = Math.min((game.val / maxVal) * 100, 100);

                            return (
                                <div
                                    key={game.id}
                                    className="flex-1 flex flex-col justify-end h-full group relative min-w-[8px] max-w-[30px]"
                                >
                                    {/* Tooltip on Hover */}
                                    <div className="absolute -top-16 left-1/2 -translate-x-1/2 bg-[#18181b] border border-white/20 text-white text-[10px] p-2 rounded shadow-2xl opacity-0 group-hover:opacity-100 z-50 pointer-events-none whitespace-nowrap transition-opacity">
                                        <div className="font-bold text-xs mb-1">
                                            {game.val} {activeTab}
                                        </div>
                                        <div className="text-gray-400 text-[9px]">{game.MATCHUP}</div>
                                        <div className="text-gray-400 text-[9px]">{game.dateLabel}</div>
                                    </div>

                                    {/* Bar */}
                                    <div
                                        className={`w-full rounded-t transition-all duration-300 ${game.isHit
                                                ? 'bg-emerald-500 hover:bg-emerald-400'
                                                : 'bg-red-500 hover:bg-red-400'
                                            } shadow-sm relative`}
                                        style={{ height: `${heightPct}%` }}
                                    >
                                        {/* Value inside bar (hidden on mobile) */}
                                        <div className="absolute top-2 w-full text-center text-white text-[9px] font-bold leading-none opacity-80 hidden md:block">
                                            {game.val}
                                        </div>
                                    </div>

                                    {/* Date & Team Labels below bar */}
                                    <div className="mt-1 flex flex-col items-center gap-0.5 min-h-[40px]">
                                        {/* Opponent Team Logo */}
                                        <div className="w-5 h-5 rounded-full bg-gray-800 border border-white/10 flex items-center justify-center text-[7px] font-bold text-gray-300">
                                            {game.opponent.substring(0, 3)}
                                        </div>
                                        {/* Date */}
                                        <div className="text-[7px] text-gray-600 font-medium whitespace-nowrap hidden lg:block">
                                            {game.dateLabel.split(' ')[0]}
                                        </div>
                                        <div className="text-[8px] text-gray-500 font-bold hidden lg:block">
                                            {game.dateLabel.split(' ')[1]}
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