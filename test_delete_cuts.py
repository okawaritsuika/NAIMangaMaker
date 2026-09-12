import copy
import tempfile
import unittest
from pathlib import Path
from test_story_fixture import DemoWorkbench


class DeleteCuts(unittest.TestCase):
    def test_unassigned_cut_deletion_is_persistent_and_preserves_other_cuts(self):
        with tempfile.TemporaryDirectory() as folder:
            wb = DemoWorkbench(Path(folder))
            project = wb.create(dict(seed='A gardener plants flowers', count=2))
            original = copy.deepcopy(project['panels'])
            result = wb.delete_panel(project['id'], original[0]['id'])
            self.assertEqual(result['panels'], original[1:])
            self.assertEqual(wb.load(project['id'])['panels'], original[1:])
            self.assertEqual(result['deleted_panels'][original[0]['id']]['panel'], original[0])
            wb.delete_panel(project['id'], original[1]['id'])
            self.assertEqual(wb.load(project['id'])['panels'], [])

    def test_assigned_cut_is_protected_and_deleted_page_can_restore_its_cut(self):
        with tempfile.TemporaryDirectory() as folder:
            wb = DemoWorkbench(Path(folder))
            project = wb.create(dict(seed='A gardener plants flowers', count=2))
            original = copy.deepcopy(project['panels'])
            page_id = 'g123456789abc'
            project['pages'].append(dict(id=page_id, title='1페이지', panel_ids=[original[0]['id']]))
            wb.commit(project)
            with self.assertRaises(ValueError):
                wb.delete_panel(project['id'], original[0]['id'])
            self.assertEqual(wb.load(project['id'])['panels'], original)
            wb.delete_page(project['id'], page_id)
            wb.delete_panel(project['id'], original[0]['id'])
            result = wb.restore_page(project['id'], page_id)
            self.assertEqual(result['panels'], original)
            self.assertEqual(result['pages'][0]['panel_ids'], [original[0]['id']])


if __name__ == '__main__':
    unittest.main()
