import React, { useEffect, useMemo, useState } from 'react';
import { Info, ChevronLeft, ChevronRight } from 'lucide-react';
import { Player, SimilarPlayerCandidate, SimilarPlayersMode } from '../types';
import { buildSimilarPlayersDataset } from '../utils/similarPlayers';

interface SimilarPlayersProps {
   player?: Player | null;
   players: Player[];
   activeTab: string;
   activeSportsbook: 'dk' | 'fd' | 'mgm' | 'cz';
   activeSeason: '25/26' | '24/25';
   isLoadingCandidates?: boolean;
   similarCandidatesByProp: SimilarPlayerCandidate[];
   similarCandidatesByPosition: SimilarPlayerCandidate[];
}

const PAGE_SIZE = 8;
const MAX_PAGES = 2;

function formatSignedValue(value: number, digits = 1) {
   const rounded = digits === 0 ? Math.round(value) : value.toFixed(digits);
   return value > 0 ? `+${rounded}` : `${rounded}`;
}

function formatValue(value: number) {
   return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function formatNullableValue(value: number | null) {
   if (value === null) return 'N/A';
   return formatValue(value);
}

export const SimilarPlayers: React.FC<SimilarPlayersProps> = ({
   player,
   players,
   activeTab,
   activeSportsbook,
   activeSeason,
   isLoadingCandidates = false,
   similarCandidatesByProp,
   similarCandidatesByPosition,
}) => {
   const [mode, setMode] = useState<SimilarPlayersMode>('prop');
   const [page, setPage] = useState(0);

   const activeCandidates = mode === 'prop' ? similarCandidatesByProp : similarCandidatesByPosition;
   const dataset = useMemo(() => buildSimilarPlayersDataset({
      player,
      players,
      candidates: activeCandidates,
      activeTab,
      activeSportsbook,
      rowLimit: PAGE_SIZE * MAX_PAGES,
   }), [activeCandidates, activeSportsbook, activeTab, player, players]);

   useEffect(() => {
      setPage(0);
   }, [mode, player?.id, activeTab, activeSportsbook, dataset.rows.length]);

   const pagedRows = useMemo(() => {
      return [...dataset.rows]
         .sort((a, b) => {
            const dateDiff = new Date(b.gameDate ?? '').getTime() - new Date(a.gameDate ?? '').getTime();
            if (dateDiff !== 0) return dateDiff;
            const historyPriority = Number(Boolean(b.hasHistoricalLine)) - Number(Boolean(a.hasHistoricalLine));
            if (historyPriority !== 0) return historyPriority;
            const scoreDiff = (a.similarityScore ?? 0) - (b.similarityScore ?? 0);
            if (scoreDiff !== 0) return scoreDiff;
            return (a.lineGap ?? 0) - (b.lineGap ?? 0);
         })
         .slice(0, PAGE_SIZE * MAX_PAGES);
   }, [dataset.rows]);

   const pageCount = Math.max(1, Math.min(MAX_PAGES, Math.ceil(pagedRows.length / PAGE_SIZE)));
   const currentPage = Math.min(page, pageCount - 1);
   const visibleRows = pagedRows.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
   const modeDescription = mode === 'prop'
      ? 'Closest current lines plus matching production profile on the active slate.'
      : 'Closest position archetypes, then filtered by line and role profile.';
   const summary = dataset.summary;
   const candidatePreview = dataset.candidateNames.join(', ');
   const avgDiffColor = summary.total === 0 ? 'text-white' : summary.avgDiff >= 0 ? 'text-green500' : 'text-red500';
   const avgDiffPercentColor = summary.total === 0 ? 'text-white' : summary.avgDiffPercent >= 0 ? 'text-green500' : 'text-red500';
   const hitRateColor = summary.total === 0 ? 'text-white' : summary.hitRate >= 50 ? 'text-green500' : 'text-red500';
   const rowsMissingHistoricalLine = dataset.rows.filter((row) => !row.hasHistoricalLine).length;
   const showLoadingState = activeSeason === '25/26'
      && Boolean(player)
      && (
         isLoadingCandidates
         || (activeCandidates.length > 0 && dataset.rows.length === 0 && dataset.hasPendingCandidates)
      );

   const renderEmptyState = () => {
      if (activeSeason !== '25/26') {
         return (
            <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
               Similar-player comps currently use the live `25/26` season profile plus archived line history. Archive-season comps need a batch archive fetch path before they can be accurate.
            </div>
         );
      }

      if (!player) {
         return (
            <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
               Select a player to build comparable-player history.
            </div>
         );
      }

      if (!activeCandidates.length) {
         return (
            <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
               {mode === 'position'
                  ? 'No same-position comparables were found for this player yet.'
                  : 'No comparable active-slate players with a matching prop line were found for this market yet.'}
            </div>
         );
      }

      if (dataset.hasPendingCandidates) {
         return (
            <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
               Loading comparable-player histories ({dataset.loadedCandidateCount}/{dataset.totalCandidateCount} ready) to replace the placeholder table with real historical lines.
            </div>
         );
      }

      return (
         <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
            Comparable players were found, but there were not enough historical lines near the current {dataset.statLabel} number to build a stable sample.
         </div>
      );
   };

   const renderLoadingState = () => (
      <div className="flex-1 flex flex-col items-center justify-center text-center px-6 py-10">
         <div className="w-8 h-8 rounded-full border-2 border-borderMedium border-t-white animate-spin mb-3" aria-hidden="true" />
         <p className="text-sm text-gray-200">Loading similar players</p>
         <p className="text-xs text-gray-500 mt-1">
            The rest of the player view is ready. This panel fills in after the comps are ranked and prefetched.
         </p>
      </div>
   );

   return (
      <div className="bg-bgElevation0 rounded-lg p-5 w-full flex flex-col h-full min-h-[420px]">
         <div className="flex justify-between items-start mb-4 gap-3">
            <div className="min-w-0">
               <div className="flex items-center gap-2 mb-1">
                  <h3 className="text-sm font-bold text-white">Similar Players</h3>
                  <Info
                     className="w-3.5 h-3.5 text-gray-400 shrink-0"
                     title="Comps are ranked from the current slate, then historical rows are filtered to past lines that were close to the selected player&apos;s current number."
                  />
               </div>
               <p className="text-xs text-gray-500">
                  {activeSeason === '25/26' ? '25/26 live-slate comps' : 'Current-season comps only'}
                  {dataset.currentLine !== null ? ` • Current line ${formatValue(dataset.currentLine)}` : ''}
               </p>
               <p className="text-[11px] text-gray-400 mt-1 max-w-[34rem]">
                  {modeDescription}
                  {candidatePreview ? ` Top comps: ${candidatePreview}.` : ''}
                </p>
               {rowsMissingHistoricalLine > 0 && (
                  <p className="text-[11px] text-gray-500 mt-1">
                     {rowsMissingHistoricalLine} row{rowsMissingHistoricalLine === 1 ? '' : 's'} shown without archived closing lines yet.
                  </p>
               )}
            </div>

            <div className="flex bg-bgElevation1 rounded-xl p-1 shrink-0">
               <button
                  type="button"
                  onClick={() => setMode('prop')}
                  className={`text-xs font-bold px-3 py-1.5 rounded-lg whitespace-nowrap uppercase tracking-wider transition-colors ${mode === 'prop' ? 'bg-bgElevation2 text-white shadow-sm' : 'text-fgSubtle hover:text-white'}`}
               >
                  By Prop
               </button>
               <button
                  type="button"
                  onClick={() => setMode('position')}
                  className={`text-xs font-bold px-3 py-1.5 rounded-lg whitespace-nowrap uppercase tracking-wider transition-colors ${mode === 'position' ? 'bg-bgElevation2 text-white shadow-sm' : 'text-fgSubtle hover:text-white'}`}
               >
                  By Position
               </button>
            </div>
         </div>

         <div className="grid grid-cols-3 mb-5 gap-2">
            <div className="text-center">
               <div className="text-[10px] text-fgSubtle font-bold uppercase mb-1 whitespace-nowrap">Avg Diff</div>
               <div className={`${avgDiffColor} font-bold font-chakra text-lg`}>
                  {summary.total ? formatSignedValue(summary.avgDiff, 1) : '--'}
               </div>
            </div>
            <div className="text-center">
               <div className="text-[10px] text-fgSubtle font-bold uppercase mb-1 whitespace-nowrap">Avg Diff %</div>
               <div className={`${avgDiffPercentColor} font-bold font-chakra text-lg`}>
                  {summary.total ? `${formatSignedValue(summary.avgDiffPercent, 0)}%` : '--'}
               </div>
            </div>
            <div className="text-center">
               <div className="text-[10px] text-fgSubtle font-bold uppercase mb-1 whitespace-nowrap">Hit Rate</div>
               <div className={`${hitRateColor} font-bold font-chakra text-lg whitespace-nowrap`}>
                  {summary.total ? `${summary.hitRate}% (${summary.hits}/${summary.total})` : '--'}
               </div>
            </div>
         </div>

         {showLoadingState ? renderLoadingState() : dataset.rows.length > 0 ? (
            <>
               <div className="w-full overflow-x-auto custom-scrollbar pb-2">
                  <div className="min-w-[600px]">
                     <div className="grid grid-cols-[1fr_1fr_2fr_1fr_1fr_1fr] text-[10px] text-fgSubtle font-bold uppercase mb-3 px-2">
                        <div>Date</div>
                        <div>Team</div>
                        <div>Player</div>
                        <div className="text-center">Line</div>
                        <div className="text-center">Result</div>
                        <div className="text-right">Diff %</div>
                     </div>

                     <div className="space-y-1">
                        {visibleRows.map((game) => (
                           <div key={`${game.playerId}-${game.gameDate}-${game.line}-${game.result}`} className="grid grid-cols-[1fr_1fr_2fr_1fr_1fr_1fr] text-xs items-center py-2.5 px-2 hover:bg-bgElevation1 rounded transition-colors border-b border-borderMedium/40 last:border-0">
                              <div className="text-gray-300 font-medium">{game.date}</div>
                              <div className="text-gray-300">{game.team}</div>
                              <div className="text-white font-medium truncate pr-2">{game.player}</div>
                              <div className="text-center">
                                 <span className={`px-1.5 py-0.5 rounded font-bold font-chakra border text-[11px] ${game.line === null ? 'text-gray-300 bg-bgElevation1 border-borderMedium/60' : 'text-white bg-borderMedium border-borderMuted'}`}>
                                    {formatNullableValue(game.line)}
                                 </span>
                              </div>
                              <div className="text-center">
                                 <span className={`px-1.5 py-0.5 rounded-[4px] text-white font-bold font-chakra text-[11px] min-w-[30px] inline-block ${game.hit === null ? 'bg-borderMedium text-gray-200' : game.hit ? 'bg-green600' : 'bg-red600'}`}>
                                    {formatValue(game.result)}
                                 </span>
                              </div>
                              <div className="text-right">
                                 <span className={`px-1.5 py-0.5 rounded-[4px] font-bold font-chakra text-[11px] min-w-[44px] inline-block text-center ${game.diffPercent === null ? 'bg-bgElevation1 text-gray-300' : game.diffPercent >= 0 ? 'bg-green600 text-white' : 'bg-red600 text-white'}`}>
                                    {game.diffPercent === null ? 'N/A' : `${formatSignedValue(game.diffPercent, 0)}%`}
                                 </span>
                              </div>
                           </div>
                        ))}
                     </div>
                  </div>
               </div>

               {pageCount > 1 && (
                  <div className="flex items-center justify-center gap-2 mt-auto pt-4 text-xs font-bold text-gray-400">
                     <div className="flex items-center gap-2 shrink-0 whitespace-nowrap">
                        <button
                           type="button"
                           onClick={() => setPage((current) => Math.max(0, current - 1))}
                           disabled={currentPage === 0}
                           className={`w-6 h-6 rounded flex items-center justify-center transition-colors ${currentPage === 0 ? 'bg-borderMedium/40 text-gray-600 cursor-default' : 'bg-borderMedium text-blue-500 hover:bg-gray-700'}`}
                        >
                           <ChevronLeft className="w-3 h-3" />
                        </button>
                        <span className="whitespace-nowrap tabular-nums">{currentPage + 1} / {pageCount}</span>
                        <button
                           type="button"
                           onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
                           disabled={currentPage >= pageCount - 1}
                           className={`w-6 h-6 rounded flex items-center justify-center transition-colors ${currentPage >= pageCount - 1 ? 'bg-borderMedium/40 text-gray-600 cursor-default' : 'bg-borderMedium text-blue-500 hover:bg-gray-700'}`}
                        >
                           <ChevronRight className="w-3 h-3" />
                        </button>
                     </div>
                  </div>
               )}
            </>
         ) : renderEmptyState()}
      </div>
   );
};
