import React, { useState } from 'react';
import { Search, ChevronDown } from 'lucide-react';

const Sidebar = ({ games = [] }) => {
    const [statFilter, setStatFilter] = useState('Points');
    const [gameFilter, setGameFilter] = useState('All Games');
    const [searchTerm, setSearchTerm] = useState('');

    // Mock games data if none provided
    const defaultGames = [
        { id: 1, away: 'BKN', home: 'ORL', time: '7:00 PM', date: 'Thu' },
        { id: 2, away: 'WAS', home: 'DET', time: '7:00 PM', date: 'Thu' },
        { id: 3, away: 'CHI', home: 'TOR', time: '7:30 PM', date: 'Thu' },
        { id: 4, away: 'UTA', home: 'ATL', time: '7:30 PM', date: 'Thu' },
        { id: 5, away: 'CHA', home: 'HOU', time: '8:00 PM', date: 'Thu' },
        { id: 6, away: 'SAS', home: 'DAL', time: '8:30 PM', date: 'Thu' },
        { id: 7, away: 'GSW', home: 'PHX', time: '10:00 PM', date: 'Thu' },
        { id: 8, away: 'LAL', home: 'DEN', time: '10:00 PM', date: 'Thu' },
    ];

    const gameList = games.length > 0 ? games : defaultGames;

    // Filter games based on search
    const filteredGames = gameList.filter(game =>
        game.away?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        game.home?.toLowerCase().includes(searchTerm.toLowerCase())
    );

    return (
        <div className="w-full h-full bg-[#09090b] border-r border-white/10 flex flex-col font-sans">

            {/* Header Section */}
            <div className="p-3 space-y-2.5 border-b border-white/10 bg-black">
                {/* Filter Dropdowns */}
                <div className="flex gap-2">
                    <div className="relative flex-1">
                        <select
                            value={statFilter}
                            onChange={(e) => setStatFilter(e.target.value)}
                            className="w-full bg-[#18181b] text-white text-[10px] font-bold py-2 px-3 rounded border border-white/10 appearance-none cursor-pointer hover:bg-white/5 transition-colors pr-7"
                        >
                            <option>Points</option>
                            <option>Assists</option>
                            <option>Rebounds</option>
                            <option>Threes</option>
                        </select>
                        <ChevronDown size={12} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none" />
                    </div>

                    <div className="relative flex-1">
                        <select
                            value={gameFilter}
                            onChange={(e) => setGameFilter(e.target.value)}
                            className="w-full bg-[#18181b] text-white text-[10px] font-bold py-2 px-3 rounded border border-white/10 appearance-none cursor-pointer hover:bg-white/5 transition-colors pr-7"
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
                        placeholder="Filter match..."
                        className="w-full bg-[#18181b] text-[11px] text-gray-300 pl-8 pr-3 py-2 rounded border border-white/10 focus:outline-none focus:border-gray-600 focus:ring-1 focus:ring-gray-600/50 placeholder-gray-600 transition-all"
                    />
                </div>
            </div>

            {/* Games List */}
            <div className="flex-1 overflow-y-auto p-2 space-y-1 custom-scrollbar">
                {filteredGames.length === 0 ? (
                    <div className="text-center py-8 text-gray-600 text-xs">
                        No games found
                    </div>
                ) : (
                    filteredGames.map((game) => (
                        <div
                            key={game.id}
                            className="bg-transparent border border-transparent hover:border-white/10 hover:bg-white/5 rounded-lg p-2.5 flex justify-between items-center cursor-pointer transition-all group"
                        >
                            {/* Away Team */}
                            <div className="flex flex-col items-center w-12">
                                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-gray-800 to-gray-900 border border-white/10 mb-1 flex items-center justify-center text-[8px] font-bold text-gray-300 shadow-sm group-hover:border-white/20 transition-colors">
                                    {game.away}
                                </div>
                                <span className="text-[8px] text-gray-600 font-medium">{game.date}</span>
                            </div>

                            {/* Time & vs */}
                            <div className="text-center flex flex-col">
                                <span className="text-[10px] font-bold text-white group-hover:text-blue-400 transition-colors">
                                    {game.time}
                                </span>
                                <span className="text-[8px] text-gray-600 font-bold mt-0.5">vs</span>
                            </div>

                            {/* Home Team */}
                            <div className="flex flex-col items-center w-12">
                                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-gray-800 to-gray-900 border border-white/10 mb-1 flex items-center justify-center text-[8px] font-bold text-gray-300 shadow-sm group-hover:border-white/20 transition-colors">
                                    {game.home}
                                </div>
                                <span className="text-[8px] text-gray-600 font-medium">{game.date}</span>
                            </div>
                        </div>
                    ))
                )}
            </div>

            {/* Custom Scrollbar Styles */}
            <style jsx>{`
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