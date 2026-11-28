import json,math,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from live_paper import Agent,snapshot
import evaluation as ev
import btc_reference as btc

class EvaluationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'paper.sqlite';self.agent=Agent(self.path);self.db=self.agent.db
  self.addCleanup(self.temp.cleanup);self.addCleanup(self.agent.ledger.close)
  self.db.row_factory=sqlite3.Row
 def add(self,i,p,y,action=None,delay=3,valid=1,prompt='test-v1'):
  start=172800+i*86400
  action=action or ('Up' if p is not None and p>.5 else 'Down')
  evidence=json.dumps({'inputs_available_at':start-1,'opening_features':{'mid_up':.5,'volatility':.003}})
  with self.db:
   self.db.execute('INSERT INTO brain_decisions(start,ready,p_up,action,model,status,evidence,response,requested_at,completed_at,response_valid,prompt_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
    (start,start+delay,p,action,'Qwen','ok' if valid else 'error',evidence,'{}' if valid else None,start+1,start+delay,valid,prompt))
   self.db.execute('INSERT INTO forecasts(start,p,winner,labeled) VALUES(?,?,?,?)',(start,p,y,start+310))
   self.db.execute("INSERT INTO bet_attempts(start,at,status,reason,capital,stake,model) VALUES(?,?,'rejected','prueba','100','3','Qwen')",(start,start+delay))
  return start
 def populate(self):
  for i,(p,y) in enumerate(((.8,'Up'),(.3,'Down'),(.6,'Down'),(.9,'Up'))):self.add(i,p,y,prompt='test-v1' if i<2 else 'test-v2')
 def test_binary_scores_market_skill_and_confusion(self):
  self.populate();result=ev.evaluate(self.db)['overall']['prediction']
  self.assertEqual(result['n'],4);self.assertAlmostEqual(result['brier'],.125)
  self.assertAlmostEqual(result['market_brier'],.25);self.assertAlmostEqual(result['brier_skill'],.5)
  self.assertAlmostEqual(result['accuracy'],.75);self.assertAlmostEqual(result['balanced_accuracy'],.75)
  self.assertAlmostEqual(result['f1_up'],.8);self.assertAlmostEqual(result['f1_down'],2/3)
  self.assertAlmostEqual(result['ece'],.3)
  self.assertAlmostEqual(result['log_loss'],-(math.log(.8)+math.log(.7)+math.log(.4)+math.log(.9))/4)
  self.assertEqual(result['confusion'],dict(actual_up_pred_up=2,actual_up_pred_down=0,actual_down_pred_up=1,actual_down_pred_down=1))
 def test_versions_coverage_latency_and_late_invalid_responses(self):
  self.populate();self.add(4,.8,'Up',delay=20);self.add(5,.8,'Up',valid=0);self.add(6,None,'Down',action='abstain')
  report=ev.evaluate(self.db);self.assertEqual(len(report['groups']),2)
  o=report['overall'];self.assertEqual(o['prediction']['n'],4)
  self.assertEqual(o['coverage']['signals'],4);self.assertEqual(o['coverage']['abstentions'],1)
  self.assertAlmostEqual(o['coverage']['signal_rate'],4/7)
  self.assertEqual(o['api']['valid'],6);self.assertEqual(o['api']['completed'],7)
  self.assertEqual(o['api']['median_seconds'],2);self.assertEqual(o['api']['p95_seconds'],19)
  self.assertEqual(o['coverage']['api_failures'],1);self.assertEqual(o['coverage']['late_responses'],1)
 def test_side_breakdown_separates_signals_from_pending_and_realized_purchases(self):
  starts=[self.add(i,p,y) for i,(p,y) in enumerate(((.1,'Up'),(.2,'Down'),(.7,'Up')))]
  self.add(3,.2,'Up',delay=19)
  self.add(4,None,'Down',action='abstain')
  self.add(5,.2,None)
  with self.db:
   self.db.executemany("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?)",[
    (f"btc-updown-5m-{starts[0]}","Down","5","3",starts[0]+300,starts[0]+310,"0","-3"),
    (f"btc-updown-5m-{starts[1]}","Down","5","3",starts[1]+300,None,None,None)])
  group=ev.evaluate(self.db)['overall'];sides=group['by_side']
  self.assertEqual(sides['Down'],dict(signals=3,official=2,purchases=2,settled=1,net=-3,wins=0,losses=1))
  self.assertEqual(sides['Up'],dict(signals=1,official=3,purchases=0,settled=0,net=None,wins=0,losses=0))
  self.assertEqual(group['prediction']['precision_down'],.5)
 def test_prior_reference_never_uses_future_labels_and_missing_times_are_unknown(self):
  start=self.add(0,.8,'Up')
  with self.db:
   self.db.execute("INSERT INTO forecasts(start,winner,labeled) VALUES(0,'Down',?)",(start+20000,))
   self.db.execute('UPDATE brain_decisions SET requested_at=NULL,completed_at=NULL')
  result=ev.evaluate(self.db)['overall']
  self.assertAlmostEqual(result['prediction']['prior_brier'],.25)
  self.assertIsNone(result['api']['median_seconds']);self.assertEqual(result['api']['timing_unknown'],1)
 def test_empty_and_perfect_reference_have_no_infinity(self):
  empty=ev.evaluate(self.db);self.assertIsNone(empty['overall']['prediction']['brier'])
  self.assertIsNone(empty['overall']['trading']['profit_factor'])
  self.add(0,1,'Up')
  with self.db:self.db.execute('UPDATE brain_decisions SET evidence=?',(json.dumps({'inputs_available_at':172799,'opening_features':{'mid_up':1}}),))
  pred=ev.evaluate(self.db)['overall']['prediction']
  self.assertIsNone(pred['brier_skill']);self.assertIsNone(pred['improvement_ci']['low'])
  json.dumps(ev.evaluate(self.db),allow_nan=False)
 def test_drawdown_net_profit_and_no_false_pending_wins(self):
  orders=[dict(market_id=str(i),cost='3',pnl=str(p),settled_at=172800+i*86400,payout=str(3+p)) for i,p in enumerate((3,-3,2,0))]
  orders.append(dict(market_id='pending',cost='3',pnl=None,settled_at=None,payout=None))
  t=ev.trading_scores(orders);curve=ev.equity_scores(orders,'99')
  self.assertEqual(t['pending'],1);self.assertEqual(t['wins'],2);self.assertEqual(t['losses'],1)
  self.assertAlmostEqual(t['profit_factor'],5/3);self.assertEqual(t['mean_net'],.5)
  self.assertEqual(curve['initial'],100);self.assertEqual(curve['capital'],102)
  self.assertAlmostEqual(curve['max_drawdown_pct'],3/103);self.assertEqual(curve['max_drawdown_usd'],3)
  self.assertEqual(t['max_losing_streak'],1)
 def test_bootstrap_is_reproducible_and_requires_multiple_days(self):
  one=ev.block_interval([(172800,.1),(172801,.2)]);self.assertIsNone(one['low'])
  data=[(172800,.1),(259200,.2),(345600,-.1)]
  self.assertEqual(ev.block_interval(data),ev.block_interval(data))
  self.assertEqual(ev.block_interval(data)['blocks'],3)
 def test_cache_invalidates_on_official_results_and_migration_preserves_records(self):
  self.add(0,.8,'Up');first=ev.snapshot(self.db)
  self.assertEqual(first['overall']['prediction']['n'],1)
  # A newer official result invalidates the cached metrics.
  self.add(1,.3,'Down');second=ev.snapshot(self.db)
  self.assertEqual(second['overall']['prediction']['n'],2)
  self.assertEqual(self.agent.ledger.cash,100)

class TargetCorrectionTests(unittest.TestCase):
 def test_twap_uses_exact_start_and_gamma_reconciles_wrong_spot(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'paper.sqlite';agent=Agent(path);db=agent.db
   try:
    with db:
     db.execute('INSERT INTO btc_targets VALUES(3000,3001,59900,?)',(btc.SOURCE,))
     db.execute("INSERT INTO btc_twap_quotes VALUES(3001,3002,60001,'Polymarket','60001')")
    with patch('btc_reference.time.time',return_value=3005),patch('btc_reference.target',side_effect=ValueError()):
     self.assertFalse(btc.refresh_target(db,3000))
    self.assertIsNone(snapshot(path,3000)['btc']['target'])
    with db:db.execute("INSERT INTO btc_twap_quotes VALUES(3000,3002,60000,'Polymarket','60000')")
    with patch('btc_reference.time.time',return_value=3005),patch('btc_reference.target',side_effect=ValueError()):
     self.assertTrue(btc.refresh_target(db,3000))
    self.assertEqual(snapshot(path,3000)['btc']['target']['price'],60000)
    with patch('btc_reference.time.time',return_value=3010),patch('btc_reference.target',return_value=60000.25):btc.refresh_target(db,3000)
    result=snapshot(path,3000)['btc']['target'];self.assertEqual(result['price'],60000.25);self.assertEqual(result['kind'],'confirmed')
    with patch('btc_reference.time.time',return_value=3011),patch('btc_reference.target',side_effect=ValueError()):btc.refresh_target(db,3000)
    self.assertEqual(snapshot(path,3000)['btc']['target']['price'],60000.25)
    self.assertEqual(db.execute('SELECT count(*) FROM btc_target_revisions').fetchone()[0],2)
   finally:agent.ledger.close()
