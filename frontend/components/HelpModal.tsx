import React from 'react';
import { X, TrendingUp, TrendingDown } from 'lucide-react';

interface HelpModalProps {
    onClose: () => void;
}

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div className="mb-8">
        <h2 className="text-white font-bold text-[15px] mb-3 pb-2 border-b border-white/10">{title}</h2>
        {children}
    </div>
);

const ExampleBox = ({ children }: { children: React.ReactNode }) => (
    <div className="bg-bgElevation2 border border-borderMedium/40 rounded-lg px-4 py-3 my-3 text-[12px] text-neutral300 leading-relaxed">
        {children}
    </div>
);

const Label = ({ color = 'text-neutral300', children }: { color?: string; children: React.ReactNode }) => (
    <span className={`font-bold ${color}`}>{children}</span>
);

export const HelpModal: React.FC<HelpModalProps> = ({ onClose }) => {
    return (
        /* Backdrop */
        <div
            className="fixed inset-0 z-[200] flex items-center justify-center"
            style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)' }}
            onClick={onClose}
        >
            {/* Modal */}
            <div
                className="relative bg-bgElevation1 border border-borderMedium/60 rounded-2xl shadow-2xl w-full max-w-[560px] mx-4 max-h-[85vh] flex flex-col"
                onClick={e => e.stopPropagation()}
            >
                {/* Header */}
                <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-white/8 shrink-0">
                    <h1 className="text-white font-bold text-[17px] tracking-tight">Understanding the Dashboard</h1>
                    <button
                        onClick={onClose}
                        className="text-fgSubtle hover:text-white transition-colors p-1 rounded-lg hover:bg-white/10"
                    >
                        <X className="w-4 h-4" />
                    </button>
                </div>

                {/* Scrollable body */}
                <div className="overflow-y-auto px-6 py-5 text-[13px] text-neutral400 leading-relaxed">

                    {/* Hit Rate */}
                    <Section title="Hit Rate">
                        <p>
                            The hit rate tells you how often this player <Label color="text-green500">beat the line</Label> in recent games.
                            A line (e.g. <Label>21.5 Points</Label>) is the number the sportsbook sets — you're betting whether the player goes
                            over or under it.
                        </p>
                        <ExampleBox>
                            <div className="text-center mb-2">
                                <div className="text-[10px] text-fgSubtle uppercase tracking-widest mb-1">HIT RATE</div>
                                <div className="text-green500 font-bold text-[20px]">55.2% <span className="text-white/80">(16/29)</span></div>
                                <div className="text-fgSubtle text-[11px]">29 of 59 games shown</div>
                            </div>
                            <div className="text-neutral300 mt-1">
                                The player <Label color="text-green500">beat</Label> the line in 16 of the last 29 games shown on the chart.
                                <br />
                                <Label color="text-green500">Green = over 50%</Label> (more hits than misses) &nbsp;·&nbsp; <Label color="text-red500">Red = under 50%</Label>
                            </div>
                        </ExampleBox>
                        <p className="text-[12px]">
                            ⚠️ Hit rate changes based on what games are showing. If the Filters panel is open and limiting to 10 games,
                            the hit rate only reflects those 10 games.
                        </p>
                    </Section>

                    {/* Per-Game Stats */}
                    <Section title="Per-Game Stats (PTS, AST, REB, 3PM, MINS, USAGE, FGA)">
                        <p>
                            These are the player's averages across the games shown in the chart. Each stat has <Label>three parts</Label>:
                        </p>
                        <ExampleBox>
                            <div className="grid grid-cols-3 gap-4 text-center mb-3">
                                {[
                                    { label: 'PTS', value: '23.4', diff: '+3.6', c: 'text-green500' },
                                    { label: 'AST', value: '8.0', diff: '+0.2', c: 'text-green500' },
                                    { label: 'REB', value: '2.2', diff: '-1.2', c: 'text-red500' },
                                ].map(s => (
                                    <div key={s.label}>
                                        <div className="text-[9px] text-fgSubtle uppercase tracking-widest mb-0.5">{s.label}</div>
                                        <div className="text-white font-bold text-[16px]">{s.value}</div>
                                        <div className={`font-bold text-[11px] ${s.c}`}>{s.diff}</div>
                                    </div>
                                ))}
                            </div>
                            <div className="space-y-1">
                                <div><Label color="text-white">Label</Label> — which stat (PTS = Points, AST = Assists, REB = Rebounds, etc.)</div>
                                <div><Label color="text-white">Value</Label> — average across the games currently visible on the chart</div>
                                <div><Label color="text-white">Diff</Label> — how that average compares to the player's full-season average</div>
                            </div>
                        </ExampleBox>
                        <p className="text-[12px]">
                            <span className="text-green500 font-bold">+3.6 PTS</span> means the player averages 3.6 more points in the
                            filtered games shown, compared to their season-long average. Useful for spotting hot/cold streaks or favorable matchups.
                        </p>
                        <p className="mt-2 text-[12px]">
                            <Label color="text-white">USAGE</Label> is the percentage of team plays that ended with that player shooting, getting fouled,
                            or turning it over while on the floor. Higher usage generally means more opportunities to accumulate stats.
                        </p>
                    </Section>

                    {/* The Line & Sportsbook */}
                    <Section title="The Line & Sportsbook">
                        <p>
                            The pill in the top-left (e.g. <Label color="text-white">21.5 Pts · O +102 · U -130</Label>) shows the current
                            betting line from the selected sportsbook.
                        </p>
                        <ExampleBox>
                            <div className="space-y-2">
                                <div><Label color="text-white">21.5</Label> — the sportsbook's projected points total (the line you bet over/under)</div>
                                <div>
                                    <Label color="text-green500">O +102</Label> — bet $100 to win $102 if the player scores MORE than 21.5.
                                    Positive odds = profit exceeds your bet.
                                </div>
                                <div>
                                    <Label color="text-red500">U -130</Label> — bet $130 to win $100 if the player scores FEWER than 21.5.
                                    Negative odds = you risk more than you win.
                                </div>
                            </div>
                        </ExampleBox>
                        <p className="text-[12px]">
                            Hover or click the pill to switch between DraftKings and FanDuel — the line and odds often differ slightly between books.
                        </p>
                    </Section>

                    {/* Line Movement */}
                    <Section title="Line Movement">
                        <p>
                            The line movement strip shows how the sportsbook's line (or odds) has changed throughout the current day,
                            based on snapshots captured automatically. Toggle between <Label color="text-white">Line</Label> and <Label color="text-white">Juice</Label> mode.
                        </p>

                        <div className="mt-3 mb-1 font-bold text-white text-[13px]">Line Mode</div>
                        <ExampleBox>
                            <div className="flex items-center gap-4 mb-2">
                                <div><Label color="text-neutral300">Open</Label> <Label color="text-white">21.5</Label></div>
                                <div><Label color="text-neutral300">Now</Label> <Label color="text-white">22.5</Label></div>
                                <div className="flex items-center gap-1 text-green500 font-bold"><TrendingUp className="w-3.5 h-3.5" /> +1.0</div>
                            </div>
                            <div>
                                The line started at 21.5 and moved up to 22.5. This usually means <Label color="text-green500">sharp bettors
                                hammered the Over</Label> early in the day — the book raised the line to protect itself. A rising line is generally
                                a bullish signal for the over.
                            </div>
                        </ExampleBox>
                        <ExampleBox>
                            <div className="flex items-center gap-4 mb-2">
                                <div><Label color="text-neutral300">Open</Label> <Label color="text-white">21.5</Label></div>
                                <div><Label color="text-neutral300">Now</Label> <Label color="text-white">20.5</Label></div>
                                <div className="flex items-center gap-1 text-red500 font-bold"><TrendingDown className="w-3.5 h-3.5" /> -1.0</div>
                            </div>
                            <div>
                                The line dropped — books got heavy <Label color="text-red500">Under action</Label> and lowered the number to rebalance.
                            </div>
                        </ExampleBox>

                        <div className="mt-4 mb-1 font-bold text-white text-[13px]">Juice Mode (Over Odds)</div>
                        <p className="text-[12px] mb-2">
                            "Juice" (also called vig) is the sportsbook's commission embedded in the odds. Tracking juice movement
                            reveals how much the market favors one side, even when the line itself doesn't move.
                        </p>
                        <ExampleBox>
                            <div className="flex items-center gap-4 mb-2">
                                <div><Label color="text-neutral300">Open</Label> <Label color="text-white">-122</Label></div>
                                <div><Label color="text-neutral300">Now</Label> <Label color="text-white">+100</Label></div>
                            </div>
                            <div className="space-y-1">
                                <div>Open <Label color="text-white">-122</Label>: You bet $122 to win $100 on the Over. The book was heavily taxing over bettors.</div>
                                <div>Now <Label color="text-white">+100</Label>: You win $100 per $100 bet — zero juice. The over became much more attractive.</div>
                            </div>
                            <div className="mt-2 pt-2 border-t border-white/10">
                                This means sharps moved money to the Over (pushing the line up) and the book now needs to
                                offer better over odds to attract under bettors and balance the action. A classic sharp-money signal.
                            </div>
                        </ExampleBox>

                        <p className="text-[12px] mt-2">
                            💡 Hover over any point on the sparkline to see the exact value and time of that snapshot.
                            The time labels on the far right show the earliest and latest snapshots captured for the day.
                        </p>
                    </Section>

                    {/* Chart */}
                    <Section title="The Bar Chart">
                        <p>
                            Each bar represents one game. The <Label color="text-yellow-400">yellow horizontal line</Label> is the current betting line.
                        </p>
                        <ExampleBox>
                            <div className="space-y-1.5">
                                <div><Label color="text-green500">Green bar</Label> — player beat the line (went Over) in that game</div>
                                <div><Label color="text-red500">Red bar</Label> — player missed the line (went Under)</div>
                                <div><Label color="text-white">? bar</Label> — the upcoming game (no score yet). Shows the opponent and date.</div>
                            </div>
                        </ExampleBox>
                        <p className="text-[12px]">
                            Hover over any bar to see the full game details: final score, opponent, margin of victory, and the historical closing line if available.
                        </p>
                    </Section>

                </div>

                {/* Footer */}
                <div className="px-6 py-3 border-t border-white/8 shrink-0 flex justify-end">
                    <button
                        onClick={onClose}
                        className="px-4 py-1.5 bg-bgElevation2 hover:bg-bgElevation0 border border-borderMedium text-white text-[12px] font-medium rounded-lg transition-colors"
                    >
                        Got it
                    </button>
                </div>
            </div>
        </div>
    );
};
