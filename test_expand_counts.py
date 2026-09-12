import copy
import tempfile
import unittest
from pathlib import Path
from engine.iterative_comic_server import validate_job_payload
from test_story_fixture import DemoWorkbench


class ExpandCounts(unittest.TestCase):
    def test_more_than_two_on_both_sides_preserves_existing_panels(self):
        for position, count in [('before', 3), ('after', 7)]:
            with self.subTest(position=position), tempfile.TemporaryDirectory() as folder:
                wb = DemoWorkbench(Path(folder))
                project = wb.create(dict(seed='A gardener plants flowers', count=2))
                original = copy.deepcopy(project['panels'])
                payload = dict(anchor_id=original[0]['id'], position=position, intent='action', count=count)
                validate_job_payload('expand', payload)
                result = wb.expand(project['id'], payload)
                self.assertEqual(len(result['panels']), count + 2)
                self.assertEqual([p for p in result['panels'] if p['id'] in {o['id'] for o in original}], original)
                insertion = 0 if position == 'before' else 1
                self.assertEqual(len(result['panels'][insertion:insertion+count]), count)

    def test_positive_integer_without_maximum(self):
        validate_job_payload('expand', dict(count=1000))
        for value in [0, -1, 1.5, True, '3']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_job_payload('expand', dict(count=value))


if __name__ == '__main__':
    unittest.main()
