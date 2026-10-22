import { useCallback, useEffect, useRef, useState } from 'react';
import { LIVE_SEASON, panelSeasonKey } from '../constants';
import { fetchDashboardSeasonSplits, SeasonSplitsResponse } from './dashboardApi';

/**
 * A finished attempt. Absent means still in flight, which is what the panels
 * read as "loading" -- so a failure MUST land here too, or a panel waits
 * forever on a request that already gave up.
 */
type SplitsEntry =
  | { status: 'ok'; payload: SeasonSplitsResponse }
  | { status: 'error' };

type SplitsCache = Record<string, SplitsEntry>;

function buildKey(playerId: number, seasonKey: string, opponent: string | null) {
  return `${playerId}:${seasonKey}:${opponent ?? ''}`;
}

/**
 * Lazily loads the prior-season aggregates behind each panel's season dropdown.
 *
 * Deliberately not bundled into /api/player: most sessions never open a panel's
 * dropdown at all, and the payload is four aggregates wide. It is fetched when a
 * panel actually asks for a season, and kept keyed by opponent as well as
 * player, because the same player's 25/26 profile is joined to a different
 * defense depending on which game is selected.
 *
 * Results are NOT discarded when the effect re-runs. The cache is keyed by
 * (player, season, opponent), so a resolved response is correct for its key no
 * matter what the component has moved on to displaying -- there is no stale-
 * response hazard to guard against. An earlier version guarded anyway, with a
 * `cancelled` flag set by the effect cleanup, and that was exactly wrong under
 * StrictMode: React runs the cleanup and re-runs the effect immediately, so the
 * first fetch was always marked cancelled and threw its result away, while the
 * re-run saw the key already in requestedRef and never fetched again. Every
 * panel sat on "Loading" forever.
 */
export function useSeasonSplits(
  playerId: number | null | undefined,
  opponent: string | null | undefined,
  requestedSeasons: string[],
) {
  const [cache, setCache] = useState<SplitsCache>({});
  // Every key a fetch has been started for. Kept in a ref so the effect does not
  // have to depend on `cache` -- depending on state it also writes would make it
  // re-run on its own completion.
  const requestedRef = useRef<Set<string>>(new Set());

  const normalizedOpponent = opponent ? String(opponent).toUpperCase() : null;
  // Joined rather than passed as an array so the effect does not re-run on every
  // render just because the caller rebuilt the list inline.
  const requestedKey = Array.from(new Set(requestedSeasons)).sort().join(',');

  useEffect(() => {
    if (!playerId) {
      return;
    }

    const seasons = requestedKey ? requestedKey.split(',') : [];

    for (const season of seasons) {
      const seasonKey = panelSeasonKey(season);
      if (!seasonKey) {
        continue; // The live season comes off the player row already in hand.
      }

      const cacheKey = buildKey(playerId, seasonKey, normalizedOpponent);
      if (requestedRef.current.has(cacheKey)) {
        continue;
      }

      requestedRef.current.add(cacheKey);

      void fetchDashboardSeasonSplits(playerId, seasonKey, normalizedOpponent)
        .then((payload) => {
          setCache((previous) => ({ ...previous, [cacheKey]: { status: 'ok', payload } }));
        })
        .catch((error) => {
          console.error('[season-splits]', error);
          // Recorded as a failure rather than as empty data: a panel that cannot
          // reach the endpoint must not tell the reader the season is empty.
          // Dropping the key also lets a later dependency change retry, without
          // looping -- the effect does not re-run on cache writes.
          requestedRef.current.delete(cacheKey);
          setCache((previous) => ({ ...previous, [cacheKey]: { status: 'error' } }));
        });
    }
  }, [playerId, normalizedOpponent, requestedKey]);

  const getSplits = useCallback((season: string) => {
    const seasonKey = panelSeasonKey(season);
    if (!playerId || !seasonKey) {
      return { splits: null as SeasonSplitsResponse | null, isLoading: false, hasError: false };
    }

    const entry = cache[buildKey(playerId, seasonKey, normalizedOpponent)];
    return {
      splits: entry?.status === 'ok' ? entry.payload : null,
      isLoading: entry === undefined,
      hasError: entry?.status === 'error',
    };
  }, [cache, normalizedOpponent, playerId]);

  return { getSplits };
}

/**
 * Per-panel season state.
 *
 * Panels start on the live season and are moved one at a time, so changing the
 * shot-type panel does not silently change the shooting-zone panel next to it.
 * Selecting a different player resets them: a season you chose because LeBron's
 * October sample was thin says nothing about the next player you open.
 */
export function usePanelSeasons<K extends string>(panelIds: readonly K[], resetKey: unknown) {
  const buildInitial = () => Object.fromEntries(
    panelIds.map((id) => [id, LIVE_SEASON]),
  ) as Record<K, string>;

  const [seasons, setSeasons] = useState<Record<K, string>>(buildInitial);

  useEffect(() => {
    setSeasons(buildInitial());
    // buildInitial is stable in everything that matters (the panel id list is a
    // module constant); the reset is driven by resetKey alone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  const setPanelSeason = useCallback((panelId: K, season: string) => {
    setSeasons((previous) => (
      previous[panelId] === season ? previous : { ...previous, [panelId]: season }
    ));
  }, []);

  return { panelSeasons: seasons, setPanelSeason };
}
