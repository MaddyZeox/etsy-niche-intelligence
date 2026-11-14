import unittest
from keyword_demand_intelligence import empty_research, normalize_record


class DemandSchemaTests(unittest.TestCase):
    def test_missing_data_is_null_not_zero(self):
        record = empty_research('car toys', 'US', 'en')
        self.assertFalse(record['data_available'])
        self.assertTrue(all(v is None for v in record['metrics'].values()))
        self.assertEqual(record['market'], 'US')

    def test_valid_estimate_keeps_provenance(self):
        record = normalize_record({
            'keyword': 'car toys', 'market': 'US', 'language': 'en',
            'provider': 'licensed_provider', 'source_type': 'third_party_estimate',
            'license_status': 'authorized', 'collected_at': '2026-11-10T12:00:00Z',
            'period_start': '2026-10-01', 'period_end': '2026-10-31',
            'metrics': {'etsy_monthly_searches': 123},
            'monthly_history': [{'month': '2026-09', 'searches': 99},
                                {'month': '2026-10', 'searches': 123}]
        })
        self.assertEqual(record['metrics']['etsy_monthly_searches'], 123)
        self.assertEqual(record['source_type'], 'third_party_estimate')
        self.assertEqual(record['monthly_history'][1]['month'], '2026-10')

    def test_google_cannot_masquerade_as_etsy(self):
        with self.assertRaises(ValueError):
            normalize_record({'keyword': 'car toys', 'provider': 'google',
                              'source_type': 'google_search',
                              'metrics': {'etsy_monthly_searches': 500}})

    def test_unavailable_cannot_have_claimed_volume(self):
        with self.assertRaises(ValueError):
            normalize_record({'keyword': 'car toys', 'provider': 'none',
                              'source_type': 'not_available',
                              'metrics': {'etsy_monthly_searches': 0}})

    def test_bad_history_rejected(self):
        with self.assertRaises(ValueError):
            normalize_record({'keyword': 'car toys', 'provider': 'provider',
                              'source_type': 'third_party_estimate',
                              'monthly_history': [{'month':'2026-13','searches':99}]})

    def test_non_english_supported(self):
        record = empty_research('jouets voiture', 'FR', 'fr')
        self.assertEqual(record['keyword'], 'jouets voiture')
        self.assertEqual(record['language'], 'fr')

    def test_negative_volume_rejected(self):
        with self.assertRaises(ValueError):
            normalize_record({'keyword': 'car toys', 'provider': 'provider',
                              'source_type': 'third_party_estimate',
                              'metrics': {'etsy_monthly_searches': -1}})

if __name__ == '__main__':
    unittest.main()
