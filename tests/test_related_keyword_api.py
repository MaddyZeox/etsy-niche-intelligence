import importlib.util, pathlib, tempfile, unittest, shutil, sys
from unittest.mock import patch
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('related_api_test_backend', ROOT / 'backend' / 'main.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

class RelatedKeywordAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(backend.app)
        self.listings = [
            {'listing_id': 1, 'shop_id': 11, 'title': 'Handmade pottery spoon rest', 'tags': ['ceramic spoon rest', 'pottery spoon rest'], 'favorites': 12},
            {'listing_id': 2, 'shop_id': 12, 'title': 'Ceramic pottery spoon rest', 'tags': ['ceramic spoon rest'], 'favorites': 4},
        ]

    def test_response_contains_discovery(self):
        result = self.client.post('/research/related-keywords', json={'keyword':'spoon rest','listings':self.listings})
        self.assertEqual(result.status_code, 200, result.text)
        data=result.json()
        self.assertGreater(len(data['suggestions']), 0)
        self.assertIsNone(data['etsy_monthly_searches'])
        self.assertFalse(data['direct_competitors_verified'])

    def test_empty_and_unrelated(self):
        result = self.client.post('/research/related-keywords',json={'keyword':'spoon rest','listings':[]})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['suggestions'],[])

    def test_input_validation(self):
        result=self.client.post('/research/related-keywords',json={'keyword':'spoon rest','listings':[], 'limit':101})
        self.assertEqual(result.status_code,422)

    def test_report_uses_same_listing_cohort(self):
        # Reuse report's validated listings; no fresh Etsy request.
        with patch.object(backend, '_keyword_demand_report', return_value={'data_available': False}), \
             patch.object(backend, '__relevance_audit', return_value={}), \
             patch.object(backend, '__competitor_evidence', return_value={}):
            result=backend._research_response({'source':'Etsy Open API v3','listings':self.listings}, 'spoon rest', 'US', False)
        self.assertEqual(result['related_keywords']['sampled_unique'], len(self.listings))
        self.assertTrue(result['related_keywords']['suggestions'])
