import React from 'react';
import { Search, Filter } from 'lucide-react';

const Sidebar = () => {
    const games = [
        { id: 1, t1: 'BKN', t2: 'ORL', time: '7:00 PM' },
        { id: 2, t1: 'WAS', t2: 'DET', time: '7:00 PM' },
        { id: 3, t1: 'CHI', t2: 'TOR', time: '7:30 PM' },
        { id: 4, t1: 'UTA', t2: 'ATL', time: '7:30 PM' },
        { id: 5, t1: 'CHA', t2: 'HOU', time: '8:00 PM' },
        { id: 6, t1: 'SAS', t2: 'DAL', time: '8:30 PM' },
        { id: 7, t1: 'GSW', t2: 'PHX', time: '10:00 PM' },
    ];

    return (
        <div className="w-80 h-screen bg-[#09090b] border-r border-[#27272a] flex flex-col font-sans">

            {/* Header / Search */}
            <div className="p-4 space-y-3 border-b border-[#27272a]">
                <div className="flex gap-2">
                    <button className="flex-1 bg-[#18181b] text-white text-xs font-bold py-2 rounded border border-[#27272a] flex justify-between px-3 items-center">
                        Points <span className="text-gray-500">▼</span>
                    </button>
                    <button className="flex-1 bg-[#18181b] text-white text-xs font-bold py-2 rounded border border-[#27272a] flex justify-between px-3 items-center">
                        All Games <span className="text-gray-500">▼</span>
                    </button>
                </div>
                <div className="relative">
                    <input
                        type="text"
                        placeholder="Search players or teams..."
                        className="w-full bg-[#18181b] text-sm text-gray-300 pl-3 pr-4 py-2.5 rounded border border-[#27272a] focus:outline-none focus:border-gray-600"
                    />
                </div>
            </div>

            {/* Game List */}
            <div className="flex-1 overflow-y-auto p-3 space-y-2">
                {games.map((g) => (
                    <div key={g.id} className="bg-[#09090b] border border-[#27272a] hover:border-gray-600 rounded-xl p-3 flex justify-between items-center cursor-pointer transition-all group">
                        {/* Team 1 */}
                        <div className="flex flex-col items-center w-12">
                            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-gray-700 to-gray-900 border border-gray-600 mb-1 flex items-center justify-center text-[8px] font-bold text-white">
                                {g.t1}
                            </div>
                            <span className="text-[10px] font-bold text-white group-hover:text-blue-400">{g.t1}</span>
                        </div>

                        {/* Time */}
                        <div className="text-center flex flex-col">
                            <span className="text-[10px] text-gray-500 font-bold uppercase">Thu</span>
                            <span className="text-xs font-bold text-white">{g.time}</span>
                        </div>

                        {/* Team 2 */}
                        <div className="flex flex-col items-center w-12">
                            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-gray-700 to-gray-900 border border-gray-600 mb-1 flex items-center justify-center text-[8px] font-bold text-white">
                                {g.t2}
                            </div>
                            <span className="text-[10px] font-bold text-white group-hover:text-blue-400">{g.t2}</span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default Sidebar;