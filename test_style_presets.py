import tempfile,unittest
from pathlib import Path
from engine import image_preferences as prefs
from test_story_fixture import DemoWorkbench

class StylePresetsTests(unittest.TestCase):
 def test_multiple_styles_update_load_and_delete_survive_reload(self):
  with tempfile.TemporaryDirectory() as folder:
   wb=DemoWorkbench(Path(folder));before=prefs.load(wb)
   a={'style_prompt':'pencil sketch','negative_prompt':'color','color_mode':'monochrome'}
   b={'style_prompt':'watercolor','negative_prompt':'blur','color_mode':'prompt'}
   first=prefs.save_style(wb,{'name':'연필','settings':a})['saved_id']
   second=prefs.save_style(wb,{'name':'수채화','settings':b})['saved_id']
   reloaded=DemoWorkbench(Path(folder));self.assertEqual(len(prefs.styles(reloaded)),2)
   self.assertEqual(prefs.load(reloaded),before)
   prefs.save_style(reloaded,{'id':first,'name':'연필 수정','settings':dict(a,style_prompt='graphite')})
   self.assertEqual(prefs.styles(wb)[0]['id'],first)
   self.assertEqual(prefs.styles(wb)[0]['settings']['style_prompt'],'graphite')
   prefs.store(wb,{'settings':prefs.styles(wb)[0]['settings']})
   self.assertEqual(prefs.load(wb)['width'],before['width'])
   prefs.delete_style(wb,first)
   self.assertEqual([r['id'] for r in prefs.styles(reloaded)],[second])
   self.assertEqual(prefs.load(wb)['style_prompt'],'graphite')

 def test_invalid_or_duplicate_save_preserves_existing(self):
  with tempfile.TemporaryDirectory() as folder:
   wb=DemoWorkbench(Path(folder));data={'style_prompt':'ink','negative_prompt':'','color_mode':'prompt'}
   prefs.save_style(wb,{'name':'Style','settings':data})
   before=prefs.styles(wb)
   for value in [{'name':' STYLE ','settings':data},{'name':'','settings':data},{'id':'../bad','name':'new','settings':data},{'name':'new','settings':dict(data,width=832)},{'name':'new','settings':dict(data,color_mode='bad')}]:
    with self.assertRaises(ValueError):prefs.save_style(wb,value)
    self.assertEqual(prefs.styles(wb),before)
   with self.assertRaises(ValueError):prefs.delete_style(wb,'st000000000000')

if __name__=='__main__':unittest.main()
