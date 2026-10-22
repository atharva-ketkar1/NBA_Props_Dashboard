/**
 * Single source of truth for the stat-tab labels and the backend stat keys
 * they map to.
 *
 * This map used to be copy-pasted into App.tsx, Header.tsx, BarChart.tsx and
 * Sidebar.tsx. The copies drifted: tabs added to the header (Stl+Blk, Fouls,
 * FT Attempted, the quarter/half markets, Double/Triple Double) were never
 * added to the others, and every lookup there falls back to 'PTS', so those
 * tabs silently charted and filtered points instead of the selected stat.
 * Import from here instead of re-declaring it.
 */

export const STAT_LABELS: Record<string, string> = {
  'Points': 'PTS',
  'Assists': 'AST',
  'Rebounds': 'REB',
  'Threes': 'FG3M',
  'Pts+Ast': 'PTS+AST',
  'Pts+Reb': 'PTS+REB',
  'Reb+Ast': 'REB+AST',
  'Pts+Reb+Ast': 'PTS+REB+AST',
  'Fantasy': 'FAN',
  'Blocks': 'BLK',
  'Steals': 'STL',
  'Stl+Blk': 'STL+BLK',
  'Turnovers': 'TOV',
  'Fouls': 'PF',
  'FT Attempted': 'FTA',
  'Double Double': 'DD2',
  'Triple Double': 'TD3',
  '1Q Points': '1Q_PTS',
  '1Q Assists': '1Q_AST',
  '1Q Rebounds': '1Q_REB',
  '1H Points': '1H_PTS',
};

/** Order the stat tabs are rendered in, in the header and the sidebar filter. */
export const TAB_ORDER = [
  'Points', 'Assists', 'Rebounds', 'Threes', 'Pts+Ast', 'Pts+Reb', 'Reb+Ast', 'Pts+Reb+Ast',
  'Double Double', 'Triple Double', '1Q Points', '1Q Assists', '1Q Rebounds', '1H Points',
  'Steals', 'Blocks', 'Stl+Blk', 'Turnovers', 'Fouls', 'FT Attempted',
];
