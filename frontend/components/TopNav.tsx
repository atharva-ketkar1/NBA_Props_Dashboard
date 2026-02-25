import React from 'react';
import { Book, User, Copy, Menu } from 'lucide-react';

const LeagueButton = ({ label, logoColor, isActive, logoSrc }: { label: string, logoColor: string, isActive?: boolean, logoSrc?: string }) => {
    const [imageError, setImageError] = React.useState(false);

    return (
        <button className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-[11px] font-bold transition-colors ${isActive ? 'bg-borderMedium text-white' : 'text-gray-500 hover:text-gray-300 hover:bg-borderMedium/50'}`}>
            <div className="w-3.5 h-4 flex items-center justify-center rounded-[1px] overflow-hidden bg-black/20">
                {logoSrc && !imageError ? (
                    <img
                        src={logoSrc}
                        alt={label}
                        className="w-full h-full object-contain"
                        onError={() => setImageError(true)}
                    />
                ) : (
                    <>
                        {label === 'NBA' && (
                            <div className="flex w-full h-full">
                                <div className="w-1/2 h-full bg-blue800"></div>
                                <div className="w-1/2 h-full bg-red700"></div>
                            </div>
                        )}
                        {label === 'WNBA' && (
                            <div className="w-full h-full bg-orange600"></div>
                        )}
                        {label === 'NFL' && (
                            <div className="w-full h-full bg-blue900 relative">
                                <div className="absolute top-0 left-1/2 -translate-x-1/2 text-[4px] text-white">★</div>
                            </div>
                        )}
                    </>
                )}
            </div>
            {label}
        </button>
    );
};

export const TopNav: React.FC<{ onMenuClick: () => void }> = ({ onMenuClick }) => {
    // Replace with your actual backend URL structure
    const BASE_URL = "http://localhost:5000/assets/sport_logos";

    return (
        <div className="h-16 bg-bgCanvas border-b border-borderMedium flex items-center justify-between px-4 lg:px-6 shrink-0 z-50 relative">
            <div className="flex items-center gap-1.5 hidden lg:flex">
                <LeagueButton
                    label="NBA"
                    logoColor="#00538c"
                    isActive={true}
                    logoSrc={`${BASE_URL}/nba.svg`}
                />
                <div className="h-4 w-px bg-borderMedium mx-1"></div>
                <LeagueButton
                    label="WNBA"
                    logoColor="#f57c00"
                    logoSrc={`${BASE_URL}/wnba-icon.png`}
                />
                <div className="h-4 w-px bg-borderMedium mx-1"></div>
                <LeagueButton
                    label="NFL"
                    logoColor="#013369"
                    logoSrc={`${BASE_URL}/nfl.svg`}
                />
            </div>

            <button className="lg:hidden text-gray-400 hover:text-white" onClick={onMenuClick}>
                <Menu className="w-6 h-6" />
            </button>

            <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center gap-2 select-none cursor-pointer">
                <div className="w-6 h-6 bg-white flex items-center justify-center mask-logo clip-path-polygon transform -skew-x-12 rounded-[2px]">
                    <div className="w-2.5 h-4 bg-neutral950 transform skew-x-12"></div>
                </div>
                <span className="text-[19px] font-bold text-white tracking-tight">PropsMadness</span>
            </div>

            <div className="flex items-center gap-3">
                <button className="p-2 text-gray-400 hover:text-white transition-colors hidden sm:block">
                    <Book className="w-5 h-5" />
                </button>

                <button className="flex items-center gap-2 bg-bgCanvas hover:bg-borderMedium text-white text-xs font-bold px-3 py-2 rounded-lg transition-colors border border-borderMedium relative group">
                    <Copy className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">Check My Prop</span>
                    <span className="absolute -top-2 -right-2 bg-green500 text-neutral950 text-[9px] font-extrabold px-1.5 py-0.5 rounded shadow-sm leading-none">NEW</span>
                </button>

                <button className="w-9 h-9 bg-bgCanvas border border-borderMedium rounded-full flex items-center justify-center text-gray-400 hover:text-white hover:bg-borderMedium transition-colors">
                    <User className="w-5 h-5" />
                </button>
            </div>
        </div>
    );
};