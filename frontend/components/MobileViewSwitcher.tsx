import React from 'react';
import { BarChart2, Target, Users, Shuffle, MapPin } from 'lucide-react';

export type MobileView = 'graph' | 'shooting' | 'similar' | 'types' | 'assists';

interface ViewOption {
    id: MobileView;
    label: string;
    icon: React.ElementType;
}

// Mirror your existing desktop logic
const getViewsForTab = (activeTab: string): ViewOption[] => {
    const graph = { id: 'graph' as const, label: 'Graph', icon: BarChart2 };
    const similar = { id: 'similar' as const, label: 'Similar', icon: Users };
    const shooting = { id: 'shooting' as const, label: 'Shooting', icon: Target };
    const types = { id: 'types' as const, label: 'Types', icon: Shuffle };
    const assists = { id: 'assists' as const, label: 'Assists', icon: MapPin };

    // Rebounds, DD, TD, Blocks, Steals, TOV, Fantasy — only graph + similar
    if (['Rebounds', '1Q Rebounds', 'Double Double', 'Triple Double',
        'Blocks', 'Steals', 'Turnovers', 'Fantasy'].includes(activeTab)) {
        return [graph, similar];
    }

    // Assists tabs — graph + assists zones + similar
    if (['Assists', '1Q Assists', 'Reb+Ast'].includes(activeTab)) {
        return [graph, assists, similar];
    }

    // Everything else (Points, Threes, combos) — graph + shooting + types + similar
    return [graph, shooting, types, similar];
};

export const MobileViewSwitcher: React.FC<{
    activeView: MobileView;
    activeTab: string;
    onViewChange: (view: MobileView) => void;
}> = ({ activeView, activeTab, onViewChange }) => {
    const views = getViewsForTab(activeTab);

    // Auto-correct if current view isn't valid for this tab
    React.useEffect(() => {
        const valid = views.find(v => v.id === activeView);
        if (!valid) onViewChange('graph');
    }, [activeTab]);

    return (
        <div className="flex md:hidden mx-3 mb-2 bg-bgElevation1 rounded-xl p-1 border border-borderMedium/30">
            {views.map(({ id, label, icon: Icon }) => (
                <button
                    key={id}
                    onClick={() => onViewChange(id)}
                    className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-[11px] font-bold transition-all duration-200
            ${activeView === id
                            ? 'bg-bgElevation2 text-white shadow-sm border border-borderMedium/40'
                            : 'text-fgSubtle hover:text-white'
                        }`}
                >
                    <Icon className="w-3.5 h-3.5" />
                    {label}
                </button>
            ))}
        </div>
    );
};