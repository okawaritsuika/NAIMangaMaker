import unittest,tempfile,threading,json,urllib.request,urllib.error
from pathlib import Path
from server import Server
from key_store import KeyStore
class WorkspaceTests(unittest.TestCase):
 def test_routes_draft_and_missing_key(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=KeyStore(Path(tmp)/'keys.json');server=Server(('127.0.0.1',0),store,tmp);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
   def req(path,data=None):
    with urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'X-NAIMangaMaker':'1'})) as response:return json.load(response)
   try:
    self.assertEqual(req('/api/workspace')['projects'],[])
    draft={'storySeed':'A library mystery','storyCount':'7','storyAuto':False,'characters':[]}
    self.assertTrue(req('/api/workspace/draft',draft)['saved']);self.assertEqual(req('/api/workspace')['draft'],draft)
    large_draft=dict(draft,storySeed='긴 이야기 설정. '*16000)
    self.assertTrue(req('/api/workspace/draft',large_draft)['saved']);self.assertEqual(req('/api/workspace')['draft'],large_draft)
    with self.assertRaises(urllib.error.HTTPError) as error:req('/api/story/start',{'seed':'A library mystery','count':7})
    self.assertEqual(error.exception.code,400);self.assertEqual(req('/api/workspace')['starts'],[])
    with self.assertRaises(urllib.error.HTTPError) as error:req('/files/../../keys.json')
    self.assertEqual(error.exception.code,404)
   finally:
    server.shutdown();server.server_close();thread.join()
    if server.story_app:server.story_app.pool.shutdown()
if __name__=='__main__':unittest.main()
