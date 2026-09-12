"""Local workspace server. API responses never contain credentials."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import sys
import threading
import os
import re
from urllib.parse import urlsplit, parse_qs
from tag_catalog import search as search_tags
from key_store import KeyStore
import character_library
ROOT = Path(getattr(sys, '_MEIPASS', str(Path(__file__).parent)))
class Server(ThreadingHTTPServer):
    def __init__(self, address, store=None, data_dir=None):
        super().__init__(address, Handler)
        self.store = store
        self.store_lock = threading.Lock()
        self.data_dir = Path(data_dir) if data_dir else Path(os.environ['LOCALAPPDATA'])/'NAIMangaMaker'
        self.story_app = None
        self.story_lock = threading.RLock()
    def keys(self):
        with self.store_lock:
            if self.store is None: self.store = KeyStore()
            return self.store
    def stories(self):
        with self.story_lock:
            if self.story_app is None:
                from engine_bridge import make_app
                self.story_app = make_app(self.keys(), self.data_dir/'stories')
            return self.story_app
    def workspace(self):
        app=self.stories()
        path=self.data_dir/'workspace.json'
        with self.story_lock:
            draft=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            return dict(draft=draft, projects=app.workbench.list_projects(), starts=app.starts.list(), auto=app.auto.list())
class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value, mime='application/json; charset=utf-8'):
        payload = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers(); self.wfile.write(payload)
    def allowed(self, mutation=False):
        hosts = [f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}']
        if self.headers.get('Host') not in hosts: return False
        origin = self.headers.get('Origin')
        if origin and origin not in ['http://' + h for h in hosts]: return False
        return not mutation or self.headers.get('X-NAIMangaMaker') == '1'
    def do_GET(self):
        if not self.allowed(): return self.reply(403, {'error':'접근할 수 없습니다.'})
        view={'/library':'library.html','/prompts':'prompts.html','/reader':'reader.html','/image-editor':'image-editor.html','/image_cost_ui.js':'image_cost_ui.js'}.get(urlsplit(self.path).path)
        if view:return self.reply(200,(ROOT/'web'/view).read_bytes(),'text/javascript; charset=utf-8' if view.endswith('.js') else 'text/html; charset=utf-8')
        if self.path == '/api/prompts/choices':
            from engine import prompt_management
            return self.reply(200,prompt_management.expansion_choices(self.server.stories().workbench))
        if self.path in ('/api/library','/api/prompts'):
            try:
                from engine import comic_library,prompt_management
                app=self.server.stories()
                with app.lock:return self.reply(200,comic_library.list_library(app.workbench) if self.path=='/api/library' else prompt_management.catalog(app.workbench))
            except Exception:return self.reply(500,{'error':'저장된 정보를 읽지 못했습니다. 서버 로그를 확인해 주세요.'})
        if self.path == '/image-settings': return self.reply(200, (ROOT/'web/image-settings.html').read_bytes(), 'text/html; charset=utf-8')
        if self.path == '/image-settings.js': return self.reply(200, (ROOT/'web/image-settings.js').read_bytes(), 'text/javascript; charset=utf-8')
        if self.path == '/api/image-settings':
            from engine import image_preferences
            try:
                app=self.server.stories()
                with app.lock:return self.reply(200,image_preferences.public(app.workbench))
            except Exception:return self.reply(500,{'error':'이미지 설정을 읽지 못했습니다.'})
        if self.path == '/characters': return self.reply(200, (ROOT/'web/characters.html').read_bytes(), 'text/html; charset=utf-8')
        if self.path == '/character-library.js': return self.reply(200, (ROOT/'web/character-library.js').read_bytes(), 'text/javascript; charset=utf-8')
        if urlsplit(self.path).path == '/api/characters':
            try:
                app=self.server.stories()
                with app.lock:return self.reply(200,character_library.public(app.workbench,parse_qs(urlsplit(self.path).query).get('q',[''])[0]))
            except ValueError as exc:return self.reply(400,{'error':str(exc)})
        if self.path == '/health': return self.reply(200, {'app':'NAIMangaMaker','ready':True})
        if urlsplit(self.path).path == '/': return self.reply(200, (ROOT/'web/index.html').read_bytes(), 'text/html; charset=utf-8')
        if self.path == '/workspace.js': return self.reply(200, (ROOT/'web/workspace.js').read_bytes(), 'text/javascript; charset=utf-8')
        if urlsplit(self.path).path == '/api/tags':
            query=parse_qs(urlsplit(self.path).query).get('q',[''])[0]
            return self.reply(200, {'items':search_tags(query[:81])})
        if self.path == '/history.js': return self.reply(200, (ROOT/'web/history.js').read_bytes(), 'text/javascript; charset=utf-8')
        if self.path == '/middle.js': return self.reply(200, (ROOT/'web/middle.js').read_bytes(), 'text/javascript; charset=utf-8')
        if self.path == '/inline-reader.js': return self.reply(200, (ROOT/'web/inline-reader.js').read_bytes(), 'text/javascript; charset=utf-8')
        if self.path == '/tags.js': return self.reply(200, (ROOT/'web/tags.js').read_bytes(), 'text/javascript; charset=utf-8')
        if self.path.startswith(('/api/workspace','/api/story/','/files/')):
            try:
                if self.path == '/api/workspace': return self.reply(200,self.server.workspace())
                app=self.server.stories()
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})/pages/(g[0-9a-f]{12})/(prompt|studio)',self.path)
                if match:
                    with app.lock:
                        if match[3]=='prompt':return self.reply(200,app.workbench.preview_prompt(match[1],match[2]))
                        from engine import image_studio
                        return self.reply(200,image_studio.get(app.workbench,match[1],match[2]))
                if self.path=='/api/story/jobs':return self.reply(200,{'jobs':app.list_jobs()})
                match=re.fullmatch(r'/api/story/jobs/(j[0-9a-f]{12})',self.path)
                if match:return self.reply(200,app.public_job(app.get_job(match[1])))
                match=re.fullmatch(r'/api/story/(starts|auto)/([as][0-9a-f]{12})',self.path)
                if match:
                    manager=getattr(app,match[1]);return self.reply(200,manager.public(manager.get(match[2])))
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})',self.path)
                if match:return self.reply(200,app.workbench.public(app.workbench.load(match[1])))
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})/export',self.path)
                if match:
                    with app.lock:return self.reply(200,app.workbench.export(match[1]).read_bytes(),'application/zip')
                match=re.fullmatch(r'/api/story/jobs/(j[0-9a-f]{12})/response',self.path)
                if match:return self.reply(200,app.response(match[1]))
                if self.path.startswith('/files/'):
                    base=app.workbench.directory.resolve();file=(base/self.path[7:]).resolve()
                    if file.is_relative_to(base) and file.suffix.lower()=='.png' and file.is_file():return self.reply(200,file.read_bytes(),'image/png')
                return self.reply(404,{'error':'저장된 작업을 찾을 수 없습니다.'})
            except (ValueError,FileNotFoundError,KeyError):return self.reply(400,{'error':'저장된 작업 정보를 확인하지 못했습니다.'})
            except Exception:return self.reply(500,{'error':'작업실을 준비하지 못했습니다. 서버 로그를 확인해 주세요.'})
        if self.path == '/api/keys':
            try: return self.reply(200, self.server.keys().public())
            except Exception: return self.reply(500, {'error':'저장된 키 정보를 읽지 못했습니다. 저장 파일은 보존했습니다.'})
        self.reply(404, {'error':'없는 페이지입니다.'})
    def do_POST(self): self.mutate()
    def do_DELETE(self): self.mutate()
    def mutate(self):
        if not self.allowed(True): return self.reply(403, {'error':'접근할 수 없습니다.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= (100000 if self.path.startswith(('/api/story/','/api/workspace','/api/image-settings','/api/characters','/api/prompts/')) else 16384): raise ValueError('요청이 너무 큽니다.')
            data = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(data, dict): raise ValueError('요청 형식을 확인해 주세요.')
            if self.path in ('/api/prompts/save','/api/prompts/reset','/api/prompts/preview','/api/prompts/preset','/api/prompts/assign') and self.command=='POST':
                from engine import prompt_management
                operation={'/api/prompts/save':prompt_management.save_profile,'/api/prompts/reset':prompt_management.reset_profile,'/api/prompts/preview':prompt_management.preview,'/api/prompts/preset':prompt_management.save_preset,'/api/prompts/assign':prompt_management.assign_preset}[self.path]
                app=self.server.stories()
                with app.lock:return self.reply(200,operation(app.workbench,data))
            if self.path=='/api/characters' and self.command=='POST':
                app=self.server.stories()
                with app.lock:return self.reply(200,character_library.store(app.workbench,data))
            match=re.fullmatch(r'/api/characters/(ch[0-9a-f]{12})/archive',self.path)
            if match and self.command=='POST':
                app=self.server.stories()
                with app.lock:return self.reply(200,character_library.archive(app.workbench,match[1]))
            if self.path=='/api/image-settings' and self.command=='POST':
                from engine import image_preferences
                app=self.server.stories()
                with app.lock:return self.reply(200,image_preferences.store(app.workbench,data))
            if self.path=='/api/workspace/draft' and self.command=='POST':
                with self.server.story_lock:
                    path=self.server.data_dir/'workspace.json';path.parent.mkdir(parents=True,exist_ok=True)
                    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');tmp.replace(path)
                return self.reply(200,{'saved':True})
            if self.path.startswith('/api/story/') and self.command=='POST':
                app=self.server.stories()
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})/panels/([a-z][0-9a-f]{12})/(edit|reroll|restore)',self.path)
                if match:
                    pid,panel_id,operation=match.groups()
                    if operation=='restore':
                        return self.reply(200,app.workbench.public(app.mutation(pid,'restore_panel',panel_id,data)))
                    self.server.keys().credential_for('story')
                    return self.reply(200,app.job('edit_panel' if operation=='edit' else 'reroll_panel',data,pid,panel_id))
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})/compose',self.path)
                if match:return self.reply(200,app.job('compose',data,match[1]))
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})/settings',self.path)
                if match:
                    self.server.keys().credential_for('story')
                    return self.reply(200,app.job('settings',data,match[1]))
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})/expand',self.path)
                if match:
                    self.server.keys().credential_for('story')
                    from engine import prompt_management
                    return self.reply(200,app.job('expand',prompt_management.capture_expansion_profile(app.workbench,data),match[1]))
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})/pages/(g[0-9a-f]{12})/(edit|delete|restore|version|prompt)',self.path)
                if match:
                    methods=dict(edit='edit_page',delete='delete_page',restore='restore_page',version='select_render',prompt='save_prompt')
                    return self.reply(200,app.workbench.public(app.mutation(match[1],methods[match[3]],match[2],data)))
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})/pages/(g[0-9a-f]{12})/studio/(render|apply|defaults)',self.path)
                if match:
                    from engine import image_studio
                    pid,gid,operation=match.groups()
                    if operation=='render':
                        self.server.keys().credential_for('image')
                        return self.reply(200,app.job('studio_render',data,pid,gid))
                    with app.lock:
                        if operation=='defaults':return self.reply(200,image_studio.with_defaults(app.workbench,pid,gid,data))
                        app.auto.guard(pid)
                        if pid in app.active:raise ValueError('진행 중인 생성이 끝난 뒤 결과를 적용해 주세요.')
                        image_studio.apply(app.workbench,pid,gid,data)
                        return self.reply(200,image_studio.get(app.workbench,pid,gid))
                match=re.fullmatch(r'/api/story/projects/(p[0-9a-f]{12})/pages/(g[0-9a-f]{12})/(render|cost|image-cost)',self.path)
                if match:
                    image_key=self.server.keys().credential_for('image')[1]
                    if match[3] in ('cost','image-cost'):
                        from engine.image_cost import assess, subscription
                        with app.lock:settings=app.workbench.preview_prompt(match[1],match[2])
                        from engine.image_preferences import validate
                        override=data.get('image_settings',{})
                        validate(override)
                        settings.update({k:override[k] for k in ('width','height','steps') if k in override})
                        return self.reply(200,assess(settings,subscription(image_key)))
                    return self.reply(200,app.job('render',data,match[1],match[2]))
                match=re.fullmatch(r'/api/story/jobs/(j[0-9a-f]{12})/retry',self.path)
                if match:
                    job=app.get_job(match[1])
                    if job['kind'] not in ('settings','expand','edit_panel','reroll_panel','compose','render','studio_render'):raise ValueError('해당 작업 화면에서 재개해 주세요.')
                    if job['kind']!='compose':self.server.keys().credential_for('image' if job['kind'] in ('render','studio_render') else 'story')
                    if type(data.get('restart',False)) is not bool:raise ValueError('재시도 방식을 확인해 주세요.')
                    return self.reply(200,app.retry(match[1],restart=data.get('restart',False)))
                if self.path=='/api/story/start':
                    self.server.keys().credential_for('story')
                    data['generation_options']={**data.get('generation_options',{}),'concise_prompts':True}
                    return self.reply(200,app.starts.start(data))
                if self.path=='/api/story/auto':
                    self.server.keys().credential_for('story')
                    data.pop('project_id',None)
                    data['generation_options']={**data.get('generation_options',{}),'concise_prompts':True}
                    data.setdefault('brief',{})['generation_options']=data['generation_options']
                    return self.reply(200,app.auto.start(data))
                match=re.fullmatch(r'/api/story/(starts|auto)/([as][0-9a-f]{12})/(pause|resume)',self.path)
                if match:return self.reply(200,getattr(getattr(app,match[1]),match[3])(match[2]))
                return self.reply(404,{'error':'없는 작업입니다.'})
            store = self.server.keys()
            if self.command == 'POST' and self.path == '/api/keys': result = store.add(data.get('name'), data.get('token'))
            elif self.command == 'POST' and self.path == '/api/keys/roles': result = store.select({k:v for k,v in data.items() if k!='image_policy'},data.get('image_policy'))
            elif self.path.startswith('/api/keys/'):
                parts = self.path.split('/')
                if self.command == 'DELETE' and len(parts) == 4: result = store.remove(parts[3])
                elif self.command == 'POST' and len(parts) == 5 and parts[4] == 'refresh': result = store.refresh(parts[3])
                else: return self.reply(404, {'error':'없는 기능입니다.'})
            else: return self.reply(404, {'error':'없는 기능입니다.'})
            self.reply(200, result)
        except ValueError as exc: self.reply(400, {'error':str(exc) if not isinstance(exc, json.JSONDecodeError) else '요청 형식을 확인해 주세요.'})
        except Exception: self.reply(500, {'error':'키 정보를 처리하지 못했습니다. 다시 시도해 주세요.'})
def serve(port=8795):
    with Server(('127.0.0.1', port)) as server: server.serve_forever()
