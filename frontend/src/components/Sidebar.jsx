import React from 'react';
import { Search } from 'lucide-react';

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
        // CHANGE: 'h-screen' -> 'h-full' to fit inside the parent flex container
        <div className="w-full h-full bg-[#09090b] border-r border-white/5 flex flex-col font-sans">

            {/* Header / Search */}
            <div className="p-4 space-y-3 border-b border-white/5">
                <div className="flex gap-2">
                    <button className="flex-1 bg-[#18181b] text-white text-xs font-bold py-2 rounded border border-white/10 flex justify-between px-3 items-center hover:bg-white/5 transition">
                        Points <span className="text-gray-500">▼</span>
                    </button>
                    <button className="flex-1 bg-[#18181b] text-white text-xs font-bold py-2 rounded border border-white/10 flex justify-between px-3 items-center hover:bg-white/5 transition">
                        All <span className="text-gray-500">▼</span>
                    </button>
                </div>
                <div className="relative">
                    <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                    <input
                        type="text"
                        placeholder="Filter match..."
                        className="w-full bg-[#18181b] text-xs text-gray-300 pl-9 pr-4 py-2.5 rounded border border-white/10 focus:outline-none focus:border-gray-600 placeholder-gray-600"
                    />
                </div>
            </div>

            {/* Game List */}
            <div className="flex-1 overflow-y-auto p-3 space-y-1 custom-scrollbar">
                {games.map((g) => (
                    <div key={g.id} className="bg-transparent border border-transparent hover:border-white/5 hover:bg-white/5 rounded-lg p-2 flex justify-between items-center cursor-pointer transition-all group">
                        <div className="flex flex-col items-center w-10">
                            <div className="w-6 h-6 rounded-full bg-gradient-to-br from-gray-800 to-black border border-white/10 mb-1 flex items-center justify-center text-[7px] font-bold text-gray-400">
                                {g.t1}
                            </div>
                        </div>

                        <div className="text-center flex flex-col">
                            <span className="text-[10px] font-bold text-white group-hover:text-blue-400 transition-colors">{g.time}</span>
                        </div>

                        <div className="flex flex-col items-center w-10">
                            <div className="w-6 h-6 rounded-full bg-gradient-to-br from-gray-800 to-black border border-white/10 mb-1 flex items-center justify-center text-[7px] font-bold text-gray-400">
                                {g.t2}
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default Sidebar;