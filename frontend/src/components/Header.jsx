import React from 'react';
import { Bell, Search, UserCircle, Menu } from 'lucide-react';

const Header = () => {
    return (
        <header className="h-14 bg-[#09090b] border-b border-white/5 flex items-center justify-between px-4 lg:px-6 z-50">

            {/* LEFT: Logo & Brand */}
            <div className="flex items-center gap-4">
                {/* Mobile Menu Button (Optional) */}
                <button className="md:hidden text-gray-400 hover:text-white">
                    <Menu size={20} />
                </button>

                <div className="flex items-center gap-2">
                    <div className="w-8 h-8 bg-gradient-to-tr from-blue-600 to-blue-400 rounded-lg flex items-center justify-center shadow-lg shadow-blue-500/20">
                        <span className="text-white font-black italic text-sm">PM</span>
                    </div>
                    <div className="hidden md:block">
                        <h1 className="text-white font-bold text-sm leading-none tracking-tight">PropsMadness</h1>
                        <span className="text-[10px] text-gray-500 font-medium tracking-wider">CLONE DASHBOARD</span>
                    </div>
                </div>

                {/* Vertical Separator */}
                <div className="h-6 w-px bg-white/10 mx-2 hidden md:block"></div>

                {/* Navigation Links */}
                <nav className="hidden md:flex items-center gap-6 text-xs font-bold text-gray-400">
                    <a href="#" className="text-white hover:text-blue-400 transition-colors">NBA</a>
                    <a href="#" className="hover:text-white transition-colors">NFL</a>
                    <a href="#" className="hover:text-white transition-colors">MLB</a>
                    <a href="#" className="hover:text-white transition-colors">NHL</a>
                </nav>
            </div>

            {/* RIGHT: User Actions */}
            <div className="flex items-center gap-4">

                {/* Search Bar (Compact) */}
                <div className="relative hidden md:block">
                    <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
                    <input
                        type="text"
                        placeholder="Find a player..."
                        className="bg-[#18181b] border border-white/10 text-white text-xs rounded-full pl-8 pr-4 py-1.5 focus:outline-none focus:border-blue-500 w-48 transition-all"
                    />
                </div>

                {/* Notifications */}
                <button className="relative text-gray-400 hover:text-white transition-colors">
                    <Bell size={18} />
                    <span className="absolute top-0 right-0 w-2 h-2 bg-red-500 rounded-full border-2 border-[#09090b]"></span>
                </button>

                {/* Profile */}
                <button className="flex items-center gap-2 text-gray-300 hover:text-white transition-colors">
                    <UserCircle size={24} />
                    <span className="text-xs font-bold hidden md:block">Guest User</span>
                </button>
            </div>
        </header>
    );
};

export default Header;