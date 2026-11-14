import json
import pathlib
import sys
import unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from universal_relevance_v3 import analyze,normalize

class RelevanceTests(unittest.TestCase):
    def test_title_phrase(self):
        r=analyze('minimalist wedding invitation',[{'listing_id':1,'title':'Minimalist Wedding Invitation Canva Template','tags':[]}]);self.assertEqual(r['counts']['strong_title_match'],1)
    def test_tag_only_is_not_direct(self):
        r=analyze('leather jacket',[{'listing_id':2,'title':'Denim coat','tags':['leather jacket']}]);self.assertEqual(r['counts']['tags_only'],1)
    def test_unicode_normalization(self):
        self.assertEqual(normalize('Invitación de Boda &amp; Minimalista'),'invitación de boda minimalista')
        r=analyze('invitation de mariage',[{'listing_id':1,'title':'Invitation de mariage minimaliste'}]);self.assertEqual(r['counts']['strong_title_match'],1)
    def test_non_english_does_not_invent_translation(self):
        r=analyze('wedding invitation',[{'listing_id':1,'title':'Hochzeitseinladung minimalistisch','tags':['hochzeitseinladung']}]);self.assertEqual(r['counts']['needs_review'],1)
    def test_dedup(self):
        r=analyze('mug',[{'listing_id':1,'title':'Mug'},{'listing_id':1,'title':'Mug'},{'listing_id':2,'title':'Mug'}]);self.assertEqual(r['sampled_unique'],2)
    def test_product_type_guard(self):
        r=analyze('wedding invitation',[{'listing_id':1,'title':'Wedding Invitation Set','tags':['wedding invitation']}],product_type='sweatshirt');self.assertEqual(r['counts']['needs_review'],1)
    def test_no_invented_score(self):
        r=analyze('tote bag',[{'listing_id':1,'title':'Canvas tote bag'}]);self.assertIsNone(r['difficulty']);self.assertEqual(r['verdict'],'NOT_SCORED')
    def test_missing_ids_and_invalid_tag_types(self):
        r=analyze('dog gift',[{'listing_id':None,'title':'dog gift'},{'listing_id':3,'title':'Dog gift','tags':None}]);self.assertEqual(r['sampled_unique'],1)
    def test_all_real_data(self):
        fixtures = ROOT/'tests'/'fixtures'
        for path in fixtures.glob('*.json'):
            data=json.loads(path.read_text(encoding='utf-8'))
            r=analyze(data['keyword'],data['listings'])
            self.assertEqual(sum(r['counts'].values()), len({str(x['listing_id']) for x in data['listings'] if x.get('listing_id') is not None}))
            self.assertFalse(r['model_validated'])

if __name__=='__main__': unittest.main()
