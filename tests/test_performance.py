import importlib.util
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault('keyring', types.SimpleNamespace(get_password=lambda *args: None))
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ni_backend_performance_test', ROOT / 'backend/main.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def report(country='US', currency='USD', age_minutes=0, keyword='Visiting Card'):
    return {'source':'Etsy Open API v3','collection_run_id':'a'*32,
            'keyword': keyword, 'buyer_country':country,
            'listing_currency_filter':currency,
            'currency_filter_mode':'original_listing_currency' if country else 'none',
            'sample_before_currency_filter':2, 'excluded_currency_count':1 if country else 0,
            'listings':[{'listing_id':1,'currency':currency if country else 'EUR','price':5}],
            'collected_at':(datetime.now(timezone.utc)-timedelta(minutes=age_minutes)).isoformat(),
            'unique_shops':1}

class TestResearchCache(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name)/'test.json'

    def test_valid_cache_hit(self):
        self.p.write_text(json.dumps(report()),encoding='utf-8')
        self.assertIsNotNone(m._read_recent_report(self.p,'Visiting Card','US','USD'))

    def test_global_not_usa(self):
        self.p.write_text(json.dumps(report(country=None,currency=None)),encoding='utf-8')
        self.assertIsNone(m._read_recent_report(self.p,'Visiting Card','US','USD'))

    def test_other_country_not_usa(self):
        self.p.write_text(json.dumps(report(country='GB',currency='GBP')),encoding='utf-8')
        self.assertIsNone(m._read_recent_report(self.p,'Visiting Card','US','USD'))

    def test_expired_report_not_reused(self):
        self.p.write_text(json.dumps(report(age_minutes=16)),encoding='utf-8')
        self.assertIsNone(m._read_recent_report(self.p,'Visiting Card','US','USD'))

    def test_invalid_currency_rejected(self):
        r=report();r['listings'][0]['currency']='GBP'
        self.p.write_text(json.dumps(r),encoding='utf-8')
        self.assertIsNone(m._read_recent_report(self.p,'Visiting Card','US','USD'))

    def test_missing_receipt_rejected(self):
        r=report();r['collection_run_id']=None
        self.p.write_text(json.dumps(r),encoding='utf-8')
        self.assertIsNone(m._read_recent_report(self.p,'Visiting Card','US','USD'))

    def test_keyword_isolation(self):
        self.p.write_text(json.dumps(report()),encoding='utf-8')
        self.assertIsNone(m._read_recent_report(self.p,'Wedding Card','US','USD'))

    def test_result_run_validation(self):
        self.assertFalse(m._matching_research_result(report(),'Visiting Card','US','USD','b'*32))
        self.assertTrue(m._matching_research_result(report(),'Visiting Card','US','USD','a'*32))

    def test_response_contract(self):
        with patch.object(m, '__relevance_audit',return_value={'test':True}):
            x=m._research_response(report(),'Visiting Card','US',True)
        self.assertEqual(x['report_cache'],'hit')
        self.assertEqual(x['buyer_country'],'US')
        self.assertEqual(x['listing_currency_filter'],'USD')
        self.assertEqual(x['sample_size'],1)
        self.assertIn('relevance_audit',x)

    def test_no_report_file(self):
        self.assertIsNone(m._read_recent_report(self.p,'Visiting Card','US','USD'))

if __name__=='__main__':unittest.main()
