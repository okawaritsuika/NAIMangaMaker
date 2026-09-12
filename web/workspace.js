(() => {
 const by=id=>document.getElementById(id), form=by('storyForm');let active=null,timer=null,draftTimer=null,draftQueue=Promise.resolve(),ready=false,loadedRevision='',projectId='';
 const fieldIds=['storySeed','storyDirection','storyPlace','storyAction','storyState','storyCount','storyDialogue','storyTranslation','storyPages','storyMin','storyMax'];
 const textNode=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n};
 function actors(host='storyActors'){return [...by(host).children].map((row,i)=>({id:row.dataset.actorId||'actor'+(i+1),gender:row.querySelector('[data-field=gender]').value,name:row.querySelector('[data-field=name]').value.trim(),personality:row.querySelector('[data-field=personality]').value.trim(),appearance:row.querySelector('[data-field=appearance]').value.trim()})).filter(r=>r.name||r.personality||r.appearance||r.gender!=='auto')}
 function actor(value={},host='storyActors'){
  if(by(host).children.length>=4)return;
  const box=textNode('details','actor-fields actor-fold'),head=textNode('summary','actor-head'),title=textNode('strong','actor-summary');
  box.open=!Object.keys(value).length;box.dataset.actorId=host==='storyActors'?'':value.id||'';head.append(title);box.append(head);
  const body=textNode('div','actor-body'),remove=textNode('button','','삭제');remove.type='button';
  remove.onclick=()=>{box.characterLibrary?.destroy();box.remove();by('addActor').disabled=false;updateActorCount(host);scheduleDraft()};
  const tools=textNode('div','actor-tools');if(host==='storyActors')tools.append(remove);body.append(tools);
  const gl=textNode('label','','성별'),gs=document.createElement('select');gs.dataset.field='gender';
  for(const [v,t] of [['auto','자동'],['girl','여성 · girl'],['boy','남성 · boy']])gs.add(new Option(t,v));
  gs.value=value.gender||'auto';gl.append(gs);body.append(gl);
  for(const [key,label] of [['name','이름'],['personality','성격'],['appearance','외형']]){
   const l=textNode('label','',label),input=document.createElement(key==='name'?'input':'textarea');input.dataset.field=key;input.value=value[key]||'';input.maxLength=key==='name'?200:5000;if(key!=='name')input.rows=2;l.append(input);body.append(l);
  }
  box.append(body);by(host).append(box);
  const updateTitle=()=>{const name=box.querySelector('[data-field=name]').value.trim(),appearance=box.querySelector('[data-field=appearance]').value.trim().replace(/\s+/g,' '),gender=gs.value==='auto'?'인물':gs.value;title.textContent=name||gender+(appearance?' · '+appearance.slice(0,48)+(appearance.length>48?'…':''):'');};
  box.addEventListener('input',updateTitle);box.addEventListener('change',updateTitle);updateTitle();
  box.characterLibrary=window.CharacterLibrary.attach({genderInput:gs,nameInput:box.querySelector('[data-field=name]'),personalityInput:box.querySelector('[data-field=personality]'),appearanceInput:box.querySelector('[data-field=appearance]')});
  if(host==='storyActors')by('addActor').disabled=by(host).children.length>=4;updateActorCount(host);
 }
 function updateActorCount(host='storyActors'){by(host==='storyActors'?'actorCount':'currentActorCount').textContent=by(host).children.length ? by(host).children.length+'명' : '선택';}
 let currentSettingsProject=null;
 const currentDraftKey=pid=>'naimanga-current-settings-'+pid;
 function currentSettingsValue(){return {direction:by('currentDirection').value,place:by('currentPlace').value,characters:actors('currentActors'),generation_options:{...currentSettingsProject.generation_options,translation_mode:by('currentTranslation').value}};}
 window.updateCurrentStorySettings=p=>{
  const same=currentSettingsProject?.id===p.id,opened=same?[...by('currentActors').children].map(n=>n.open):[];
  currentSettingsProject=p;by('currentStorySettings').hidden=false;by('currentStoryTitle').textContent=p.title;by('currentSeed').textContent=p.seed_ko||'—';
  let saved=null;try{saved=JSON.parse(localStorage.getItem(currentDraftKey(p.id))||'null')}catch{}
  const value=saved||{direction:p.direction_ko,place:p.place_ko,characters:p.characters_ko,generation_options:p.generation_options};
  by('currentDirection').value=value.direction||'';by('currentPlace').value=value.place||'';by('currentTranslation').value=value.generation_options?.translation_mode||'full';
  for(const row of by('currentActors').children)row.characterLibrary?.destroy();by('currentActors').replaceChildren();
  for(const [i,row] of (value.characters||[]).entries()){actor(row,'currentActors');by('currentActors').lastChild.open=opened[i]||false;}
  updateActorCount('currentActors');by('currentSettingsStatus').textContent=saved?'수정 중 · 저장하면 이후 생성에 반영됩니다.':'저장된 설정 · 이후 생성에 사용됩니다.';
 };
 const currentForm=by('currentSettingsForm');
 function saveCurrentDraft(){if(!currentSettingsProject)return;try{localStorage.setItem(currentDraftKey(currentSettingsProject.id),JSON.stringify(currentSettingsValue()));by('currentSettingsStatus').textContent='수정 중 · 저장하면 이후 생성에 반영됩니다.'}catch{by('currentSettingsStatus').textContent='초안을 보관하지 못했어요. 설정 저장을 눌러 주세요.'}}
 currentForm.addEventListener('input',saveCurrentDraft);currentForm.addEventListener('change',saveCurrentDraft);
 currentForm.onsubmit=async event=>{event.preventDefault();if(!currentSettingsProject||by('currentSettingsFields').disabled)return;saveCurrentDraft();await window.ComicMiddle.saveSettings(currentSettingsValue(),'current-settings-'+currentSettingsProject.id);};
 by('resetCurrentSettings').onclick=()=>{if(!currentSettingsProject)return;try{localStorage.removeItem(currentDraftKey(currentSettingsProject.id))}catch{}window.updateCurrentStorySettings(currentSettingsProject)};
 function mode(){const auto=by('storyAuto').checked;by('autoFields').hidden=!auto;by('autoFields').querySelectorAll('input').forEach(n=>n.disabled=!auto);by('storyCount').disabled=auto;by('countHint').textContent=auto?'자동 완성은 페이지 목표에 맞춰 첫 컷부터 구성합니다.':'원하는 컷 수를 입력하세요. 많은 컷은 이어서 나눠 만듭니다.';by('storySubmit').textContent=auto?'자동으로 이야기 완성':'첫 이야기 만들기'}
 function draft(){const data={};for(const id of fieldIds)data[id]=by(id).value;data.storyAuto=by('storyAuto').checked;data.storyImages=by('storyImages').checked;data.characters=actors();return data}
 function saveDraft(){const data=draft();by('draftStatus').textContent='설정 저장 중…';draftQueue=draftQueue.catch(()=>{}).then(()=>api('/api/workspace/draft','POST',data)).then(()=>{by('draftStatus').textContent='설정이 자동 저장됩니다.'}).catch(()=>{by('draftStatus').textContent='설정을 저장하지 못했습니다. 다시 입력하거나 재시도해 주세요.'});return draftQueue}
 function scheduleDraft(){if(!ready)return;clearTimeout(draftTimer);draftTimer=setTimeout(saveDraft,400)}
 function brief(){return {seed:by('storySeed').value.trim(),direction:by('storyDirection').value.trim(),place:by('storyPlace').value.trim(),action_hint:by('storyAction').value.trim(),state_change:by('storyState').value.trim(),count:Number(by('storyCount').value),dialogue:by('storyDialogue').value,characters:actors(),generation_options:{translation_mode:by('storyTranslation').value,concise_prompts:true}}}
 function showError(message){by('storyProgress').hidden=false;by('storyProgress').className='story-progress failed';by('runMessage').textContent=message;by('runTitle').textContent='진행을 확인해 주세요.';by('runPause').hidden=true;by('runResume').hidden=!active;by('runRaw').hidden=!active?.current_job_id}
 let middleBusy=false;
 function lock(value){window.ComicMiddle?.setBusy(value);by('currentSettingsFields').disabled=value||middleBusy;by('storySubmit').disabled=value||middleBusy;by('storyProjects').disabled=value||middleBusy}
 function projects(list){const select=by('storyProjects');select.replaceChildren(new Option('저장된 이야기',''));for(const p of list)select.add(new Option(p.title||'새 이야기',p.id));select.value=projectId}
 async function loadProject(pid,force=false){if(!pid)return;const p=await api('/api/story/projects/'+pid);projectId=pid;by('storyProjects').value=pid;const revision=pid+':'+p.revision+':'+p.updated;if(!force&&loadedRevision===revision)return;loadedRevision=revision;window.ComicMiddle.render(p);}

 async function poll(){if(!active)return;clearTimeout(timer);try{const run=await api('/api/story/'+active.type+'/'+active.id);active={...run,type:active.type};by('storyProgress').hidden=false;const running=['running','queued'].includes(run.status);by('storyProgress').className='story-progress'+(['failed','interrupted'].includes(run.status)?' failed':'');by('runTitle').textContent=active.type==='starts'?'첫 이야기 · '+(run.progress?.panels||0)+' / '+run.target_panels+'컷':'자동 완성 · '+(run.progress?.pages||0)+' / '+(run.progress?.target_pages||'?')+'페이지';by('runMessage').textContent=run.message||run.status;by('runPause').hidden=!running;by('runPause').disabled=!!run.pause_requested;by('runResume').hidden=running||run.status==='complete';by('runRaw').hidden=!run.current_job_id;lock(running);if(run.project_id){try{await loadProject(run.project_id)}catch(e){if(run.status==='complete')throw e}}if(running){timer=setTimeout(poll,1500)}else{const ws=await api('/api/workspace');projects(ws.projects)}}catch(e){showError(e.message+' 저장된 결과는 유지됩니다. 이어서 진행으로 다시 확인할 수 있습니다.');lock(false)}}
 document.addEventListener('middle-busy',e=>{middleBusy=e.detail;const busy=middleBusy||['running','queued'].includes(active?.status);by('currentSettingsFields').disabled=busy;by('storySubmit').disabled=busy;by('storyProjects').disabled=busy});
 document.addEventListener('middle-refresh',async e=>{await loadProject(e.detail,true)});
 window.addEventListener('focus',()=>{if(ready&&projectId&&!middleBusy&&!['running','queued'].includes(active?.status))loadProject(projectId,true).catch(e=>showError(e.message));});
 form.addEventListener('input',scheduleDraft);form.addEventListener('change',scheduleDraft);by('storyAuto').addEventListener('change',mode);by('addActor').onclick=()=>{actor();scheduleDraft()};
 form.onsubmit=async e=>{e.preventDefault();if(!ready||!form.reportValidity())return;const data=brief(),auto=by('storyAuto').checked;if(!auto&&(!Number.isSafeInteger(data.count)||data.count<1)){showError('시작할 컷 수는 1 이상의 정수로 입력해 주세요.');return}const min=Number(by('storyMin').value),max=Number(by('storyMax').value),pages=Number(by('storyPages').value);if(auto&&(!Number.isInteger(min)||!Number.isInteger(max)||!Number.isInteger(pages)||min<1||max>5||min>max||pages<1||pages>30)){showError('목표 페이지는 1~30, 페이지당 컷 수는 1~5이며 최소가 최대보다 작거나 같아야 합니다.');return}lock(true);active=null;clearTimeout(timer);clearTimeout(draftTimer);await saveDraft();try{const payload=auto?{brief:data,target_pages:pages,min_panels_per_page:min,max_panels_per_page:max,panels_per_page:max,render_images:by('storyImages').checked,generation_options:data.generation_options}:data;const result=await api(auto?'/api/story/auto':'/api/story/start','POST',payload);active={id:result.run_id,type:auto?'auto':'starts'};projectId='';loadedRevision='';by('storyOutput').replaceChildren(textNode('p','form-hint','새 이야기를 만들고 있습니다.'));by('runRawText').hidden=true;await poll()}catch(error){showError(error.message);lock(false)}};
 by('runPause').onclick=async()=>{if(!active)return;try{await api('/api/story/'+active.type+'/'+active.id+'/pause','POST',{});await poll()}catch(e){showError(e.message)}};
 by('runResume').onclick=async()=>{if(!active)return;try{await api('/api/story/'+active.type+'/'+active.id+'/resume','POST',{});await poll()}catch(e){showError(e.message)}};
 by('runRaw').onclick=async()=>{if(!active?.current_job_id)return;try{const raw=await api('/api/story/jobs/'+active.current_job_id+'/response');by('runRawText').hidden=false;by('runRawText').textContent=raw.text||JSON.stringify(raw.result,null,2)}catch(e){showError(e.message)}};
 by('storyProjects').onchange=async()=>{if(!by('storyProjects').value)return;try{await loadProject(by('storyProjects').value,true)}catch(e){showError(e.message)}};
 (async()=>{lock(true);try{const ws=await api('/api/workspace'),saved=ws.draft||{};for(const id of fieldIds)if(saved[id]!=null)by(id).value=saved[id];by('storyAuto').checked=!!saved.storyAuto;by('storyImages').checked=!!saved.storyImages;for(const value of saved.characters||[])actor(value);mode();projects(ws.projects||[]);const runs=[...(ws.starts||[]).map(r=>({...r,type:'starts'})),...(ws.auto||[]).map(r=>({...r,type:'auto'}))].sort((a,b)=>(b.updated||b.created||'').localeCompare(a.updated||a.created||''));ready=true;by('draftStatus').textContent='설정이 자동 저장됩니다.';const linked=new URLSearchParams(location.search).get('project');if(linked&&ws.projects?.some(p=>p.id===linked)){active=runs.find(r=>r.project_id===linked)||null;if(active)await poll();else await loadProject(linked)}else if(runs.length){active=runs[0];await poll()}else if(ws.projects?.length){await loadProject(ws.projects[0].id)}window.ComicMiddle.resume();lock(['running','queued'].includes(active?.status))}catch(e){showError(e.message);lock(true);by('draftStatus').textContent='작업실 연결을 확인한 뒤 새로고침해 주세요.'}})();
})();
