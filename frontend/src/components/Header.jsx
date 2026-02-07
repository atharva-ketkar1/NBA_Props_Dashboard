import React from 'react';
import { Bell, Search, UserCircle, Menu } from 'lucide-react';

const Header = () => {
    return (
        <header className="h-12 bg-black border-b border-white/10 flex items-center justify-between px-4 lg:px-6 z-50 shadow-lg">

            {/* LEFT: Logo & Brand */}
            <div className="flex items-center gap-3">
                {/* Mobile Menu Button */}
                <button className="md:hidden text-gray-400 hover:text-white transition-colors">
                    <Menu size={18} />
                </button>

                {/* Logo & Title */}
                <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 bg-gradient-to-br from-blue-600 to-blue-500 rounded-md flex items-center justify-center shadow-lg shadow-blue-500/30">
                        <span className="text-white font-black italic text-xs">PM</span>
                    </div>
                    <div className="hidden sm:block">
                        <h1 className="text-white font-bold text-xs leading-none tracking-tight">PropsMadness</h1>
                        <span className="text-[9px] text-gray-600 font-bold tracking-widest uppercase">Clone Dashboard</span>
                    </div>
                </div>

                {/* Vertical Separator */}
                <div className="h-6 w-px bg-white/10 mx-1 hidden md:block"></div>

                {/* Sport Navigation Links */}
                <nav className="hidden md:flex items-center gap-5 text-[11px] font-bold">
                    <a href="#" className="text-white hover:text-blue-400 transition-colors tracking-wide uppercase">
                        NBA
                    </a>
                    <a href="#" className="text-gray-500 hover:text-white transition-colors tracking-wide uppercase">
                        NFL
                    </a>
                    <a href="#" className="text-gray-500 hover:text-white transition-colors tracking-wide uppercase">
                        MLB
                    </a>
                    <a href="#" className="text-gray-500 hover:text-white transition-colors tracking-wide uppercase">
                        NHL
                    </a>
                </nav>
            </div>

            {/* RIGHT: Search & User Actions */}
            <div className="flex items-center gap-3">

                {/* Search Bar */}
                <div className="relative hidden lg:block">
                    <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
                    <input
                        type="text"
                        placeholder="Find a player..."
                        className="bg-[#18181b] border border-white/10 text-white text-[11px] rounded-full pl-8 pr-3 py-1.5 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/50 w-44 transition-all placeholder-gray-600"
                    />
                </div>

                {/* Notifications */}
                <button className="relative text-gray-400 hover:text-white transition-colors p-1.5 hover:bg-white/5 rounded-full">
                    <Bell size={16} />
                    <span className="absolute top-0.5 right-0.5 w-2 h-2 bg-red-500 rounded-full border border-black"></span>
                </button>

                {/* User Profile */}
                <button className="flex items-center gap-2 text-gray-300 hover:text-white transition-colors hover:bg-white/5 rounded-full px-2 py-1">
                    <UserCircle size={20} />
                    <span className="text-[11px] font-bold hidden md:block">Guest User</span>
                </button>
            </div>
        </header>
    );
};

export default Header;