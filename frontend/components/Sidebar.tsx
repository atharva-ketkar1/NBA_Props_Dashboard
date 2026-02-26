import React, { useState, useEffect, useMemo } from 'react';
import { ChevronDown, Search, Lock, Plus, LockOpen, X } from 'lucide-react';
import { Player, Game } from '../types';
import { ImageWithFallback } from './ui/ImageWithFallback';

const STAT_FILTERS = [
    { label: 'Points', key: 'PTS' },
    { label: 'Assists', key: 'AST' },
    { label: 'Rebounds', key: 'REB' },
    { label: 'Threes', key: 'FG3M' },
    { label: 'Fantasy', key: 'FAN' },
    { label: 'PRA', key: 'PTS+REB+AST' }
];

interface SidebarProps {
    isOpen?: boolean;
    onClose?: () => void;
    players: Player[];
    activePlayerId?: number;
    onSelectPlayer: (id: number) => void;
}

const RealTeamLogo = ({ teamId, tricode, sizeClass = "w-7 h-7" }: { teamId: number, tricode: string, sizeClass?: string }) => {
    return (
        <div className="flex flex-col items-center justify-center gap-1">
            <div className={`${sizeClass} flex items-center justify-center font-bold text-white overflow-hidden`}>
                <ImageWithFallback
                    src={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${teamId}.svg`}
                    fallbackComponent={<span className="text-[8px]">{tricode}</span>}
                    alt={tricode}
                    className="w-full h-full object-contain"
                />
            </div>
        </div>
    );
};

const PlayerRow = ({ player, statFilter, isActive, onClick }: { player: Player, statFilter: string, isActive: boolean, onClick: () => void }) => {
    const book =
        player.props?.[statFilter]?.['dk']
            ? 'dk'
            : player.props?.[statFilter]?.['fd']
                ? 'fd'
                : null;

    const prop = book ? player.props?.[statFilter]?.[book] : null;
    const hasProp = !!prop;
    const line = prop?.line;

    const logoFile =
        book === "dk"
            ? "draftkings.webp"
            : book === "fd"
                ? "fanduel.webp"
                : null;

    const logoSrc = logoFile
        ? `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/sportsbook_logos/${logoFile}`
        : null;
    // Basic placeholder logic for color, can be enhanced later
    const isPlusYellow = true;

    return (
        <div
            onClick={onClick}
            className={`flex items-center justify-between p-3 border-b border-borderMedium bg-bgElevation0 hover:bg-bgElevation1 transition-colors group cursor-pointer first:rounded-t-none last:rounded-b-md ${isActive ? 'bg-bgElevation1' : ''}`}
        >
            <div className="flex items-center gap-3">
                <div className="relative w-10 h-10 rounded-full border border-borderMedium overflow-hidden bg-bgElevation1">
                    <ImageWithFallback
                        src={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/player_headshots/${player.id}.png`}
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
                            <span className="text-white font-bold text-xs">{line}</span>
                            <div className="flex items-center gap-1">
                                <div className="bg-bgElevation1 px-1.5 py-0.5 rounded text-[10px] font-bold border border-borderMedium">
                                    <span className="text-fgSubtle">O</span> <span className="text-green500">{prop?.over || '-'}</span>
                                </div>
                                <div className="bg-bgElevation1 px-1.5 py-0.5 rounded text-[10px] font-bold border border-borderMedium">
                                    <span className="text-fgSubtle">U</span> <span className="text-red500">{prop?.under || '-'}</span>
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

const GameCard: React.FC<{ game: ProcessedGame, isExpanded: boolean, onToggle: () => void, activePlayerId?: number, onSelectPlayer: (id: number) => void, statFilter: string }> = ({
    game, isExpanded, onToggle, activePlayerId, onSelectPlayer, statFilter
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
                                {player.id === activePlayerId && <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-blue500 z-20"></div>}
                                <PlayerRow
                                    player={player}
                                    statFilter={statFilter}
                                    isActive={player.id === activePlayerId}
                                    onClick={() => onSelectPlayer(player.id)}
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

export const Sidebar: React.FC<SidebarProps> = ({ isOpen = false, onClose, players, activePlayerId, onSelectPlayer }) => {
    const [statFilter, setStatFilter] = useState('PTS');
    const [timeFilter, setTimeFilter] = useState('All Games');
    const [scheduleData, setScheduleData] = useState<Game[]>([]);
    const [expandedGames, setExpandedGames] = useState<Record<string, boolean>>({});
    const [searchTerm, setSearchTerm] = useState('');

    // Fetch live game data
    useEffect(() => {
        const apiUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000';
        fetch(`${apiUrl}/data/current/nba_dashboard_games.json`)
            .then(res => res.json())
            .then(data => {
                const sortedGames = data.sort((a: Game, b: Game) => {
                    return new Date(a.game_time_utc).getTime() - new Date(b.game_time_utc).getTime();
                });
                setScheduleData(sortedGames);
            })
            .catch(err => console.error("Error loading schedule:", err));
    }, []);

    // Group active players into the schedule with Filters
    const processedGames = useMemo(() => {
        if (!scheduleData.length || !players.length) return [];

        // 1. Time Filter
        let filteredSchedule = scheduleData;
        if (timeFilter === 'Today') {
            const now = new Date();
            const localYMD = now.toLocaleDateString('en-CA'); // YYYY-MM-DD
            filteredSchedule = scheduleData.filter(g => g.game_date === localYMD);
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
                const hasProp = p.props && p.props[statFilter];
                if (!hasProp) return false;

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
    }, [scheduleData, players, statFilter, timeFilter, searchTerm]);

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
                fixed inset-y-0 left-0 z-[60] w-[300px] bg-bgElevation0 
                transform transition-transform duration-300 ease-in-out
                ${isOpen ? 'translate-x-0' : '-translate-x-full'}

                lg:static lg:inset-auto lg:translate-x-0 
                lg:flex lg:flex-col lg:z-0
                
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
                    <div className="flex-1 relative">
                        <select
                            value={statFilter}
                            onChange={(e) => setStatFilter(e.target.value)}
                            className="w-full bg-bgElevation1 hover:bg-bgElevation2 text-white text-xs font-bold py-2 px-3 rounded-lg border-transparent appearance-none cursor-pointer outline-none focus:border-blue-500"
                        >
                            {STAT_FILTERS.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
                        </select>
                        <ChevronDown className="w-3.5 h-3.5 text-gray-500 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                    </div>

                    <div className="flex-1 relative">
                        <select
                            value={timeFilter}
                            onChange={(e) => setTimeFilter(e.target.value)}
                            className="w-full bg-bgElevation1 hover:bg-bgElevation2 text-white text-xs font-bold py-2 px-3 rounded-lg border-transparent appearance-none cursor-pointer outline-none focus:border-blue-500"
                        >
                            <option value="All Games">All Games</option>
                            <option value="Today">Today</option>
                        </select>
                        <ChevronDown className="w-3.5 h-3.5 text-gray-500 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
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
                <div className="flex-1 overflow-y-auto space-y-2 pr-1 custom-scrollbar -mr-2">
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