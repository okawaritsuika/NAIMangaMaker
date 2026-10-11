"""Offline HTTP checks for completion metadata and preserving saved comics."""
import base64
import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from engine.iterative_comic import save
from key_store import KeyStore
from server import Server


class ComicCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        store = KeyStore(self.directory / 'keys.json')
        credentials = patch.object(store, 'credential_for',
            side_effect=AssertionError('Completion must never request credentials'))
        self.credentials = credentials.start()
        self.addCleanup(credentials.stop)
        self.server = Server(('127.0.0.1', 0), store, self.directory)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.app = self.server.stories()
        self.workbench = self.app.workbench
        self.project = dict(id='p000000000001', title='저장된 만화', seed_ko='작은 정원',
            direction_ko='평화로운 이야기', place_ko='정원', characters_ko=[],
            revision=7, updated='2026-01-01T00:00:00+00:00', ended=True,
            panels=[dict(id='c000000000001', description='A flowerpot on a table.',
                description_ko='탁자 위의 화분', dialogues=[], dialogues_ko=[],
                camera=dict(subjects=[], framing='close-up'))],
            pages=[dict(id='g000000000001', panel_ids=['c000000000001'],
                status='draft', stale=True, renders=[])],
            deleted_pages=[dict(id='g000000000002', panel_ids=[])],
            panel_versions={}, operations=['o000000000001'],
            generation_options=dict(translation_mode='full', concise_prompts=True))
        self.path = self.workbench.folder(self.project['id']) / 'project.json'
        save(self.path, self.project)
        self.endpoint = '/api/story/projects/' + self.project['id']

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.server.story_app:
            self.server.story_app.pool.shutdown()

    def request(self, path, data=None):
        request = urllib.request.Request(self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'X-NAIMangaMaker': '1', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_completion_persists_reopens_and_is_idempotent_without_keys(self):
        legacy_bytes = self.path.read_bytes()
        self.assertIs(self.request(self.endpoint)['completed'], False)
        unchanged = self.request(self.endpoint + '/completion', {'completed': False})
        self.assertEqual(7, unchanged['revision'])
        self.assertEqual(legacy_bytes, self.path.read_bytes())

        for completed, revision in ((True, 8), (False, 9)):
            with self.subTest(completed=completed):
                response = self.request(self.endpoint + '/completion', {'completed': completed})
                self.assertIs(response['completed'], completed)
                self.assertEqual(revision, response['revision'])
                saved = self.workbench.load(self.project['id'])
                expected = copy.deepcopy(self.project)
                expected.update(completed=completed, revision=revision, updated=saved['updated'])
                self.assertEqual(expected, saved)
                self.assertNotEqual(self.project['updated'], saved['updated'])
                self.assertIs(self.request(self.endpoint)['completed'], completed)
                saved_bytes = self.path.read_bytes()
                repeated = self.request(self.endpoint + '/completion', {'completed': completed})
                self.assertEqual(response, repeated)
                self.assertEqual(saved_bytes, self.path.read_bytes())
        self.credentials.assert_not_called()

    def test_completion_rejects_missing_or_non_boolean_values_without_changes(self):
        before = self.path.read_bytes()
        payloads = [{}] + [{'completed': value} for value in (None, 0, 1, 'true', [], {})]
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request(self.endpoint + '/completion', payload)
                self.assertEqual(400, error.exception.code)
                error.exception.close()
                self.assertEqual(before, self.path.read_bytes())

    def test_completion_cannot_mutate_a_project_during_generation(self):
        before = self.path.read_bytes()
        for guard in ('active', 'starts', 'auto'):
            with self.subTest(guard=guard):
                with self.app.lock:
                    if guard == 'active':
                        self.app.active.add(self.project['id'])
                    else:
                        getattr(self.app, guard).runs['offline-run'] = dict(
                            id='offline-run', project_id=self.project['id'], status='running')
                try:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        self.request(self.endpoint + '/completion', {'completed': True})
                    self.assertEqual(400, error.exception.code)
                    error.exception.close()
                    self.assertEqual(before, self.path.read_bytes())
                finally:
                    with self.app.lock:
                        self.app.active.discard(self.project['id'])
                        self.app.starts.runs.clear()
                        self.app.auto.runs.clear()

    def test_library_completion_does_not_replace_render_or_ending_status(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
            'AAAAC0lEQVR42mP8/x8AAwMCAO+j6E0AAAAASUVORK5CYII=')
        expected_statuses = {}
        for number, status in enumerate(('draft', 'in_progress', 'rendered', 'complete'), 2):
            project = copy.deepcopy(self.project)
            project['id'] = 'p' + format(number, '012x')
            project['ended'] = status == 'complete'
            folder = self.workbench.folder(project['id'])
            if status == 'draft':
                project['pages'] = []
            elif status in ('rendered', 'complete'):
                project['pages'][0].update(status='rendered', stale=False,
                    image_url='/files/' + project['id'] + '/page.png')
                folder.mkdir(parents=True)
                (folder / 'page.png').write_bytes(png)
            save(folder / 'project.json', project)
            expected_statuses[project['id']] = status
        before = {row['id']: row for row in self.request('/api/library')['projects']}
        for pid, status in expected_statuses.items():
            with self.subTest(status=status):
                self.assertEqual(status, before[pid]['status'])
                self.assertIs(before[pid]['completed'], False)
                self.request('/api/story/projects/' + pid + '/completion', {'completed': True})
        after = {row['id']: row for row in self.request('/api/library')['projects']}
        for pid, status in expected_statuses.items():
            with self.subTest(status=status):
                self.assertIs(after[pid]['completed'], True)
                self.assertEqual(status, after[pid]['status'])
                for key in ('ended', 'panel_count', 'page_count', 'rendered_count', 'cover_url'):
                    self.assertEqual(before[pid][key], after[pid][key])

    def test_reset_draft_preserves_saved_projects_and_completion(self):
        self.request(self.endpoint + '/completion', {'completed': True})
        before = self.path.read_bytes()
        library_before = self.request('/api/library')
        self.request('/api/workspace/draft', dict(storySeed='지울 새 이야기', storyCount='7',
            storyAuto=True, characters=[dict(name='알렉스', personality='차분함')]))
        defaults = dict(storySeed='', storyDirection='', storyPlace='', storyAction='',
            storyState='', storyCount='2', storyDialogue='auto', storyTranslation='full',
            storyPages='3', storyMin='2', storyMax='4', storyAuto=False,
            storyImages=False, characters=[])
        self.assertTrue(self.request('/api/workspace/draft', defaults)['saved'])
        workspace = self.request('/api/workspace')
        self.assertEqual(defaults, workspace['draft'])
        self.assertEqual([self.project['id']], [row['id'] for row in workspace['projects']])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(library_before, self.request('/api/library'))


if __name__ == '__main__':
    unittest.main()
