import React, { useMemo, useRef, useState, useEffect } from 'react';
import { Player } from '../types';
import { HelpCircle, SlidersHorizontal, ChevronRight, ChevronLeft, Ban, Activity } from 'lucide-react';
import { ImageWithFallback } from './ui/ImageWithFallback';
import { TEAM_IDS, TEAM_COLORS } from '../constants';
import { LineMovementSparkline } from './LineMovementSparkline';

interface HeaderProps {
  player?: Player;
  activeTab: string;
  onTabChange: (tab: string) => void;
  activeSportsbook: 'dk' | 'fd' | 'mgm' | 'cz';
  onSportsbookChange: (sb: 'dk' | 'fd' | 'mgm' | 'cz') => void;
  customLine?: number | null;
  onToggleFilters?: () => void;
  isFiltersOpen?: boolean;
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
  'Turnovers': 'TOV',
  'Double Double': 'DD2',
  'Triple Double': 'TD3',
  '1Q Points': '1Q_PTS',
  '1Q Assists': '1Q_AST',
  '1Q Rebounds': '1Q_REB',
  '1H Points': '1H_PTS',
  'Stl+Blk': 'STL+BLK',
  'Fouls': 'PF',
  'FT Attempted': 'FTA'
};

const TAB_ORDER = [
  'Points', 'Assists', 'Rebounds', 'Threes', 'Pts+Ast', 'Pts+Reb', 'Reb+Ast', 'Pts+Reb+Ast',
  'Double Double', 'Triple Double', '1Q Points', '1Q Assists', '1Q Rebounds', '1H Points',
  'Steals', 'Blocks', 'Stl+Blk', 'Turnovers', 'Fouls', 'FT Attempted'
];

const SPORTSBOOKS = [
  { id: 'dk', label: 'DraftKings', logo: `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/sportsbook_logos/draftkings.webp` },
  { id: 'fd', label: 'FanDuel', logo: `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/sportsbook_logos/fanduel.webp` },
] as const;


const StatItem = ({ label, value, diff, isCompact }: { label: string, value: string | number, diff?: string | number, isCompact?: boolean }) => {
  const diffVal = typeof diff === 'string' ? parseFloat(diff) : (diff || 0);
  const isPositive = diffVal > 0;
  const diffClass = isPositive ? 'text-green500' : (diffVal < 0 ? 'text-red500' : 'text-gray-500');
  const diffText = diffVal > 0 ? `+${diffVal.toFixed(1)}` : (diffVal === 0 ? '-' : `${diffVal.toFixed(1)}`);

  return (
    // REDUCED: px-4 -> px-3
    <div className={`flex flex-col items-center shrink-0 transition-all duration-300 ${isCompact ? 'px-0.5' : 'px-1 lg:px-2'}`}>
      <span className={`text-fgSubtle uppercase tracking-wider font-bold mb-0.5 whitespace-nowrap transition-all duration-300 ${isCompact ? 'text-[8.5px]' : 'text-[9px]'}`}>{label}</span>
      <span className={`font-bold text-white leading-none mb-0.5 transition-all duration-300 ${isCompact ? 'text-[15px]' : 'text-[18px]'}`}>{typeof value === 'number' ? value.toFixed(1) : value}</span>
      <span className={`font-bold font-chakra ${diffClass} transition-all duration-300 ${isCompact ? 'text-[9px]' : 'text-[10px]'}`}>{diffText}</span>
    </div>
  );
};

export const Header: React.FC<HeaderProps> = ({ player, activeTab, onTabChange, activeSportsbook, onSportsbookChange, customLine, onToggleFilters, isFiltersOpen }) => {
  const [sparklineMode, setSparklineMode] = useState<'line' | 'juice'>('line');

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(true);

  const checkScroll = () => {
    if (scrollContainerRef.current) {
      const { scrollLeft, scrollWidth, clientWidth } = scrollContainerRef.current;
      setCanScrollLeft(scrollLeft > 0);
      setCanScrollRight(Math.ceil(scrollLeft + clientWidth) < scrollWidth - 1);
    }
  };

  useEffect(() => {
    checkScroll();
    window.addEventListener('resize', checkScroll);
    return () => window.removeEventListener('resize', checkScroll);
  }, [player]);

  const scrollByAmount = (direction: 'left' | 'right') => {
    if (scrollContainerRef.current) {
      const amount = scrollContainerRef.current.clientWidth * 0.70;
      scrollContainerRef.current.scrollBy({ left: direction === 'left' ? -amount : amount, behavior: 'smooth' });
    }
  };

  const statKey = STAT_LABELS[activeTab] || 'PTS';

  const { line, odds, hitRateInfo, statsData, hasLine } = useMemo(() => {
    if (!player) return { line: 0, odds: { over: 0, under: 0 }, hitRateInfo: null, statsData: [], hasLine: false };

    let prop = player.props?.[statKey]?.[activeSportsbook];
    const hasLine = !!prop;
    const lineVal = prop?.line || 0;

    const formatOdds = (val: number | string) => {
      const num = Number(val);
      if (isNaN(num)) return val;
      return num > 0 ? `+${num}` : `${num}`;
    };
    const oddsVal = { over: formatOdds(prop?.over || 0), under: formatOdds(prop?.under || 0) };

    const logs = player.game_log || [];
    const gamesPlayed = logs.length;
    let hits = 0;

    if (hasLine) {
      const activeLine = customLine !== null && customLine !== undefined ? customLine : lineVal;
      logs.forEach(game => {
        let val = game[statKey];
        if (val === undefined) {
          if (statKey === 'PTS+REB+AST') val = (game.PTS || 0) + (game.REB || 0) + (game.AST || 0);
          else if (statKey === 'PTS+REB') val = (game.PTS || 0) + (game.REB || 0);
          else if (statKey === 'PTS+AST') val = (game.PTS || 0) + (game.AST || 0);
          else if (statKey === 'REB+AST') val = (game.REB || 0) + (game.AST || 0);
        }
        if (val !== undefined && val >= activeLine) hits++;
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
      { label: 'USAGE', key: 'USG_PCT', fallback: '0.0%' }, // Fallback if no usage
      { label: 'FGA', key: 'FGA' },
    ].map(item => {
      const val = seasonStats[item.key] || 0;
      if (item.key === 'USG_PCT') {
        return {
          label: item.label,
          value: val > 0 ? `${(val * 100).toFixed(1)}%` : item.fallback,
          diff: undefined // USG_PCT is not in gamelogs, so diff would be inaccurate
        };
      }
      return {
        label: item.label,
        value: val,
        diff: calculateDiff(item.key)
      };
    });

    return {
      line: lineVal,
      odds: oddsVal,
      hitRateInfo: { rate, hits, total: gamesPlayed },
      statsData: tickerItems,
      hasLine
    };
  }, [player, statKey, activeSportsbook, customLine]);

  useEffect(() => {
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
    <div className="bg-bgElevation0 pt-0 px-0 pb-0 w-full rounded-t-xl relative z-40">

      {/* Top Nav Tabs */}
      <div className="relative w-full border-b border-borderMedium/40 mb-0 flex items-end select-none">

        {/* Left Arrow & Fade */}
        <div className={`absolute left-0 bottom-[1px] top-0 w-16 z-20 flex items-end justify-start bg-gradient-to-r from-bgElevation0 via-bgElevation0/90 to-transparent pointer-events-none transition-opacity ${canScrollLeft ? 'opacity-100' : 'opacity-0'}`}>
          <button
            onClick={() => scrollByAmount('left')}
            className="pl-3 pb-2 pt-4 pr-6 text-fgSubtle hover:text-white transition-colors pointer-events-auto"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
        </div>

        {/* Right Arrow & Fade */}
        <div className={`absolute right-0 bottom-[1px] top-0 w-16 z-20 flex items-end justify-end bg-gradient-to-l from-bgElevation0 via-bgElevation0/90 to-transparent transition-opacity flex`}>
          <button
            onClick={() => scrollByAmount('right')}
            disabled={!canScrollRight}
            className={`pr-3 pb-2 pt-4 pl-6 transition-colors pointer-events-auto ${canScrollRight ? 'text-fgSubtle hover:text-white cursor-pointer' : 'text-fgSubtle opacity-40 cursor-not-allowed'}`}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>

        <div
          ref={scrollContainerRef}
          onScroll={checkScroll}
          className="flex items-center gap-4 text-[13px] font-bold text-fgSubtle w-full z-10 relative overflow-x-auto no-scrollbar pt-3 scroll-smooth"
        >
          {/* Spacer so first item doesn't touch the edge fully when scrolling */}
          <div className="w-4 shrink-0" />

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
                        whitespace-nowrap transition-colors flex items-center px-1 pb-2 pt-1 border-b-[3px] -mb-[1px]
                        ${isActive ? 'text-gray-300 border-gray-400' : 'border-transparent'}
                        ${hasTabLine ? 'cursor-pointer hover:text-gray-300 hover:border-borderMedium/50' : 'cursor-not-allowed opacity-40 hover:text-fgSubtle'}
                    `}
                >
                  {tab}
                </span>
              </div>
            );
          })}

          {/* Spacer for the right side */}
          <div className="w-6 shrink-0" />
        </div>
      </div>

      {/* Main Stats Row */}
      <div className="flex flex-col xl:flex-row items-center w-full bg-bgElevation0 relative z-30 border-b border-borderMedium/40 rounded-t-xl">

        {/* Section 1: Player Info (Should not squish) */}
        <div className="flex items-center gap-3 lg:gap-4 px-3 lg:px-4 py-3 border-r border-borderMedium/40 w-full xl:w-auto justify-start shrink-0 min-w-max">
          <div className="relative shrink-0 w-[48px] h-[48px]">
            <div
              className="w-full h-full rounded-full overflow-hidden"
              style={gradientStyle}
            >
              <div className="w-full h-full rounded-full overflow-hidden bg-bgElevation1 flex items-center justify-center">
                <ImageWithFallback
                  src={`https://cdn.nba.com/headshots/nba/latest/260x190/${player.id}.png`}
                  fallbackSrc={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/player_headshots/${player.id}.png`}
                  alt={player.name}
                  className="w-full h-full object-cover transform scale-125 pt-1.5"
                />
              </div>
            </div>
            {/* FIX: Team Logo moved to Top Right Position, overlapping border */}
            <div className="absolute -top-1 -right-1 z-10 w-5 h-5 flex items-center justify-center pointer-events-none drop-shadow-md">
              <img
                src={teamLogoUrl}
                alt={player.team}
                width={20}
                height={20}
                className="w-full h-full object-contain"
                loading="eager"
              />
            </div>
          </div>

          <div className="flex flex-col min-w-0 pr-4">
            <div className="flex items-baseline gap-1.5 mb-1">
              <h1 className="text-lg font-bold text-gray-200 tracking-tight whitespace-nowrap truncate leading-none">
                {player.name} <span className="text-neutral500 font-medium text-xs ml-0.5">{player.position}</span>
              </h1>
            </div>

            <div className="flex items-center select-none"> {/* Added select-none to prevent selection during hover */}
              {/* FIX: Improved Hover Persistence via padding bridge */}
              <div className={`bg-bgElevation1 rounded pl-1.5 pr-2 py-1 flex items-center gap-2 border ${hasLine ? 'border-borderMedium/30 hover:bg-bgElevation1/80' : 'border-red-900/30'} relative group cursor-pointer transition-colors`}>

                {/* FIX: Restored Sportsbook Logo (Full Color if possible, or standardized container) */}
                {/* User asked for the LOGO back, removing the blue box and invert if it hides colors */}
                <div className="w-3.5 h-3.5 rounded-[2px] flex items-center justify-center shrink-0 overflow-hidden bg-white">
                  <img src={currentSbLogo} alt={activeSportsbook} className="w-full h-full object-contain" />
                </div>

                {hasLine ? (
                  <span className="text-white font-bold text-[11px] whitespace-nowrap leading-none flex items-center">
                    {line} <span className="text-neutral400 font-medium text-[10px] ml-1">{STAT_LABELS[activeTab] === 'PTS' ? 'Pts' : STAT_LABELS[activeTab]}</span>
                  </span>
                ) : (
                  <span className="text-red-500 font-bold text-[10px] whitespace-nowrap flex items-center gap-1">No Line</span>
                )}

                {hasLine && (
                  <div className="flex gap-2 text-[10px] font-bold ml-1 border-l border-borderMuted pl-2 whitespace-nowrap leading-none">
                    <span className="text-neutral500">O <span className="text-green600">{odds.over}</span></span>
                    <span className="text-neutral500">U <span className="text-red600">{odds.under}</span></span>
                  </div>
                )}

                {/* FIX: Dropdown Hover Bridge. 
                    The dropdown is positioned at `top-full mt-2`. That's an 8px gap. 
                    We need an invisible bridge covering that gap.
                    Added `before:h-4 before:-top-4` to bridge the gap properly.
                */}
                <div className="absolute top-full left-0 mt-2 bg-bgElevation1 border border-borderMedium rounded-md shadow-xl z-50 flex-col gap-1 p-1 hidden group-hover:flex min-w-[200px] 
                                before:absolute before:-top-4 before:left-0 before:w-full before:h-4 before:bg-transparent">
                  {SPORTSBOOKS.map(sb => {
                    const sbProp = player.props?.[statKey]?.[sb.id];
                    const sbHasLine = !!sbProp;
                    const isSelected = activeSportsbook === sb.id;
                    const formatOdds = (val: number | string) => {
                      const num = Number(val);
                      if (isNaN(num)) return val;
                      return num > 0 ? `+${num}` : `${num}`;
                    };

                    return (
                      <button
                        key={sb.id}
                        disabled={!sbHasLine}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (sbHasLine) onSportsbookChange(sb.id as any);
                        }}
                        className={`
                                        flex items-center justify-between gap-4 px-3 py-2.5 rounded-lg text-xs font-bold text-left transition-all relative
                                        ${isSelected ? 'bg-bgElevation2 border border-borderMedium/50 text-white shadow-sm' : 'border border-transparent text-gray-400 hover:text-white hover:bg-bgElevation2/50'}
                                        ${!sbHasLine ? 'opacity-40 cursor-not-allowed hover:bg-transparent hover:border-transparent' : ''}
                                    `}
                      >
                        <div className="flex items-center gap-2.5">
                          <div className="w-4 h-4 rounded-[2px] overflow-hidden bg-white shrink-0 flex items-center justify-center">
                            <img src={sb.logo} alt={sb.label} className="w-full h-full object-contain" />
                          </div>
                          <span className="truncate tracking-wide">{sb.label}</span>
                        </div>

                        {sbHasLine && (
                          <div className="flex items-center gap-3 shrink-0 ml-4 font-chakra">
                            <span className="text-[13px] text-white font-bold tracking-tight">{sbProp.line}</span>
                            <div className="flex items-center gap-2.5 text-[11px] font-bold border-l border-borderMuted pl-3">
                              <span className="text-neutral500 flex items-center gap-1">
                                O <span className="text-green500 tracking-tight">{formatOdds(sbProp.over)}</span>
                              </span>
                              <span className="text-neutral500 flex items-center gap-1">
                                U <span className="text-red500 tracking-tight">{formatOdds(sbProp.under)}</span>
                              </span>
                            </div>
                          </div>
                        )}
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Section 2: Hit Rate (Shrinks padding dynamically) */}
        <div className={`flex flex-col items-center justify-center py-3.5 border-r border-borderMedium/40 w-full xl:w-auto shrink-0 transition-all duration-300 ${isFiltersOpen ? 'px-2 lg:px-3 min-w-[100px]' : 'px-4 lg:px-6 min-w-[140px]'}`}>
          <span className={`text-fgSubtle font-bold tracking-widest mb-1 whitespace-nowrap uppercase transition-all duration-300 ${isFiltersOpen ? 'text-[9px]' : 'text-[10px]'}`}>HIT RATE</span>
          {hasLine ? (
            <span className={`font-bold tracking-tight leading-none mb-1 transition-all duration-300 ${parseFloat(hitRateInfo?.rate || '0') >= 50 ? 'text-green500' : 'text-red500'} ${isFiltersOpen ? 'text-[20px]' : 'text-[24px]'}`}>
              {hitRateInfo?.rate}% <span className={`opacity-90 transition-all duration-300 ${isFiltersOpen ? 'text-[12px]' : 'text-[15px]'}`}>({hitRateInfo?.hits}/{hitRateInfo?.total})</span>
            </span>
          ) : (
            <span className={`font-bold text-borderMedium leading-none mb-1 transition-all duration-300 ${isFiltersOpen ? 'text-[20px]' : 'text-[24px]'}`}>--.--%</span>
          )}
          <span className={`text-neutral600 font-medium whitespace-nowrap transition-all duration-300 ${isFiltersOpen ? 'text-[9px]' : 'text-[10px]'}`}>
            {hitRateInfo?.hits || 0} of {hitRateInfo?.total || 0} games
          </span>
        </div>

        {/* Section 3: Stats Grid (Shrinks padding dynamically) */}
        <div className={`flex-1 py-3 w-full xl:w-auto min-w-0 overflow-x-auto no-scrollbar transition-all duration-300 ${isFiltersOpen ? 'px-1 lg:px-2' : 'px-3 lg:px-4'}`}>
          <div className={`flex items-center justify-between w-full h-full min-w-max px-2 transition-all duration-300 ${isFiltersOpen ? 'gap-1 lg:gap-2' : 'gap-2 lg:gap-4'}`}>
            {(isFiltersOpen ? statsData.slice(0, 6) : statsData).map((stat, i) => (
              <StatItem key={stat.label} label={stat.label} value={stat.value} diff={stat.diff} isCompact={isFiltersOpen} />
            ))}
          </div>
        </div>

        {/* Section 4: Actions & Line Movement */}
        {!isFiltersOpen && (
          <div className="flex items-center gap-4 px-4 py-3.5 border-l border-borderMedium/40 w-full xl:w-auto justify-end shrink-0 bg-bgElevation0 relative transition-all duration-300">
            <HelpCircle className="w-5 h-5 text-borderMuted cursor-pointer hover:text-neutral400 transition-colors shrink-0" />

            <div className="relative">
              <button
                onClick={() => {
                  if (onToggleFilters) {
                    onToggleFilters();
                  }
                }}
                className="flex items-center gap-2 bg-bgElevation0 border border-borderMedium hover:bg-bgElevation1 hover:border-borderMuted text-white text-[11px] font-bold px-3 py-2 rounded-lg transition-all whitespace-nowrap uppercase tracking-wide">
                <SlidersHorizontal className="w-3.5 h-3.5" />
                Filters
              </button>
            </div>
          </div>
        )}

      </div>
    </div>
  );
};