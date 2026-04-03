import React from 'react';
import { X, HelpCircle, Lock, ChevronUp } from 'lucide-react';
import { ImageWithFallback } from './ui/ImageWithFallback';
import { ASSETS_BASE } from '../utils/config';
import { isOverlayFilterSupported } from '../utils/filterOverlays';
import { Player, TeamInjuryReport, TeammateInjuryCard } from '../types';

interface FiltersPanelProps {
    isOpen: boolean;
    onClose: () => void;
    activeFilter: string | null;
    onFilterChange: (filter: string | null) => void;
    player?: Player | null;
    teammateInjuryCards?: TeammateInjuryCard[];
    teamInjuryReport?: TeamInjuryReport | null;
    gameCount: number;
    onGameCountChange: (count: number) => void;
    activeSeason?: string;
    onSeasonChange?: (s: string) => void;
}

const TEAMMATE_STATUS_META: Record<string, { badge: string; badgeClass: string; textClass: string; label: string }> = {
    Out: {
        badge: 'OUT',
        badgeClass: 'bg-red500 text-white',
        textClass: 'text-red500',
        label: 'Out',
    },
    Questionable: {
        badge: 'QUES',
        badgeClass: 'bg-[#CA8A04] text-white',
        textClass: 'text-[#CA8A04]',
        label: 'Questionable',
    },
    Doubtful: {
        badge: 'DOUBT',
        badgeClass: 'bg-[#CA8A04] text-white',
        textClass: 'text-[#CA8A04]',
        label: 'Doubtful',
    },
    Probable: {
        badge: 'PROB',
        badgeClass: 'bg-[#14532D] text-white',
        textClass: 'text-green500',
        label: 'Probable',
    },
    Available: {
        badge: '',
        badgeClass: '',
        textClass: 'text-green500',
        label: 'Available',
    },
};

function getTeammateStatusMeta(status?: string | null, reportStatus?: string | null) {
    const cleanStatus = String(status ?? '').trim();
    if (cleanStatus && TEAMMATE_STATUS_META[cleanStatus]) {
        return TEAMMATE_STATUS_META[cleanStatus];
    }

    if (reportStatus === 'not_submitted') {
        return {
            badge: '',
            badgeClass: '',
            textClass: 'text-fgSubtle',
            label: 'No report',
        };
    }

    return {
        badge: '',
        badgeClass: '',
        textClass: 'text-fgSubtle',
        label: cleanStatus || 'No report',
    };
}

function getInitials(name?: string | null) {
    const parts = String(name ?? '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) {
        return 'NA';
    }
    if (parts.length === 1) {
        return parts[0].slice(0, 2).toUpperCase();
    }
    return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

function getTeammateImpactMeta(statImpact?: number | null) {
    if (statImpact === null || statImpact === undefined || !Number.isFinite(statImpact)) {
        return {
            label: '0.0',
            className: 'text-fgSubtle',
        };
    }

    return {
        label: statImpact > 0 ? `+${statImpact.toFixed(1)}` : statImpact.toFixed(1),
        className: statImpact > 0
            ? 'text-green500'
            : statImpact < 0
                ? 'text-red500'
                : 'text-fgSubtle',
    };
}

export const FiltersPanel: React.FC<FiltersPanelProps> = ({
    isOpen,
    onClose,
    activeFilter,
    onFilterChange,
    player,
    teammateInjuryCards = [],
    teamInjuryReport = null,
    gameCount,
    onGameCountChange,
    activeSeason,
    onSeasonChange,
}) => {
    const [activeTab, setActiveTab] = React.useState('Suggested');
    const [isSuggestedExpanded, setIsSuggestedExpanded] = React.useState(false);

    if (!isOpen) return null;

    // Helper to format rank colors
    const getRankColor = (rank: number | undefined | null) => {
        if (!rank) return 'text-borderMuted';
        const r = Number(rank);
        if (r <= 10) return 'text-red500'; // Harder
        if (r >= 21) return 'text-green500'; // Easier
        return 'text-gray-400';
    };

    // Calculate DSZ & DSZ2
    let dsz = '';
    let dszRank: number | null = null;
    let dsz2 = '';
    let dsz2Rank: number | null = null;

    if (player?.shooting_zones) {
        const zones = Object.entries(player.shooting_zones).map(([zone, data]: any) => {
            const pct = parseInt(data.percentage.replace('%', ''));
            return { zone, pct };
        }).sort((a, b) => b.pct - a.pct);

        const formatZone = (z: string) => z.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

        if (zones.length > 0) {
            dsz = formatZone(zones[0].zone);
            dszRank = player.opp_def_zones?.[zones[0].zone]?.rank;
        }
        if (zones.length > 1) {
            dsz2 = formatZone(zones[1].zone);
            dsz2Rank = player.opp_def_zones?.[zones[1].zone]?.rank;
        }
    }

    // DPT
    let dpt = '';
    let dptRank: number | null = null;
    if (player?.play_type_analysis && player.play_type_analysis.length > 0) {
        const sortedPlays = [...player.play_type_analysis].sort((a: any, b: any) => {
            const aPct = parseInt(a.percent.replace('%', ''));
            const bPct = parseInt(b.percent.replace('%', ''));
            return bPct - aPct;
        });
        dpt = sortedPlays[0].type;
        dptRank = sortedPlays[0].rank;
    }

    // Paint pts allowed
    const paintPtsRank = player?.opp_def_zones?.paint?.rank;

    // Shot types
    const csRank = player?.shot_type_analysis?.opp_def?.catch_and_shoot?.rank;
    const pullupRank = player?.shot_type_analysis?.opp_def?.pull_up?.rank;

    // Stats
    const usgPct = player?.stats?.USG_PCT;
    const fga = player?.stats?.FGA;

    const renderFilterButton = ({
        filterId,
        label,
        rank,
        customRank,
        className = 'px-2 py-1.5 lg:py-1 rounded-[6px] text-[12px] lg:text-[11px]',
    }: {
        filterId: string;
        label: string;
        rank?: number | null;
        customRank?: React.ReactNode;
        className?: string;
    }) => {
        const isSupported = isOverlayFilterSupported(filterId, player);
        const isActive = activeFilter === filterId;

        return (
            <button
                key={`${filterId}-${label}`}
                onClick={() => isSupported && onFilterChange(isActive ? null : filterId)}
                disabled={!isSupported}
                className={`${className} font-medium flex items-center gap-1 transition-colors border ${isActive
                    ? 'bg-blue500 text-white border-transparent'
                    : isSupported
                        ? 'bg-bgElevation1 text-[#D4D4D4] border-borderMedium/50 hover:bg-bgElevation2 hover:text-white'
                        : 'bg-bgElevation1/60 text-[#A3A3A3]/45 border-borderMedium/30 cursor-not-allowed'
                    }`}
            >
                <span>{label}</span>
                {rank ? <span className={isSupported ? getRankColor(rank) : 'text-[#A3A3A3]/45'}>#{rank}</span> : null}
                {customRank ? <span className={isSupported ? '' : 'opacity-40'}>{customRank}</span> : null}
                {!isSupported ? <Lock className="w-3 h-3 opacity-60" /> : null}
            </button>
        );
    };

    return (
        <div className="w-[320px] bg-bgElevation0 border-l border-borderMedium/40 flex flex-col h-full overflow-y-auto shrink-0 shadow-[-10px_0_30px_rgba(0,0,0,0.5)] z-40">
            {/* Header */}
            <div className="flex items-center justify-between px-3 py-2.5 border-b border-borderMedium/40 shrink-0">
                <div className="flex items-center gap-2">
                    <h2 className="text-white font-semibold text-base">Filters</h2>
                    <HelpCircle className="w-4 h-4 text-borderMuted" />
                </div>
                <button onClick={onClose} className="text-borderMuted hover:text-white transition-colors">
                    <X className="w-4.5 h-4.5" />
                </button>
            </div>

            <div className="p-3 flex flex-col gap-2">
                {/* Season & Games Toggles */}
                <div className="flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold text-fgSubtle uppercase tracking-wider w-[50px]">Season</span>
                        <div className="flex bg-bgElevation1 rounded-md border border-borderMedium/40 p-0.5 flex-1 ml-2">
                            {['23/24', '24/25', '25/26', 'All'].map(s => {
                                const isActive = s === (activeSeason || '25/26');
                                const isClickable = s === '24/25' || s === '25/26';
                                return (
                                    <button
                                        key={s}
                                        onClick={() => isClickable && onSeasonChange && onSeasonChange(s)}
                                        className={`flex-1 py-1 text-xs font-medium rounded transition-colors ${isActive ? 'bg-blue500 text-white shadow-sm' : isClickable ? 'text-fgSubtle hover:text-white cursor-pointer' : 'text-fgSubtle/30 cursor-not-allowed'}`}
                                    >
                                        {s}
                                    </button>
                                );
                            })}
                        </div>
                    </div>

                    <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold text-fgSubtle uppercase tracking-wider w-[50px]">Games</span>
                        <div className="flex items-center gap-2 flex-1 ml-2">
                            <div className="flex bg-bgElevation1 rounded-md border border-borderMedium/40 p-0.5 flex-1">
                                {['10', '20', 'Max'].map(g => {
                                    const maxGames = player?.game_log?.length || 82;
                                    const isMax = g === 'Max';
                                    const handleClick = () => {
                                        if (g === '10') onGameCountChange(10);
                                        else if (g === '20') onGameCountChange(20);
                                        else if (isMax) onGameCountChange(maxGames);
                                    };
                                    const isActive = (g === '10' && gameCount === 10) ||
                                        (g === '20' && gameCount === 20) ||
                                        (isMax && gameCount === maxGames);
                                    return (
                                        <button
                                            key={g}
                                            onClick={handleClick}
                                            className={`flex-1 py-1 text-xs font-medium rounded transition-colors ${isActive ? 'bg-blue500 text-white shadow-sm' : 'text-fgSubtle hover:text-white'}`}
                                        >
                                            {g}
                                        </button>
                                    );
                                })}
                            </div>
                            <div className="flex items-center justify-between bg-bgElevation2 border border-borderMedium/50 rounded-md px-2 py-1 shrink-0 w-[70px]">
                                <span onClick={() => onGameCountChange(Math.max(1, gameCount - 1))} className="text-fgSubtle text-xs cursor-pointer hover:text-white select-none px-1 py-0.5">-</span>
                                <span className="text-white text-xs font-medium">{gameCount}</span>
                                <span onClick={() => onGameCountChange(Math.min(player?.game_log?.length || 82, gameCount + 1))} className="text-fgSubtle text-xs cursor-pointer hover:text-white select-none px-1 py-0.5">+</span>
                            </div>
                            <Lock className="w-4 h-4 text-borderMuted" />
                        </div>
                    </div>
                </div>

                <div className="flex items-center justify-between border-b border-borderMedium/40 mt-1 pb-0">
                    {['Suggested', 'Opp Rankings', 'Splits', 'Stats'].map(t => (
                        <button
                            key={t}
                            onClick={() => setActiveTab(t)}
                            className={`pb-1 text-[11px] font-semibold tracking-wide ${t === activeTab ? 'text-[#F5F5F5] border-b-2 border-blue500' : 'text-[#A3A3A3] hover:text-[#F5F5F5] border-b-2 border-transparent transition-colors'}`}
                        >
                            {t}
                        </button>
                    ))}
                </div>

                {activeTab === 'Suggested' ? (
                    <>
                        {/* Suggested Tab Content - Pills */}
                        <div className="flex flex-wrap gap-1 mt-1">
                            {[
                                { filterId: 'Minutes', label: 'Minutes' },
                                { filterId: 'Def vs Position (PTS)', label: 'Def vs Position (PTS)' },
                                { filterId: 'H2H', label: 'H2H' },
                                { filterId: 'Def vs DPT', label: `Def vs ${dpt || 'DPT'} (DPT)`, rank: dptRank },
                                { filterId: 'Def vs DSZ', label: `Def vs ${dsz || 'DSZ'} (DSZ)`, rank: dszRank },
                            ].map((stat) =>
                                renderFilterButton({
                                    filterId: stat.filterId,
                                    label: stat.label,
                                    rank: stat.rank,
                                }),
                            )}
                            {!isSuggestedExpanded ? (
                                <button
                                    onClick={() => setIsSuggestedExpanded(true)}
                                    className="px-2 py-1.5 lg:py-1 rounded-full text-[12px] lg:text-[11px] font-medium transition-colors border bg-bgElevation2 text-[#A3A3A3] border-borderMedium/50 hover:text-white hover:bg-bgElevation3">
                                    +7 more
                                </button>
                            ) : (
                                <>
                                    {[
                                        { filterId: 'Opp Paint Pts Allowed', label: 'Opp Paint Pts Allowed', rank: paintPtsRank },
                                        { filterId: 'Opp DefRtg', label: 'Opp DefRtg' },
                                        { filterId: 'Opp Pace', label: 'Opp Pace', customRank: <span className="text-green500">#5</span> },
                                        { filterId: 'USG%', label: 'USG%' },
                                        { filterId: 'FGA', label: 'FGA' },
                                        { filterId: 'Def vs DSZ2', label: 'Def vs DSZ2', rank: dsz2Rank },
                                        { filterId: 'Def vs Pull Up', label: 'Def vs Pull Up', rank: pullupRank }
                                    ].map((stat) =>
                                        renderFilterButton({
                                            filterId: stat.filterId,
                                            label: stat.label,
                                            rank: stat.rank,
                                            customRank: stat.customRank,
                                        }),
                                    )}
                                    <button
                                        onClick={() => setIsSuggestedExpanded(false)}
                                        className="p-1.5 rounded-full text-[13px] font-medium transition-colors border bg-bgElevation2 text-[#A3A3A3] border-borderMedium/50 hover:text-white hover:bg-bgElevation3 flex items-center justify-center w-[24px] h-[24px] mt-0.5 ml-1">
                                        <ChevronUp className="w-4 h-4" />
                                    </button>
                                </>
                            )}
                        </div>

                        {/* Teammates Section (Mirrored from PropsMadness Layout) */}
                        <div className="flex flex-col gap-2 mt-1 border-t border-borderMedium/40 pt-2">
                            <div className="flex items-center justify-between">
                                <span className="text-white text-sm font-semibold">Teammates</span>
                                <button className="bg-bgElevation1 border border-borderMedium/50 text-fgSubtle text-xs font-medium px-2 py-1 rounded-md flex items-center gap-1 hover:text-white">
                                    {teamInjuryReport?.report_status === 'submitted' ? 'All' : 'No report'}
                                    <span className="font-normal">=</span>
                                </button>
                            </div>

                            <div className="flex items-center gap-2 relative overflow-x-auto no-scrollbar pb-2">
                                {teammateInjuryCards.length > 0 ? teammateInjuryCards.map((teammate) => {
                                    const statusMeta = getTeammateStatusMeta(teammate.currentStatus, teammate.reportStatus);
                                    const impactMeta = getTeammateImpactMeta(teammate.statImpact);
                                    const tooltipText = [
                                        teammate.playerName,
                                        statusMeta.label,
                                        teammate.impactSampleLabel ? `Impact ${impactMeta.label}` : null,
                                        teammate.impactSampleLabel,
                                        teammate.reason,
                                    ].filter(Boolean).join(' - ');
                                    const headshotSrc = teammate.playerId
                                        ? `https://cdn.nba.com/headshots/nba/latest/260x190/${teammate.playerId}.png`
                                        : '';
                                    const fallbackSrc = teammate.playerId
                                        ? `${ASSETS_BASE}/assets/player_headshots/${teammate.playerId}.png`
                                        : undefined;

                                    return (
                                        <div
                                            key={`${teammate.playerId ?? teammate.playerName}-${teammate.currentStatus ?? 'na'}`}
                                            className="bg-bgElevation1 border border-borderMedium/40 rounded-lg p-1.5 w-[66px] shrink-0 flex flex-col items-center relative"
                                            title={tooltipText}
                                        >
                                            <div className="relative w-8 h-8 flex-shrink-0">
                                                <div className="w-full h-full rounded-full overflow-hidden bg-bgElevation2 border border-borderMedium/40">
                                                    <ImageWithFallback
                                                        src={headshotSrc || undefined}
                                                        fallbackSrc={fallbackSrc}
                                                        fallbackComponent={
                                                            <div className="w-full h-full flex items-center justify-center bg-bgElevation2 text-[10px] font-bold text-fgSubtle">
                                                                {getInitials(teammate.playerName)}
                                                            </div>
                                                        }
                                                        alt={teammate.playerName}
                                                        className="w-full h-full object-cover transform scale-125 pt-1.5"
                                                    />
                                                </div>
                                                {statusMeta.badge ? (
                                                    <div className={`absolute -bottom-1 left-1/2 -translate-x-1/2 ${statusMeta.badgeClass} text-[8px] font-bold px-1 py-[1px] leading-tight rounded-sm border border-bgElevation1 whitespace-nowrap z-10 w-fit pointer-events-none`}>
                                                        + {statusMeta.badge}
                                                    </div>
                                                ) : null}
                                            </div>
                                            <span className="text-white text-[10px] font-semibold tracking-wide truncate w-full text-center mt-2.5">
                                                {teammate.displayName}
                                            </span>
                                            <span className={`${impactMeta.className} text-[11px] font-bold mt-0.5 truncate w-full text-center px-0.5`}>
                                                {impactMeta.label}
                                            </span>
                                        </div>
                                    );
                                }) : (
                                    <div className="text-fgSubtle text-xs py-2">
                                        No teammates available.
                                    </div>
                                )}
                            </div>
                        </div>
                    </>
                ) : activeTab === 'Opp Rankings' ? (
                    <div className="flex flex-col mt-2">
                        <div className="flex items-center gap-3 border-b border-borderMedium/40 pb-3">
                            <span className="text-[11px] font-semibold text-white uppercase tracking-wider flex items-center gap-1">Timeframe <HelpCircle className="w-3 h-3 text-borderMuted" /></span>
                            <div className="flex gap-1 ml-auto">
                                {['All', 'L5', 'L10', 'L15'].map(tf => (
                                    <button
                                        key={tf}
                                        className={`px-3 py-1 text-[11px] font-medium rounded transition-colors ${tf === 'All' ? 'bg-blue500 text-white' : 'text-fgSubtle hover:text-white bg-bgElevation1 border border-borderMedium/40'}`}
                                    >
                                        {tf}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <div className="mt-4">
                            <span className="text-[10px] font-semibold text-borderMuted uppercase tracking-wider block mb-2">Team Defense</span>
                            <div className="flex flex-wrap gap-1">
                                {[
                                    { filterId: 'Def vs Position (PTS)', label: 'Position' },
                                    { filterId: 'Opp DefRtg', label: 'DefRtg' },
                                    { filterId: 'Def vs Pull Up', label: 'Pull Up', rank: pullupRank },
                                    { filterId: 'Catch & Shoot', label: 'C&S', rank: csRank },
                                    { filterId: 'Opp Paint Pts Allowed', label: 'Paint Pts Allowed', rank: paintPtsRank },
                                    { filterId: 'Opp Pace', label: 'Pace', customRank: <span className="text-green500">#5</span> },
                                    { filterId: '3PT Att Allowed', label: '3PT Att Allowed' },
                                    { filterId: 'FT Att Allowed', label: 'FT Att Allowed' },
                                ].map((stat) =>
                                    renderFilterButton({
                                        filterId: stat.filterId,
                                        label: stat.label,
                                        rank: stat.rank,
                                        customRank: stat.customRank,
                                        className: 'px-2 py-1.5 rounded-[6px] text-[13px]',
                                    }),
                                )}
                            </div>
                        </div>

                        <div className="mt-4">
                            <span className="text-[10px] font-semibold text-borderMuted uppercase tracking-wider block mb-2">Play Types</span>
                            <div className="flex flex-wrap gap-1">
                                {['Transition', 'PnR Ball Handler', 'Isolation', 'Spot Up', 'Off Scr', 'Post Up', 'Handoff', 'PnR RM', 'Putback'].map(pt => {
                                    const ptData = player?.play_type_analysis?.find((p: any) => p.type.toLowerCase().includes(pt.toLowerCase()) || pt.toLowerCase().includes(p.type.toLowerCase()));
                                    const rank = ptData?.rank;
                                    return renderFilterButton({
                                        filterId: pt,
                                        label: pt,
                                        rank,
                                        className: 'px-2 py-1.5 rounded-[6px] text-[13px]',
                                    });
                                })}
                            </div>
                        </div>

                    </div>
                ) : activeTab === 'Splits' ? (
                    <div className="flex flex-col mt-2">
                        <div className="flex flex-wrap gap-1">
                            {['H2H', 'Home', 'Away', 'Regular', 'Playoffs', 'B2B', 'Win/Loss Margin', 'Game Total Pts', 'CL Spread', 'CL Total Pts', 'CL Pts'].map((pill) =>
                                renderFilterButton({
                                    filterId: pill,
                                    label: pill,
                                    className: 'px-4 py-2 rounded-[6px] text-[13px] tracking-wide',
                                }),
                            )}
                        </div>
                    </div>
                ) : (
                    <div className="flex flex-col mt-2 border border-borderMedium/40 rounded-lg p-3 bg-bgElevation1/50 relative overflow-hidden">
                        <div className="flex justify-between items-center mb-4">
                            <span className="text-white text-xs font-semibold">Base Status</span>
                            <div className="flex bg-bgElevation1 border border-borderMedium/40 rounded p-0.5">
                                <button className="bg-blue500 text-white rounded text-[10px] uppercase font-bold px-2 py-0.5 tracking-wider">Average</button>
                                <button className="text-borderMuted rounded text-[10px] uppercase font-bold px-2 py-0.5 tracking-wider hover:text-fgSubtle">Median</button>
                            </div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {['Minutes', 'Points', 'Assists', 'Rebounds', 'USG%', 'FG%', 'FGA', '3PA', '3P', 'FTA', 'Fouls'].map((stat) =>
                                renderFilterButton({
                                    filterId: stat,
                                    label: stat,
                                    className: 'px-2 py-1.5 lg:py-1 rounded-[6px] text-[12px] lg:text-[11px]',
                                }),
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div >
    );
};
