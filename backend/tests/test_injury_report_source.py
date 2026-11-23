"""The injury report's page URL, and what a page that cannot answer has to say.

NBA retired `official.nba.com/nba-injury-report-<season>-season/` around
2026-11-20; it now 301s to `www.nba.com/players/injury-report`. The proxy worker
refuses redirects, so every fetch raised `WorkerRejected` and the artifact froze
for three days -- long enough that 533 of 869 candidates were blocked on
`blocked_stale_injury_context` while the pipeline reported healthy.

Three of these tests fail against the pre-fix code on the URL alone. The two that
pin the error text are the ones with a longer life: the page now lists only
*today's* PDFs where the season page listed the whole season, so a date that is
not on the page is a routine outcome rather than an upstream break, and the two
must not read alike in a log.

`test_latest_pdf_for_the_date_is_chosen` is deliberately half pin, half bug test:
the newest-wins selection already worked, and only its URL and label assertions
fail today.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scrapers import fetch_nba_injury_report as injury
from utils.http_client import WORKER_ALLOWED_HOSTS

PDF_BASE = "https://ak-static.cms.nba.com/referee/injury"


def _page(*hrefs: str) -> str:
    anchors = "".join(f'<a href="{href}">report</a>' for href in hrefs)
    return f"<html><body>{anchors}</body></html>"


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class ReportPageUrlTests(unittest.TestCase):
    def test_report_page_url_is_the_live_nba_page(self):
        url = injury._report_page_url()
        self.assertEqual(url, "https://www.nba.com/players/injury-report")
        # The worker refuses any host outside its allowlist before it issues a
        # request, so a URL the allowlist does not name can never succeed.
        self.assertIn(urlparse(url).hostname, WORKER_ALLOWED_HOSTS)

    def test_the_page_url_can_be_overridden_without_a_deploy(self):
        override = "https://www.nba.com/players/injury-report?x=1"
        with patch.dict(os.environ, {"NBA_INJURY_REPORT_PAGE_URL": override}):
            self.assertEqual(injury._report_page_url(), override)

    def test_a_blank_override_falls_back_to_the_default(self):
        with patch.dict(os.environ, {"NBA_INJURY_REPORT_PAGE_URL": "   "}):
            self.assertEqual(injury._report_page_url(), injury.DEFAULT_REPORT_PAGE_URL)


class LatestPdfSelectionTests(unittest.TestCase):
    def test_latest_pdf_for_the_date_is_chosen(self):
        page = _page(
            f"{PDF_BASE}/Injury-Report_2026-11-23_11_00AM.pdf",
            f"{PDF_BASE}/Injury-Report_2026-11-23_02_15PM.pdf",
            f"{PDF_BASE}/Injury-Report_2026-11-24_12_00AM.pdf",
            "https://www.nba.com/news/some-unrelated-story",
        )
        with patch.object(injury, "proxied_get", return_value=_FakeResponse(page)) as fake:
            page_url, pdf_url, timestamp = injury.fetch_latest_report_pdf_url(
                query_date="20261123",
            )

        self.assertEqual(pdf_url, f"{PDF_BASE}/Injury-Report_2026-11-23_02_15PM.pdf")
        self.assertEqual(timestamp, "2026-11-23T14:15:00-05:00")
        self.assertEqual(page_url, "https://www.nba.com/players/injury-report")
        self.assertEqual(fake.call_args.args[0], "https://www.nba.com/players/injury-report")
        self.assertEqual(fake.call_args.kwargs["label"], "injury-report-page")

    def test_a_page_without_the_target_date_names_what_it_found(self):
        page = _page(f"{PDF_BASE}/Injury-Report_2026-11-24_12_00AM.pdf")
        with patch.object(injury, "proxied_get", return_value=_FakeResponse(page)):
            with self.assertRaises(ValueError) as caught:
                injury.fetch_latest_report_pdf_url(query_date="20261123")

        message = str(caught.exception)
        self.assertIn("2026-11-23", message)
        self.assertIn("2026-11-24", message)

    def test_a_page_with_no_pdf_anchors_is_distinguishable(self):
        page = _page(
            "https://www.nba.com/news/some-unrelated-story",
            "https://www.nba.com/players",
        )
        with patch.object(injury, "proxied_get", return_value=_FakeResponse(page)):
            with self.assertRaises(ValueError) as caught:
                injury.fetch_latest_report_pdf_url(query_date="20261123")

        message = str(caught.exception)
        self.assertIn("no Injury-Report PDFs", message)
        self.assertIn("2 anchors", message)
        # The other failure mode must not read like this one.
        self.assertNotIn("lists reports for", message)


if __name__ == "__main__":
    unittest.main()
