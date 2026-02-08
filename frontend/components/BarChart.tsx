import React, { useMemo } from 'react';
import { Player, GameLog } from '../types';
import { ImageWithFallback } from './ui/ImageWithFallback';
import { TEAM_IDS } from '../constants';

interface BarChartProps {
    player?: Player;
    activeTab: string;
    activeSportsbook: 'dk' | 'fd' | 'mgm' | 'cz';
}

const STAT_LABELS: Record<string, string> = {
    'Points': 'PTS',
    'Assists': 'AST',
    'Rebounds': 'REB',
    'Threes': 'FG3M',
    'Pts+Ast': 'PTS+AST',
    'Pts+Reb': 'PTS+REB',
    'Reb+Ast': 'REB+AST',
    'Pts+Reb+Ast': 'PTS+REB+AST',
    'Fantasy': 'FAN',
    'Blocks': 'BLK',
    'Steals': 'STL',
    'Turnovers': 'TOV'
};


const TeamLogoCircle = ({ team, opponent, teamId }: { team: string, opponent: string, teamId?: number }) => {
    // If we have teamId, use it. Otherwise assume team is tricode (fallback).
    // The previous code passed opponent as team.
    const logoUrl = teamId
        ? `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${teamId}.svg`
        : `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${team}.svg`;

    return (
        <div className="w-5 h-5 rounded-full flex items-center justify-center border border-white/10 overflow-hidden bg-[#18181b] z-20 relative">
            <ImageWithFallback
                src={logoUrl}
                fallbackComponent={<span className="text-[7px] text-white font-bold">{team}</span>}
                alt={team}
                className="w-full h-full object-contain p-0.5"
            />
        </div>
    )
}

export const BarChart: React.FC<BarChartProps> = ({ player, activeTab, activeSportsbook }) => {
    const statKey = STAT_LABELS[activeTab] || 'PTS';

    const { chartData, lineValue } = useMemo(() => {
        if (!player || !player.game_log) return { chartData: [], lineValue: 0 };

        // 1. Get Line based on Active Sportsbook
        const prop = player.props?.[statKey]?.[activeSportsbook];
        const line = prop?.line || 0;

        // 2. Prepare Data (Last 30 games max)
        const log = player.game_log.slice(0, 30).reverse();

        const data = log.map(game => {
            let val = game[statKey];
            if (val === undefined) {
                if (statKey === 'PTS+REB+AST') val = (game.PTS || 0) + (game.REB || 0) + (game.AST || 0);
                else if (statKey === 'PTS+REB') val = (game.PTS || 0) + (game.REB || 0);
                else if (statKey === 'PTS+AST') val = (game.PTS || 0) + (game.AST || 0);
                else if (statKey === 'REB+AST') val = (game.REB || 0) + (game.AST || 0);
                else val = 0;
            }

            const parts = game.MATCHUP.split(' ');
            const opponent = parts[parts.length - 1]; // "DEN"
            const opponentId = TEAM_IDS[opponent];

            // Date formatting: "YYYY-MM-DD" -> "Nov 08"
            // const dateObj = new Date(game.GAME_DATE);
            const [year, monthStr, day] = game.GAME_DATE.split('-');
            const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            const month = months[parseInt(monthStr) - 1];

            const dateObj = new Date(parseInt(year), parseInt(monthStr) - 1, parseInt(day));
            const dateDayOfWeek = dateObj.toLocaleDateString('en-US', { weekday: 'short' });

            return {
                ...game,
                score: val,
                opponent,
                opponentId,
                dateMonth: month,
                dateDay: day,
                dateDayOfWeek
            };
        });

        return { chartData: data, lineValue: line };
    }, [player, statKey, activeSportsbook]);

    // Dynamic Scale
    const maxScore = Math.max(...chartData.map(d => d.score), lineValue * 1.2, 10);
    const linePercent = (lineValue / maxScore) * 100;

    if (!player) return null;

    return (
        <div className="bg-[#050505] w-full h-[380px] select-none relative overflow-hidden">

            {/* Chart Container */}
            <div className="w-full h-full flex flex-col relative pt-12 pb-14 pl-12 pr-6">

                {/* Y Axis Labels */}
                <div className="absolute left-3 top-12 bottom-14 flex flex-col justify-between text-[11px] text-[#71717a] font-medium py-1 z-0">
                    <span>{Math.round(maxScore)}</span>
                    <span>{Math.round(maxScore * 0.75)}</span>
                    <span>{Math.round(maxScore * 0.5)}</span>
                    <span>0</span>
                </div>

                {/* Horizontal Guide Lines */}
                <div className="absolute left-12 right-4 top-12 bottom-14 flex flex-col justify-between pointer-events-none z-0">
                    <div className="w-full h-px bg-[#27272a]/40"></div>
                    <div className="w-full h-px bg-[#27272a]/40"></div>
                    <div className="w-full h-px bg-[#27272a]/40"></div>
                    <div className="w-full h-px bg-[#27272a]/40"></div>
                </div>

                {/* The LINE Overlay & Handle */}
                <div
                    className="absolute left-12 right-6 z-20 flex items-center pointer-events-none transition-all duration-500 ease-out"
                    style={{ bottom: `calc(${linePercent}% + 36px)` }} // Adjusted for padding-bottom changes (14 + 20ish?)
                // Chart Area Height logic:
                // Height = 100% of container. Container is flex-col justifying-end.
                // The bottom 0 of chart is at bottom of container.
                // The visual chart bottom is constrained by pb-14 of wrapper.
                // Actually, the bars are "items-end h-full".
                // So bottom align is correct.
                // We just need to offset by the padding-bottom of the wrapper? No, wrapper padding pushes content in.
                // The "flex-1" container is INSIDE the wrapper.
                // So "bottom: X%" relates to that inner container height?
                // No, absolute is relative to wrapper.
                // Wrapper is `pt-12 pb-14`.
                // Available height = Total - 48 - 56.
                // Bottom offset = `calc((100% - 104px) * ${linePercent / 100} + 56px)` approx?
                // Let's refine based on "flex items-end".
                // The bars container is `h-full`.
                // So `bottom: linePercent%` of that inner container.
                // Inner container is `absolute inset x top 12 bottom 14` effectively?
                // No, it's flex-1.
                // Let's try `bottom: calc(${linePercent / 100} * (100% - 104px) + 56px)`
                // Or more simply: positioned absolute relative to Chart Wrapper.
                >
                    {/* Yellow Handle - NOW ON LEFT of line */}
                    {/* Positioned absolute at start of line? */}

                    {/* The Line */}
                    <div className="h-px bg-[#facc15] w-full shadow-[0_0_4px_rgba(250,204,21,0.5)] relative">
                        {/* Label on Left Side of Line */}
                        <div className="absolute -left-1 transform -translate-y-1/2 z-30 flex items-center">
                            <div className="bg-[#18181b] px-1.5 py-0.5 rounded text-[#facc15] text-[10px] font-bold flex items-center gap-1 border border-[#27272a] shadow-sm whitespace-nowrap">
                                <span className="w-1.5 h-1.5 bg-[#facc15] rounded-[1px]"></span>
                                {lineValue}
                            </div>
                        </div>
                    </div>
                </div>


                {/* Bars Container - Flex Row */}
                <div className="flex-1 flex items-end justify-between gap-1.5 z-10 relative px-1 h-full w-full">
                    {chartData.map((game, idx) => {
                        const heightPercent = Math.min((game.score / maxScore) * 100, 100);
                        const isOver = game.score >= lineValue;
                        const barColor = isOver ? 'bg-[#22c55e]' : 'bg-[#ef4444]';

                        return (
                            <div key={idx} className="flex flex-col items-center gap-2 group w-full h-full justify-end relative">
                                {/* Hover Tooltip */}
                                <div className="absolute -top-10 bg-gray-800 text-white text-[10px] px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-50 pointer-events-none border border-gray-700 shadow-xl">
                                    <div className="font-bold">{game.dateMonth} {game.dateDay}</div>
                                    <div className="text-gray-400">vs {game.opponent} • {game.score} {STAT_LABELS[activeTab]}</div>
                                </div>

                                {/* Bar */}
                                <div
                                    className={`w-full mx-px rounded-[2px] transition-all duration-200 hover:brightness-110 relative ${barColor}`}
                                    style={{ height: `${heightPercent}%` }}
                                >
                                    {/* Score Text inside bar */}
                                    <span className="absolute bottom-1 left-1/2 -translate-x-1/2 text-white font-bold text-[8px] drop-shadow-md">
                                        {game.score}
                                    </span>
                                </div>

                                {/* X-Axis Group (Logo + Date Stacked) */}
                                <div className="absolute top-full mt-2 flex flex-col items-center gap-1.5 w-full">
                                    {/* Team Logo */}
                                    <TeamLogoCircle team={game.opponent} opponent={game.opponent} teamId={game.opponentId} />

                                    {/* Stacked Date */}
                                    <div className="flex flex-col items-center text-[9px] font-bold leading-[0.9] text-[#71717a] opacity-80 group-hover:opacity-100 hover:text-white transition-all">
                                        <span className="uppercase text-[8px]">{game.dateDayOfWeek}</span>
                                        <span className="text-white">{game.dateDay}</span>
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* Footer / Watermark area */}
            <div className="absolute bottom-2 left-6 flex items-center gap-2 pointer-events-none">
                <span className="text-[10px] text-[#52525b] font-bold tracking-normal flex items-center gap-1 opacity-50">
                    <div className="w-1.5 h-2 bg-[#52525b]"></div>
                    PropsMadness
                </span>
            </div>

        </div>
    );
};