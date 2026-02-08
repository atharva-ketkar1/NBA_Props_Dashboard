import React, { useState } from 'react';
import { Sidebar } from './Sidebar';
import { TopNav } from './TopNav';

interface LayoutProps {
  children: React.ReactNode;
  sidebarProps?: any; // We'll type this properly or use the SidebarProps interface if exported
}

export const Layout: React.FC<LayoutProps> = ({ children, sidebarProps }) => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  return (
    <div className="flex min-h-screen bg-background text-white font-sans overflow-hidden">

      {/* Sidebar - responsive behavior handled within Sidebar component */}
      <Sidebar isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} {...sidebarProps} />

      {/* Mobile Backdrop */}
      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40 lg:hidden"
          onClick={() => setIsSidebarOpen(false)}
        ></div>
      )}

      {/* Main Content Area (TopNav + Content) */}
      <div className="flex-1 flex flex-col min-w-0 h-screen bg-background ml-4 lg:ml-8">
        <TopNav onMenuClick={() => setIsSidebarOpen(true)} />

        <main className="flex-1 overflow-y-auto custom-scrollbar w-full relative">
          <div className="w-full mx-auto space-y-6 p-4 lg:p-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
};