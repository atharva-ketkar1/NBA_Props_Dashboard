import React, { useState } from 'react';
import { Sidebar } from './Sidebar';
import { TopNav } from './TopNav';

interface LayoutProps {
  children: React.ReactNode;
  sidebarProps?: any;
}

export const Layout: React.FC<LayoutProps> = ({ children, sidebarProps }) => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  return (
    // CHANGE: bg-[#050505] -> bg-[#09090b] (Main App Background)
    <div className="flex flex-col h-screen bg-[#09090b] text-white font-sans overflow-hidden">

      {/* TopNav */}
      <TopNav onMenuClick={() => setIsSidebarOpen(true)} />

      {/* Main Body */}
      <div className="flex flex-1 overflow-hidden relative p-4 lg:p-6 gap-4 lg:gap-6">

        <Sidebar isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} {...sidebarProps} />

        {/* Mobile Backdrop */}
        {isSidebarOpen && (
          <div
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[55] lg:hidden"
            onClick={() => setIsSidebarOpen(false)}
          ></div>
        )}

        {/* CHANGE: Main Content bg updated to match app background or transparent */}
        <main className="flex-1 overflow-y-auto custom-scrollbar w-full min-w-0 relative rounded-xl">
          <div className="w-full mx-auto pb-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
};