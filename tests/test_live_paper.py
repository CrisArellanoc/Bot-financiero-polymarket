import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import live_model as ml
import live_paper as live
import local_observer as observer


class LocalObserverTests(unittest.TestCase):
    def test_remote_alias_never_reaches_chat(self):
        for info in ({"remote_host": "example.invalid"}, {"remote_model": "otro"}, {}):
            replies = [{"models": [{"name": "mi-modelo"}]}, info]
            with patch.object(observer, "ollama", side_effect=replies) as api, self.assertRaises(ValueError):
                observer.explain("mi-modelo", {"evento": "prueba"})
            self.assertEqual(api.call_count, 2)
        with patch.object(observer, "ollama", return_value={"models": []}) as api, self.assertRaises(ValueError):
            observer.explain("sin-instalar", {})
        self.assertEqual(api.call_count, 1)

    def test_only_validated_visible_summary_is_kept(self):
        note = {"estado": "observacion", "principal": "E0", "secundario": "ninguno"}
        evidence = {"mensajes": [{"hecho": "El simulador espera la apertura."}]}
        result = {"done": True, "message": {"content": json.dumps(note), "thinking": "descartar"}}
        with patch.object(observer, "select_model"), patch.object(observer, "ollama", return_value=result) as api:
            self.assertEqual(observer.explain("mi-modelo", evidence), ("observacion", evidence["mensajes"][0]["hecho"]))
            route, body = api.call_args.args
            self.assertEqual(route, "/api/chat")
            self.assertEqual(body["format"]["properties"]["principal"]["enum"], ["E0"])
            self.assertIs(body["think"], False)
            self.assertNotIn("tools", body)
            result["done_reason"] = "length"
            with self.assertRaises(ValueError):
                observer.explain("mi-modelo", evidence)

    def test_external_redirect_is_rejected(self):
        with self.assertRaises(ValueError):
            observer.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid")


class CandidateTests(unittest.TestCase):
    def test_cutoff_checks_both_clocks_and_actual_observation(self):
        good = [(970, 971, .48, .50, 2, 3), (995, 997, .50, .52, 2, 3)]
        bad = [(998, 1001, .80, .82, 2, 3), (1001, 999, .9, .92, 2, 3)]
        x, seen = ml.features(good+bad, 1000)
        self.assertAlmostEqual(x[0], .51)
        self.assertEqual(seen, 997)
        with self.assertRaises(ValueError):
            ml.features(good[:1]+bad, 1000)

    def test_fit_purges_late_labels_and_scores_unseen_periods(self):
        rows = [(i*300, [.5, .02, (-1)**i, 0, .01], int(i%2 == 0), i*300+340)
                for i in range(ml.MIN_ROWS)]
        model, report = ml.fit_candidate(rows)
        self.assertTrue(report["eligible"])
        self.assertEqual(report["train"], int(len(rows)*.55)-1)
        self.assertEqual(report["validation"], sum(b["n"] for b in report["blocks"]))
        self.assertGreater(ml.probability(model, rows[0][1]), .5)
        self.assertLess(ml.probability(model, rows[1][1]), .5)
        with self.assertRaises(ValueError):
            ml.fit_candidate(rows[:10])

    def test_invalid_schedule_and_minimum(self):
        book = {"asks": [{"price": ".30", "size": "20"}]}
        market = {"orderMinSize": 5, "feesEnabled": True,
                  "feeSchedule": {"rate": .07, "exponent": 1, "takerOnly": True}}
        self.assertEqual(live.costs(market, book, live.number(".60")), "0.29400")
        for change in ({"orderMinSize": 8}, {"feesEnabled": None}, {"feeSchedule": {}}):
            with self.assertRaises(ValueError):
                live.costs({**market, **change}, book, live.number(".60"))


class CircuitTests(unittest.TestCase):
    start = 3000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/"paper.sqlite"
        self.agent = live.Agent(self.path)
        self.db = self.agent.db
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.agent.ledger.close)
        self.book = {"min_order_size": "5", "asks": [{"price": ".41", "size": "30"}]}
        self.top = {"best_bid": .40, "best_ask": .41}
        self.raw = {"orderMinSize": 5, "feesEnabled": False}
        self.view = {"active": True, "closed": False, "accepting_orders": True}
        with self.db:
            self.db.executemany("INSERT INTO ticks VALUES(?,?,?,?,?,?,?)",
                                [(3000, t, t-1, .48, .50, 3, 2) for t in [2980,2996]])

    def seed_model(self, up):
        model = {"mean": [0]*5, "scale": [1]*5, "w1": [[0]]*5, "b1": [0],
                 "b2": 2 if up else -2, "w2": [0], "a": 1, "b": 0}
        with self.db:
            self.db.execute("INSERT INTO models VALUES(?,?,?,?)",
                            (288, 2900, json.dumps(model), '{"eligible":true}'))

    def buy(self):
        with (patch("live_paper.time.time", return_value=3002),
              patch("live_paper.market_for", return_value=({}, self.view, self.raw)),
              patch("live_paper.orderbook", return_value=(self.book, self.top, 3002, 3001)) as book):
            self.agent.decide(self.start)
            self.agent.decide(self.start)
        return book

    def test_warmup_freezes_reference_without_buy(self):
        self.assertFalse(self.buy().called)
        data = live.snapshot(self.path, 3000)
        self.assertAlmostEqual(data["forecast"]["p"], .49)
        self.assertEqual(data["summary"]["orders"], 0)
        self.assertEqual(len(data["trace"]), 3)

    def test_persisted_local_report_is_scoped_to_its_window(self):
        self.buy()
        start, through, evidence = observer.next_evidence(self.db, 3003)
        self.assertEqual(evidence["fuente_pronostico"], "Libro Up")
        self.assertEqual(evidence["mercado"], live.slug(start))
        with self.db:
            self.db.execute("INSERT INTO local_notes VALUES(?,?,?,?,?,?,?)",
                            (start, through, 3004, "prueba", "observacion", "Abstención simulada.", observer.REVISION))
        self.assertIsNone(observer.next_evidence(self.db, 3005))
        self.assertEqual(live.snapshot(self.path, start)["notes"][0]["model"], "prueba")
        self.assertFalse(live.snapshot(self.path, start+300)["notes"])

    def test_down_fill_official_settlement_restart_safe(self):
        self.seed_model(False)
        mock = self.buy()
        self.assertEqual(mock.call_args.args[1], "Down")
        self.assertEqual(mock.call_count, 1)
        with (patch("live_paper.time.time", return_value=3430),
              patch("live_paper.market_for", return_value=({}, self.view, self.raw)),
              patch("live_paper.normalize_resolved", return_value={"outcome": "Down"})):
            self.agent.resolve(3430)
            self.agent.resolve(3430)
        data = live.snapshot(self.path, 3000)
        self.assertEqual(data["summary"]["settled"], 1)
        self.assertEqual(data["summary"]["orders"], 1)
        self.assertAlmostEqual(data["summary"]["cash"], 111.8)
        self.assertEqual(data["summary"]["accuracy"], 1)

    def test_up_fill_and_missing_approval(self):
        self.seed_model(True)
        self.assertEqual(self.buy().call_args.args[1], "Up")

    def test_late_or_invalid_inputs_abstain(self):
        with patch("live_paper.time.time", return_value=3020):
            self.agent.decide(3000)
        self.assertIsNone(live.snapshot(self.path, 3000)["forecast"]["p"])

    def test_unresolved_does_not_train(self):
        self.buy()
        with (patch("live_paper.market_for", return_value=({}, self.view, self.raw)),
              patch("live_paper.normalize_resolved", side_effect=ValueError("Aún pendiente"))):
            self.agent.resolve(3440)
        self.agent.train(3450)
        self.assertIsNone(live.snapshot(self.path, 3000)["model"])
        self.assertEqual(live.snapshot(self.path, 3000)["summary"]["labeled"], 0)
