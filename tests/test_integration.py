import importlib.util, sys, types, tempfile, json, pathlib, unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.modules['keyring'] = types.SimpleNamespace(get_password=lambda service, name: 'dummy')
spec = importlib.util.spec_from_file_location('collector_for_tests', ROOT/'etsy_collector_keyword.py')
collector = importlib.util.module_from_spec(spec); spec.loader.exec_module(collector)
spec2 = importlib.util.spec_from_file_location('backend_for_tests', ROOT/'backend/main.py')
backend = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(backend)

class Tests(unittest.TestCase):
    def test_filename_unchanged(self):
        self.assertEqual(collector.research_filename('Visiting Card','US','native-USD'), 'etsy_visiting-card_fc2a9854_US_NATIVE-USD.json')
    def test_original_currency_cohort_and_receipt(self):
        class FakeDB:
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def execute(self,*a): return self
            def commit(self): pass
            def executemany(self,*a): pass
            def fetchall(self): return []
            def close(self): pass
        with tempfile.TemporaryDirectory() as td:
            out=pathlib.Path(td)
            (out/'cache.sqlite3').touch()
            prices=['USD','GBP','USD','CAD']
            data={'count':4,'results':[{'listing_id':i+1,'shop_id':9,'title':str(i), 'price':{'amount':1000,'divisor':100,'currency_code':c}} for i,c in enumerate(prices)]}
            with patch.object(collector,'OUTPUT_DIR',out),patch.object(collector,'DB_FILE',out/'cache.sqlite3'),patch.object(collector,'fetch_json',return_value=data),patch.object(collector,'read_cached',return_value={9:{'shop_name':'demo'}}),patch.object(collector,'tidy_cache'),patch.object(collector,'sqlite3') as sq:
                sq.connect.return_value=FakeDB()
                collector.main('Visiting Card',pages=1,buyer_country='US',run_id='a'*32)
            result=json.loads((out/collector.research_filename('Visiting Card','US','native-USD')).read_text())
            self.assertEqual([r['currency'] for r in result['listings']],['USD','USD'])
            self.assertEqual(result['excluded_currency_count'],2)
            self.assertEqual(result['collection_run_id'],'a'*32)
    def test_backend_success_stale_and_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); (root/'research_results').mkdir()
            # importlib loader loads real temporary module; root matters for path.
            (root/'etsy_collector_keyword.py').write_text((ROOT/'etsy_collector_keyword.py').read_text())
            # The production response now requires analyze() and competitor_evidence().
            # Copy the real module into this isolated test root instead of a legacy
            # analyze-only stub that can no longer satisfy the response contract.
            import shutil
            shutil.copyfile(ROOT/'universal_relevance_v3.py', root/'universal_relevance_v3.py')
            # The response also loads the demand foundation from the isolated root.
            shutil.copyfile(ROOT/'keyword_demand_intelligence.py', root/'keyword_demand_intelligence.py')
            shutil.copyfile(ROOT/'related_keyword_discovery.py', root/'related_keyword_discovery.py')
            def fake_run(cmd,**kwargs):
                rid=cmd[cmd.index('--run-id')+1]; country=cmd[cmd.index('--buyer-country')+1] if '--buyer-country' in cmd else None
                filename=collector.research_filename(cmd[2],country, 'native-USD' if country=='US' else None)
                payload={'source':'Etsy Open API v3','collection_run_id':rid,'keyword':cmd[2],'buyer_country':country,'listing_currency_filter':'USD' if country else None,'currency_filter_mode':'original_listing_currency' if country else 'none','listings':[{'listing_id':123456,'currency':'USD','title':'Visiting Card','tags':['visiting card'],'num_favorers':5}],'sample_before_currency_filter':2,'excluded_currency_count':1}
                (root/'research_results'/filename).write_text(json.dumps(payload))
                return types.SimpleNamespace(returncode=0,stdout='done',stderr='')
            with patch.object(backend,'_PROJECT_ROOT',root),patch.object(backend,'_COLLECTOR',root/'etsy_collector_keyword.py'):
                with patch.object(backend.subprocess,'run',side_effect=fake_run):
                    resp=TestClient(backend.app).post('/etsy/search',json={'keyword':'Visiting Card','buyer_country':'US'})
                    self.assertEqual(resp.status_code,200,resp.text)
                    self.assertEqual(resp.json()['sample_size'],1)
                    self.assertIn('competitor_evidence',resp.json())
                # old file remains but new request run id is missing => reject stale.
                with patch.object(backend.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout='',stderr='')):
                    resp=TestClient(backend.app).post('/etsy/search',json={'keyword':'Visiting Card','buyer_country':'US'})
                    self.assertEqual(resp.status_code,502)
                    self.assertIn('Stale data',resp.text)
                with patch.object(backend.subprocess,'run',return_value=types.SimpleNamespace(returncode=1,stdout='COLLECTION_ERROR: HTTP 429\n',stderr='secret-value')):
                    resp=TestClient(backend.app).post('/etsy/search',json={'keyword':'Visiting Card','buyer_country':'US'})
                    self.assertEqual(resp.status_code,502)
                    self.assertIn('HTTP 429',resp.text)
                    self.assertNotIn('secret-value',resp.text)

if __name__=='__main__': unittest.main()
