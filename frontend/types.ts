
// --- Real Backend Types ---

export interface GameLog {
  SEASON_ID: string;
  PLAYER_ID: string;
  PLAYER_NAME: string;
  TEAM_ID: number;
  TEAM_ABBREVIATION: string;
  TEAM_NAME: string;
  GAME_ID: string;
  GAME_DATE: string;
  MATCHUP: string;
  WL: string;
  MIN: number;
  FGM: number;
  FGA: number;
  FG_PCT: number;
  FG3M: number;
  FG3A: number;
  FG3_PCT: number;
  FTM: number;
  FTA: number;
  FT_PCT: number;
  OREB: number;
  DREB: number;
  REB: number;
  AST: number;
  STL: number;
  BLK: number;
  TOV: number;
  PF: number;
  PTS: number;
  PLUS_MINUS: number;
  FANTASY_PTS: number;
  VIDEO_AVAILABLE: number;
  DATE_STR: string;
  POTENTIAL_AST: number;
  AST_POINTS_CREATED: number;
  REB_CHANCES: number;
  REB_CONTEST_PCT: number;
  DRIVES: number;
  DRIVE_PTS: number;
  DRIVE_PASSES: number;
  "PTS+REB+AST": number;
  "PTS+REB": number;
  "PTS+AST": number;
  "REB+AST": number;
  "STL+BLK": number;
  [key: string]: any; // Allow for other computed stats
}

export interface PlayerStats {
  PLAYER_ID: number;
  TEAM_ABBREVIATION: number | string;
  MIN: number;
  GP: number;
  PTS: number;
  FGM: number;
  FGA: number;
  FG_PCT: number;
  FG3M: number;
  FG3A: number;
  FG3_PCT: number;
  FTM: number;
  FTA: number;
  FT_PCT: number;
  PLUS_MINUS: number;
  REB: number;
  OREB: number;
  DREB: number;
  AST: number;
  TOV: number;
  STL: number;
  BLK: number;
  PF: number;
  POTENTIAL_AST: number;
  DRIVES: number;
  DRIVE_PTS: number;
  REB_CHANCES: number;
  "PTS+REB+AST": number;
  "PTS+REB": number;
  "PTS+AST": number;
  "REB+AST": number;
  "STL+BLK": number;
  [key: string]: any;
}

export interface PropLine {
  line: number;
  over: number;
  under: number;
}

export interface PlayerProps {
  [statType: string]: {
    [sportsbook: string]: PropLine;
  };
}

export interface Player {
  id: number;
  name: string;
  team: string; // Tricode like 'GSW'
  position?: string; // Not always in master_feed, might be in 'stats'
  stats: PlayerStats;
  game_log: GameLog[];
  props: PlayerProps;
}

export interface Game {
  game_id: string;
  game_code: string;
  home_team_id: number;
  home_team_name: string;
  home_team_city: string;
  home_team_tricode: string;
  home_team_wins: number;
  home_team_losses: number;
  home_score: number;
  away_team_id: number;
  away_team_name: string;
  away_team_city: string;
  away_team_tricode: string;
  away_team_wins: number;
  away_team_losses: number;
  away_score: number;
  arena_name: string;
  arena_city: string;
  arena_state: string;
  arena_full: string;
  game_time_utc: string;
  game_time_et: string;
  game_date: string;
  game_weekday: string;
  game_et: string;
  game_status: number;
  game_status_text: string;
  is_live: boolean;
  is_final: boolean;
  is_scheduled: boolean;
  period: number;
  game_clock: string;
  regulation_periods: number;
  home_leader_name: string;
  home_leader_points: number;
  home_leader_rebounds: number;
  home_leader_assists: number;
  away_leader_name: string;
  away_leader_points: number;
  away_leader_rebounds: number;
  away_leader_assists: number;
  [key: string]: any;
}

// --- Legacy / UI Specific Types (can be deprecated or adapted) ---

export interface PlayTypeData {
  type: string;
  points: string;
  percent: string;
  rank: number;
}

export interface SimilarPlayerGame {
  date: string;
  team: string;
  player: string;
  line: number;
  result: number;
  diffPercent: number;
}
