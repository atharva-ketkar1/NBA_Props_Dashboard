import React, { useMemo, useState, useEffect } from 'react';
import { Player, Game } from '../types';
import { TEAM_IDS } from '../constants';
import { HoverTooltip, HoveredGameData } from './HoverTooltip';
import { colors } from '../utils/propsmadness_colors';

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

export const BarChart: React.FC<BarChartProps> = ({ player, activeTab, activeSportsbook }) => {
    const statKey = STAT_LABELS[activeTab] || 'PTS';

    // Hover State
    const [hoverData, setHoverData] = useState<HoveredGameData | null>(null);

    // Schedule State for Upcoming Game 
    const [scheduleData, setScheduleData] = useState<Game[]>([]);

    useEffect(() => {
        const apiUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';
        fetch(`${apiUrl}/data/current/nba_dashboard_games.json`)
            .then(res => res.json())
            .then(data => setScheduleData(data))
            .catch(err => console.error("Error loading schedule:", err));
    }, []);

    const { chartData, lineValue } = useMemo(() => {
        if (!player || !player.game_log) return { chartData: [], lineValue: 0 };

        // 1. Get Line based on Active Sportsbook
        const prop = player.props?.[statKey]?.[activeSportsbook];
        const line = prop?.line || 0;

        // 2. Prepare Data (Last 30 games max)
        const log = player.game_log.slice(0, 30).reverse();

        const data: any[] = log.map(game => {
            let val = game[statKey];
            if (val === undefined) {
                if (statKey === 'PTS+REB+AST') val = (game.PTS || 0) + (game.REB || 0) + (game.AST || 0);
                else if (statKey === 'PTS+REB') val = (game.PTS || 0) + (game.REB || 0);
                else if (statKey === 'PTS+AST') val = (game.PTS || 0) + (game.AST || 0);
                else if (statKey === 'REB+AST') val = (game.REB || 0) + (game.AST || 0);
                else val = 0;
            }

            const parts = game.MATCHUP.split(' ');
            const opponent = parts[parts.length - 1]; // e.g., "DEN"
            const opponentId = TEAM_IDS[opponent];

            // Resolve Logo URL for SVG
            const logoUrl = opponentId
                ? `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${opponentId}.svg`
                : `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${opponent}.svg`;

            // Date formatting: "YYYY-MM-DD" -> "Nov", "08"
            const [year, monthStr, day] = game.GAME_DATE.split('-');
            const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            const month = months[parseInt(monthStr) - 1];

            return {
                ...game,
                score: val,
                opponent,
                logoUrl,
                dateMonth: month,
                dateDay: day
            };
        });

        // 3. Append upcoming game dynamically
        let upcomingOpponent = 'TBD';
        const today = new Date();
        const fallbackMonths = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        let upcomingMonth = fallbackMonths[today.getMonth()];
        let upcomingDay = String(today.getDate()).padStart(2, '0');

        if (player.team && scheduleData.length > 0) {
            const playerGame = scheduleData.find(g => g.home_team_tricode === player.team || g.away_team_tricode === player.team);
            if (playerGame) {
                upcomingOpponent = playerGame.home_team_tricode === player.team ? playerGame.away_team_tricode : playerGame.home_team_tricode;
                if (playerGame.game_date) {
                    const [y, mStr, dStr] = playerGame.game_date.split('-');
                    if (mStr && dStr) {
                        upcomingMonth = fallbackMonths[parseInt(mStr) - 1];
                        upcomingDay = dStr;
                    }
                }
            }
        }

        const upcomingOpponentId = TEAM_IDS[upcomingOpponent];
        const upcomingLogoUrl = upcomingOpponentId
            ? `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${upcomingOpponentId}.svg`
            : upcomingOpponent === 'TBD'
                ? `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${TEAM_IDS['HOU'] || 'HOU'}.svg`
                : `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${upcomingOpponent}.svg`;

        data.push({
            score: null, // Special flag for upcoming game
            opponent: upcomingOpponent === 'TBD' ? 'HOU' : upcomingOpponent,
            logoUrl: upcomingLogoUrl,
            dateMonth: upcomingMonth,
            dateDay: upcomingDay,
            isUpcoming: true
        });

        return { chartData: data, lineValue: line };
    }, [player, statKey, activeSportsbook, scheduleData]);

    if (!player) return null;

    // --- Responsive SVG Layout Constants ---
    const VIEWBOX_WIDTH = 1000; // Internal coordinate system width
    const VIEWBOX_HEIGHT = 400; // Internal coordinate system height
    const X_START = 80;         // Left margin to leave room for Y-axis labels
    const X_END = VIEWBOX_WIDTH - 20; // Right margin
    const AVAILABLE_WIDTH = X_END - X_START;

    // Dynamic Spacing: Distribute available width evenly based on number of games
    const gameCount = Math.max(chartData.length, 1);
    const spacing = AVAILABLE_WIDTH / gameCount;

    // Dynamic Bar Width: Shrinks when there are many games, caps at 32px when few games
    const barWidth = Math.min(spacing * 0.85, 32);

    // Dynamic Scale: Calculate max value to ensure bars don't clip the top
    const maxScore = Math.max(...chartData.map(d => d.score), lineValue + 5, 10);

    // Helpers to calculate exact Y coordinates and Heights inside the SVG
    const getY = (val: number) => {
        const availableHeight = 250; // Keeps top margin and leaves room for logos at bottom
        return VIEWBOX_HEIGHT - 120 - ((val / maxScore) * availableHeight);
    };

    const getBarHeight = (val: number) => {
        const availableHeight = 250;
        return (val / maxScore) * availableHeight;
    };

    const propLineY = getY(lineValue);

    return (
        <div className="bg-bgElevation0 w-full h-full min-h-[400px] select-none relative rounded-xl shadow-2xl overflow-hidden flex flex-col">

            {/* Responsive SVG Container */}
            <svg
                viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
                preserveAspectRatio="xMidYMid meet"
                className="w-full h-full"
            >
                {/* 1. Y-Axis Grid Lines & Labels */}
                <g className="text-fgSubtle font-bold" fill="currentColor" textAnchor="end" fontSize="12">
                    <text x="50" y={getY(0)} dominantBaseline="middle">0</text>
                    <text x="50" y={getY(maxScore * 0.5)} dominantBaseline="middle">{Math.round(maxScore * 0.5)}</text>
                    <text x="50" y={getY(maxScore)} dominantBaseline="middle">{Math.round(maxScore)}</text>

                    <line x1="60" x2="100%" y1={getY(0)} y2={getY(0)} stroke={colors.borderMedium} strokeOpacity="0.4" />
                    <line x1="60" x2="100%" y1={getY(maxScore * 0.5)} y2={getY(maxScore * 0.5)} stroke={colors.borderMedium} strokeOpacity="0.4" />
                    <line x1="60" x2="100%" y1={getY(maxScore)} y2={getY(maxScore)} stroke={colors.borderMedium} strokeOpacity="0.4" />
                </g>

                {/* 2. Map through GameLogs for Bars, Text, and Logos */}
                {chartData.map((game, index) => {
                    // Center the bar within its allocated spacing column
                    const columnCenter = X_START + (index * spacing) + (spacing / 2);
                    const xPos = columnCenter - (barWidth / 2);
                    const yPos = getY(game.score);
                    const barHeight = getBarHeight(game.score);
                    const isOver = game.score >= lineValue;

                    // Dynamically scale inner elements if bars get very narrow
                    const logoSize = Math.min(barWidth * 1.2, 28);
                    const fontSize = Math.min(barWidth * 0.6, 14);

                    return (
                        <g
                            key={index}
                            className="group cursor-pointer"
                            onMouseEnter={(e) => {
                                setHoverData({
                                    game,
                                    x: e.clientX,
                                    y: e.clientY,
                                    lineValue,
                                    statKey,
                                    activeSportsbook
                                });
                            }}
                            onMouseMove={(e) => {
                                if (hoverData) {
                                    setHoverData({ ...hoverData, x: e.clientX, y: e.clientY });
                                }
                            }}
                            onMouseLeave={() => setHoverData(null)}
                        >
                            {/* The Bar */}
                            {game.isUpcoming ? (
                                <rect
                                    x={xPos}
                                    y={getY(lineValue + 2)} // arbitrary height for the placeholder
                                    width={barWidth}
                                    height={getBarHeight(lineValue + 2)}
                                    rx="4"
                                    ry="4"
                                    fill="transparent"
                                    stroke={colors.borderMedium}
                                    strokeWidth="1.5"
                                    strokeDasharray="4 4"
                                    className="opacity-70"
                                >
                                    <title>Upcoming Game</title>
                                </rect>
                            ) : (
                                <rect
                                    x={xPos}
                                    y={yPos}
                                    width={barWidth}
                                    height={barHeight}
                                    rx="4"
                                    ry="4"
                                    fill={isOver ? colors.graphBarOver : colors.graphBarUnder}
                                    className="transition-all duration-300 group-hover:brightness-110 group-hover:opacity-80"
                                >
                                    <title>{`${game.dateMonth} ${game.dateDay} vs ${game.opponent} - ${game.score} ${statKey}`}</title>
                                </rect>
                            )}

                            {/* Stat Value Text Inside Bar */}
                            <text
                                x={columnCenter}
                                y={game.isUpcoming ? VIEWBOX_HEIGHT - 128 : VIEWBOX_HEIGHT - 128}
                                textAnchor="middle"
                                fill={game.isUpcoming ? colors.fgSubtle : colors.fixedWhite}
                                fontWeight="900"
                                fontSize={fontSize}
                                className="pointer-events-none drop-shadow-md"
                            >
                                {game.isUpcoming ? '?' : game.score}
                            </text>

                            {/* Team Logo */}
                            <image
                                x={columnCenter - (logoSize / 2)}
                                y={VIEWBOX_HEIGHT - 114}
                                width={logoSize}
                                height={logoSize}
                                href={game.logoUrl}
                            />

                            {/* Stacked Date Label with connecting dots visualization */}
                            {(() => {
                                // Logic to determine if we should show connecting dots
                                const nextGame = chartData[index + 1];
                                const isConnected = nextGame && nextGame.dateMonth === game.dateMonth && !game.isUpcoming && parseInt(nextGame.dateDay) - parseInt(game.dateDay) === 1;

                                const prevGame = index > 0 ? chartData[index - 1] : null;
                                const isConnectedToPrev = prevGame && prevGame.dateMonth === game.dateMonth && !prevGame.isUpcoming && parseInt(game.dateDay) - parseInt(prevGame.dateDay) === 1;

                                return (
                                    <g>
                                        <text
                                            x={columnCenter}
                                            y={VIEWBOX_HEIGHT - 75}
                                            textAnchor="middle"
                                        >
                                            <tspan x={columnCenter} dy="0" fill={colors.fgSubtle} fontSize="10" fontWeight="normal">
                                                {game.dateMonth}
                                            </tspan>
                                            <tspan x={columnCenter} dy="1.2em" fill={colors.fgDefault} fontSize="11" fontWeight="700">
                                                {game.dateDay}
                                            </tspan>
                                        </text>

                                        {/* Render connecting dots exactly centered between columns */}
                                        {isConnected && (
                                            <text
                                                x={columnCenter + spacing / 2}
                                                y={VIEWBOX_HEIGHT - 63}
                                                textAnchor="middle"
                                                fill={colors.fgSubtle}
                                                fontSize="11"
                                                fontWeight="700"
                                            >
                                                ..
                                            </text>
                                        )}
                                    </g>
                                );
                            })()}
                        </g>
                    );
                })}

                {/* 3. The Prop Line Threshold Overlay */}
                <g>
                    <line
                        x1="60"
                        x2="100%"
                        y1={propLineY}
                        y2={propLineY}
                        stroke={colors.yellow400}
                        strokeWidth="2"
                        strokeDasharray="4 4"
                        className="drop-shadow-sm"
                    />

                    {/* Yellow Grip Handle */}
                    <rect
                        x="60"
                        y={propLineY - 12}
                        width="36"
                        height="24"
                        rx="4"
                        fill={colors.yellow400}
                    />
                    <text
                        x="78"
                        y={propLineY + 2}
                        dominantBaseline="middle"
                        textAnchor="middle"
                        fontSize="12"
                        fontWeight="900"
                        fill={colors.black}
                    >
                        {lineValue}
                    </text>
                </g>
            </svg>

            {/* LINE Legend element centered at bottom */}
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1.5 z-20">
                <div className="w-2.5 h-2.5 bg-yellow400 rounded-[1px]" />
                <span className="text-fgSubtle font-bold text-[9px] tracking-widest uppercase">LINE</span>
            </div>

            {/* Footer / Watermark */}
            <div className="absolute bottom-3 left-3 pointer-events-none opacity-40 z-20">
                <span className="text-neutral600 font-medium text-[10px]">PropsMadness</span>
            </div>

            {/* Hover Tooltip Overlay */}
            <HoverTooltip data={hoverData} />
        </div>
    );
};