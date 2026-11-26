import tempfile,unittest
from pathlib import Path
from live_paper import Agent,snapshot

class PortfolioTests(unittest.TestCase):
 def test_open_capital_and_settled_results_reconcile(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'paper.sqlite';agent=Agent(path)
   try:
    with agent.db:
     agent.db.execute("UPDATE account SET cash='102' WHERE id=1")
     agent.db.executemany('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?)',[
      ('a','Up','20','8',300,310,'20','12'),
      ('b','Down','10','6',600,610,'0','-6'),
      ('c','Up','10','4',900,None,None,None)])
    p=snapshot(path)['portfolio']
    self.assertEqual((p['initial'],p['capital_realized'],p['exposure']),(100,106,4))
    self.assertEqual((p['wins'],p['losses'],p['pending'],p['win_rate']),(1,1,1,.5))
    self.assertEqual(p['orders'][1]['capital_realized'],106)
    self.assertEqual(p['orders'][2]['capital_realized'],112)
    self.assertEqual(snapshot(path)['summary']['cash'],102)
   finally:agent.ledger.close()
 def test_empty_account_is_not_a_win(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'paper.sqlite';agent=Agent(path)
   try:
    p=snapshot(path)['portfolio']
    self.assertEqual(p['initial'],100)
    self.assertIsNone(p['win_rate'])
    self.assertEqual(p['orders'],[])
   finally:agent.ledger.close()
