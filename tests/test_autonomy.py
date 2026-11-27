import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from autonomy import Limits, PaperLedger, training_eligible


def proposal(**changes):
    row = dict(market_id='synthetic-1', outcome='Up', model_version='synthetic-calibrated-v1',
               model_approved=True, costs_verified=True, market_open=True, rules_verified=True,
               now=1001, start=1000, end=1300, book_time=1000.5, inputs_available_at=999,
               shares='10', p_win='0.7', max_price='0.55', total_costs_extra='0.2',
               min_order_size='5', asks=[['0.5', '5'], ['0.52', '20']], best_bid='0.49')
    return {**row, **changes}


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'paper.sqlite')
        self.ledger = PaperLedger(self.path, '100')
        self.limits = Limits('10', '20', '15', '0.03', '0.05', 2, 3)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_daily_control_persists_and_does_not_disable_other_limits(self):
        from dataclasses import replace
        limits=replace(self.limits,max_daily_loss_budget="1")
        self.assertIn("diario",self.ledger.buy(proposal(),limits)["reason"])
        with self.ledger.db:
            self.ledger.db.execute("UPDATE paper_controls SET daily_loss_enabled=0")
        self.assertEqual(self.ledger.buy(proposal(),limits)["status"],"filled_paper")
        self.ledger.close()
        self.ledger=PaperLedger(self.path,"100")
        self.assertEqual(self.ledger.db.execute("SELECT daily_loss_enabled FROM paper_controls").fetchone()[0],0)
        self.assertIn("Saldo",self.ledger.buy(proposal(market_id="other"),replace(limits,max_order_cost="1"))["reason"])

    def test_fill_uses_depth_and_costs(self):
        fill = self.ledger.buy(proposal(), self.limits)
        self.assertEqual(fill['status'], 'filled_paper')
        self.assertEqual(Decimal(fill['total_cost']), Decimal('5.3'))
        self.assertEqual(self.ledger.cash, Decimal('94.7'))

    def test_settlement_profit_and_idempotence(self):
        self.ledger.buy(proposal(), self.limits)
        result = self.ledger.settle('synthetic-1', 'Up', 1301, 1302, True)
        self.assertEqual(Decimal(result['net_pnl']), Decimal('4.7'))
        self.assertEqual(self.ledger.cash, Decimal('104.7'))
        with self.assertRaises(ValueError):
            self.ledger.settle('synthetic-1', 'Up', 1301, 1303, True)
        self.assertEqual(self.ledger.cash, Decimal('104.7'))

    def test_insufficient_edge_even_with_60_percent_prediction(self):
        row = proposal(p_win='0.6', asks=[['0.7', '20']], best_bid='0.69', max_price='0.75')
        self.assertEqual(self.ledger.buy(row, self.limits)['status'], 'rejected')

    def test_stale_future_and_late_inputs_rejected(self):
        for change in ({'book_time': 990}, {'book_time': 1002}, {'inputs_available_at': 1000.1}, {'now': 1004}):
            with self.subTest(change=change):
                self.assertEqual(self.ledger.buy(proposal(**change), self.limits)['status'], 'rejected')
        self.assertEqual(self.ledger.cash, Decimal('100'))

    def test_invalid_and_unverified_rejected(self):
        for change in ({'p_win': 'NaN'}, {'costs_verified': False}, {'model_approved': False},
                       {'asks': [['0.5', '1']]}, {'best_bid': '0.1'}, {'total_costs_extra': '-1'}):
            with self.subTest(change=change):
                self.assertEqual(self.ledger.buy(proposal(**change), self.limits)['status'], 'rejected')

    def test_duplicate_and_restart_preserve_cash(self):
        self.ledger.buy(proposal(), self.limits)
        self.ledger.close()
        self.ledger = PaperLedger(self.path, '9999')
        self.assertEqual(self.ledger.cash, Decimal('94.7'))
        self.assertEqual(self.ledger.buy(proposal(), self.limits)['status'], 'rejected')
        self.assertEqual(self.ledger.cash, Decimal('94.7'))

    def test_exposure_and_daily_loss_budget(self):
        limits = Limits('10', '6', '15', '0.03', '0.05', 2, 3)
        self.ledger.buy(proposal(), limits)
        self.assertEqual(self.ledger.buy(proposal(market_id='synthetic-2'), limits)['status'], 'rejected')
        self.ledger.settle('synthetic-1', 'Down', 1301, 1302, True)
        limits = Limits('10', '20', '10', '0.03', '0.05', 2, 3)
        next_row = proposal(market_id='synthetic-2', start=1400, end=1700, now=1401, book_time=1400.5)
        self.assertEqual(self.ledger.buy(next_row, limits)['status'], 'rejected')

    def test_order_cost_and_cash_limits(self):
        limits = Limits('5', '20', '15', '0.03', '0.05', 2, 3)
        self.assertEqual(self.ledger.buy(proposal(), limits)['status'], 'rejected')
        low_cash = PaperLedger(':memory:', '1')
        try:
            self.assertEqual(low_cash.buy(proposal(), self.limits)['status'], 'rejected')
        finally:
            low_cash.close()

    def test_unverified_or_premature_settlement(self):
        self.ledger.buy(proposal(), self.limits)
        for resolved, now, verified in ((1301, 1302, False), (1200, 1201, True), (1400, 1302, True)):
            with self.assertRaises(ValueError):
                self.ledger.settle('synthetic-1', 'Up', resolved, now, verified)
        self.assertEqual(self.ledger.cash, Decimal('94.7'))

    def test_training_waits_for_confirmed_label_and_cutoff(self):
        row = dict(inputs_available_at=999, start=1000, end=1300,
                   label_available_at=1320, resolution_final=True, outcome='Up')
        self.assertTrue(training_eligible(row, 1320))
        self.assertFalse(training_eligible(row, 1319))
        self.assertFalse(training_eligible({**row, 'resolution_final': False}, 1400))
        self.assertFalse(training_eligible({**row, 'inputs_available_at': 1001}, 1400))
        self.assertFalse(training_eligible({**row, 'label_available_at': 1200}, 1400))
        self.assertFalse(training_eligible({}, 1400))


if __name__ == '__main__':
    unittest.main()
