import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from threading import Event
from unittest.mock import Mock, patch

from live_paper import Agent, chart_feed, check_remote, snapshot
from urllib.error import URLError
import service_status
from laya_shadow import run
from local_observer import observe


class RemoteWorkerTests(unittest.TestCase):
    def test_remote_opening_is_audited_then_verified_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paper.sqlite"
            agent = Agent(path)
            try:
                xs = [.48,.02,0,0,0]
                with agent.db:
                    agent.db.execute("INSERT INTO forecasts(start,x,p,version) VALUES(3000,?,.48,'Libro Up')",
                                     (json.dumps(xs),))
                stopped = Event()
                predictor = Mock()
                predictor.name = "Laya"

                def infer(values):
                    self.assertEqual(values,xs)
                    stopped.set()
                    return .63

                predictor.predict.side_effect = infer
                with patch("laya_shadow.load_model", side_effect=AssertionError("carga local")), \
                     patch("laya_shadow.time.time", return_value=3002):
                    run(path,None,stopped,predictor)
                stopped.clear()
                supervisor = Mock()
                supervisor.name = "Verificador"

                def review(evidence):
                    self.assertTrue(any("63.0%" in m["hecho"] for m in evidence["mensajes"]))
                    self.assertTrue(any("Control periódico" in m["hecho"] for m in evidence["mensajes"]))
                    stopped.set()
                    return "observacion","Control recibido."

                supervisor.explain.side_effect = review
                with patch("local_observer.time.time",return_value=3004):
                    observe(path,"Remoto",stopped,supervisor)
                data = snapshot(path,3000)
                self.assertEqual(data["laya"]["source"],"Remoto · Laya")
                self.assertEqual(data["notes"][0]["model"],"Remoto · Verificador")
                self.assertEqual(data["summary"]["orders"],0)
                statuses = {item["service"]:item for item in data["services"]}
                self.assertEqual(statuses["supervisor"]["state"],"ok")
                self.assertEqual(statuses["laya"]["last_ok"],3002)
                self.assertEqual({r["service"]:r["last_ok"] for r in data["teacher"]},
                                 {"laya":3002,"supervisor":3004})
            finally:
                agent.ledger.close()

    def test_missing_configuration_fails_connection_check(self):
        with patch("builtins.print"):
            self.assertEqual(check_remote({}),1)

    def test_market_error_and_recovery_preserve_the_last_success_time(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"paper.sqlite"
            agent = Agent(path)
            try:
                stop = Mock()
                stop.is_set.side_effect = [False,False,True]
                stop.wait.return_value = False
                book = ({},{"best_bid":.49,"best_ask":.51},3002,3001)
                with patch("live_paper.time.time",return_value=3002), \
                     patch("live_paper.market_for",return_value=({},{"slug":"btc-updown-5m-3000"},{})), \
                     patch("live_paper.read_book",side_effect=[URLError("sin red"),book,book,book]):
                    chart_feed(path,stop)
                services = {item["service"]:item for item in snapshot(path,3000)["services"]}
                self.assertEqual(services["polymarket"]["state"],"ok")
                self.assertEqual(services["polymarket"]["last_ok"],3002)
                self.assertEqual(len(snapshot(path,3000)["prices"]),1)
                with patch("service_status.time.time",return_value=3010):
                    service_status.record(agent.db,"polymarket","error","sin red")
                data = {item["service"]:item for item in snapshot(path,3000)["services"]}
                self.assertEqual(data["polymarket"]["last_ok"],3002)
                self.assertEqual(data["polymarket"]["at"],3010)
            finally:
                agent.ledger.close()

    def test_evaluation_separates_sources_and_late_predictions(self):
        with tempfile.TemporaryDirectory() as folder:
            agent = Agent(Path(folder)/"paper.sqlite")
            try:
                with agent.db:
                    agent.db.executemany("INSERT INTO forecasts(start,x,p,version,winner) VALUES(?,?,?,?,?)",
                        [(3000,'[0.6,0,0,0,0]',.6,"Libro Up","Up"),(3300,'[0.4,0,0,0,0]',.4,"Libro Up","Down")])
                    agent.db.executemany("INSERT INTO laya_predictions VALUES(?,?,?,?)",
                        [(3000,3002,.8,"Disponible"),(3300,3320,.8,"Disponible")])
                    agent.db.executemany("INSERT INTO laya_sources VALUES(?,?)",
                        [(3000,"Laya local"),(3300,"Remoto · Laya")])
                metrics = {row["source"]:row for row in snapshot(Path(folder)/"paper.sqlite")["comparison_by_source"]}
                self.assertEqual(metrics["Laya local"]["accuracy"],1)
                self.assertAlmostEqual(metrics["Laya local"]["brier"],.04)
                self.assertEqual(metrics["Remoto · Laya"]["accuracy"],0)
                self.assertAlmostEqual(metrics["Remoto · Laya"]["brier"],.64)
                self.assertEqual(metrics["Remoto · Laya"]["delayed"],1)
            finally:
                agent.ledger.close()
