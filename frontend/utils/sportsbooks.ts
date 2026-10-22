/**
 * Sportsbook selection and fallback.
 *
 * The book toggle is a preference, not a hard filter. Early in a slate only one
 * book has posted lines for the later games, so filtering strictly by the active
 * book hid those games from the sidebar entirely: a Friday game with FanDuel
 * lines only would vanish while DraftKings was selected, then reappear on the FD
 * toggle. These helpers resolve, per game, which book actually has coverage so a
 * game shows up whenever any book has lines for it.
 *
 * Fallback is resolved per game rather than per player so one game card never
 * mixes logos from different books.
 */
import type { Player, SportsbookId } from '../types.js';
import { getSportsbooksWithPropForDate } from './propResolution.js';

export const DEFAULT_SPORTSBOOK: SportsbookId = 'dk';
export const SPORTSBOOK_FALLBACK_PRIORITY: SportsbookId[] = ['pp', 'fd', 'dk', 'mgm', 'cz'];

const UNDATED_AVAILABILITY_KEY = '__undated__';

/** player id -> stat type -> sportsbook -> game date -> line is known to exist */
export type PropAvailabilityByPlayer = Record<number, Record<string, Record<string, Record<string, boolean>>>>;

/**
 * Books the availability feed says have a line, including ones whose prop body
 * has not hydrated into `rawData` yet.
 */
export function getAvailableSportsbooksForDate(
  availabilityByPlayer: PropAvailabilityByPlayer,
  playerId: number,
  statType: string,
  gameDate?: string | null,
) {
  const sportsbookMap = availabilityByPlayer?.[playerId]?.[statType] ?? {};
  const dateKey = gameDate || UNDATED_AVAILABILITY_KEY;

  return Object.keys(sportsbookMap).filter((sportsbook) => {
    const dateMap = sportsbookMap[sportsbook] ?? {};
    return Boolean(dateMap[dateKey] || dateMap[UNDATED_AVAILABILITY_KEY]);
  });
}

/** Books with a line for this player/stat/date, hydrated or merely announced. */
export function getKnownSportsbooksForDate(
  player: Player,
  availabilityByPlayer: PropAvailabilityByPlayer,
  statType: string,
  gameDate?: string | null,
) {
  return Array.from(new Set([
    ...getSportsbooksWithPropForDate(player, statType, gameDate),
    ...getAvailableSportsbooksForDate(availabilityByPlayer, player.id, statType, gameDate),
  ]));
}

/**
 * Keep the active book whenever it has any coverage at all; otherwise take the
 * book covering the most lines, breaking ties on SPORTSBOOK_FALLBACK_PRIORITY.
 * Returns null when no supported book has coverage.
 */
export function resolveBestSportsbook(
  countsBySportsbook: Partial<Record<string, number>>,
  activeSportsbook?: SportsbookId | null,
): SportsbookId | null {
  if (activeSportsbook && (countsBySportsbook[activeSportsbook] ?? 0) > 0) {
    return activeSportsbook;
  }

  const ranked = SPORTSBOOK_FALLBACK_PRIORITY
    .filter((sportsbook) => (countsBySportsbook[sportsbook] ?? 0) > 0)
    .sort((left, right) => (
      (countsBySportsbook[right] ?? 0) - (countsBySportsbook[left] ?? 0)
      || SPORTSBOOK_FALLBACK_PRIORITY.indexOf(left) - SPORTSBOOK_FALLBACK_PRIORITY.indexOf(right)
    ));

  return ranked[0] ?? null;
}

/** Counts of players covered per book, for one game's worth of players. */
export function countSportsbookCoverage(
  players: Player[],
  availabilityByPlayer: PropAvailabilityByPlayer,
  statType: string,
  gameDate?: string | null,
) {
  const counts: Partial<Record<string, number>> = {};

  players.forEach((player) => {
    getKnownSportsbooksForDate(player, availabilityByPlayer, statType, gameDate)
      .forEach((sportsbook) => {
        counts[sportsbook] = (counts[sportsbook] ?? 0) + 1;
      });
  });

  return counts;
}
