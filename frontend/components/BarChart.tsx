import React, { useMemo, useState, useEffect, useRef } from 'react';
import { ActiveTeammateFilter, Player, Game, SportsbookId } from '../types';
import { TEAM_IDS } from '../constants';
import { HoverTooltip, HoveredGameData } from './HoverTooltip';
import { colors } from '../utils/propsmadness_colors';
import { motion } from 'framer-motion';
import { ASSETS_BASE } from '../utils/config';
import { getDashboardDate, getDashboardScheduleDates } from '../utils/dashboardDate';
import {
    formatOverlayAxisValue,
    formatOverlayLegend,
    getOverlayFilterDefinition,
} from '../utils/filterOverlays';
import { fetchApiJson } from '../utils/network';
import { fetchDashboardGames } from '../utils/dashboardApi';

const USE_DB = import.meta.env.VITE_USE_DB === 'true';

interface BarChartProps {
    player?: Player;
    activeTab: string;
    activeSportsbook: SportsbookId;
    customLine?: number | null;
    onCustomLineChange?: (line: number | null) => void;
    activeFilterOverlay?: string | null;
    activeTeammateFilter?: ActiveTeammateFilter | null;
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

const CHART_MODE_FILTERS = new Set(['H2H', 'Home', 'Away', 'B2B']);
const ACTION_NETWORK_ODDS_PATH = '/data/current/action_network_odds.json';

const getGameKey = (game: any) => `${game?.GAME_ID ?? game?.GAME_DATE ?? ''}-${game?.MATCHUP ?? ''}`;

const getIsHomeGame = (game: any) => String(game?.MATCHUP || '').includes('vs.');
const getIsAwayGame = (game: any) => String(game?.MATCHUP || '').includes('@');

const getScheduleMarketKey = (game: any) => {
    const gameDate = String(game?.game_date ?? '').trim();
    const awayTeam = String(game?.away_team_tricode ?? '').trim().toUpperCase();
    const homeTeam = String(game?.home_team_tricode ?? '').trim().toUpperCase();
    if (!gameDate || !awayTeam || !homeTeam) return null;
    return `${gameDate}:${awayTeam}@${homeTeam}`;
};

const mergeActionNetworkMarkets = (
    scheduleRows: Game[],
    oddsRows?: Game[] | null,
) => {
    if (!Array.isArray(oddsRows) || !oddsRows.length) {
        return scheduleRows;
    }

    const oddsByGameId = new Map<string, Game>();
    const oddsByMatchup = new Map<string, Game>();

    oddsRows.forEach((game) => {
        const gameId = String(game?.game_id ?? '').trim();
        if (gameId) {
            oddsByGameId.set(gameId, game);
        }

        const matchupKey = getScheduleMarketKey(game);
        if (matchupKey) {
            oddsByMatchup.set(matchupKey, game);
        }
    });

    return scheduleRows.map((game) => {
        const gameId = String(game?.game_id ?? '').trim();
        const matchupKey = getScheduleMarketKey(game);
        const marketGame = (gameId ? oddsByGameId.get(gameId) : null)
            ?? (matchupKey ? oddsByMatchup.get(matchupKey) : null);

        if (!marketGame) {
            return game;
        }

        return {
            ...game,
            has_action_network_markets: marketGame.has_action_network_markets ?? Boolean(marketGame.markets),
            markets: marketGame.markets ?? game.markets,
        };
    });
};

export const BarChart: React.FC<BarChartProps> = ({ player, activeTab, activeSportsbook, customLine, onCustomLineChange, activeFilterOverlay, activeTeammateFilter, isFiltersOpen, historicalGameCount, activeSeason }) => {
    const statKey = STAT_LABELS[activeTab] || 'PTS';
    const chartMode = CHART_MODE_FILTERS.has(activeFilterOverlay ?? '') ? activeFilterOverlay : null;
    const overlayDefinition = useMemo(
        () => (chartMode ? null : getOverlayFilterDefinition(activeFilterOverlay)),
        [activeFilterOverlay, chartMode],
    );
    const shouldHoldPreviousChart = Boolean(
        USE_DB
        && activeSeason === '25/26'
        && player
        && !player.detail_loaded
        && (player.game_log?.length ?? 0) === 0
    );

    // Hover State
    const [hoverData, setHoverData] = useState<HoveredGameData | null>(null);
    const [stableChartPlayer, setStableChartPlayer] = useState<Player | undefined>(() => (
        !shouldHoldPreviousChart && player ? player : undefined
    ));
    const pendingSettledPlayerRef = useRef<number | null>(null);
    const [isChartSettling, setIsChartSettling] = useState(false);

    // Mobile specific chart adaptations
    const [isMobile, setIsMobile] = useState(typeof window !== 'undefined' && window.innerWidth < 1024);
    useEffect(() => {
        const handleResize = () => setIsMobile(window.innerWidth < 1024);
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    useEffect(() => {
        setHoverData(null);
    }, [
        player?.id,
        activeTab,
        activeSportsbook,
        activeSeason,
        activeFilterOverlay,
        activeTeammateFilter?.playerId,
        activeTeammateFilter?.playerName,
        activeTeammateFilter?.mode,
    ]);

    useEffect(() => {
        if (!player) {
            setStableChartPlayer(undefined);
            return;
        }

        if (!shouldHoldPreviousChart) {
            setStableChartPlayer(player);
        }
    }, [player, shouldHoldPreviousChart]);

    useEffect(() => {
        if (!player?.id) {
            pendingSettledPlayerRef.current = null;
            setIsChartSettling(false);
            return;
        }

        if (shouldHoldPreviousChart) {
            pendingSettledPlayerRef.current = player.id;
            setIsChartSettling(false);
            return;
        }

        if (pendingSettledPlayerRef.current === player.id) {
            pendingSettledPlayerRef.current = null;
            setIsChartSettling(true);

            const timeoutId = window.setTimeout(() => {
                setIsChartSettling(false);
            }, 180);

            return () => {
                window.clearTimeout(timeoutId);
            };
        }

        setIsChartSettling(false);
    }, [player?.id, shouldHoldPreviousChart]);

    const chartPlayer = shouldHoldPreviousChart
        ? (stableChartPlayer ?? player)
        : player;

    // Dragging Line State
    const [isDragging, setIsDragging] = useState(false);
    const [dragY, setDragY] = useState<number | null>(null);
    const [currentValue, setCurrentValue] = useState<number | null>(null);
    const svgRef = React.useRef<SVGSVGElement>(null);

    // Schedule State for Upcoming Game 
    const [scheduleData, setScheduleData] = useState<Game[]>([]);
    const scheduleCacheRef = useRef(new Map<string, Game[]>());
    const scheduleKeyRef = useRef('');

    useEffect(() => {
        const propDates = Array.from(new Set(
            Object.values(chartPlayer?.props_by_date ?? {}).flatMap((statEntry) =>
                Object.values(statEntry ?? {}).flatMap((bookEntry) =>
                    Object.keys(bookEntry ?? {}).filter((dateKey) => dateKey !== '__undated__')
                )
            )
        )).sort();
        const relevantDates = propDates.length > 0 ? propDates : Array.from(getDashboardScheduleDates());
        const scheduleKey = relevantDates.join('|');

        if (scheduleKeyRef.current === scheduleKey) {
            return;
        }

        const cachedSchedule = scheduleCacheRef.current.get(scheduleKey);
        if (cachedSchedule) {
            scheduleKeyRef.current = scheduleKey;
            setScheduleData(cachedSchedule);
            return;
        }

        let cancelled = false;

        if (USE_DB) {
            fetchDashboardGames(relevantDates)
                .then(({ games }) => {
                    if (cancelled) return;
                    const nextGames = games ?? [];
                    scheduleCacheRef.current.set(scheduleKey, nextGames);
                    scheduleKeyRef.current = scheduleKey;
                    setScheduleData(nextGames);
                })
                .catch((error) => {
                    console.error('[api] games error:', error);
                });
        } else {
            const actionOddsPromise = fetchApiJson<{ games?: Game[] }>(ACTION_NETWORK_ODDS_PATH)
                .catch(() => ({ games: [] }));

            Promise.all([
                fetchApiJson<Game[]>('/data/current/nba_dashboard_games.json'),
                actionOddsPromise,
            ])
                .then(([data, actionOdds]) => {
                    if (cancelled) return;
                    const filteredGames = (data as Game[]).filter(g => relevantDates.includes(g.game_date));
                    const nextGames = mergeActionNetworkMarkets(filteredGames, actionOdds?.games ?? []);
                    scheduleCacheRef.current.set(scheduleKey, nextGames);
                    scheduleKeyRef.current = scheduleKey;
                    setScheduleData(nextGames);
                })
                .catch(err => console.error("Error loading schedule:", err));
        }

        return () => {
            cancelled = true;
        };
    }, [chartPlayer]);

    const { chartData, lineValue, graphAvgSecondary, comparisonAvgSecondary, isRankOverlay, isBinaryOverlay, historicalSampleCount } = useMemo(() => {
        if (!chartPlayer || !chartPlayer.game_log) {
            return {
                chartData: [],
                lineValue: 0,
                graphAvgSecondary: 0,
                comparisonAvgSecondary: null,
                isRankOverlay: false,
                isBinaryOverlay: false,
                historicalSampleCount: 0,
            };
        }

        const prop = chartPlayer.props?.[statKey]?.[activeSportsbook];
        const line = prop?.line || 0;
        const numGames = (isMobile && !isFiltersOpen) ? 12 : (historicalGameCount || (isFiltersOpen ? 19 : 29));
        const chronologicalSeasonGames = [...chartPlayer.game_log].sort(
            (a: any, b: any) => new Date(a.GAME_DATE).getTime() - new Date(b.GAME_DATE).getTime(),
        );
        const b2bKeys = new Set<string>();
        chronologicalSeasonGames.forEach((game: any, index: number) => {
            if (index === 0) return;
            const currentDate = new Date(game.GAME_DATE);
            const previousDate = new Date(chronologicalSeasonGames[index - 1].GAME_DATE);
            const diffDays = Math.round((currentDate.getTime() - previousDate.getTime()) / 86400000);
            if (diffDays === 1) {
                b2bKeys.add(getGameKey(game));
            }
        });

        const dashboardDate = getDashboardDate();
        const fallbackMonths = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        let upcomingGame: Game | null = null;
        let upcomingOpponent = 'TBD';
        let upcomingMonth = fallbackMonths[Number(dashboardDate.slice(5, 7)) - 1];
        let upcomingDay = dashboardDate.slice(8, 10);

        if (chartPlayer.team && scheduleData.length > 0) {
            const sortedSchedule = [...scheduleData].sort((a, b) =>
                new Date(a.game_time_utc).getTime() - new Date(b.game_time_utc).getTime()
            );
            upcomingGame = sortedSchedule.find(g =>
                (g.home_team_tricode === chartPlayer.team || g.away_team_tricode === chartPlayer.team)
                && (!prop?.game_date || g.game_date === prop.game_date)
            ) || sortedSchedule.find(g => g.home_team_tricode === chartPlayer.team || g.away_team_tricode === chartPlayer.team) || null;

            if (upcomingGame) {
                upcomingOpponent = upcomingGame.home_team_tricode === chartPlayer.team ? upcomingGame.away_team_tricode : upcomingGame.home_team_tricode;
                if (upcomingGame.game_date) {
                    const [_, monthStr, dayStr] = upcomingGame.game_date.split('-');
                    if (monthStr && dayStr) {
                        upcomingMonth = fallbackMonths[parseInt(monthStr, 10) - 1];
                        upcomingDay = dayStr;
                    }
                }
            }
        }

        const matchesChartMode = (game: any) => {
            if (!chartMode) return true;
            if (chartMode === 'H2H') {
                if (upcomingOpponent === 'TBD') return false;
                const parts = String(game?.MATCHUP || '').split(' ');
                const opponent = parts[parts.length - 1];
                return opponent === upcomingOpponent;
            }
            if (chartMode === 'Home') return getIsHomeGame(game);
            if (chartMode === 'Away') return getIsAwayGame(game);
            if (chartMode === 'B2B') return b2bKeys.has(getGameKey(game));
            return true;
        };
        const teammateGameIds = new Set(
            activeTeammateFilter?.activeGameIds ?? [],
        );
        const matchesTeammateMode = (game: any) => {
            if (!activeTeammateFilter) return true;
            const gameId = String(game?.GAME_ID ?? '').trim();
            const teammateWasActive = gameId ? teammateGameIds.has(gameId) : false;
            return activeTeammateFilter.mode === 'with'
                ? teammateWasActive
                : !teammateWasActive;
        };
        const teammateFilteredGames = activeTeammateFilter
            ? chartPlayer.game_log.filter((game: any) => matchesTeammateMode(game))
            : chartPlayer.game_log;

        const selectedGames = chartMode
            ? teammateFilteredGames.filter((game: any) => matchesChartMode(game)).slice(0, numGames)
            : teammateFilteredGames.slice(0, numGames);

        const log = [...selectedGames].reverse();

        const data: any[] = log.map((game, gameIndex) => {
            let val = game[statKey];
            if (val === undefined) {
                if (statKey === 'PTS+REB+AST') val = (game.PTS || 0) + (game.REB || 0) + (game.AST || 0);
                else if (statKey === 'PTS+REB') val = (game.PTS || 0) + (game.REB || 0);
                else if (statKey === 'PTS+AST') val = (game.PTS || 0) + (game.AST || 0);
                else if (statKey === 'REB+AST') val = (game.REB || 0) + (game.AST || 0);
                else val = 0;
            }

            const parts = game.MATCHUP.split(' ');
            const opponent = parts[parts.length - 1];
            const opponentId = TEAM_IDS[opponent];
            const logoUrl = opponentId
                ? `${ASSETS_BASE}/assets/team_logos/${opponentId}.svg`
                : `${ASSETS_BASE}/assets/team_logos/${opponent}.svg`;

            const [__, monthStr, day] = game.GAME_DATE.split('-');
            const month = fallbackMonths[parseInt(monthStr, 10) - 1];

            const secondaryValue = overlayDefinition?.getGameValue({
                player: chartPlayer,
                game,
                gameIndex,
                games: log,
                upcomingGame,
                upcomingOpponent,
            }) ?? null;

            return {
                ...game,
                score: val,
                secondaryValue,
                opponent,
                logoUrl,
                dateMonth: month,
                dateDay: day,
            };
        });

        const validSecondary = data
            .map((game) => game.secondaryValue)
            .filter((value): value is number => value !== null && value !== undefined && Number.isFinite(Number(value)))
            .map(Number);

        const graphAvgSecondary = validSecondary.length > 0
            ? validSecondary.reduce((sum, value) => sum + value, 0) / validSecondary.length
            : 0;

        const comparisonAvgSecondary = overlayDefinition?.getComparisonValue?.({
            player: chartPlayer,
            games: log,
            upcomingGame,
            upcomingOpponent,
            graphAverage: graphAvgSecondary,
        }) ?? null;

        const latestSeasonGame = chronologicalSeasonGames[chronologicalSeasonGames.length - 1] ?? null;
        const latestSeasonGameDate = latestSeasonGame ? new Date(latestSeasonGame.GAME_DATE) : null;
        const upcomingGameDate = upcomingGame?.game_date ? new Date(upcomingGame.game_date) : null;
        const isUpcomingB2B = Boolean(
            latestSeasonGameDate
            && upcomingGameDate
            && !Number.isNaN(upcomingGameDate.getTime())
            && Math.round((upcomingGameDate.getTime() - latestSeasonGameDate.getTime()) / 86400000) === 1,
        );

        const includeUpcomingGame = activeSeason !== '24/25' && (
            !chartMode
            || (chartMode === 'H2H' && upcomingOpponent !== 'TBD')
            || (chartMode === 'Home' && Boolean(upcomingGame && upcomingGame.home_team_tricode === chartPlayer.team))
            || (chartMode === 'Away' && Boolean(upcomingGame && upcomingGame.away_team_tricode === chartPlayer.team))
            || (chartMode === 'B2B' && isUpcomingB2B)
        );
        const canRenderUpcomingPlaceholder = log.length > 0 || Boolean(chartPlayer.detail_loaded);

        if (includeUpcomingGame && canRenderUpcomingPlaceholder) {
            const upcomingOpponentId = TEAM_IDS[upcomingOpponent];
            const upcomingLogoUrl = upcomingOpponentId
                ? `${ASSETS_BASE}/assets/team_logos/${upcomingOpponentId}.svg`
                : upcomingOpponent === 'TBD'
                    ? `${ASSETS_BASE}/assets/team_logos/${TEAM_IDS['HOU'] || 'HOU'}.svg`
                    : `${ASSETS_BASE}/assets/team_logos/${upcomingOpponent}.svg`;

            let upcomingSecondaryValue = null;
            if (overlayDefinition?.getUpcomingValue) {
                upcomingSecondaryValue = overlayDefinition.getUpcomingValue({
                    player: chartPlayer,
                    games: log,
                    upcomingGame,
                    upcomingOpponent,
                    graphAverage: graphAvgSecondary,
                });
            } else if (overlayDefinition?.fallbackUpcoming === 'graph_average' && validSecondary.length > 0) {
                upcomingSecondaryValue = graphAvgSecondary;
            }

            data.push({
                GAME_DATE: upcomingGame?.game_date ?? dashboardDate,
                GAME_ID: upcomingGame?.game_id ?? `upcoming-${dashboardDate}-${chartPlayer.team}-${upcomingOpponent}`,
                MATCHUP: upcomingGame?.matchup
                    ?? `${chartPlayer.team} vs. ${upcomingOpponent}`,
                away_team_tricode: upcomingGame?.away_team_tricode ?? null,
                game_time_et: upcomingGame?.game_time_et ?? null,
                has_action_network_markets: upcomingGame?.has_action_network_markets ?? false,
                home_team_tricode: upcomingGame?.home_team_tricode ?? null,
                markets: upcomingGame?.markets ?? {},
                score: null,
                secondaryValue: upcomingSecondaryValue,
                opponent: upcomingOpponent,
                logoUrl: upcomingLogoUrl,
                dateMonth: upcomingMonth,
                dateDay: upcomingDay,
                isUpcoming: true,
            });
        }

        return {
            chartData: data,
            lineValue: line,
            graphAvgSecondary,
            comparisonAvgSecondary,
            isRankOverlay: overlayDefinition?.kind === 'rank',
            isBinaryOverlay: overlayDefinition?.kind === 'binary',
            historicalSampleCount: log.length,
        };
    }, [chartPlayer, statKey, activeSportsbook, activeTeammateFilter, scheduleData, overlayDefinition, chartMode, isFiltersOpen, historicalGameCount, activeSeason, isMobile]);

    if (!player) return null;

    // --- Responsive SVG Layout Constants ---
    const VIEWBOX_WIDTH = isMobile ? 650 : (isFiltersOpen ? 700 : 1000); // Internal coordinate system width dynamcially adjusts to prevent shrinking
    const VIEWBOX_HEIGHT = 400; // Internal coordinate system height
    const X_START = 80;         // Left margin to leave room for Y-axis labels
    const X_END = VIEWBOX_WIDTH - 20; // Right margin
    const AVAILABLE_WIDTH = X_END - X_START;

    const baseVisibleCount = (isMobile && !isFiltersOpen) ? 14 : (historicalGameCount || (isFiltersOpen ? 19 : 29));
    const currentVisibleCount = historicalSampleCount > 0
        ? Math.min(baseVisibleCount, historicalSampleCount)
        : baseVisibleCount;
    const visibleBarCount = Math.max(chartData.length, 1);
    const useCompactLayout = currentVisibleCount <= 19;
    const compactSpacingCap = isMobile ? 36 : 31;
    const spacing = useCompactLayout
        ? Math.min(AVAILABLE_WIDTH / visibleBarCount, compactSpacingCap)
        : AVAILABLE_WIDTH / visibleBarCount;

    // Keep short samples visually dense instead of stretching them across the full chart width.
    const totalContentWidth = chartData.length * spacing;
    const paddingLeft = useCompactLayout ? Math.max((AVAILABLE_WIDTH - totalContentWidth) / 2, 0) : 0;

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
    const secondaryValues = chartData
        .map((game) => game.secondaryValue)
        .filter((value): value is number => value !== null && value !== undefined && Number.isFinite(Number(value)))
        .map(Number);
    const hasSecondaryOverlay = Boolean(overlayDefinition && secondaryValues.length > 0);

    let minSecondary = 0;
    let maxSecondary = 10;
    if (hasSecondaryOverlay) {
        if (isRankOverlay) {
            minSecondary = 1;
            maxSecondary = 30;
        } else if (isBinaryOverlay) {
            minSecondary = 0;
            maxSecondary = 1;
        } else {
            let rawMin = Math.min(...secondaryValues, 0);
            let rawMax = Math.max(...secondaryValues, 0);
            if (rawMin === rawMax) {
                const pad = Math.max(1, Math.abs(rawMax) * 0.15 || 1);
                rawMin -= pad;
                rawMax += pad;
            } else {
                const pad = (rawMax - rawMin) * 0.15;
                rawMin -= pad;
                rawMax += pad;
                if (rawMin > 0) rawMin = 0;
            }
            minSecondary = rawMin;
            maxSecondary = rawMax;
        }
    }
    const secondaryRange = Math.max(maxSecondary - minSecondary, 1);

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
        return VIEWBOX_HEIGHT - 120 - (((val - minSecondary) / secondaryRange) * availableHeight);
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
    const secondaryTickValues = isRankOverlay
        ? [30, 20, 10, 1]
        : [minSecondary, minSecondary + (secondaryRange / 3), minSecondary + ((secondaryRange * 2) / 3), maxSecondary];
    const historicalSecondaryPoints = hasSecondaryOverlay
        ? chartData.flatMap((game, index) => {
            if (game.isUpcoming || game.secondaryValue === null || game.secondaryValue === undefined) return [];
            const columnCenter = X_START + paddingLeft + (index * spacing) + (spacing / 2);
            return [{
                x: columnCenter,
                y: getSecondaryY(Number(game.secondaryValue)),
            }];
        })
        : [];
    const upcomingSecondaryPoint = hasSecondaryOverlay && chartData.length > 0 ? (() => {
        const game = chartData[chartData.length - 1];
        if (!game?.isUpcoming || game.secondaryValue === null || game.secondaryValue === undefined) return null;
        const x = X_START + paddingLeft + ((chartData.length - 1) * spacing) + (spacing / 2);
        return { x, y: getSecondaryY(Number(game.secondaryValue)) };
    })() : null;

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
                    <line x1="60" x2={hasSecondaryOverlay ? "100%" : X_END} y1={getY(maxScore)} y2={getY(maxScore)} stroke={colors.borderMedium} strokeOpacity="0.4" />
                </g>

                {/* Secondary Y-Axis Grid Lines & Labels (Right Side) */}
                {hasSecondaryOverlay && (
                    <g className="text-blue500 font-bold" fill="currentColor" textAnchor="start" fontSize="12">
                        {secondaryTickValues.map((tickValue, index) => (
                            <text
                                key={`${tickValue}-${index}`}
                                x={X_END + 4}
                                y={getSecondaryY(tickValue)}
                                dominantBaseline="middle"
                            >
                                {formatOverlayAxisValue(overlayDefinition, tickValue)}
                            </text>
                        ))}
                    </g>
                )}

                <motion.g
                    initial={false}
                    animate={isChartSettling
                        ? { opacity: [0.9, 1], y: [3, 0] }
                        : { opacity: 1, y: 0 }}
                    transition={{ duration: 0.18, ease: 'easeOut' }}
                >
                {/* 2. Map through GameLogs for Bars, Text, and Logos */}
                {chartData.map((game, index) => {
                        // Center the bar within its allocated spacing column
                        const columnCenter = X_START + paddingLeft + (index * spacing) + (spacing / 2);
                        const yPos = getY(game.score);
                        const barHeight = getBarHeight(game.score);
                        const isOver = game.score >= activeLineThreshold;

                        // Dynamically scale inner elements if bars get very narrow
                        const logoSize = Math.min(barWidth * 1.2, 28);
                        const fontSize = Math.min(barWidth * 0.6, 14);

                        const showLabel = !shouldCondense || condensedLabels.has(index);

                        return (
                            <motion.g
                                key={`slot-${index}`}
                                initial={false}
                                animate={{ opacity: 1, x: columnCenter, y: 0 }}
                                transition={{ type: 'spring', stiffness: 260, damping: 28, mass: 0.8 }}
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
                                        transition={{ type: 'spring', stiffness: 220, damping: 24, mass: 0.8 }}
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
                                        transition={{ type: 'spring', stiffness: 220, damping: 24, mass: 0.8 }}
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

                {/* Secondary Line Chart Path Overlay */}
                {hasSecondaryOverlay && (
                    <g>
                        <path
                            d={historicalSecondaryPoints.map((point, index) => (
                                `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`
                            )).join(' ')}
                            fill="none"
                            stroke={colors.blue500}
                            strokeWidth="2.5"
                            className="drop-shadow-sm transition-all duration-300"
                        />
                        {/* Connecting upcoming dotted line */}
                        {historicalSecondaryPoints.length > 0 && upcomingSecondaryPoint && (() => {
                            const previousPoint = historicalSecondaryPoints[historicalSecondaryPoints.length - 1];
                            return (
                                <>
                                    <line
                                        x1={previousPoint.x}
                                        y1={previousPoint.y}
                                        x2={upcomingSecondaryPoint.x}
                                        y2={upcomingSecondaryPoint.y}
                                        fill="none"
                                        stroke="currentColor"
                                        className="text-blue500 opacity-60"
                                        strokeWidth="2.5"
                                        strokeDasharray="6 4"
                                    />
                                    {/* Cap indicator for the upcoming expected node */}
                                    <line
                                        x1={upcomingSecondaryPoint.x}
                                        y1={upcomingSecondaryPoint.y - 5}
                                        x2={upcomingSecondaryPoint.x}
                                        y2={upcomingSecondaryPoint.y + 5}
                                        stroke="currentColor"
                                        className="text-blue500 opacity-60"
                                        strokeWidth="2.5"
                                    />
                                </>
                            );
                        })()}
                    </g>
                )}
                </motion.g>

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

                {hasSecondaryOverlay && overlayDefinition ? (
                    <div className="flex items-center gap-1.5 flex-1 justify-start">
                        <div className="w-2.5 h-2.5 bg-blue500 rounded-[1px]" />
                        <span className="text-blue500 font-bold text-[9px] tracking-widest uppercase whitespace-nowrap">
                            {overlayDefinition.label} {formatOverlayLegend(overlayDefinition, graphAvgSecondary, comparisonAvgSecondary)}
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
            <HoverTooltip data={hoverData} player={chartPlayer ?? player} />
        </div>
    );
};
