import React from 'react';

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
}

export const HoverTooltip: React.FC<TooltipProps> = ({ data }) => {
    if (!data) return null;

    const { game, x, y, lineValue, statKey, activeSportsbook } = data;

    if (game.isUpcoming) return null;

    // Formatting utilities
    const isWin = game.score >= lineValue;
    const diff = Math.abs(game.score - lineValue);
    const badgeText = isWin ? `Won by ${diff}` : `Lost by ${diff}`;
    const badgeColor = isWin ? 'bg-green600' : 'bg-red600';

    // Mock Odds (Since they aren't historically stored yet)
    const O_ODDS = '-125';
    const U_ODDS = '-102';

    // Sportsbook logo resolution for the header
    let sbLogo = '';
    if (activeSportsbook === 'dk') sbLogo = '/assets/sportsbook_logos/draftkings.webp';
    else if (activeSportsbook === 'fd') sbLogo = '/assets/sportsbook_logos/fanduel.webp';

    // Base API URL for local dev asset resolution
    const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';
    const logoSrc = `${BASE_URL}${sbLogo}`;

    // --- Dynamic Stat Row Configuration based on current Tab (statKey) ---
    const renderTableRows = () => {
        const rows: { label: string; value: string }[] = [
            { label: 'Minutes', value: `${game.MIN || 0}'` },
        ];

        // Format helper for shooting percentages
        const formatPct = (m: number, a: number) => {
            const mVal = m || 0;
            const aVal = a || 0;
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
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` }); // Update if specific 1Q PF logic exists
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
            case '1Q_PTS': // (Make sure your STAT_LABELS maps '1Q Points' to '1Q_PTS')
                rows[0] = { label: '1Q Minutes', value: `${game['1Q_MIN'] || 0}'` }; // Overwrites full-game minutes
                rows.push({ label: '1Q Points', value: `${game['1Q_PTS'] || 0}` });
                rows.push({ label: '1Q FT Made', value: `${game['1Q_FTM'] || 0}` }); // No attempts scraped yet, so just showing Makes
                rows.push({ label: '1Q 3PT Made', value: `${game['1Q_FG3M'] || 0}` });
                rows.push({ label: '1Q FG Made', value: `${game['1Q_FGM'] || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;

            case '1Q_AST':
                rows[0] = { label: '1Q Minutes', value: `${game['1Q_MIN'] || 0}'` };
                rows.push({ label: '1Q Assists', value: `${game['1Q_AST'] || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;

            case '1Q_REB':
                rows[0] = { label: '1Q Minutes', value: `${game['1Q_MIN'] || 0}'` };
                rows.push({ label: '1Q Rebounds', value: `${game['1Q_REB'] || 0}` });
                rows.push({ label: 'OREB', value: `${game.OREB || 0}` }); // Displaying full game OREB since 1Q OREB wasn't scraped
                rows.push({ label: 'DREB', value: `${game.DREB || 0}` }); // Displaying full game DREB
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;

            // --- 1H PROPS ---
            case '1H_PTS':
                rows[0] = { label: '1H Minutes', value: `${game['1H_MIN'] || 0}'` };
                rows.push({ label: '1H Points', value: `${game['1H_PTS'] || 0}` });
                rows.push({ label: '1H FG Made', value: `${game['1H_FGM'] || 0}` });
                rows.push({ label: 'Fouls (1Q)', value: `${game.PF || 0} (${game['1Q_PF'] || 0})` });
                break;

            default:
                // Fallback for props like Fantasy, Double Double, etc.
                rows.push({ label: statKey, value: `${game.score}` });
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

    // --- Hardcoded Placeholders for Did Not Play as requested ---
    const inactivePlayers = [
        { name: "M. MCBRIDE", pts: "12.9", img: "https://cdn.nba.com/headshots/nba/latest/260x190/1630540.png" },
        { name: "L. SHAMET", pts: "9.7", img: "https://cdn.nba.com/headshots/nba/latest/260x190/1629013.png" },
        { name: "G. YABUSELE", pts: "3.5", img: "https://cdn.nba.com/headshots/nba/latest/260x190/1627824.png" }
    ];

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

    return (
        <div
            className="fixed z-[100] w-56 flex flex-col bg-bgElevation0 rounded-xl border border-borderMedium shadow-2xl overflow-hidden pointer-events-none transform -translate-y-1/2"
            style={popoverStyle}
        >
            {/* Header Area */}
            <div className="flex justify-between items-start p-3 pb-2 relative border-b border-borderMedium">
                <div className="flex flex-col gap-1.5 z-10">
                    <div className="text-white font-bold text-xs">
                        {game.GAME_DATE.replace(/-/g, '/').substring(5)} vs {game.opponent}
                    </div>
                    <div className="flex items-center gap-1.5 text-[10px] font-bold">
                        {sbLogo ? (
                            <img src={logoSrc} alt="book" className="w-3.5 h-3.5 rounded-full object-cover bg-white" />
                        ) : (
                            <span className="text-white uppercase">{activeSportsbook}</span>
                        )}
                        <span className="text-white">CL {lineValue}</span>
                        <span className="text-white ml-0.5">O <span className="text-green600">{O_ODDS}</span></span>
                        <span className="text-white">U <span className="text-red600">{U_ODDS}</span></span>
                    </div>
                </div>

                <div className={`${badgeColor} text-white font-bold text-[10px] px-2 py-1 rounded absolute top-2 right-2 z-10`}>
                    {badgeText}
                </div>
            </div>

            {/* Dynamic Stats Table */}
            <div className="p-3 bg-bgElevation0 flex flex-col gap-1">
                {renderTableRows()}
            </div>

            {/* DID NOT PLAY block */}
            <div className="bg-bgElevation1 border-t border-borderMedium">
                <div className="w-full text-center py-2 bg-borderMedium/50 border-b border-borderMedium">
                    <span className="text-neutral400 font-bold text-[9px] uppercase tracking-wider">{getDnpTitle()}</span>
                </div>
                <div className="p-2 px-3 flex flex-col gap-2">
                    {inactivePlayers.map((p, i) => (
                        <div key={i} className="flex justify-between items-center">
                            <div className="flex items-center gap-2">
                                <img src={p.img} alt={p.name} className="w-5 h-5 rounded-full bg-borderMedium object-cover" />
                                <span className="text-grayEmphasized font-bold text-[10px]">{p.name}</span>
                            </div>
                            <span className="text-white font-bold text-[10px]">({p.pts})</span>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};