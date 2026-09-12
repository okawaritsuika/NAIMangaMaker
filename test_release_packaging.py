import json
import tempfile
import unittest
from pathlib import Path
from release_packaging import ROOT, resource_args


class ReleasePackaging(unittest.TestCase):
    def test_reviewed_resources_exist_and_only_public_data_is_listed(self):
        args = resource_args()
        names = json.loads((ROOT / 'release_resources.json').read_text(encoding='utf-8'))['files']
        self.assertEqual(len(args), 2 * len(names))
        self.assertEqual({n for n in names if n.endswith('.json')}, {
            'assets/tags/ko.json', 'assets/tags/ko-progress.json', 'assets/tags/manifest.json',
            'engine/reference_settings.json'})
        self.assertNotIn('web/reader.html', names)

    def test_unlisted_runtime_data_cannot_be_bundled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'engine').mkdir()
            (root / 'engine/default_system.txt').write_text('public default')
            (root / 'engine/keys.json').write_text('private runtime fixture')
            manifest = root / 'release_resources.json'
            manifest.write_text(json.dumps(dict(version=1, files=['engine/default_system.txt'])))
            self.assertNotIn('keys.json', str(resource_args(root)))
            for name in ('engine/keys.json', '../keys.json', 'engine/__pycache__/cached.pyc'):
                manifest.write_text(json.dumps(dict(version=1, files=[name])))
                with self.assertRaises(ValueError):
                    resource_args(root)


if __name__ == '__main__':
    unittest.main()
