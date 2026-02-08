import React, { useState, useMemo } from 'react';
import { ChevronDown } from 'lucide-react';

const TEAM_ID_MAP = {
    ATL: 1610612737, BOS: 1610612738, BKN: 1610612751, CHA: 1610612766,
    CHI: 1610612741, CLE: 1610612739, DAL: 1610612742, DEN: 1610612743,
    DET: 1610612765, GSW: 1610612744, HOU: 1610612745, IND: 1610612754,
    LAC: 1610612746, LAL: 1610612747, MEM: 1610612763, MIA: 1610612748,
    MIL: 1610612749, MIN: 1610612750, NOP: 1610612740, NYK: 1610612752,
    OKC: 1610612760, ORL: 1610612753, PHI: 1610612755, PHX: 1610612756,
    POR: 1610612757, SAC: 1610612758, SAS: 1610612759, TOR: 1610612761,
    UTA: 1610612762, WAS: 1610612764
};

const PlayerCard = ({ player }) => {
    const [activeTab, setActiveTab] = useState('PTS');
    const [activeSportsbook, setActiveSportsbook] = useState('dk');

    // --- Auto-switch sportsbook if current one has no line ---
    React.useEffect(() => {
        const currentProps = player?.props?.[activeTab]?.[activeSportsbook];
        const hasLine = currentProps && currentProps.line > 0;

        if (!hasLine) {
            const books = ['dk', 'fd'];
            const validBook = books.find(book => {
                const p = player?.props?.[activeTab]?.[book];
                return p && p.line > 0;
            });
            if (validBook) setActiveSportsbook(validBook);
        }
    }, [activeTab, player]);

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
        { label: '1Q Points', key: 'PTS_1Q' },
        { label: '1Q Assists', key: 'AST_1Q' },
        { label: '1Q Rebounds', key: 'REB_1Q' },
        { label: '1H Points', key: 'PTS_1H' },
        { label: '1H Assists', key: 'AST_1H' },
        { label: '1H Rebounds', key: 'REB_1H' },
    ];

    const HEADER_STATS = [
        { label: 'PTS', key: 'PTS' },
        { label: 'AST', key: 'AST' },
        { label: 'REB', key: 'REB' },
        { label: '3PM', key: 'FG3M' },
        { label: 'MINS', key: 'MIN' },
    ];

    const propData = player?.props?.[activeTab]?.[activeSportsbook] || {};
    const line = propData.line || 0;
    const overOdds = propData.over || -110;
    const underOdds = propData.under || -110;

    const getOpponentAbbrev = (matchup) => {
        if (!matchup) return null;
        if (matchup.includes('@')) return matchup.split('@')[1].trim();
        if (matchup.toLowerCase().includes('vs')) return matchup.split('vs')[1].replace('.', '').trim();
        return null;
    };

    const calculateComboStat = (game, statKey) => {
        if (game[statKey] !== undefined && game[statKey] !== null) return game[statKey];
        const comboStats = {
            'PTS+AST': (game.PTS || 0) + (game.AST || 0),
            'PTS+REB': (game.PTS || 0) + (game.REB || 0),
            'REB+AST': (game.REB || 0) + (game.AST || 0),
            'PTS+REB+AST': (game.PTS || 0) + (game.REB || 0) + (game.AST || 0),
        };
        if (comboStats[statKey] !== undefined) return comboStats[statKey];
        if (statKey.includes('_1Q')) {
            const baseStat = statKey.replace('_1Q', '');
            return game[`${baseStat}_1Q`] || game[`1Q_${baseStat}`] || game[baseStat] || 0;
        }
        if (statKey.includes('_1H')) {
            const baseStat = statKey.replace('_1H', '');
            return game[`${baseStat}_1H`] || game[`1H_${baseStat}`] || game[baseStat] || 0;
        }
        if (statKey === 'DD2' || statKey === 'TD3') return game[statKey] || 0;
        return game.PTS || 0;
    };

    const graphData = useMemo(() => {
        if (!player?.game_log) return [];
        return player.game_log.slice(0, 30).reverse().map((game, i) => {
            const val = calculateComboStat(game, activeTab);
            const [year, month, day] = game.GAME_DATE.split('-').map(Number);
            const date = new Date(year, month - 1, day);
            const opponentAbbrev = getOpponentAbbrev(game.MATCHUP);
            const opponentTeamId = TEAM_ID_MAP[opponentAbbrev];
            return {
                ...game,
                val,
                isHit: val >= line,
                id: i,
                dateLabel: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
                opponentAbbrev,
                opponentTeamId
            };
        });
    }, [player, activeTab, line]);

    const maxVal = useMemo(() => {
        if (!graphData.length) return line > 0 ? line * 1.25 : 10;
        const dataMax = Math.max(...graphData.map(g => g.val));
        const actualMax = Math.max(dataMax, line);
        return actualMax + (actualMax * 0.15);
    }, [graphData, line]);

    const hitCount = graphData.filter(g => g.isHit).length;
    const hitRate = graphData.length ? ((hitCount / graphData.length) * 100).toFixed(1) : 0;

    const calculateDiff = (statKey) => {
        if (!player?.game_log || player.game_log.length < 5) return { diff: '+0.0', color: 'var(--colors-text-general-tertiary)' };
        const recent5 = player.game_log.slice(0, 5);
        const recentAvg = recent5.reduce((sum, game) => sum + (game[statKey] || 0), 0) / 5;
        const seasonAvg = player.stats?.[statKey] || 0;
        const diff = recentAvg - seasonAvg;
        return {
            diff: `${diff >= 0 ? '+' : ''}${diff.toFixed(1)}`,
            color: diff >= 0 ? 'text-[var(--colors-graph-bar-over)]' : 'text-[var(--colors-graph-bar-under)]'
        };
    };

    if (!player) return null;

    const teamId = player.game_log?.[0]?.TEAM_ID;
    const headshotUrl = `http://localhost:5000/assets/player_headshots/${player.id}.png`;
    const teamLogoUrl = `http://localhost:5000/assets/team_logos/${teamId}.svg`;

    return (
        <div className="w-full text-white font-sans rounded-2xl overflow-hidden shadow-2xl flex flex-col ring-1 ring-white/5 relative"
            style={{ backgroundColor: 'var(--colors-bg-elevation-2)' }}>

            {/* INJECTED CSS VARIABLES FROM SNIPCSS */}
            <style type="text/css">{`
                :root {
                    --colors-bg-elevation-2: #18181b; /* Zinc-900 equivalent */
                    --colors-bg-elevation-0: #09090b; /* Zinc-950 equivalent */
                    --colors-text-general-primary: #ffffff;
                    --colors-text-general-tertiary: #a1a1aa; /* Zinc-400 */
                    --colors-graph-bar-over: #22c55e; /* Green-500 */
                    --colors-graph-bar-under: #ef4444; /* Red-500 */
                    --colors-graph-line-minutes: #eab308; /* Yellow-500 */
                    --colors-border-subtle: #27272a; /* Zinc-800 */
                }
                .scrollbar-hide::-webkit-scrollbar { display: none; }
                .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
            `}</style>

            {/* A. TABS */}
            <div className="w-full border-b border-[var(--colors-border-subtle)]" style={{ backgroundColor: 'var(--colors-bg-elevation-0)' }}>
                <div className="flex items-center gap-0 overflow-x-auto scrollbar-hide">
                    {TABS.map((tab) => {
                        const propsObj = player?.props?.[tab.key];
                        const hasValidLine = propsObj && Object.values(propsObj).some(book => book.line > 0);
                        const isActive = activeTab === tab.key;

                        return (
                            <button
                                key={tab.key}
                                onClick={() => hasValidLine && setActiveTab(tab.key)}
                                disabled={!hasValidLine}
                                className={`
                                    relative h-12 px-5 flex items-center justify-center text-[11px] font-bold uppercase tracking-wider whitespace-nowrap transition-all group
                                    ${isActive ? 'text-white bg-[var(--colors-border-subtle)]' : 'text-[var(--colors-text-general-tertiary)] hover:text-white hover:bg-white/5'}
                                    ${!hasValidLine ? 'opacity-40 cursor-not-allowed grayscale hover:bg-transparent' : 'cursor-pointer'}
                                `}
                            >
                                {tab.label}
                                {isActive && hasValidLine && (
                                    <div className="absolute bottom-0 left-0 w-full h-[2px] bg-blue-500"></div>
                                )}
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* B. MAIN CONTENT */}
            <div className="p-6 flex-1 flex flex-col" style={{ backgroundColor: 'var(--colors-bg-elevation-2)' }}>

                {/* 1. HEADER */}
                <div className="flex flex-col xl:flex-row justify-between items-start gap-8 mb-6 relative z-40">
                    {/* Player Info */}
                    <div className="flex flex-col gap-4 min-w-[300px]">
                        <div className="flex items-center gap-4">
                            <div className="relative w-16 h-16 rounded-full border-2 border-[var(--colors-border-subtle)] overflow-hidden bg-[var(--colors-bg-elevation-0)]">
                                <img src={headshotUrl} alt={player.name} className="w-full h-full object-cover transform scale-125 pt-2"
                                    onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'flex'; }} />
                                <div className="hidden w-full h-full items-center justify-center font-black text-sm text-[var(--colors-text-general-tertiary)]">
                                    {player.name.slice(0, 2)}
                                </div>
                            </div>
                            <div className="flex flex-col justify-center">
                                <div className="flex items-center gap-2 mb-1">
                                    {teamId && <img src={teamLogoUrl} alt="Team" className="w-5 h-5 object-contain opacity-80" />}
                                    <span className="text-xs font-bold uppercase tracking-wider text-[var(--colors-text-general-tertiary)]">
                                        {player.team} • {player.position || 'F'}
                                    </span>
                                </div>
                                <h1 className="text-3xl font-black text-white leading-none tracking-tight">{player.name}</h1>
                            </div>
                        </div>

                        {/* Betting Badge */}
                        <div className="flex items-center gap-2 mt-1">
                            <div className="flex items-center rounded-md border border-[var(--colors-border-subtle)] h-10 overflow-visible relative"
                                style={{ backgroundColor: 'var(--colors-bg-elevation-0)' }}>
                                <div className="relative h-full border-r border-[var(--colors-border-subtle)] group z-50">
                                    <button className={`h-full px-3 flex items-center justify-center gap-2 transition-all min-w-[120px] cursor-pointer ${activeSportsbook === 'fd' ? 'text-[#0090FF]' : 'text-[#53d337]'}`}>
                                        <span className="text-xs font-bold text-white">{activeSportsbook === 'fd' ? 'FanDuel' : 'DraftKings'}</span>
                                        <ChevronDown size={16} className="text-[var(--colors-text-general-tertiary)] group-hover:text-white transition-colors" />
                                    </button>
                                </div>
                                <div className="px-4 flex items-center gap-3">
                                    <span className="text-xl font-black text-white">{line > 0 ? line : '-'}</span>
                                    <span className="text-[10px] font-bold uppercase text-[var(--colors-text-general-tertiary)]">{activeTab}</span>
                                    {line > 0 && (
                                        <>
                                            <div className="w-px h-4 bg-[var(--colors-border-subtle)]"></div>
                                            <div className="flex gap-2">
                                                <span className="text-xs font-bold text-[var(--colors-text-general-tertiary)]">o{overOdds}</span>
                                                <span className="text-xs font-bold text-[var(--colors-text-general-tertiary)]">u{underOdds}</span>
                                            </div>
                                        </>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Stats Grid */}
                    <div className="flex items-center gap-6 w-full xl:w-auto overflow-x-auto pb-2 xl:pb-0">
                        {/* Hit Rate */}
                        <div className="flex flex-col justify-center items-center h-20 w-40 rounded-xl flex-shrink-0 shadow-lg relative overflow-hidden"
                            style={{ backgroundColor: 'var(--colors-bg-elevation-0)' }}>
                            <div className="absolute top-0 left-0 w-full h-1 bg-white/5"></div>
                            <span className="text-[10px] font-bold tracking-widest uppercase mb-1 text-[var(--colors-text-general-tertiary)]">Hit Rate</span>
                            <div className="flex flex-col items-center">
                                <span className={`text-3xl font-black leading-none ${Number(hitRate) >= 50 ? 'text-[var(--colors-graph-bar-over)]' : 'text-[var(--colors-graph-bar-under)]'}`}>
                                    {hitRate}%
                                </span>
                                <span className="text-xs font-bold mt-1 text-[var(--colors-text-general-tertiary)]">
                                    {hitCount}/{graphData.length}
                                </span>
                            </div>
                        </div>

                        {/* Other Stats */}
                        <div className="flex items-center gap-6 flex-shrink-0">
                            {HEADER_STATS.map((stat) => {
                                const diffData = calculateDiff(stat.key);
                                return (
                                    <div key={stat.key} className="flex flex-col items-center justify-between h-16 w-16">
                                        <span className="text-[10px] font-bold uppercase text-[var(--colors-text-general-tertiary)]">{stat.label}</span>
                                        <span className="text-2xl font-black text-white leading-none">{player.stats?.[stat.key]?.toFixed(1) || '-'}</span>
                                        <span className={`text-xs font-bold ${diffData.color}`}>{diffData.diff}</span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>

                {/* 2. PERFORMANCE CHART (Applying SnipCSS colors/styles to bars) */}
                <div className="relative w-full flex-1 min-h-[300px] select-none mt-8">
                    {/* Y-Axis */}
                    <div className="absolute left-0 top-0 bottom-14 flex flex-col justify-between text-[10px] font-mono pointer-events-none z-10 pr-2 text-[var(--colors-text-general-tertiary)]">
                        <span>{Math.round(maxVal)}</span>
                        <span>{Math.round(maxVal * 0.66)}</span>
                        <span>{Math.round(maxVal * 0.33)}</span>
                        <span>0</span>
                    </div>

                    {/* Prop Line (Dashed) */}
                    <div className="absolute left-8 right-0 bottom-14 top-0 z-20 pointer-events-none">
                        <div className="absolute left-0 right-0 border-t-2 border-dashed opacity-80"
                            style={{
                                bottom: line > 0 ? `${(line / maxVal) * 100}%` : '0%',
                                display: line > 0 ? 'block' : 'none',
                                borderColor: 'var(--colors-graph-line-minutes)'
                            }}>
                            <div className="absolute -left-10 -translate-y-1/2 text-[#0a0a0a] text-xs font-black px-2 py-0.5 rounded-sm"
                                style={{ backgroundColor: 'var(--colors-graph-line-minutes)' }}>
                                {line.toFixed(1)}
                            </div>
                        </div>
                    </div>

                    {/* Bars */}
                    <div className="absolute inset-0 left-8 bottom-14 flex items-end justify-between gap-[6px]">
                        {graphData.map((game) => {
                            const heightPct = maxVal > 0 ? Math.max((game.val / maxVal) * 100, line > 0 ? 2 : 0) : 0;
                            const isHit = game.val >= line;

                            return (
                                <div key={game.id} className="flex-1 flex flex-col justify-end h-full group relative min-w-[24px]">
                                    {/* Bar Body: Using rx="4px" equivalent and SnipCSS colors */}
                                    <div
                                        className="w-full transition-all duration-300 relative group-hover:brightness-110 rounded-t-[4px]"
                                        style={{
                                            height: `${heightPct}%`,
                                            backgroundColor: line === 0 ? '#3B82F6' : isHit ? 'var(--colors-graph-bar-over)' : 'var(--colors-graph-bar-under)'
                                        }}
                                    >
                                        <div className="absolute bottom-1 w-full text-center text-white text-sm font-black leading-none hidden md:block text-shadow-sm">
                                            {game.val.toFixed(game.val % 1 === 0 ? 0 : 1)}
                                        </div>
                                    </div>

                                    {/* X-Axis */}
                                    <div className="absolute top-full left-0 w-full pt-3 flex flex-col items-center gap-1">
                                        <div className="w-7 h-7 rounded-full border border-white/10 flex items-center justify-center p-1"
                                            style={{ backgroundColor: 'var(--colors-bg-elevation-0)' }}>
                                            {game.opponentTeamId ? (
                                                <img src={`http://localhost:5000/assets/team_logos/${game.opponentTeamId}.svg`}
                                                    alt={game.opponentAbbrev} className="w-full h-full object-contain opacity-90"
                                                    onError={(e) => { e.target.style.display = 'none'; }} />
                                            ) : (
                                                <span className="text-[8px] font-bold text-gray-500">{game.opponentAbbrev}</span>
                                            )}
                                        </div>
                                        <div className="flex flex-col items-center leading-none mt-0.5">
                                            <span className="text-[9px] font-bold uppercase text-[var(--colors-text-general-tertiary)]">
                                                {game.dateLabel.split(' ')[0]}
                                            </span>
                                            <span className="text-xs text-white font-black">
                                                {game.dateLabel.split(' ')[1]}
                                            </span>
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