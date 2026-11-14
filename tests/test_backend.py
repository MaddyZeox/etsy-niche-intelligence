import importlib.util
import pathlib
import sys
import unittest
from fastapi.testclient import TestClient
ROOT = pathlib.Path(__file__).resolve().parents[1]

class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('ni_local_main',ROOT/'backend'/'main.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.client=TestClient(module.app)

    def test_health(self):
        self.assertEqual(self.client.get('/health').status_code,200)

    def test_dashboard(self):
        r=self.client.get('/dashboard')
        self.assertEqual(r.status_code,200)
        self.assertIn('Keyword-to-product match audit',r.text)

    def test_audit_without_etsy_api(self):
        r=self.client.post('/research/relevance',json={'keyword':'leather jacket','listings':[{'listing_id':1,'title':'Leather jacket'}]})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['counts']['strong_title_match'],1)
        self.assertIsNone(r.json()['difficulty'])

    def test_empty_payload_is_rejected(self):
        self.assertEqual(self.client.post('/research/relevance',json={'keyword':'a','listings':[]}).status_code,422)

if __name__=='__main__':unittest.main()
