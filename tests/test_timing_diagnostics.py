import contextlib
import importlib.util
import io
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

class DiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake_keyring = types.ModuleType('keyring')
        fake_keyring.get_password = lambda service, key: 'test-only-value'
        with patch.dict(sys.modules, {'keyring': fake_keyring}):
            spec = importlib.util.spec_from_file_location('collector_timing_test', ROOT / 'etsy_collector_keyword.py')
            cls.collector = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.collector)

    def test_no_behavior_change_and_timing_lines(self):
        m = self.collector
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            listings = [
                {'listing_id': 1, 'title': 'A', 'shop_id': 10, 'price': {'amount': 1250, 'divisor': 100, 'currency_code': 'USD'}, 'num_favorers': 3, 'url': 'https://www.etsy.com/listing/1', 'tags': ['a']},
                {'listing_id': 2, 'title': 'B', 'shop_id': 11, 'price': {'amount': 900, 'divisor': 100, 'currency_code': 'GBP'}, 'num_favorers': 4, 'tags': []},
            ]
            calls = []
            def fetch(path, headers):
                calls.append(path)
                return {'results': listings, 'count': 10}
            def shop(sid, headers):
                return sid, {'shop_id': sid, 'shop_name': 'Shop A', 'review_count': 4}, None
            preview = base/'preview_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json'
            with patch.object(m, 'DB_FILE', base/'shops.sqlite3'), patch.object(m,'OUTPUT_DIR',base/'research_results'), patch.object(m,'fetch_json',side_effect=fetch), patch.object(m,'shop_request',side_effect=shop), patch.object(m.keyring,'get_password',return_value='dummy'):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    m.main('test keyword',pages=1,buyer_country='US',run_id='a'*32,preview_path=preview)
            log = buf.getvalue()
            for key in ('listing_page_1_seconds','listing_api_total_seconds','preview_write_seconds','listings_ready_elapsed_seconds','sqlite_cache_read_seconds','shop_enrichment_seconds','sqlite_write_cleanup_seconds','finalize_report_seconds','full_report_elapsed_seconds'):
                self.assertIn('TIMING '+key+': ', log)
            self.assertEqual(len(calls), 1)
            result = json.loads((base/'research_results'/m.research_filename('test keyword','US','native-USD')).read_text())
            stage = json.loads(preview.read_text())
            self.assertEqual(result['collection_run_id'], 'a'*32)
            self.assertEqual(result['sample_before_currency_filter'],2)
            self.assertEqual(result['excluded_currency_count'],1)
            self.assertEqual([r['currency'] for r in result['listings']],['USD'])
            self.assertEqual(result['listings'][0]['shop_name'],'Shop A')
            self.assertEqual(stage['stage'],'listings_only')
            self.assertIsNone(stage['listings'][0]['shop_name'])
            self.assertEqual(stage['listing_currency_filter'],'USD')
            self.assertNotIn('TIMING', result)

    def test_global_still_mixed(self):
        m = self.collector
        with tempfile.TemporaryDirectory() as d:
            b = Path(d)
            listings=[{'listing_id':i,'title':'test','shop_id':None,'tags':[],'price':{'amount':100,'divisor':100,'currency_code':c}} for i,c in enumerate(['USD','GBP'],1)]
            with patch.object(m,'DB_FILE',b/'c.sqlite3'),patch.object(m,'OUTPUT_DIR',b/'out'),patch.object(m,'fetch_json',return_value={'results':listings,'count':2}),patch.object(m.keyring,'get_password',return_value='dummy'):
                with contextlib.redirect_stdout(io.StringIO()): m.main('global test',pages=1,run_id='b'*32)
            r=json.loads((b/'out'/m.research_filename('global test')).read_text())
            self.assertEqual(len(r['listings']),2)
            self.assertEqual(r['currency_filter_mode'],'none')

if __name__=='__main__': unittest.main()
