"""Single source of truth for prop-type naming.

`PROP_MAP` translates the raw `prop_type` strings the sportsbook scrapers emit
into the internal stat keys used by `master_feed.json`, the `player_props`
table, and the archive writers. It used to be copy-pasted into the aggregator,
the closing-line cron, and the props upsert; drift between those copies meant
the feed and the DB could disagree about what a stat type is.
"""

PROP_MAP = {
    'points':   'PTS',
    'rebounds': 'REB',
    'assists':  'AST',
    'threes':   'FG3M',
    'blocks':   'BLK',
    'steals':   'STL',
    'pra':      'PTS+REB+AST',
    'pr':       'PTS+REB',
    'pa':       'PTS+AST',
    'ra':       'REB+AST',
    'stocks':   'STL+BLK',
}
