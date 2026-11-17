import unittest
import numpy as np
from optimize import features_for, cv_splits
from market_history import iso, parse_time


class OptimizeTests(unittest.TestCase):
    def test_features_only_use_their_own_history(self):
        x=np.arange(1500,dtype=float).reshape(3,500)
        rows=[{'start':iso(i*300)} for i in range(3)]
        a=features_for(x,rows,20,True)
        x[2,:]=99999
        b=features_for(x,rows,20,True)
        np.testing.assert_array_equal(a[0:2],b[0:2])
        self.assertEqual(a.shape,(3,31))

    def test_cv_purges_labels_and_respects_outer_test(self):
        rows=[{'start':iso(i*300),'label_available_at':iso(i*300+370)} for i in range(80)]
        for tr,va in cv_splits(rows,list(range(64))):
            self.assertLess(max(va),64)
            self.assertLessEqual(parse_time(rows[tr[-1]]['label_available_at']),parse_time(rows[va[0]]['start']))


if __name__=='__main__':unittest.main()
