import { Player, PlayerProps, PlayerPropsByDate, PropLine } from '../types';
import { getDashboardDate } from './dashboardDate';

const UNDATED_PROP_KEY = '__undated__';

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

  return {
    ...player,
    props: flattenedProps,
    props_by_date: propsByDate,
    active_game_date: gameDate ?? null,
  };
}
