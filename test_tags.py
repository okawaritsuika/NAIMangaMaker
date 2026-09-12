import unittest
from tag_catalog import catalog, search

class TagTests(unittest.TestCase):
    def test_bundled_data_and_categories(self):
        self.assertEqual(len(catalog()), 191110)
        self.assertEqual(len({c for r in catalog() for c in r['categories']}), 14)
    def test_exact_spaces_and_limits(self):
        self.assertEqual(search('long hair')[0]['tag'], 'long_hair')
        self.assertEqual(search('long_hair'), search('long hair'))
        self.assertLessEqual(len(search('a')), 20)
        self.assertEqual(search(''), [])
        self.assertEqual(search('x'*81), [])
        self.assertEqual(search('no_such_tag_983427123'), [])
    def test_category(self):
        self.assertIn('expression', search('smile')[0]['categories'])
    def test_complete_translation_coverage(self):
        expected = [r for r in catalog() if not set(r['categories']) & {'artist', 'character', 'series'}]
        self.assertEqual(len(expected), 37697)
        self.assertEqual(sum(bool(r['translation']) for r in catalog()), 37697)
        for row in expected:
            self.assertRegex(row['translation'], '[가-힣]', row['tag'])
    def test_korean_translation_and_search(self):
        self.assertEqual(search('cowboy_shot')[0]['translation'], '허벅지 위까지 담은 구도')
        self.assertEqual(search('허벅지 위까지 담은 구도')[0]['tag'], 'cowboy_shot')
        self.assertIn('pleated_skirt', [r['tag'] for r in search('주름치마')])
        # Character/artist tags remain present even without Korean translations.
        row=next(r for r in catalog() if 'artist' in r['categories'])
        self.assertEqual(search(row['tag'])[0]['tag'],row['tag'])

if __name__ == '__main__': unittest.main()
