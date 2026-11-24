import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from threading import Event
from unittest.mock import Mock, patch

from live_paper import Agent, check_remote, snapshot
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
            finally:
                agent.ledger.close()

    def test_missing_configuration_fails_connection_check(self):
        with patch("builtins.print"):
            self.assertEqual(check_remote({}),1)
