import importlib.util
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

MODULE_PATH = Path(__file__).resolve().parents[1] / 'backend' / 'main.py'
spec = importlib.util.spec_from_file_location('ni_v46_test', MODULE_PATH)
ni = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ni)

class PollingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.job_id = uuid.uuid4().hex
        self.preview = Path(self.temp.name) / f'preview_{self.job_id}.json'
        self.record = {
            'source':'Etsy Open API v3','stage':'listings_only',
            'collection_run_id':self.job_id,'keyword':'Invitation Card',
            'buyer_country':'US','listing_currency_filter':'USD',
            'currency_filter_mode':'original_listing_currency',
            'sample_before_currency_filter':1,'excluded_currency_count':0,
            'collected_at':'2026-11-09T00:00:00+00:00',
            'listings':[{'listing_id':17, 'currency':'USD', 'title':'X'*1000, 'price':2.0}]
        }
        self.preview.write_text(json.dumps(self.record))
        self.job = {'status':'running','created':time.monotonic(),'keyword':'Invitation Card','country':'US',
                    'currency':'USD','preview':self.preview,'last_preview':None,'data':None,'error':None}
        ni._JOBS[self.job_id] = self.job
        self.client = TestClient(ni.app)
        self.mock = patch.object(ni,'_research_response',side_effect=lambda data, *args, **kwargs: {'listings':data['listings'], 'keyword':data['keyword']})
        self.mock.start()
    def tearDown(self):
        self.mock.stop()
        ni._JOBS.pop(self.job_id,None)
        self.temp.cleanup()
    def get(self):
        return self.client.get('/etsy/search/progress/'+self.job_id)
    def test_initial_preview_then_lightweight_polls_then_complete(self):
        first=self.get()
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['status'],'listings_ready')
        self.assertIn('data',first.json())
        second=self.get()
        self.assertEqual(second.status_code,200)
        self.assertEqual(second.json()['status'],'collecting')
        self.assertEqual(second.json()['stage'],'shops_enrichment')
        self.assertNotIn('data',second.json())
        self.assertLess(len(second.content),len(first.content)//4)
        self.job['status']='complete'
        self.job['data']={'keyword':'Invitation Card','listings':[{'currency':'USD'}]}
        final=self.get()
        self.assertEqual(final.json()['status'],'complete')
        self.assertIn('data',final.json())
    def test_waiting_before_preview_and_failure(self):
        self.preview.unlink()
        self.assertEqual(self.get().json()['status'],'collecting')
        self.job['status']='failed'
        self.job['error']='Etsy rate limit'
        self.assertEqual(self.get().json()['status'],'failed')
    def test_reject_invalid_id(self):
        self.assertEqual(self.client.get('/etsy/search/progress/not-a-job').status_code,404)
    def test_no_cross_country_preview(self):
        self.record['listings'][0]['currency']='GBP'
        self.preview.write_text(json.dumps(self.record))
        self.assertEqual(self.get().json()['status'],'collecting')

if __name__=='__main__': unittest.main()
