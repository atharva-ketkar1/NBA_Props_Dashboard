import React, { useMemo, useState } from 'react';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { SportsbookId } from '../types';

interface MovementSnapshot {
    timestamp: string;
    label: string;
    players: Record<string, any>;
}

interface MovementPoint {
    time: Date;
    val: number;
    label: string;
    over: number | null;
    under: number | null;
}

interface SparklineProps {
    movements: MovementSnapshot[];
    playerId: string | number;
    statKey: string;
    activeSportsbook: SportsbookId;
    activeGameDate?: string | null;
}

const SB_MAP: Record<string, string> = { dk: 'draftkings', fd: 'fanduel' };

// The series Supabase holds is sparse: `_compact_snapshots_for_supabase` stores
// an entry only for the moments a line actually moved, so a prop missing from an
// entry held its previous value rather than having no value. Two consequences
// below: the path is drawn as a step (flat, then a jump) instead of a diagonal,
// and the last known value is carried forward to the right edge rather than the
// chart ending at the last change. A diagonal between 6am and 9pm would imply a
// drift that never happened.
const CARRY_FORWARD_MAX_HOURS = 36;

const formatOdds = (odds: number | null) => {
    if (odds === null || Number.isNaN(odds)) return null;
    return odds > 0 ? `+${odds}` : String(odds);
};

export const LineMovementSparkline: React.FC<SparklineProps> = ({ movements, playerId, statKey, activeSportsbook, activeGameDate }) => {
    // ── ALL HOOKS MUST BE ABOVE ANY EARLY RETURN ──────────────────────────────
    const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

    const { dataPoints, opening, current, delta, direction } = useMemo(() => {
        const empty = { dataPoints: [] as MovementPoint[], opening: null, current: null, delta: null, direction: 'flat' as const };
        if (!movements || movements.length === 0) return empty;

        const sbKey = SB_MAP[activeSportsbook] || activeSportsbook;
        const pts: MovementPoint[] = [];

        movements.forEach(snap => {
            const pData = snap.players[String(playerId)];
            if (pData && pData.props) {
                if (activeGameDate && pData.game_date !== activeGameDate) {
                    return;
                }
                const propObj = pData.props[statKey]?.[sbKey];
                if (propObj) {
                    const val = Number(propObj.line);
                    if (!isNaN(val)) {
                        const over = Number(propObj.over);
                        const under = Number(propObj.under);
                        pts.push({
                            time: new Date(snap.timestamp),
                            val,
                            label: snap.label,
                            over: isNaN(over) ? null : over,
                            under: isNaN(under) ? null : under,
                        });
                    }
                }
            }
        });

        pts.sort((a, b) => a.time.getTime() - b.time.getTime());
        if (pts.length === 0) return empty;

        // The sparse series repeats nothing, but a `pre_game` entry is stored
        // whole, so the same line can arrive twice. Collapse those so a flat
        // stretch is not drawn as two identical change points.
        const deduped = pts.filter((pt, i) => i === 0 || pt.val !== pts[i - 1].val);

        const opening = deduped[0].val;
        const current = deduped[deduped.length - 1].val;
        const delta = +(current - opening).toFixed(1);
        const direction: 'up' | 'down' | 'flat' = delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat';
        return { dataPoints: deduped, opening, current, delta, direction };
    }, [movements, playerId, statKey, activeSportsbook, activeGameDate]);

    if (activeSportsbook === 'pp') {
        return (
            <div className="flex items-center gap-1.5 text-borderMuted text-[10px] italic opacity-80">
                <Minus className="w-3 h-3" />
                PrizePicks line movement is unavailable in v1.
            </div>
        );
    }

    // ── EARLY RETURN (after all hooks) ────────────────────────────────────────
    // One point is a complete answer now, not missing data: it means the line
    // opened there and never moved. Only an empty series is nothing to show.
    if (dataPoints.length === 0) {
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

    const formatVal = (v: number | null) => (v === null ? '--' : String(v));

    const now = new Date();
    const dateKey = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
    const todayKey = dateKey(now);

    // Carry the last known line forward to "now", because that is what the book
    // is still posting -- the series just has nothing new to say about it. Only
    // within a sane horizon, so an archived date is not stretched to today.
    const lastPoint = dataPoints[dataPoints.length - 1];
    const hoursSinceLast = (now.getTime() - lastPoint.time.getTime()) / 3_600_000;
    const axisEnd = hoursSinceLast > 0 && hoursSinceLast < CARRY_FORWARD_MAX_HOURS ? now : lastPoint.time;

    const distinctDayKeys = new Set([...dataPoints.map((point) => dateKey(point.time)), dateKey(axisEnd)]);
    const showDateContext = distinctDayKeys.size > 1 || dateKey(dataPoints[0].time) !== todayKey;

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
        minV -= 0.5;
        maxV += 0.5;
    } else {
        const pad = (maxV - minV) * 0.3;
        minV -= pad; maxV += pad;
    }
    const valRange = maxV - minV;

    const timeMin = dataPoints[0].time.getTime();
    const timeMax = axisEnd.getTime();
    const timeRange = timeMax - timeMin || 1;

    const xFor = (time: number) => PAD.left + ((time - timeMin) / timeRange) * cW;
    const yFor = (val: number) => PAD.top + cH - ((val - minV) / valRange) * cH;

    const points = dataPoints.map(dp => ({ ...dp, x: xFor(dp.time.getTime()), y: yFor(dp.val) }));
    const rightEdge = PAD.left + cW;

    // A step, not a polyline: hold the previous value across to the moment it
    // changed, then jump. The flat tail is the line as it stands right now.
    const pathD = [
        `M ${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`,
        ...points.slice(1).flatMap(p => [`H ${p.x.toFixed(1)}`, `V ${p.y.toFixed(1)}`]),
        `H ${rightEdge.toFixed(1)}`,
    ].join(' ');

    const fillId = `fill-${playerId}-${statKey}`;
    const fillD = `${pathD} V ${PAD.top + cH} H ${points[0].x.toFixed(1)} Z`;

    const hovered = hoveredIdx !== null ? points[hoveredIdx] : null;
    const hoveredOdds = hovered ? formatOdds(hovered.over) : null;

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
                <div className={`flex items-center gap-0.5 font-bold text-[10px] ${trendColor}`}>
                    <TrendIcon className="w-3 h-3" />
                    <span>{delta === 0 ? 'No change' : delta > 0 ? `+${delta}` : String(delta)}</span>
                </div>
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

                    {/* Dot on each moment the line moved — the opening included */}
                    {points.map((p, i) => (
                        <circle
                            key={`dot-${i}`}
                            cx={p.x}
                            cy={p.y}
                            r={i === points.length - 1 ? 3 : 2}
                            fill={strokeColor}
                            stroke="#111113"
                            strokeWidth="1.5"
                        />
                    ))}

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
                        className="absolute z-50 pointer-events-none bg-bgElevation0 border border-borderMedium/60 rounded-sm px-2 py-1 text-[10px] whitespace-nowrap shadow-xl"
                        style={{ left: `clamp(0px, ${(hovered.x / VW * 100).toFixed(1)}%, calc(100% - 96px))`, bottom: '100%', marginBottom: '6px' }}
                    >
                        <div className="text-white font-bold">
                            {formatVal(hovered.val)}
                            {hoveredOdds && <span className="text-fgSubtle font-semibold ml-1">({hoveredOdds})</span>}
                        </div>
                        <div className="text-fgSubtle">{formatTimestamp(hovered.time)}</div>
                    </div>
                )}
            </div>

            {/* Time labels */}
            <div className="flex items-center gap-1 shrink-0 text-[9px] text-fgSubtle/50">
                <span>{formatTimestamp(dataPoints[0].time)}</span>
                <span className="opacity-40">→</span>
                <span>{axisEnd === now ? 'now' : formatTimestamp(axisEnd)}</span>
            </div>
        </div>
    );
};
