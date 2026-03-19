import React, { useState, useEffect, useMemo, useRef } from 'react';
import { ChevronDown, Search, Lock, Plus, LockOpen, X, Check } from 'lucide-react';
import { Player, Game } from '../types';
import { ImageWithFallback } from './ui/ImageWithFallback';
import { ASSETS_BASE } from '../utils/config';
import { getPreferredSportsbookProp, playerHasPropForDate } from '../utils/propResolution';

const USE_DB = import.meta.env.VITE_USE_DB === 'true';

const STAT_MAP: Record<string, string> = {
    'Points': 'PTS', 'Assists': 'AST', 'Rebounds': 'REB', 'Threes': 'FG3M',
    'Pts+Ast': 'PTS+AST', 'Pts+Reb': 'PTS+REB', 'Reb+Ast': 'REB+AST',
    'Pts+Reb+Ast': 'PTS+REB+AST', 'Fantasy': 'FAN', 'Blocks': 'BLK',
    'Steals': 'STL', 'Turnovers': 'TOV', '1Q Points': '1Q_PTS',
    '1Q Assists': '1Q_AST', '1Q Rebounds': '1Q_REB', '1H Points': '1H_PTS',
    'Double Double': 'DD2', 'Triple Double': 'TD3'
};

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
    onSelectPlayer: (id: number, gameDate?: string | null) => void;
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

const PlayerRow = ({ player, statFilter, gameDate, isActive, onClick }: { player: Player, statFilter: string, gameDate?: string | null, isActive: boolean, onClick: () => void }) => {
    const preferredProp = getPreferredSportsbookProp(player, statFilter, gameDate);
    const book = preferredProp?.book ?? null;
    const prop = preferredProp?.prop ?? null;
    const hasProp = !!prop;
    const line = prop?.line;

    const logoFile =
        book === "dk"
            ? "draftkings.webp"
            : book === "fd"
                ? "fanduel.webp"
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

    return (
        <div
            onClick={onClick}
            className={`flex items-center justify-between p-3 border-b border-borderMedium bg-bgElevation0 hover:bg-bgElevation1 transition-colors group cursor-pointer first:rounded-t-none last:rounded-b-md ${isActive ? 'bg-bgElevation1' : ''}`}
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
                    <span className={`text-[13px] font-bold leading-none ${isActive ? 'text-white' : 'text-gray-300 group-hover:text-white'}`}>{player.name}</span>

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
                            <div className="flex items-center gap-1">
                                <div className="bg-bgElevation1 px-1.5 py-0.5 rounded text-[10px] font-bold border border-borderMedium">
                                    <span className="text-fgSubtle">O</span> <span className="text-green500 font-chakra">{formatOdds(prop?.over)}</span>
                                </div>
                                <div className="bg-bgElevation1 px-1.5 py-0.5 rounded text-[10px] font-bold border border-borderMedium">
                                    <span className="text-fgSubtle">U</span> <span className="text-red500 font-chakra">{formatOdds(prop?.under)}</span>
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div className="flex items-center gap-1.5 bg-borderMedium px-2 py-1 rounded-[4px] text-[10px] font-bold text-neutral400 border border-transparent w-fit mt-0.5">
                            <Lock className="w-2.5 h-2.5" />
                            UNLOCK
                        </div>
                    )}
                </div>
            </div>

            {/* Plus Button / Active Indicator */}
            <button className={`w-4 h-4 rounded-[2px] flex items-center justify-center ${isActive ? 'bg-blue500 text-white' : (isPlusYellow ? 'bg-yellow400 hover:bg-yellow-400 text-black' : 'bg-red500 text-white')} self-start mt-0.5`}>
                {isActive ? <LockOpen className="w-3 h-3" /> : <Plus className="w-3 h-3 font-bold" strokeWidth={4} />}
            </button>
        </div>
    )
}

interface ProcessedGame extends Game {
    players: Player[];
}

const GameCard: React.FC<{ game: ProcessedGame, isExpanded: boolean, onToggle: () => void, activePlayerId?: number, activeGameDate?: string | null, onSelectPlayer: (id: number, gameDate?: string | null) => void, statFilter: string }> = ({
    game, isExpanded, onToggle, activePlayerId, activeGameDate, onSelectPlayer, statFilter
}) => {
    const getNickname = (name: string) => name ? name.split(' ').pop() : '';

    // We use UTC time to construct a localized Date object, then generate the short weekday
    const gameTimeObj = game.game_time_utc ? new Date(game.game_time_utc) : new Date(game.game_date + 'T23:59:59Z');
    // For consistency with ET scheduling, checking day
    const gameDay = gameTimeObj.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'America/New_York' });

    // Clean up time string: "07:30 PM ET" -> "7:30 PM"
    const formattedTime = game.game_time_et ? game.game_time_et.replace(' ET', '').replace(/^0/, '') : '';

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
                            <span className="text-green-500 animate-pulse text-xs font-bold">LIVE</span>
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
                                <PlayerRow
                                    player={player}
                                    statFilter={statFilter}
                                    gameDate={game.game_date}
                                    isActive={player.id === activePlayerId && game.game_date === activeGameDate}
                                    onClick={() => onSelectPlayer(player.id, game.game_date)}
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
    activeGameDate, activeTab = 'Points', onTabChange = () => { }
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
            import('../utils/supabase').then(({ supabase }) => {
                supabase
                    .from('games')
                    .select('*')
                    .in('game_date', propDates)
                    .then(({ data, error }) => {
                        if (error) { console.error('[supabase] games error:', error); return; }
                        const sorted = (data ?? []).sort((a: Game, b: Game) =>
                            new Date(a.game_time_utc).getTime() - new Date(b.game_time_utc).getTime()
                        );
                        setScheduleData(sorted);
                    });
            });
        } else {
            const apiUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';
            fetch(`${apiUrl}/data/current/nba_dashboard_games.json`)
                .then(res => res.json())
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
                if (!playerHasPropForDate(p, statFilter, game.game_date)) return false;

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
    }, [scheduleData, players, statFilter, gameFilter, searchTerm]);

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
                lg:sticky lg:top-4 lg:h-[calc(100vh-5rem)] lg:self-start

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
                                onSelectPlayer={onSelectPlayer}
                                statFilter={statFilter}
                            />
                        );
                    })}
                </div>
            </div>
        </>
    );
};
