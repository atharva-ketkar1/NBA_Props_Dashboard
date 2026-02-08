import React from 'react';
import { Bell, Search, UserCircle, Menu, ChevronDown } from 'lucide-react';

const Header = () => {
    return (
        <header className="h-14 bg-black border-b border-[#27272a] flex items-center justify-between px-4 z-50">

            {/* LEFT: Logo & Brand */}
            <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                    {/* Replicating the 'PM' Logo */}
                    <div className="w-8 h-8 bg-gradient-to-br from-blue-600 to-blue-700 rounded-lg flex items-center justify-center shadow-lg shadow-blue-900/20 ring-1 ring-white/10">
                        <span className="text-white font-black italic text-sm">PM</span>
                    </div>
                    <div className="hidden lg:block">
                        <h1 className="text-white font-bold text-sm leading-none tracking-tight">PropsMadness</h1>
                        <span className="text-[10px] text-gray-500 font-bold tracking-widest uppercase">Clone</span>
                    </div>
                </div>

                {/* Vertical Separator */}
                <div className="h-6 w-px bg-[#27272a] mx-2 hidden md:block"></div>

                {/* MAIN NAVIGATION - The "Tabs" */}
                {/* This mimics the original's top bar navigation */}
                <nav className="hidden md:flex items-center gap-1">
                    {['NBA', 'NFL', 'NHL', 'MLB', 'UFC'].map((sport, i) => (
                        <a
                            key={sport}
                            href="#"
                            className={`
                                px-4 py-1.5 rounded text-[11px] font-black uppercase tracking-wide transition-all
                                ${i === 0
                                    ? 'bg-[#18181b] text-white ring-1 ring-white/10 shadow-sm'
                                    : 'text-gray-500 hover:text-white hover:bg-white/5'}
                            `}
                        >
                            {sport}
                        </a>
                    ))}
                </nav>
            </div>

            {/* RIGHT: Search & Actions */}
            <div className="flex items-center gap-3">
                <div className="relative hidden lg:block">
                    <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
                    <input
                        type="text"
                        placeholder="Search player..."
                        className="bg-[#18181b] border border-[#27272a] text-white text-[11px] font-medium rounded-md pl-9 pr-3 py-2 w-56 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/50 transition-all placeholder-gray-600"
                    />
                    <div className="absolute right-2 top-1/2 -translate-y-1/2 flex gap-1">
                        <span className="text-[10px] text-gray-600 border border-gray-800 rounded px-1.5 py-0.5">⌘K</span>
                    </div>
                </div>

                <div className="h-6 w-px bg-[#27272a] mx-1 hidden lg:block"></div>

                <button className="flex items-center gap-2 text-white hover:bg-[#18181b] rounded-md px-2 py-1.5 transition-colors border border-transparent hover:border-[#27272a]">
                    <div className="w-6 h-6 rounded-full bg-gradient-to-r from-emerald-500 to-emerald-600 flex items-center justify-center text-[10px] font-bold">
                        AK
                    </div>
                    <ChevronDown size={12} className="text-gray-500" />
                </button>
            </div>
        </header>
    );
};

export default Header;