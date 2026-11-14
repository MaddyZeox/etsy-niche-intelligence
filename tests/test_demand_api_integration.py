"""Safe keyword-demand API regression tests; no external API or paid data."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ni_demand_backend_test', ROOT / 'backend' / 'main.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class DemandAPIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(backend.app)

    def test_unavailable_volume_is_null(self):
        response = self.client.get('/research/keyword-demand', params={
            'keyword': 'car toys', 'market': 'US', 'language': 'en'})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertFalse(data['data_available'])
        self.assertEqual(data['keyword'], 'car toys')
        self.assertEqual(data['market'], 'US')
        self.assertEqual(data['language'], 'en')
        self.assertEqual(data['source_type'], 'not_available')
        self.assertEqual(data['monthly_history'], [])
        self.assertTrue(all(value is None for value in data['metrics'].values()))

    def test_global_non_english_keywords(self):
        response = self.client.get('/research/keyword-demand', params={
            'keyword': 'jouets voiture', 'market': 'GLOBAL', 'language': 'fr'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['language'], 'fr')

    def test_invalid_market_and_language_rejected(self):
        for key, value in [('market', 'USA'), ('language', 'bad language')]:
            response = self.client.get('/research/keyword-demand', params={
                'keyword': 'car toys', key: value})
            self.assertEqual(response.status_code, 422, response.text)

    def test_report_attachment_does_not_change_etsy_fields(self):
        sample = {'source': 'Etsy Open API v3', 'listings': [{'listing_id': 1, 'title': 'car toys'}],
                  'collected_at': '2026-11-10T10:00:00Z'}
        with patch.object(backend, '__relevance_audit', return_value={'ok': True}), \
             patch.object(backend, '__competitor_evidence', return_value={'ok': True}):
            report = backend._research_response(sample, 'car toys', 'US', True)
        self.assertEqual(report['sample_size'], 1)
        self.assertEqual(report['report_cache'], 'hit')
        self.assertEqual(report['listings'], sample['listings'])
        self.assertEqual(report['competitor_evidence'], {'ok': True})
        self.assertEqual(report['keyword_demand']['market'], 'US')
        self.assertIsNone(report['keyword_demand']['metrics']['etsy_monthly_searches'])

    def test_missing_module_reports_error_without_fallback_numbers(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td, patch.object(backend, '_PROJECT_ROOT', Path(td)):
            response = self.client.get('/research/keyword-demand', params={'keyword': 'car toys'})
        self.assertEqual(response.status_code, 503, response.text)

if __name__ == '__main__':
    unittest.main()
