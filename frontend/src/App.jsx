import { useState } from 'react'
import PlayerCard from './components/PlayerCard'

// --- DUMMY DATA FOR TESTING (Matches your JSON structure) ---
const TEST_PLAYER = {
  "id": 2544,
  "name": "LeBron James",
  "team": "LAL",
  "game_log": [
    // 3 Fake games to test the chart
    { "DATE_STR": "02/05", "MATCHUP": "LAL vs GSW", "PTS": 28, "REB": 10, "AST": 8 },
    { "DATE_STR": "02/03", "MATCHUP": "LAL @ NYK", "PTS": 15, "REB": 5, "AST": 5 },
    { "DATE_STR": "02/01", "MATCHUP": "LAL vs BOS", "PTS": 32, "REB": 8, "AST": 12 }
  ],
  "props": {
    "PTS": {
      "dk": { "line": 24.5, "over": "-110", "under": "-110" },
      "fd": { "line": 23.5, "over": "-115", "under": "-105" }
    }
  }
}

function App() {
  return (
    <div className="min-h-screen bg-slate-950 p-8">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-3xl font-bold text-white mb-8 text-center">
          NBA Prop Dashboard
        </h1>

        {/* Render the Component */}
        <PlayerCard player={TEST_PLAYER} />

      </div>
    </div>
  )
}

export default App