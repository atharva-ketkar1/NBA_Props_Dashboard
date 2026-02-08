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

// Optimized TeamLogo that takes an ID
const RealTeamLogo = ({ teamId, tricode, large = false }: { teamId: number, tricode: string, large?: boolean }) => {
    // Determine background color based on team tricode (Mock logic)
    // For now we use the dark default, or could enable the color map if desired.
    // Keeping it simple with the Mock's visual style.
    return (
        <div className="flex flex-col items-center justify-center gap-1">
            <div className={`${large ? 'w-10 h-10' : 'w-8 h-8'} rounded-full flex items-center justify-center font-bold text-white border border-white/10 overflow-hidden bg-[#18181b]`}>
                <ImageWithFallback
                    src={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/team_logos/${teamId}.svg`}
                    fallbackComponent={<span className="text-[8px]">{tricode}</span>}
                    alt={tricode}
                    className="w-full h-full object-contain p-1.5"
                />
            </div>
            <span className={`${large ? 'text-[10px]' : 'text-[9px]'} text-gray-400 font-bold tracking-wide mt-0.5`}>{tricode}</span>
        </div>
    );
};

const PlayerRow = ({ player, statFilter, isActive, onClick }: { player: Player, statFilter: string, isActive: boolean, onClick: () => void }) => {
    const prop = player.props?.[statFilter]?.['dk'] || player.props?.[statFilter]?.['fd'];
    const hasProp = !!prop;
    const line = prop?.line;
    // Basic placeholder logic for color, can be enhanced later
    const isPlusYellow = true;

    return (
        <div
            onClick={onClick}
            className={`flex items-center justify-between p-3 border-b border-[#27272a] bg-[#09090b] hover:bg-[#121214] transition-colors group cursor-pointer first:rounded-t-none last:rounded-b-md ${isActive ? 'bg-[#18181b]' : ''}`}
        >
            <div className="flex items-center gap-3">
                <div className="relative w-10 h-10 rounded-full border border-[#27272a] overflow-hidden bg-[#18181b]">
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
                            <div className="bg-[#007aff] w-3.5 h-3.5 rounded-[3px] flex items-center justify-center">
                                {/* Simple icon or just text */}
                                <span className="text-[8px] font-bold text-white">P</span>
                            </div>
                            <span className="text-white font-bold text-xs">{line}</span>
                            <div className="flex items-center gap-1">
                                <div className="bg-[#18181b] px-1.5 py-0.5 rounded text-[10px] font-bold border border-[#27272a]">
                                    <span className="text-[#71717a]">O</span> <span className="text-[#22c55e]">{prop?.over || '-'}</span>
                                </div>
                                <div className="bg-[#18181b] px-1.5 py-0.5 rounded text-[10px] font-bold border border-[#27272a]">
                                    <span className="text-[#71717a]">U</span> <span className="text-[#ef4444]">{prop?.under || '-'}</span>
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div className="flex items-center gap-1.5 bg-[#27272a] px-2 py-1 rounded-[4px] text-[10px] font-bold text-[#a1a1aa] border border-transparent w-fit mt-0.5">
                            <Lock className="w-2.5 h-2.5" />
                            UNLOCK
                        </div>
                    )}
                </div>
            </div>

            {/* Plus Button / Active Indicator */}
            <button className={`w-4 h-4 rounded-[2px] flex items-center justify-center ${isActive ? 'bg-[#007aff] text-white' : (isPlusYellow ? 'bg-[#facc15] hover:bg-yellow-400 text-black' : 'bg-[#ef4444] text-white')} self-start mt-0.5`}>
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
}) => (
    <div className={`transition-all duration-200 border rounded-lg overflow-hidden relative ${isExpanded ? 'bg-[#000000] border-[#27272a] mb-2' : 'bg-[#121214] hover:bg-[#1f1f22] border-transparent hover:border-border'}`}>

        {/* Active Game Indicator Line */}
        {isExpanded && <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-[#3b82f6] z-10"></div>}

        {/* Game Header */}
        <div
            className={`p-3 flex items-center justify-between cursor-pointer ${isExpanded ? 'bg-[#050505] pb-4 border-b border-[#27272a] pl-4' : ''}`}
            onClick={onToggle}
        >
            <div className="flex items-center gap-4 w-full justify-between">
                <div className="w-16 flex justify-center">
                    <RealTeamLogo teamId={game.away_team_id} tricode={game.away_team_tricode} large={isExpanded} />
                </div>

                <div className="flex flex-col items-center min-w-[80px]">
                    <span className={`${isExpanded ? 'text-xs text-[#a1a1aa] font-medium' : 'text-[10px] text-gray-500 font-medium'}`}>
                        {game.is_live ? <span className="text-green-500 animate-pulse">LIVE</span> : (isExpanded ? 'Vs' : '@')}
                    </span>
                    <span className={`${isExpanded ? 'text-sm text-white' : 'text-xs text-gray-300'} font-bold whitespace-nowrap`}>
                        {game.is_live || game.is_final
                            ? `${game.away_score} - ${game.home_score}`
                            : game.game_status_text
                        }
                    </span>
                    {!game.is_live && !game.is_final && (
                        <span className="text-[10px] text-[#71717a] font-medium mt-0.5">
                            {game.game_time_et}
                        </span>
                    )}
                </div>

                <div className="w-16 flex justify-center">
                    <RealTeamLogo teamId={game.home_team_id} tricode={game.home_team_tricode} large={isExpanded} />
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
                            {player.id === activePlayerId && <div className="absolute left-0 top-0 bottom-0 w-[3px] bg-[#3b82f6] z-20"></div>}
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
            {/* 
                Sidebar Container 
                - Mobile: Fixed z-index for slide-over
                - Desktop: Static flex column
                - Visuals: Dark background, border right
            */}
            <div className={`
                fixed inset-y-0 left-0 z-[60] w-[300px] bg-[#050505] 
                transform transition-transform duration-300 ease-in-out
                ${isOpen ? 'translate-x-0' : '-translate-x-full'}

                lg:static lg:inset-auto lg:translate-x-0 
                lg:flex lg:flex-col lg:z-0
                
                border-r border-[#27272a]
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
                            className="w-full bg-[#121214] hover:bg-[#1f1f22] text-white text-xs font-bold py-2 px-3 rounded-lg border border-[#27272a] appearance-none cursor-pointer outline-none focus:border-blue-500"
                        >
                            {STAT_FILTERS.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
                        </select>
                        <ChevronDown className="w-3.5 h-3.5 text-gray-500 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
                    </div>

                    <div className="flex-1 relative">
                        <select
                            value={timeFilter}
                            onChange={(e) => setTimeFilter(e.target.value)}
                            className="w-full bg-[#121214] hover:bg-[#1f1f22] text-white text-xs font-bold py-2 px-3 rounded-lg border border-[#27272a] appearance-none cursor-pointer outline-none focus:border-blue-500"
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
                        className="w-full bg-[#121214] text-xs font-medium text-white placeholder-gray-500 py-2.5 pl-9 pr-4 rounded-lg border border-[#27272a] focus:outline-none focus:border-gray-600 transition-colors"
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