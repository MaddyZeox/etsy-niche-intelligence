import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# keyring is Windows-provided in real installation; fake it for isolated tests.
sys.modules.setdefault('keyring', types.SimpleNamespace(get_password=lambda *args: 'test-not-secret'))
spec = importlib.util.spec_from_file_location('test_collector_v45', ROOT/'etsy_collector_keyword.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
spec2 = importlib.util.spec_from_file_location('test_backend_v45', ROOT/'backend/main.py')
b = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(b)


def listing(idx, code):
    return {'listing_id':idx,'title':f'Test item {idx}','tags':['test'], 'price': {'amount': 1000+idx*100, 'divisor':100,'currency_code':code},'num_favorers':2,'url':f'https://www.etsy.com/listing/{idx}', 'shop_id': idx+100}


class ProgressiveTests(unittest.TestCase):
    def test_market_isolation_preview_validity(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'preview_abc.json'
            def fake_api(path, headers):
                if '/listings/' in path:
                    return {'results':[listing(1,'USD'),listing(2,'EUR')], 'count':900}
                return {'shop_name':'Example','transaction_sold_count':20,'created_timestamp':1500000000,'review_count':2,'review_average':4.7}
            with patch.object(c,'OUTPUT_DIR',Path(td)),patch.object(c,'DB_FILE',Path(td)/'shops.sqlite3'),patch.object(c,'fetch_json',side_effect=fake_api),patch.object(c,'tidy_cache'), patch.object(c,'PAGES',1):
                c.main('test item',pages=1,buyer_country='US',run_id='a'*32,preview_path=path)
            partial=json.loads(path.read_text())
            self.assertEqual(partial['stage'],'listings_only')
            self.assertEqual(len(partial['listings']),1)
            self.assertEqual(partial['listings'][0]['currency'],'USD')
            self.assertFalse(partial['listings'][0]['shop_data_available'])
            self.assertTrue(b._matching_research_result(partial,'test item','US','USD','a'*32))
            self.assertFalse(b._matching_research_result(partial,'test item','GB','GBP','a'*32))
            self.assertIsNone(b._read_recent_report(path,'test item','US','USD'))
            complete=list(Path(td).glob('etsy_*.json'))
            self.assertEqual(len(complete),1)
            final=json.loads(complete[0].read_text())
            self.assertEqual(final['listings'][0]['shop_name'],'Example')
            self.assertTrue(b._matching_research_result(final,'test item','US','USD','a'*32))
    def test_invalid_currency_is_rejected(self):
        sample={'source':'Etsy Open API v3','keyword':'test','buyer_country':'US','listing_currency_filter':'USD','currency_filter_mode':'original_listing_currency','sample_before_currency_filter':1,'excluded_currency_count':0,'listings':[{'currency':'EUR'}]}
        self.assertFalse(b._matching_research_result(sample,'test','US','USD'))
    def test_tampered_job_id_rejected(self):
        self.assertEqual(len(b._JOBS),0)
        from fastapi import HTTPException
        try:
            b.research_progress('bad',types.SimpleNamespace(client=types.SimpleNamespace(host='testclient')))
        except HTTPException as exc:
            self.assertEqual(exc.status_code,404)
        else: self.fail('Expected rejection')

if __name__=='__main__':unittest.main()

