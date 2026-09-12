import copy
import tempfile
import unittest
from pathlib import Path
from test_story_fixture import DemoWorkbench


class PageInsertion(unittest.TestCase):
    def test_front_middle_and_default_append_preserve_existing_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            wb = DemoWorkbench(Path(folder))
            p = wb.create(dict(seed='A gardener plants flowers', count=2))
            ids = [panel['id'] for panel in p['panels']]
            for panel_id in ids:
                p = wb.page(p['id'], dict(panel_ids=[panel_id]))
            original = copy.deepcopy(p)
            p = wb.page(p['id'], dict(panel_ids=ids[::-1], before_page_id=p['pages'][1]['id']))
            self.assertEqual([p['pages'][i]['id'] for i in [0, 2]], [page['id'] for page in original['pages']])
            self.assertEqual(p['pages'][1]['panel_ids'], ids)
            p = wb.page(p['id'], dict(panel_ids=ids, before_page_id=p['pages'][0]['id']))
            self.assertEqual(p['pages'][1]['id'], original['pages'][0]['id'])
            before_append = copy.deepcopy(p['pages'])
            p = wb.page(p['id'], dict(panel_ids=ids))
            self.assertEqual(p['pages'][:-1], before_append)
            self.assertEqual(p['panels'], original['panels'])
            self.assertEqual([page['title'] for page in p['pages']], [f'{i}페이지' for i in range(1, 6)])

    def test_missing_target_does_not_fall_back_to_append(self):
        with tempfile.TemporaryDirectory() as folder:
            wb = DemoWorkbench(Path(folder))
            p = wb.create(dict(seed='A gardener plants flowers', count=2))
            with self.assertRaisesRegex(ValueError, '기준 페이지'):
                wb.page(p['id'], dict(panel_ids=[p['panels'][0]['id']], before_page_id='g000000000000'))
            self.assertEqual(wb.load(p['id']), p)


if __name__ == '__main__':
    unittest.main()
