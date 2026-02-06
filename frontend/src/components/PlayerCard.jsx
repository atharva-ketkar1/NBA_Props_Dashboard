import React, { useState } from 'react';
import { TrendingUp, TrendingDown, Activity, Calendar } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Cell } from 'recharts';

const PROPS = ['PTS', 'REB', 'AST', 'PTS+REB+AST', 'FG3M', 'STL+BLK'];

const PlayerCard = ({ player }) => {
    const [activeProp, setActiveProp] = useState('PTS');

    // 1. Get the Odds for the selected prop (Safe access)
    const propData = player.props?.[activeProp] || {};
    const dkLine = propData.dk?.line || 0;
    const fdLine = propData.fd?.line || 0;

    // Use the DraftKings line as the "Target" for the chart (fallback to FD, then 0)
    const targetLine = dkLine || fdLine;

    // 2. Prepare Data for the Chart (Last 10 Games)
    // We reverse the log so the chart goes Left (Old) -> Right (New)
    const chartData = player.game_log
        .slice(0, 10)
        .reverse()
        .map(game => ({
            date: game.DATE_STR.slice(0, 5), // "11/24"
            val: game[activeProp] || 0,      // The actual stat value
            result: (game[activeProp] || 0) > targetLine ? 'HIT' : 'MISS' // Color logic
        }));

    return (
        <div className="bg-slate-800 rounded-xl p-6 shadow-lg border border-slate-700 w-full max-w-2xl mx-auto my-4">

            {/* --- HEADER: Player Info --- */}
            <div className="flex justify-between items-start mb-6">
                <div>
                    <h2 className="text-2xl font-bold text-white">{player.name}</h2>
                    <div className="flex items-center gap-2 text-slate-400 text-sm mt-1">
                        <span className="font-mono bg-slate-700 px-2 py-0.5 rounded text-white">{player.team}</span>
                        <span>•</span>
                        {/* Grab matchup from the most recent game (Game 0) */}
                        <span>{player.game_log[0]?.MATCHUP || "No Matchup"}</span>
                    </div>
                </div>
                <div className="text-right">
                    <div className="text-3xl font-mono font-bold text-emerald-400">
                        {targetLine > 0 ? targetLine : "N/A"}
                    </div>
                    <div className="text-xs text-slate-500 uppercase tracking-wider">Target Line</div>
                </div>
            </div>

            {/* --- TABS: Prop Selector --- */}
            <div className="flex gap-2 overflow-x-auto pb-4 mb-4 scrollbar-hide">
                {PROPS.map(prop => (
                    <button
                        key={prop}
                        onClick={() => setActiveProp(prop)}
                        className={`px-4 py-2 rounded-lg text-sm font-bold whitespace-nowrap transition-colors ${activeProp === prop
                                ? 'bg-blue-600 text-white shadow-blue-900/50 shadow-lg'
                                : 'bg-slate-700 text-slate-400 hover:bg-slate-600 hover:text-white'
                            }`}
                    >
                        {prop}
                    </button>
                ))}
            </div>

            {/* --- ODDS CARD: DK vs FD --- */}
            <div className="grid grid-cols-2 gap-4 mb-6">
                {/* DraftKings */}
                <div className="bg-slate-900/50 p-3 rounded-lg border border-slate-700">
                    <div className="text-xs text-slate-400 mb-1 flex justify-between">
                        <span>DraftKings</span>
                        {propData.dk?.line && <span className="text-blue-400 font-mono">{propData.dk.line}</span>}
                    </div>
                    <div className="flex justify-between text-sm font-mono">
                        <span className="text-emerald-400">o {propData.dk?.over || '-'}</span>
                        <span className="text-rose-400">u {propData.dk?.under || '-'}</span>
                    </div>
                </div>

                {/* FanDuel */}
                <div className="bg-slate-900/50 p-3 rounded-lg border border-slate-700">
                    <div className="text-xs text-slate-400 mb-1 flex justify-between">
                        <span>FanDuel</span>
                        {propData.fd?.line && <span className="text-blue-400 font-mono">{propData.fd.line}</span>}
                    </div>
                    <div className="flex justify-between text-sm font-mono">
                        <span className="text-emerald-400">o {propData.fd?.over || '-'}</span>
                        <span className="text-rose-400">u {propData.fd?.under || '-'}</span>
                    </div>
                </div>
            </div>

            {/* --- CHART: Last 10 Games --- */}
            <div className="h-48 w-full mt-4">
                {targetLine > 0 ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={chartData}>
                            <Tooltip
                                cursor={{ fill: '#334155', opacity: 0.4 }}
                                contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', color: '#fff' }}
                            />
                            <ReferenceLine y={targetLine} stroke="#94a3b8" strokeDasharray="3 3" />
                            <Bar dataKey="val" radius={[4, 4, 0, 0]}>
                                {chartData.map((entry, index) => (
                                    <Cell key={`cell-${index}`} fill={entry.val >= targetLine ? '#34d399' : '#f87171'} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                ) : (
                    <div className="h-full flex items-center justify-center text-slate-500 italic">
                        No line available for {activeProp}
                    </div>
                )}
            </div>

            {/* HIT RATE LABEL */}
            <div className="mt-2 text-center">
                <span className="text-xs text-slate-400">
                    Hit Rate (L10): <span className="text-white font-bold">
                        {chartData.filter(g => g.result === 'HIT').length} / 10
                    </span>
                </span>
            </div>

        </div>
    );
};

export default PlayerCard;