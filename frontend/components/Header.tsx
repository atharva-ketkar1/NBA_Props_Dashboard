import React, { useMemo } from 'react';
import { Player } from '../types';
import { HelpCircle, SlidersHorizontal, ChevronRight, Ban } from 'lucide-react';
import { ImageWithFallback } from './ui/ImageWithFallback';

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

const TAB_ORDER = ['Points', 'Assists', 'Rebounds', 'Threes', 'Pts+Reb+Ast', 'Pts+Ast', 'Pts+Reb', 'Reb+Ast', 'Fantasy', 'Blocks', 'Steals'];

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
    <div className="flex flex-col items-center min-w-[50px] px-2 lg:px-3 first:pl-0 shrink-0">
      <span className="text-[9px] text-[#71717a] uppercase tracking-wider font-bold mb-0.5 whitespace-nowrap">{label}</span>
      <span className="text-[20px] font-bold text-white leading-none mb-0.5">{typeof value === 'number' ? value.toFixed(1) : value}</span>
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
        if (val >= lineVal) hits++;
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
      { label: 'TOV', key: 'TOV' },
      { label: 'STL', key: 'STL' },
      { label: 'BLK', key: 'BLK' }
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

  if (!player) return <div className="p-4 text-white">Select a player</div>;

  return (
    // CHANGE: bg-[#050505] -> bg-[#121214] (Card Background)
    // Also added rounded-xl to match the "Card" look
    <div className="bg-[#121214] pt-4 px-0 pb-0 w-full rounded-xl border border-[#27272a] overflow-hidden">

      {/* Top Nav Tabs */}
      <div className="relative w-full border-b border-[#27272a] mb-0 px-5">
        <div className="flex items-center gap-6 text-[13px] font-bold text-[#71717a] overflow-x-auto no-scrollbar pr-12 pb-3 mask-linear-fade">
          {TAB_ORDER.map((tab, i) => {
            const isActive = tab === activeTab;
            const tabKey = STAT_LABELS[tab];
            const tabProp = player.props?.[tabKey]?.[activeSportsbook];
            const hasTabLine = !!tabProp;

            return (
              <div key={tab} className="relative group">
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
                  {!hasTabLine && <Ban className="w-3 h-3 text-[#71717a] opacity-0 group-hover:opacity-100 transition-opacity" />}
                </span>
              </div>
            );
          })}
        </div>

        {/* CHANGE: Gradient fade matches new card BG (#121214) */}
        <div className="absolute right-0 top-0 bottom-[2px] w-20 bg-gradient-to-l from-[#121214] via-[#121214] to-transparent pointer-events-none flex items-center justify-end pr-5">
          <div className="text-[#52525b]">
            <ChevronRight className="w-5 h-5" />
          </div>
        </div>
      </div>

      {/* Main Stats Row - CHANGE: bg-[#121214] */}
      <div className="flex flex-col xl:flex-row items-center w-full bg-[#121214]">

        {/* Section 1: Player Info */}
        <div className="flex items-center gap-4 px-6 py-4 xl:py-5 border-b xl:border-b-0 xl:border-r border-[#27272a] w-full xl:w-auto shrink-0 justify-start">
          <div className="relative shrink-0 w-[52px] h-[52px]">
            <div className="w-full h-full rounded-full border border-[#552583] overflow-hidden bg-[#18181b]">
              <ImageWithFallback
                src={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/player_headshots/${player.id}.png`}
                alt={player.name}
                className="w-full h-full object-cover transform scale-125 pt-1"
              />
            </div>
            <div className="absolute -bottom-1 -right-1 bg-[#09090b] rounded-full p-[2px]">
              <div className="w-4 h-4 rounded-full flex items-center justify-center">
                <div className="w-full h-full bg-[#552583] rounded-full text-[6px] text-white flex items-center justify-center font-bold overflow-hidden">
                  {player.team}
                </div>
              </div>
            </div>
          </div>
          <div className="flex flex-col gap-1 min-w-0">
            <div className="flex items-baseline gap-2">
              <h1 className="text-lg font-bold text-white tracking-tight whitespace-nowrap truncate">{player.name} <span className="text-[#71717a] font-semibold text-sm">{player.position || 'POS'}</span></h1>
            </div>

            <div className="flex items-center">
              <div className={`bg-[#18181b] rounded-[4px] px-2 py-1 flex items-center gap-2 border ${hasLine ? 'border-[#27272a] hover:border-gray-600' : 'border-red-900/30'} relative group cursor-pointer transition-colors`}>

                <div className="w-4 h-4 rounded-[3px] flex items-center justify-center shrink-0 overflow-hidden bg-white">
                  {currentSbLogo ? (
                    <img src={currentSbLogo} alt={activeSportsbook} className="w-full h-full object-contain" />
                  ) : (
                    <span className="text-[10px] font-bold text-black uppercase">{activeSportsbook}</span>
                  )}
                </div>

                {hasLine ? (
                  <span className="text-white font-bold text-xs whitespace-nowrap">{line} <span className="text-[#a1a1aa] font-medium">{activeTab}</span></span>
                ) : (
                  <span className="text-red-500 font-bold text-[10px] whitespace-nowrap flex items-center gap-1">No Line</span>
                )}

                {hasLine && (
                  <div className="flex gap-2 text-[10px] font-bold ml-1 border-l border-[#3f3f46] pl-2 whitespace-nowrap">
                    <span className="text-[#71717a]">O <span className="text-[#22c55e]">{odds.over}</span></span>
                    <span className="text-[#71717a]">U <span className="text-[#ef4444]">{odds.under}</span></span>
                  </div>
                )}

                <div className="absolute top-full left-0 mt-2 bg-[#18181b] border border-[#27272a] rounded-md shadow-xl z-[9999] flex-col gap-1 p-1 hidden group-hover:flex min-w-[140px] before:absolute before:-top-4 before:left-0 before:w-full before:h-4 before:bg-transparent">
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
                                        flex items-center gap-2 px-2 py-1.5 rounded text-xs font-bold text-left transition-colors relative
                                        ${isSelected ? 'bg-[#27272a] text-white' : 'text-gray-400 hover:text-white hover:bg-[#27272a]/50'}
                                        ${!sbHasLine ? 'opacity-40 cursor-not-allowed hover:bg-transparent' : ''}
                                    `}
                      >
                        <div className="w-3.5 h-3.5 rounded-[2px] overflow-hidden bg-white shrink-0 flex items-center justify-center">
                          <img src={sb.logo} alt={sb.label} className="w-full h-full object-contain" />
                        </div>
                        <span className="truncate flex-1">{sb.label}</span>
                        {!sbHasLine && <Ban className="w-3 h-3 text-red-500 absolute right-2" />}
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Section 2: Hit Rate */}
        <div className="flex flex-col items-center justify-center px-6 py-4 xl:py-5 border-b xl:border-b-0 xl:border-r border-[#27272a] w-full xl:w-auto shrink-0">
          <span className="text-[10px] text-[#a1a1aa] font-bold tracking-widest mb-1 whitespace-nowrap uppercase">HIT RATE ({hitRateInfo?.total || 0} G)</span>
          {hasLine ? (
            <span className={`text-[22px] font-bold tracking-tight leading-none mb-1 ${parseFloat(hitRateInfo?.rate || '0') >= 50 ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>
              {hitRateInfo?.rate}% <span className={`text-[18px] ${parseFloat(hitRateInfo?.rate || '0') >= 50 ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>({hitRateInfo?.hits}/{hitRateInfo?.total})</span>
            </span>
          ) : (
            <span className="text-[22px] font-bold text-[#27272a] leading-none mb-1">--.--%</span>
          )}
          <span className="text-[10px] text-[#71717a] font-medium whitespace-nowrap">Season</span>
        </div>

        {/* Section 3: Stats Grid */}
        <div className="flex-1 overflow-x-auto no-scrollbar py-4 xl:py-5 px-4 w-full xl:w-auto">
          <div className="flex items-center justify-between xl:justify-around min-w-max h-full gap-4">
            {statsData.map((stat, i) => (
              <React.Fragment key={stat.label}>
                <StatItem label={stat.label} value={stat.value} diff={stat.diff} />
                {i < statsData.length - 1 && <div className="h-8 w-px bg-[#27272a]"></div>}
              </React.Fragment>
            ))}
          </div>
        </div>

        {/* Section 4: Actions - CHANGE: bg-[#121214] */}
        <div className="flex items-center gap-4 px-6 py-4 xl:py-5 xl:border-l border-[#27272a] w-full xl:w-auto justify-end shrink-0 bg-[#121214]">
          <HelpCircle className="w-5 h-5 text-[#52525b] cursor-pointer hover:text-[#a1a1aa] transition-colors" />
          <button className="flex items-center gap-2 bg-transparent border border-[#27272a] hover:bg-[#18181b] text-white text-xs font-bold px-4 py-2 rounded-lg transition-all whitespace-nowrap uppercase tracking-wide">
            <SlidersHorizontal className="w-3.5 h-3.5" />
            Filters
          </button>
        </div>

      </div>
    </div>
  );
};