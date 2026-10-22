#!/usr/bin/env python3
"""
test_preview_discord.py
Inspect, and optionally post, the below-threshold Discord preview.

The preview only appears when nothing on the board clears
EDGE_SCORE_DISCORD_MIN_SIGNAL_SCORE, which during a normal week means it may
not appear for days. This script renders it on demand from the current board so
the formatting can be checked without waiting for a qualifying slate.

Dry run by default -- it prints what would be sent and posts nothing. Pass
--send to actually deliver one message to the configured webhook.

    python3 test_preview_discord.py            # render only
    python3 test_preview_discord.py --send      # render and post once
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import edge_score as es  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send",
        action="store_true",
        help="actually post the preview to the webhook (default: dry run)",
    )
    parser.add_argument(
        "--path",
        default=es.EDGE_SCORE_PATH,
        help="edge score payload to render (default: the live board)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"No edge score payload at {args.path}", file=sys.stderr)
        return 1

    with open(args.path) as handle:
        payload = json.load(handle)

    recommendations = payload.get("recommendations") or []
    official = es._filter_official_alert_recommendations(recommendations)
    preview = es._filter_preview_recommendations(recommendations)

    print(f"board:            {len(recommendations)} recommendations")
    print(f"generated_at:     {payload.get('generated_at')}")
    print(f"alert threshold:  {es.EDGE_DISCORD_MIN_SIGNAL_SCORE}")
    print(f"clearing it:      {len(official)}")
    print(f"preview picks:    {len(preview)} (enabled={es.EDGE_DISCORD_PREVIEW_ENABLED})")
    print()

    if official:
        print(
            "NOTE: real alert candidates exist, so in the live pipeline the preview "
            "would be suppressed. Rendering it anyway for inspection."
        )
        print()

    discord_payload = es._build_discord_preview_payload(payload, preview)
    if not discord_payload:
        print("Nothing to preview (no eligible pregame picks on the board).")
        return 0

    for recommendation in preview:
        print(
            f"  rank {recommendation.get('rank')}  "
            f"{recommendation.get('player_name')}  "
            f"{recommendation.get('stat_type')} {recommendation.get('pick')} "
            f"{recommendation.get('line')}  [{recommendation.get('sportsbook')}]  "
            f"score={es._ranking_edge_score(recommendation):.1f}"
        )
    print()
    print(json.dumps(discord_payload, indent=2)[:4000])

    if not args.send:
        print()
        print("Dry run. Pass --send to post this to Discord.")
        return 0

    webhook_url = (
        es.EDGE_SCORE_DISCORD_PREVIEW_WEBHOOK_URL
        or es.EDGE_SCORE_DISCORD_WEBHOOK_URL
        or es.EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL
    )
    if not webhook_url:
        print("No preview/alert/tracker webhook configured.", file=sys.stderr)
        return 1

    response = es._post_discord_webhook(webhook_url, discord_payload)
    if not response:
        print("Post failed; see logs above.", file=sys.stderr)
        return 1
    print(f"Posted. channel_id={response.get('channel_id')} message_id={response.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
