import React from 'react';
import { Info } from 'lucide-react';

const CourtShape = () => (
    <svg viewBox="0 0 500 420" className="w-full h-full overflow-visible">
        {/* Background Court Color - standard dark/black from card */}
        <rect x="0" y="0" width="500" height="420" fill="#050505" />
        
        {/* 1. Deep 3 Zone (Background for half court) - Orange */}
        <rect x="0" y="0" width="500" height="420" fill="#e79036" rx="4" />
        
        {/* 2. Mid Range Zone - Olive Green */}
        {/* Shape: 3pt line area */}
        <path 
            d="M36,0 L36,137 A237.5,237.5 0 0,0 464,137 L464,0 Z" 
            fill="#bdcc66" 
            stroke="black"
            strokeWidth="2"
        />

        {/* 3. Paint Zone - Orange */}
        <path 
            d="M170,0 L330,0 L330,190 L170,190 Z" 
            fill="#e79036" 
            stroke="black" 
            strokeWidth="2" 
        />
        {/* Paint Cap */}
        <path 
            d="M170,190 A80,80 0 0,0 330,190" 
            fill="#e79036" 
            stroke="black" 
            strokeWidth="2" 
        />

        {/* 4. Corners - Yellow */}
        <rect x="0" y="0" width="36" height="137" fill="#facc15" stroke="black" strokeWidth="2" />
        <rect x="464" y="0" width="36" height="137" fill="#facc15" stroke="black" strokeWidth="2" />

        {/* Rim / Restricted Area Lines */}
        <path d="M220,47 A30,30 0 0,0 280,47" fill="none" stroke="black" strokeWidth="2" />
        <line x1="220" y1="35" x2="220" y2="47" stroke="black" strokeWidth="2" />
        <line x1="280" y1="35" x2="280" y2="47" stroke="black" strokeWidth="2" />
        
        {/* Backboard */}
        <line x1="220" y1="40" x2="280" y2="40" stroke="black" strokeWidth="3" />
        {/* Hoop */}
        <circle cx="250" cy="47.5" r="7.5" fill="none" stroke="black" strokeWidth="2" />

        {/* Inner Paint Box (Free Throw Box) Lines - Optional but adds detail */}
        <path d="M170,190 L330,190" stroke="black" strokeWidth="1" />
        
        {/* Center inner circle label holder (visual trick for the 15% label) */}
        <rect x="220" y="210" width="60" height="30" fill="white" fillOpacity="0" />

    </svg>
);

const ZoneLabel = ({ top, left, pct, val }: { top: string, left: string, pct: string, val: string }) => (
    <div className="absolute transform -translate-x-1/2 -translate-y-1/2 bg-white flex shadow-[0_2px_4px_rgba(0,0,0,0.3)] rounded-[3px] overflow-hidden text-[11px] font-bold border border-white z-10" style={{ top, left }}>
        <div className="px-1 py-0.5 text-black min-w-[32px] text-center tracking-tight">{pct}</div>
        <div className="px-1 py-0.5 bg-white text-black border-l border-gray-300 min-w-[22px] text-center tracking-tight">{val}</div>
    </div>
);

export const ShootingZones: React.FC = () => {
  return (
    <div className="bg-card rounded-lg p-5 w-full">
      <div className="flex justify-between items-start mb-4">
        <div>
           <div className="flex items-center gap-2 mb-1">
             <h3 className="text-sm font-bold text-white">Shooting Zones</h3>
             <Info className="w-3.5 h-3.5 text-gray-400" />
           </div>
           <p className="text-xs text-gray-500">25/26 Season</p>
        </div>
        
        {/* Controls */}
        <div className="flex bg-[#0f0f11] rounded-lg p-1 border border-border items-center">
            <button className="text-xs font-bold px-3 py-1.5 rounded-md text-white bg-[#27272a] shadow-sm border border-border/50">Player</button>
            <div className="px-2 py-1 text-[10px] font-bold text-gray-500 bg-[#27272a]/50 rounded-[4px] mx-1 h-fit">vs</div>
            <button className="text-xs font-bold px-3 py-1.5 rounded-md text-gray-400 hover:text-white">Opp Defense</button>
        </div>
      </div>

      <div className="relative w-full aspect-[1.3] max-w-[320px] mx-auto mt-6">
         <CourtShape />
         
         {/* Data Labels - Positioned to match screenshot */}
         {/* Left Corner */}
         <ZoneLabel top="16%" left="8%" pct="2%" val="19" />
         {/* Right Corner */}
         <ZoneLabel top="16%" left="92%" pct="2%" val="14" />
         
         {/* Paint / Rim */}
         <ZoneLabel top="16%" left="50%" pct="49%" val="25" />
         
         {/* High Post / FT Circle */}
         <ZoneLabel top="45%" left="50%" pct="15%" val="2" />
         
         {/* Mid Range Center */}
         <ZoneLabel top="68%" left="50%" pct="11%" val="26" />
         
         {/* Top of Key 3PT */}
         <ZoneLabel top="88%" left="50%" pct="21%" val="10" />
      </div>
    </div>
  );
};