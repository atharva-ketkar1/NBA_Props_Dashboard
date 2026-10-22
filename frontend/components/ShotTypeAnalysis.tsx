import React from 'react';
import { Info } from 'lucide-react';
import { ShotTypeData } from '../types';
import { colors } from '../utils/propsmadness_colors';
import { describePanelGap, PanelNoData, PanelSeasonSelect } from './PanelSeason';
import { hasEnoughPanelGames, LIVE_SEASON } from '../constants';
import type { SeasonSplitsResponse } from '../utils/dashboardApi';

function getOppRankColor(rank?: number, percentage?: number) {
   if (rank === undefined) return colors.bgElevation2; // default neutral

   let baseScore = 3;
   if (rank <= 6) baseScore = 1;
   else if (rank <= 10) baseScore = 2;
   else if (rank <= 20) baseScore = 3;
   else if (rank <= 24) baseScore = 4;
   else baseScore = 5;

   // Volume Gravity based on Frequency / Diet Percentage
   if (percentage !== undefined) {
      if (percentage < 10) {
         baseScore = 3; // Neutral
      } else if (percentage <= 25) {
         if (baseScore === 1) baseScore = 2;
         if (baseScore === 5) baseScore = 4;
      }
   }

   switch (baseScore) {
      case 1: return colors.courtRed;
      case 2: return colors.courtOrange;
      case 3: return colors.courtYellow;
      case 4: return colors.courtLightGreen;
      case 5: return colors.courtGreen;
      default: return colors.courtYellow;
   }
}

interface ShotTypeAnalysisProps {
   /** The raw {player, opp_def} aggregate, in the shape both seasons store it. */
   shotTypeAnalysis?: any;
   /** Games behind the live aggregate; the prior season brings its own count. */
   gamesPlayed?: number | null;
   /** This panel's own season, independent of the global game-log filter. */
   season?: string;
   onSeasonChange?: (season: string) => void;
   /** The prior-season bundle, when this panel is not on the live season. */
   splits?: SeasonSplitsResponse | null;
   isLoadingSplits?: boolean;
   /** The prior-season request failed, as opposed to coming back empty. */
   hasSplitsError?: boolean;
}

/**
 * Reshape the stored aggregate into the three bars the panel draws.
 *
 * Lives here rather than at the call site because both seasons go through it:
 * the live row and the prior-season bundle store the same shape, and the bar
 * widths, labels and ordering have to be identical or switching season would
 * look like the data changed when only its source did.
 */
function buildShotTypeBars(shotTypeAnalysis: any): ShotTypeData[] {
   const player = shotTypeAnalysis?.player ?? {};
   const oppDef = shotTypeAnalysis?.opp_def ?? {};
   const catchAndShoot = player.catch_and_shoot;
   const pullUp = player.pull_up;
   const lessThanTen = player.less_than_10_ft;

   // Never fall back to placeholder shot splits: a player with a
   // shot_type_analysis object but no actual splits would otherwise render those
   // placeholder percentages as their real profile. An empty panel is honest.
   if (!catchAndShoot && !pullUp && !lessThanTen) {
      return [];
   }

   return [
      {
         type: 'C&S',
         percentage: catchAndShoot?.percentage || 0,
         attempts: Math.round(catchAndShoot?.points || 0),
         frequency: catchAndShoot?.percentage || 0,
         width: catchAndShoot?.percentage || 33.3,
         rank: oppDef.catch_and_shoot?.rank,
      },
      {
         type: '< 10 ft',
         percentage: lessThanTen?.percentage || 0,
         attempts: Math.round(lessThanTen?.points || 0),
         frequency: lessThanTen?.percentage || 0,
         width: lessThanTen?.percentage || 33.3,
         rank: oppDef.less_than_10_ft?.rank,
      },
      {
         type: 'Pull Up',
         percentage: pullUp?.percentage || 0,
         attempts: Math.round(pullUp?.points || 0),
         frequency: pullUp?.percentage || 0,
         width: pullUp?.percentage || 33.3,
         rank: oppDef.pull_up?.rank,
      },
   ] as ShotTypeData[];
}

export const ShotTypeAnalysis: React.FC<ShotTypeAnalysisProps> = ({
   shotTypeAnalysis,
   gamesPlayed,
   season = LIVE_SEASON,
   onSeasonChange,
   splits,
   isLoadingSplits = false,
   hasSplitsError = false,
}) => {
   const isLiveSeason = season === LIVE_SEASON;
   const source = isLiveSeason ? shotTypeAnalysis : splits?.shotTypeAnalysis;
   const games = isLiveSeason ? gamesPlayed : splits?.stats?.GP;
   const data = buildShotTypeBars(source);
   // Three bars that always sum to 100% read as a settled shot diet whatever
   // they rest on, so the games test is doing real work here.
   const hasData = data.length > 0 && hasEnoughPanelGames(games);
   const gap = describePanelGap({
      gamesPlayed: games,
      noDataMessage: 'No shot-type data for this season',
      season,
      isLiveSeason,
      hasError: hasSplitsError,
      seasonHasNoRecord: !isLiveSeason && splits?.hasData === false,
   });
   const isLoading = !isLiveSeason && isLoadingSplits;

   return (
      <div className="bg-bgElevation0 rounded-lg p-3 lg:p-5 w-full min-w-0">
         <div className="flex items-center gap-2 mb-1 relative z-50">
            <h3 className="text-sm font-bold text-white">Shot Type Analysis</h3>
            <div className="relative group flex items-center">
               <Info className="w-3.5 h-3.5 text-gray-400 cursor-pointer hover:text-gray-300 transition-colors" />
               <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 hidden group-hover:block w-[280px] bg-[#1a1a1a] text-[#ededed] text-[13px] leading-relaxed rounded-lg p-3 shadow-2xl z-50 border border-[#333333] pointer-events-none">
                  Opponent ranks form a bell curve (1-6 Red/Tough, 25-30 Green/Easy). Colors adjust for Volume Gravity (shot types with very low frequency are pulled to Yellow/Neutral).
               </div>
            </div>
         </div>
         <div className="mb-6">
            <PanelSeasonSelect
               season={season}
               onSeasonChange={(next) => onSeasonChange?.(next)}
               className="text-xs text-gray-500"
            />
         </div>

         {hasData ? (
            <>
               <div className="flex text-xs text-gray-400 font-medium mb-2 w-full">
                  {data.map((item, idx) => (
                     <div key={idx} className="flex justify-center min-w-0" style={{ width: `${item.width}%` }}>
                        <span className="whitespace-nowrap text-center tracking-tight">{item.type}</span>
                     </div>
                  ))}
               </div>

               <div className="flex w-full h-14 rounded-lg border border-black/40 text-xs">
                  {data.map((item, idx) => {
                     const dietPercentage = item.frequency !== undefined ? item.frequency : item.percentage;
                     const bgColor = getOppRankColor(item.rank, dietPercentage);

                     return (
                        <div
                           key={idx}
                           className={`flex items-center justify-center relative min-w-0 ${idx === 0 ? 'rounded-l-[7px]' : ''
                              } ${idx === data.length - 1 ? 'rounded-r-[7px]' : ''
                              } ${idx < data.length - 1 ? 'border-r-2 border-black/60' : ''}`}
                           style={{ width: `${item.width}%`, backgroundColor: bgColor }}
                        >
                           <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 z-10 bg-white px-1.5 py-0.5 rounded-[3px] text-[11px] font-semibold font-chakra tracking-[-0.01em] text-black shadow-sm flex gap-1 items-center whitespace-nowrap">
                              <span>{item.frequency !== undefined ? item.frequency : item.percentage}%</span>
                              {(item.rank !== undefined || item.attempts > 0) && (
                                 <>
                                    <span className="text-gray-300 text-[9px]">|</span>
                                    <span>{item.rank !== undefined ? item.rank : item.attempts}</span>
                                 </>
                              )}
                           </div>
                        </div>
                     );
                  })}
               </div>
            </>
         ) : isLoading ? (
            <div className="flex items-center justify-center h-[88px] text-center text-xs text-fgSubtle">
               Loading {season}...
            </div>
         ) : (
            <PanelNoData
               message={gap.message}
               detail={gap.detail}
               season={season}
               onSeasonChange={(next) => onSeasonChange?.(next)}
               className="h-[88px]"
            />
         )}
      </div>
   );
};
