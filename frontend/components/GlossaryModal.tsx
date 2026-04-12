import React from 'react';
import { X } from 'lucide-react';

interface GlossaryModalProps {
    onClose: () => void;
}

const GLOSSARY_SECTIONS = [
    {
        title: 'Box Score',
        items: [
            ['PTS', 'Points'],
            ['REB', 'Rebounds'],
            ['AST', 'Assists'],
            ['STL', 'Steals'],
            ['BLK', 'Blocks'],
            ['TOV', 'Turnovers'],
            ['PF', 'Personal Fouls'],
            ['MIN', 'Minutes Played'],
            ['MINS', 'Minutes Played'],
            ['OREB', 'Offensive Rebounds'],
            ['DREB', 'Defensive Rebounds'],
            ['FAN', 'Fantasy Points'],
            ['+/-', 'Plus Minus'],
        ],
    },
    {
        title: 'Shooting',
        items: [
            ['FG', 'Field Goals Made'],
            ['FGA', 'Field Goal Attempts'],
            ['FG%', 'Field Goal Percentage'],
            ['3P', 'Three Pointers Made'],
            ['3PM', 'Three Pointers Made'],
            ['3PA', 'Three Point Attempts'],
            ['3P%', 'Three Point Percentage'],
            ['FT', 'Free Throws Made'],
            ['FTA', 'Free Throw Attempts'],
            ['FT%', 'Free Throw Percentage'],
            ['C&S', 'Catch and Shoot'],
            ['Pull Up', 'Shot taken off the dribble'],
        ],
    },
    {
        title: 'Combo Props',
        items: [
            ['PRA', 'Points + Rebounds + Assists'],
            ['PR', 'Points + Rebounds'],
            ['PA', 'Points + Assists'],
            ['RA', 'Rebounds + Assists'],
            ['STL+BLK', 'Steals + Blocks'],
            ['DD', 'Double Double'],
            ['TD', 'Triple Double'],
        ],
    },
    {
        title: 'Dashboard',
        items: [
            ['USG%', 'Usage Percentage'],
            ['USAGE', 'Usage Percentage'],
            ['B2B', 'Back to Back'],
            ['L5', 'Last 5 Games'],
            ['L10', 'Last 10 Games'],
            ['L15', 'Last 15 Games'],
            ['Opp', 'Opponent'],
            ['DefRtg', 'Defensive Rating'],
            ['Pace', 'Estimated possessions per game'],
            ['1Q', 'First Quarter'],
            ['1H', 'First Half'],
            ['DSZ', 'Dominant Shot Zone'],
            ['DSZ2', 'Second Dominant Shot Zone'],
            ['DPT', 'Dominant Play Type'],
            ['POT AST', 'Potential Assists'],
            ['REB Chances', 'Rebound Chances'],
            ['Drives', 'Drives to the basket'],
            ['DNP', 'Did Not Play'],
            ['GTD', 'Game Time Decision'],
            ['Ques', 'Questionable'],
            ['Prob', 'Probable'],
            ['Out', 'Not expected to play'],
            ['Live', 'Game is in progress'],
            ['Final', 'Game is complete'],
        ],
    },
    {
        title: 'Lines',
        items: [
            ['Line', 'Sportsbook stat total'],
            ['O', 'Over'],
            ['U', 'Under'],
            ['Juice', 'The price or commission built into odds'],
            ['Vig', 'Sportsbook commission'],
            ['Hit Rate', 'How often the player cleared the selected line'],
            ['Open', 'First captured line or odds'],
            ['Now', 'Most recent captured line or odds'],
        ],
    },
    {
        title: 'Sportsbooks',
        items: [
            ['DK', 'DraftKings'],
            ['FD', 'FanDuel'],
            ['PP', 'PrizePicks'],
            ['MGM', 'BetMGM'],
            ['CZ', 'Caesars'],
        ],
    },
];

export const GlossaryModal: React.FC<GlossaryModalProps> = ({ onClose }) => {
    return (
        <div
            className="fixed inset-0 z-[200] flex items-center justify-center"
            style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)' }}
            onClick={onClose}
        >
            <div
                className="relative mx-4 flex max-h-[85vh] w-full max-w-[620px] flex-col rounded-2xl border border-borderMedium/60 bg-bgElevation1 shadow-2xl"
                onClick={(event) => event.stopPropagation()}
            >
                <div className="flex shrink-0 items-center justify-between border-b border-white/8 px-6 pb-4 pt-5">
                    <div>
                        <h1 className="text-[17px] font-bold tracking-tight text-white">Abbreviations</h1>
                        <p className="mt-1 text-[12px] text-neutral400">
                            Quick translations for common stats, filters, and sportsbook terms.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label="Close abbreviations"
                        className="rounded-lg p-1 text-fgSubtle transition-colors hover:bg-white/10 hover:text-white"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>

                <div className="overflow-y-auto px-6 py-5 text-[13px] text-neutral400">
                    {GLOSSARY_SECTIONS.map((section) => (
                        <section key={section.title} className="mb-7 last:mb-0">
                            <h2 className="mb-3 border-b border-white/10 pb-2 text-[15px] font-bold text-white">
                                {section.title}
                            </h2>
                            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                {section.items.map(([term, definition]) => (
                                    <div
                                        key={`${section.title}-${term}`}
                                        className="grid grid-cols-[72px_1fr] items-start gap-3 rounded-lg border border-borderMedium/40 bg-bgElevation2 px-3 py-2.5"
                                    >
                                        <span className="font-bold text-white">{term}</span>
                                        <span className="leading-snug text-neutral300">{definition}</span>
                                    </div>
                                ))}
                            </div>
                        </section>
                    ))}
                </div>

                <div className="flex shrink-0 justify-end border-t border-white/8 px-6 py-3">
                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-lg border border-borderMedium bg-bgElevation2 px-4 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-bgElevation0"
                    >
                        Got it
                    </button>
                </div>
            </div>
        </div>
    );
};
