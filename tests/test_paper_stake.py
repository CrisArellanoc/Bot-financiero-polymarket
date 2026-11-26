from decimal import Decimal
import sqlite3
import unittest

from paper_stake import capital_and_stake, size_for_budget


class StakeTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"feesEnabled": False, "orderMinSize": "5"}
        self.book = {"min_order_size": "5", "asks": [{"price": ".5", "size": "100"}]}

    def test_capital_counts_pending_cost_and_preserves_three_percent(self):
        with sqlite3.connect(":memory:") as db:
            db.executescript("CREATE TABLE account(id INTEGER,cash TEXT); "
                             "CREATE TABLE orders(cost TEXT,settled_at REAL); "
                             "INSERT INTO account VALUES(1,'100');")
            self.assertEqual(capital_and_stake(db), (Decimal(100), Decimal("3.00")))
            db.execute("UPDATE account SET cash='90'")
            self.assertEqual(capital_and_stake(db), (Decimal(90), Decimal("2.70")))
            db.execute("INSERT INTO orders VALUES('10',NULL)")
            self.assertEqual(capital_and_stake(db), (Decimal(100), Decimal("3.00")))
            db.execute("UPDATE orders SET settled_at=1")
            self.assertEqual(capital_and_stake(db), (Decimal(90), Decimal("2.70")))

    def test_six_shares_invest_exactly_three_dollars(self):
        result = size_for_budget(self.raw, self.book, ".55", "3.00")
        self.assertEqual(Decimal(result["shares"]), 6)
        self.assertEqual(Decimal(result["total_cost"]), 3)
        self.assertEqual(Decimal(result["fees"]), 0)

    def test_fees_are_inside_budget_across_multiple_levels(self):
        self.raw.update(feesEnabled=True, feeSchedule={"rate": .07, "exponent": 1, "takerOnly": True})
        self.book["asks"] = [{"price": ".49", "size": "3"}, {"price": ".50", "size": "20"}]
        result = size_for_budget(self.raw, self.book, ".51", "3.00")
        total = Decimal(result["total_cost"])
        self.assertLessEqual(total, 3)
        self.assertLess(Decimal(3)-total, Decimal(".00001"))
        self.assertGreater(Decimal(result["fees"]), 0)
        self.assertEqual(total, Decimal(result["gross"])+Decimal(result["fees"]))

    def test_minimum_is_share_count_not_spend(self):
        self.book["asks"] = [{"price": ".7", "size": "100"}]
        with self.assertRaisesRegex(ValueError, "mínimo"):
            size_for_budget(self.raw, self.book, ".75", "3.00")
        self.raw["orderMinSize"] = "7"
        with self.assertRaisesRegex(ValueError, "mínimo"):
            size_for_budget(self.raw, {**self.book,"asks":[{"price":".4","size":"100"}]}, ".5", "2.70")

    def test_insufficient_depth_never_reduces_the_stake(self):
        self.book["asks"] = [{"price": ".5", "size": "5"}]
        with self.assertRaisesRegex(ValueError, "3% completo"):
            size_for_budget(self.raw, self.book, ".6", "3.00")
        with self.assertRaisesRegex(ValueError, "3% completo"):
            size_for_budget(self.raw, {**self.book,"asks":[{"price":".5","size":"5.99"}]}, ".6", "3.00")
        self.book["asks"] = [{"price": ".5", "size": "5"}, {"price": ".7", "size": "100"}]
        with self.assertRaises(ValueError):
            size_for_budget(self.raw, self.book, ".6", "3.00")

    def test_unknown_fees_and_invalid_inputs_fail_closed(self):
        for raw in ({"orderMinSize":"5"}, {**self.raw,"feesEnabled":True},
                    {**self.raw,"feesEnabled":True,"feeSchedule":{"rate":.1,"exponent":True,"takerOnly":True}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                size_for_budget(raw, self.book, ".6", "3.00")
        for budget in ("NaN", "0", "-3", "3.001"):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                size_for_budget(self.raw, self.book, ".6", budget)


if __name__ == "__main__":
    unittest.main()
