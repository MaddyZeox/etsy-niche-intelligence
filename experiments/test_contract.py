import json
import unittest
from pathlib import Path
from universal_contract import normalize

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.base = {'keyword':'minimalist wedding invitation', 'listings':[{'listing_id':1,'title':'Hochzeitseinladung elegant', 'tags':['einladung','Hochzeit'], 'currency':'EUR', 'shop_total_sales':50}]}

    def test_lossless_unicode(self):
        result=normalize(self.base,country=None,scope='unfiltered')
        self.assertEqual(result['records'][0]['original']['title'],'Hochzeitseinladung elegant')
        self.assertIsNone(result['records'][0]['language']['detected'])
        self.assertIsNone(result['records'][0]['relevance']['confidence'])
        self.assertEqual(result['records'][0]['seller_signals']['shop_total_sales'],50)

    def test_country_is_not_fake_filter(self):
        result=normalize(self.base,country='de',scope='buyer_shipping_eligible')
        self.assertEqual(result['market']['target_country'],'DE')
        self.assertFalse(result['market']['country_filter_verified_applied_at_collection'])
        self.assertIsNone(result['market']['buyer_search_volume_by_country'])

    def test_reject_duplicate_ids(self):
        d=dict(self.base, listings=self.base['listings']*2)
        with self.assertRaisesRegex(ValueError,'duplicate listing IDs'):
            normalize(d,country=None,scope='unfiltered')

    def test_invalid_country(self):
        with self.assertRaises(ValueError):
            normalize(self.base,country='USA',scope='unfiltered')

    def test_no_country_for_scope(self):
        with self.assertRaises(ValueError):
            normalize(self.base,country=None,scope='shop_location')

    def test_missing_title(self):
        d=dict(self.base,listings=[{'listing_id':1,'title':''}])
        with self.assertRaisesRegex(ValueError,'title'):
            normalize(d,country=None,scope='unfiltered')

if __name__=='__main__': unittest.main(verbosity=2)
