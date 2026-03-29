import type { Player, PlayerProps, PlayerPropsByDate, PropLine, SportsbookId } from '../types.js';
import { getDashboardDate } from './dashboardDate.js';

const UNDATED_PROP_KEY = '__undated__';
const INTRADAY_SPORTSBOOK_MAP: Record<string, string> = {
  draftkings: 'dk',
  fanduel: 'fd',
  pp: 'pp',
  prizepicks: 'pp',
  dk: 'dk',
  fd: 'fd',
};

function getPropDateKey(gameDate?: string | null) {
  return gameDate || UNDATED_PROP_KEY;
}

function getPropsByDate(player: Player): PlayerPropsByDate {
  if (player.props_by_date) {
    return player.props_by_date;
  }

  const propsByDate: PlayerPropsByDate = {};

  Object.entries(player.props ?? {}).forEach(([statType, sportsbookMap]) => {
    Object.entries(sportsbookMap ?? {}).forEach(([sportsbook, prop]) => {
      if (!prop) return;
      propsByDate[statType] ??= {};
      propsByDate[statType][sportsbook] ??= {};
      propsByDate[statType][sportsbook][getPropDateKey(prop.game_date)] = prop;
    });
  });

  return propsByDate;
}

function getDatedKeys(propBucket: Record<string, PropLine> | undefined) {
  return Object.keys(propBucket ?? {})
    .filter((key) => key !== UNDATED_PROP_KEY)
    .sort();
}

function resolvePropBucket(
  propBucket: Record<string, PropLine> | undefined,
  preferredDate?: string | null,
) {
  if (!propBucket) return undefined;

  const datedKeys = getDatedKeys(propBucket);
  const hasDatedProps = datedKeys.length > 0;

  if (preferredDate) {
    if (propBucket[preferredDate]) {
      return propBucket[preferredDate];
    }

    if (!hasDatedProps && propBucket[UNDATED_PROP_KEY]) {
      return propBucket[UNDATED_PROP_KEY];
    }

    return undefined;
  }

  return propBucket[UNDATED_PROP_KEY] ?? (datedKeys[0] ? propBucket[datedKeys[0]] : undefined);
}

function applyIntradayOverrides(
  player: Player,
  flattenedProps: PlayerProps,
  preferredDate?: string | null,
): PlayerProps {
  const movements = Array.isArray(player.intraday_movements) ? player.intraday_movements : [];
  if (!movements.length) return flattenedProps;

  const playerId = String(player.id);
  const targetDate = preferredDate ?? player.active_game_date ?? getDashboardDate();
  const nextProps: PlayerProps = { ...flattenedProps };
  const seen = new Set<string>();

  for (let idx = movements.length - 1; idx >= 0; idx -= 1) {
    const snapshot = movements[idx];
    const playerData = snapshot?.players?.[playerId];
    if (!playerData?.props) continue;

    const snapshotDate = playerData.game_date ?? targetDate ?? null;
    if (targetDate && playerData.game_date !== targetDate) {
      continue;
    }

    Object.entries(playerData.props).forEach(([statType, sportsbookMap]) => {
      Object.entries(sportsbookMap ?? {}).forEach(([sportsbook, prop]) => {
        if (!prop) return;

        const mappedSportsbook = INTRADAY_SPORTSBOOK_MAP[sportsbook] || sportsbook;
        const overrideKey = `${statType}:${mappedSportsbook}`;
        if (seen.has(overrideKey)) return;
        seen.add(overrideKey);

        nextProps[statType] ??= {};
        nextProps[statType][mappedSportsbook] = {
          ...nextProps[statType][mappedSportsbook],
          ...prop,
          game_date: snapshotDate ?? nextProps[statType][mappedSportsbook]?.game_date,
          game_id: playerData.game_id ?? nextProps[statType][mappedSportsbook]?.game_id,
        };
      });
    });
  }

  return nextProps;
}

export function playerHasAnyProp(player: Player) {
  const propsByDate = getPropsByDate(player);

  return Object.values(propsByDate).some((sportsbookMap) =>
    Object.values(sportsbookMap).some((propBucket) => Object.keys(propBucket ?? {}).length > 0),
  );
}

export function playerHasPropForDate(player: Player, statType: string, gameDate?: string | null) {
  const propsByDate = getPropsByDate(player);
  const sportsbookMap = propsByDate[statType];

  if (!sportsbookMap) return false;

  return Object.values(sportsbookMap).some((propBucket) => !!resolvePropBucket(propBucket, gameDate));
}

export function getSportsbookProp(
  player: Player,
  statType: string,
  sportsbook: SportsbookId | string,
  gameDate?: string | null,
) {
  const propsByDate = getPropsByDate(player);
  const prop = resolvePropBucket(propsByDate[statType]?.[sportsbook], gameDate);
  if (!prop) {
    return null;
  }

  return {
    book: sportsbook,
    prop,
  };
}

export function playerHasSportsbookPropForDate(
  player: Player,
  statType: string,
  sportsbook: SportsbookId | string,
  gameDate?: string | null,
) {
  return Boolean(getSportsbookProp(player, statType, sportsbook, gameDate));
}

export function getPreferredSportsbookProp(
  player: Player,
  statType: string,
  gameDate?: string | null,
  preferredSportsbooks: string[] = ['dk', 'fd'],
) {
  const propsByDate = getPropsByDate(player);
  const sportsbookMap = propsByDate[statType];

  if (!sportsbookMap) return null;

  const orderedSportsbooks = [
    ...preferredSportsbooks.filter((sportsbook) => sportsbookMap[sportsbook]),
    ...Object.keys(sportsbookMap).filter((sportsbook) => !preferredSportsbooks.includes(sportsbook)),
  ];

  for (const sportsbook of orderedSportsbooks) {
    const prop = resolvePropBucket(sportsbookMap[sportsbook], gameDate);
    if (prop) {
      return { book: sportsbook, prop };
    }
  }

  return null;
}

export function getResolvedPlayerGameDate(player: Player, preferredDate?: string | null) {
  const propsByDate = getPropsByDate(player);
  const allDates = Array.from(new Set(
    Object.values(propsByDate)
      .flatMap((sportsbookMap) => Object.values(sportsbookMap))
      .flatMap((propBucket) => getDatedKeys(propBucket)),
  )).sort();

  if (preferredDate && allDates.includes(preferredDate)) {
    return preferredDate;
  }

  const dashboardDate = getDashboardDate();
  if (allDates.includes(dashboardDate)) {
    return dashboardDate;
  }

  return allDates[0] ?? null;
}

export function materializePlayerForGameDate(player: Player, gameDate?: string | null): Player {
  const propsByDate = getPropsByDate(player);
  const flattenedProps: PlayerProps = {};

  Object.entries(propsByDate).forEach(([statType, sportsbookMap]) => {
    Object.entries(sportsbookMap).forEach(([sportsbook, propBucket]) => {
      const prop = resolvePropBucket(propBucket, gameDate);
      if (!prop) return;

      flattenedProps[statType] ??= {};
      flattenedProps[statType][sportsbook] = prop;
    });
  });

  const resolvedProps = applyIntradayOverrides(player, flattenedProps, gameDate);

  return {
    ...player,
    props: resolvedProps,
    props_by_date: propsByDate,
    active_game_date: gameDate ?? null,
  };
}
