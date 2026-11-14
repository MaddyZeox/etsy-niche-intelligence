import importlib.util
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys, types
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
sys.modules['keyring']=types.SimpleNamespace(get_password=lambda a,b: 'dummy')
spec=importlib.util.spec_from_file_location('batchcollector',ROOT/'etsy_collector_keyword.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class BatchTests(unittest.TestCase):
    def rows(self,n=10):
        return [{'listing_id':1000+i,'shop_id':2000+i,'price':{'amount':900,'divisor':100,'currency_code':'USD'},'title':'Item'} for i in range(n)]
    def shop(self,row):
        return {'shop_id':row['shop_id'],'shop_name':'Shop '+str(row['shop_id']),
                'transaction_sold_count':123,'review_count':5,'review_average':4.6,'created_timestamp':12345}
    def batch(self, rows):
        return {'results':[dict(row,Shop=self.shop(row)) for row in rows]}
    def test_mixed_shops_one_batch(self):
        rows=self.rows()
        with patch.object(c,'fetch_json',return_value=self.batch(rows)) as fetch,patch.object(c,'shop_request',side_effect=AssertionError('should not fallback')):
            result,errs,stats=c.retrieve_missing_shops(rows,[r['shop_id'] for r in rows],{'x-api-key':'dummy'})
        self.assertEqual(len(result),10);self.assertFalse(errs)
        self.assertEqual(stats['batch_requests'],1)
        self.assertEqual(stats['individual_shop_requests'],0)
        self.assertIn('/listings/batch?',fetch.call_args.args[0]);self.assertIn('includes=Shop',fetch.call_args.args[0])
    def test_listings_deduplicate_shop(self):
        rows=self.rows(2)+[dict(self.rows(2)[0],listing_id=9000)]
        with patch.object(c,'fetch_json',return_value=self.batch(rows[:2])) as fetch:
            profiles,_,stats=c.retrieve_missing_shops(rows,[2000,2001],{})
        self.assertEqual(len(profiles),2);self.assertEqual(stats['batch_requests'],1)
        self.assertNotIn('9000',fetch.call_args.args[0])
    def test_price_and_currency_untouched(self):
        rows=self.rows(2);rows[1]['price']['currency_code']='GBP'
        before=[repr(r) for r in rows]
        with patch.object(c,'fetch_json',return_value=self.batch(rows)):
            profiles,_,_=c.retrieve_missing_shops(rows,[2000,2001],{})
        self.assertEqual([repr(r) for r in rows],before)
        self.assertEqual(len(profiles),2)
    def test_swapped_shop_id_rejected_and_falls_back(self):
        rows=self.rows(2);batch=self.batch(rows)
        batch['results'][0]['Shop']['shop_id']=999
        with patch.object(c,'fetch_json',return_value=batch),patch.object(c,'shop_request',return_value=(2000,self.shop(rows[0]),None)) as fallback:
            result,errs,stats=c.retrieve_missing_shops(rows,[2000,2001],{})
        self.assertEqual(len(result),2);self.assertEqual(fallback.call_count,1)
        self.assertEqual(stats['individual_shop_requests'],1);self.assertFalse(errs)
    def test_wrong_listing_shop_id_rejected(self):
        rows=self.rows(2);batch=self.batch(rows)
        batch['results'][0]['shop_id']=999
        with patch.object(c,'fetch_json',return_value=batch),patch.object(c,'shop_request',return_value=(2000,self.shop(rows[0]),None)) as fallback:
            profiles,_,stats=c.retrieve_missing_shops(rows,[2000,2001],{})
        self.assertEqual(len(profiles),2);self.assertEqual(fallback.call_count,1)
    def test_missing_metrics_fallback(self):
        rows=self.rows(2);batch=self.batch(rows)
        batch['results'][0]['Shop']['review_count']=None
        with patch.object(c,'fetch_json',return_value=batch),patch.object(c,'shop_request',return_value=(2000,self.shop(rows[0]),None)) as fallback:
            profiles,_,stats=c.retrieve_missing_shops(rows,[2000,2001],{})
        self.assertEqual(len(profiles),2);self.assertEqual(fallback.call_count,1)
    def test_batch_failure_fallback(self):
        rows=self.rows(3)
        with patch.object(c,'fetch_json',side_effect=ValueError('invalid')),patch.object(c,'shop_request',side_effect=lambda sid,h:(sid,self.shop(next(r for r in rows if r['shop_id']==sid)),None)):
            profiles,errs,stats=c.retrieve_missing_shops(rows,[r['shop_id'] for r in rows],{})
        self.assertEqual(len(profiles),3);self.assertEqual(stats['batch_failed_requests'],1)
        self.assertEqual(stats['individual_shop_requests'],3)
    def test_single_shop_uses_legacy(self):
        rows=self.rows(1)
        with patch.object(c,'fetch_json',side_effect=AssertionError('no batch')),patch.object(c,'shop_request',return_value=(2000,self.shop(rows[0]),None)):
            profiles,_,stats=c.retrieve_missing_shops(rows,[2000],{})
        self.assertEqual(stats['batch_requests'],0);self.assertEqual(len(profiles),1)
    def test_quick_never_fetches_shops(self):
        rows=self.rows(2)
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            with patch.object(c,'DB_FILE',base/'shops.sqlite3'),patch.object(c,'OUTPUT_DIR',base/'reports'),patch.object(c,'fetch_json',return_value={'count':2,'results':rows}),patch.object(c,'retrieve_missing_shops',side_effect=AssertionError('quick must not fetch')),patch.object(c.keyring,'get_password',return_value='dummy'):
                c.main('somekeyword',pages=1,run_id='a'*32,mode='quick')
    def test_10_per_batch_25_shops(self):
        rows=self.rows(25)
        def batch_response(path,h):
            import urllib.parse
            ids=urllib.parse.parse_qs(path.split('?',1)[1])['listing_ids'][0]
            chosen=set(map(int,ids.split(',')))
            return self.batch([r for r in rows if r['listing_id'] in chosen])
        with patch.object(c,'fetch_json',side_effect=batch_response) as f,patch.object(c,'shop_request',side_effect=AssertionError('no fallback')):
            profiles,_,stats=c.retrieve_missing_shops(rows,[r['shop_id'] for r in rows],{})
        self.assertEqual(len(profiles),25);self.assertEqual(stats['batch_requests'],3)
    def test_429_not_amplified(self):
        import urllib.error
        rows=self.rows(2)
        err=urllib.error.HTTPError('example',429,'quota',{},None)
        with patch.object(c,'fetch_json',side_effect=err),patch.object(c,'shop_request',side_effect=AssertionError('must not fallback')):
            with self.assertRaises(urllib.error.HTTPError):
                c.retrieve_missing_shops(rows,[2000,2001],{})
    def test_invalid_ids_not_selected(self):
        rows=self.rows(2)+[{'shop_id':True,'listing_id':888},{'shop_id':-1,'listing_id':9},{'shop_id':22,'listing_id':'fake'}]
        self.assertEqual(c.shop_listing_pairs(rows,[2000,2001,22]),[(1000,2000),(1001,2001)])
if __name__=='__main__':unittest.main()
