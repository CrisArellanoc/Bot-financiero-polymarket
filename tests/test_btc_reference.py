import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import btc_reference as btc
from live_paper import Agent, snapshot
import service_status
import yahoo_predictor as yahoo


class ReferenceTests(unittest.TestCase):
    def test_public_reference_has_exact_window_and_no_authentication(self):
        for price in (59960.125,None,False,-1,float("nan")):
            response=Mock()
            response.__enter__=Mock(return_value=response)
            response.__exit__=Mock(return_value=False)
            with patch("btc_reference.urlopen",return_value=response) as fetch, \
                 patch("btc_reference.json.load",return_value={"openPrice":price}):
                if price == 59960.125:
                    self.assertEqual(btc.target(3000),price)
                else:
                    with self.assertRaises(ValueError): btc.target(3000)
                request=fetch.call_args.args[0]
                self.assertTrue(request.full_url.startswith(btc.SOURCE+"?symbol=BTC&"))
                self.assertIn("eventStartTime=1970-01-01T00%3A50%3A00Z",request.full_url)
                self.assertIn("endDate=1970-01-01T00%3A55%3A00Z",request.full_url)
                self.assertNotIn("Authorization",request.headers)

    def test_reference_is_frozen_and_errors_never_invent_a_price(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"paper.sqlite"
            agent=Agent(path)
            try:
                stop=Mock()
                stop.is_set.side_effect=[False,False,True]
                with patch("btc_reference.time.time",return_value=3005), \
                     patch("btc_reference.target",side_effect=[ValueError("no price"),59960.]) as get:
                    btc.collect(path,stop)
                self.assertEqual(get.call_count,2)
                self.assertEqual(snapshot(path,3000)["btc"]["target"]["price"],59960.)
                stop.is_set.side_effect=[False,True]
                with patch("btc_reference.time.time",return_value=3006),patch("btc_reference.target") as get:
                    btc.collect(path,stop)
                    get.assert_not_called()
                self.assertIsNone(snapshot(path,3300)["btc"]["target"])
            finally: agent.ledger.close()

    def test_stale_or_wrong_symbol_quotes_are_not_current(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=Agent(Path(folder)/"paper.sqlite")
            try:
                quote=lambda ts,symbol="BTC-USD": {"chart":{"result":[{"meta":{
                    "regularMarketPrice":60000.,"regularMarketTime":ts,"symbol":symbol}}]}}
                self.assertFalse(yahoo.store_quote(agent.db,quote(2500),3005))
                self.assertFalse(yahoo.store_quote(agent.db,quote(3001,"ETH-USD"),3005))
                self.assertTrue(yahoo.store_quote(agent.db,quote(3001),3005))
                self.assertTrue(yahoo.store_quote(agent.db,quote(3001),3006))
                self.assertEqual(agent.db.execute("SELECT ts,seen,price FROM btc_quotes").fetchall(),[(3001,3005,60000)])
            finally: agent.ledger.close()

    def test_usd_forecast_uses_frozen_input_close_and_cannot_follow_new_prices(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"paper.sqlite"
            agent=Agent(path)
            try:
                start=7500
                network={"mean":[0]*28,"scale":[1]*28,"layers":[{"weights":[[0] for _ in range(28)],"bias":[1.]}]}
                weights={**network,"a":1,"b":0,"price_model":{**network,"target_scale":.001}}
                with agent.db:
                    agent.db.execute("INSERT INTO yahoo_models VALUES('test',?,?,?)",(start-100,json.dumps(weights),'{}'))
                candles=[dict(start=i*300,open=100.,high=102.,low=99.,close=101.,volume=10.) for i in range(24)]
                yahoo.store_candles(agent.db,candles,start-10)
                with patch("yahoo_predictor.time.time",return_value=start+2): yahoo.forecast(agent.db,start)
                f=snapshot(path,start)["btc"]["forecast"]
                self.assertAlmostEqual(f["price"],101.10105051683755)
                self.assertEqual(f["anchor"],101.)
                candles[-1]["close"]=100.
                yahoo.store_candles(agent.db,candles,start+20)
                with patch("yahoo_predictor.time.time",return_value=start+21): yahoo.forecast(agent.db,start)
                self.assertEqual(snapshot(path,start)["btc"]["forecast"],f)
                self.assertEqual(agent.ledger.cash,100)
            finally: agent.ledger.close()

    def test_teacher_confirmation_is_separate_from_local_status_and_resets_on_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"paper.sqlite"
            agent=Agent(path)
            try:
                service_status.record(agent.db,"supervisor","ok","Local",True)
                self.assertEqual(snapshot(path)["teacher"],[])
                with patch("service_status.time.time",return_value=3010):
                    service_status.teacher(agent.db,"supervisor","ok",True)
                with patch("service_status.time.time",return_value=3020):
                    service_status.teacher(agent.db,"supervisor","error")
                self.assertEqual(snapshot(path)["teacher"],[dict(service="supervisor",state="error",at=3020,last_ok=3010)])
                service_status.initialize(agent.db)
                self.assertEqual(snapshot(path)["teacher"],[])
            finally: agent.ledger.close()
