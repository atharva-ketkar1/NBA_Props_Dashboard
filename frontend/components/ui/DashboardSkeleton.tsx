import React from 'react';

export const DashboardSkeleton: React.FC = () => {
    return (
        <div className="flex flex-col gap-6 animate-pulse w-full max-w-screen-2xl mx-auto">
            {/* Header + Chart Skeleton */}
            <div className="bg-card rounded-xl border border-border overflow-hidden shadow-lg h-[465px] bg-bgElevation1">
                <div className="h-20 border-b border-border bg-borderMedium/20"></div>
                <div className="h-full bg-bgElevation1"></div>
            </div>

            {/* Bottom Grid Skeleton */}
            <section className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-10 gap-6">
                <div className="xl:col-span-4 flex flex-col gap-6">
                    <div className="h-[320px] bg-bgElevation1 rounded-xl border border-border"></div>
                    <div className="h-[320px] bg-bgElevation1 rounded-xl border border-border"></div>
                </div>
                <div className="xl:col-span-6 flex flex-col gap-6">
                    <div className="h-[320px] bg-bgElevation1 rounded-xl border border-border"></div>
                    <div className="h-[320px] bg-bgElevation1 rounded-xl border border-border"></div>
                </div>
            </section>
        </div>
    );
};
