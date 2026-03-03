import React, { useMemo } from 'react';

interface MovementSnapshot {
    timestamp: string;
    label: string;
    players: Record<string, any>; // The large dump of player data
}

interface SparklineProps {
    movements: MovementSnapshot[];
    playerId: string | number;
    statKey: string;
    activeSportsbook: 'dk' | 'fd' | 'mgm' | 'cz';
    mode: 'line' | 'juice';
}

export const LineMovementSparkline: React.FC<SparklineProps> = ({ movements, playerId, statKey, activeSportsbook, mode }) => {
    const dataPoints = useMemo(() => {
        if (!movements || movements.length === 0) return [];

        const pts: { time: Date, val: number, label: string }[] = [];

        // Scan each snapshot
        movements.forEach(snap => {
            const pData = snap.players[String(playerId)];
            if (pData && pData.props) {
                const propObj = pData.props[statKey]?.[activeSportsbook];
                if (propObj) {
                    const val = mode === 'line' ? propObj.line : propObj.over; // tracking over juice
                    pts.push({
                        time: new Date(snap.timestamp),
                        val: val,
                        label: snap.label
                    });
                }
            }
        });

        // Sort chronologically
        return pts.sort((a, b) => a.time.getTime() - b.time.getTime());
    }, [movements, playerId, statKey, activeSportsbook, mode]);

    if (dataPoints.length < 2) {
        return (
            <div className="text-gray-500 text-[10px] italic flex items-center justify-center h-full">
                Not enough line movement data available to generate a sparkline.
            </div>
        );
    }

    const { minVal, maxVal } = useMemo(() => {
        let min = Math.min(...dataPoints.map(d => d.val));
        let max = Math.max(...dataPoints.map(d => d.val));

        if (min === max) {
            min -= mode === 'line' ? 1 : 10;
            max += mode === 'line' ? 1 : 10;
        } else {
            const pad = (max - min) * 0.2;
            min -= pad;
            max += pad;
        }
        return { minVal: min, maxVal: max };
    }, [dataPoints, mode]);

    // SVG parameters
    const svgWidth = 250;
    const svgHeight = 40;
    const margin = { top: 5, right: 20, bottom: 5, left: 10 };
    const chartWidth = svgWidth - margin.left - margin.right;
    const chartHeight = svgHeight - margin.top - margin.bottom;

    const timeMin = dataPoints[0].time.getTime();
    const timeMax = dataPoints[dataPoints.length - 1].time.getTime();
    const timeRange = timeMax - timeMin || 1;

    // Calculate coordinates
    const points = dataPoints.map((dp, i) => {
        const x = margin.left + ((dp.time.getTime() - timeMin) / timeRange) * chartWidth;
        const y = margin.top + chartHeight - (((dp.val - minVal) / (maxVal - minVal)) * chartHeight);
        return { ...dp, x, y };
    });

    // Create SVG path string
    const pathD = `M ${points.map(p => `${p.x},${p.y}`).join(' L ')}`;

    return (
        <div className="flex flex-col items-center justify-center w-[250px] relative mt-1">
            <div className="text-[9px] text-fgSubtle uppercase font-bold tracking-widest self-start ml-2 mb-1">
                Day Movement ({mode === 'line' ? 'Line' : 'Over Juice'})
            </div>

            <svg width={svgWidth} height={svgHeight} className="overflow-visible">
                <path
                    d={pathD}
                    fill="none"
                    stroke="#3b82f6"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                />

                {/* Render points */}
                {points.map((p, i) => (
                    <g key={i} className="group cursor-default">
                        <circle
                            cx={p.x}
                            cy={p.y}
                            r="3"
                            fill="#1e3a8a"
                            stroke="#60a5fa"
                            strokeWidth="1.5"
                            className="hover:r-4 transition-all duration-200"
                        />

                        {/* Simple tooltip tag */}
                        <text
                            x={p.x}
                            y={p.y - 8}
                            textAnchor="middle"
                            fontSize="9"
                            fill="#93c5fd"
                            fontWeight="bold"
                            className="opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                            {mode === 'line' ? p.val : (p.val > 0 ? `+${p.val}` : p.val)}
                        </text>
                    </g>
                ))}

                {/* Current Value label at end vertex */}
                {points.length > 0 && (
                    <text
                        x={points[points.length - 1].x + 6}
                        y={points[points.length - 1].y + 3}
                        fontSize="10"
                        fill="#fff"
                        fontWeight="bold"
                    >
                        {mode === 'line' ? points[points.length - 1].val : (points[points.length - 1].val > 0 ? `+${points[points.length - 1].val}` : points[points.length - 1].val)}
                    </text>
                )}
            </svg>
        </div>
    );
};
