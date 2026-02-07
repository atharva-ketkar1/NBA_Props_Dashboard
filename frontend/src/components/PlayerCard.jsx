import React, { useState, useMemo } from 'react';
import { SlidersHorizontal } from 'lucide-react';

const PlayerCard = ({ player }) => {
    const [activeTab, setActiveTab] = useState('PTS');
    const [activeSportsbook, setActiveSportsbook] = useState('dk'); // 'dk' or 'fd'

    // Tab configuration matching PropsMadness
    // EXPANDED TAB LIST (Matching the original's density)
    const TABS = [
        { label: 'Points', key: 'PTS' },
        { label: 'Assists', key: 'AST' },
        { label: 'Rebounds', key: 'REB' },
        { label: 'Threes', key: 'FG3M' },
        { label: 'Pts+Ast', key: 'PTS+AST' },
        { label: 'Pts+Reb', key: 'PTS+REB' },
        { label: 'Reb+Ast', key: 'REB+AST' },
        { label: 'Pts+Reb+Ast', key: 'PTS+REB+AST' },
        // New Advanced Tabs
        { label: 'Double Double', key: 'DD2' },
        { label: 'Triple Double', key: 'TD3' },
        { label: '1Q Points', key: 'PTS_1Q' },
        { label: '1Q Assists', key: 'AST_1Q' },
        { label: '1Q Rebounds', key: 'REB_1Q' },
        { label: '1H Points', key: 'PTS_1H' },
        { label: '1H Assists', key: 'AST_1H' },
        { label: '1H Rebounds', key: 'REB_1H' },
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

    // --- NEW: Dynamic Image URLs ---
    // Extract Team ID from the most recent game log entry (reliable source)
    const teamId = player.game_log?.[0]?.TEAM_ID;

    // Construct URLs pointing to your local server
    // Note: This assumes you are running 'npx serve' from the 'backend' folder
    const headshotUrl = `http://localhost:5000/assets/player_headshots/${player.id}.png`;
    const teamLogoUrl = `http://localhost:5000/assets/team_logos/${teamId}.svg`;

    return (
        <div className="w-full bg-[#0a0a0a] text-white font-sans rounded-2xl overflow-hidden shadow-2xl flex flex-col ring-1 ring-white/10 relative">

            {/* Hide scrollbar */}
            <style>{`
                .scrollbar-hide::-webkit-scrollbar { display: none; }
                .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            `}</style>

            {/* A. TABS - SCROLLABLE CONTAINER */}
            <div className="w-full border-b border-[#27272a] bg-[#09090b]">
                <div className="flex items-center gap-0 overflow-x-auto scrollbar-hide">
                    {TABS.map((tab) => {
                        const isActive = activeTab === tab.key;
                        return (
                            <button
                                key={tab.key}
                                onClick={() => setActiveTab(tab.key)}
                                className={`
                                    relative h-10 px-4 flex items-center justify-center text-[10px] font-bold uppercase tracking-wider whitespace-nowrap transition-all
                                    ${isActive
                                        ? 'text-white bg-[#18181b]'
                                        : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'}
                                `}
                            >
                                {tab.label}
                                {/* Active Indicator Line at Bottom */}
                                {isActive && (
                                    <div className="absolute bottom-0 left-0 w-full h-[2px] bg-blue-500"></div>
                                )}
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* B. MAIN CONTENT */}
            <div className="p-6 flex-1 flex flex-col bg-black">

                {/* 1. HERO HEADER - REFACTORED FOR PHASE 2 */}
                <div className="flex flex-col xl:flex-row justify-between items-start gap-6 mb-2">

                    {/* LEFT: Player Identity & Betting Lines */}
                    <div className="flex flex-col gap-3 min-w-[300px]">

                        {/* Top Row: Avatar + Name + Team */}
                        <div className="flex items-center gap-4">
                            {/* Avatar (Slightly smaller, cleaner border) */}
                            <div className="relative w-14 h-14 rounded-full border-2 border-[#27272a] overflow-hidden bg-[#18181b]">
                                <img
                                    src={headshotUrl}
                                    alt={player.name}
                                    className="w-full h-full object-cover transform scale-125 pt-1.5"
                                    onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'flex'; }}
                                />
                                <div className="hidden w-full h-full items-center justify-center font-black text-sm text-gray-500 bg-[#18181b]">
                                    {player.name.slice(0, 2)}
                                </div>
                            </div>

                            {/* Name & Team Info Stack */}
                            <div className="flex flex-col justify-center">
                                {/* Team Name + Position Row */}
                                <div className="flex items-center gap-2 mb-0.5">
                                    {teamId && (
                                        <img src={teamLogoUrl} alt="Team" className="w-4 h-4 object-contain opacity-80" />
                                    )}
                                    <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                                        {player.team} • {player.position || 'F'}
                                    </span>
                                </div>
                                {/* Player Name - Big & Bright */}
                                <h1 className="text-2xl font-black text-white leading-none tracking-tight">
                                    {player.name}
                                </h1>
                            </div>
                        </div>

                        {/* Bottom Row: The Betting Badge (Compact) */}
                        <div className="flex items-center gap-2 mt-1">
                            <div className="flex items-center bg-[#18181b] rounded-md border border-[#27272a] h-8 overflow-hidden">
                                {/* Book Toggle */}
                                <div className="flex h-full border-r border-[#27272a]">
                                    {['fd', 'dk'].map((book) => (
                                        <button
                                            key={book}
                                            onClick={() => setActiveSportsbook(book)}
                                            className={`px-2.5 h-full flex items-center justify-center transition-all ${activeSportsbook === book
                                                    ? 'bg-blue-600 text-white'
                                                    : 'text-gray-600 hover:text-gray-400'
                                                }`}
                                        >
                                            <span className="text-[10px] font-black uppercase">{book}</span>
                                        </button>
                                    ))}
                                </div>
                                {/* The Line */}
                                <div className="px-3 flex items-center gap-3">
                                    <div className="flex items-baseline gap-1">
                                        <span className="text-base font-black text-white">{line}</span>
                                        <span className="text-[9px] font-bold text-gray-500 uppercase">{activeTab}</span>
                                    </div>
                                    <div className="w-px h-3 bg-[#27272a]"></div>
                                    <div className="flex gap-2">
                                        <span className="text-[10px] font-bold text-gray-400">
                                            o{overOdds}
                                        </span>
                                        <span className="text-[10px] font-bold text-gray-400">
                                            u{underOdds}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* RIGHT: Stats Grid (The "Dashboard" Look) */}
                    <div className="flex items-center gap-2 w-full xl:w-auto overflow-x-auto pb-2 xl:pb-0">

                        {/* 1. The HIT RATE Box (Key Feature) */}
                        <div className="flex flex-col justify-center items-center h-16 w-28 bg-[#18181b] border border-[#27272a] rounded-lg mr-2 flex-shrink-0">
                            <span className="text-[9px] text-gray-500 font-bold tracking-widest uppercase mb-0.5">Hit Rate</span>
                            <div className="flex items-baseline gap-1.5">
                                <span className={`text-2xl font-black ${Number(hitRate) >= 50 ? 'text-emerald-500' : 'text-red-500'}`}>
                                    {hitRate}%
                                </span>
                                <span className="text-[9px] font-bold text-gray-600">
                                    {hitCount}/{graphData.length}
                                </span>
                            </div>
                        </div>

                        {/* 2. The Stats Columns */}
                        <div className="flex items-center gap-1 flex-shrink-0">
                            {HEADER_STATS.map((stat, i) => {
                                const diffData = calculateDiff(stat.key);
                                return (
                                    <div key={stat.key} className={`flex flex-col items-center justify-center w-14 h-16 ${i !== HEADER_STATS.length - 1 ? 'border-r border-[#27272a]' : ''}`}>
                                        {/* Label */}
                                        <span className="text-[9px] font-bold text-gray-600 uppercase mb-0.5">{stat.label}</span>
                                        {/* Value */}
                                        <span className="text-base font-bold text-white leading-none mb-0.5">
                                            {player.stats?.[stat.key]?.toFixed(1) || '-'}
                                        </span>
                                        {/* Diff Indicator (Pill style) */}
                                        <span className={`text-[9px] font-bold px-1 rounded ${diffData.diff.includes('+')
                                                ? 'text-emerald-500 bg-emerald-500/10'
                                                : diffData.diff.includes('-')
                                                    ? 'text-red-500 bg-red-500/10'
                                                    : 'text-gray-500'
                                            }`}>
                                            {diffData.diff}
                                        </span>
                                    </div>
                                );
                            })}
                        </div>
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