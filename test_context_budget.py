"""Offline semantic-budget and persisted summary regression tests."""
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.context_budget import fit_request, request_bytes, SUMMARY_INPUT_BYTES, _summary_chunk, _history_source, _summarize
from engine.iterative_comic import Workbench, read, save
from engine.iterative_recovery import RecoverySession


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workbench = Workbench(temporary.name, key_provider=lambda: 'offline-key')
        self.project = self.workbench.folder('p123456789abc')
        self.folder = self.project / 'operations' / 'o123456789abc'
        self.calls = []

        def generate(request_path, output_path, *_args, **_kwargs):
            request = read(request_path)
            self.calls.append(request)
            self.assertEqual('glm-4-6', request['model'])
            self.assertLessEqual(request_bytes(request), SUMMARY_INPUT_BYTES)
            self.assertEqual('context_summary_request.json', request_path.name)
            target = int(re.search(r'at most (\d+) UTF-8 bytes', request['messages'][-1]['content']).group(1))
            task = request['messages'][-1]['content']
            if 'Reference scope: Fixed FUTURE' in task:
                memory = 'FUTURE: Aster must later find the key. This event has not happened. The ending stays unresolved.'
            elif 'Reference scope: Accepted PAST' in task:
                memory = 'PAST: Aster accepted the quest and promised to find the key. The key remains missing; never wear red.'
            else:
                memory = 'Aster (actor1) must never wear red. Find the missing key; the ending remains unresolved.'
            self.assertLessEqual(len(memory.encode('utf-8')), target)
            save(output_path / 'result.json', dict(http_status=200, finish_reason='stop'))
            (output_path / 'story.txt').write_text(json.dumps({'memory': memory}), encoding='utf-8')

        self.transport = patch('engine.iterative_comic.generate', side_effect=generate)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    @staticmethod
    def request(value, *, model='xialong-v1', prefix='Use the supplied facts.\n', suffix=''):
        return dict(model=model, max_tokens=4096, temperature=.25, enable_thinking=False, stream=True,
                    messages=[dict(role='system', content='Follow the output contract exactly.'),
                              dict(role='user', content=prefix + json.dumps(value, ensure_ascii=False) + suffix)])

    @staticmethod
    def panel(index):
        return dict(id=f'c{index:012x}', actor='actor1', description=f'Aster searches location {index}. ' * 12,
                    background='The garden', dialogues=['The key is still missing.'],
                    camera=dict(subjects=['actor1'], focus='Aster', framing='wide', angle='side'),
                    visual_states={'actor1': dict(appearance=['adult', 'blue coat', 'no red clothes'],
                                                  held_objects=[], pose='standing')})

    def test_two_hundred_kilobyte_initial_json_preserves_structure_ids_and_schema(self):
        value = dict(seed=('Aster must never wear red. Find the missing key; ending unresolved. ' * 3500),
                     characters=[dict(id='actor1', name='Aster', gender='auto', personality='Careful', appearance='Blue coat')],
                     count=2, dialogue='none', intent='story', state_change='Keep the blue coat')
        schema = '\nReturn JSON only:\n{"panels":[{"id":"unchanged ID","description":"one instant"}],"ended":false}'
        request = self.request(value, suffix=schema)
        original = copy.deepcopy(request)
        self.assertGreater(len(value['seed'].encode('utf-8')), 200000)
        fitted = fit_request(self.workbench, self.folder, 'story', request)
        self.assertLessEqual(request_bytes(fitted), 28672)
        content = fitted['messages'][-1]['content']
        result = json.loads(content[len('Use the supplied facts.\n'):-len(schema)])
        self.assertEqual(value['characters'], result['characters'])
        for key in ('count', 'dialogue', 'intent', 'state_change'):
            self.assertEqual(value[key], result[key])
        self.assertIn('never wear red', result['seed'])
        self.assertIn('unresolved', result['seed'])
        self.assertTrue(content.endswith(schema))
        self.assertEqual(original, request)
        self.assertEqual(request['max_tokens'], fitted['max_tokens'])
        self.assertTrue(self.calls)
        self.assertTrue(list((self.project / '_context_memory').glob('*/source.txt')))

    def test_korean_input_translation_uses_utf8_and_smaller_echo_budget(self):
        value = dict(seed='아스터는 빨간 옷을 절대 입지 않는다. 잃어버린 열쇠를 찾아야 한다. ' * 1500,
                     characters=[dict(id='actor1', name='아스터', appearance='파란 외투')], count=1)
        request = self.request(value, model='glm-4-6')
        fitted = fit_request(self.workbench, self.folder, 'input_translation', request)
        self.assertLessEqual(request_bytes(fitted), 12000)
        result = json.loads(fitted['messages'][-1]['content'].split('\n', 1)[1])
        self.assertEqual(value['characters'], result['characters'])
        self.assertEqual(1, result['count'])
        self.assertIn('never wear red', result['seed'])

    def test_past_and_future_history_keep_opposite_adjacent_boundaries(self):
        past = [self.panel(index) for index in range(80)]
        future = [self.panel(index) for index in range(80, 160)]
        request = self.request(future, prefix='Fixed suffix:\n', suffix='\nThese are FUTURE; do not rewrite them.')
        request['messages'].insert(1, dict(role='assistant', content=json.dumps(past)))
        fitted = fit_request(self.workbench, self.folder, 'story', request)
        self.assertLessEqual(request_bytes(fitted), 28672)
        history = json.loads(fitted['messages'][1]['content'])
        self.assertEqual(past[-8:], history['recent_panels'])
        self.assertIn('PAST', history['earlier_accepted_memory'])
        fixed = json.loads(fitted['messages'][-1]['content'][len('Fixed suffix:\n'):
                    -len('\nThese are FUTURE; do not rewrite them.')])
        self.assertEqual(future[:8], fixed['next_fixed_panels'])
        self.assertIn('FUTURE', fixed['later_fixed_future_memory'])
        self.assertIn('not happened', fixed['later_fixed_future_memory'])
        self.assertGreater(sum('Reference scope: Fixed FUTURE' in row['messages'][-1]['content']
                               for row in self.calls), 1)

    def test_director_controls_current_pages_and_active_states_are_exact(self):
        panels = [self.panel(index) for index in range(100)]
        value = dict(accepted_story=panels, current_page=panels[-2:], last_completed_page=panels[-5:-2],
                     latest_active_states=panels[-1]['visual_states'], current_page_slots=2,
                     closing_required=True, accepted_panel_count=100, canonical_ended_hint=False)
        fitted = fit_request(self.workbench, self.folder, 'director', self.request(value, model='glm-4-6'))
        result = json.loads(fitted['messages'][-1]['content'].split('\n', 1)[1])
        self.assertEqual(panels[-8:], result['accepted_story'])
        for key in set(value) - {'accepted_story'}:
            self.assertEqual(value[key], result[key])
        self.assertIn('accepted_story_memory', result)

    def test_summary_cache_is_reused_across_operations_and_recovery_session(self):
        request = self.request(dict(direction='Aster must never wear red. The missing key is unresolved. ' * 2000))
        job = dict(project_id=self.project.name)
        with RecoverySession(self.workbench, job, lambda: None):
            first = fit_request(self.workbench, self.folder, 'story', request)
        calls = len(self.calls)
        self.assertGreater(calls, 0)
        self.assertTrue(job['stage_complete'])
        self.assertTrue(job['stage_folder'].startswith('_context_memory/'))
        second = fit_request(self.workbench, self.project / 'operations' / 'oaaaaaaaaaaaa', 'story', request)
        self.assertEqual(first, second)
        self.assertEqual(calls, len(self.calls))

    def test_summary_cache_preserves_crlf_source_bytes_and_reuses_response(self):
        source = 'Aster must never wear red.\r\nThe missing key remains unresolved.\r\n' * 40
        first = _summary_chunk(self.workbench, self.folder, source, 256, 'User facts and requests')
        saved_source = next((self.project / '_context_memory').glob('*/source.txt'))
        self.assertEqual(source.encode('utf-8'), saved_source.read_bytes())
        calls = len(self.calls)
        second = _summary_chunk(self.workbench, self.project / 'operations' / 'oaaaaaaaaaaaa',
                                source, 256, 'User facts and requests')
        self.assertEqual(first, second)
        self.assertEqual(1, calls)
        self.assertEqual(calls, len(self.calls))

    def test_leaf_notes_that_fit_large_target_are_kept_without_lossy_merge(self):
        source = ''.join(f'SOURCE{index:02d}:' + 'x' * 9991 for index in range(8))
        notes = []

        def summarize(request_path, output_path, *_args, **_kwargs):
            request = read(request_path)
            self.assertLessEqual(request_bytes(request), SUMMARY_INPUT_BYTES)
            actual_source = request['messages'][-1]['content'].split('\nReference:\n', 1)[1]
            self.assertTrue(actual_source.startswith('SOURCE'))
            index = int(actual_source[6:8])
            memory = f'Chapter {index}: Aster keeps the promise. ' + 'The search remains unresolved. ' * 45
            notes.append(memory)
            save(output_path / 'result.json', dict(http_status=200, finish_reason='stop'))
            (output_path / 'story.txt').write_text(json.dumps(dict(memory=memory)), encoding='utf-8')

        with patch('engine.iterative_comic.generate', side_effect=summarize) as generate:
            memory = _summarize(self.workbench, self.folder, source, 16000, 'Accepted PAST events')
            self.assertEqual(8, generate.call_count)
            self.assertGreater(len(memory.encode('utf-8')), 10000)
            self.assertLessEqual(len(memory.encode('utf-8')), 16000)
            self.assertTrue(all(note in memory for note in notes))
            repeated = _summarize(self.workbench, self.project / 'operations' / 'oaaaaaaaaaaaa',
                                  source, 16000, 'Accepted PAST events')
            self.assertEqual(memory, repeated)
            self.assertEqual(8, generate.call_count)

    def test_history_memory_uses_available_space_and_final_request_stays_bounded(self):
        panels = [self.panel(index) for index in range(88)]
        for panel in panels:
            panel['description'] = 'Aster continues the unresolved search. ' * 25
        request = self.request(dict(count=2, direction='Keep the promise unresolved.'))
        request['messages'].insert(1, dict(role='assistant', content=json.dumps(panels)))

        def summarize(request_path, output_path, *_args, **_kwargs):
            request = read(request_path)
            self.assertLessEqual(request_bytes(request), SUMMARY_INPUT_BYTES)
            memory = f'Reference {generate.call_count}: ' + 'Aster keeps each earlier promise unresolved. ' * 35
            save(output_path / 'result.json', dict(http_status=200, finish_reason='stop'))
            (output_path / 'story.txt').write_text(json.dumps(dict(memory=memory)), encoding='utf-8')

        with patch('engine.iterative_comic.generate', side_effect=summarize) as generate:
            fitted = fit_request(self.workbench, self.folder, 'story', request)
        history = json.loads(fitted['messages'][1]['content'])
        self.assertEqual(panels[-8:], history['recent_panels'])
        self.assertGreater(len(history['earlier_accepted_memory'].encode('utf-8')), 4096)
        self.assertLessEqual(request_bytes(fitted), 28672)
        self.assertEqual(request['max_tokens'], fitted['max_tokens'])

    def test_summary_cache_versions_and_targets_keep_prior_records_intact(self):
        source = 'Aster must never wear red. The key remains missing. ' * 80
        with patch('engine.context_budget.SUMMARY_VERSION', 'story-memory-v2'):
            old = _summary_chunk(self.workbench, self.folder, source, 256, 'Accepted PAST events')
        cache = self.project / '_context_memory'
        saved = {path: path.read_bytes() for path in cache.rglob('*') if path.is_file()}
        new = _summary_chunk(self.workbench, self.folder, source, 512, 'Accepted PAST events')
        small = _summary_chunk(self.workbench, self.folder, source, 256, 'Accepted PAST events')
        self.assertEqual(old, new)
        self.assertEqual(old, small)
        self.assertEqual(3, len(self.calls))
        self.assertEqual(3, len(list(cache.glob('*/source.txt'))))
        self.assertEqual(new, _summary_chunk(self.workbench, self.folder, source, 512, 'Accepted PAST events'))
        self.assertEqual(3, len(self.calls))
        for path, original in saved.items():
            self.assertEqual(original, path.read_bytes())

    def test_oversized_current_scene_is_not_summarized(self):
        panel = self.panel(1)
        panel['description'] = 'The actual current description must stay exact. ' * 1000
        request = self.request(dict(panels=[panel]))
        with self.assertRaisesRegex(ValueError, '필수 인물·컷 정보'):
            fit_request(self.workbench, self.folder, 'output_translation', request)
        self.assertEqual([], self.calls)

    def test_oversized_unstructured_system_is_rejected_before_network(self):
        request = self.request(dict(seed='Aster finds a key'))
        request['messages'][0]['content'] = 'Keep this exact custom output contract. ' * 1000
        with self.assertRaisesRegex(ValueError, '고정 지시문'):
            fit_request(self.workbench, self.folder, 'story', request)
        self.assertEqual([], self.calls)

    def test_failed_summary_is_never_implicitly_resent(self):
        request = self.request(dict(seed='Aster must never wear red. ' * 2000))
        with patch('engine.iterative_comic.generate', side_effect=RuntimeError('offline network failure')) as generate:
            with self.assertRaisesRegex(RuntimeError, 'offline network failure'):
                fit_request(self.workbench, self.folder, 'story', request)
            with self.assertRaisesRegex(ValueError, '미완료 응답'):
                fit_request(self.workbench, self.folder, 'story', request)
            self.assertEqual(1, generate.call_count)

    def test_summary_that_does_not_shrink_is_rejected_without_slicing(self):
        request = self.request(dict(seed='Aster must never wear red. ' * 2000))

        def excessive(_request_path, output_path, *_args, **_kwargs):
            save(output_path / 'result.json', dict(http_status=200, finish_reason='stop'))
            (output_path / 'story.txt').write_text(json.dumps(dict(memory='Unshortened memory. ' * 2000)), encoding='utf-8')

        with patch('engine.iterative_comic.generate', side_effect=excessive) as generate:
            with self.assertRaisesRegex(ValueError, '기억 요약이 요청한 길이'):
                fit_request(self.workbench, self.folder, 'story', request)
            self.assertEqual(1, generate.call_count)

    def test_oversized_valid_summary_is_recompressed_from_saved_output_and_cached(self):
        source = 'Aster must never wear red. The missing key remains unresolved. ' * 100
        intermediate = 'Aster must never wear red. The key is still missing. ' * 45
        final = 'Aster must never wear red. The key remains missing.'
        requests = []

        def summarize(request_path, output_path, *_args, **_kwargs):
            request = read(request_path)
            requests.append((request_path, request))
            self.assertLessEqual(request_bytes(request), SUMMARY_INPUT_BYTES)
            actual_source = request['messages'][-1]['content'].split('\nReference:\n', 1)[1]
            self.assertEqual(source if len(requests) == 1 else intermediate, actual_source)
            self.assertLessEqual(len(actual_source.encode('utf-8')), 10000)
            save(output_path / 'result.json', dict(http_status=200, finish_reason='stop'))
            (output_path / 'story.txt').write_text(json.dumps(dict(memory=intermediate if len(requests) == 1 else final)),
                                                 encoding='utf-8')

        with patch('engine.iterative_comic.generate', side_effect=summarize) as generate:
            result = _summary_chunk(self.workbench, self.folder, source, 256, 'Accepted PAST events')
            self.assertEqual(final, result)
            self.assertEqual(2, generate.call_count)
            self.assertNotEqual(requests[0][0], requests[1][0])
            preserved = read(requests[0][0].parent / 'context_summary' / 'parsed.json')
            self.assertEqual(intermediate, preserved['memory'])
            repeated = _summary_chunk(self.workbench, self.project / 'operations' / 'oaaaaaaaaaaaa',
                                      source, 256, 'Accepted PAST events')
            self.assertEqual(final, repeated)
            self.assertEqual(2, generate.call_count)

    def test_additional_compression_has_a_fixed_limit_and_preserves_each_result(self):
        source = 'Aster must never wear red. ' * 250
        lengths = (3000, 2000, 1000)

        def summarize(request_path, output_path, *_args, **_kwargs):
            request = read(request_path)
            self.assertLessEqual(request_bytes(request), SUMMARY_INPUT_BYTES)
            save(output_path / 'result.json', dict(http_status=200, finish_reason='stop'))
            (output_path / 'story.txt').write_text(json.dumps(dict(memory='A' * lengths[generate.call_count - 1])),
                                                 encoding='utf-8')

        with patch('engine.iterative_comic.generate', side_effect=summarize) as generate:
            with self.assertRaisesRegex(ValueError, '추가 압축 후에도'):
                _summary_chunk(self.workbench, self.folder, source, 256, 'User facts')
            self.assertEqual(3, generate.call_count)
            self.assertEqual(3, len(list((self.project / '_context_memory').glob('*/context_summary/parsed.json'))))
            with self.assertRaisesRegex(ValueError, '추가 압축 후에도'):
                _summary_chunk(self.workbench, self.folder, source, 256, 'User facts')
            self.assertEqual(3, generate.call_count)

    def test_output_reservation_is_preserved_and_lowers_input_budget(self):
        request = self.request(dict(seed='Aster must never wear red. ' * 2000))
        request['max_tokens'] = 20000
        fitted = fit_request(self.workbench, self.folder, 'story', request)
        self.assertEqual(20000, fitted['max_tokens'])
        self.assertLessEqual(request_bytes(fitted), 36864 - 20000 - 1024)

    def test_state_evidence_keeps_timeline_and_does_not_invent_missing_fields(self):
        rows = [
            dict(id='boots', prefix_index=3, description='Aster puts on blue boots.',
                 visual_states={'actor1': dict(appearance=['blue boots'])}),
            dict(id='book', prefix_index=50, description='Aster sets the book down.',
                 visual_states={'actor1': dict(held_objects=[])})]
        original = copy.deepcopy(rows)
        events = list(_history_source(rows))
        self.assertEqual([3, 50], [row['prefix_index'] for row in events])
        self.assertEqual({'actor1': {'appearance': ['blue boots']}}, events[0]['state_changes'])
        self.assertEqual({'actor1': {'held_objects': []}}, events[1]['state_changes'])
        self.assertEqual(original, rows)

    def test_partial_evidence_retains_unobserved_prior_fields(self):
        rows = [
            dict(id='start', visual_states={'actor1': dict(appearance=['blue coat'], held_objects=['book'])}),
            dict(id='boots', visual_states={'actor1': dict(appearance=['blue coat', 'blue boots'])}),
            dict(id='still_holding', visual_states={'actor1': dict(held_objects=['book'])}),
            dict(id='drop', visual_states={'actor1': dict(held_objects=[])}),
            dict(id='same_outfit', visual_states={'actor1': dict(appearance=['blue coat', 'blue boots'])})]
        events = list(_history_source(rows))
        self.assertEqual({'actor1': {'appearance': ['blue coat', 'blue boots']}}, events[1]['state_changes'])
        self.assertNotIn('state_changes', events[2])
        self.assertEqual({'actor1': {'held_objects': []}}, events[3]['state_changes'])
        self.assertNotIn('state_changes', events[4])


if __name__ == '__main__':
    unittest.main()
