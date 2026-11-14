import unittest
from related_keyword_discovery import discover


class RelatedKeywordDiscoveryTests(unittest.TestCase):
    def test_tags_and_titles_dedupe_per_listing(self):
        sample = [
            {'listing_id': 1, 'title': 'Wooden car toys for kids', 'tags': ['wooden car', 'wooden car', 'car toys'], 'favorites': 10, 'shop_id': 10},
            {'listing_id': 2, 'title': 'Wooden car toys play set', 'tags': ['wooden car'], 'favorites': 2, 'shop_id': 20},
        ]
        rows = discover('car toys', sample)['suggestions']
        found = next(x for x in rows if x['phrase'] == 'wooden car')
        self.assertEqual((found['listing_count'], found['seller_tag_listing_count'], found['unique_shops_with_ids']), (2, 2, 2))
        self.assertEqual(found['median_listing_favorites'], 6)

    def test_unrelated_tag_ignored(self):
        sample = [{'listing_id': 1, 'title': 'toy plane', 'tags': ['home decor', 'car train']},
                  {'listing_id': 2, 'title': 'toy plane', 'tags': ['home decor', 'car train']}]
        rows = discover('car toys', sample)['suggestions']
        self.assertFalse(any(x['phrase'] == 'home decor' for x in rows))
        self.assertTrue(any(x['phrase'] == 'car train' for x in rows))

    def test_no_invented_metrics(self):
        result = discover('car toys', [])
        self.assertIsNone(result['etsy_monthly_searches'])
        self.assertIsNone(result['keyword_difficulty'])
        self.assertFalse(result['direct_competitors_verified'])

    def test_unicode(self):
        sample = [{'listing_id': 1, 'title': '陶器 茶碗 手作り', 'tags': ['陶器 茶碗']},
                  {'listing_id': 2, 'title': '陶器 茶碗 素敵', 'tags': ['陶器 茶碗']}]
        self.assertIn('陶器 茶碗', [x['phrase'] for x in discover('陶器', sample)['suggestions']])

    def test_no_translation_claim(self):
        result = discover('cup', [{'listing_id': 1, 'title': 'tasse café'}, {'listing_id': 2, 'title': 'tasse café'}])
        self.assertEqual(result['suggestions'], [])
        self.assertFalse(result['cross_language_semantics_verified'])

    def test_duplicate_ids_not_double_counted(self):
        sample = [{'listing_id': 1, 'tags': ['toy car set']}, {'listing_id': '1', 'tags': ['toy car set']},
                  {'listing_id': 2, 'tags': ['toy car set']}]
        self.assertEqual(discover('toy car', sample)['suggestions'][0]['listing_count'], 2)

    def test_invalid_favorites_not_used(self):
        sample = [{'listing_id': 1, 'tags': ['toy car set'], 'favorites': True},
                  {'listing_id': 2, 'tags': ['toy car set'], 'favorites': -5}]
        row = discover('toy car', sample)['suggestions'][0]
        self.assertIsNone(row['median_listing_favorites'])
        self.assertEqual(row['listings_with_favorites'], 0)

    def test_minimum_and_limits(self):
        with self.assertRaises(ValueError):
            discover('car', [], limit=0)
        with self.assertRaises(ValueError):
            discover('car', [], min_listings=0)
        with self.assertRaises(ValueError):
            discover('', [])
        with self.assertRaises(ValueError):
            discover('car', [{}] * 5001)

if __name__ == '__main__':
    unittest.main()
