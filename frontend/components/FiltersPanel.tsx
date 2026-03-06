import React from 'react';
import { X, HelpCircle, Lock, ChevronRight } from 'lucide-react';
import { ImageWithFallback } from './ui/ImageWithFallback';

interface FiltersPanelProps {
    isOpen: boolean;
    onClose: () => void;
    activeFilter: string | null;
    onFilterChange: (filter: string | null) => void;
}

export const FiltersPanel: React.FC<FiltersPanelProps> = ({ isOpen, onClose, activeFilter, onFilterChange }) => {
    if (!isOpen) return null;

    // We'll hardcode the UI as requested, mimicking the SSOT image.
    // The only functional part is the 'Minutes' pill toggling activeFilter State.

    return (
        <div className="w-[320px] bg-bgElevation0 border-l border-borderMedium/40 flex flex-col h-full overflow-y-auto custom-scrollbar shrink-0 shadow-[-10px_0_30px_rgba(0,0,0,0.5)] z-40">
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-borderMedium/40 shrink-0">
                <div className="flex items-center gap-2">
                    <h2 className="text-white font-bold text-[15px]">Filters</h2>
                    <HelpCircle className="w-4 h-4 text-borderMuted" />
                </div>
                <button onClick={onClose} className="text-borderMuted hover:text-white transition-colors">
                    <X className="w-5 h-5" />
                </button>
            </div>

            <div className="p-4 flex flex-col gap-6">
                {/* Season & Games Toggles */}
                <div className="flex flex-col gap-4">
                    <div className="flex items-center gap-3">
                        <span className="text-fgSubtle text-xs font-bold w-[50px]">Season</span>
                        <div className="flex bg-bgElevation1 rounded-lg border border-borderMedium p-0.5 w-[220px]">
                            {['23/24', '24/25', '25/26', 'All'].map(s => (
                                <button
                                    key={s}
                                    className={`flex-1 py-1.5 text-[11px] font-bold rounded-md transition-colors ${s === '25/26' ? 'bg-blue500 text-white shadow-sm' : 'text-fgSubtle hover:text-white'}`}
                                >
                                    {s}
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="flex items-center gap-3">
                        <span className="text-fgSubtle text-xs font-bold w-[50px]">Games</span>
                        <div className="flex items-center gap-2 w-[220px]">
                            <div className="flex bg-bgElevation1 rounded-lg border border-borderMedium p-0.5 flex-1">
                                {['10', '20', 'Max'].map(g => (
                                    <button
                                        key={g}
                                        className={`flex-1 py-1.5 text-[11px] font-bold rounded-md transition-colors text-fgSubtle hover:text-white`}
                                    >
                                        {g}
                                    </button>
                                ))}
                            </div>
                            <div className="flex items-center gap-2 bg-bgElevation1 rounded-lg border border-borderMedium px-2 py-1.5 shrink-0">
                                <span className="text-fgSubtle text-[10px] font-bold cursor-pointer hover:text-white">-</span>
                                <span className="text-white text-[11px] font-bold">19</span>
                                <span className="text-fgSubtle text-[10px] font-bold cursor-pointer hover:text-white">+</span>
                            </div>
                            <Lock className="w-3.5 h-3.5 text-borderMuted" />
                        </div>
                    </div>
                </div>

                {/* Main Tabs */}
                <div className="flex items-center justify-between border-b border-borderMedium/40 pb-[1px]">
                    {['Suggested', 'Opp Rankings', 'Splits', 'Stats'].map(t => (
                        <button
                            key={t}
                            className={`pb-2 text-[12px] font-bold ${t === 'Suggested' ? 'text-blue500 border-b-2 border-blue500' : 'text-fgSubtle hover:text-white border-b-2 border-transparent'}`}
                        >
                            {t}
                        </button>
                    ))}
                </div>

                {/* Suggested Tab Content - Pills */}
                <div className="flex flex-wrap gap-2">
                    <button
                        onClick={() => onFilterChange(activeFilter === 'Minutes' ? null : 'Minutes')}
                        className={`px-3 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition-colors border ${activeFilter === 'Minutes' ? 'bg-blue500 text-white border-blue500' : 'bg-bgElevation1 text-gray-300 border-borderMedium hover:bg-bgElevation2'}`}
                    >
                        Minutes
                    </button>
                    <button className="px-3 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition-colors border bg-bgElevation1 text-gray-300 border-borderMedium hover:bg-bgElevation2">
                        Def vs Position (PTS) <span className="text-red500">#1</span>
                    </button>
                    <button className="px-3 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition-colors border bg-bgElevation1 text-gray-300 border-borderMedium hover:bg-bgElevation2">
                        H2H
                    </button>
                    <button className="px-3 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition-colors border bg-bgElevation1 text-gray-300 border-borderMedium hover:bg-bgElevation2">
                        Def vs Transition (DPT) <span className="text-fgSubtle">#14</span>
                    </button>
                    <button className="px-3 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition-colors border bg-bgElevation1 text-gray-300 border-borderMedium hover:bg-bgElevation2">
                        Def vs Restricted Area (DSZ) <span className="text-red500">#2</span>
                    </button>
                    <button className="px-3 py-1.5 rounded-lg text-[11px] font-bold transition-colors border bg-bgElevation1 text-fgSubtle border-borderMedium hover:text-white">
                        +8 more
                    </button>
                </div>

                {/* Slider */}
                <div className="flex flex-col gap-2 pt-2 px-1">
                    <div className="relative w-full h-[30px] flex items-center">
                        <div className="absolute left-0 right-0 h-1 bg-borderMedium rounded-full"></div>
                        <div className="absolute left-[30%] right-0 h-1 bg-blue500 rounded-full"></div>

                        {/* Thumbs */}
                        <div className="absolute left-[30%] w-3 h-3 bg-blue500 rounded-full -translate-x-1/2"></div>
                        <div className="absolute right-0 w-3 h-3 bg-blue500 rounded-full translate-x-1/2"></div>

                        {/* Values */}
                        <span className="absolute left-0 -top-4 text-[11px] font-bold text-white">0</span>
                        <span className="absolute right-0 -top-4 text-[11px] font-bold text-white">38</span>
                    </div>
                    <div className="flex justify-center -mt-1">
                        <span className="bg-bgElevation1 border border-borderMedium text-fgSubtle text-[10px] font-bold px-3 py-1 rounded-full">
                            Expected
                        </span>
                    </div>
                </div>

                {/* Teammates Section */}
                <div className="flex flex-col gap-3 mt-4 border-t border-borderMedium/40 pt-4">
                    <div className="flex items-center justify-between">
                        <span className="text-white text-[13px] font-bold">Teammates</span>
                        <button className="bg-bgElevation1 border border-borderMedium text-fgSubtle text-[11px] font-bold px-2 py-1 rounded-md flex items-center gap-1 hover:text-white">
                            All <span className="font-normal">=</span>
                        </button>
                    </div>

                    <div className="flex items-center gap-2 relative">
                        {/* Card 1 */}
                        <div className="flex flex-col items-center bg-bgElevation1 border border-borderMedium rounded-xl p-2 w-[85px] relative hover:bg-bgElevation2 cursor-pointer transition-colors">
                            <div className="relative w-8 h-8 rounded-full overflow-hidden bg-bgElevation2 flex items-center justify-center border border-borderMedium">
                                {/* Adding the red + Out Badge over the picture. Positioned absolutely */}
                                <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 bg-red-600 text-[8px] font-bold text-white px-1 leading-none rounded-sm border border-black z-10 flex items-center gap-0.5 whitespace-nowrap whitespace-nowrap tracking-tighter">
                                    <span className="text-[7px] font-black leading-none">+</span> OUT
                                </div>
                                <ImageWithFallback
                                    src="https://cdn.nba.com/headshots/nba/latest/260x190/203084.png"
                                    fallbackSrc={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/player_headshots/203084.png`}
                                    alt="H. Barnes"
                                    className="w-full h-full object-cover transform scale-125 pt-1"
                                />
                            </div>
                            <span className="text-white text-[11px] font-bold mt-2 whitespace-nowrap">H. Barnes</span>
                            <span className="text-red500 font-chakra text-[11px] font-bold">-7.7</span>
                        </div>

                        {/* Card 2 */}
                        <div className="flex flex-col items-center bg-bgElevation1 border border-borderMedium rounded-xl p-2 w-[85px] hover:bg-bgElevation2 cursor-pointer transition-colors">
                            <div className="w-8 h-8 rounded-full overflow-hidden bg-bgElevation2 flex items-center justify-center border border-borderMedium">
                                <ImageWithFallback
                                    src="https://cdn.nba.com/headshots/nba/latest/260x190/1641705.png"
                                    fallbackSrc={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/player_headshots/1641705.png`}
                                    alt="V. Wembanyama"
                                    className="w-full h-full object-cover transform scale-125 pt-1"
                                />
                            </div>
                            <span className="text-white text-[11px] font-bold mt-2 truncate w-full text-center">V. Wemb...</span>
                            <span className="text-red500 font-chakra text-[11px] font-bold">-8.2</span>
                        </div>

                        {/* Card 3 */}
                        <div className="flex flex-col items-center bg-bgElevation1 border border-borderMedium rounded-xl p-2 w-[85px] hover:bg-bgElevation2 cursor-pointer transition-colors">
                            <div className="w-8 h-8 rounded-full overflow-hidden bg-bgElevation2 flex items-center justify-center border border-borderMedium">
                                <ImageWithFallback
                                    src="https://cdn.nba.com/headshots/nba/latest/260x190/1642273.png"
                                    fallbackSrc={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'}/assets/player_headshots/1642273.png`}
                                    alt="S. Castle"
                                    className="w-full h-full object-cover transform scale-125 pt-1"
                                />
                            </div>
                            <span className="text-white text-[11px] font-bold mt-2 w-full truncate text-center">S. Castle</span>
                            <span className="text-red500 font-chakra text-[11px] font-bold">-8.4</span>
                        </div>

                        <ChevronRight className="w-5 h-5 text-borderMuted absolute -right-2 top-1/2 -translate-y-1/2 hover:text-white cursor-pointer" />
                    </div>
                </div>

            </div>
        </div>
    );
};
