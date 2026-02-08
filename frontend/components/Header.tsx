import React, { useMemo } from 'react';
import { Player } from '../types';
import { HelpCircle, SlidersHorizontal, ChevronRight, Ban } from 'lucide-react';
import { ImageWithFallback } from './ui/ImageWithFallback';
import { TEAM_IDS } from '../constants';

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
  const diffClass = isPositive ? 'text-[#22c55e]' : (diffVal < 0 ? 'text-[#ef4444]' : 'text-gray-500');
  const diffText = diffVal > 0 ? `+${diffVal.toFixed(1)}` : (diffVal === 0 ? '-' : `${diffVal.toFixed(1)}`);

  return (
    // REDUCED: px-4 -> px-3
    <div className="flex flex-col items-center min-w-[50px] px-3 first:pl-0 shrink-0">
      {/* REDUCED: text-[10px] -> text-[9px] */}
      <span className="text-[9px] text-[#71717a] uppercase tracking-wider font-bold mb-0.5 whitespace-nowrap">{label}</span>
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

  // FIX: Team Logo Logic using TEAM_IDS
  const teamId = player && TEAM_IDS[player.team] ? TEAM_IDS[player.team] : null;
  const teamLogoUrl = teamId
    ? `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${teamId}.svg`
    : `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${player?.team}.svg`;


  if (!player) return <div className="p-4 text-white">Select a player</div>;

  return (
    // FIX: Removed overflow-hidden to allow dropdown to show
    <div className="bg-[#050505] pt-0 px-0 pb-0 w-full rounded-t-xl border-b border-[#27272a]">

      {/* Top Nav Tabs */}
      <div className="relative w-full border-b border-[#27272a] mb-0 px-5">
        <div className="flex items-center gap-8 text-[13px] font-bold text-[#71717a] overflow-x-auto no-scrollbar pr-12 pb-3 pt-3 mask-linear-fade">
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
                        ${hasTabLine ? 'cursor-pointer hover:text-white' : 'cursor-not-allowed opacity-40 hover:text-[#71717a]'}
                    `}
                >
                  {tab}
                </span>
              </div>
            );
          })}
        </div>

        {/* Gradient Fade */}
        <div className="absolute right-0 top-0 bottom-[2px] w-20 bg-gradient-to-l from-[#050505] via-[#050505] to-transparent pointer-events-none flex items-center justify-end pr-5">
          <div className="text-[#52525b]">
            <ChevronRight className="w-5 h-5" />
          </div>
        </div>
      </div>

      {/* Main Stats Row */}
      <div className="flex flex-col xl:flex-row items-center w-full bg-[#050505] relative z-30">

        {/* Section 1: Player Info */}
        {/* REDUCED: py-5 -> py-3.5 */}
        <div className="flex items-center gap-5 px-6 py-3.5 border-b xl:border-b-0 xl:border-r border-[#27272a] w-full xl:w-auto shrink-0 justify-start">
          <div className="relative shrink-0 w-[58px] h-[58px]">
            <div className="w-full h-full rounded-full border-[2px] border-[#27272a] overflow-hidden bg-[#18181b]">
              <ImageWithFallback
                src={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/player_headshots/${player.id}.png`}
                alt={player.name}
                className="w-full h-full object-cover transform scale-125 pt-1.5"
              />
            </div>
            {/* FIX: Team Logo moved to Top Right Position, overlapping border */}
            <div className="absolute -top-1 -right-1 bg-[#09090b] rounded-full p-[2px] z-10 w-6 h-6 flex items-center justify-center">
              <div className="w-full h-full rounded-full flex items-center justify-center overflow-hidden border border-[#27272a] bg-[#18181b]">
                {/* FIX: Use teamLogoUrl with proper ID */}
                <img
                  src={teamLogoUrl}
                  alt={player.team}
                  width={24}
                  height={24}
                  className="absolute top-0 w-6 h-6 object-contain bg-transparent"
                  loading="eager"
                />
              </div>
            </div>
          </div>

          <div className="flex flex-col gap-1 min-w-0">
            <div className="flex items-baseline gap-2">
              <h1 className="text-xl font-bold text-white tracking-tight whitespace-nowrap truncate leading-none">
                {player.name} <span className="text-[#52525b] font-bold text-sm ml-0.5">{player.position || 'G'}</span>
              </h1>
            </div>

            <div className="flex items-center select-none"> {/* Added select-none to prevent selection during hover */}
              {/* FIX: Improved Hover Persistence via padding bridge */}
              <div className={`bg-[#18181b] rounded-lg pl-1.5 pr-2.5 py-1.5 flex items-center gap-3 border ${hasLine ? 'border-[#27272a] hover:border-[#3f3f46]' : 'border-red-900/30'} relative group cursor-pointer transition-colors pb-1.5`}>

                {/* FIX: Restored Sportsbook Logo (Full Color if possible, or standardized container) */}
                {/* User asked for the LOGO back, removing the blue box and invert if it hides colors */}
                <div className="w-4 h-4 rounded-[2px] flex items-center justify-center shrink-0 overflow-hidden bg-white">
                  <img src={currentSbLogo} alt={activeSportsbook} className="w-full h-full object-contain" />
                </div>

                {hasLine ? (
                  <span className="text-white font-bold text-[13px] whitespace-nowrap leading-none">
                    {line} <span className="text-[#a1a1aa] font-medium text-[11px] ml-0.5">{STAT_LABELS[activeTab] === 'PTS' ? 'Pts' : STAT_LABELS[activeTab]}</span>
                  </span>
                ) : (
                  <span className="text-red-500 font-bold text-[10px] whitespace-nowrap flex items-center gap-1">No Line</span>
                )}

                {hasLine && (
                  <div className="flex gap-2.5 text-[11px] font-bold ml-0 border-l border-[#3f3f46] pl-3 whitespace-nowrap leading-none">
                    <span className="text-[#52525b]">O <span className="text-[#22c55e]">{odds.over}</span></span>
                    <span className="text-[#52525b]">U <span className="text-[#ef4444]">{odds.under}</span></span>
                  </div>
                )}

                {/* FIX: Dropdown Hover Bridge. 
                    The dropdown is positioned at `top-full mt-2`. That's an 8px gap. 
                    We need an invisible bridge covering that gap.
                    Added `before:h-4 before:-top-4` to bridge the gap properly.
                */}
                <div className="absolute top-full left-0 mt-2 bg-[#18181b] border border-[#27272a] rounded-md shadow-xl z-50 flex-col gap-1 p-1 hidden group-hover:flex min-w-[160px] 
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
                                        ${isSelected ? 'bg-[#27272a] text-white' : 'text-gray-400 hover:text-white hover:bg-[#27272a]/50'}
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
        <div className="flex flex-col items-center justify-center px-8 py-3.5 border-b xl:border-b-0 xl:border-r border-[#27272a] w-full xl:w-auto shrink-0 min-w-[180px]">
          <span className="text-[10px] text-[#71717a] font-bold tracking-widest mb-1 whitespace-nowrap uppercase">HIT RATE</span>
          {hasLine ? (
            <span className={`text-[24px] font-bold tracking-tight leading-none mb-1 ${parseFloat(hitRateInfo?.rate || '0') >= 50 ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
              {hitRateInfo?.rate}% <span className={`text-[18px] opacity-90`}>({hitRateInfo?.hits}/{hitRateInfo?.total})</span>
            </span>
          ) : (
            <span className="text-[24px] font-bold text-[#27272a] leading-none mb-1">--.--%</span>
          )}
          <span className="text-[10px] text-[#52525b] font-medium whitespace-nowrap">
            {hitRateInfo?.total || 0} of {hitRateInfo?.total || 0} games
          </span>
        </div>

        {/* Section 3: Stats Grid */}
        {/* REDUCED: py-5 -> py-3.5 */}
        <div className="flex-1 overflow-x-auto no-scrollbar py-3.5 px-6 w-full xl:w-auto">
          <div className="flex items-center justify-between xl:justify-around min-w-max h-full gap-8">
            {statsData.map((stat, i) => (
              <StatItem key={stat.label} label={stat.label} value={stat.value} diff={stat.diff} />
            ))}
          </div>
        </div>

        {/* Section 4: Actions */}
        {/* REDUCED: py-5 -> py-3.5 */}
        <div className="flex items-center gap-6 px-6 py-3.5 xl:border-l border-[#27272a] w-full xl:w-auto justify-end shrink-0 bg-[#050505]">
          <HelpCircle className="w-5 h-5 text-[#3f3f46] cursor-pointer hover:text-[#a1a1aa] transition-colors" />
          <button className="flex items-center gap-2 bg-[#050505] border border-[#27272a] hover:bg-[#18181b] hover:border-[#3f3f46] text-white text-xs font-bold px-4 py-2 rounded-lg transition-all whitespace-nowrap uppercase tracking-wide">
            <SlidersHorizontal className="w-3.5 h-3.5" />
            Filters
          </button>
        </div>

      </div>
    </div>
  );
};