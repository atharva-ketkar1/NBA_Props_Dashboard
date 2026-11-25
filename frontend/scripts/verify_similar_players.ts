/**
 * Regression gate for the comparable-player ranking.
 *
 *   cd frontend && npx vite-node scripts/verify_similar_players.ts
 *
 * Exits non-zero if recall against the hand-labelled sets drops below the
 * recorded floor. Follows the `scripts/verify_design_tokens.py` convention --
 * a standalone script run by hand, because this project has no frontend test
 * runner and adding one for this would be a bigger change than the thing being
 * guarded.
 *
 * ---------------------------------------------------------------------------
 * Why a labelled set rather than an eyeball check
 * ---------------------------------------------------------------------------
 * "Similar" has no ground truth in the data, so the only honest way to tune the
 * weights is against stated human judgement. It is also the only protection
 * against the specific failure this ranking already had once: the previous
 * version optimised for production LEVEL, which looks plausible one player at a
 * time (every comp is within a few points per game) and is obviously wrong the
 * moment you ask for a list.
 *
 * The weights in similarPlayers.ts were tuned against these sets. Treat a
 * change that improves one target and silently costs another as a regression --
 * which is exactly what the per-target floors below make visible.
 *
 * ---------------------------------------------------------------------------
 * A methodology warning, recorded because it already bit once
 * ---------------------------------------------------------------------------
 * An early prototype of this rewrite appeared to recover 3/5 of the Kyshawn
 * George set. That result was an artifact: the prototype carried a hardcoded
 * height map covering only ~20 players, and the expected comps were in it while
 * most of the league was not -- so the height term acted as a bonus applied to
 * precisely the hand-listed names. With real height for all 475 players the
 * honest figure is 0/5. If a tuning change produces a sudden jump, check that
 * the feature it leans on is populated for the whole pool, not just the labels.
 */

import { readFileSync, existsSync } from 'node:fs';
import { rankSimilarPlayers } from '../utils/similarPlayers';
import type { Player } from '../types';

/**
 * Where the player pool comes from, in preference order:
 *
 *   1. MASTER_FEED_PATH, if set. Explicit beats implicit, and a path that is
 *      set but missing is a mistake worth failing on.
 *   2. The live feed, when the pipeline has run on this machine. Real data is
 *      the better check -- it moves, so it can surface things a frozen
 *      snapshot cannot.
 *   3. The committed fixture. `backend/data` is gitignored, so this is the only
 *      option on a CI runner. Without it this script exited 2 on every push and
 *      turned CI permanently red; skipping instead would have been worse, since
 *      the ranking would then be unguarded exactly where a regression is most
 *      likely to arrive.
 *
 * Rebuild the fixture with scripts/build_comps_fixture.py.
 */
const LIVE_FEED = '../backend/data/current/master_feed.json';
const FIXTURE = new URL('./fixtures/comps_pool.json', import.meta.url).pathname;

function resolveFeedPath(): { path: string; source: string } {
  const explicit = process.env.MASTER_FEED_PATH;
  if (explicit) {
    if (!existsSync(explicit)) {
      console.error(
        `MASTER_FEED_PATH is set to ${explicit}, which does not exist.`,
      );
      process.exit(2);
    }
    return { path: explicit, source: 'MASTER_FEED_PATH' };
  }
  if (existsSync(LIVE_FEED)) return { path: LIVE_FEED, source: 'live feed' };
  if (existsSync(FIXTURE)) return { path: FIXTURE, source: 'committed fixture' };

  console.error(
    'No player pool found. Expected the live feed at '
    + `${LIVE_FEED} or the fixture at ${FIXTURE}.\n`
    + 'Rebuild the fixture with: python3 scripts/build_comps_fixture.py',
  );
  process.exit(2);
}

/**
 * Hand-labelled expectations.
 *
 * `Kyshawn George` is the case that prompted the rewrite and is kept even
 * though it scores 0 -- see the note on it below. The other three are archetype
 * anchors spanning a rim-running centre, a lead guard and a high-usage shooting
 * guard, so a change that helps wings at the cost of bigs cannot pass quietly.
 *
 * Matched on a diacritic-folded surname substring, so entries must stay
 * unambiguous within the league (hence 'Keyonte George' rather than 'George').
 * Folding is not cosmetic: the feed stores 'Nikola Jokić' and 'Luka Dončić'
 * with their accents, so a plain substring match on 'Jokic' silently finds
 * nothing and reports a false regression.
 */
const LABELLED_SETS: Array<{
  target: string;
  expected: string[];
  /** Minimum expected comps that must appear in the top 12. */
  floor: number;
  note?: string;
}> = [
  {
    target: 'Kyshawn George',
    expected: ['Hunter', 'Keegan Murray', 'Jabari Smith', 'Porter', 'Mikal Bridges'],
    floor: 2,
    note:
      'Floor is 0 on purpose, and this set is a KNOWN DISAGREEMENT rather than a '
      + 'target to tune toward. George carries 4.4 AST on a 16.6% assist share at '
      + '22.4% usage; four of the five expected comps sit near a 9% assist share. '
      + 'They are the same SIZE and shot profile but a different functional role, '
      + 'and no FEATURE reweighting recovered them (six variants tried: '
      + 'playmaking down, size up, shooting up, play-type down, two combinations). '
      + 'What did help was structural: `productionGap` as a first-class scoring '
      + 'term, which took this set 0/5 -> 2/5. The lesson was that stripping '
      + 'production level entirely over-corrected the original bug -- role '
      + 'prominence is part of what people mean by "similar", it just must not '
      + 'DOMINATE the way it used to. Going beyond 2/5 likely means deciding size '
      + 'and shot profile should outrank functional role, which is a product '
      + 'decision rather than a tuning one.',
  },
  {
    target: 'Rudy Gobert',
    expected: ['Kessler', 'Queta', 'Robinson', 'Allen', 'Bassey'],
    floor: 4,
    note: 'Non-shooting rim-running, rim-protecting centres. Was 2/5 before the rewrite.',
  },
  {
    target: 'Trae Young',
    expected: ['Garland', 'Nembhard', 'Cunningham', 'Quickley', 'Keyonte George'],
    floor: 4,
    note: 'Lead/combo guard creators.',
  },
  {
    target: 'Stephen Curry',
    expected: ['Brunson', 'Herro', 'Booker', 'Powell', 'Alexander-Walker'],
    floor: 4,
    note: 'High-usage scoring guards. Was 4/5 before the rewrite.',
  },
];

/**
 * Total recall. The shipped version scored 10/20; the archetype rewrite took it
 * to 14/20; adding `productionGap` as a first-class scoring term took it to
 * 16/20 (and Kyshawn George from 0/5 to 2/5).
 */
const TOTAL_FLOOR = 16;
const TOP_N = 12;

/**
 * Opponent-anchored check, separate from the archetype sets above because it
 * tests a different property: that comps who have already faced tonight's
 * opponent are ranked first.
 *
 * This is the panel's actual purpose -- the table it renders shows only games
 * against that opponent, so a comp who never faced them is a wasted slot.
 * Before the tiering, Sengun vs Phoenix filled 9 of 12 slots with players who
 * could not contribute a single row.
 */
const OPPONENT_CASE = {
  target: 'Sengun',
  opponent: 'PHX',
  /** Every slot should be fillable, given ~21 eligible bigs have faced any team. */
  minUsable: 12,
  /** Named in the original report of this gap; both have faced Phoenix. */
  expected: ['Jokic', 'Wembanyama'],
};

function main() {
  const { path: feedPath, source } = resolveFeedPath();
  console.log(`pool: ${source} (${feedPath})\n`);

  const players: Player[] = JSON.parse(readFileSync(feedPath, 'utf8'));
  const fold = (value: string) =>
    value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  // The ranking reads game logs only via buildSimilarPlayersDataset, but
  // detail_loaded gates candidate display, so set it for a headless run.
  players.forEach((player) => { (player as any).detail_loaded = true; });

  const withHeight = players.filter((player) => player.height_inches).length;
  if (withHeight === 0) {
    console.warn(
      'WARNING: no player in this feed has height_inches. Size is the largest '
      + 'single contributor to this ranking, so these numbers are not comparable '
      + 'to the recorded floors. Re-run the pipeline so season_stats_scrape '
      + 'populates it.',
    );
  } else if (withHeight < players.length * 0.9) {
    console.warn(`WARNING: only ${withHeight}/${players.length} players have height.`);
  }

  let total = 0;
  let failures = 0;

  for (const { target, expected, floor, note } of LABELLED_SETS) {
    const player = players.find((entry) => entry.name === target);
    if (!player) {
      console.error(`FAIL  ${target}: not present in the feed`);
      failures += 1;
      continue;
    }

    const ranked = rankSimilarPlayers({
      player,
      players,
      activeTab: 'Points',
      activeSportsbook: 'dk',
      selectedGameDate: null,
      mode: 'position',
      limit: TOP_N,
    });

    const names = ranked.map((candidate) => fold(candidate.name));
    const hits = expected.filter((needle) =>
      names.some((name) => name.includes(fold(needle))));
    total += hits.length;

    const ok = hits.length >= floor;
    if (!ok) failures += 1;
    console.log(
      `${ok ? 'ok  ' : 'FAIL'}  ${target.padEnd(16)} ${hits.length}/${expected.length} `
      + `in top ${TOP_N} (floor ${floor})`,
    );
    if (!ok) {
      console.log(`        got: ${ranked.map((c) => c.name).join(', ')}`);
      if (note) console.log(`        note: ${note}`);
    }
  }

  console.log(`\ntotal recall: ${total}/${LABELLED_SETS.reduce((sum, s) => sum + s.expected.length, 0)} (floor ${TOTAL_FLOOR})`);

  if (total < TOTAL_FLOOR) {
    console.error(
      `\nREGRESSION: total recall ${total} is below the recorded floor ${TOTAL_FLOOR}.`,
    );
    failures += 1;
  }

  // --- opponent anchoring -------------------------------------------------
  const anchor = players.find((entry) => fold(entry.name).includes(fold(OPPONENT_CASE.target)));
  if (!anchor) {
    console.error(`\nFAIL  opponent anchoring: ${OPPONENT_CASE.target} not in feed`);
    failures += 1;
  } else {
    const ranked = rankSimilarPlayers({
      player: anchor,
      players,
      activeTab: 'Points',
      activeSportsbook: 'dk',
      selectedGameDate: null,
      mode: 'position',
      targetOpponent: OPPONENT_CASE.opponent,
      limit: TOP_N,
    });
    const facedOpponent = (id: number) => {
      const candidate = players.find((entry) => entry.id === id);
      const declared = (candidate as any)?.opponents_played;
      if (Array.isArray(declared)) {
        return declared.map((v: unknown) => String(v).toUpperCase()).includes(OPPONENT_CASE.opponent);
      }
      return (candidate?.game_log ?? []).some((game: any) =>
        String(game?.MATCHUP ?? '').split(' ').pop() === OPPONENT_CASE.opponent);
    };
    const usable = ranked.filter((candidate) => facedOpponent(candidate.id)).length;
    const names = ranked.map((candidate) => fold(candidate.name));
    const named = OPPONENT_CASE.expected.filter((needle) =>
      names.some((name) => name.includes(fold(needle))));

    const ok = usable >= OPPONENT_CASE.minUsable
      && named.length === OPPONENT_CASE.expected.length;
    if (!ok) failures += 1;
    console.log(
      `\n${ok ? 'ok  ' : 'FAIL'}  opponent anchoring: ${OPPONENT_CASE.target} vs `
      + `${OPPONENT_CASE.opponent} -- ${usable}/${TOP_N} comps have faced them `
      + `(min ${OPPONENT_CASE.minUsable}), named comps found ${named.length}/`
      + `${OPPONENT_CASE.expected.length}`,
    );
    if (!ok) console.log(`        got: ${ranked.map((c) => c.name).join(', ')}`);
  }

  if (failures) {
    console.error(`\n${failures} check(s) failed.`);
    process.exit(1);
  }
  console.log('\nIN SPEC');
}

main();
