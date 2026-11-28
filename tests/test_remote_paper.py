import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from remote_paper import RemoteAgent, opening_messages, opening_twap_changes
from live_paper import snapshot
import paper_audit as audit
import btc_reference as btc

class RemotePaperTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'paper.sqlite'
  self.brain=Mock(name='brain');self.brain.name='Profesor';self.brain.decide.return_value=dict(action='Up',p_up=.8,reason='E0 y E1 sustentan Up',refs=['E0','E1'])
  self.agent=RemoteAgent(self.path,self.brain);self.db=self.agent.db
  self.addCleanup(self.temp.cleanup);self.addCleanup(self.agent.ledger.close)
  with self.db:
   self.db.executemany('INSERT INTO ticks VALUES(?,?,?,?,?,?,?)',[(3000,t,t-1,.48,.50,3,2) for t in (2980,2996)])
   self.db.execute("INSERT INTO btc_targets VALUES(3000,3001,60000,?)",(btc.GAMMA_SOURCE,))
   self.db.execute("INSERT INTO btc_twap_quotes VALUES(2999,3000,60001,'Polymarket','60001')")
  self.book=dict(min_order_size='5',asks=[dict(price='.50',size='50')]);self.raw=dict(orderMinSize=5,feesEnabled=False)
  self.view=dict(active=True,closed=False,accepting_orders=True)
 def decide(self):
  with patch('live_paper.time.time',return_value=3002),patch('live_paper.market_for',return_value=({},self.view,self.raw)),patch('live_paper.orderbook',return_value=(self.book,{'best_bid':.49,'best_ask':.50},3002,3001)),patch('live_paper.ml.fit_candidate',side_effect=AssertionError('Local training')),patch('yahoo_predictor.forecast',side_effect=AssertionError('Local prediction')):
   self.agent.decide(3000);self.agent.decide(3000);self.agent.train(3000)
 def test_remote_fill_exact_three_percent_and_audit_settlement(self):
  self.decide();self.brain.decide.assert_called_once()
  data=snapshot(self.path,3000)
  self.assertEqual(data['portfolio']['next_stake'],3)
  self.assertEqual(data['bet_attempts'][0]['status'],'filled_paper')
  self.assertEqual(data['summary']['cash'],97)
  self.assertEqual(self.db.execute('SELECT cost FROM orders').fetchone()[0],'3.00000000')
  report=audit.export(self.path,3000)
  self.assertTrue(report['chain_verified'] and report['decision_hashes_verified'] and report['receipts_verified'])
  with patch('live_paper.time.time',return_value=3400),patch('live_paper.market_for',return_value=({},None,None)),patch('live_paper.normalize_resolved',return_value={'outcome':'Up'}):self.agent.resolve(3400)
  data=snapshot(self.path,3000)
  self.assertEqual(data['portfolio']['capital_realized'],103)
  self.assertEqual(data['portfolio']['next_stake'],3.09)
  self.assertTrue(audit.export(self.path,3000)['receipts_verified'])
  with self.db:self.db.execute("UPDATE orders SET pnl='999' WHERE market_id='btc-updown-5m-3000'")
  self.assertFalse(audit.export(self.path,3000)['receipts_verified'])
 def test_opening_evidence_respects_direction_time_and_missing_history(self):
  for price in (59900,60100):
   with self.db:
    self.db.execute("DELETE FROM btc_twap_quotes")
    self.db.executemany("INSERT INTO btc_twap_quotes VALUES(?,?,?,'Polymarket','test')",
                        [(2920,2920,61000),(2984,2984,60000),(2985,3003,62000),(3000,3000,price)])
   changes=opening_twap_changes(self.db,3000,price,3001)
   self.assertEqual(changes[15],round((price/60000-1)*10000,3))
   self.assertIsNone(changes[60])
  for mid,side in ((.635,"Up"),(.365,"Down"),(.5,"empate")):
   book,reference,trend=opening_messages(3000,[mid,.02,0,.0,0],60000,3000,60000,changes)
   self.assertIn(f"por precio: {side}.",book["hecho"])
   self.assertIn("observada +0.0000 USD",reference["hecho"])
   self.assertIn("60 s: sin muestra",trend["hecho"])
 def test_abstention_and_unreachable_api_never_place_order(self):
  for reply in ('abstain','error'):
   with self.subTest(reply=reply):
    if reply=='abstain':self.brain.decide.return_value=dict(action='abstain',p_up=None,reason='Evidencia insuficiente',refs=['E1'])
    else:self.brain.decide.side_effect=ValueError('Servidor inaccesible')
    self.decide()
    self.assertEqual(self.agent.ledger.cash,100)
    self.assertEqual(self.db.execute('SELECT count(*) FROM orders').fetchone()[0],0)
    with self.db:
     for table in ('bet_attempts','brain_decisions','forecasts','audit_events'):self.db.execute('DELETE FROM '+table)
 def test_early_sale_once_and_official_resolution(self):
  self.decide()
  self.brain.decide.return_value=dict(action="Down",p_up=.2,reason="Cambio de tendencia",refs=["E0"])
  with self.db:self.db.execute("INSERT INTO btc_twap_quotes VALUES(3059,3060,59990,'Polymarket','59990')")
  with patch('live_paper.time.time',return_value=3060),patch('live_paper.market_for',return_value=({},self.view,self.raw)),patch('live_paper.orderbook',return_value=(dict(self.book,bids=[dict(price=".45",size="20")]),{},3060,3060)):
   for _ in range(2):self.agent.review_position(3000,"Up","6","3.00000000")
  self.assertAlmostEqual(float(self.agent.ledger.cash),99.70)
  self.assertEqual(len(snapshot(self.path)["portfolio"]["early_sales"]),1)
  with patch('live_paper.time.time',return_value=3400),patch('live_paper.market_for',return_value=({},None,None)),patch('live_paper.normalize_resolved',return_value={'outcome':'Down'}):self.agent.resolve(3400)
  self.assertAlmostEqual(float(self.agent.ledger.cash),99.70)
  self.assertTrue(audit.export(self.path,3000)["receipts_verified"])
  self.assertEqual(snapshot(self.path)["portfolio"]["early_sales"][0]["winner"],"Down")
 def test_exploration_can_sample_a_weak_forecast_at_observed_price(self):
  self.agent.exploration=True
  self.brain.decide.return_value=dict(action="Up",p_up=.50,reason="Empate",refs=["E0"])
  self.decide()
  self.assertEqual(self.agent.ledger.cash,97)
  self.assertEqual(self.brain.decide.call_args.args[0]["modo"],"exploracion_paper")
  self.assertTrue(audit.export(self.path,3000)["receipts_verified"])
 def test_quote_uses_held_side_depth_after_cost_and_expires(self):
  from live_paper import record_contract_quote
  self.decide()
  book=dict(self.book,bids=[dict(price=".49",size="2"),dict(price=".46",size="4")])
  with patch("live_paper.time.time",return_value=3050):
   record_contract_quote(self.db,3000,"Up",self.raw,(book,dict(best_bid=.49,best_ask=.51),3050,3050))
   order=snapshot(self.path,3000)["portfolio"]["orders"][0]
   self.assertAlmostEqual(order["sale_pnl"],-.18)
   self.assertEqual(order["bid"],.49)
   book["bids"].pop()
   record_contract_quote(self.db,3000,"Up",self.raw,(book,dict(best_bid=.49,best_ask=None),3050,3050))
   self.assertNotIn("sale_pnl",snapshot(self.path,3000)["portfolio"]["orders"][0])
  with patch("live_paper.time.time",return_value=3065):
   self.assertNotIn("bid",snapshot(self.path,3000)["portfolio"]["orders"][0])
 def test_minimum_can_increase_three_percent(self):
  self.raw['orderMinSize']=10
  self.decide()
  self.assertEqual(snapshot(self.path)['bet_attempts'][0]['status'],'filled_paper')
  self.assertEqual(self.agent.ledger.cash,95)
  self.assertIn('mínimo válido aplicado: 5',snapshot(self.path)['bet_attempts'][0]['reason'])
 def test_hash_chain_detects_evidence_and_event_edits(self):
  self.decide()
  with self.db:self.db.execute("UPDATE brain_decisions SET evidence='{}'")
  self.assertFalse(audit.export(self.path,3000)['decision_hashes_verified'])
  with self.db:self.db.execute("UPDATE audit_events SET payload='{}' WHERE id=1")
  self.assertFalse(audit.verify(self.db))
  self.assertFalse(audit.export(self.path,3000)['chain_verified'])
 def test_late_response_is_rejected(self):
  def slow(evidence,timeout):
   self.clock.return_value=3016
   return dict(action='Down',p_up=.1,reason='E1 indica descenso',refs=['E1'])
  self.brain.decide.side_effect=slow
  with patch('live_paper.time.time',return_value=3002) as self.clock:self.agent.decide(3000)
  self.assertEqual(snapshot(self.path)['bet_attempts'][0]['status'],'rejected')
  self.assertEqual(self.agent.ledger.cash,100)
 def test_unpublished_target_waits_then_rejects(self):
  with self.db:self.db.execute('DELETE FROM btc_targets')
  with patch('live_paper.time.time',return_value=3002):self.agent.decide(3000)
  self.assertEqual(self.db.execute('SELECT count(*) FROM bet_attempts').fetchone()[0],0)
  with patch('live_paper.time.time',return_value=3013):self.agent.decide(3000)
  self.assertEqual(snapshot(self.path)['bet_attempts'][0]['status'],'rejected')

 def test_crash_recovery_does_not_repeat_remote_decision_or_purchase(self):
  self.decide()
  with self.db:self.db.execute("UPDATE bet_attempts SET status='pending'")
  self.agent.ledger.close()
  self.agent=RemoteAgent(self.path,self.brain);self.db=self.agent.db
  # The original cleanup closes the previous connection; register the reopened one.
  self.addCleanup(self.agent.ledger.close)
  self.agent.decide(3000)
  self.brain.decide.assert_called_once()
  self.assertEqual(self.agent.ledger.cash,97)
  self.assertEqual(snapshot(self.path)['bet_attempts'][0]['status'],'filled_paper')

 def test_audit_endpoint_returns_receipt_json(self):
  from functools import partial
  from http.server import ThreadingHTTPServer
  from threading import Thread
  from urllib.request import urlopen
  from live_paper import Panel
  self.decide()
  server=ThreadingHTTPServer(('127.0.0.1',0),partial(Panel,db_path=self.path))
  worker=Thread(target=server.serve_forever);worker.start()
  try:
   with urlopen(f'http://127.0.0.1:{server.server_port}/api/audit?start=3000') as response:
    data=json.load(response)
   self.assertTrue(data['chain_verified'] and data['receipts_verified'])
   self.assertEqual(data['orders'][0]['cost'],'3.00000000')
   self.assertEqual(data['mode'],'paper')
   from urllib.request import Request
   from urllib.error import HTTPError
   base=f'http://127.0.0.1:{server.server_port}'
   for enabled in (False,True):
    request=Request(base+'/api/controls',data=json.dumps(dict(daily_loss_enabled=enabled)).encode(),
                    headers={'Content-Type':'application/json','Origin':base},method='POST')
    with urlopen(request) as response:self.assertEqual(response.status,204)
    self.assertEqual(snapshot(self.path)['portfolio']['daily_loss_enabled'],enabled)
   with self.assertRaises(HTTPError) as caught:
    urlopen(Request(base+'/api/controls',data=b'{"daily_loss_enabled":false}',
                    headers={'Content-Type':'application/json','Origin':'https://external.invalid'},method='POST'))
   self.assertEqual(caught.exception.code,403)
   self.assertTrue(audit.export(self.path)['chain_verified'])
  finally:server.shutdown();server.server_close();worker.join()

 def test_reference_correction_during_llm_call_rejects_without_rewriting_evidence(self):
  def changed(evidence,timeout):
   with self.db:self.db.execute('UPDATE btc_targets SET price=60002 WHERE start=3000')
   return dict(action='Up',p_up=.8,reason='E1 sustenta Up',refs=['E1'])
  self.brain.decide.side_effect=changed
  self.decide()
  data=snapshot(self.path)
  self.assertEqual(data['bet_attempts'][0]['status'],'rejected')
  self.assertEqual(self.agent.ledger.cash,100)
  record=audit.export(self.path,3000)['decisions'][0]
  self.assertEqual(record['evidence']['price_to_beat']['price'],60000)
  self.assertTrue(record['prompt_version'].startswith('llm-paper-v3:'))
  self.assertEqual(record['completed_at']-record['requested_at'],0)
