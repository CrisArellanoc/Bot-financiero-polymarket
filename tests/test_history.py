import unittest
from market_history import normalize_resolved


def event():
    return {'id': 'e1', 'slug': 'btc-updown-5m-0', 'eventMetadata': {'priceToBeat': 100, 'finalPrice': 101},
            'markets': [{'id': 'm1', 'conditionId': 'c1', 'slug': 'btc-updown-5m-0',
                         'endDate': '1970-01-01T00:05:00Z', 'outcomes': '["Down","Up"]',
                         'outcomePrices': '["0","1"]', 'clobTokenIds': '["10","20"]',
                         'description': 'Synthetic rules', 'closed': True, 'umaResolutionStatus': 'resolved',
                         'closedTime': '1970-01-01T00:06:00Z'}]}


class HistoryTests(unittest.TestCase):
    def test_final_label_and_observation_time(self):
        r = normalize_resolved(event(), '1970-01-01T00:10:00Z')
        self.assertEqual(r['outcome'], 'Up')
        self.assertEqual(r['target_price_difference'], 1)
        self.assertEqual(r['metadata_first_observed_at'], '1970-01-01T00:10:00Z')

    def test_price_at_one_is_not_resolution(self):
        e = event()
        e['markets'][0]['umaResolutionStatus'] = 'proposed'
        with self.assertRaises(ValueError): normalize_resolved(e, '1970-01-01T00:10:00Z')

    def test_mismatch_and_missing_prices(self):
        for metadata in ({'priceToBeat': 100, 'finalPrice': 99}, {}, {'priceToBeat': 100, 'finalPrice': float('nan')}):
            e = event()
            e['eventMetadata'] = metadata
            with self.assertRaises(ValueError): normalize_resolved(e, '1970-01-01T00:10:00Z')

    def test_tie_is_up_and_future_close_rejected(self):
        e = event()
        e['eventMetadata']['finalPrice'] = 100
        self.assertEqual(normalize_resolved(e, '1970-01-01T00:10:00Z')['outcome'], 'Up')
        with self.assertRaises(ValueError): normalize_resolved(e, '1970-01-01T00:05:30Z')


if __name__ == '__main__': unittest.main()
