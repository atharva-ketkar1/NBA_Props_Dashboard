import React, { useEffect, useMemo, useState } from 'react';
import { Info, ChevronLeft, ChevronRight } from 'lucide-react';
import { Player, SimilarPlayerCandidate, SimilarPlayersMode, SportsbookId } from '../types';
import { buildSimilarPlayersDataset } from '../utils/similarPlayers';
import { LIVE_SEASON, teamNickname } from '../constants';
import { PanelSeasonSelect } from './PanelSeason';
import { formatBreakEvenLabel, hitRateToneClass } from '../utils/betMath';

interface SimilarPlayersProps {
   player?: Player | null;
   players: Player[];
   activeTab: string;
   activeSportsbook: SportsbookId;
   activeSeason: string;
   targetOpponent?: string | null;
   /**
    * Reports how many rows currently fit the panel, so the parent can rank and
    * prefetch enough comps to fill it. The measurement has to happen here --
    * it is this component's box -- but what follows from it (how many
    * candidates to rank, how many histories to fetch) is the parent's job.
    */
   onRowCapacityChange?: (rows: number) => void;
   isLoadingCandidates?: boolean;
   similarCandidatesByProp: SimilarPlayerCandidate[];
   similarCandidatesByPosition: SimilarPlayerCandidate[];
   /** This panel's own season. Only the live season has comps; see below. */
   panelSeason?: string;
   onPanelSeasonChange?: (season: string) => void;
}

/**
 * Row sizing.
 *
 * The panel is `h-full` inside a grid, so its height changes with the viewport,
 * which stat tab is open, and whether the filters drawer is pushing the layout.
 * A fixed page size therefore either leaves dead space at the bottom or pushes
 * the pagination out of view. These let the table ask how many rows actually
 * fit and render exactly that many.
 *
 * ROW_HEIGHT_PX tracks the row markup below: py-2.5 (20px) + a ~16px xs line +
 * 1px bottom border, plus the 4px space-y-1 gap. Measured once from a real row
 * at runtime where possible, so a style change cannot silently desync it.
 */
const FALLBACK_ROW_HEIGHT_PX = 41;
const MIN_ROWS = 3;
// Caps how tall the table can get on a large monitor. 16 rows x ~41px is ~656px
// of rows, which fills a ~980px panel without leaving dead space under it;
// beyond that the second page takes over rather than the table running on.
const MAX_ROWS = 16;
const DEFAULT_PAGE_SIZE = 8;
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
   targetOpponent,
   onRowCapacityChange,
   isLoadingCandidates = false,
   similarCandidatesByProp,
   similarCandidatesByPosition,
   panelSeason = LIVE_SEASON,
   onPanelSeasonChange,
}) => {
   const [mode, setMode] = useState<SimilarPlayersMode>('prop');
   const [page, setPage] = useState(0);

   const activeCandidates = mode === 'prop' ? similarCandidatesByProp : similarCandidatesByPosition;
   // How many rows fit in the box right now. Measured rather than assumed --
   // see the note on ROW sizing above.
   const rowsAreaRef = React.useRef<HTMLDivElement | null>(null);
   const rowProbeRef = React.useRef<HTMLDivElement | null>(null);
   const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);

   useEffect(() => {
      const area = rowsAreaRef.current;
      if (!area || typeof ResizeObserver === 'undefined') return undefined;

      const recompute = () => {
         const available = area.clientHeight;
         // 0 while the panel is in a `hidden` mobile view. Keep the last good
         // value rather than collapsing the table to MIN_ROWS behind the scenes.
         if (!available) return;
         const probe = rowProbeRef.current?.getBoundingClientRect().height;
         const rowHeight = probe && probe > 8 ? probe + 4 : FALLBACK_ROW_HEIGHT_PX;
         const fits = Math.floor(available / rowHeight);
         setPageSize(Math.max(MIN_ROWS, Math.min(MAX_ROWS, fits)));
      };

      recompute();
      const observer = new ResizeObserver(recompute);
      observer.observe(area);
      return () => observer.disconnect();
   }, []);

   // Tell the parent how many rows fit, so it can rank and prefetch enough
   // comps to fill the box rather than a hardcoded 12/14 and 2/3.
   useEffect(() => {
      onRowCapacityChange?.(pageSize);
   }, [pageSize, onRowCapacityChange]);

   const dataset = useMemo(() => buildSimilarPlayersDataset({
      player,
      players,
      candidates: activeCandidates,
      activeTab,
      activeSportsbook,
      targetOpponent,
      rowLimit: pageSize * MAX_PAGES,
   }), [activeCandidates, activeSportsbook, activeTab, player, players, targetOpponent, pageSize]);

   useEffect(() => {
      setPage(0);
   }, [mode, player?.id, activeTab, activeSportsbook, targetOpponent, dataset.rows.length, pageSize]);

   const pagedRows = useMemo(
      () => dataset.rows.slice(0, pageSize * MAX_PAGES),
      [dataset.rows, pageSize],
   );

   const pageCount = Math.max(1, Math.min(MAX_PAGES, Math.ceil(pagedRows.length / pageSize)));
   const currentPage = Math.min(page, pageCount - 1);
   const visibleRows = pagedRows.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
   // The two modes now mean genuinely different things, so the copy says which
   // is which. Previously both mixed scoring level into the ranking, so "By
   // Position" returned players who produced a similar number of points rather
   // than players who play alike, and the distinction was mostly cosmetic.
   const modeDescription = mode === 'prop'
      ? `Players who play alike and are currently priced near this line.`
      : `Players with the closest playstyle — size, shot mix and role, independent of how much they score.`;
   // Comps that have already faced tonight's opponent are ranked first, because
   // the table below only shows games against that opponent. Saying so stops
   // the list looking arbitrary when a very similar player is absent from it.
   // Normally every comp shown has faced this opponent, so the note states the
   // rule. But the filter skips candidates whose history is unknown -- which is
   // the whole DB-mode pool until the aggregator has run once after the
   // measurements migration. Claiming the filter applied when it did not would
   // be the panel asserting something false about its own contents.
   const opponentFilterApplied = Boolean(targetOpponent)
      && activeCandidates.some((candidate) => candidate.facedTargetOpponent === true);
   const opponentNote = !targetOpponent
      ? null
      : opponentFilterApplied
         ? `Only comps who have already played ${teamNickname(targetOpponent)} are shown, since the table is their games against ${teamNickname(targetOpponent)}.`
         : `Matchup history is still loading for this slate, so comps are not yet filtered to players who have faced ${teamNickname(targetOpponent)}.`;
   const ppHistoryUnavailable = activeSportsbook === 'pp';
   const summary = dataset.summary;
   const candidatePreview = dataset.candidateNames.join(', ');
   const avgDiffColor = summary.total === 0 ? 'text-white' : summary.avgDiff >= 0 ? 'text-green500' : 'text-red500';
   const avgDiffPercentColor = summary.total === 0 ? 'text-white' : summary.avgDiffPercent >= 0 ? 'text-green500' : 'text-red500';
   // No odds here: these are historical closing LINES for comparable players,
   // and the archive does not carry each row's price. So this falls back to
   // -110 (52.4%) rather than the 50% bar it used to use, which coloured a
   // losing hit rate green.
   const hitRateColor = summary.total === 0
      ? 'text-white'
      : hitRateToneClass(summary.hitRate);
   const rowsMissingHistoricalLine = dataset.rows.filter((row) => !row.hasHistoricalLine).length;
   const showLoadingState = activeSeason === LIVE_SEASON
      && Boolean(player)
      && (
         isLoadingCandidates
         || (
            activeCandidates.length > 0
            && dataset.rows.length === 0
            && dataset.loadedCandidateCount === 0
            && dataset.hasPendingCandidates
         )
      );

   const renderEmptyState = () => {
      if (activeSeason !== LIVE_SEASON) {
         return (
            <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
               Similar-player comps currently use the live {LIVE_SEASON} season profile plus archived line history. Archive-season comps need a batch archive fetch path before they can be accurate.
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
         // Reachable now in a way it was not before: comps are hard-filtered to
         // players who have already faced this opponent, so early in a season a
         // genuinely similar player may simply not have played them yet. Say
         // that specifically rather than "no comparables found", which would
         // read as a data problem.
         if (targetOpponent) {
            return (
               <div className="flex-1 flex flex-col items-center justify-center text-center px-6 py-10 gap-1">
                  <span className="text-sm text-gray-400">
                     No similar player has played {teamNickname(targetOpponent)} yet this season.
                  </span>
                  <span className="text-xs text-gray-500 max-w-[28rem]">
                     This panel only compares against the same opponent, so it fills in as
                     {' '}{teamNickname(targetOpponent)} works through the schedule.
                  </span>
               </div>
            );
         }
         return (
            <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
               {mode === 'position'
                  ? 'No same-position comparables were found for this player yet.'
                  : 'No comparable active-slate players with a matching prop line were found for this market yet.'}
            </div>
         );
      }

      if (dataset.hasPendingCandidates) {
         if (dataset.loadedCandidateCount > 0) {
            return (
               <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
                  Comparable players were found, but the prefetched histories do not include enough games{targetOpponent ? ` vs ${teamNickname(targetOpponent)}` : ''} near the current {dataset.statLabel} number yet.
               </div>
            );
         }

         return (
            <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
               Loading comparable-player histories ({dataset.loadedCandidateCount}/{dataset.totalCandidateCount} ready) to replace the placeholder table with real historical lines{targetOpponent ? ` vs ${teamNickname(targetOpponent)}` : ''}.
            </div>
         );
      }

      return (
         <div className="flex-1 flex items-center justify-center text-center px-6 py-10 text-sm text-gray-400">
            Comparable players were found, but there were not enough historical lines{targetOpponent ? ` vs ${teamNickname(targetOpponent)}` : ''} near the current {dataset.statLabel} number to build a stable sample.
         </div>
      );
   };

   /**
    * The one panel the season dropdown cannot yet serve.
    *
    * Unlike the four aggregate panels, comps are not a stored season split: they
    * are ranked from the live slate and then matched against archived closing
    * lines. A prior-season version has to rank comps from that season's rosters
    * and read historical_player_props instead, which is a separate build. The
    * control is here so the panel reads consistently with its neighbours, and it
    * says plainly that the data is not wired up rather than showing an empty
    * table that looks like "no comps found".
    */
   const renderPriorSeasonUnavailable = () => (
      <div className="flex-1 flex flex-col items-center justify-center gap-lg text-center px-6 py-10">
         <span className="text-sm font-medium leading-[21px] text-textGeneralSecondary">
            Similar players are only available for the {LIVE_SEASON} season
         </span>
         <button
            type="button"
            onClick={() => onPanelSeasonChange?.(LIVE_SEASON)}
            className="flex items-center justify-center h-7 px-2 rounded-btn bg-bgElevation3 hover:bg-borderMuted text-[12px] font-medium leading-[18px] text-textGeneralPrimary transition-colors"
         >
            Back to {LIVE_SEASON} season
         </button>
      </div>
   );

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
                  <h3 className="text-sm font-bold text-white">
                     Similar Players
                     {targetOpponent
                        ? ` ${dataset.statLabel} vs ${teamNickname(targetOpponent)}`
                        : ''}
                  </h3>
                  <span
                     className="shrink-0"
                     title="Comps are ranked from the current slate, then historical rows are filtered to the selected player's opponent and past lines that were close to the current number."
                  >
                     <Info className="w-3.5 h-3.5 text-gray-400" />
                  </span>
               </div>
               <div className="flex items-center gap-2 flex-wrap text-xs text-gray-500">
                  <PanelSeasonSelect
                     season={panelSeason}
                     onSeasonChange={(next) => onPanelSeasonChange?.(next)}
                     className="text-xs text-gray-500"
                  />
                  <span>
                     {activeSeason === LIVE_SEASON ? `${LIVE_SEASON} live-slate comps` : 'Current-season comps only'}
                     {targetOpponent ? ` • vs ${targetOpponent}` : ''}
                     {dataset.currentLine !== null ? ` • Current line ${formatValue(dataset.currentLine)}` : ''}
                  </span>
               </div>
               <p className="text-[11px] text-gray-400 mt-1 max-w-[34rem]">
                  {modeDescription}
                  {candidatePreview ? ` Top comps: ${candidatePreview}.` : ''}
                </p>
               {opponentNote && (
                  <p className="text-[11px] text-gray-500 mt-1 max-w-[34rem]">{opponentNote}</p>
               )}
               {ppHistoryUnavailable ? (
                  <p className="text-[11px] text-gray-500 mt-1">
                     PrizePicks comparable rows use current PP lines only. Archived closing-line history is unavailable in v1.
                  </p>
               ) : rowsMissingHistoricalLine > 0 && (
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
               <div className={`${avgDiffColor} font-semibold font-chakra text-lg`}>
                  {summary.total ? formatSignedValue(summary.avgDiff, 1) : '--'}
               </div>
            </div>
            <div className="text-center">
               <div className="text-[10px] text-fgSubtle font-bold uppercase mb-1 whitespace-nowrap">Avg Diff %</div>
               <div className={`${avgDiffPercentColor} font-semibold font-chakra text-lg`}>
                  {summary.total ? `${formatSignedValue(summary.avgDiffPercent, 0)}%` : '--'}
               </div>
            </div>
            <div className="text-center">
               <div className="text-[10px] text-fgSubtle font-bold uppercase mb-1 whitespace-nowrap">Hit Rate</div>
               <div
                  className={`${hitRateColor} font-semibold font-chakra text-lg whitespace-nowrap`}
                  title={`Green above ${formatBreakEvenLabel(null)}, not 50% -- at -110 a prop needs 52.4% to break even`}
               >
                  {summary.total ? `${summary.hitRate}% (${summary.hits}/${summary.total})` : '--'}
               </div>
               {summary.total > 0 && (
                  <div className="text-[9px] text-fgSubtle mt-0.5 whitespace-nowrap tabular-nums">
                     vs 52.4% to break even
                  </div>
               )}
            </div>
         </div>

         {panelSeason !== LIVE_SEASON ? (
            renderPriorSeasonUnavailable()
         ) : showLoadingState ? renderLoadingState() : dataset.rows.length > 0 ? (
            <>
               <div className="w-full flex-1 min-h-0 flex flex-col overflow-x-auto custom-scrollbar pb-2">
                  <div className="min-w-[600px] flex-1 min-h-0 flex flex-col">
                     <div className="grid grid-cols-[1fr_1fr_2fr_1fr_1fr_1fr] text-[10px] text-fgSubtle font-bold uppercase mb-3 px-2">
                        <div>Date</div>
                        <div>Opp</div>
                        <div>Player</div>
                        <div className="text-center">Line</div>
                        <div className="text-center">Result</div>
                        <div className="text-right">Diff %</div>
                     </div>

                     {/*
                        `flex-1 min-h-0 overflow-y-auto` is load-bearing, not
                        cosmetic. This box is measured to decide how many rows to
                        render, so if its own content could stretch it, rendering
                        a row would grow the box, which would fit another row,
                        which would grow it again. Taking the height from the
                        panel and letting content scroll breaks that loop.
                        Scroll rather than clip, so that on a panel too short for
                        even MIN_ROWS the last row is reachable instead of being
                        cut in half. In the normal case exactly as many rows are
                        rendered as fit, so no scrollbar ever appears.
                      */}
                     <div ref={rowsAreaRef} className="space-y-1 flex-1 min-h-0 overflow-y-auto custom-scrollbar">
                        {visibleRows.map((game, rowIndex) => (
                           <div
                              key={`${game.playerId}-${game.gameDate}-${game.line}-${game.result}`}
                              ref={rowIndex === 0 ? rowProbeRef : undefined}
                              className="grid grid-cols-[1fr_1fr_2fr_1fr_1fr_1fr] text-xs items-center py-2.5 px-2 hover:bg-bgElevation1 rounded transition-colors border-b border-borderMedium/40 last:border-0"
                           >
                              <div className="text-gray-300 font-medium">{game.date}</div>
                              <div className="text-gray-300 truncate pr-2">
                                 {targetOpponent
                                    ? teamNickname(game.team)
                                    : (game.opponent ?? 'N/A')}
                              </div>
                              <div className="text-white font-medium truncate pr-2">{game.player}</div>
                              <div className="text-center">
                                 <span className={`px-1.5 py-0.5 rounded font-semibold font-chakra tracking-[-0.01em] border text-[11px] ${game.line === null ? 'text-gray-300 bg-bgElevation1 border-borderMedium/60' : 'text-white bg-borderMedium border-borderMuted'}`}>
                                    {formatNullableValue(game.line)}
                                 </span>
                              </div>
                              <div className="text-center">
                                 <span className={`px-1.5 py-0.5 rounded-xs text-white font-semibold font-chakra tracking-[-0.01em] text-[11px] min-w-[30px] inline-block ${game.hit === null ? 'bg-borderMedium text-gray-200' : game.hit ? 'bg-green600' : 'bg-red600'}`}>
                                    {formatValue(game.result)}
                                 </span>
                              </div>
                              <div className="text-right">
                                 <span className={`px-1.5 py-0.5 rounded-xs font-semibold font-chakra tracking-[-0.01em] text-[11px] min-w-[44px] inline-block text-center ${game.diffPercent === null ? 'bg-bgElevation1 text-gray-300' : game.diffPercent >= 0 ? 'bg-green600 text-white' : 'bg-red600 text-white'}`}>
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
