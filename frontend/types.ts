
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
  USG_PCT?: number;
  [key: string]: any;
}

export interface PropLine {
  line: number;
  over: number | null;
  under: number | null;
  implied?: number | null;
  game_date?: string;
  game_id?: string;
  updated_at?: string;
}

export type SportsbookId = 'dk' | 'fd' | 'mgm' | 'cz' | 'pp';

export interface PlayerProps {
  [statType: string]: {
    [sportsbook: string]: PropLine;
  };
}

export interface PlayerPropsByDate {
  [statType: string]: {
    [sportsbook: string]: {
      [gameDate: string]: PropLine;
    };
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
  props_by_date?: PlayerPropsByDate;
  active_game_date?: string | null;
  historical_odds?: Record<string, any>;
  intraday_movements?: any[];
  [key: string]: any; // Allow generic injection like shot_type_analysis
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
  has_injury_report?: boolean;
  injury_teams?: Record<string, TeamInjuryReport>;
  injury_source?: string | null;
  injury_report_timestamp_et?: string | null;
  injury_source_generated_at?: string | null;
  injury_updated_at?: string | null;
  [key: string]: any;
}

export interface InjuryReportPlayer {
  player_name?: string | null;
  report_player_name?: string | null;
  current_status?: string | null;
  reason?: string | null;
}

export interface TeamInjuryReport {
  team_tricode: string;
  team_name?: string | null;
  report_status?: string | null;
  report_timestamp_et?: string | null;
  source_generated_at?: string | null;
  updated_at?: string | null;
  players: InjuryReportPlayer[];
}

export interface TeammateInjuryCard {
  playerId: number | null;
  playerName: string;
  displayName: string;
  position: string | null;
  currentStatus: string | null;
  reportStatus: string | null;
  reason: string | null;
  minutesPerGame: number | null;
  statPerGame: number | null;
  statImpact: number | null;
  impactSampleLabel: string | null;
  isImpactLoading: boolean;
  activeGameIds: string[];
  defaultFilterMode: TeammateFilterMode;
  prominenceScore: number;
}

export type TeammateFilterMode = 'with' | 'without';

export interface ActiveTeammateFilter {
  playerId: number | null;
  playerName: string;
  displayName: string;
  currentStatus: string | null;
  mode: TeammateFilterMode;
  activeGameIds: string[];
  isImpactLoading: boolean;
}

// --- Legacy / UI Specific Types (can be deprecated or adapted) ---

export interface PlayTypeData {
  type: string;
  points: string;
  percent: string;
  rank: number | string;
}

export interface ShotTypeData {
  type: string;
  percentage: number;
  attempts: number;
  width?: number; // Optional for manual width control, else calculated
  rank?: number;  // Opponent defense rank
  frequency?: number; // Calculated frequency of this shot type
}

export interface SimilarPlayerGame {
  playerId?: number;
  date: string;
  gameDate?: string;
  team: string;
  opponent?: string | null;
  player: string;
  line: number | null;
  result: number;
  diff?: number | null;
  diffPercent: number | null;
  hit?: boolean | null;
  similarityScore?: number;
  lineGap?: number;
  source?: string;
  hasHistoricalLine?: boolean;
}

export type SimilarPlayersMode = 'prop' | 'position';

export interface SimilarPlayerCandidate {
  id: number;
  name: string;
  team: string;
  position?: string;
  currentLine: number | null;
  currentAverage: number;
  similarityScore: number;
  detailLoaded: boolean;
}

export interface SimilarPlayersSummary {
  avgDiff: number;
  avgDiffPercent: number;
  hitRate: number;
  hits: number;
  total: number;
}

export interface EdgeScoreRecommendation {
  recommendation_key: string;
  rank: number;
  sportsbook_rank?: number;
  player_id: number;
  player_name: string;
  player_headshot_url?: string | null;
  team: string;
  opponent?: string | null;
  position?: string;
  game_id?: string | null;
  game_date?: string | null;
  game_time_et?: string | null;
  sportsbook: string;
  sportsbook_label: string;
  stat_type: string;
  stat_label: string;
  pick: 'over' | 'under';
  pick_label: string;
  line: number;
  odds?: number | null;
  odds_display?: string;
  opposite_odds?: number | null;
  edge_score: number;
  confidence: number;
  signal_score: number;
  reasons: string[];
  inputs: Record<string, any>;
  component_scores: Record<string, number>;
  available_component_weights?: number;
}

export interface EdgeScoreSportsbookBoard {
  sportsbook: string;
  sportsbook_label: string;
  count: number;
  limit: number;
  recommendations: EdgeScoreRecommendation[];
}

export interface EdgeScoreSummary {
  active_players?: number;
  candidate_count?: number;
  top_count?: number;
  available_books?: string[];
  sportsbook_board_limit?: number;
  sportsbook_boards?: Record<string, EdgeScoreSportsbookBoard>;
  duration_s?: number;
  scoring_model?: string;
  [key: string]: any;
}

export interface EdgeScorePayload {
  generated_at: string;
  refresh_label: string;
  game_dates: string[];
  summary: EdgeScoreSummary;
  recommendations: EdgeScoreRecommendation[];
  notification: Record<string, any>;
}
