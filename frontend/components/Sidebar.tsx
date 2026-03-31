import React, { useState, useEffect, useMemo, useRef } from 'react';
import { ChevronDown, Search, Lock, Plus, LockOpen, X, Check } from 'lucide-react';
import { Player, Game, SportsbookId } from '../types';
import { ImageWithFallback } from './ui/ImageWithFallback';
import { ASSETS_BASE } from '../utils/config';
import { getSportsbookProp, playerHasSportsbookPropForDate } from '../utils/propResolution';
import { fetchApiJson } from '../utils/network';
import { fetchDashboardGames } from '../utils/dashboardApi';

const USE_DB = import.meta.env.VITE_USE_DB === 'true';

const STAT_MAP: Record<string, string> = {
    'Points': 'PTS', 'Assists': 'AST', 'Rebounds': 'REB', 'Threes': 'FG3M',
    'Pts+Ast': 'PTS+AST', 'Pts+Reb': 'PTS+REB', 'Reb+Ast': 'REB+AST',
    'Pts+Reb+Ast': 'PTS+REB+AST', 'Fantasy': 'FAN', 'Blocks': 'BLK',
    'Steals': 'STL', 'Turnovers': 'TOV', '1Q Points': '1Q_PTS',
    '1Q Assists': '1Q_AST', '1Q Rebounds': '1Q_REB', '1H Points': '1H_PTS',
    'Double Double': 'DD2', 'Triple Double': 'TD3'
};

type PlayerAvailabilityByDate = Record<number, Record<string, Record<string, Record<string, boolean>>>>;

function playerHasAvailableSportsbookPropForDate(
    availabilityByPlayer: PlayerAvailabilityByDate,
    playerId: number,
    statType: string,
    sportsbook: SportsbookId | string,
    gameDate?: string | null,
) {
    const availabilityBucket = availabilityByPlayer?.[playerId]?.[statType]?.[sportsbook] ?? {};
    const dateKey = gameDate || '__undated__';
    return Boolean(availabilityBucket[dateKey] || availabilityBucket.__undated__);
}

const CustomDropdown = ({ value, options, onChange, placeholder }: { value: string, options: { label: string, value: string, disabled?: boolean }[], onChange: (val: string) => void, placeholder?: string }) => {
    const [isOpen, setIsOpen] = useState(false);
    const dropdownRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const selectedLabel = options.find(o => o.value === value)?.label || placeholder || value;

    return (
        <div className="relative w-full" ref={dropdownRef}>
            <div
                className="w-full bg-bgElevation1 hover:bg-bgElevation2 text-white text-xs font-bold py-2 px-3 rounded-lg border border-transparent cursor-pointer flex justify-between items-center transition-colors"
                onClick={() => setIsOpen(!isOpen)}
            >
                <span className="truncate">{selectedLabel}</span>
                <ChevronDown className={`w-3.5 h-3.5 text-gray-500 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
            </div>
            {isOpen && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-bgElevation1 border border-borderMedium rounded-lg shadow-xl z-50 py-1">
                    {options.map((option) => (
                        <div
                            key={option.value}
                            className={`px-3 py-2 text-xs font-bold flex items-center justify-between ${option.disabled ? 'text-gray-600 cursor-not-allowed opacity-50' : 'cursor-pointer hover:bg-bgElevation2 ' + (value === option.value ? 'text-white' : 'text-gray-400')}`}
                            onClick={(e) => {
                                e.stopPropagation();
                                if (!option.disabled) {
                                    onChange(option.value);
                                    setIsOpen(false);
                                }
                            }}
                        >
                            <span>{option.label}</span>
                            {value === option.value && <Check className="w-3.5 h-3.5 text-gray-500" />}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

interface SidebarProps {
    isOpen?: boolean;
    onClose?: () => void;
    players: Player[];
    activePlayerId?: number;
    activeGameDate?: string | null;
    pendingPlayerId?: number;
    pendingGameDate?: string | null;
    activeSportsbook?: SportsbookId;
    propsAvailabilityByDate?: PlayerAvailabilityByDate;
    onSelectPlayer: (id: number, gameDate?: string | null) => void;
    onPrefetchPlayer?: (id: number, gameDate?: string | null) => void;
    activeTab?: string;
    onTabChange?: (tab: string) => void;
}

const RealTeamLogo = ({ teamId, tricode, sizeClass = "w-7 h-7" }: { teamId: number, tricode: string, sizeClass?: string }) => {
    return (
        <div className="flex flex-col items-center justify-center gap-1">
            <div className={`${sizeClass} flex items-center justify-center font-bold text-white overflow-hidden`}>
                <ImageWithFallback
                    src={`${ASSETS_BASE}/assets/team_logos/${teamId}.svg`}
                    fallbackComponent={<span className="text-[8px]">{tricode}</span>}
                    alt={tricode}
                    className="w-full h-full object-contain"
                />
            </div>
        </div>
    );
};

const PlayerRow = ({
    player,
    statFilter,
    gameDate,
    activeSportsbook = 'dk',
    isAvailable = false,
    isActive,
    isPending,
    onClick,
    onPrefetch,
}: {
    player: Player,
    statFilter: string,
    gameDate?: string | null,
    activeSportsbook?: SportsbookId,
    isAvailable?: boolean,
    isActive: boolean,
    isPending: boolean,
    onClick: () => void,
    onPrefetch?: () => void
}) => {
    const prefetchTimeoutRef = useRef<number | null>(null);
    const selectedProp = getSportsbookProp(player, statFilter, activeSportsbook, gameDate);
    const book = selectedProp?.book ?? null;
    const prop = selectedProp?.prop ?? null;
    const hasProp = !!prop;
    const isHydratingProp = isAvailable && !hasProp;
    const line = prop?.line;
    const displayBook = book ?? activeSportsbook;

    const logoFile =
        displayBook === "dk"
            ? "draftkings.webp"
            : displayBook === "fd"
                ? "fanduel.webp"
                : displayBook === "pp"
                    ? "prizepicks.webp"
                : null;

    const logoSrc = logoFile
        ? `${ASSETS_BASE}/assets/sportsbook_logos/${logoFile}`
        : null;
    // Basic placeholder logic for color, can be enhanced later
    const isPlusYellow = true;

    const formatOdds = (val: number | string | undefined) => {
        if (val === undefined || val === null) return '-';
        const num = Number(val);
        if (isNaN(num)) return String(val);
        return num > 0 ? `+${num}` : String(num);
    };
    const showPricedOdds = book !== 'pp' && prop?.over !== null && prop?.over !== undefined && prop?.under !== null && prop?.under !== undefined;

    useEffect(() => {
        return () => {
            if (prefetchTimeoutRef.current !== null) {
                window.clearTimeout(prefetchTimeoutRef.current);
            }
        };
    }, []);

    const cancelPrefetch = () => {
        if (prefetchTimeoutRef.current !== null) {
            window.clearTimeout(prefetchTimeoutRef.current);
            prefetchTimeoutRef.current = null;
        }
    };

    const schedulePrefetch = () => {
        if (!onPrefetch) {
            return;
        }

        cancelPrefetch();
        prefetchTimeoutRef.current = window.setTimeout(() => {
            prefetchTimeoutRef.current = null;
            onPrefetch();
        }, 140);
    };

    return (
        <div
            onClick={onClick}
            onMouseEnter={schedulePrefetch}
            onMouseLeave={cancelPrefetch}
            onFocus={schedulePrefetch}
            onBlur={cancelPrefetch}
            aria-busy={isPending}
            className={`flex items-center justify-between p-3 border-b border-borderMedium bg-bgElevation0 transition-colors group cursor-pointer first:rounded-t-none last:rounded-b-md ${isPending ? 'bg-bgElevation2/80' : isActive ? 'bg-bgElevation1' : 'hover:bg-bgElevation1'}`}
        >
            <div className="flex items-center gap-3">
                <div className="relative w-10 h-10 rounded-full border border-borderMedium overflow-hidden bg-bgElevation1 flex items-center justify-center">
                    <ImageWithFallback
                        src={`https://cdn.nba.com/headshots/nba/latest/260x190/${player.id}.png`}
                        fallbackSrc={`${ASSETS_BASE}/assets/player_headshots/${player.id}.png`}
                        alt={player.name}
                        className="w-full h-full object-cover transform scale-125 pt-1"
                    />
                </div>
                <div className="flex flex-col gap-1">
                    <span className={`text-[13px] font-bold leading-none ${isPending || isActive ? 'text-white' : 'text-gray-300 group-hover:text-white'}`}>{player.name}</span>

                    {hasProp ? (
                        <div className="flex items-center gap-2 mt-0.5">
                            <div className="w-3.5 h-3.5 rounded-[3px] overflow-hidden bg-white flex items-center justify-center">
                                {logoSrc && (
                                    <img
                                        src={logoSrc}
                                        alt={book}
                                        className="w-full h-full object-contain"
                                    />
                                )}
                            </div>
                            <span className="text-white font-bold font-chakra text-xs">{line}</span>
                            {showPricedOdds ? (
                                <div className="flex items-center gap-1">
                                    <div className="bg-bgElevation1 px-1.5 py-0.5 rounded text-[10px] font-bold border border-borderMedium">
                                        <span className="text-fgSubtle">O</span> <span className="text-green500 font-chakra">{formatOdds(prop?.over)}</span>
                                    </div>
                                    <div className="bg-bgElevation1 px-1.5 py-0.5 rounded text-[10px] font-bold border border-borderMedium">
                                        <span className="text-fgSubtle">U</span> <span className="text-red500 font-chakra">{formatOdds(prop?.under)}</span>
                                    </div>
                                </div>
                            ) : (
                                <></>
                            )}
                        </div>
                    ) : isHydratingProp ? (
                        <div className="flex items-center gap-2 mt-0.5">
                            <div className="w-3.5 h-3.5 rounded-[3px] overflow-hidden bg-white flex items-center justify-center">
                                {logoSrc && (
                                    <img
                                        src={logoSrc}
                                        alt={displayBook}
                                        className="w-full h-full object-contain"
                                    />
                                )}
                            </div>
                            <span className="text-white font-bold font-chakra text-xs">...</span>
                            <div className="bg-bgElevation1 px-1.5 py-0.5 rounded text-[10px] font-bold border border-borderMedium text-fgSubtle uppercase tracking-[0.12em]">
                                Updating
                            </div>
                        </div>
                    ) : (
                        <div className="flex items-center gap-1.5 bg-borderMedium px-2 py-1 rounded-[4px] text-[10px] font-bold text-neutral400 border border-transparent w-fit mt-0.5">
                            <Lock className="w-2.5 h-2.5" />
                            UNAVAILABLE
                        </div>
                    )}
                </div>
            </div>

            {/* Plus Button / Active Indicator */}
            <button className={`w-4 h-4 rounded-[2px] flex items-center justify-center ${isPending || isActive ? 'bg-blue500 text-white' : (isPlusYellow ? 'bg-yellow400 hover:bg-yellow-400 text-black' : 'bg-red500 text-white')} self-start mt-0.5`}>
                {isPending ? (
                    <div className="w-2.5 h-2.5 rounded-full border-2 border-white/40 border-t-white animate-spin" aria-hidden="true" />
                ) : isActive ? (
                    <LockOpen className="w-3 h-3" />
                ) : (
                    <Plus className="w-3 h-3 font-bold" strokeWidth={4} />
                )}
            </button>
        </div>
    )
}

interface ProcessedGame extends Game {
    players: Player[];
}

const GameCard: React.FC<{ game: ProcessedGame, isExpanded: boolean, onToggle: () => void, activePlayerId?: number, activeGameDate?: string | null, pendingPlayerId?: number, pendingGameDate?: string | null, activeSportsbook?: SportsbookId, propsAvailabilityByDate?: PlayerAvailabilityByDate, onSelectPlayer: (id: number, gameDate?: string | null) => void, onPrefetchPlayer?: (id: number, gameDate?: string | null) => void, statFilter: string }> = ({
    game, isExpanded, onToggle, activePlayerId, activeGameDate, pendingPlayerId, pendingGameDate, activeSportsbook, propsAvailabilityByDate = {}, onSelectPlayer, onPrefetchPlayer, statFilter
}) => {
    const getNickname = (name: string) => name ? name.split(' ').pop() : '';

    const gameTimeObj = game.game_time_utc ? new Date(game.game_time_utc) : new Date(game.game_date + 'T23:59:59Z');
    const hasValidGameTime = !Number.isNaN(gameTimeObj.getTime());
    const gameDay = hasValidGameTime
        ? gameTimeObj.toLocaleDateString(undefined, { weekday: 'short' })
        : '';
    const formattedTime = hasValidGameTime
        ? gameTimeObj.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
        : (game.game_time_et ? game.game_time_et.replace(' ET', '').replace(/^0/, '') : '');

    return (
        <div className={`transition-all duration-200 border rounded-xl overflow-hidden relative mb-2 ${isExpanded ? 'bg-bgElevation0 border-borderMedium' : 'bg-bgElevation0 hover:bg-bgElevation1 border-borderMedium'}`}>

            {/* Active Game Indicator Line */}
            {isExpanded && <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-blue500 z-10"></div>}

            {/* Game Header */}
            <div
                className={`py-3 px-4 flex items-center justify-between cursor-pointer ${isExpanded ? 'bg-neutral950 border-b border-borderMedium' : ''}`}
                onClick={onToggle}
            >
                <div className="flex items-center gap-2 w-full justify-between">
                    <div className="flex flex-col items-center gap-1.5 w-[70px]">
                        <RealTeamLogo teamId={game.away_team_id} tricode={game.away_team_tricode} sizeClass="w-7 h-7" />
                        <span className="text-xs text-white font-medium">{getNickname(game.away_team_name)}</span>
                    </div>

                    <div className="flex flex-col items-center min-w-[70px] gap-0.5">
                        {game.is_live ? (
                            <span className="flex items-center gap-1 text-green-500 text-xs font-bold">
                                <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                                LIVE
                            </span>
                        ) : game.is_final ? (
                            <span className="text-xs text-gray-500 font-medium">FINAL</span>
                        ) : (
                            <span className="text-xs text-gray-400 font-medium">{gameDay}</span>
                        )}

                        <span className={`${isExpanded ? 'text-[13px] text-white' : 'text-[13px] text-gray-300'} font-medium whitespace-nowrap`}>
                            {game.is_live || game.is_final
                                ? `${game.away_score} - ${game.home_score}`
                                : formattedTime
                            }
                        </span>
                    </div>

                    <div className="flex flex-col items-center gap-1.5 w-[70px]">
                        <RealTeamLogo teamId={game.home_team_id} tricode={game.home_team_tricode} sizeClass="w-7 h-7" />
                        <span className="text-xs text-white font-medium">{getNickname(game.home_team_name)}</span>
                    </div>
                </div>
            </div>

            {/* Expanded Player List */}
            {isExpanded && (
                <div className="flex flex-col pl-[3px]">
                    {game.players.length > 0 ? (
                        game.players.map(player => (
                            <div key={player.id} className="relative">
                                {/* Selected Player Blue Line */}
                                {player.id === activePlayerId && game.game_date === activeGameDate && <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-blue500 z-20"></div>}
                                {player.id === pendingPlayerId && game.game_date === pendingGameDate && player.id !== activePlayerId && <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-blue500/70 z-20 animate-pulse"></div>}
                                <PlayerRow
                                    player={player}
                                    statFilter={statFilter}
                                    gameDate={game.game_date}
                                    activeSportsbook={activeSportsbook}
                                    isAvailable={playerHasAvailableSportsbookPropForDate(
                                        propsAvailabilityByDate,
                                        player.id,
                                        statFilter,
                                        activeSportsbook ?? 'dk',
                                        game.game_date,
                                    )}
                                    isActive={player.id === activePlayerId && game.game_date === activeGameDate}
                                    isPending={player.id === pendingPlayerId && game.game_date === pendingGameDate && !(player.id === activePlayerId && game.game_date === activeGameDate)}
                                    onClick={() => onSelectPlayer(player.id, game.game_date)}
                                    onPrefetch={() => onPrefetchPlayer?.(player.id, game.game_date)}
                                />
                            </div>
                        ))
                    ) : (
                        <div className="p-4 text-center text-xs text-gray-500">
                            No players found matching filter.
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export const Sidebar: React.FC<SidebarProps> = ({
    isOpen = false, onClose, players, activePlayerId, onSelectPlayer,
    activeGameDate, pendingPlayerId, pendingGameDate, activeSportsbook = 'dk', propsAvailabilityByDate = {}, activeTab = 'Points', onTabChange = () => { }, onPrefetchPlayer
}) => {
    const statFilter = STAT_MAP[activeTab] || 'PTS';
    const [gameFilter, setGameFilter] = useState('All Games');
    const [scheduleData, setScheduleData] = useState<Game[]>([]);
    const [expandedGames, setExpandedGames] = useState<Record<string, boolean>>({});
    const [searchTerm, setSearchTerm] = useState('');

    // Fetch live game data
    useEffect(() => {
        if (!players.length) return;

        const propDates = Array.from(new Set(
            players.flatMap(p =>
                Object.values(p.props_by_date ?? {}).flatMap(statEntry =>
                    Object.values(statEntry as Record<string, Record<string, any>>).flatMap(bookEntry =>
                        Object.keys(bookEntry).filter(k => k !== '__undated__')
                    )
                )
            )
        )).sort();

        if (!propDates.length) return;

        if (USE_DB) {
            fetchDashboardGames(propDates)
                .then(({ games }) => {
                    const sorted = (games ?? []).sort((a: Game, b: Game) =>
                        new Date(a.game_time_utc).getTime() - new Date(b.game_time_utc).getTime()
                    );
                    setScheduleData(sorted);
                })
                .catch((error) => {
                    console.error('[api] games error:', error);
                });
        } else {
            fetchApiJson<Game[]>('/data/current/nba_dashboard_games.json')
                .then(data => {
                    const sortedGames = (data as Game[])
                        .filter(g => propDates.includes(g.game_date))
                        .sort((a, b) =>
                            new Date(a.game_time_utc).getTime() - new Date(b.game_time_utc).getTime()
                        );
                    setScheduleData(sortedGames);
                })
                .catch(err => console.error("Error loading schedule:", err));
        }
    }, [players]);

    // Dynamically generate prop options and game options
    const propOptions = useMemo(() => {
        const propSet = new Set<string>();
        players.forEach(p => {
            Object.keys(p.props ?? {}).forEach(key => propSet.add(key));
            Object.keys(p.props_by_date ?? {}).forEach(key => propSet.add(key));
        });

        // Exact tabs from Header
        const TAB_ORDER = [
            'Points', 'Assists', 'Rebounds', 'Threes', 'Pts+Ast', 'Pts+Reb', 'Reb+Ast',
            'Pts+Reb+Ast', 'Double Double', 'Triple Double', '1Q Points', '1Q Assists',
            '1Q Rebounds', '1H Points', 'Blocks', 'Steals', 'Turnovers', 'Fantasy'
        ];

        return TAB_ORDER.map(label => {
            const key = STAT_MAP[label] || label;
            return {
                label,
                value: label,
                disabled: !propSet.has(key) // Greyed out if no players have the line
            };
        });
    }, [players]);

    const gameOptions = useMemo(() => {
        const options = [{ label: 'All Games', value: 'All Games' }];
        scheduleData.forEach(game => {
            options.push({
                label: `${game.away_team_tricode}-${game.home_team_tricode}`,
                value: game.game_id
            });
        });
        return options;
    }, [scheduleData]);

    // Group active players into the schedule with Filters
    const processedGames = useMemo(() => {
        if (!scheduleData.length || !players.length) return [];

        // 1. Game Filter
        let filteredSchedule = scheduleData;
        if (gameFilter !== 'All Games') {
            filteredSchedule = scheduleData.filter(g => g.game_id === gameFilter);
        }

        // 2. Process Games & Players
        const result: ProcessedGame[] = [];

        filteredSchedule.forEach(game => {
            // Check if Game matches Search (Team Name)
            const gameMatchesSearch = !searchTerm ||
                game.home_team_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                game.away_team_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                game.home_team_tricode.toLowerCase().includes(searchTerm.toLowerCase()) ||
                game.away_team_tricode.toLowerCase().includes(searchTerm.toLowerCase());

            // Find players for this game who HAVE the prop
            const gamePlayers = players.filter(p => {
                // Team Match
                const isInGame = p.team === game.home_team_tricode || p.team === game.away_team_tricode;
                if (!isInGame) return false;

                // Prop Match
                const hasResolvedProp = playerHasSportsbookPropForDate(p, statFilter, activeSportsbook, game.game_date);
                const hasAvailableProp = playerHasAvailableSportsbookPropForDate(
                    propsAvailabilityByDate,
                    p.id,
                    statFilter,
                    activeSportsbook,
                    game.game_date,
                );
                if (!hasResolvedProp && !hasAvailableProp) return false;

                // Search Match (Player Name)
                if (searchTerm && !gameMatchesSearch) {
                    // If game didn't match, Player MUST match
                    return p.name.toLowerCase().includes(searchTerm.toLowerCase());
                }

                return true;
            });

            if (gamePlayers.length > 0) {
                result.push({ ...game, players: gamePlayers });
            } else if (gameMatchesSearch && searchTerm) {
                // Show game if it matches search even if no players (optional UX choice)
            }
        });

        // Auto-expand games if searching
        if (searchTerm) {
            const newExpanded: Record<string, boolean> = {};
            result.forEach(g => newExpanded[g.game_id] = true);
            // Side-effect in render is strict mode safe? Better to do in effect, but this logic is derived.
            // We'll rely on user interaction or initial state mostly.
        }

        return result;
    }, [scheduleData, players, statFilter, gameFilter, searchTerm, activeSportsbook, propsAvailabilityByDate]);

    const toggleGame = (gameId: string) => {
        setExpandedGames(prev => ({
            ...prev,
            [gameId]: !prev[gameId]
        }));
    };

    return (
        <>
            {/* Sidebar Container 
                - Mobile: Fixed z-index for slide-over
                - Desktop: Static flex column
                - Visuals: Dark background, border right
            */}
            <div className={`
                fixed inset-y-0 left-0 z-[60] w-[300px] bg-bgElevation0 overflow-hidden
                transform transition-transform duration-300 ease-in-out
                ${isOpen ? 'translate-x-0' : '-translate-x-full'}

                lg:static lg:inset-auto lg:translate-x-0 
                lg:flex lg:flex-col lg:z-0
                lg:sticky lg:top-4 lg:h-[calc(100vh-5rem)] lg:self-start lg:shrink-0 lg:min-w-[300px] lg:basis-[300px]

                flex flex-col gap-3 p-4
            `}>

                {/* Mobile Close Button */}
                <button
                    onClick={onClose}
                    className="lg:hidden absolute top-4 right-4 p-1 text-gray-400 hover:text-white"
                >
                    <X className="w-5 h-5" />
                </button>

                {/* Filters Row */}
                <div className="flex gap-2 mb-1 lg:mt-0 mt-8 shrink-0">
                    <div className="flex-1 w-1/2">
                        <CustomDropdown value={activeTab} options={propOptions} onChange={onTabChange} placeholder="Select Prop" />
                    </div>
                    <div className="flex-1 w-1/2">
                        <CustomDropdown value={gameFilter} options={gameOptions} onChange={setGameFilter} placeholder="All Games" />
                    </div>
                </div>

                {/* Search */}
                <div className="relative shrink-0">
                    <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                    <input
                        type="text"
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        placeholder="Search players or teams..."
                        className="w-full bg-bgElevation1 text-xs font-medium text-white placeholder-gray-500 py-2 pl-9 pr-4 rounded-lg border-transparent focus:outline-none focus:border-gray-600 transition-colors"
                    />
                </div>

                {/* Game List */}
                <div className="min-h-0 flex-1 overflow-y-auto space-y-2 pr-1 custom-scrollbar -mr-2">
                    {processedGames.length === 0 && (
                        <div className="text-center py-8 text-gray-600 text-xs">
                            {searchTerm ? 'No matches found.' : 'Loading games...'}
                        </div>
                    )}

                    {processedGames.map((game) => {
                        const isExpanded = expandedGames[game.game_id] || (searchTerm.length > 0);

                        return (
                            <GameCard
                                key={game.game_id}
                                game={game}
                                isExpanded={!!isExpanded}
                                onToggle={() => toggleGame(game.game_id)}
                                activePlayerId={activePlayerId}
                                activeGameDate={activeGameDate}
                                pendingPlayerId={pendingPlayerId}
                                pendingGameDate={pendingGameDate}
                                activeSportsbook={activeSportsbook}
                                propsAvailabilityByDate={propsAvailabilityByDate}
                                onSelectPlayer={onSelectPlayer}
                                onPrefetchPlayer={onPrefetchPlayer}
                                statFilter={statFilter}
                            />
                        );
                    })}
                </div>
            </div>
        </>
    );
};
