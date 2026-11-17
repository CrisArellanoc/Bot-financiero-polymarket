import copy
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from polymarket_public import DataError, inspect_market, inspect_book, get_json, GAMMA


def fixture():
    return {"slug": "btc-updown-5m-0", "markets": [{
        "slug": "btc-updown-5m-0", "endDate": "1970-01-01T00:05:00Z",
        "outcomes": '["Down", "Up"]', "clobTokenIds": '["20", "10"]',
        "description": "Synthetic fixture for schema validation only"
    }]}


class PublicDataTests(unittest.TestCase):
    def test_maps_by_label_not_assumed_position(self):
        self.assertEqual(inspect_market(fixture(), "btc-updown-5m-0")["outcome_ids"], {"Down": "20", "Up": "10"})

    def test_protocol_v2(self):
        event = fixture()
        event["markets"][0].update(version="v2", positionIds=["20", "10"], clobTokenIds=None)
        self.assertEqual(inspect_market(event, event["slug"])["id_field"], "positionIds")

    def test_rejects_wrong_horizon_or_schema(self):
        for change in ({"endDate": "1970-01-01T00:15:00Z"}, {"outcomes": '["Yes", "No"]'},
                       {"clobTokenIds": '["10", "10"]'}, {"version": "v3"}, {"description": ""}):
            with self.subTest(change=change):
                event = copy.deepcopy(fixture())
                event["markets"][0].update(change)
                with self.assertRaises(DataError):
                    inspect_market(event, event["slug"])

    def test_empty_event(self):
        with self.assertRaises(DataError):
            inspect_market({}, "btc-updown-5m-0")

    def test_best_prices_do_not_depend_on_order(self):
        book = {"asset_id": "10", "bids": [{"price": ".3", "size": "1"}, {"price": ".4", "size": "1"}],
                "asks": [{"price": ".7", "size": "1"}, {"price": ".6", "size": "1"}]}
        result = inspect_book(book, "10")
        self.assertEqual(result["best_bid"], .4)
        self.assertEqual(result["best_ask"], .6)
        self.assertAlmostEqual(result["spread"], .2)

    def test_empty_book_is_not_zero_price(self):
        self.assertIsNone(inspect_book({"asset_id": "10", "bids": [], "asks": []}, "10")["best_ask"])

    def test_nan_and_wrong_asset_rejected(self):
        book = {"asset_id": "10", "bids": [{"price": "NaN", "size": "1"}], "asks": []}
        with self.assertRaises(DataError):
            inspect_book(book, "10")
        with self.assertRaises(DataError):
            inspect_book(book, "20")

    @patch("polymarket_public.time.sleep")
    @patch("polymarket_public.urlopen")
    def test_retry_is_bounded(self, opener, sleep):
        opener.side_effect = HTTPError("https://example.invalid", 429, "rate limit", {}, None)
        with self.assertRaises(HTTPError):
            get_json(GAMMA, "/events")
        self.assertEqual(opener.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("polymarket_public.urlopen")
    def test_no_retry_on_forbidden(self, opener):
        opener.side_effect = HTTPError("https://example.invalid", 403, "forbidden", {}, None)
        with self.assertRaises(HTTPError):
            get_json(GAMMA, "/events")
        self.assertEqual(opener.call_count, 1)


if __name__ == "__main__":
    unittest.main()
