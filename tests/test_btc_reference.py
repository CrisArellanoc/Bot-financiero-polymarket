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

    def test_twap_rejects_wrong_source_symbol_window_and_stale_prices(self):
        base={"topic":btc.TOPIC,"type":"update","payload":{
            "symbol":"btc/usd","window_s":60,"timestamp":3001000,
            "value":60000.125,"full_accuracy_value":"60000125000000000000000"}}
        points=btc.twap_points(base,3005)
        self.assertEqual(points[0][:4],(3001,3005,60000.125,btc.QUOTE_SOURCE))
        self.assertEqual(points[0][4],"60000.125")
        for changes in ({"symbol":"eth/usd"},{"window_s":30},{"value":1},
                        {"timestamp":2700000},{"timestamp":3010000},
                        {"full_accuracy_value":"NaN"},{"full_accuracy_value":"1e999"}):
            self.assertEqual(btc.twap_points({**base,"payload":{**base["payload"],**changes}},3005),[])
        self.assertEqual(btc.twap_points({**base,"topic":"crypto_prices_chainlink"},3005),[])
        self.assertEqual(btc.twap_points({**base,"topic":"Yahoo"},3005),[])

    def test_snapshot_is_ordered_deduplicated_and_never_mixed_with_yahoo(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"paper.sqlite"
            agent=Agent(path)
            try:
                message={"topic":btc.TOPIC,"type":"subscribe","payload":{
                    "symbol":"btc/usd","window_s":60,"data":[
                        {"timestamp":3002000,"value":60002},
                        {"timestamp":3001000,"value":60001}]}}
                with agent.db:
                    agent.db.execute("INSERT INTO btc_quotes VALUES(3003,3004,123,'Yahoo Finance BTC-USD')")
                self.assertEqual(btc.store_twap(agent.db,message,3005),3002)
                btc.store_twap(agent.db,message,3006)
                data=snapshot(path,3000)["btc"]["quotes"]
                self.assertEqual([r["price"] for r in data],[60001,60002])
                self.assertTrue(all(r["source"]==btc.QUOTE_SOURCE for r in data))
                self.assertTrue(all(r["seen"]==3005 for r in data))
            finally: agent.ledger.close()

    def test_stream_recovers_after_disconnect_and_stops_without_yahoo_fallback(self):
        from threading import Event
        import websocket
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"paper.sqlite"
            agent=Agent(path)
            stop=Event()
            conn=Mock(connected=True)
            conn.recv.side_effect=lambda: (stop.set() or json.dumps({"topic":btc.TOPIC,"type":"update",
                "payload":{"symbol":"btc/usd","window_s":60,"timestamp":3001000,"value":60000}}))
            try:
                # First failure uses the retry wait, then a genuine accepted message.
                with patch("websocket.create_connection",side_effect=[websocket.WebSocketException(),conn]) as connect, \
                     patch("btc_reference.time.time",return_value=3002), \
                     patch.object(stop,"wait",return_value=False) as wait:
                    btc.stream(path,stop)
                self.assertEqual(connect.call_count,2)
                wait.assert_called_once_with(1)
                self.assertNotIn("header",connect.call_args.kwargs)
                self.assertEqual(connect.call_args.args,(btc.STREAM,))
                sent=json.loads(conn.send.call_args_list[0].args[0])
                self.assertEqual(sent["subscriptions"][0]["topic"],btc.TOPIC)
                self.assertEqual(sent["subscriptions"][0]["filters"],'{"symbol":"btc/usd"}')
                self.assertEqual(snapshot(path,3000)["btc"]["quotes"][0]["price"],60000)
                state=snapshot(path,3000)["btc"]["feed"]
                self.assertEqual(state["state"],"stopped")
                self.assertEqual(state["last_ok"],3002)
                conn.close.assert_called_once()
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
