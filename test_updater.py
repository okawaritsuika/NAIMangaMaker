import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import updater


class Updates(unittest.TestCase):
    def release(self, body=b'MZnew'):
        return dict(version='v1.0.1', url=updater.RELEASES_URL+'/download/v1.0.1/'+updater.ASSET_NAME,
                    sha256=hashlib.sha256(body).hexdigest(), size=len(body))

    def test_release_validation(self):
        release=self.release()
        data=dict(tag_name='v1.0.1', assets=[dict(name=updater.ASSET_NAME,
            browser_download_url=release['url'],digest='sha256:'+release['sha256'],size=release['size'])])
        self.assertEqual(updater.parse_release(data)['version'],'v1.0.1')
        data['assets'][0]['browser_download_url']='https://example.com/program.exe'
        with self.assertRaises(ValueError):updater.parse_release(data)
        data['tag_name']='v1.0.0'
        self.assertIsNone(updater.parse_release(data))

    def test_download_corruption_and_verified_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp); target=folder/'app.exe';target.write_bytes(b'MZold')
            personal=folder/'settings.json';personal.write_text('personal')
            with patch.object(updater.urllib.request,'urlopen',return_value=io.BytesIO(b'MZbad')):
                with self.assertRaises(ValueError):updater.download(self.release(),folder)
            self.assertEqual(target.read_bytes(),b'MZold')
            with patch.object(updater.urllib.request,'urlopen',return_value=io.BytesIO(b'MZnew')):
                new=updater.download(self.release(),folder)
            updater.replace_executable(target,new,self.release()['sha256'])
            self.assertEqual(target.read_bytes(),b'MZnew')
            self.assertEqual((folder/'app.previous.exe').read_bytes(),b'MZold')
            self.assertEqual(personal.read_text(),'personal')

    def test_failed_replace_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);target=folder/'app.exe';target.write_bytes(b'MZold')
            new=folder/'new.exe';new.write_bytes(b'MZnew')
            original=updater.os.replace
            def fail_staged(src,dst):
                if str(src).endswith('.update-new'):raise OSError('simulated replacement failure')
                return original(src,dst)
            with patch.object(updater.os,'replace',side_effect=fail_staged):
                with self.assertRaises(OSError):updater.replace_executable(target,new,updater.sha256(new))
            self.assertEqual(target.read_bytes(),b'MZold')

if __name__=='__main__':unittest.main()
