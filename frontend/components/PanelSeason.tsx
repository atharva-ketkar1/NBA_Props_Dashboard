import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import {
    hasEnoughPanelGames,
    PANEL_MIN_GAMES,
    PANEL_SEASON_OPTIONS,
    PRIOR_PANEL_SEASON,
} from '../constants';

interface PanelSeasonSelectProps {
    season: string;
    onSeasonChange: (season: string) => void;
    /** Caption classes, so each panel keeps the type scale it already used. */
    className?: string;
}

/**
 * The season picker that sits where each panel used to print a fixed caption.
 *
 * These four panels -- shooting zones, assist zones, shot types, play types --
 * are season aggregates, not game logs, so they do not follow the global season
 * filter: that filter swaps a player's game log, and an aggregate has no games
 * to swap. Each panel carries its own season instead, which is also what makes
 * the early-season case readable: last season's shot profile against the team
 * they play tomorrow is a sensible thing to look at, and the chart above it
 * staying on this season is not a contradiction.
 */
export const PanelSeasonSelect: React.FC<PanelSeasonSelectProps> = ({
    season,
    onSeasonChange,
    className = '',
}) => {
    const [isOpen, setIsOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!isOpen) {
            return;
        }

        const handleClickOutside = (event: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        };

        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [isOpen]);

    return (
        <div className="relative inline-block" ref={containerRef}>
            <button
                type="button"
                onClick={() => setIsOpen((open) => !open)}
                className={`flex items-center gap-1 hover:text-fgDefault transition-colors ${className}`}
                aria-haspopup="listbox"
                aria-expanded={isOpen}
            >
                <span>{season} Season</span>
                <ChevronDown className={`w-3.5 h-3.5 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
            </button>

            {isOpen && (
                <div
                    role="listbox"
                    className="absolute left-0 top-full mt-1 z-50 min-w-[140px] bg-bgElevation2 border border-borderMedium rounded-btn shadow-2xl overflow-hidden py-1"
                >
                    {PANEL_SEASON_OPTIONS.map((option) => (
                        <button
                            key={option}
                            type="button"
                            role="option"
                            aria-selected={option === season}
                            onClick={() => {
                                onSeasonChange(option);
                                setIsOpen(false);
                            }}
                            className={`w-full text-left px-3 py-1.5 text-[12px] font-medium transition-colors ${option === season
                                ? 'text-textGeneralPrimary bg-bgElevation3'
                                : 'text-textGeneralSecondary hover:bg-bgElevation3/60'
                                }`}
                        >
                            {option} Season
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
};

interface PanelNoDataProps {
    /** What is missing, in the panel's own words: "No shot-type data for this season". */
    message?: string;
    /** One quieter line of justification, e.g. "4 games played". */
    detail?: string;
    /** The season the panel is currently on, so the offer is only made when it helps. */
    season: string;
    onSeasonChange: (season: string) => void;
    /** Panels reserve different heights; the empty state fills whatever it is given. */
    className?: string;
}

/**
 * The empty state, with a one-click way out of it.
 *
 * A panel with nothing in it is a dead end in October, when every season
 * aggregate is either missing or built on two games. Offering last season turns
 * it into a choice, and the button is the whole point -- the season dropdown
 * above is easy to miss when you are looking at an empty panel.
 *
 * It also stands in for a sample too thin to read, not just a missing one. A
 * distribution built on one game is a worse thing to show than nothing: it
 * looks like a profile, it is drawn in the same colours as a profile, and
 * nothing on the panel says it rests on nine shots. See PANEL_MIN_GAMES.
 */
export const PanelNoData: React.FC<PanelNoDataProps> = ({
    message = 'No data for this season',
    detail,
    season,
    onSeasonChange,
    className = '',
}) => {
    const canOfferPriorSeason = season !== PRIOR_PANEL_SEASON;

    // gap-lg is 12px in this project's spacing scale -- the same 12px the
    // template calls `md`. Matching the number, not the name.
    return (
        <div className={`flex flex-col items-center justify-center gap-lg text-center ${className}`}>
            <div className="flex flex-col gap-1">
                <span className="text-sm font-medium leading-[21px] text-textGeneralSecondary">
                    {message}
                </span>
                {detail && (
                    <span className="text-[11px] leading-tight text-fgSubtle">
                        {detail}
                    </span>
                )}
            </div>
            {canOfferPriorSeason && (
                <button
                    type="button"
                    onClick={() => onSeasonChange(PRIOR_PANEL_SEASON)}
                    className="flex items-center justify-center h-7 px-2 rounded-btn bg-bgElevation3 hover:bg-borderMuted text-[12px] font-medium leading-[18px] text-textGeneralPrimary transition-colors"
                >
                    View {PRIOR_PANEL_SEASON} season
                </button>
            )}
        </div>
    );
};

/**
 * The message and sub-line for a panel that cannot draw its splits.
 *
 * Centralised because these are four genuinely different claims and it would be
 * easy to collapse them into one vague line in four places:
 *
 *   - the request failed            -> say so; do NOT claim the season is empty
 *   - the player has no such season -> a rookie, not a gap in our data
 *   - the season has too few games  -> owes the reader the count
 *   - there is simply nothing       -> the plain empty state
 */
export function describePanelGap({
    gamesPlayed,
    noDataMessage = 'No data for this season',
    season,
    isLiveSeason = true,
    hasError = false,
    seasonHasNoRecord = false,
}: {
    gamesPlayed?: number | null;
    noDataMessage?: string;
    season?: string;
    isLiveSeason?: boolean;
    hasError?: boolean;
    /** The request succeeded and the player has no row for that season at all. */
    seasonHasNoRecord?: boolean;
}): { message: string; detail?: string } {
    if (hasError) {
        return {
            message: `Couldn't load ${season ?? 'that season'}`,
            detail: 'The request failed. This is not the same as the season being empty.',
        };
    }

    if (!isLiveSeason && seasonHasNoRecord) {
        return {
            message: `No ${season ?? 'prior'} season on record`,
            detail: 'Rookies and players who sat out a full season have nothing to show here.',
        };
    }

    const games = Number(gamesPlayed);

    // No games, or enough games and still nothing: both are "no data". The
    // second case is the one worth guarding -- a player with a full season and
    // a missing aggregate must not be told they need ten more games.
    if (!Number.isFinite(games) || games <= 0 || hasEnoughPanelGames(games)) {
        return { message: noDataMessage };
    }

    return {
        message: 'Not enough data for this season',
        detail: `${games} game${games === 1 ? '' : 's'} played, ${PANEL_MIN_GAMES} needed`,
    };
}
