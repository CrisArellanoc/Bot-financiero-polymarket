import json
import sqlite3
import unittest
from contextlib import closing

from opening_memory import add_context, historical_examples


class MemoryTest(unittest.TestCase):
    def test_only_observed_labels_enter_prospective_experiment(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE forecasts(start INTEGER,x TEXT,winner TEXT,seen REAL,labeled REAL)")
            db.execute("CREATE TABLE brain_decisions(start INTEGER)")
            x = [.5,.01,.1,0,.01]
            for i in range(26):
                t = i*300
                db.execute("INSERT INTO forecasts VALUES(?,?,?,?,?)",
                           (t,json.dumps(x),"Up",t+(i==24),t+310 if i!=25 else 100000))
                db.execute("INSERT INTO brain_decisions VALUES(?)",(t,))
            self.assertEqual(len(historical_examples(db,12000)),24)
            base = dict(prompt_version="v",mensajes=[])
            self.assertEqual(add_context(db,300,x,base),"v")
            versions = set()
            for start in (12000,12300):
                evidence = dict(prompt_version="v",mensajes=[])
                versions.add(add_context(db,start,x,evidence))
                if evidence["prompt_version"].endswith("/memoria-v1"):
                    self.assertEqual(len(evidence["casos_similares"]),5)
                    self.assertTrue(all(r["start"]<7200 for r in evidence["casos_similares"]))
                    self.assertEqual(evidence["mensajes"][-1]["ref"],"E5")
                else:
                    self.assertNotIn("casos_similares",evidence)
            self.assertEqual(versions,{"v/control-v1","v/memoria-v1"})
