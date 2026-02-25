import React, { useMemo } from 'react';
import { Player } from '../types';
import { HelpCircle, SlidersHorizontal, ChevronRight, Ban } from 'lucide-react';
import { ImageWithFallback } from './ui/ImageWithFallback';
import { TEAM_IDS, TEAM_COLORS } from '../constants';

interface HeaderProps {
  player?: Player;
  activeTab: string;
  onTabChange: (tab: string) => void;
  activeSportsbook: 'dk' | 'fd' | 'mgm' | 'cz';
  onSportsbookChange: (sb: 'dk' | 'fd' | 'mgm' | 'cz') => void;
}

const STAT_LABELS: Record<string, string> = {
  'Points': 'PTS',
  'Assists': 'AST',
  'Rebounds': 'REB',
  'Threes': 'FG3M',
  'Pts+Ast': 'PTS+AST',
  'Pts+Reb': 'PTS+REB',
  'Reb+Ast': 'REB+AST',
  'Pts+Reb+Ast': 'PTS+REB+AST',
  'Fantasy': 'FAN',
  'Blocks': 'BLK',
  'Steals': 'STL',
  'Turnovers': 'TOV'
};

const TAB_ORDER = ['Points', 'Assists', 'Rebounds', 'Threes', 'Pts+Ast', 'Pts+Reb', 'Reb+Ast', 'Pts+Reb+Ast', 'Double Double', 'Triple Double', '1Q Points', '1Q Assists', '1Q Rebounds', '1H Points'];

const SPORTSBOOKS = [
  { id: 'dk', label: 'DraftKings', logo: `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/sportsbook_logos/draftkings.webp` },
  { id: 'fd', label: 'FanDuel', logo: `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/sportsbook_logos/fanduel.webp` },
] as const;


const StatItem = ({ label, value, diff }: { label: string, value: string | number, diff?: string | number }) => {
  const diffVal = typeof diff === 'string' ? parseFloat(diff) : (diff || 0);
  const isPositive = diffVal > 0;
  const diffClass = isPositive ? 'text-green500' : (diffVal < 0 ? 'text-red500' : 'text-gray-500');
  const diffText = diffVal > 0 ? `+${diffVal.toFixed(1)}` : (diffVal === 0 ? '-' : `${diffVal.toFixed(1)}`);

  return (
    // REDUCED: px-4 -> px-3
    <div className="flex flex-col items-center px-1 lg:px-2 shrink-0">
      {/* REDUCED: text-[10px] -> text-[9px] */}
      <span className="text-[9px] text-fgSubtle uppercase tracking-wider font-bold mb-0.5 whitespace-nowrap">{label}</span>
      {/* REDUCED: text-[20px] -> text-[18px] */}
      <span className="text-[18px] font-bold text-white leading-none mb-0.5">{typeof value === 'number' ? value.toFixed(1) : value}</span>
      {/* REDUCED: text-[11px] -> text-[10px] */}
      <span className={`text-[10px] font-bold ${diffClass}`}>{diffText}</span>
    </div>
  );
};

export const Header: React.FC<HeaderProps> = ({ player, activeTab, onTabChange, activeSportsbook, onSportsbookChange }) => {

  const statKey = STAT_LABELS[activeTab] || 'PTS';

  const { line, odds, hitRateInfo, statsData, hasLine } = useMemo(() => {
    if (!player) return { line: 0, odds: { over: 0, under: 0 }, hitRateInfo: null, statsData: [], hasLine: false };

    let prop = player.props?.[statKey]?.[activeSportsbook];
    const hasLine = !!prop;
    const lineVal = prop?.line || 0;
    const oddsVal = { over: prop?.over || 0, under: prop?.under || 0 };

    const logs = player.game_log || [];
    const gamesPlayed = logs.length;
    let hits = 0;

    if (hasLine) {
      logs.forEach(game => {
        let val = game[statKey];
        if (val === undefined) {
          if (statKey === 'PTS+REB+AST') val = (game.PTS || 0) + (game.REB || 0) + (game.AST || 0);
          else if (statKey === 'PTS+REB') val = (game.PTS || 0) + (game.REB || 0);
          else if (statKey === 'PTS+AST') val = (game.PTS || 0) + (game.AST || 0);
          else if (statKey === 'REB+AST') val = (game.REB || 0) + (game.AST || 0);
        }
        if (val !== undefined && val >= lineVal) hits++;
      });
    }

    const rate = (hasLine && gamesPlayed > 0) ? ((hits / gamesPlayed) * 100).toFixed(1) : '0.0';

    const seasonStats = player.stats || {};
    const last5 = logs.slice(0, 5);

    const calculateDiff = (key: string) => {
      if (!last5.length) return 0;
      const sum = last5.reduce((acc, g) => acc + (g[key] || 0), 0);
      const avg = sum / last5.length;
      const season = seasonStats[key] || 0;
      return avg - season;
    };

    const tickerItems = [
      { label: 'PTS', key: 'PTS' },
      { label: 'AST', key: 'AST' },
      { label: 'REB', key: 'REB' },
      { label: '3PM', key: 'FG3M' },
      { label: 'MINS', key: 'MIN' },
      { label: 'USAGE', key: 'usage', fallback: '0.0%' }, // Fallback if no usage
      { label: 'FGA', key: 'FGA' },
    ].map(item => ({
      label: item.label,
      value: seasonStats[item.key] || 0,
      diff: calculateDiff(item.key)
    }));

    return {
      line: lineVal,
      odds: oddsVal,
      hitRateInfo: { rate, hits, total: gamesPlayed },
      statsData: tickerItems,
      hasLine
    };
  }, [player, statKey, activeSportsbook]);

  React.useEffect(() => {
    if (!player || !player.props || !player.props[statKey]) return;
    const activeProp = player.props[statKey]?.[activeSportsbook];
    if (!activeProp) {
      const availableSb = SPORTSBOOKS.find(sb => player.props?.[statKey]?.[sb.id]);
      if (availableSb) {
        onSportsbookChange(availableSb.id as any);
      }
    }
  }, [player, statKey, activeSportsbook, onSportsbookChange]);

  const currentSbLogo = SPORTSBOOKS.find(sb => sb.id === activeSportsbook)?.logo;

  const teamId = player && TEAM_IDS[player.team] ? TEAM_IDS[player.team] : null;
  const teamLogoUrl = teamId
    ? `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${teamId}.svg`
    : `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${player?.team}.svg`;

  const teamColors = player && TEAM_COLORS[player.team] ? TEAM_COLORS[player.team] : ["#27272a", "#18181b"];

  const gradientStyle = {
    background: `linear-gradient(to top right, ${teamColors[0]}, ${teamColors[1]})`,
    padding: '2px'
  };

  if (!player) return <div className="p-4 text-white">Select a player</div>;

  return (
    <div className="bg-black pt-0 px-0 pb-0 w-full rounded-t-xl">

      {/* Top Nav Tabs */}
      <div className="relative w-full border-b border-borderMedium mb-0 px-5">
        <div className="flex items-center justify-between gap-1 text-[11px] xl:text-[12px] font-bold text-fgSubtle pb-3 pt-3 w-full">
          {TAB_ORDER.map((tab, i) => {
            const isActive = tab === activeTab;
            const tabKey = STAT_LABELS[tab];
            const tabProp = player.props?.[tabKey]?.[activeSportsbook];
            const hasTabLine = !!tabProp;

            return (
              <div key={tab} className="relative group shrink-0">
                <span
                  onClick={() => {
                    if (hasTabLine) onTabChange(tab)
                  }}
                  className={`
                        whitespace-nowrap transition-colors border-b-[2px] -mb-[14px] pb-3 flex items-center gap-1.5
                        ${isActive ? 'text-white border-white' : 'border-transparent'}
                        ${hasTabLine ? 'cursor-pointer hover:text-white' : 'cursor-not-allowed opacity-40 hover:text-fgSubtle'}
                    `}
                >
                  {tab}
                </span>
              </div>
            );
          })}
        </div>

        {/* Gradient Fade removed to show all tabs */}
      </div>

      {/* Main Stats Row */}
      <div className="flex flex-col xl:flex-row items-center w-full bg-neutral950 relative z-30">

        {/* Section 1: Player Info */}
        {/* REDUCED: py-5 -> py-3.5 */}
        <div className="flex items-center gap-3 lg:gap-4 px-3 lg:px-4 py-3.5 border-b xl:border-b-0 border-borderMedium w-full xl:w-auto justify-start">
          <div className="relative shrink-0 w-[58px] h-[58px]">
            <div
              className="w-full h-full rounded-full overflow-hidden"
              style={gradientStyle}
            >
              <div className="w-full h-full rounded-full overflow-hidden bg-bgElevation1">
                <ImageWithFallback
                  src={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/player_headshots/${player.id}.png`}
                  alt={player.name}
                  className="w-full h-full object-cover transform scale-125 pt-1.5"
                />
              </div>
            </div>
            {/* FIX: Team Logo moved to Top Right Position, overlapping border */}
            <div className="absolute -top-1 -right-1 z-10 w-6 h-6 flex items-center justify-center pointer-events-none drop-shadow-md">
              <img
                src={teamLogoUrl}
                alt={player.team}
                width={24}
                height={24}
                className="w-full h-full object-contain"
                loading="eager"
              />
            </div>
          </div>

          <div className="flex flex-col gap-1 min-w-0">
            <div className="flex items-baseline gap-2">
              <h1 className="text-xl font-bold text-white tracking-tight whitespace-nowrap truncate leading-none">
                {player.name} <span className="text-neutral600 font-bold text-sm ml-0.5">{player.position}</span>
              </h1>
            </div>

            <div className="flex items-center select-none"> {/* Added select-none to prevent selection during hover */}
              {/* FIX: Improved Hover Persistence via padding bridge */}
              <div className={`bg-bgElevation1 rounded-lg pl-1.5 pr-2.5 py-1.5 flex items-center gap-3 border ${hasLine ? 'border-transparent hover:bg-bgElevation1/50' : 'border-red-900/30'} relative group cursor-pointer transition-colors pb-1.5`}>

                {/* FIX: Restored Sportsbook Logo (Full Color if possible, or standardized container) */}
                {/* User asked for the LOGO back, removing the blue box and invert if it hides colors */}
                <div className="w-4 h-4 rounded-[2px] flex items-center justify-center shrink-0 overflow-hidden bg-white">
                  <img src={currentSbLogo} alt={activeSportsbook} className="w-full h-full object-contain" />
                </div>

                {hasLine ? (
                  <span className="text-white font-bold text-[13px] whitespace-nowrap leading-none">
                    {line} <span className="text-neutral400 font-medium text-[11px] ml-0.5">{STAT_LABELS[activeTab] === 'PTS' ? 'Pts' : STAT_LABELS[activeTab]}</span>
                  </span>
                ) : (
                  <span className="text-red-500 font-bold text-[10px] whitespace-nowrap flex items-center gap-1">No Line</span>
                )}

                {hasLine && (
                  <div className="flex gap-2.5 text-[11px] font-bold ml-0 border-l border-borderMuted pl-3 whitespace-nowrap leading-none">
                    <span className="text-neutral600">O <span className="text-green500">{odds.over}</span></span>
                    <span className="text-neutral600">U <span className="text-red500">{odds.under}</span></span>
                  </div>
                )}

                {/* FIX: Dropdown Hover Bridge. 
                    The dropdown is positioned at `top-full mt-2`. That's an 8px gap. 
                    We need an invisible bridge covering that gap.
                    Added `before:h-4 before:-top-4` to bridge the gap properly.
                */}
                <div className="absolute top-full left-0 mt-2 bg-bgElevation1 border border-borderMedium rounded-md shadow-xl z-50 flex-col gap-1 p-1 hidden group-hover:flex min-w-[160px] 
                                before:absolute before:-top-4 before:left-0 before:w-full before:h-4 before:bg-transparent">
                  {SPORTSBOOKS.map(sb => {
                    const sbProp = player.props?.[statKey]?.[sb.id];
                    const sbHasLine = !!sbProp;
                    const isSelected = activeSportsbook === sb.id;

                    return (
                      <button
                        key={sb.id}
                        disabled={!sbHasLine}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (sbHasLine) onSportsbookChange(sb.id as any);
                        }}
                        className={`
                                        flex items-center gap-3 px-2 py-2 rounded text-xs font-bold text-left transition-colors relative
                                        ${isSelected ? 'bg-borderMedium text-white' : 'text-gray-400 hover:text-white hover:bg-borderMedium/50'}
                                        ${!sbHasLine ? 'opacity-40 cursor-not-allowed hover:bg-transparent' : ''}
                                    `}
                      >
                        <div className="w-4 h-4 rounded-[2px] overflow-hidden bg-white shrink-0 flex items-center justify-center">
                          <img src={sb.logo} alt={sb.label} className="w-full h-full object-contain" />
                        </div>
                        <span className="truncate flex-1">{sb.label}</span>
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Section 2: Hit Rate */}
        {/* REDUCED: py-5 -> py-3.5 */}
        <div className="flex flex-col items-center justify-center px-4 lg:px-6 py-3.5 border-b xl:border-b-0 border-borderMedium w-full xl:w-auto shrink-0 min-w-[140px]">
          <span className="text-[10px] text-fgSubtle font-bold tracking-widest mb-1 whitespace-nowrap uppercase">HIT RATE</span>
          {hasLine ? (
            <span className={`text-[24px] font-bold tracking-tight leading-none mb-1 ${parseFloat(hitRateInfo?.rate || '0') >= 50 ? 'text-green500' : 'text-red500'}`}>
              {hitRateInfo?.rate}% <span className={`text-[18px] opacity-90`}>({hitRateInfo?.hits}/{hitRateInfo?.total})</span>
            </span>
          ) : (
            <span className="text-[24px] font-bold text-borderMedium leading-none mb-1">--.--%</span>
          )}
          <span className="text-[10px] text-neutral600 font-medium whitespace-nowrap">
            {hitRateInfo?.total || 0} of {hitRateInfo?.total || 0} games
          </span>
        </div>

        {/* Section 3: Stats Grid */}
        {/* REDUCED: py-5 -> py-3.5 */}
        <div className="flex-1 py-3.5 px-3 lg:px-4 w-full xl:w-auto">
          <div className="flex items-center justify-between w-full h-full gap-1 lg:gap-2">
            {statsData.map((stat, i) => (
              <StatItem key={stat.label} label={stat.label} value={stat.value} diff={stat.diff} />
            ))}
          </div>
        </div>

        {/* Section 4: Actions */}
        <div className="flex items-center gap-4 px-4 py-3.5 border-borderMedium w-full xl:w-auto justify-end shrink bg-black">
          <HelpCircle className="w-5 h-5 text-borderMuted cursor-pointer hover:text-neutral400 transition-colors shrink-0" />
          <button className="flex items-center gap-2 bg-black border border-borderMedium hover:bg-bgElevation1 hover:border-borderMuted text-white text-[11px] font-bold px-3 py-2 rounded-lg transition-all whitespace-nowrap uppercase tracking-wide">
            <SlidersHorizontal className="w-3.5 h-3.5" />
            Filters
          </button>
        </div>

      </div>
    </div>
  );
};