import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_story_fixture import DemoWorkbench


class EmphasisFixture(DemoWorkbench):
    conflict = False

    def reply(self, folder, name, task):
        if name == 'story' and (Path(folder) / 'before.json').exists():
            if self.conflict:
                return dict(conflict='An outfit change requires action connection.', panels=[])
            result = super().reply(folder, name, task)
            for panel in result['panels']:
                panel['camera']['angle'] = 'from above'
            return result
        if name == 'conflict_translation':
            return dict(reason='착장 변경은 행동 연결로 요청해 주세요.')
        return super().reply(folder, name, task)


class EmphasisHints(unittest.TestCase):
    def test_expression_hint_between_existing_pages_keeps_pages_and_cuts(self):
        with tempfile.TemporaryDirectory() as folder:
            wb = EmphasisFixture(Path(folder))
            project = wb.create(dict(seed='A gardener plants flowers', count=2))
            for panel in project['panels']:
                wb.page(project['id'], dict(panel_ids=[panel['id']], layout='auto'))
            before = copy.deepcopy(wb.load(project['id']))
            hint = 'gentle smile, looking toward the flowerpot'
            result = wb.expand(project['id'], dict(anchor_id=before['panels'][0]['id'],
                position='after', intent='emphasis', count=2, state_change=hint))
            self.assertEqual(result['panels'][0], before['panels'][0])
            self.assertEqual(result['panels'][-1], before['panels'][-1])
            self.assertEqual(len(result['panels']), 4)
            self.assertEqual(result['pages'], before['pages'])
            operation = result['operations'][-1]['id']
            stored = json.loads((wb.folder(project['id']) / 'operations' / operation / 'input_en.json').read_text())
            self.assertEqual(stored['state_change'], hint)
            self.assertIn('interpret state_change by its meaning', next(call['task'] for call in reversed(wb.demo_calls) if call['stage'] == 'story'))

    def test_actual_conflict_still_preserves_project(self):
        with tempfile.TemporaryDirectory() as folder:
            wb = EmphasisFixture(Path(folder))
            before = wb.create(dict(seed='A gardener plants flowers', count=2))
            wb.conflict = True
            with self.assertRaisesRegex(ValueError, '착장 변경'):
                wb.expand(before['id'], dict(anchor_id=before['panels'][0]['id'],
                    position='after', intent='emphasis', count=1, state_change='replace the green shirt with a red coat'))
            self.assertEqual(wb.load(before['id']), before)


if __name__ == '__main__':
    unittest.main()
