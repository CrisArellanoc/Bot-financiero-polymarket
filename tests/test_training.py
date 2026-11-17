import unittest
import numpy as np
from training import supervised, metrics, partitions
from market_history import iso


def rows(n=40):
    return [dict(slug=f'synthetic-{i}', start=iso(i*300), end=iso(i*300+300),
                 reported_closed_at=iso(i*300+370), metadata_first_observed_at=iso(999999),
                 label_first_observed_at=iso(999999), resolution_final=True,
                 price_to_beat=100+i, final_price=101+i, outcome='Up') for i in range(n)]


class TrainingTests(unittest.TestCase):
    def test_backfill_not_assumed_available_historically(self):
        with self.assertRaises(ValueError): supervised(rows(), 2)

    def test_proxy_excludes_most_recent_unpublished_market(self):
        x,y,s,_ = supervised(rows(), 2, True)
        self.assertEqual(s[0]['slug'], 'synthetic-4')
        self.assertEqual(s[0]['last_input_market'], 'synthetic-2')
        self.assertTrue(np.all(x == 1))
        self.assertTrue(np.all(y == 1))

    def test_purge_late_labels_at_split(self):
        _,_,samples,_ = supervised(rows(), 2, True)
        train,valid,test = partitions(samples)
        self.assertLess(train[-1]+1, valid[0]+1)
        from market_history import parse_time
        self.assertLessEqual(parse_time(samples[train[-1]]['label_available_at']), parse_time(samples[valid[0]]['start']))
        self.assertFalse(set(train) & set(test))

    def test_duplicates_rejected(self):
        records = rows()
        records.append(records[0])
        with self.assertRaises(ValueError): supervised(records, 2, True)

    def test_metrics_constant_and_zeros(self):
        r = metrics(np.array([-1,0,1]), np.array([0,0,0]))
        self.assertIsNone(r['pearson_correlation'])
        self.assertIsNone(r['directional_bias'])
        self.assertAlmostEqual(r['directional_accuracy'], 1/3)
        self.assertAlmostEqual(r['up_down_accuracy_tie_is_up'], 2/3)
        with self.assertRaises(ValueError): metrics([1], [float('nan')])


if __name__ == '__main__': unittest.main()
