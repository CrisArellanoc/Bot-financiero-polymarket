import json
import tempfile
import unittest
from pathlib import Path
from event_store import EventStore


class StoreTests(unittest.TestCase):
    def test_scope_dedup_and_first_received(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            snapshot = {'market': {'slug':'synthetic', 'outcome_ids':{'Up':'1','Down':'2'},
                'window_start_utc':'1970-01-01T00:00:00Z','window_end_utc':'1970-01-01T00:05:00Z'},
                'raw_event':{'markets':[{'slug':'synthetic','conditionId':'cond'}]}}
            (base/'snapshot.json').write_text(json.dumps(snapshot),encoding='utf-8')
            book = {'event_type':'book','market':'cond','asset_id':'1','timestamp':'1000',
                    'bids':[{'price':'.4','size':'10'}], 'asks':[{'price':'.5','size':'10'}]}
            records = [book,book,{**book,'market':'other'},{**book,'asset_id':'3'}]
            line = json.dumps({'kind':'frame','received_at_utc':'1970-01-01T00:00:02Z','raw':json.dumps(records)})
            (base/'stream.jsonl').write_text(line+'\n',encoding='utf-8')
            store=EventStore(str(base/'data.sqlite'))
            try:
                r=store.import_stream(base/'snapshot.json',base/'stream.jsonl')
                self.assertEqual(r,{'stored':1,'duplicate':1,'foreign':1,'rejected':1,'heartbeats':0})
                self.assertEqual(store.import_stream(base/'snapshot.json',base/'stream.jsonl')['stored'],0)
                self.assertEqual(store.db.execute('SELECT count(*) FROM events').fetchone()[0],1)
            finally: store.close()


if __name__ == '__main__': unittest.main()
