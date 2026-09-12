import base64
import copy
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
from engine import image_inpaint, image_studio
from engine.forced_infill_delivery import sha, save
from engine.iterative_comic_render import build_settings
from engine.iterative_comic_server import App
from test_story_fixture import DemoWorkbench


def fixture(directory):
    wb=DemoWorkbench(Path(directory))
    project=wb.create(dict(seed='A gardener plants flowers',count=1))
    pid=project['id']
    project=wb.page(pid,dict(panel_ids=[project['panels'][0]['id']],layout='auto'))
    page=project['pages'][0];gid=page['id']
    page.update(width=256,height=256,image_settings=dict(width=256,height=256))
    settings,audit=build_settings(project,page)
    folder=wb.folder(pid)/'renders'/gid/'r123456789abc';folder.mkdir(parents=True)
    Image.new('RGBA',(256,256),(20,50,80,255)).save(folder/'page.png')
    save(folder/'settings.json',settings);save(folder/'audit.json',audit)
    relative=folder.relative_to(wb.folder(pid)).as_posix()
    row=dict(id=folder.name,folder=relative,status='rendered',verified=True,original_api_png=True,
        request_page=copy.deepcopy(audit['source']['page']),settings_path=relative+'/settings.json',audit_path=relative+'/audit.json',
        image_sha256=sha(folder/'page.png'),image_url=f'/files/{pid}/{relative}/page.png')
    page.update(renders=[row],selected_render_index=0,image_url=row['image_url'],status='rendered')
    wb.commit(project)
    form=image_studio.get(wb,pid,gid)['prompt']
    data=dict(source_sha256=image_studio.source_hash(project,page),seed=42,
        prompt_overrides=dict(prompt='A red flower in a blue vase',negative_prompt='',characters=[]),
        image_settings={k:form[k] for k in ('width','height','steps','scale','cfg_rescale','noise_schedule','sampler')})
    mask=Image.new('L',(256,256));ImageDraw.Draw(mask).rectangle((80,80,150,160),fill=255)
    data['inpaint']=dict(render_id=row['id'],image_sha256=row['image_sha256'],strength=.8,
        mask=base64.b64encode(image_inpaint.png(mask)).decode())
    return wb,pid,gid,data


class InpaintTests(unittest.TestCase):
    def test_mask_validation_rejects_bad_or_changed_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            wb,pid,gid,data=fixture(directory)
            page=wb.load(pid)['pages'][0]
            for update in (dict(strength=float('nan')),dict(image_sha256='wrong'),dict(render_id='../other'),
                           dict(mask='bad!'),dict(mask=base64.b64encode(image_inpaint.png(Image.new('L',(64,64)))).decode()),
                           dict(mask=base64.b64encode(image_inpaint.png(Image.new('L',(256,256)))).decode())):
                with self.subTest(update=list(update)),self.assertRaises(ValueError):
                    image_inpaint.inputs(wb,pid,page,dict(data['inpaint'],**update))

    def test_request_candidate_apply_and_unmasked_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            wb,pid,gid,data=fixture(directory)
            original=copy.deepcopy(wb.load(pid)['pages'][0])
            def response(request,timeout):
                body=json.loads(request.data)
                self.assertEqual(body['action'],'infill')
                self.assertEqual(body['model'],'nai-diffusion-5-full-inpainting')
                self.assertEqual(body['parameters']['strength'],.8)
                self.assertEqual(body['parameters']['seed'],42)
                self.assertEqual(body['parameters']['noise_schedule'],'karras')
                self.assertEqual(body['parameters']['v4_prompt']['caption']['char_captions'],[])
                with Image.open(io.BytesIO(base64.b64decode(body['parameters']['mask']))) as mask:
                    self.assertEqual(mask.getpixel((0,0)),0);self.assertEqual(mask.getpixel((100,100)),255)
                raw=io.BytesIO()
                with zipfile.ZipFile(raw,'w') as archive: archive.writestr('image.png',image_inpaint.png(Image.new('RGB',(256,256),'red')))
                return io.BytesIO(raw.getvalue())
            wb.image_key_provider=lambda:'fixture-key'
            with patch('engine.image_cost.ensure_allowance',return_value={'fixture':True}),patch('engine.image_inpaint.urllib.request.urlopen',side_effect=response) as call:
                result=image_studio.render(wb,pid,gid,data)
            self.assertEqual(call.call_count,1)
            page=result['pages'][0];row=page['renders'][-1]
            self.assertEqual(page['image_url'],original['image_url'])
            self.assertEqual(len(page['renders']),2)
            with Image.open(wb.folder(pid)/row['folder']/'page.png') as image:
                self.assertEqual(image.getpixel((0,0)),(20,50,80,255))
                self.assertEqual(image.getpixel((100,100)),(255,0,0,255))
            self.assertFalse(row['original_api_png']);self.assertTrue(row['composited'])
            result=wb.select_render(pid,gid,dict(index=1))
            self.assertEqual(result['pages'][0]['image_url'],row['image_url'])
            self.assertFalse(result['pages'][0]['stale'])
            self.assertEqual(len(result['pages'][0]['renders']),2)
            self.assertTrue((wb.folder(pid)/row['folder']/'api.png').exists())

    def test_uncertain_request_does_not_repeat_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            wb,pid,gid,data=fixture(directory)
            wb.image_key_provider=lambda:'fixture-key'
            class Session:
                def artifact_folder(self,*args,**kwargs):return wb.folder(pid)/'renders'/gid/'o123456789abc'
                def stage_started(self,*args):pass
                def stage_completed(self,*args):pass
            with patch('engine.iterative_recovery.current_session',return_value=Session()),patch('engine.image_cost.ensure_allowance',return_value={}),patch('engine.image_inpaint.urllib.request.urlopen',side_effect=TimeoutError) as call:
                with self.assertRaises(TimeoutError):image_studio.render(wb,pid,gid,data)
                with self.assertRaises(RuntimeError):image_studio.render(wb,pid,gid,data)
                self.assertEqual(call.call_count,1)
            self.assertEqual(len(wb.load(pid)['pages'][0]['renders']),2)

    def test_background_job_commits_candidate_without_replacing_page(self):
        with tempfile.TemporaryDirectory() as directory:
            wb,pid,gid,data=fixture(directory)
            original=wb.load(pid)['pages'][0]['image_url']
            wb.image_key_provider=lambda:'fixture-key'
            app=App(wb)
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as archive:archive.writestr('image.png',image_inpaint.png(Image.new('RGB',(256,256),'red')))
            try:
                with patch('engine.image_cost.ensure_allowance',return_value={}),patch('engine.image_inpaint.urllib.request.urlopen',return_value=io.BytesIO(raw.getvalue())) as call:
                    jid=app.job('studio_render',data,pid,gid)['job_id']
                    deadline=time.monotonic()+10
                    while app.get_job(jid)['status'] in ('queued','running') and time.monotonic()<deadline:time.sleep(.02)
                    self.assertEqual(app.get_job(jid)['status'],'complete',app.get_job(jid).get('error'))
                    self.assertEqual(call.call_count,1)
                page=app.workbench.load(pid)['pages'][0]
                self.assertEqual(page['image_url'],original)
                self.assertEqual(page['renders'][-1]['status'],'rendered')
            finally:app.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
