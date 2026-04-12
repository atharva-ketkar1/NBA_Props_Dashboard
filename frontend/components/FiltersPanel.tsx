import React from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, ChevronUp, HelpCircle, LoaderCircle, Lock, Minus, Plus, X } from 'lucide-react';
import { ImageWithFallback } from './ui/ImageWithFallback';
import { ASSETS_BASE } from '../utils/config';
import { TEAM_IDS } from '../constants';
import { isOverlayFilterSupported } from '../utils/filterOverlays';
import {
    ActiveTeammateFilter,
    Player,
    TeamInjuryReport,
    TeammateFilterMode,
    TeammateInjuryCard,
} from '../types';

interface FiltersPanelProps {
    isOpen: boolean;
    onClose: () => void;
    activeFilter: string | null;
    onFilterChange: (filter: string | null) => void;
    player?: Player | null;
    teammateInjuryCards?: TeammateInjuryCard[];
    teamInjuryReport?: TeamInjuryReport | null;
    selectedTeammateFilter?: ActiveTeammateFilter | null;
    teammateStatLabel?: string;
    onPreviewTeammateToggle?: (teammate: TeammateInjuryCard) => void;
    onTeammateModeSelect?: (teammate: TeammateInjuryCard, mode: TeammateFilterMode) => void;
    gameCount: number;
    onGameCountChange: (count: number) => void;
    activeSeason?: string;
    onSeasonChange?: (s: string) => void;
}

const TEAMMATE_PREVIEW_LIMIT = 10;

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

function getTeammateSelectionKey(playerId: number | null, playerName: string) {
    return playerId !== null
        ? `id:${playerId}`
        : `name:${String(playerName ?? '').trim().toLowerCase()}`;
}

function teammateCardMatchesSelection(
    teammate: TeammateInjuryCard,
    selection?: ActiveTeammateFilter | null,
) {
    if (!selection) {
        return false;
    }

    return getTeammateSelectionKey(teammate.playerId, teammate.playerName)
        === getTeammateSelectionKey(selection.playerId, selection.playerName);
}

function formatOneDecimalValue(value?: number | null) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return '0.0';
    }

    return Number(value).toFixed(1);
}

function getPreviewTeammateStatusPriority(currentStatus?: string | null) {
    const cleanStatus = String(currentStatus ?? '').trim();
    if (cleanStatus === 'Out') return 0;
    if (cleanStatus === 'Doubtful') return 1;
    if (cleanStatus === 'Questionable') return 2;
    return 3;
}

function getTeammateModalViewportSize() {
    if (typeof window === 'undefined') {
        return { width: 1280, height: 900 };
    }

    return {
        width: Math.max(360, window.innerWidth || 1280),
        height: Math.max(480, window.innerHeight || 900),
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
    selectedTeammateFilter = null,
    teammateStatLabel = 'PTS',
    onPreviewTeammateToggle,
    onTeammateModeSelect,
    gameCount,
    onGameCountChange,
    activeSeason,
    onSeasonChange,
}) => {
    const [activeTab, setActiveTab] = React.useState('Suggested');
    const [isSuggestedExpanded, setIsSuggestedExpanded] = React.useState(false);
    const [isTeammateModalOpen, setIsTeammateModalOpen] = React.useState(false);
    const [teammateModalViewport, setTeammateModalViewport] = React.useState(getTeammateModalViewportSize);
    const teammatePreviewCards = React.useMemo(() => {
        const prominentCards = teammateInjuryCards.slice(0, TEAMMATE_PREVIEW_LIMIT);
        return [...prominentCards].sort((left, right) => {
            const leftStatusRank = getPreviewTeammateStatusPriority(left.currentStatus);
            const rightStatusRank = getPreviewTeammateStatusPriority(right.currentStatus);
            if (leftStatusRank !== rightStatusRank) {
                return leftStatusRank - rightStatusRank;
            }

            if (right.prominenceScore !== left.prominenceScore) {
                return right.prominenceScore - left.prominenceScore;
            }

            return left.playerName.localeCompare(right.playerName);
        });
    }, [teammateInjuryCards]);
    const teamLogoUrl = React.useMemo(() => {
        const teamTricode = String(player?.team ?? teamInjuryReport?.team_tricode ?? '').trim().toUpperCase();
        if (!teamTricode) {
            return null;
        }

        const teamId = TEAM_IDS[teamTricode];
        if (teamId) {
            return `${ASSETS_BASE}/assets/team_logos/${teamId}.svg`;
        }

        return `${ASSETS_BASE}/assets/team_logos/${teamTricode}.svg`;
    }, [player?.team, teamInjuryReport?.team_tricode]);

    React.useEffect(() => {
        if (!isOpen) {
            setIsTeammateModalOpen(false);
        }
    }, [isOpen]);

    React.useEffect(() => {
        if (!isTeammateModalOpen) {
            return undefined;
        }

        setTeammateModalViewport(getTeammateModalViewportSize());

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                setIsTeammateModalOpen(false);
            }
        };
        const handleResize = () => {
            setTeammateModalViewport(getTeammateModalViewportSize());
        };

        document.addEventListener('keydown', handleKeyDown);
        window.addEventListener('resize', handleResize);

        return () => {
            document.removeEventListener('keydown', handleKeyDown);
            window.removeEventListener('resize', handleResize);
        };
    }, [isTeammateModalOpen]);

    // Must be above the early return to comply with React rules of hooks
    const teammateModalStyle = React.useMemo<React.CSSProperties>(() => {
        const viewportWidth = teammateModalViewport.width;
        const viewportHeight = teammateModalViewport.height;
        const viewportAspect = viewportWidth / viewportHeight;
        const modalHeight = Math.min(viewportHeight * 0.82, 820);
        const modalAspect = Math.min(Math.max(viewportAspect * 0.82, 0.76), 0.98);
        const modalWidth = Math.min(
            viewportWidth * 0.94,
            modalHeight * modalAspect,
            780,
        );

        return {
            width: `${Math.round(modalWidth)}px`,
            maxHeight: `${Math.round(modalHeight)}px`,
        };
    }, [teammateModalViewport.height, teammateModalViewport.width]);

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
        className = 'px-2.5 py-1.5 rounded-[6px] text-[12px]',
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
                className={`${className} font-medium flex items-center gap-1.5 transition-colors border ${isActive
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

    const renderTeammateBadge = (teammate: TeammateInjuryCard) => {
        const statusMeta = getTeammateStatusMeta(teammate.currentStatus, teammate.reportStatus);
        if (!statusMeta.badge) {
            return null;
        }

        return (
            <div className={`absolute -bottom-1 left-1/2 -translate-x-1/2 ${statusMeta.badgeClass} text-[8px] font-bold px-1 py-[1px] leading-tight rounded-sm border border-bgElevation1 whitespace-nowrap z-10 w-fit pointer-events-none`}>
                + {statusMeta.badge}
            </div>
        );
    };

    const renderTeammateAvatar = (teammate: TeammateInjuryCard, sizeClass = 'w-8 h-8') => {
        const headshotSrc = teammate.playerId
            ? `https://cdn.nba.com/headshots/nba/latest/260x190/${teammate.playerId}.png`
            : '';
        const fallbackSrc = teammate.playerId
            ? `${ASSETS_BASE}/assets/player_headshots/${teammate.playerId}.png`
            : undefined;

        return (
            <div className={`relative ${sizeClass} flex-shrink-0`}>
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
                {renderTeammateBadge(teammate)}
            </div>
        );
    };

    const renderPreviewTeammateCard = (teammate: TeammateInjuryCard) => {
        const isSelected = teammateCardMatchesSelection(teammate, selectedTeammateFilter);
        const isWithMode = selectedTeammateFilter?.mode === 'with';
        const isImpactLoading = teammate.isImpactLoading && !isSelected;
        const impactMeta = getTeammateImpactMeta(teammate.statImpact);
        const statusMeta = getTeammateStatusMeta(teammate.currentStatus, teammate.reportStatus);
        const cardStateLabel = isSelected
            ? (isWithMode ? 'WITH' : 'W/O')
            : impactMeta.label;
        const cardStateClass = isSelected
            ? (isWithMode ? 'text-green500' : 'text-red500')
            : impactMeta.className;
        const cardShellClass = isSelected
            ? (isWithMode
                ? 'bg-[#071A10] border-green500/70 shadow-[0_0_0_1px_rgba(34,197,94,0.12)]'
                : 'bg-[#1B0B0D] border-red500/70 shadow-[0_0_0_1px_rgba(239,68,68,0.12)]')
            : 'bg-bgElevation1 border-borderMedium/40 hover:border-borderMedium/70 hover:bg-bgElevation2';
        const tooltipText = [
            teammate.playerName,
            statusMeta.label,
            isSelected ? `Filter ${cardStateLabel}` : `Impact ${impactMeta.label}`,
            teammate.impactSampleLabel,
            teammate.reason,
        ].filter(Boolean).join(' - ');

        return (
            <button
                key={`${teammate.playerId ?? teammate.playerName}-${teammate.currentStatus ?? 'na'}`}
                type="button"
                onClick={() => onPreviewTeammateToggle?.(teammate)}
                aria-label={`Toggle teammate filter for ${teammate.playerName}`}
                className={`${cardShellClass} border rounded-xl p-2 w-[76px] shrink-0 flex flex-col items-center relative transition-colors`}
                title={tooltipText}
            >
                {renderTeammateAvatar(teammate, 'w-11 h-11')}
                <span className="text-white text-[11px] font-semibold tracking-wide truncate w-full text-center mt-2">
                    {teammate.displayName}
                </span>
                {isImpactLoading ? (
                    <LoaderCircle
                        className="mt-1 h-3 w-3 animate-spin text-fgSubtle"
                        aria-label={`Loading ${teammate.playerName} impact`}
                    />
                ) : (
                    <span className={`${cardStateClass} text-[12px] font-bold mt-0.5 truncate w-full text-center px-0.5`}>
                        {cardStateLabel}
                    </span>
                )}
            </button>
        );
    };

    // teammateModalStyle is defined above the early return (rules of hooks)

    const teammateModal = (isTeammateModalOpen && typeof document !== 'undefined')
        ? createPortal(
            <div
                className="fixed inset-0 z-[220] flex items-center justify-center"
                style={{ background: 'rgba(0,0,0,0.78)', backdropFilter: 'blur(4px)' }}
                onClick={() => setIsTeammateModalOpen(false)}
            >
                <div
                    className="mx-3 flex w-full flex-col overflow-hidden rounded-xl border border-borderMedium/50 bg-[#0B0B0B] shadow-2xl"
                    style={teammateModalStyle}
                    onClick={(event) => event.stopPropagation()}
                >
                    <div className="flex shrink-0 items-center justify-between border-b border-borderMedium/40 px-5 py-4">
                        <h3 className="text-[18px] font-semibold tracking-[-0.03em] text-white sm:text-[22px]">
                            Filter by Teammate
                        </h3>
                        <button
                            type="button"
                            onClick={() => setIsTeammateModalOpen(false)}
                            aria-label="Close teammate filter modal"
                            className="text-[#575757] transition-colors hover:text-white"
                        >
                            <X className="w-8 h-8" strokeWidth={2.2} />
                        </button>
                    </div>

                    <div className="shrink-0 border-b border-borderMedium/40 bg-bgElevation1/20 px-4 py-4">
                        <div className="flex items-center justify-between rounded-[16px] border border-[#37586A] bg-[#0E1113] px-4 py-3">
                            <div className="flex min-w-0 items-center gap-3">
                                <span className="shrink-0 text-[14px] font-medium tracking-[-0.04em] text-[#8D8D8D] sm:text-[18px]">
                                    {activeSeason ?? '25/26'}
                                </span>
                                <span className="truncate text-[14px] font-semibold tracking-[-0.04em] text-white sm:text-[18px]">
                                    {teamInjuryReport?.team_name ?? player?.team ?? 'Team'}
                                </span>
                                {teamLogoUrl ? (
                                    <img
                                        src={teamLogoUrl}
                                        alt={teamInjuryReport?.team_name ?? player?.team ?? 'Team'}
                                        className="h-7 w-7 shrink-0 object-contain"
                                    />
                                ) : null}
                            </div>
                            <ChevronDown className="h-6 w-6 shrink-0 text-[#575757]" />
                        </div>
                    </div>

                    <div className="grid shrink-0 grid-cols-[88px_minmax(0,1fr)_48px_56px_56px] border-b border-borderMedium/40 bg-bgElevation1/40 px-4 py-3 text-[12px] font-semibold tracking-[-0.04em] text-[#8D8D8D] sm:grid-cols-[132px_minmax(0,1fr)_72px_72px_72px] sm:text-[15px]">
                        <div className="flex items-center gap-5 pl-1 sm:gap-8 sm:pl-2">
                            <span>W</span>
                            <span>W/O</span>
                        </div>
                        <div>PLAYER</div>
                        <div className="text-center">POS</div>
                        <div className="text-center">MIN</div>
                        <div className="text-right pr-4">{teammateStatLabel}</div>
                    </div>

                    <div className="min-h-0 flex-1 overflow-y-auto">
                        {teammateInjuryCards.map((teammate) => {
                            const isSelected = teammateCardMatchesSelection(teammate, selectedTeammateFilter);
                            const isWithSelected = isSelected && selectedTeammateFilter?.mode === 'with';
                            const isWithoutSelected = isSelected && selectedTeammateFilter?.mode === 'without';
                            const withButtonClass = isWithSelected
                                ? 'border-green500/80 bg-[#071A10] text-green500'
                                : 'border-borderMedium/60 bg-transparent text-[#575757] hover:border-green500/70 hover:text-green500';
                            const withoutButtonClass = isWithoutSelected
                                ? 'border-red500/80 bg-[#1B0B0D] text-red500'
                                : 'border-borderMedium/60 bg-transparent text-[#575757] hover:border-red500/70 hover:text-red500';

                            return (
                                <div
                                    key={`teammate-modal-${teammate.playerId ?? teammate.playerName}`}
                                    className="grid grid-cols-[88px_minmax(0,1fr)_48px_56px_56px] items-center px-4 py-3 sm:grid-cols-[132px_minmax(0,1fr)_72px_72px_72px]"
                                >
                                    <div className="flex items-center gap-2">
                                        <button
                                            type="button"
                                            onClick={() => onTeammateModeSelect?.(teammate, 'with')}
                                            aria-label={`Show games with ${teammate.playerName}`}
                                            className={`${withButtonClass} flex h-8 w-10 items-center justify-center rounded-[6px] border transition-colors sm:h-10 sm:w-[60px]`}
                                        >
                                            <Plus className="h-4 w-4 sm:h-5 sm:w-5" strokeWidth={2.4} />
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => onTeammateModeSelect?.(teammate, 'without')}
                                            aria-label={`Show games without ${teammate.playerName}`}
                                            className={`${withoutButtonClass} flex h-8 w-10 items-center justify-center rounded-[6px] border transition-colors sm:h-10 sm:w-[60px]`}
                                        >
                                            <Minus className="h-4 w-4 sm:h-5 sm:w-5" strokeWidth={2.4} />
                                        </button>
                                    </div>

                                    <div className="flex min-w-0 items-center gap-3 sm:gap-4">
                                        {renderTeammateAvatar(teammate, 'w-10 h-10 sm:w-14 sm:h-14')}
                                        <div className="min-w-0">
                                            <div className="truncate text-[14px] font-semibold tracking-[-0.04em] text-white sm:text-[16px]">
                                                {teammate.displayName}
                                            </div>
                                        </div>
                                    </div>

                                    <div className="text-center text-[14px] font-semibold tracking-[-0.04em] text-[#C8C8C8] sm:text-[15px]">
                                        {teammate.position ?? '-'}
                                    </div>
                                    <div className="text-center text-[14px] font-semibold tracking-[-0.04em] text-[#C8C8C8] sm:text-[15px]">
                                        {formatOneDecimalValue(teammate.minutesPerGame)}
                                    </div>
                                    <div className="pr-1 text-right text-[14px] font-semibold tracking-[-0.04em] text-[#C8C8C8] sm:pr-4 sm:text-[15px]">
                                        {formatOneDecimalValue(teammate.statPerGame)}
                                    </div>
                                </div>
                            );
                        })}

                        {!teammateInjuryCards.length ? (
                            <div className="px-5 py-10 text-center text-sm text-fgSubtle">
                                No teammates available.
                            </div>
                        ) : null}
                    </div>
                </div>
            </div>,
            document.body,
        )
        : null;

    return (
        <>
        <div className="w-[320px] bg-bgElevation0 border-l border-borderMedium/40 flex flex-col h-full shrink-0 shadow-[-10px_0_30px_rgba(0,0,0,0.5)] z-40">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-borderMedium/40 shrink-0">
                <div className="flex items-center gap-2">
                    <h2 className="text-white font-semibold text-[15px]">Filters</h2>
                    <HelpCircle className="w-4 h-4 text-borderMuted" />
                </div>
                <button
                    type="button"
                    onClick={onClose}
                    aria-label="Close filters panel"
                    className="text-borderMuted hover:text-white transition-colors"
                >
                    <X className="w-5 h-5" />
                </button>
            </div>

            <div className="flex-1 overflow-y-auto">
            <div className="px-4 pt-3 pb-3 flex flex-col gap-3">
                {/* Season & Games Toggles */}
                <div className="flex flex-col gap-2.5">
                    <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold text-fgSubtle uppercase tracking-wider w-[56px]">Season</span>
                        <div className="flex bg-bgElevation1 rounded-md border border-borderMedium/40 p-0.5 flex-1 ml-2">
                            {['23/24', '24/25', '25/26', 'All'].map(s => {
                                const isActive = s === (activeSeason || '25/26');
                                const isClickable = s === '24/25' || s === '25/26';
                                return (
                                    <button
                                        key={s}
                                        onClick={() => isClickable && onSeasonChange && onSeasonChange(s)}
                                        className={`flex-1 py-1.5 text-xs font-medium rounded transition-colors ${isActive ? 'bg-blue500 text-white shadow-sm' : isClickable ? 'text-fgSubtle hover:text-white cursor-pointer' : 'text-fgSubtle/30 cursor-not-allowed'}`}
                                    >
                                        {s}
                                    </button>
                                );
                            })}
                        </div>
                    </div>

                    <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold text-fgSubtle uppercase tracking-wider w-[56px]">Games</span>
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
                                            className={`flex-1 py-1.5 text-xs font-medium rounded transition-colors ${isActive ? 'bg-blue500 text-white shadow-sm' : 'text-fgSubtle hover:text-white'}`}
                                        >
                                            {g}
                                        </button>
                                    );
                                })}
                            </div>
                            <div className="flex items-center justify-between bg-bgElevation2 border border-borderMedium/50 rounded-md px-2 py-1.5 shrink-0 w-[72px]">
                                <span onClick={() => onGameCountChange(Math.max(1, gameCount - 1))} className="text-fgSubtle text-xs cursor-pointer hover:text-white select-none px-1">-</span>
                                <span className="text-white text-xs font-medium">{gameCount}</span>
                                <span onClick={() => onGameCountChange(Math.min(player?.game_log?.length || 82, gameCount + 1))} className="text-fgSubtle text-xs cursor-pointer hover:text-white select-none px-1">+</span>
                            </div>
                            <Lock className="w-4 h-4 text-borderMuted" />
                        </div>
                    </div>
                </div>

                <div className="flex items-center justify-between border-b border-borderMedium/40 pb-0 gap-1">
                    {['Suggested', 'Opp Rankings', 'Splits', 'Stats'].map(t => (
                        <button
                            key={t}
                            onClick={() => setActiveTab(t)}
                            className={`pb-2 text-[11px] font-semibold tracking-wide ${t === activeTab ? 'text-[#F5F5F5] border-b-2 border-blue500' : 'text-[#A3A3A3] hover:text-[#F5F5F5] border-b-2 border-transparent transition-colors'}`}
                        >
                            {t}
                        </button>
                    ))}
                </div>

                {activeTab === 'Suggested' ? (
                    <>
                        {/* Suggested Tab Content - Pills */}
                        <div className="flex flex-wrap gap-1.5 mt-1.5">
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
                                    className="px-2.5 py-1.5 rounded-full text-[12px] font-medium transition-colors border bg-bgElevation2 text-[#A3A3A3] border-borderMedium/50 hover:text-white hover:bg-bgElevation3">
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
                                        className="p-1.5 rounded-full text-[13px] font-medium transition-colors border bg-bgElevation2 text-[#A3A3A3] border-borderMedium/50 hover:text-white hover:bg-bgElevation3 flex items-center justify-center w-6 h-6 ml-0.5">
                                        <ChevronUp className="w-4 h-4" />
                                    </button>
                                </>
                            )}
                        </div>

                        {/* Teammates Section */}
                        <div className="flex flex-col gap-3 mt-2 border-t border-borderMedium/40 pt-4">
                            <div className="flex items-center justify-between">
                                <span className="text-white text-[14px] font-semibold tracking-[-0.01em]">Teammates</span>
                                <button
                                    type="button"
                                    onClick={() => teammateInjuryCards.length > 0 && setIsTeammateModalOpen(true)}
                                    disabled={teammateInjuryCards.length === 0}
                                    aria-label="Open all teammates"
                                    className={`bg-bgElevation1 border border-borderMedium/50 text-xs font-medium px-2.5 py-1 rounded-md flex items-center gap-1 transition-colors ${teammateInjuryCards.length > 0
                                        ? 'text-fgSubtle hover:text-white'
                                        : 'text-fgSubtle/40 cursor-not-allowed'
                                        }`}
                                >
                                    All
                                    <span className="font-normal">=</span>
                                </button>
                            </div>

                            <div className="flex items-center gap-2.5 relative overflow-x-auto no-scrollbar pb-3">
                                {teammatePreviewCards.length > 0 ? teammatePreviewCards.map((teammate) => (
                                    renderPreviewTeammateCard(teammate)
                                )) : (
                                    <div className="text-fgSubtle text-xs py-4">
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
            </div>
        </div>
        {teammateModal}
        </>
    );
};
