import React, { useMemo } from 'react';
import { Player } from '../types';
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

    // Dynamic Scale: Ensure maxScore is divisible by 4 for clean ticks
    const maxValue = Math.max(...chartData.map(d => d.score), lineValue);
    const maxScore = Math.max(Math.ceil((maxValue * 1.1) / 4) * 4, 10);
    const linePercent = (lineValue / maxScore) * 100;

    if (!player) return null;

    return (
        <div className="bg-[#09090b] w-full h-[400px] select-none relative rounded-xl border border-[#27272a]/50 shadow-2xl flex flex-col">

            {/* Header/Title overlay if needed, or clean */}

            {/* Y Axis Labels - Absolute Left */}
            <div className="absolute left-3 top-10 bottom-24 flex flex-col justify-between text-[11px] text-[#71717a] font-bold py-1 z-10 h-auto leading-none">
                {/* Use flex centering for labels to ensure they align with grid lines perfectly */}
                <span className="translate-y-[-50%]">{Math.round(maxScore)}</span>
                <span className="translate-y-[-50%]">{Math.round(maxScore * 0.75)}</span>
                <span className="translate-y-[-50%]">{Math.round(maxScore * 0.5)}</span>
                <span className="translate-y-[-50%]">{Math.round(maxScore * 0.25)}</span>
                <span className="translate-y-[50%]">0</span>
            </div>

            {/* CHART AREA WRAPPER */}
            {/* Margins set the chart box. Top: 40px, Bottom: 96px (bottom-24), Left: 48px, Right: 24px */}
            <div className="absolute left-10 right-6 top-10 bottom-24 z-0">

                {/* 1. Grid Lines (Relative to Chart Area) */}
                <div className="w-full h-full flex flex-col justify-between pointer-events-none absolute inset-0 z-0">
                    <div className="w-full h-px bg-[#27272a]/40"></div>
                    <div className="w-full h-px bg-[#27272a]/40"></div>
                    <div className="w-full h-px bg-[#27272a]/40"></div>
                    <div className="w-full h-px bg-[#27272a]/40"></div>
                    <div className="w-full h-px bg-[#27272a]/40"></div>
                </div>

                {/* 2. The LINE Overlay (Relative to Chart Area) */}
                {/* FIX: Added 'h-0' and removed child translations. The wrapper is now a zero-height line at exact position. */}
                <div
                    className="absolute left-0 right-[-24px] z-20 flex items-center h-0 pointer-events-none transition-all duration-500 ease-out"
                    style={{ bottom: `${linePercent}%` }}
                >
                    {/* Yellow Grip Handle - WITH VALUE */}
                    <div className="h-6 px-1.5 bg-[#facc15] rounded-l-sm rounded-r-md flex items-center justify-center shadow-[0_0_15px_rgba(250,204,21,0.3)] -ml-[1px] z-30 min-w-[36px]">
                        <span className="text-black font-extrabold text-[11px] leading-none">{lineValue}</span>
                    </div>

                    {/* The Line */}
                    <div className="h-[2px] bg-[#facc15] w-full shadow-[0_0_8px_rgba(250,204,21,0.6)]"></div>
                </div>

                {/* 3. Bars Container */}
                <div className="w-full h-full flex items-end justify-between gap-2 z-10 relative pl-2">
                    {chartData.map((game, idx) => {
                        const heightPercent = Math.min((game.score / maxScore) * 100, 100);
                        const isOver = game.score >= lineValue;
                        const barColor = isOver ? 'bg-[#22c55e]' : 'bg-[#ef4444]';

                        return (
                            <div key={idx} className="flex flex-col items-center group w-full h-full justify-end relative">
                                {/* Hover Tooltip */}
                                <div className="absolute -top-12 bg-gray-900 border border-gray-700 text-white text-[10px] px-2 py-1.5 rounded-md opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-50 pointer-events-none shadow-xl">
                                    <div className="font-bold mb-0.5">{game.dateMonth} {game.dateDay} vs {game.opponent}</div>
                                    <div className="text-gray-300">{game.score} {STAT_LABELS[activeTab]}</div>
                                </div>

                                {/* Bar */}
                                <div
                                    className={`w-full rounded-t-[4px] transition-all duration-300 hover:brightness-110 relative ${barColor}`}
                                    style={{ height: `${heightPercent}%` }}
                                >
                                    {/* Score Text - BIG & BOLD at bottom */}
                                    <span className="absolute bottom-1.5 left-1/2 -translate-x-1/2 text-white font-black text-sm drop-shadow-lg leading-none tracking-tighter">
                                        {game.score}
                                    </span>
                                </div>

                                {/* X-Axis Group (Logo + Date) - ABSOLUTE below chart area */}
                                <div className="absolute top-full mt-3 flex flex-col items-center gap-1 w-full">
                                    {/* Team Logo */}
                                    <div className="w-6 h-6 hover:scale-110 transition-transform">
                                        <TeamLogoCircle team={game.opponent} opponent={game.opponent} teamId={game.opponentId} />
                                    </div>

                                    {/* Stacked Date */}
                                    <div className="flex flex-col items-center justify-center mt-1">
                                        <span className="text-[9px] font-bold text-[#71717a] uppercase leading-none mb-[2px]">{game.dateMonth}</span>
                                        <span className="text-[10px] font-extrabold text-[#e4e4e7] leading-none">{game.dateDay}</span>
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* Line Label centered bottom */}
            <div className="absolute bottom-1 left-1/2 -translate-x-1/2 z-30">
                <div className="bg-[#18181b]/90 backdrop-blur-sm px-2.5 py-1 rounded-md text-[#facc15] text-[10px] font-black tracking-wide flex items-center gap-2 border border-[#facc15]/20 shadow-lg">
                    <div className="w-2 h-2 bg-[#facc15] rounded-[2px] shadow-[0_0_6px_rgba(250,204,21,0.6)]"></div>
                    LINE
                </div>
            </div>

            {/* Footer / Watermark area */}
            <div className="absolute bottom-3 left-3 flex items-center gap-2 pointer-events-none opacity-40">
                <span className="text-[10px] text-[#52525b] font-medium">PropsMadness</span>
            </div>

        </div>
    );
};