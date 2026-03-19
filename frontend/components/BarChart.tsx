import React, { useMemo, useState, useEffect } from 'react';
import { Player, Game } from '../types';
import { TEAM_IDS } from '../constants';
import { HoverTooltip, HoveredGameData } from './HoverTooltip';
import { colors } from '../utils/propsmadness_colors';
import { motion, AnimatePresence } from 'framer-motion';
import { ASSETS_BASE } from '../utils/config';

const USE_DB = import.meta.env.VITE_USE_DB === 'true';

interface BarChartProps {
    player?: Player;
    activeTab: string;
    activeSportsbook: 'dk' | 'fd' | 'mgm' | 'cz';
    customLine?: number | null;
    onCustomLineChange?: (line: number | null) => void;
    activeFilterOverlay?: string | null;
    isFiltersOpen?: boolean;
    historicalGameCount?: number;
    activeSeason?: string;
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

export const BarChart: React.FC<BarChartProps> = ({ player, activeTab, activeSportsbook, customLine, onCustomLineChange, activeFilterOverlay, isFiltersOpen, historicalGameCount, activeSeason }) => {
    const statKey = STAT_LABELS[activeTab] || 'PTS';

    // Hover State
    const [hoverData, setHoverData] = useState<HoveredGameData | null>(null);

    // Mobile specific chart adaptations
    const [isMobile, setIsMobile] = useState(typeof window !== 'undefined' && window.innerWidth < 1024);
    useEffect(() => {
        const handleResize = () => setIsMobile(window.innerWidth < 1024);
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    // Dragging Line State
    const [isDragging, setIsDragging] = useState(false);
    const [dragY, setDragY] = useState<number | null>(null);
    const [currentValue, setCurrentValue] = useState<number | null>(null);
    const svgRef = React.useRef<SVGSVGElement>(null);

    // Schedule State for Upcoming Game 
    const [scheduleData, setScheduleData] = useState<Game[]>([]);

    useEffect(() => {
        if (USE_DB) {
            const today = new Date();
            if (today.getHours() < 9) today.setDate(today.getDate() - 1);
            const yyyy = today.getFullYear();
            const mm = String(today.getMonth() + 1).padStart(2, '0');
            const dd = String(today.getDate()).padStart(2, '0');
            const todayStr = `${yyyy}-${mm}-${dd}`;

            const tomorrow = new Date(today);
            tomorrow.setDate(today.getDate() + 1);
            const tyyyy = tomorrow.getFullYear();
            const tmm = String(tomorrow.getMonth() + 1).padStart(2, '0');
            const tdd = String(tomorrow.getDate()).padStart(2, '0');
            const tomorrowStr = `${tyyyy}-${tmm}-${tdd}`;

            import('../utils/supabase').then(({ supabase }) => {
                supabase
                    .from('games')
                    .select('*')
                    .in('game_date', [todayStr, tomorrowStr])
                    .then(({ data, error }) => {
                        if (error) { console.error('[supabase] games error:', error); return; }
                        setScheduleData(data ?? []);
                    });
            });
        } else {
            const apiUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';
            fetch(`${apiUrl}/data/current/nba_dashboard_games.json`)
                .then(res => res.json())
                .then(data => setScheduleData(data))
                .catch(err => console.error("Error loading schedule:", err));
        }
    }, []);

    const { chartData, lineValue, graphAvgSecondary, seasonAvgSecondary, isRankOverlay } = useMemo(() => {
        if (!player || !player.game_log) return { chartData: [], lineValue: 0, graphAvgSecondary: 0, seasonAvgSecondary: 0, isRankOverlay: false };

        // 1. Get Line based on Active Sportsbook
        const prop = player.props?.[statKey]?.[activeSportsbook];
        const line = prop?.line || 0;

        // 2. Prepare Data (Use historicalGameCount passed from App)
        const numGames = (isMobile && !isFiltersOpen) ? 12 : (historicalGameCount || (isFiltersOpen ? 19 : 29));
        const log = player.game_log.slice(0, numGames).reverse();

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
                ? `${ASSETS_BASE}/assets/team_logos/${opponentId}.svg`
                : `${ASSETS_BASE}/assets/team_logos/${opponent}.svg`;

            // Date formatting: "YYYY-MM-DD" -> "Nov", "08"
            const [year, monthStr, day] = game.GAME_DATE.split('-');
            const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            const month = months[parseInt(monthStr) - 1];

            let secondaryVal = null;
            if (activeFilterOverlay === 'Minutes') secondaryVal = Number(game.MIN || 0);
            else if (activeFilterOverlay === 'USG%') secondaryVal = Number(game.USG_PCT || 0) * 100;
            else if (activeFilterOverlay === 'FGA') secondaryVal = Number(game.FGA || 0);
            else if (activeFilterOverlay === 'Def vs DPT') secondaryVal = game.opp_ranks?.dpt;
            else if (activeFilterOverlay === 'Def vs DSZ') secondaryVal = game.opp_ranks?.dsz;
            else if (activeFilterOverlay === 'Def vs DSZ2') secondaryVal = game.opp_ranks?.dsz2;
            else if (activeFilterOverlay === 'Opp Paint Pts Allowed') secondaryVal = game.opp_ranks?.paint_allowed;
            else if (activeFilterOverlay === 'Def vs Pull Up') secondaryVal = game.opp_ranks?.pull_up;

            return {
                ...game,
                score: val,
                secondaryValue: secondaryVal,
                opponent,
                logoUrl,
                dateMonth: month,
                dateDay: day
            };
        });

        // 2b. Calculate global averages based on exact charted values
        let seasonAvgSecondary = 0;
        let graphAvgSecondary = 0;
        let isRankOverlay = activeFilterOverlay?.startsWith('Def vs') || activeFilterOverlay?.startsWith('Opp');

        if (activeFilterOverlay) {
            const validSecondary = data.filter(g => g.secondaryValue !== null && g.secondaryValue !== undefined);
            const sumSecondary = validSecondary.reduce((acc, g) => acc + Number(g.secondaryValue), 0);
            graphAvgSecondary = validSecondary.length > 0 ? sumSecondary / validSecondary.length : 0;

            if (!isRankOverlay) {
                if (activeFilterOverlay === 'Minutes') seasonAvgSecondary = Number(player.stats?.MIN || 0);
                if (activeFilterOverlay === 'USG%') seasonAvgSecondary = Number(player.stats?.USG_PCT || 0) * 100;
                if (activeFilterOverlay === 'FGA') seasonAvgSecondary = Number(player.stats?.FGA || 0);
            } else {
                seasonAvgSecondary = graphAvgSecondary; // For ranks, the "season average" is just the graph average visually
            }
        }

        // 3. Append upcoming game dynamically
        if (activeSeason !== '24/25') {
            let upcomingOpponent = 'TBD';
            const today = new Date();
            if (today.getHours() < 9) today.setDate(today.getDate() - 1);
            const fallbackMonths = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            let upcomingMonth = fallbackMonths[today.getMonth()];
            let upcomingDay = String(today.getDate()).padStart(2, '0');

            let upcomingSecondaryRank = null;
            if (isRankOverlay) {
                // Find dpt, dsz, etc to grab rank for the upcoming opponent (which is stored currently in the player object directly)
                if (activeFilterOverlay === 'Def vs DPT') {
                    if (player?.play_type_analysis) {
                        const sortedPlays = [...player.play_type_analysis].sort((a: any, b: any) => parseInt(b.percent) - parseInt(a.percent));
                        upcomingSecondaryRank = sortedPlays[0]?.rank || null;
                    }
                } else if (activeFilterOverlay === 'Def vs DSZ' || activeFilterOverlay === 'Def vs DSZ2') {
                    if (player?.shooting_zones) {
                        const zones = Object.entries(player.shooting_zones).map(([zone, data]: any) => ({ zone, pct: parseInt(data.percentage) })).sort((a, b) => b.pct - a.pct);
                        const targetZone = activeFilterOverlay === 'Def vs DSZ' ? zones[0]?.zone : zones[1]?.zone;
                        upcomingSecondaryRank = player.opp_def_zones?.[targetZone]?.rank || null;
                    }
                } else if (activeFilterOverlay === 'Opp Paint Pts Allowed') {
                    upcomingSecondaryRank = player?.opp_def_zones?.paint?.rank || null;
                } else if (activeFilterOverlay === 'Def vs Pull Up') {
                    upcomingSecondaryRank = player?.shot_type_analysis?.opp_def?.pull_up?.rank || null;
                }
            }

            if (player.team && scheduleData.length > 0) {
                const sortedSchedule = [...scheduleData].sort((a, b) =>
                    new Date(a.game_time_utc).getTime() - new Date(b.game_time_utc).getTime()
                );
                const playerGame = sortedSchedule.find(g =>
                    (g.home_team_tricode === player.team || g.away_team_tricode === player.team)
                    && (!prop?.game_date || g.game_date === prop.game_date)
                ) || sortedSchedule.find(g => g.home_team_tricode === player.team || g.away_team_tricode === player.team);
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
                ? `${ASSETS_BASE}/assets/team_logos/${upcomingOpponentId}.svg`
                : upcomingOpponent === 'TBD'
                    ? `${ASSETS_BASE}/assets/team_logos/${TEAM_IDS['HOU'] || 'HOU'}.svg`
                    : `${ASSETS_BASE}/assets/team_logos/${upcomingOpponent}.svg`;

            data.push({
                score: null, // Special flag for upcoming game
                secondaryValue: isRankOverlay && upcomingSecondaryRank ? upcomingSecondaryRank : (activeFilterOverlay ? graphAvgSecondary : null), // Use upcoming rank or connect base avg
                opponent: upcomingOpponent === 'TBD' ? 'HOU' : upcomingOpponent,
                logoUrl: upcomingLogoUrl,
                dateMonth: upcomingMonth,
                dateDay: upcomingDay,
                isUpcoming: true
            });
        }

        return { chartData: data, lineValue: line, graphAvgSecondary, seasonAvgSecondary, isRankOverlay: !!isRankOverlay };
    }, [player, statKey, activeSportsbook, scheduleData, activeFilterOverlay, isFiltersOpen, historicalGameCount, activeSeason, isMobile]);

    if (!player) return null;

    // --- Responsive SVG Layout Constants ---
    const VIEWBOX_WIDTH = isMobile ? 650 : (isFiltersOpen ? 700 : 1000); // Internal coordinate system width dynamcially adjusts to prevent shrinking
    const VIEWBOX_HEIGHT = 400; // Internal coordinate system height
    const X_START = 80;         // Left margin to leave room for Y-axis labels
    const X_END = VIEWBOX_WIDTH - 20; // Right margin
    const AVAILABLE_WIDTH = X_END - X_START;

    const currentVisibleCount = (isMobile && !isFiltersOpen) ? 14 : (historicalGameCount || (isFiltersOpen ? 19 : 29));
    // 1. Dynamic Spacing: For <= 19 games, map length based on 20 slots. For > 19, use full count.
    const layoutSlots = currentVisibleCount <= 19 ? (isMobile ? 13 : 20) : chartData.length;
    const spacing = AVAILABLE_WIDTH / layoutSlots;

    // Calculate total layout width and padding for centering
    const totalContentWidth = chartData.length * spacing;
    const paddingLeft = currentVisibleCount <= 19 ? (AVAILABLE_WIDTH - totalContentWidth) / 2 : 0;

    const shouldCondense = isFiltersOpen && currentVisibleCount > 19;
    const condensedLabels = new Set<number>();
    if (shouldCondense) {
        const numLabels = Math.min(8, chartData.length);
        for (let i = 0; i < numLabels; i++) {
            condensedLabels.add(Math.floor(i * (chartData.length - 1) / (numLabels - 1)));
        }
    }

    // Dynamic Bar Width
    const barWidth = Math.min(spacing * (isMobile ? 1.0 : 0.85), isMobile ? 40 : 32);

    // Dynamic Scale: Calculate max value to ensure bars don't clip the top
    const maxScore = Math.max(...chartData.map(d => Number(d.score || 0)), lineValue + 5, 10);
    const maxSecondary = isRankOverlay ? 30 : (activeFilterOverlay ? Math.max(...chartData.map(d => Number(d.secondaryValue || 0)), 15) * 1.2 : 10);

    // Helpers to calculate exact Y coordinates and Heights inside the SVG
    const getY = (val: number) => {
        const availableHeight = 250; // Keeps top margin and leaves room for logos at bottom
        return VIEWBOX_HEIGHT - 120 - ((val / maxScore) * availableHeight);
    };

    const getSecondaryY = (val: number | null | undefined) => {
        if (val === null || val === undefined) return 0; // Or some fallback
        const availableHeight = 250;
        if (isRankOverlay) {
            // For ranks, #1 is hardest (red) and should be high up, #30 is easiest and should be lower?
            // Invert it so #1 is at bottom, #30 is at top
            return VIEWBOX_HEIGHT - 120 - (((31 - val) / 30) * availableHeight);
        }
        return VIEWBOX_HEIGHT - 120 - ((val / maxSecondary) * availableHeight);
    };

    const getBarHeight = (val: number) => {
        const availableHeight = 250;
        return (val / maxScore) * availableHeight;
    };

    const propLineY = getY(customLine !== undefined && customLine !== null ? customLine : lineValue);

    // Calculate live line value based on drag position
    useEffect(() => {
        if (isDragging && dragY !== null) {
            const availableHeight = 250;
            // Reverse math from getY
            // y = VIEWBOX_HEIGHT - 120 - ((val / maxScore) * availableHeight)
            // y - VIEWBOX_HEIGHT + 120 = - ((val / maxScore) * availableHeight)
            // (VIEWBOX_HEIGHT - 120 - y) = (val / maxScore) * availableHeight
            // val = ((VIEWBOX_HEIGHT - 120 - y) / availableHeight) * maxScore
            const val = ((VIEWBOX_HEIGHT - 120 - dragY) / availableHeight) * maxScore;

            // Snap to nearest 0.5 step to make it feel like a prop line
            const snappedVal = Math.round(val * 2) / 2;
            const finalVal = Math.max(0, Math.min(snappedVal, maxScore));
            setCurrentValue(finalVal);
            if (onCustomLineChange) onCustomLineChange(finalVal);
        } else if (!isDragging && customLine === null) {
            setCurrentValue(lineValue);
        } else if (!isDragging && customLine !== null && customLine !== undefined) {
            setCurrentValue(customLine);
        }
    }, [dragY, isDragging, maxScore, lineValue, onCustomLineChange, customLine]);

    const handleMouseMove = (e: React.MouseEvent<SVGSVGElement> | React.TouchEvent<SVGSVGElement>) => {
        if (!isDragging || !svgRef.current) return;
        setHoverData(null); // Force hide tooltips while dragging

        let clientY;
        if ('touches' in e) {
            clientY = e.touches[0].clientY;
        } else {
            clientY = (e as React.MouseEvent).clientY;
        }

        const point = svgRef.current.createSVGPoint();
        point.y = clientY;
        const ctm = svgRef.current.getScreenCTM();
        if (ctm) {
            const svgPoint = point.matrixTransform(ctm.inverse());
            setDragY(Math.max(10, Math.min(VIEWBOX_HEIGHT - 120, svgPoint.y)));
        }
    };

    const handleMouseUp = () => {
        setIsDragging(false);
    };

    const activeLineThreshold = customLine !== undefined && customLine !== null ? customLine : lineValue;

    return (
        <div
            className={`bg-bgElevation0 w-full h-full min-h-[380px] sm:min-h-[450px] aspect-[4/3] lg:aspect-auto select-none relative rounded-xl shadow-2xl overflow-hidden flex flex-col ${isDragging ? 'cursor-grabbing' : ''}`}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            onTouchEnd={handleMouseUp}
        >

            {/* Responsive SVG Container */}
            <svg
                ref={svgRef}
                viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
                preserveAspectRatio="xMidYMid meet"
                className="w-full h-full"
                onMouseMove={handleMouseMove}
                onTouchMove={handleMouseMove}
            >
                {/* 1. Y-Axis Grid Lines & Labels */}
                <g className="text-fgSubtle font-bold" fill="currentColor" textAnchor="end" fontSize="12">
                    <text x="50" y={getY(0)} dominantBaseline="middle">0</text>
                    <text x="50" y={getY(maxScore / 3)} dominantBaseline="middle">{Math.round(maxScore / 3)}</text>
                    <text x="50" y={getY((maxScore * 2) / 3)} dominantBaseline="middle">{Math.round((maxScore * 2) / 3)}</text>
                    <text x="50" y={getY(maxScore)} dominantBaseline="middle">{Math.round(maxScore)}</text>

                    <line x1="60" x2="100%" y1={getY(0)} y2={getY(0)} stroke={colors.borderMedium} strokeOpacity="0.4" />
                    <line x1="60" x2="100%" y1={getY(maxScore / 3)} y2={getY(maxScore / 3)} stroke={colors.borderMedium} strokeOpacity="0.4" />
                    <line x1="60" x2="100%" y1={getY((maxScore * 2) / 3)} y2={getY((maxScore * 2) / 3)} stroke={colors.borderMedium} strokeOpacity="0.4" />
                    <line x1="60" x2={activeFilterOverlay ? "100%" : X_END} y1={getY(maxScore)} y2={getY(maxScore)} stroke={colors.borderMedium} strokeOpacity="0.4" />
                </g>

                {/* Secondary Y-Axis Grid Lines & Labels (Right Side) */}
                {activeFilterOverlay && (
                    <g className="text-blue500 font-bold" fill="currentColor" textAnchor="start" fontSize="12">
                        {isRankOverlay ? (
                            <>
                                <text x={X_END + 4} y={getSecondaryY(30)} dominantBaseline="middle">#30</text>
                                <text x={X_END + 4} y={getSecondaryY(20)} dominantBaseline="middle">#20</text>
                                <text x={X_END + 4} y={getSecondaryY(10)} dominantBaseline="middle">#10</text>
                                <text x={X_END + 4} y={getSecondaryY(1)} dominantBaseline="middle">#1</text>
                            </>
                        ) : (
                            <>
                                <text x={X_END + 4} y={getSecondaryY(0)} dominantBaseline="middle">0</text>
                                <text x={X_END + 4} y={getSecondaryY(maxSecondary / 3)} dominantBaseline="middle">{Math.round(maxSecondary / 3)}{activeFilterOverlay === 'USG%' ? '%' : ''}</text>
                                <text x={X_END + 4} y={getSecondaryY((maxSecondary * 2) / 3)} dominantBaseline="middle">{Math.round((maxSecondary * 2) / 3)}{activeFilterOverlay === 'USG%' ? '%' : ''}</text>
                                <text x={X_END + 4} y={getSecondaryY(maxSecondary)} dominantBaseline="middle">{Math.round(maxSecondary)}{activeFilterOverlay === 'USG%' ? '%' : ''}</text>
                            </>
                        )}
                    </g>
                )}

                {/* 2. Map through GameLogs for Bars, Text, and Logos */}
                <AnimatePresence>
                    {chartData.map((game, index) => {
                        // Center the bar within its allocated spacing column
                        const columnCenter = X_START + paddingLeft + (index * spacing) + (spacing / 2);
                        const yPos = getY(game.score);
                        const barHeight = getBarHeight(game.score);
                        const isOver = game.score >= activeLineThreshold;

                        // Dynamically scale inner elements if bars get very narrow
                        const logoSize = Math.min(barWidth * 1.2, 28);
                        const fontSize = Math.min(barWidth * 0.6, 14);

                        const uniqueKey = game.isUpcoming ? 'upcoming' : `${game.GAME_DATE}-${game.MATCHUP}`;
                        const showLabel = !shouldCondense || condensedLabels.has(index);

                        return (
                            <motion.g
                                key={uniqueKey}
                                initial={{ opacity: 0, y: 30, x: columnCenter }}
                                animate={{ opacity: 1, y: 0, x: columnCenter }}
                                exit={{ opacity: 0, y: 50, transition: { duration: 0.2 } }}
                                transition={{ duration: 0.3 }}
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
                                    if (isDragging) return;
                                    if (hoverData) {
                                        setHoverData({ ...hoverData, x: e.clientX, y: e.clientY });
                                    }
                                }}
                                onMouseLeave={() => {
                                    if (!isDragging) setHoverData(null)
                                }}
                            >
                                {/* The Bar */}
                                {game.isUpcoming ? (
                                    <motion.rect
                                        initial={false}
                                        animate={{
                                            y: getY(lineValue),
                                            height: getBarHeight(lineValue)
                                        }}
                                        transition={{ duration: 0.4, ease: "easeInOut" }}
                                        x={-barWidth / 2}
                                        width={barWidth}
                                        rx="4"
                                        ry="4"
                                        fill="rgba(255, 255, 255, 0.05)"
                                        stroke="#FFFFFF"
                                        strokeWidth="1.5"
                                        strokeDasharray="4 4"
                                        className="opacity-100"
                                    >
                                        <title>Upcoming Game</title>
                                    </motion.rect>
                                ) : (
                                    <motion.rect
                                        initial={false}
                                        animate={{
                                            y: yPos,
                                            height: barHeight,
                                            fill: isOver ? colors.graphBarOver : colors.graphBarUnder
                                        }}
                                        transition={{ duration: 0.4, ease: "easeInOut" }}
                                        x={-barWidth / 2}
                                        width={barWidth}
                                        rx="4"
                                        ry="4"
                                        className="group-hover:brightness-110 group-hover:opacity-80"
                                    >
                                        <title>{`${game.dateMonth} ${game.dateDay} vs ${game.opponent} - ${game.score} ${statKey}`}</title>
                                    </motion.rect>
                                )}

                                {/* Stat Value Text Inside Bar */}
                                {!shouldCondense && (
                                    <text
                                        x={0}
                                        y={VIEWBOX_HEIGHT - 128}
                                        textAnchor="middle"
                                        fill={game.isUpcoming ? colors.fgSubtle : colors.fixedWhite}
                                        fontWeight="900"
                                        fontSize={fontSize}
                                        className="pointer-events-none drop-shadow-md transition-all duration-300"
                                    >
                                        {game.isUpcoming ? '?' : game.score}
                                    </text>
                                )}

                                {/* Visual Logic based on Games Count */}
                                {!shouldCondense && (
                                    <>
                                        {/* Team Logo */}
                                        <image
                                            x={-logoSize / 2}
                                            y={VIEWBOX_HEIGHT - 114}
                                            width={logoSize}
                                            height={logoSize}
                                            href={game.logoUrl}
                                            className="transition-all duration-300"
                                        />
                                    </>
                                )}

                                {/* Stacked Date Label with connecting dots visualization */}
                                {showLabel && (() => {
                                    // Logic to determine if we should show connecting dots
                                    const nextGame = chartData[index + 1];
                                    const isConnected = nextGame && nextGame.dateMonth === game.dateMonth && !game.isUpcoming && parseInt(nextGame.dateDay) - parseInt(game.dateDay) === 1;

                                    return (
                                        <g className="transition-all duration-300">
                                            <text
                                                x={0}
                                                y={shouldCondense ? VIEWBOX_HEIGHT - 105 : VIEWBOX_HEIGHT - 75}
                                                textAnchor="middle"
                                            >
                                                <tspan x={0} dy="0" fill={colors.fgSubtle} fontSize="10" fontWeight="normal">
                                                    {game.dateMonth}
                                                </tspan>
                                                <tspan x={0} dy="1.2em" fill={colors.fgDefault} fontSize="11" fontWeight="700">
                                                    {game.dateDay}
                                                </tspan>
                                            </text>

                                            {/* Render connecting dots exactly centered between columns */}
                                            {!shouldCondense && isConnected && (
                                                <text
                                                    x={spacing / 2}
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
                            </motion.g>
                        );
                    })}
                </AnimatePresence>

                {/* Secondary Line Chart Path Overlay */}
                {activeFilterOverlay && (
                    <g>
                        <path
                            d={chartData.map((d, i) => {
                                const columnCenter = X_START + paddingLeft + (i * spacing) + (spacing / 2);
                                const yPos = getSecondaryY(d.secondaryValue || 0);
                                if (d.isUpcoming) {
                                    return ''; // Handled separately
                                }
                                return `${i === 0 ? 'M' : 'L'} ${columnCenter} ${yPos}`;
                            }).join(' ')}
                            fill="none"
                            stroke={colors.blue500}
                            strokeWidth="2.5"
                            className="drop-shadow-sm transition-all duration-300"
                        />
                        {/* Connecting upcoming dotted line */}
                        {chartData.length > 1 && (() => {
                            const lastGameIndex = chartData.length - 2;
                            const prevX = X_START + paddingLeft + (lastGameIndex * spacing) + (spacing / 2);
                            const prevY = getSecondaryY(chartData[lastGameIndex].secondaryValue);

                            const upcomingX = X_START + paddingLeft + ((chartData.length - 1) * spacing) + (spacing / 2);
                            const upcomingY = getSecondaryY(chartData[chartData.length - 1].secondaryValue);

                            // Skip if either value is invalid
                            if (chartData[lastGameIndex].secondaryValue === null || chartData[lastGameIndex].secondaryValue === undefined ||
                                chartData[chartData.length - 1].secondaryValue === null || chartData[chartData.length - 1].secondaryValue === undefined) {
                                return null;
                            }

                            return (
                                <>
                                    <line
                                        x1={prevX}
                                        y1={prevY}
                                        x2={upcomingX}
                                        y2={upcomingY}
                                        fill="none"
                                        stroke="currentColor"
                                        className="text-blue500 opacity-60"
                                        strokeWidth="2.5"
                                        strokeDasharray="6 4"
                                    />
                                    {/* Cap indicator for the upcoming expected node */}
                                    <line
                                        x1={upcomingX}
                                        y1={upcomingY - 5}
                                        x2={upcomingX}
                                        y2={upcomingY + 5}
                                        stroke="currentColor"
                                        className="text-blue500 opacity-60"
                                        strokeWidth="2.5"
                                    />
                                </>
                            );
                        })()}
                    </g>
                )}

                {/* 3. The Interactive Prop Line Threshold Overlay */}
                <g
                    className="group"
                    style={{ cursor: isDragging ? 'grabbing' : 'grab' }}
                    onMouseDown={() => setIsDragging(true)}
                    onTouchStart={() => setIsDragging(true)}
                    onDoubleClick={(e) => {
                        e.stopPropagation();
                        // Reset line to sportsbook default on double click
                        if (onCustomLineChange) onCustomLineChange(null);
                        setCurrentValue(lineValue);
                    }}
                >
                    {/* Invisible thicker line for easier hovering/grabbing */}
                    <motion.line
                        initial={false}
                        animate={{ y1: propLineY, y2: propLineY }}
                        transition={{ duration: isDragging ? 0 : 0.4, ease: "easeInOut" }}
                        x1="16"
                        x2="100%"
                        stroke="transparent"
                        strokeWidth="24"
                    />

                    {/* The Visual Yellow Line - Solid to match target */}
                    <motion.line
                        initial={false}
                        animate={{ y1: propLineY, y2: propLineY }}
                        transition={{ duration: isDragging ? 0 : 0.4, ease: "easeInOut" }}
                        x1="58"
                        x2="100%"
                        stroke={colors.yellow400}
                        strokeWidth="1.5"
                        className={`drop-shadow-sm transition-opacity duration-150 ${isDragging ? 'opacity-80' : 'opacity-100'}`}
                    />

                    {/* Default Rectangular Handle (Hidden on Hover) */}
                    <motion.rect
                        initial={false}
                        animate={{ y: propLineY - 12 }}
                        transition={{ duration: isDragging ? 0 : 0.4, ease: "easeInOut" }}
                        x="16"
                        width="42"
                        height="24"
                        rx="4"
                        fill={colors.yellow400}
                        className="group-hover:opacity-0 transition-opacity"
                    />

                    {/* Default Handle Text (Aligned with the y-axis text right edge) */}
                    <motion.text
                        initial={false}
                        animate={{ y: propLineY + 1 }}
                        transition={{ duration: isDragging ? 0 : 0.4, ease: "easeInOut" }}
                        x="50"
                        dominantBaseline="middle"
                        textAnchor="end"
                        fontSize="12"
                        className="font-bold fill-black group-hover:opacity-0 transition-opacity"
                    >
                        {currentValue !== null ? currentValue : lineValue}
                    </motion.text>

                    {/* Grabbable 3x2 Dot Matrix Handle (Shown on Hover or Dragging) */}
                    <motion.g
                        initial={false}
                        animate={{ y: propLineY - 12, x: 16 }}
                        transition={{ duration: isDragging ? 0 : 0.4, ease: "easeInOut" }}
                        className={`transition-opacity ${isDragging ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}
                    >
                        {/* Background rounding for dots - Yellow */}
                        <rect
                            x="0"
                            y="0"
                            width="42"
                            height="24"
                            rx="4"
                            fill={colors.yellow400}
                        />

                        {/* The 3x2 Dot Grid - Black */}
                        <circle cx="15" cy="8" r="1.5" fill={colors.black} />
                        <circle cx="21" cy="8" r="1.5" fill={colors.black} />
                        <circle cx="27" cy="8" r="1.5" fill={colors.black} />

                        <circle cx="15" cy="16" r="1.5" fill={colors.black} />
                        <circle cx="21" cy="16" r="1.5" fill={colors.black} />
                        <circle cx="27" cy="16" r="1.5" fill={colors.black} />
                    </motion.g>

                    {/* Dragging Value Tooltip Bubble */}
                    {isDragging && (
                        <motion.g
                            initial={false}
                            animate={{ y: propLineY - 12, x: 65 }}
                            transition={{ duration: 0 }}
                        >
                            {/* Little triangular point connecting bubble to line */}
                            <polygon points="0,12 8,8 8,16" fill={colors.yellow400} />

                            {/* Tooltip Background Bubble */}
                            <rect
                                x="6"
                                y="0"
                                width="44"
                                height="24"
                                rx="12"
                                fill={colors.yellow400}
                                className="drop-shadow-sm"
                            />

                            {/* Live Text Value */}
                            <text
                                x="28"
                                y="13"
                                dominantBaseline="middle"
                                textAnchor="middle"
                                fontSize="12"
                                fontWeight="900"
                                fill={colors.black}
                                className="font-chakra tracking-tight font-bold"
                            >
                                {currentValue}
                            </text>
                        </motion.g>
                    )}
                </g>
            </svg>

            {/* Bottom Legend Container */}
            <div className={`absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center justify-center gap-6 z-20 w-full`}>
                <div className="flex items-center gap-1.5 flex-1 justify-end">
                    <div className="w-2.5 h-2.5 bg-yellow400 rounded-[1px]" />
                    <span className="text-fgSubtle font-bold text-[9px] tracking-widest uppercase">LINE</span>
                </div>

                {activeFilterOverlay ? (
                    <div className="flex items-center gap-1.5 flex-1 justify-start">
                        <div className="w-2.5 h-2.5 bg-blue500 rounded-[1px]" />
                        <span className="text-blue500 font-bold text-[9px] tracking-widest uppercase whitespace-nowrap">
                            {activeFilterOverlay} {isRankOverlay ? `[OPP RANK: #${Math.round(graphAvgSecondary)}]` : `[GRAPH AVG: ${graphAvgSecondary.toFixed(1)}${activeFilterOverlay === 'USG%' ? '%' : ''} | SEASON AVG: ${seasonAvgSecondary.toFixed(1)}${activeFilterOverlay === 'USG%' ? '%' : ''}]`}
                        </span>
                    </div>
                ) : (
                    <div className="flex-1"></div>
                )}
            </div>

            {/* Footer / Watermark */}
            <div className="absolute bottom-3 left-3 pointer-events-none opacity-40 z-20">
                <span className="text-neutral600 font-medium text-[10px] tracking-widest uppercase">PropX</span>
            </div>

            {/* Hover Tooltip Overlay */}
            <HoverTooltip data={hoverData} player={player} />
        </div>
    );
};
