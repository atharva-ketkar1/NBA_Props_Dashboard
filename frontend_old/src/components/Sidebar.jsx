import React, { useState, useEffect, useMemo } from 'react';
import { Search, ChevronDown, Lock, LockOpen } from 'lucide-react';

const STAT_FILTERS = [
    { label: 'Points', key: 'PTS' },
    { label: 'Assists', key: 'AST' },
    { label: 'Rebounds', key: 'REB' },
    { label: 'Threes', key: 'FG3M' },
    { label: 'Fantasy', key: 'FAN' },
    { label: 'PRA', key: 'PTS+REB+AST' }
];

const Sidebar = ({ players = [], activePlayerId, onSelectPlayer }) => {
    const [statFilter, setStatFilter] = useState('PTS');
    const [scheduleData, setScheduleData] = useState([]);
    const [expandedGames, setExpandedGames] = useState({});
    const [searchTerm, setSearchTerm] = useState('');

    // Fetch live game data
    useEffect(() => {
        fetch('http://localhost:5000/data/current/nba_dashboard_games.json')
            .then(res => res.json())
            .then(data => {
                // Sort games by time
                const sortedGames = data.sort((a, b) => {
                    return new Date(a.game_time_utc) - new Date(b.game_time_utc);
                });
                setScheduleData(sortedGames);
            })
            .catch(err => console.error("Error loading schedule:", err));
    }, []);

    // Group active players into the schedule
    const processedGames = useMemo(() => {
        if (!scheduleData.length || !players.length) return [];

        // Clone schedule to avoid mutation and add players array
        const games = scheduleData.map(g => ({ ...g, players: [] }));

        // Assign players to games based on Team Tricode matching
        players.forEach(player => {
            // Find game where player's team matches Home or Away Tricode
            const game = games.find(g =>
                g.home_team_tricode === player.team ||
                g.away_team_tricode === player.team
            );

            const hasProp = player.props && (
                (statFilter === 'Points' && player.props.PTS) ||
                (statFilter === 'Assists' && player.props.AST) ||
                (statFilter === 'Rebounds' && player.props.REB) ||
                (statFilter === 'Threes' && player.props.FG3M) ||
                (statFilter === 'PRA' && player.props['PTS+REB+AST']) ||
                // Fallback for default
                player.props[statFilter]
            );

            if (game && hasProp) {
                game.players.push(player);
            }
        });

        return games;
    }, [scheduleData, players, statFilter]);


    // Toggle Accordion
    const toggleGame = (gameId) => {
        setExpandedGames(prev => ({
            ...prev,
            [gameId]: !prev[gameId]
        }));
    };

    return (
        <div className="w-full h-full bg-[#09090b] border-r border-white/10 flex flex-col font-sans select-none">

            {/* Header Section */}
            <div className="p-3 space-y-2.5 border-b border-white/10 bg-black">
                {/* Filter Dropdowns */}
                <div className="flex gap-2">
                    <div className="relative flex-1">
                        <select
                            value={statFilter}
                            onChange={(e) => setStatFilter(e.target.value)}
                            className="w-full bg-[#18181b] text-white text-[10px] font-bold py-2 px-3 rounded border border-white/10 appearance-none cursor-pointer hover:bg-white/5 transition-colors pr-7 outline-none focus:border-blue-500/50"
                        >
                            {STAT_FILTERS.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
                        </select>
                        <ChevronDown size={12} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none" />
                    </div>

                    <div className="relative flex-1">
                        <select
                            className="w-full bg-[#18181b] text-white text-[10px] font-bold py-2 px-3 rounded border border-white/10 appearance-none cursor-pointer hover:bg-white/5 transition-colors pr-7 outline-none focus:border-blue-500/50"
                        >
                            <option>All Games</option>
                            <option>Today</option>
                            <option>Tomorrow</option>
                        </select>
                        <ChevronDown size={12} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none" />
                    </div>
                </div>

                {/* Search Input */}
                <div className="relative">
                    <Search size={12} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                    <input
                        type="text"
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        placeholder="Search players or teams..."
                        className="w-full bg-[#18181b] text-[11px] text-gray-300 pl-8 pr-3 py-2 rounded border border-white/10 focus:outline-none focus:border-gray-600 focus:ring-1 focus:ring-gray-600/50 placeholder-gray-600 transition-all"
                    />
                </div>
            </div>

            {/* Games List */}
            <div className="flex-1 overflow-y-auto custom-scrollbar">
                {processedGames.length === 0 && (
                    <div className="text-center py-8 text-gray-600 text-xs">
                        Loading games... (Check ./backend/data/current/nba_dashboard_games.json)
                    </div>
                )}

                {processedGames.map((game) => {
                    const isExpanded = expandedGames[game.game_id];
                    // Filter players inside the game by search term
                    const visiblePlayers = game.players.filter(p =>
                        p.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                        p.team.toLowerCase().includes(searchTerm.toLowerCase())
                    );

                    // Search Logic: Show game if it matches term OR has players matching
                    const gameMatches =
                        game.away_team_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                        game.home_team_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                        game.away_team_tricode.toLowerCase().includes(searchTerm.toLowerCase()) ||
                        game.home_team_tricode.toLowerCase().includes(searchTerm.toLowerCase());

                    if (searchTerm && visiblePlayers.length === 0 && !gameMatches) {
                        return null;
                    }

                    // Format Time/Status
                    // game_status_text is like "3:00 pm ET" or "Final" or "Q1 10:00"
                    // If live, use green status. If scheduled, use time.
                    const statusColor = game.is_live ? 'text-green-500' : 'text-gray-500';
                    const periodText = game.is_live ? `Q${game.period} ${game.game_clock}` : game.game_status_text;

                    return (
                        <div key={game.game_id} className="border-b border-white/5">
                            {/* Game Header / Accordion Trigger */}
                            <div
                                onClick={() => toggleGame(game.game_id)}
                                className={`
                                    p-3 flex items-center justify-between cursor-pointer transition-colors group
                                    ${isExpanded ? 'bg-white/5' : 'hover:bg-white/5'}
                                `}
                            >
                                {/* Matchup Display */}
                                <div className="flex items-center justify-between w-full px-2">
                                    {/* Away */}
                                    <div className="flex flex-col items-center gap-1 w-14">
                                        <img
                                            src={`http://localhost:5000/assets/team_logos/${game.away_team_id}.svg`}
                                            alt={game.away_team_tricode}
                                            className="w-6 h-6 object-contain"
                                            onError={(e) => e.target.style.display = 'none'}
                                        />
                                        <div className="flex flex-col items-center">
                                            <span className="text-[10px] font-bold text-gray-400 group-hover:text-white transition-colors">
                                                {game.away_team_tricode}
                                            </span>
                                            {/* Show score if live or final */}
                                            {(game.is_live || game.is_final) && (
                                                <span className="text-[10px] font-mono text-white">{game.away_score}</span>
                                            )}
                                        </div>
                                    </div>

                                    {/* Time / Status center */}
                                    <div className="flex flex-col items-center gap-0.5">
                                        {game.is_final ? (
                                            <span className="text-[9px] font-bold text-gray-500">FINAL</span>
                                        ) : (
                                            <span className={`text-[9px] font-bold ${statusColor} uppercase`}>
                                                {periodText}
                                            </span>
                                        )}
                                        {!game.is_live && !game.is_final && (
                                            <span className="text-[9px] font-bold text-gray-600 uppercase">{game.game_weekday?.substring(0, 3)}</span>
                                        )}
                                    </div>

                                    {/* Home */}
                                    <div className="flex flex-col items-center gap-1 w-14">
                                        <img
                                            src={`http://localhost:5000/assets/team_logos/${game.home_team_id}.svg`}
                                            alt={game.home_team_tricode}
                                            className="w-6 h-6 object-contain"
                                            onError={(e) => e.target.style.display = 'none'}
                                        />
                                        <div className="flex flex-col items-center">
                                            <span className="text-[10px] font-bold text-gray-400 group-hover:text-white transition-colors">
                                                {game.home_team_tricode}
                                            </span>
                                            {(game.is_live || game.is_final) && (
                                                <span className="text-[10px] font-mono text-white">{game.home_score}</span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Player List (Expanded) */}
                            {isExpanded && (
                                <div className="bg-[#0c0c0e] py-1 border-t border-white/5">
                                    {visiblePlayers.length > 0 ? (
                                        visiblePlayers.map(player => {
                                            const isSelected = activePlayerId === player.id;
                                            return (
                                                <div
                                                    key={player.id}
                                                    onClick={() => onSelectPlayer(player.id)}
                                                    className={`
                                                        mx-2 my-1 p-2 rounded-md flex items-center justify-between cursor-pointer transition-all border
                                                        ${isSelected
                                                            ? 'bg-blue-500/10 border-blue-500/30 ring-1 ring-blue-500/20'
                                                            : 'border-transparent hover:bg-white/5 hover:border-white/10'}
                                                    `}
                                                >
                                                    {/* Avatar & Name */}
                                                    <div className="flex items-center gap-3">
                                                        <div className={`
                                                            w-8 h-8 rounded-full overflow-hidden border 
                                                            ${isSelected ? 'border-blue-500' : 'border-[#27272a]'}
                                                        `}>
                                                            <img
                                                                src={`http://localhost:5000/assets/player_headshots/${player.id}.png`}
                                                                alt={player.name}
                                                                className="w-full h-full object-cover transform scale-125 pt-1"
                                                                onError={(e) => e.target.style.opacity = 0}
                                                            />
                                                        </div>
                                                        <div className="flex flex-col">
                                                            <span className={`text-[11px] font-bold ${isSelected ? 'text-white' : 'text-gray-300'}`}>
                                                                {player.name}
                                                            </span>
                                                            <div className="flex items-center gap-1">
                                                                <span className="text-[9px] text-gray-500 font-medium">
                                                                    {player.props[statFilter] ? 'Available' : 'No Prop'}
                                                                </span>
                                                            </div>
                                                        </div>
                                                    </div>

                                                    {/* Unlock Button / Action */}
                                                    <button className={`
                                                        px-2 py-1 rounded text-[9px] font-black uppercase tracking-wide flex items-center gap-1 transition-all
                                                        ${isSelected
                                                            ? 'bg-blue-500 text-white shadow-lg shadow-blue-500/20'
                                                            : 'bg-[#18181b] text-gray-400 border border-[#27272a] hover:text-white hover:border-gray-600'}
                                                    `}>
                                                        {isSelected ? (
                                                            <>
                                                                <LockOpen size={8} /> Active
                                                            </>
                                                        ) : (
                                                            <>
                                                                <Lock size={8} /> Unlock
                                                            </>
                                                        )}
                                                    </button>
                                                </div>
                                            );
                                        })
                                    ) : (
                                        <div className="py-3 text-center text-[10px] text-gray-600">
                                            No players found.
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* Custom Scrollbar Styles */}
            <style type="text/css">{`
                .custom-scrollbar::-webkit-scrollbar {
                    width: 4px;
                }
                .custom-scrollbar::-webkit-scrollbar-track {
                    background: transparent;
                }
                .custom-scrollbar::-webkit-scrollbar-thumb {
                    background: #27272a;
                    border-radius: 2px;
                }
                .custom-scrollbar::-webkit-scrollbar-thumb:hover {
                    background: #3f3f46;
                }
            `}</style>
        </div>
    );
};

export default Sidebar;