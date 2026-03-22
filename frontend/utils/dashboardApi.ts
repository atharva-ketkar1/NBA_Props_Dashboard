import { fetchAppJson } from './network';

type BootstrapResponse = {
  playersRows: any[];
  propsRows: any[];
  gamesRows: any[];
  lineRows: any[];
  lineVersion: string;
};

type HotResponse = {
  propsRows: any[];
  lineVersion: string;
  lineRows?: any[];
};

type GamesResponse = {
  games: any[];
};

type PlayerResponse = {
  detail: Record<string, any>;
  historicalOddsRows: any[];
};

type ArchiveResponse = {
  gameLog: any[];
};

export function fetchDashboardBootstrap() {
  return fetchAppJson<BootstrapResponse>('/api/bootstrap');
}

export function fetchDashboardHot(selectedDate: string | null, lineVersion: string) {
  return fetchAppJson<HotResponse>('/api/hot', {
    lineVersion,
    selectedDate,
  });
}

export function fetchDashboardGames(dates: string[]) {
  return fetchAppJson<GamesResponse>('/api/games', {
    dates: dates.join(','),
  });
}

export function fetchDashboardPlayer(playerId: number) {
  return fetchAppJson<PlayerResponse>('/api/player', {
    playerId,
  });
}

export function fetchDashboardArchive(playerId: number, season: string) {
  return fetchAppJson<ArchiveResponse>('/api/archive', {
    playerId,
    season,
  });
}
