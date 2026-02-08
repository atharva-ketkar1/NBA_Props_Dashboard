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
    <div className="flex flex-col h-screen bg-background text-white font-sans overflow-hidden">

      {/* Top Navigation */}
      <TopNav onMenuClick={() => setIsSidebarOpen(true)} />

      {/* Main Container - adds gap between TopNav and content, and creates the grid layout */}
      <div className="flex flex-1 overflow-hidden relative p-4 lg:p-6 gap-6">

        {/* Sidebar */}
        {/* The Sidebar component handles its own responsive positioning (fixed on mobile, static on desktop) */}
        <Sidebar isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} {...sidebarProps} />

        {/* Mobile Backdrop */}
        {isSidebarOpen && (
          <div
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[55] lg:hidden"
            onClick={() => setIsSidebarOpen(false)}
          ></div>
        )}

        {/* Main Content */}
        <main className="flex-1 overflow-y-auto relative custom-scrollbar w-full min-w-0 rounded-xl">
          <div className="w-full mx-auto pb-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
};