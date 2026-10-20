import React from 'react';
import { LIVE_SEASON, isArchiveSeason } from '../constants';

interface SeasonScopeNoteProps {
    /** The season the user has selected, which may not be the one shown. */
    activeSeason?: string;
    /** Caption classes, so each panel keeps the type scale it already used. */
    className?: string;
}

/**
 * Caption for the panels whose numbers only ever come from the live season.
 *
 * Shooting zones, assist zones, shot types and play types are season aggregates
 * read straight off the live `players` row. Selecting an archive season swaps the
 * game log and its derived averages, but these four cannot follow -- the
 * aggregates are overwritten in place each season, so there is nothing archived
 * to swap to.
 *
 * These panels used to print a bare "{LIVE_SEASON} Season" while sitting inside a
 * past-season view. Literally true, and still misleading: it reads as a heading
 * for the season you picked. Saying plainly that the split is not archived is the
 * difference between a panel the reader can trust and one they have to guess at.
 */
export const SeasonScopeNote: React.FC<SeasonScopeNoteProps> = ({ activeSeason, className = '' }) => {
    if (!isArchiveSeason(activeSeason)) {
        return <div className={className}>{LIVE_SEASON} Season</div>;
    }

    return (
        <div className="flex flex-col gap-0.5">
            <div className={className}>{LIVE_SEASON} Season</div>
            <div className="text-[10px] leading-tight text-fgSubtle/60">
                Not archived for {activeSeason}
            </div>
        </div>
    );
};
