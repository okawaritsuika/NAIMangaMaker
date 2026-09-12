"""Encrypted per-key storage, quota lookup and independent role selection."""
import base64
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.request
import urllib.error
import uuid

class Blob(c.Structure):
    _fields_=[('size',w.DWORD),('data',c.POINTER(c.c_byte))]

def crypt(value, decrypt=False):
    raw=base64.b64decode(value) if decrypt else value.encode('utf-8')
    buf=c.create_string_buffer(raw); source=Blob(len(raw),c.cast(buf,c.POINTER(c.c_byte))); out=Blob()
    lib=c.WinDLL('crypt32',use_last_error=True); kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[c.c_void_p];kernel.LocalFree.restype=c.c_void_p
    if decrypt:ok=lib.CryptUnprotectData(c.byref(source),None,None,None,None,1,c.byref(out))
    else:ok=lib.CryptProtectData(c.byref(source),'NAIMangaMaker API key',None,None,None,1,c.byref(out))
    if not ok:raise OSError('이 Windows 사용자 계정에서 키를 암호화하거나 읽지 못했습니다.')
    try:
        result=c.string_at(out.data,out.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:kernel.LocalFree(out.data)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def number(value):
    return value if type(value) in (int,float) and value>=0 else None

def quota(token):
    req=urllib.request.Request('https://image.novelai.net/user/subscription',headers={'Authorization':'Bearer '+token,'User-Agent':'NAIMangaMaker/1.0','Accept':'application/json','Cache-Control':'no-cache'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as response:raw=json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403):raise ValueError('인증에 실패했습니다. NovelAI API 키를 확인해 주세요.') from None
        if exc.code==429:raise ValueError('요청이 많아 조회가 제한됐습니다. 잠시 후 다시 시도해 주세요.') from None
        raise ValueError('NovelAI 응답을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.') from None
    except (OSError,ValueError):raise ValueError('NovelAI 연결에 실패했습니다. 네트워크 상태를 확인해 주세요.') from None
    if not isinstance(raw,dict) or type(raw.get('active')) is not bool or type(raw.get('tier')) is not int:
        raise ValueError('계정 정보를 확인하지 못했습니다. 키는 저장하지 않았습니다.')
    usage=raw.get('usage') or {}; training=raw.get('trainingStepsLeft') or {}
    if not isinstance(usage,dict):usage={}
    if not isinstance(training,dict):training={}
    percent=number(usage.get('percent'))
    if usage.get('isNegative') is True:percent=0
    fixed=number(training.get('fixedTrainingStepsLeft'));paid=number(training.get('purchasedTrainingSteps'))
    return dict(active=raw['active'],tier=raw['tier'],v5_remaining_percent=percent,
        anlas_subscription=fixed,anlas_purchased=paid,anlas_total=fixed+paid if fixed is not None and paid is not None else None,
        checked_at=time.time(),story_remaining=None)

class KeyStore:
    def __init__(self,path=None,probe=quota,encrypt=crypt,decrypt=None):
        self.path=Path(path) if path else Path(os.environ['LOCALAPPDATA'])/'NAIMangaMaker'/'keys.json'
        self.probe=probe;self.encrypt=encrypt;self.decrypt=decrypt or (lambda v:crypt(v,True));self.lock=threading.RLock()
        self.data={'keys':[],'roles':{'image':None,'story':None}}
        self.image_turns={}
        if self.path.exists():
            self.data=json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.data.get('keys'),list) or not isinstance(self.data.get('roles'),dict):raise ValueError('키 저장 파일을 확인할 수 없습니다.')
        self.data.setdefault('image_policy',{'mode':'selected','pool':[]})
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.data,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(self.path)
    def public(self):
        with self.lock:
            return {'keys':[{k:row.get(k) for k in ('id','name','mask','quota','error')} for row in self.data['keys']], 'roles':dict(self.data['roles']), 'image_policy':dict(mode=self.data['image_policy']['mode'],pool=list(self.data['image_policy']['pool']))}
    def find(self,kid):
        return next((row for row in self.data['keys'] if row['id']==kid),None)
    def add(self,name,token):
        if name is None:name=''
        if not isinstance(name,str) or len(name.strip())>40:raise ValueError('키 이름은 40자 이내로 입력해 주세요.')
        if not isinstance(token,str):raise ValueError('API 키를 입력해 주세요.')
        token=token.strip()
        if token.lower().startswith('bearer '):token=token[7:].strip()
        if not 10<=len(token)<=4096 or any(ch.isspace() for ch in token):raise ValueError('API 키 형식을 확인해 주세요.')
        fingerprint=hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            if any(row['fingerprint']==fingerprint for row in self.data['keys']):raise ValueError('이미 등록된 키입니다.')
            if len(self.data['keys'])>=32:raise ValueError('키는 최대 32개까지 등록할 수 있습니다.')
        account=self.probe(token); encrypted=self.encrypt(token)
        with self.lock:
            if any(row['fingerprint']==fingerprint for row in self.data['keys']):raise ValueError('이미 등록된 키입니다.')
            if len(self.data['keys'])>=32:raise ValueError('키는 최대 32개까지 등록할 수 있습니다.')
            if not name.strip():
                index=1
                while any(row['name']==f'키 {index}' for row in self.data['keys']):index+=1
                name=f'키 {index}'
            row=dict(id=uuid.uuid4().hex,name=name.strip(),mask='•••• '+token[-4:],fingerprint=fingerprint,encrypted=encrypted,quota=account,error=None)
            self.data['keys'].append(row)
            if len(self.data['keys'])==1:self.data['roles']={'image':row['id'],'story':row['id']}
            self.save();return self.public()
    def select(self,roles,policy=None):
        if not isinstance(roles,dict) or set(roles)!={'image','story'}:raise ValueError('이미지·스토리 키를 선택해 주세요.')
        with self.lock:
            if any(v is not None and (not isinstance(v,str) or self.find(v) is None) for v in roles.values()):raise ValueError('등록된 키를 선택해 주세요.')
            if policy is not None:
                if not isinstance(policy,dict) or policy.get('mode') not in ('selected','balanced') or not isinstance(policy.get('pool'),list):raise ValueError('이미지 사용 방식을 확인해 주세요.')
                if any(not isinstance(k,str) or self.find(k) is None for k in policy['pool']):raise ValueError('균등 사용에 포함할 키를 확인해 주세요.')
                if policy['mode']=='balanced' and not policy['pool']:raise ValueError('균등 사용에 포함할 키를 하나 이상 선택해 주세요.')
                self.data['image_policy']={'mode':policy['mode'],'pool':list(dict.fromkeys(policy['pool']))}
            self.data['roles']=dict(roles);self.save();return self.public()
    def remove(self,kid):
        with self.lock:
            row=self.find(kid)
            if row is None:raise ValueError('이미 삭제된 키입니다.')
            self.data['keys'].remove(row)
            self.data['image_policy']['pool']=[k for k in self.data['image_policy']['pool'] if k!=kid]
            for role in self.data['roles']:
                if self.data['roles'][role]==kid:self.data['roles'][role]=None
            self.save();return self.public()
    def refresh(self,kid):
        with self.lock:
            row=self.find(kid)
            if row is None:raise ValueError('키를 찾을 수 없습니다.')
            encrypted=row['encrypted']
        try:account=self.probe(self.decrypt(encrypted));error=None
        except (ValueError,OSError) as exc:account=None;error=str(exc)
        with self.lock:
            row=self.find(kid)
            if row:
                if account is not None:row['quota']=account
                row['error']=error;self.save()
            return self.public()
    def credential_for(self,role):
        """Capture credentials per request; never swap a global shared API key."""
        if role=='image':
            with self.lock:
                policy=self.data['image_policy'];pool=list(policy['pool']) if policy['mode']=='balanced' else None
            if pool is not None:
                # Refresh outside storage lock; story selection remains independent.
                for kid in pool:
                    try:self.refresh(kid)
                    except ValueError:pass
                with self.lock:
                    candidates=[r for r in self.data['keys'] if r['id'] in self.data['image_policy']['pool'] and not r.get('error') and r.get('quota',{}).get('active') and number(r.get('quota',{}).get('v5_remaining_percent')) is not None and r['quota']['v5_remaining_percent']>0]
                    if self.data['image_policy']['mode']=='balanced':
                        if not candidates:raise ValueError('균등 사용 가능한 이미지 할당량이 없습니다. 잔량이나 사용할 키를 확인해 주세요.')
                        row=max(candidates,key=lambda r:(r['quota']['v5_remaining_percent'],-self.image_turns.get(r['id'],0)))
                        self.image_turns[row['id']]=self.image_turns.get(row['id'],0)+1
                        return row['id'],self.decrypt(row['encrypted'])
        with self.lock:
            if role not in ('image','story'):raise ValueError('알 수 없는 생성 용도입니다.')
            row=self.find(self.data['roles'].get(role))
            if row is None:raise ValueError('설정에서 사용할 키를 선택해 주세요.')
            return row['id'],self.decrypt(row['encrypted'])
