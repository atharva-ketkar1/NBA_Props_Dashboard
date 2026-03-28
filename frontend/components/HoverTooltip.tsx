import React from 'react';
import { ASSETS_BASE } from '../utils/config';

// Represents the data structure parsed by BarChart when hovering over a game
export interface HoveredGameData {
    game: any;
    x: number;
    y: number;
    lineValue: number;
    statKey: string;
    activeSportsbook: string;
}

interface TooltipProps {
    data: HoveredGameData | null;
    player?: import('../types').Player;
}

export const HoverTooltip: React.FC<TooltipProps> = ({ data, player }) => {
    if (!data) return null;

    const { game, x, y, lineValue, statKey, activeSportsbook } = data;

    if (!game || game.isUpcoming) return null;

    const isGameWin = game.WL === 'W' || (game.margin !== undefined && game.margin > 0);
    const hasMargin = game.margin !== undefined && game.margin !== null;
    const diff = hasMargin ? Math.abs(game.margin) : 0;

    const badgeText = hasMargin && diff > 0 
        ? (isGameWin ? `Won by ${diff}` : `Lost by ${diff}`) 
        : (isGameWin ? 'Won' : 'Lost');
    const badgeColor = isGameWin ? 'bg-green600' : 'bg-red600';

    // Remove default mock odds and start with clean fallbacks
    let displayLine: string | number = 'N/A';
    let O_ODDS = 'N/A';
    let U_ODDS = 'N/A';
    let hasHistoricalData = false;
    let isFallback = false;

    // Sportsbook logo resolution for the header
    let sbLogo = '';
    if (activeSportsbook === 'dk') sbLogo = `/assets/sportsbook_logos/draftkings.webp`;
    else if (activeSportsbook === 'fd') sbLogo = `/assets/sportsbook_logos/fanduel.webp`;

    const logoSrc = `${ASSETS_BASE}${sbLogo}`;

    // Extract historical odds if available
    if (player && player.historical_odds && game.GAME_DATE) {
        // Find the record for this exact date
        const dateRecord = player.historical_odds[game.GAME_DATE];
        if (dateRecord) {
            // Find the player in that date record (by numeric or stringified ID)
            const playerRecord = dateRecord[String(player.id)] || dateRecord[player.id];
            
            if (playerRecord && playerRecord.props) {
                // Support both the legacy JSON shape (props.props[statKey])
                // and the normalized DB shape (props[statKey]).
                const propsTree = playerRecord.props.props || playerRecord.props;
                const statProps = propsTree?.[statKey];

                if (statProps) {
                    // Mappings from frontend abbreviation to backend database keys
                    const mappedSbRaw = activeSportsbook === 'dk' ? 'draftkings' : activeSportsbook === 'fd' ? 'fanduel' : activeSportsbook;
                    
                    // Get the exact stat line from the historical prop tree
                    const histProp = statProps[mappedSbRaw];

                    // If it doesn't exist on the active sportsbook, try fallback to DK or FD
                    const fallbackProp = histProp || statProps['draftkings'] || statProps['fanduel'];

                    if (fallbackProp) {
                        displayLine = fallbackProp.line;
                        const formatOdds = (val: any) => {
                            const num = Number(val);
                            if (isNaN(num)) return String(val);
                            return num > 0 ? `+${num}` : String(num);
                        };
                        O_ODDS = formatOdds(fallbackProp.over);
                        U_ODDS = formatOdds(fallbackProp.under);
                        hasHistoricalData = true;
                        isFallback = playerRecord.source === 'last_snapshot_fallback';
                    }
                }
            }
        }
    }

    // --- Dynamic Stat Row Configuration based on current Tab (statKey) ---
    const renderTableRows = () => {
        // Format helper for minutes (handles decimals from the NBA API)
        const formatMin = (min: any) => Math.round(Number(min || 0));

        const rows: { label: string; value: string }[] = [
            { label: 'Minutes', value: `${formatMin(game.MIN)}'` },
        ];

        // Format helper for shooting percentages
        const formatPct = (m: any, a: any) => {
            const mVal = Number(m) || 0;
            const aVal = Number(a) || 0;
            if (aVal === 0) return `${mVal}/${aVal} (0%)`;
            return `${mVal}/${aVal} (${Math.round((mVal / aVal) * 100)}%)`;
        };

        // Note: The stats backend keys (statKey) are mapped from STAT_LABELS in BarChart.tsx
        switch (statKey) {
            case 'PTS':
                rows.push({ label: 'Points', value: `${game.PTS || 0}` });
                rows.push({ label: 'FT Made', value: formatPct(game.FTM, game.FTA) });
                rows.push({ label: '3PT Made', value: formatPct(game.FG3M, game.FG3A) });
                rows.push({ label: 'FG Made', value: formatPct(game.FGM, game.FGA) });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;
            case 'AST':
                rows.push({ label: 'Assists', value: `${game.AST || 0}` });
                rows.push({ label: 'Potential AST', value: `${game.POTENTIAL_AST || 0}` });
                rows.push({ label: 'Passes', value: `${game.PASSES_MADE || 'N/A'}` });
                break;
            case 'REB':
                rows.push({ label: 'Rebounds', value: `${game.REB || 0}` });
                rows.push({ label: 'Rebound Chances', value: `${game.REB_CHANCES || 0}` });
                rows.push({ label: 'OREB', value: `${game.OREB || 0}` });
                rows.push({ label: 'DREB', value: `${game.DREB || 0}` });
                break;
            case 'FG3M': // Threes
                rows.push({ label: 'Points', value: `${game.PTS || 0}` });
                rows.push({ label: 'FT Made', value: formatPct(game.FTM, game.FTA) });
                rows.push({ label: '3PT Made', value: formatPct(game.FG3M, game.FG3A) });
                rows.push({ label: 'FG Made', value: formatPct(game.FGM, game.FGA) });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;
            case 'PTS+AST':
                rows.push({ label: 'Points', value: `${game.PTS || 0}` });
                rows.push({ label: 'FT Made', value: formatPct(game.FTM, game.FTA) });
                rows.push({ label: 'FG Made', value: formatPct(game.FGM, game.FGA) });
                rows.push({ label: 'Assists', value: `${game.AST || 0}` });
                rows.push({ label: 'Potential AST', value: `${game.POTENTIAL_AST || 0}` });
                break;
            case 'PTS+REB':
                rows.push({ label: 'Points', value: `${game.PTS || 0}` });
                rows.push({ label: 'FT Made', value: formatPct(game.FTM, game.FTA) });
                rows.push({ label: 'FG Made', value: formatPct(game.FGM, game.FGA) });
                rows.push({ label: 'Rebounds', value: `${game.REB || 0}` });
                rows.push({ label: 'Rebound Chances', value: `${game.REB_CHANCES || 0}` });
                break;
            case 'REB+AST':
                rows.push({ label: 'Rebounds', value: `${game.REB || 0}` });
                rows.push({ label: 'Rebound Chances', value: `${game.REB_CHANCES || 0}` });
                rows.push({ label: 'Assists', value: `${game.AST || 0}` });
                rows.push({ label: 'Potential AST', value: `${game.POTENTIAL_AST || 0}` });
                break;
            case 'PTS+REB+AST':
                rows.push({ label: 'Points', value: `${game.PTS || 0}` });
                rows.push({ label: 'FG Made', value: formatPct(game.FGM, game.FGA) });
                rows.push({ label: 'Rebounds', value: `${game.REB || 0}` });
                rows.push({ label: 'Rebound Chances', value: `${game.REB_CHANCES || 0}` });
                rows.push({ label: 'Assists', value: `${game.AST || 0}` });
                rows.push({ label: 'Potential AST', value: `${game.POTENTIAL_AST || 0}` });
                break;
            case 'STL':
                rows.push({ label: 'Steals', value: `${game.STL || 0}` });
                rows.push({ label: 'Blocks', value: `${game.BLK || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;
            case 'BLK':
                rows.push({ label: 'Blocks', value: `${game.BLK || 0}` });
                rows.push({ label: 'Steals', value: `${game.STL || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;
            case 'TOV':
                rows.push({ label: 'Turnovers', value: `${game.TOV || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;
            // --- 1Q PROPS ---
            case '1Q_PTS':
                rows[0] = { label: '1Q Minutes', value: `${formatMin(game['1Q_MIN'])}'` };
                rows.push({ label: '1Q Points', value: `${game['1Q_PTS'] || 0}` });
                rows.push({ label: '1Q FT Made', value: `${game['1Q_FTM'] || 0}` });
                rows.push({ label: '1Q 3PT Made', value: `${game['1Q_FG3M'] || 0}` });
                rows.push({ label: '1Q FG Made', value: `${game['1Q_FGM'] || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;
            case '1Q_AST':
                rows[0] = { label: '1Q Minutes', value: `${formatMin(game['1Q_MIN'])}'` };
                rows.push({ label: '1Q Assists', value: `${game['1Q_AST'] || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;
            case '1Q_REB':
                rows[0] = { label: '1Q Minutes', value: `${formatMin(game['1Q_MIN'])}'` };
                rows.push({ label: '1Q Rebounds', value: `${game['1Q_REB'] || 0}` });
                rows.push({ label: 'OREB', value: `${game.OREB || 0}` });
                rows.push({ label: 'DREB', value: `${game.DREB || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;
            // --- 1H PROPS ---
            case '1H_PTS':
                rows[0] = { label: '1H Minutes', value: `${formatMin(game['1H_MIN'])}'` };
                rows.push({ label: '1H Points', value: `${game['1H_PTS'] || 0}` });
                rows.push({ label: '1H FG Made', value: `${game['1H_FGM'] || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;

            default:
                rows.push({ label: statKey, value: `${game.score || 0}` });
                rows.push({ label: 'Points', value: `${game.PTS || 0}` });
                rows.push({ label: 'Assists', value: `${game.AST || 0}` });
                rows.push({ label: 'Rebounds', value: `${game.REB || 0}` });
                break;
        }

        return rows.map((r, i) => (
            <div key={i} className="flex justify-between items-center text-[11px] leading-tight py-[3px]">
                <span className="text-neutral400 font-medium">{r.label}</span>
                <span className="text-white font-bold">{r.value}</span>
            </div>
        ));
    };

    // --- Dynamic 'DID NOT PLAY' Title resolver ---
    const getDnpTitle = () => {
        switch (statKey) {
            case 'PTS': return 'DID NOT PLAY [AVG PTS]';
            case 'AST': return 'DID NOT PLAY [AVG AST]';
            case 'REB': return 'DID NOT PLAY [AVG REB]';
            case 'FG3M': return 'DID NOT PLAY [AVG 3PT]';
            case 'PTS+AST': return 'DID NOT PLAY [AVG PA]';
            case 'PTS+REB': return 'DID NOT PLAY [AVG PR]';
            case 'REB+AST': return 'DID NOT PLAY [AVG RA]';
            case 'PTS+REB+AST': return 'DID NOT PLAY [AVG PRA]';
            case 'STL': return 'DID NOT PLAY [AVG STL]';
            case 'BLK': return 'DID NOT PLAY [AVG BLK]';
            default: return `DID NOT PLAY [AVG ${statKey}]`;
        }
    };

    // --- Dynamic Did Not Play fetching from aggregated logs ---
    const dnpsSafeIter = Array.isArray(game.dnps) ? game.dnps : [];
    const inactivePlayers = dnpsSafeIter
        .map((dnp: any) => {
            const rawStat = Number(dnp?.stats?.[statKey] || 0);
            const rawName = dnp?.name || 'Unknown Player';
            const nameParts = rawName.split(' ');

            let shortName = rawName;
            if (nameParts.length > 1) {
                // Check if the last part is a common suffix
                const suffix = nameParts[nameParts.length - 1].replace(/\./g, '').toLowerCase();
                const suffixes = ['jr', 'sr', 'ii', 'iii', 'iv'];

                if (suffixes.includes(suffix) && nameParts.length > 2) {
                    shortName = `${nameParts[0][0]}. ${nameParts[nameParts.length - 2]} ${nameParts[nameParts.length - 1]}`;
                } else {
                    shortName = `${nameParts[0][0]}. ${nameParts[nameParts.length - 1]}`;
                }
            }

            return {
                name: shortName.toUpperCase(),
                pts: rawStat.toFixed(1),
                rawStat,
                img: `https://cdn.nba.com/headshots/nba/latest/260x190/${dnp?.id || 'fallback'}.png`,
                fallbackImg: `${ASSETS_BASE}/assets/player_headshots/${dnp?.id || 'fallback'}.png`
            };
        })
        .sort((a: any, b: any) => b.rawStat - a.rawStat)
        .slice(0, 3);

    // Position constraints to ensure tooltip stays on screen
    const TOOLTIP_WIDTH = 224; // Tailwind w-56 is 224px
    const OFFSET = 15;

    // Check if right edge of tooltip would overflow the window width (with 20px safety margin)
    const isOverflowingRight = typeof window !== 'undefined' && (x + OFFSET + TOOLTIP_WIDTH + 20 > window.innerWidth);
    const tooltipX = isOverflowingRight ? x - TOOLTIP_WIDTH - OFFSET : x + OFFSET;

    const popoverStyle: React.CSSProperties = {
        left: `${tooltipX}px`,
        top: `${Math.max(y - 80, 20)}px`,
    };

    const displayDate = game.GAME_DATE ? game.GAME_DATE.replace(/-/g, '/').substring(5) : 'Unknown Date';

    return (
        <div
            className="fixed z-[100] w-56 flex flex-col bg-bgElevation0 rounded-xl border border-borderMedium shadow-2xl overflow-hidden pointer-events-none transform -translate-y-1/2"
            style={popoverStyle}
        >
            {/* Header Area */}
            <div className="flex justify-between items-start p-3 pb-3 relative border-b border-borderMedium">
                <div className="flex flex-col gap-2 z-10">
                    <div className="text-white font-bold text-sm flex items-center gap-1.5">
                        {displayDate} vs {game.opponent || 'TBD'}
                    </div>

                    <div className="flex items-center gap-2 text-xs font-bold">
                        {sbLogo ? (
                            <img src={logoSrc} alt="book" className="w-4 h-4 rounded-full object-cover bg-white" />
                        ) : (
                            <span className="text-white uppercase">{activeSportsbook}</span>
                        )}
                        <span className="text-white relative">
                            CL {displayLine}
                            {isFallback && <span className="text-yellow-400 align-top text-[10px] ml-[1px] absolute -top-1">*</span>}
                        </span>
                        {hasHistoricalData && (
                            <>
                                <span className="text-white ml-0.5">O <span className="text-green600">{O_ODDS}</span></span>
                                <span className="text-white ml-0.5">U <span className="text-red600">{U_ODDS}</span></span>
                            </>
                        )}
                    </div>
                    {isFallback && (
                        <div className="text-[9px] text-yellow-400 font-medium -mt-1 opacity-80">
                            * Fallback Estimate
                        </div>
                    )}
                </div>

                <div className={`${badgeColor} text-white font-bold text-xs px-3 py-1.5 absolute top-0 right-0 rounded-bl-md z-10`}>
                    {badgeText}
                </div>
            </div>

            {/* Dynamic Stats Table */}
            <div className="p-3 bg-bgElevation0 flex flex-col gap-1">
                {renderTableRows()}
            </div>

            {/* DID NOT PLAY block */}
            {inactivePlayers.length > 0 && (
                <div className="bg-bgElevation1 border-t border-borderMedium">
                    <div className="w-full text-center py-2 bg-borderMedium/50 border-b border-borderMedium">
                        <span className="text-neutral400 font-bold text-[9px] uppercase tracking-wider">{getDnpTitle()}</span>
                    </div>
                    <div className="p-2 px-3 flex flex-col gap-2">
                        {inactivePlayers.map((p: any, i: number) => (
                            <div key={i} className="flex justify-between items-center">
                                <div className="flex items-center gap-2">
                                    <div className="w-5 h-5 rounded-full bg-borderLight flex items-center justify-center overflow-hidden border border-borderMedium">
                                        <img
                                            src={p.img}
                                            alt={p.name}
                                            className="w-full h-full object-cover"
                                            onError={(e) => {
                                                const target = e.target as HTMLImageElement;
                                                if (target.src !== p.fallbackImg && target.src !== "https://cdn.nba.com/headshots/nba/latest/260x190/fallback.png") {
                                                    target.src = p.fallbackImg;
                                                } else if (target.src === p.fallbackImg) {
                                                    target.src = "https://cdn.nba.com/headshots/nba/latest/260x190/fallback.png";
                                                }
                                            }}
                                        />
                                    </div>
                                    <span className="text-grayEmphasized font-bold text-[10px]">{p.name}</span>
                                </div>
                                <span className="text-white font-bold text-[10px]">({p.pts})</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};
