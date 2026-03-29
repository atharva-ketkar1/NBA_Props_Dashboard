import React, { useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { SportsbookId } from '../types';

interface MovementSnapshot {
    timestamp: string;
    label: string;
    players: Record<string, any>;
}

interface SparklineProps {
    movements: MovementSnapshot[];
    playerId: string | number;
    statKey: string;
    activeSportsbook: SportsbookId;
    mode: 'line' | 'juice';
    activeGameDate?: string | null;
}

const SB_MAP: Record<string, string> = { dk: 'draftkings', fd: 'fanduel' };

const impliedProb = (odds: number) => {
    if (odds < 0) return (-odds) / (-odds + 100) * 100;
    return 100 / (odds + 100) * 100;
};

export const LineMovementSparkline: React.FC<SparklineProps> = ({ movements, playerId, statKey, activeSportsbook, mode, activeGameDate }) => {
    // ── ALL HOOKS MUST BE ABOVE ANY EARLY RETURN ──────────────────────────────
    const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

    const { dataPoints, opening, current, delta, direction } = useMemo(() => {
        if (!movements || movements.length === 0) return { dataPoints: [], opening: null, current: null, delta: null, direction: 'flat' as const };

        const sbKey = SB_MAP[activeSportsbook] || activeSportsbook;
        const pts: { time: Date; val: number; label: string }[] = [];

        movements.forEach(snap => {
            const pData = snap.players[String(playerId)];
            if (pData && pData.props) {
                if (activeGameDate && pData.game_date !== activeGameDate) {
                    return;
                }
                const propObj = pData.props[statKey]?.[sbKey];
                if (propObj) {
                    const val = mode === 'line' ? Number(propObj.line) : Number(propObj.over);
                    if (!isNaN(val)) pts.push({ time: new Date(snap.timestamp), val, label: snap.label });
                }
            }
        });

        pts.sort((a, b) => a.time.getTime() - b.time.getTime());
        if (pts.length === 0) return { dataPoints: pts, opening: null, current: null, delta: null, direction: 'flat' as const };

        const opening = pts[0].val;
        const current = pts[pts.length - 1].val;
        const delta = +(current - opening).toFixed(1);
        const direction: 'up' | 'down' | 'flat' = delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat';
        return { dataPoints: pts, opening, current, delta, direction };
    }, [movements, playerId, statKey, activeSportsbook, mode, activeGameDate]);

    // Must also be above any early return (Rules of Hooks)
    const displayDelta = useMemo(() => {
        if (delta === null || opening === null || current === null) return null;
        if (mode === 'line') {
            return { text: delta > 0 ? `+${delta}` : delta === 0 ? 'No change' : String(delta), show: delta !== 0 };
        }
        // For juice: show implied probability shift (raw American-odds diff is meaningless)
        const openProb = impliedProb(opening);
        const nowProb = impliedProb(current);
        const probDelta = +(nowProb - openProb).toFixed(1);
        if (probDelta === 0) return { text: 'No change', show: false };
        return { text: `${probDelta > 0 ? '+' : ''}${probDelta}% impl.`, show: true };
    }, [delta, opening, current, mode]);

    if (activeSportsbook === 'pp') {
        return (
            <div className="flex items-center gap-1.5 text-borderMuted text-[10px] italic opacity-80">
                <Minus className="w-3 h-3" />
                PrizePicks line movement is unavailable in v1.
            </div>
        );
    }

    // ── EARLY RETURN (after all hooks) ────────────────────────────────────────
    if (dataPoints.length < 2) {
        return (
            <div className="flex items-center gap-1.5 text-borderMuted text-[10px] italic opacity-50">
                <Minus className="w-3 h-3" />
                No movement data for this stat
            </div>
        );
    }

    // ── Derived display values (not hooks) ────────────────────────────────────
    const strokeColor = direction === 'up' ? '#22c55e' : direction === 'down' ? '#ef4444' : '#6b7280';
    const trendColor = direction === 'up' ? 'text-green500' : direction === 'down' ? 'text-red500' : 'text-fgSubtle';
    const TrendIcon = direction === 'up' ? TrendingUp : direction === 'down' ? TrendingDown : Minus;

    const formatVal = (v: number | null) => {
        if (v === null) return '--';
        if (mode === 'juice') return v > 0 ? `+${v}` : String(v);
        return String(v);
    };

    const now = new Date();
    const dateKey = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
    const todayKey = dateKey(now);
    const distinctDayKeys = new Set(dataPoints.map((point) => dateKey(point.time)));
    const showDateContext = distinctDayKeys.size > 1 || (dataPoints[0] && dateKey(dataPoints[0].time) !== todayKey);

    const formatTimestamp = (date: Date) =>
        date.toLocaleString([], showDateContext
            ? { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' }
            : { hour: '2-digit', minute: '2-digit' });

    // SVG — full width via viewBox, rendered responsively
    const VW = 600;
    const VH = 30;
    const PAD = { top: 3, right: 8, bottom: 3, left: 8 };
    const cW = VW - PAD.left - PAD.right;
    const cH = VH - PAD.top - PAD.bottom;

    const vals = dataPoints.map(d => d.val);
    let minV = Math.min(...vals);
    let maxV = Math.max(...vals);
    if (minV === maxV) {
        minV -= mode === 'line' ? 0.5 : 5;
        maxV += mode === 'line' ? 0.5 : 5;
    } else {
        const pad = (maxV - minV) * 0.3;
        minV -= pad; maxV += pad;
    }
    const valRange = maxV - minV;

    const timeMin = dataPoints[0].time.getTime();
    const timeMax = dataPoints[dataPoints.length - 1].time.getTime();
    const timeRange = timeMax - timeMin || 1;

    const points = dataPoints.map(dp => ({
        ...dp,
        x: PAD.left + ((dp.time.getTime() - timeMin) / timeRange) * cW,
        y: PAD.top + cH - ((dp.val - minV) / valRange) * cH,
    }));

    const pathD = `M ${points.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' L ')}`;
    const fillId = `fill-${playerId}-${mode}`;
    const fillD = `${pathD} L ${points[points.length - 1].x},${PAD.top + cH} L ${points[0].x},${PAD.top + cH} Z`;

    const hovered = hoveredIdx !== null ? points[hoveredIdx] : null;

    return (
        <div className="flex items-center gap-4 w-full min-w-0">

            {/* Opening / Current compact stats */}
            <div className="flex items-center gap-3 shrink-0 text-[10px]">
                <div className="flex items-center gap-1.5">
                    <span className="text-fgSubtle/60 uppercase tracking-wider font-semibold text-[9px]">Open</span>
                    <span className="text-neutral300 font-bold">{formatVal(opening)}</span>
                </div>
                <div className="w-px h-3 bg-borderMedium/40" />
                <div className="flex items-center gap-1.5">
                    <span className="text-fgSubtle/60 uppercase tracking-wider font-semibold text-[9px]">Now</span>
                    <span className="text-white font-bold">{formatVal(current)}</span>
                </div>
                {displayDelta?.show && (
                    <div className={`flex items-center gap-0.5 font-bold text-[10px] ${trendColor}`}>
                        <TrendIcon className="w-3 h-3" />
                        <span>{displayDelta.text}</span>
                    </div>
                )}
            </div>

            {/* Sparkline — fills remaining space */}
            <div className="relative flex-1 min-w-0 h-[30px]">
                <svg
                    viewBox={`0 0 ${VW} ${VH}`}
                    preserveAspectRatio="none"
                    className="w-full h-full overflow-visible"
                    style={{ display: 'block' }}
                    onMouseLeave={() => setHoveredIdx(null)}
                >
                    <defs>
                        <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor={strokeColor} stopOpacity="0.25" />
                            <stop offset="100%" stopColor={strokeColor} stopOpacity="0.02" />
                        </linearGradient>
                    </defs>

                    {/* Subtle horizontal mid line */}
                    <line x1={PAD.left} x2={VW - PAD.right} y1={VH / 2} y2={VH / 2} stroke="white" strokeOpacity="0.04" strokeWidth="1" />

                    {/* Area fill */}
                    <path d={fillD} fill={`url(#${fillId})`} />

                    {/* Main line */}
                    <path d={pathD} fill="none" stroke={strokeColor} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />

                    {/* Last point dot */}
                    {points.length > 0 && (
                        <circle cx={points[points.length - 1].x} cy={points[points.length - 1].y} r="3" fill={strokeColor} stroke="#111113" strokeWidth="1.5" />
                    )}

                    {/* Invisible hit areas for hover */}
                    {points.map((p, i) => {
                        const prevX = i === 0 ? 0 : (points[i - 1].x + p.x) / 2;
                        const nextX = i === points.length - 1 ? VW : (p.x + (points[i + 1]?.x ?? VW)) / 2;
                        return (
                            <rect
                                key={i}
                                x={prevX}
                                width={nextX - prevX}
                                y={0}
                                height={VH}
                                fill="transparent"
                                onMouseEnter={() => setHoveredIdx(i)}
                            />
                        );
                    })}

                    {/* Hover vertical line + dot */}
                    {hovered && (
                        <>
                            <line x1={hovered.x} x2={hovered.x} y1={PAD.top} y2={PAD.top + cH} stroke={strokeColor} strokeWidth="1" strokeOpacity="0.5" strokeDasharray="2 2" />
                            <circle cx={hovered.x} cy={hovered.y} r="3.5" fill={strokeColor} stroke="#111113" strokeWidth="1.5" />
                        </>
                    )}
                </svg>

                {/* Hover tooltip */}
                {hovered && (
                    <div
                        className="absolute z-50 pointer-events-none bg-bgElevation0 border border-borderMedium/60 rounded-md px-2 py-1 text-[10px] whitespace-nowrap shadow-xl"
                        style={{ left: `clamp(0px, ${(hovered.x / VW * 100).toFixed(1)}%, calc(100% - 80px))`, bottom: '100%', marginBottom: '6px' }}
                    >
                        <div className="text-white font-bold">{formatVal(hovered.val)}</div>
                        <div className="text-fgSubtle">{formatTimestamp(hovered.time)}</div>
                    </div>
                )}
            </div>

            {/* Time labels */}
            <div className="flex items-center gap-1 shrink-0 text-[9px] text-fgSubtle/50">
                <span>{formatTimestamp(dataPoints[0].time)}</span>
                <span className="opacity-40">→</span>
                <span>{formatTimestamp(dataPoints[dataPoints.length - 1].time)}</span>
            </div>
        </div>
    );
};
