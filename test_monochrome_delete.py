import copy,io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from engine import image_color,image_preferences,image_studio
from engine.iterative_comic_render import build_settings,render_page
from engine.forced_infill_delivery import payload
from engine.iterative_comic import save
from engine_bridge import RemoteApp
from test_image_inpaint import fixture

class MonochromeAndDeletionTests(unittest.TestCase):
 def test_defaults_snapshot_and_all_prompt_layers(self):
  with tempfile.TemporaryDirectory() as directory:
   wb,pid,gid,data=fixture(directory)
   image_preferences.store(wb,{'settings':{'color_mode':'monochrome'}})
   project=wb.page(pid,{'panel_ids':[wb.load(pid)['panels'][0]['id']],'layout':'auto'})
   self.assertEqual(project['pages'][-1]['image_settings']['color_mode'],'monochrome')
   page=project['pages'][-1];settings,_=build_settings(project,page)
   self.assertTrue(settings['prompt'].startswith(image_color.POSITIVE))
   self.assertTrue(all(row['char_caption'].startswith(image_color.POSITIVE) for row in settings['v4_prompt']['caption']['char_captions']))
   self.assertNotIn('color_mode',payload(settings)['parameters'])
   self.assertEqual(image_studio.form(settings,build_settings(project,page)[1])['color_mode'],'monochrome')
   with self.assertRaises(ValueError):image_preferences.validate({'color_mode':'invalid'})

 def test_render_preserves_original_and_recovers_without_network(self):
  with tempfile.TemporaryDirectory() as directory:
   wb,pid,gid,data=fixture(directory)
   project=wb.load(pid);page=project['pages'][0];page['image_settings']['color_mode']='monochrome'
   raw=io.BytesIO();Image.new('RGBA',(256,256),(10,80,220,173)).save(raw,format='PNG');original=raw.getvalue()
   archive=io.BytesIO()
   with zipfile.ZipFile(archive,'w') as z:z.writestr('image.png',original)
   folder=Path(directory)/'candidate'
   # The fixture PNG has no NAI metadata; only the metadata verifier is replaced.
   with patch('engine.single_page_five.verify_rendered',return_value={'settings_verified':True}),patch('engine.iterative_comic_render.ensure_allowance',return_value={}),patch('engine.iterative_comic_render.urllib.request.urlopen',return_value=io.BytesIO(archive.getvalue())) as call:
    result=render_page(project,page,folder,'fixture-key')
    self.assertFalse(result['original_api_png']);self.assertEqual((folder/'api.png').read_bytes(),original)
    with Image.open(folder/'page.png') as out:
     r,g,b,a=out.split();self.assertEqual(r.tobytes(),g.tobytes());self.assertEqual(g.tobytes(),b.tobytes());self.assertEqual(a.getextrema(),(173,173))
    (folder/'page.png').unlink()
    recovered=render_page(project,page,folder,'')
    self.assertTrue(recovered['reused']);self.assertEqual(call.call_count,1)
    (folder/'page.png').write_bytes(original)
    with self.assertRaises(ValueError):render_page(project,page,folder,'')

 def test_delete_confirmation_busy_guards_and_only_selected_project(self):
  with tempfile.TemporaryDirectory() as directory:
   wb,pid,gid,data=fixture(directory);neighbor=wb.create({'seed':'A gardener reads a book','count':1})['id'];app=RemoteApp(wb)
   try:
    for value in ({},{'confirmed':False},{'confirmed':1}):
     with self.assertRaises(ValueError):app.delete_project(pid,value)
    with self.assertRaises(ValueError):app.delete_project('../outside',{'confirmed':True})
    app.active.add(pid)
    with self.assertRaises(ValueError):app.delete_project(pid,{'confirmed':True})
    app.active.clear();app.auto.runs['a123456789abc']={'id':'a123456789abc','project_id':pid,'status':'running'}
    with self.assertRaises(ValueError):app.delete_project(pid,{'confirmed':True})
    app.auto.runs['a123456789abc']['status']='paused'
    app.starts.runs['s123456789abc']={'project_id':pid,'status':'paused'}
    app.jobs['j123456789abc']={'project_id':pid,'status':'complete'}
    for entries,sub in [(app.jobs,'_jobs'),(app.auto.runs,'_auto'),(app.starts.runs,'_starts')]:
     for rid,row in entries.items():save(Path(directory)/sub/(rid+'.json'),row)
    save(Path(directory)/'_settings'/'keep.json',{'keep':True})
    result=app.delete_project(pid,{'confirmed':True})
    self.assertTrue(result['deleted']);self.assertFalse(wb.folder(pid).exists());self.assertTrue(wb.folder(neighbor).exists())
    self.assertFalse(app.jobs);self.assertFalse(app.auto.runs);self.assertFalse(app.starts.runs)
    self.assertTrue((Path(directory)/'_settings/keep.json').exists())
    self.assertFalse(list((Path(directory)/'_jobs').glob('*.json')))
   finally:app.pool.shutdown(wait=True)

if __name__=='__main__':unittest.main()
