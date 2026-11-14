import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import keyring
except ImportError:
    sys.modules['keyring'] = types.SimpleNamespace(get_password=lambda *args: None)

spec = importlib.util.spec_from_file_location('collector_v48', Path(__file__).resolve().parents[1] / 'etsy_collector_keyword.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

class TestScheduling(unittest.TestCase):
    def setUp(self):
        collector._effective_interval_seconds = collector.REQUEST_INTERVAL_SECONDS
        collector._next_request = 0.0
        collector._reported_qps = None
        collector._api_requests = 0

    def test_unknown_headers_keep_conservative_pace(self):
        collector.observe_rate_limit_headers({})
        self.assertEqual(collector._effective_interval_seconds, .26)

    def test_verified_five_qps_uses_headroom(self):
        collector.observe_rate_limit_headers({'x-limit-per-second': '5'})
        self.assertAlmostEqual(collector._effective_interval_seconds, .22)

    def test_lower_qps_slows_down(self):
        collector.observe_rate_limit_headers({'x-limit-per-second': '2'})
        self.assertAlmostEqual(collector._effective_interval_seconds, .55)

    def test_large_or_bad_limit_never_exceeds_local_cap(self):
        collector.observe_rate_limit_headers({'x-limit-per-second': '500'})
        self.assertAlmostEqual(collector._effective_interval_seconds, .22)
        collector.observe_rate_limit_headers({'x-limit-per-second': 'not-number'})
        self.assertAlmostEqual(collector._effective_interval_seconds, .22)
        collector.observe_rate_limit_headers({'x-limit-per-second': '0'})
        self.assertAlmostEqual(collector._effective_interval_seconds, .22)

    def test_spacing_stays_under_limit(self):
        with patch.object(collector.time, 'monotonic', return_value=100.0), patch.object(collector.time, 'sleep') as sleep:
            collector.observe_rate_limit_headers({'x-limit-per-second': '5'})
            for _ in range(5):
                collector.request_slot()
        self.assertEqual(collector._api_requests, 5)
        self.assertAlmostEqual(collector._next_request, 101.1)
        self.assertEqual(sleep.call_count, 4)

    def test_existing_country_and_filename_rules_unchanged(self):
        self.assertEqual(collector.MARKET_CURRENCY['US'], 'USD')
        self.assertEqual(collector.MARKET_CURRENCY['GB'], 'GBP')
        self.assertEqual(collector.research_filename('Visiting Card', 'US', 'native-USD'), 'etsy_visiting-card_fc2a9854_US_NATIVE-USD.json')

    def test_preview_cli_option_remains(self):
        source = (Path(__file__).resolve().parents[1] / 'etsy_collector_keyword.py').read_text()
        self.assertIn('"--preview-path"', source)
        self.assertIn('TIMING shop_enrichment_seconds:', source)
        self.assertIn('CACHE_TTL_SECONDS = 24 * 60 * 60', source)

if __name__ == '__main__':
    unittest.main()
