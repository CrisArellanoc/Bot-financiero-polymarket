import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from live_paper import Agent, snapshot
import yahoo_predictor as yahoo


def candles():
    return [dict(start=i*300,open=100.,high=102.,low=99.,close=101.,volume=10.)
            for i in range(yahoo.LAGS+5)]


class YahooTests(unittest.TestCase):
    def test_incomplete_and_unaligned_candles_are_excluded(self):
        rows=candles()[:3]
        payload={"chart":{"result":[{"meta":{"symbol":"BTC-USD"},
                 "timestamp":[r["start"] for r in rows]+[901],
                 "indicators":{"quote":[{k:[r[k] for r in rows]+[100.] for k in ("open","high","low","close","volume")}]}}]}}
        normalized,rejected=yahoo.normalize(payload,650)
        self.assertEqual([r["start"] for r in normalized],[0,300])
        self.assertEqual(rejected,2)

    def test_target_and_intermediate_candle_cannot_change_the_inputs(self):
        rows=candles()
        first=yahoo.samples(rows)[0]
        target_index=yahoo.LAGS+1
        rows[target_index]["close"]=99
        rows[target_index-1]["close"]=102
        changed=yahoo.samples(rows)[0]
        self.assertEqual(first[:2],changed[:2])
        self.assertNotEqual(first[2],changed[2])
        rows[3]["start"]+=10
        with self.assertRaises(ValueError):
            yahoo.features(rows[:yahoo.LAGS],first[0])

    def test_revisions_receive_a_new_availability_time(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=Agent(Path(folder)/"paper.sqlite")
            try:
                rows=candles()[:2]
                yahoo.store_candles(agent.db,rows,1000)
                yahoo.store_candles(agent.db,rows,1100)
                self.assertEqual(agent.db.execute("SELECT available FROM yahoo_candles WHERE start=0").fetchone()[0],1000)
                rows[0]["close"]=100.5
                yahoo.store_candles(agent.db,rows,1200)
                self.assertEqual(agent.db.execute("SELECT available FROM yahoo_candles WHERE start=0").fetchone()[0],1200)
            finally:
                agent.ledger.close()

    def test_normal_entry_point_records_one_prospective_forecast(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"paper.sqlite"
            agent=Agent(path)
            try:
                start=(yahoo.LAGS+1)*300
                rows=candles()[:yahoo.LAGS]
                model={"mean":[0]*len(yahoo.FEATURES),"scale":[1]*len(yahoo.FEATURES),
                       "layers":[{"weights":[[0] for _ in yahoo.FEATURES],"bias":[1]}],"a":1,"b":0}
                with agent.db:
                    agent.db.execute("INSERT INTO yahoo_models VALUES('test',?,?,?)",
                        (start-100,json.dumps(model),'{}'))
                yahoo.store_candles(agent.db,rows,start-50)
                with patch("live_paper.time.time",return_value=start+2):
                    agent.decide(start)
                    agent.decide(start)
                data=snapshot(path,start)
                self.assertGreater(data["yahoo"]["forecast"]["p"],.5)
                self.assertEqual(agent.db.execute("SELECT count(*) FROM yahoo_predictions").fetchone()[0],1)
                # Aunque faltó la ventana del libro, se consulta la etiqueta oficial para Yahoo.
                with patch("live_paper.market_for",return_value=({},None,None)), \
                     patch("live_paper.normalize_resolved",return_value={"outcome":"Up"}), \
                     patch("live_paper.time.time",return_value=start+700):
                    agent.resolve(start+700)
                self.assertEqual(snapshot(path,start)["forecast"]["winner"],"Up")
                self.assertEqual(snapshot(path,start)["summary"]["labeled"],0)
                # Las etiquetas Yahoo sin variables del libro no entran al entrenamiento del MLP anterior.
                with patch("live_paper.ml.MIN_ROWS",1),patch("live_paper.ml.fit_candidate") as fit:
                    agent.train(start+700)
                fit.assert_not_called()
                self.assertEqual(agent.ledger.cash,100)
            finally:
                agent.ledger.close()

    def test_late_inputs_block_inference_and_late_model_is_not_used_retroactively(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=Agent(Path(folder)/"paper.sqlite")
            try:
                start=(yahoo.LAGS+1)*300
                with agent.db:
                    agent.db.execute("INSERT INTO yahoo_models VALUES('test',?,'{}','{}')",(start-100,))
                yahoo.store_candles(agent.db,candles()[:yahoo.LAGS],start+1)
                with patch("yahoo_predictor.time.time",return_value=start+2):
                    yahoo.forecast(agent.db,start)
                result=agent.db.execute("SELECT p,detail FROM yahoo_predictions").fetchone()
                self.assertIsNone(result[0])
                self.assertIn("después",result[1])
                with agent.db:
                    agent.db.execute("DELETE FROM yahoo_predictions")
                    agent.db.execute("UPDATE yahoo_models SET ready=?",(start+1,))
                with patch("yahoo_predictor.time.time",return_value=start+2):
                    yahoo.forecast(agent.db,start)
                self.assertIsNone(agent.db.execute("SELECT p FROM yahoo_predictions").fetchone()[0])
                with agent.db:
                    agent.db.execute("DELETE FROM yahoo_predictions")
                    agent.db.execute("UPDATE yahoo_models SET ready=?",(start-100,))
                with patch("yahoo_predictor.time.time",return_value=start-1):
                    yahoo.forecast(agent.db,start)
                self.assertIsNone(agent.db.execute("SELECT p FROM yahoo_predictions").fetchone()[0])
            finally:
                agent.ledger.close()
