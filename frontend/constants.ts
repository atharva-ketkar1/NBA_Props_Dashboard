import { Player, GameLog, PlayTypeData, SimilarPlayerGame } from './types';

// export const CURRENT_PLAYER: Player = {
//   id: 'lebron-james',
//   name: 'LeBron James',
//   team: 'LAL',
//   position: 'F',
//   image: 'https://picsum.photos/id/1005/200/200', // Placeholder
//   stats: {
//     pts: 22.4,
//     ast: 6.4,
//     reb: 5.8,
//     threepm: 1.5,
//     mins: 33.1,
//     usage: '11.4', // Re-purposing for POT AST based on screenshot
//     fga: 46.5,    // Re-purposing for PASSES based on screenshot
//     diffs: {
//       pts: 4.8,
//       ast: -2.3,
//       reb: 0.8,
//       threepm: 0.1,
//       mins: 1.1,
//       usage: '-2.0',
//       fga: -2.5
//     }
//   },
//   propLine: 6.5,
//   propType: 'Ast',
//   lineOdds: {
//     over: -122,
//     under: -108
//   }
// };

export const RECENT_GAMES: GameLog[] = [
  // Mock data kept for reference or fallback if needed, but likely unused now
  // { date: 'Nov 28', opponent: 'PHI', opponentLogo: 'PHI', score: 3, isHome: false },
  // ... (commenting out just to be safe or delete)
] as any;

// ... existing RECENT_GAMES content was array of objects that don't match GameLog perfectly either.
// I'll just comment the whole thing out or leave it if I didn't verify RECENT_GAMES usage in other files.
// BarChart used to use it, but I refactored it.
// I'll comment it out.


export const PLAY_TYPES: PlayTypeData[] = [
  { type: 'Transition', points: '7 (27%)', percent: '27%', rank: 12 },
  { type: 'Free Throws', points: '4.1 (16%)', percent: '16%', rank: 16 },
  { type: 'PNR Ball Handler', points: '2.6 (10%)', percent: '10%', rank: 28 },
  { type: 'Spot Up', points: '2.6 (10%)', percent: '10%', rank: 14 },
  { type: 'Isolation', points: '2.4 (9%)', percent: '9%', rank: 2 },
  { type: 'Post Up', points: '2.3 (9%)', percent: '9%', rank: 2 },
];

export const SIMILAR_GAMES: SimilarPlayerGame[] = [
  { date: 'Jan 19', team: 'Pacers', player: 'P. Siakam', line: 23.5, result: 24, diffPercent: 2 },
  { date: 'Jan 12', team: 'Raptors', player: 'S. Barnes', line: 18.5, result: 15, diffPercent: -19 },
  { date: 'Jan 11', team: 'Raptors', player: 'S. Barnes', line: 20.5, result: 31, diffPercent: 51 },
  { date: 'Dec 30', team: 'Grizzlies', player: 'J. Jackson Jr.', line: 19.5, result: 15, diffPercent: -23 },
  { date: 'Dec 14', team: 'Hawks', player: 'J. Johnson', line: 24.5, result: 12, diffPercent: -51 },
  { date: 'Dec 12', team: 'Pacers', player: 'P. Siakam', line: 23.5, result: 20, diffPercent: -15 },
  { date: 'Nov 30', team: 'Hawks', player: 'J. Johnson', line: 23.5, result: 41, diffPercent: 74 },
  { date: 'Nov 25', team: 'Magic', player: 'F. Wagner', line: 25.5, result: 21, diffPercent: -18 },
];

// export const GAME_LIST: GameListItem[] = [
//   { id: '1', homeTeam: { name: 'Nets', logo: 'BKN' }, awayTeam: { name: 'Magic', logo: 'ORL' }, time: '7:00 PM', day: 'Thu' },
//   { id: '2', homeTeam: { name: 'Wizards', logo: 'WAS' }, awayTeam: { name: 'Pistons', logo: 'DET' }, time: '7:00 PM', day: 'Thu' },
//   { id: '3', homeTeam: { name: 'Bulls', logo: 'CHI' }, awayTeam: { name: 'Raptors', logo: 'TOR' }, time: '7:30 PM', day: 'Thu' },
//   { id: '4', homeTeam: { name: 'Jazz', logo: 'UTA' }, awayTeam: { name: 'Hawks', logo: 'ATL' }, time: '7:30 PM', day: 'Thu' },
//   { id: '5', homeTeam: { name: 'Hornets', logo: 'CHA' }, awayTeam: { name: 'Rockets', logo: 'HOU' }, time: '8:00 PM', day: 'Thu' },
//   { id: '6', homeTeam: { name: 'Spurs', logo: 'SAS' }, awayTeam: { name: 'Mavs', logo: 'DAL' }, time: '8:30 PM', day: 'Thu' },
//   { id: '7', homeTeam: { name: 'Warriors', logo: 'GSW' }, awayTeam: { name: 'Suns', logo: 'PHX' }, time: '10:00 PM', day: 'Thu' },
// ... existing code ...

// Map of Tricode to NBA Team ID
export const TEAM_IDS: Record<string, number> = {
  "ATL": 1610612737,
  "BOS": 1610612738,
  "BKN": 1610612751,
  "CHA": 1610612766,
  "CHI": 1610612741,
  "CLE": 1610612739,
  "DAL": 1610612742,
  "DEN": 1610612743,
  "DET": 1610612765,
  "GSW": 1610612744,
  "HOU": 1610612745,
  "IND": 1610612754,
  "LAC": 1610612746,
  "LAL": 1610612747,
  "MEM": 1610612763,
  "MIA": 1610612748,
  "MIL": 1610612749,
  "MIN": 1610612750,
  "NOP": 1610612740,
  "NYK": 1610612752,
  "OKC": 1610612760,
  "ORL": 1610612753,
  "PHI": 1610612755,
  "PHX": 1610612756,
  "POR": 1610612757,
  "SAC": 1610612758,
  "SAS": 1610612759,
  "TOR": 1610612761,
  "UTA": 1610612762,
  "WAS": 1610612764
};