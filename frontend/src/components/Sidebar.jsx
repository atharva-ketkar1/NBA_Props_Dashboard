import React, { useState, useEffect, useMemo } from 'react';
import { Search, ChevronDown, Lock, LockOpen, Plus } from 'lucide-react';

const TEAM_ID_MAP = {
    ATL: 1610612737, BOS: 1610612738, BKN: 1610612751, CHA: 1610612766,
    CHI: 1610612741, CLE: 1610612739, DAL: 1610612742, DEN: 1610612743,
    DET: 1610612765, GSW: 1610612744, HOU: 1610612745, IND: 1610612754,
    LAC: 1610612746, LAL: 1610612747, MEM: 1610612763, MIA: 1610612748,
    MIL: 1610612749, MIN: 1610612750, NOP: 1610612740, NYK: 1610612752,
    OKC: 1610612760, ORL: 1610612753, PHI: 1610612755, PHX: 1610612756,
    POR: 1610612757, SAC: 1610612758, SAS: 1610612759, TOR: 1610612761,
    UTA: 1610612762, WAS: 1610612764
};

// Map full team names from CSV to abbreviations
const TEAM_NAME_TO_ABBREV = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM", "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
    // CSV variations
    "ATL Hawks": "ATL", "BOS Celtics": "BOS", "BKN Nets": "BKN", "CHA Hornets": "CHA",
    "CHI Bulls": "CHI", "CLE Cavaliers": "CLE", "DAL Mavericks": "DAL", "DEN Nuggets": "DEN",
    "DET Pistons": "DET", "GS Warriors": "GSW", "HOU Rockets": "HOU", "IND Pacers": "IND",
    "MEM Grizzlies": "MEM", "MIA Heat": "MIA",
    "MIL Bucks": "MIL", "MIN Timberwolves": "MIN", "NO Pelicans": "NOP", "NY Knicks": "NYK",
    "OKC Thunder": "OKC", "ORL Magic": "ORL", "PHI 76ers": "PHI", "PHX Suns": "PHX",
    "POR Trail Blazers": "POR", "SAC Kings": "SAC", "SA Spurs": "SAS", "TOR Raptors": "TOR",
    "UTA Jazz": "UTA", "WAS Wizards": "WAS"
};

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

    // Fetch and parse CSV to get Schedule/Game data
    useEffect(() => {
        fetch('http://localhost:5000/data/current/draftkings.csv')
            .then(res => res.text())
            .then(csvText => {
                const rows = csvText.split('\n').slice(1);
                const gamesMap = {};

                // Parse CSV to build Game Objects
                rows.forEach(row => {
                    if (!row.trim()) return;
                    // CSV: player,team,prop_type,line,over_odds,under_odds,game,game_date,sportsbook
                    const cols = row.split(',');
                    const gameStr = cols[6]; // "NY Knicks @ DET Pistons"

                    if (gameStr && !gamesMap[gameStr]) {
                        const [awayName, homeName] = gameStr.split(' @ ');
                        const awayAbbrev = TEAM_NAME_TO_ABBREV[awayName] || awayName.substring(0, 3).toUpperCase();
                        const homeAbbrev = TEAM_NAME_TO_ABBREV[homeName] || homeName.substring(0, 3).toUpperCase();

                        // Determinisitic fake time based on game string length
                        const times = ['7:00 PM', '7:30 PM', '8:00 PM', '8:30 PM', '10:00 PM'];
                        const timeIndex = (gameStr.length + awayAbbrev.charCodeAt(0)) % times.length;

                        gamesMap[gameStr] = {
                            id: gameStr,
                            away: awayAbbrev,
                            home: homeAbbrev,
                            awayName: awayName,
                            homeName: homeName,
                            time: times[timeIndex],
                            date: 'Fri', // Mocking assume today is Fri as per screenshot
                            players: []
                        };
                    }
                });

                setScheduleData(Object.values(gamesMap));
            })
            .catch(err => console.error("Error loading schedule:", err));
    }, []);

    // Group active players into the schedule
    const processedGames = useMemo(() => {
        if (!scheduleData.length || !players.length) return [];

        // 1. Create a map of players by normalized name
        const playerMap = {};
        players.forEach(p => {
            playerMap[p.name.toLowerCase()] = p;
        });

        // 2. Clone schedule to avoid mutation
        const games = scheduleData.map(g => ({ ...g, players: [] }));
        const unassignedPlayers = [];

        // 3. Assign players to games based on Team ID or Team Abbrev matching
        players.forEach(player => {
            // Try to find the game where this player's team is either Home or Away
            const game = games.find(g => g.away === player.team || g.home === player.team);

            // Check if player has the prop for current filter
            // Logic: Checks if 'props[statFilter]' exists OR if statFilter is 'Points' checks 'PTS', etc.
            // Simplified: Just iterate all tabs logic from PlayerCard? 
            // For now, check if player.props[statFilter] exists
            // But wait, key in PlayerCard TABS map is different from labels.
            // Map label -> key
            // Points -> PTS

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
            } else if (!game) {
                unassignedPlayers.push(player); // Fallback if no game found (should cover untracked games)
            }
        });

        return games.filter(g => g.players.length > 0 || g.away); // Return all games derived from CSV
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
                        Loading games...
                    </div>
                )}

                {processedGames.map((game) => {
                    const isExpanded = expandedGames[game.id];
                    // Filter players inside the game by search term
                    const visiblePlayers = game.players.filter(p =>
                        p.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                        p.team.toLowerCase().includes(searchTerm.toLowerCase())
                    );

                    // If search logic requires hiding empty games, handle here. 
                    // For now show game if it matches term OR has players matching
                    if (searchTerm && visiblePlayers.length === 0 &&
                        !game.away.includes(searchTerm.toUpperCase()) &&
                        !game.home.includes(searchTerm.toUpperCase())) {
                        return null;
                    }

                    return (
                        <div key={game.id} className="border-b border-white/5">
                            {/* Game Header / Accordion Trigger */}
                            <div
                                onClick={() => toggleGame(game.id)}
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
                                            src={`http://localhost:5000/assets/team_logos/${TEAM_ID_MAP[game.away]}.svg`}
                                            alt={game.away}
                                            className="w-6 h-6 object-contain"
                                            onError={(e) => e.target.style.display = 'none'}
                                        />
                                        <span className="text-[10px] font-bold text-gray-400 group-hover:text-white transition-colors">{game.awayName?.split(' ').pop()}</span>
                                    </div>

                                    {/* Time */}
                                    <div className="flex flex-col items-center gap-0.5">
                                        <span className="text-[9px] font-bold text-gray-500 uppercase">{game.date}</span>
                                        <span className="text-[11px] font-bold text-white">{game.time}</span>
                                    </div>

                                    {/* Home */}
                                    <div className="flex flex-col items-center gap-1 w-14">
                                        <img
                                            src={`http://localhost:5000/assets/team_logos/${TEAM_ID_MAP[game.home]}.svg`}
                                            alt={game.home}
                                            className="w-6 h-6 object-contain"
                                            onError={(e) => e.target.style.display = 'none'}
                                        />
                                        <span className="text-[10px] font-bold text-gray-400 group-hover:text-white transition-colors">{game.homeName?.split(' ').pop()}</span>
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
                                                                {/* Example Prop Lock Status - Mocked as Unlock for now */}
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
                                            No players found with this prop.
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