import React from 'react';
import { Book, Copy, Menu, TrendingUp, User } from 'lucide-react';
import { ASSETS_BASE } from '../utils/config';

interface LeagueButtonProps {
    label: string;
    isActive?: boolean;
    isDisabled?: boolean;
    logoSrc?: string;
}

export interface TopNavEdgeSummary {
    recommendationCount: number;
    changeCount: number;
    leaderLabel?: string | null;
    updatedAt?: string | null;
}

interface TopNavProps {
    onMenuClick: () => void;
    onOpenEdgeBoard?: () => void;
    isEdgeBoardOpen?: boolean;
    edgeSummary?: TopNavEdgeSummary | null;
    dashboardUpdatedAt?: string | null;
}

function formatNavTimestamp(value?: string | null) {
    if (!value) return 'Waiting';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return 'Waiting';

    return parsed.toLocaleTimeString([], {
        hour: 'numeric',
        minute: '2-digit',
    });
}

const LeagueButton = ({ label, isActive = false, isDisabled = false, logoSrc }: LeagueButtonProps) => {
    const [imageError, setImageError] = React.useState(false);

    return (
        <button
            type="button"
            disabled={isDisabled}
            className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.12em] transition-colors ${isActive
                ? 'bg-black/20 text-white'
                : isDisabled
                    ? 'cursor-not-allowed text-gray-500 opacity-50'
                    : 'text-gray-300 hover:bg-borderMedium/60 hover:text-white'
                }`}
        >
            <div className="flex h-4 w-3.5 items-center justify-center overflow-hidden rounded-[1px] bg-black/20">
                {logoSrc && !imageError ? (
                    <img
                        src={logoSrc}
                        alt={label}
                        className="h-full w-full object-contain"
                        onError={() => setImageError(true)}
                    />
                ) : (
                    <>
                        {label === 'NBA' && (
                            <div className="flex h-full w-full">
                                <div className="h-full w-1/2 bg-blue800" />
                                <div className="h-full w-1/2 bg-red700" />
                            </div>
                        )}
                        {label === 'WNBA' && <div className="h-full w-full bg-orange600" />}
                        {label === 'NFL' && (
                            <div className="relative h-full w-full bg-blue900">
                                <div className="absolute left-1/2 top-0 -translate-x-1/2 text-[4px] text-white">★</div>
                            </div>
                        )}
                    </>
                )}
            </div>
            {label}
        </button>
    );
};

export const TopNav: React.FC<TopNavProps> = ({
    onMenuClick,
    onOpenEdgeBoard,
    isEdgeBoardOpen = false,
    edgeSummary,
    dashboardUpdatedAt,
}) => {
    const BASE_URL = `${ASSETS_BASE}/assets/sport_logos`;
    return (
        <div className="relative z-50 flex h-16 shrink-0 items-center justify-between border-b border-bgElevation0 bg-bgElevation0 px-4 lg:px-6">
            <div className="hidden flex-1 items-center gap-2 lg:flex">
                <div className="flex items-center gap-1 rounded-lg bg-bgCanvas p-1">
                    <LeagueButton
                        label="NBA"
                        isActive={true}
                        logoSrc={`${BASE_URL}/nba.svg`}
                    />
                    <LeagueButton
                        label="WNBA"
                        isDisabled={true}
                        logoSrc={`${BASE_URL}/wnba-icon.png`}
                    />
                    <LeagueButton
                        label="NFL"
                        isDisabled={true}
                        logoSrc={`${BASE_URL}/nfl.svg`}
                    />
                </div>

                {onOpenEdgeBoard && (
                    <button
                        type="button"
                        onClick={onOpenEdgeBoard}
                        className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-[11px] font-bold uppercase tracking-[0.12em] transition-colors ${isEdgeBoardOpen
                            ? 'border-blue500/30 bg-blue500/10 text-white'
                            : 'border-borderMedium/70 bg-transparent text-gray-300 hover:border-blue500/30 hover:bg-blue500/10 hover:text-white'
                            }`}
                    >
                        <TrendingUp className="h-3.5 w-3.5" />
                        Today&apos;s Best Props
                    </button>
                )}
            </div>

            <button className="text-gray-400 hover:text-white lg:hidden" onClick={onMenuClick} type="button">
                <Menu className="h-6 w-6" />
            </button>

            <div className="pointer-events-none absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 select-none items-center gap-2">
                <div className="relative flex h-7 w-7 items-center justify-center drop-shadow-lg">
                    <svg viewBox="0 0 100 100" className="h-full w-full" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M 25 20 L 25 80" stroke="#3B82F6" strokeWidth="14" strokeLinecap="round" />
                        <path d="M 25 25 C 60 25 60 55 25 55" stroke="#3B82F6" strokeWidth="14" strokeLinecap="round" />
                        <path d="M 45 45 L 80 80" stroke="#10B981" strokeWidth="14" strokeLinecap="round" />
                        <path d="M 80 45 L 55 70" stroke="#10B981" strokeWidth="14" strokeLinecap="round" />
                    </svg>
                </div>
                <span className="text-[20px] font-black uppercase tracking-widest text-white">
                    Prop<span className="text-blue500">X</span>
                </span>
            </div>

            <div className="flex flex-1 items-center justify-end gap-3">
                <div className="hidden items-center gap-1.5 rounded-lg border border-borderMedium bg-bgCanvas px-3 py-2 text-[11px] text-fgSubtle xl:flex">
                    <span className="font-bold uppercase tracking-[0.12em] text-fgSubtle">Updated</span>
                    <span className="font-semibold text-white">{formatNavTimestamp(dashboardUpdatedAt ?? edgeSummary?.updatedAt)}</span>
                </div>

                {onOpenEdgeBoard && (
                    <button
                        type="button"
                        onClick={onOpenEdgeBoard}
                        className={`rounded-lg border p-2 transition-colors lg:hidden ${isEdgeBoardOpen
                            ? 'border-borderMedium bg-bgCanvas text-white'
                            : 'border-borderMedium bg-transparent text-gray-400 hover:text-white'
                            }`}
                    >
                        <TrendingUp className="h-4 w-4" />
                    </button>
                )}

                <button type="button" className="hidden p-2 text-gray-400 transition-colors hover:text-white sm:block">
                    <Book className="h-5 w-5" />
                </button>

                <button type="button" className="group relative flex items-center gap-2 rounded-lg border border-borderMedium bg-bgCanvas px-3 py-2 text-xs font-bold text-white transition-colors hover:bg-borderMedium">
                    <Copy className="h-3.5 w-3.5" />
                    <span className="hidden sm:inline">Check My Prop</span>
                    <span className="absolute -right-2 -top-2 rounded bg-green500 px-1.5 py-0.5 text-[9px] font-extrabold leading-none text-neutral950 shadow-sm">
                        NEW
                    </span>
                </button>

                <button type="button" className="flex h-9 w-9 items-center justify-center rounded-full border border-borderMedium bg-bgCanvas text-gray-400 transition-colors hover:bg-borderMedium hover:text-white">
                    <User className="h-5 w-5" />
                </button>
            </div>
        </div>
    );
};
