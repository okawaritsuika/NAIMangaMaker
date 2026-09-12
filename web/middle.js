(() => {
const $=id=>document.getElementById(id),el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n};
const state={project:null,anchor:null,selected:new Set(),open:new Set(),busy:false},intents={story:'이야기 진행',dialogue:'대화',action:'행동 연결',emphasis:'다른 각도로 강조'};
let externalBusy=false,currentJob=null,pollTimer=null,clearKeys=[],batch=null;
const storageGet=k=>localStorage.getItem('naimanga-'+k),storageSet=(k,v)=>localStorage.setItem('naimanga-'+k,v),storageRemove=k=>localStorage.removeItem('naimanga-'+k);
const projectPath=()=>'/api/story/projects/'+state.project.id,isBusy=()=>state.busy||externalBusy;
function notify(message){document.dispatchEvent(new CustomEvent('job-message',{detail:message}));$('middleMessage').textContent=message;if($('batchStatus'))$('batchStatus').textContent=message}
function updateControls(){document.querySelectorAll('#expandDialog [data-mutating],#pagePreview [data-mutating],#storyOutput [data-mutating],#composeSelected,#storyList input[type=checkbox],#storyList input[type=radio]').forEach(n=>n.disabled=isBusy()||n.dataset.retryable==='no');if($('generateAll'))$('generateAll').disabled=isBusy()||!state.project?.pages.some(p=>!p.image_url||p.stale);document.dispatchEvent(new CustomEvent('middle-busy',{detail:state.busy}))}
function updateSelection(){const ordered=state.project.panels.filter(p=>state.selected.has(p.id));document.querySelectorAll('[data-panel-select]').forEach(n=>{n.checked=state.selected.has(n.dataset.panelSelect);n.closest('.story-card').classList.toggle('selected',n.checked)});$('composeSelected').disabled=isBusy()||!ordered.length;saveSelection()}
function updateAnchor(){document.querySelectorAll('[data-panel-anchor]').forEach(n=>{n.checked=n.dataset.panelAnchor===state.anchor;n.closest('.story-card').classList.toggle('anchor',n.checked)});saveSelection()}
function saveSelection(){if(state.project)writeDraft('selection-'+state.project.id,{anchor:state.anchor,selected:[...state.selected]})}
function renderGroups(){const p=state.project,list=$('storyList');list.replaceChildren();const used=new Set();for(const [i,page] of p.pages.entries()){const key='page-group-'+p.id+'-'+page.id,group=el('details','page-group');group.open=state.open.has(key);const title=el('summary','',(i+1)+'페이지 · '+page.panel_ids.length+'컷');group.append(title);for(const id of page.panel_ids){const index=p.panels.findIndex(n=>n.id===id);if(index<0)continue;used.add(id);group.append(panelCard(p.panels[index],index))}group.addEventListener('toggle',()=>{if(group.open)state.open.add(key);else state.open.delete(key)});list.append(group)}const remaining=p.panels.map((panel,index)=>({panel,index})).filter(n=>!used.has(n.panel.id));if(remaining.length){list.append(el('h3','unassigned-title','아직 페이지에 담지 않은 컷'));for(const n of remaining)list.append(panelCard(n.panel,n.index))}}
function renderProject(){if(!state.project)return;const p=state.project;$('middleTitle').textContent=p.title||'나의 이야기';$('metrics').textContent=p.panels.length+'컷 · '+p.pages.length+'페이지';$('savedSeed').textContent='시작 : '+(p.seed_ko||'');$('savedDirection').textContent='방향 : '+(p.direction_ko||'');$('savedPlace').textContent=p.place_ko?'장소 : '+p.place_ko:'';renderGroups();renderPages();updateControls();updateAnchor();updateSelection();document.dispatchEvent(new Event('project-render'))}
function render(p){window.updateCurrentStorySettings?.(p);window.InlineReader?.update(p);const changed=state.project?.id!==p.id;state.project=p;if(changed){const saved=readDraft('selection-'+p.id)||{};state.selected=new Set(saved.selected||[]);state.anchor=saved.anchor||p.panels[0]?.id}const ids=new Set(p.panels.map(n=>n.id));state.selected=new Set([...state.selected].filter(id=>ids.has(id)));if(!ids.has(state.anchor))state.anchor=p.panels[0]?.id;const host=$('storyOutput');if(!$('storyList'))host.innerHTML=`<div class="stage-header"><h2 id="middleTitle" class="story-title"></h2><span id="metrics" class="metrics"></span></div><details class="story-settings"><summary>이야기의 시작과 방향 보기</summary><p id="savedSeed"></p><p id="savedDirection"></p><p id="savedPlace"></p></details><div class="selectionbar"><div class="row"><button id="clearSelectionButton" type="button">선택 해제</button><button id="composeSelected" type="button">페이지 추가</button></div></div><div class="actions-wrap"><select id="selectedLayout" aria-label="컷 분배"><option value="auto">이야기에 맞춰 자동</option><option value="top">위쪽 큰 컷</option><option value="middle">가운데 큰 컷</option><option value="bottom">아래쪽 큰 컷</option></select></div><p id="middleMessage" role="status"></p><div id="middleJobActions" hidden><button id="middleRetry">실패 단계 재시도</button><button id="middleRestart">이 작업 처음부터</button><button id="middleRaw">받은 원문 확인</button></div><pre id="middleRawText" hidden></pre><div id="storyList" class="story-list"></div>`;
$('clearSelectionButton').onclick=()=>{state.selected.clear();updateSelection()};$('composeSelected').onclick=()=>launchJob(projectPath()+'/compose',{panel_ids:p.panels.filter(n=>state.selected.has(n.id)).map(n=>n.id),layout:$('selectedLayout').value},'페이지를 구성하고 있습니다.');
$('middleRetry').onclick=()=>retry(false);$('middleRestart').onclick=()=>retry(true);$('middleRaw').onclick=async()=>{try{const r=await api('/api/story/jobs/'+currentJob.id+'/response');$('middleRawText').hidden=false;$('middleRawText').textContent=r.text||JSON.stringify(r.result,null,2)}catch(e){notify(e.message)}};renderProject()}
async function refreshProject(){const pid=currentJob?.project_id||state.project.id;const p=await api('/api/story/projects/'+pid);render(p);document.dispatchEvent(new CustomEvent('middle-refresh',{detail:pid}))}
async function pollJob(){clearTimeout(pollTimer);try{let j=await api('/api/story/jobs/'+currentJob.id);const seen=new Set();while(j.retry_job_id&&!seen.has(j.id)){seen.add(j.id);j=await api('/api/story/jobs/'+j.retry_job_id)}currentJob=j;localStorage.setItem('naimanga-middle-job',JSON.stringify({...j,clearKeys}));document.dispatchEvent(new CustomEvent('job-update',{detail:j}));notify(j.message||j.status);state.busy=['running','queued'].includes(j.status)||!!j.automatic_retry_pending;$('middleJobActions').hidden=state.busy||j.status==='complete';$('middleRetry').disabled=!j.retryable;$('middleRestart').disabled=!j.restartable;updateControls();if(state.busy)pollTimer=setTimeout(pollJob,1200);else{if(j.status==='complete'){if(j.kind==='compose'){state.selected.clear();saveSelection()}clearKeys.forEach(storageRemove);clearKeys=[];localStorage.removeItem('naimanga-middle-job');await refreshProject();if(batch)await nextBatch()}else if(batch){stopBatch();notify((j.message||'그림 생성 실패')+' 한꺼번에 생성을 중단했습니다. 완료된 그림은 유지됩니다.')}}}catch(e){stopBatch();state.busy=false;updateControls();notify('진행 확인에 실패했습니다. 새로고침하면 저장된 작업을 다시 확인합니다. '+e.message)}}
async function launchJob(path,payload,message,opts={}){if(isBusy())return;state.busy=true;updateControls();notify(message);try{const r=await api(path,'POST',payload);currentJob={id:r.job_id,project_id:state.project.id};clearKeys=opts.clear||[];localStorage.setItem('naimanga-middle-job',JSON.stringify({...currentJob,clearKeys}));await pollJob();return true}catch(e){state.busy=false;updateControls();notify(e.message);return false}}
async function retry(restart){if(!currentJob||isBusy())return;state.busy=true;updateControls();try{const r=await api('/api/story/jobs/'+currentJob.id+'/retry','POST',{restart});currentJob={...currentJob,id:r.job_id};localStorage.setItem('naimanga-middle-job',JSON.stringify({...currentJob,clearKeys}));await pollJob()}catch(e){state.busy=false;updateControls();notify(e.message)}}
async function mutate(path,payload,message,opts={}){if(isBusy())return;state.busy=true;updateControls();try{await api(path,'POST',payload);(opts.clear||[]).forEach(storageRemove);notify(message);currentJob=null;await refreshProject()}catch(e){notify(e.message)}finally{state.busy=false;updateControls()}}
window.ComicMiddle={render,trackJob(job){currentJob=job;clearKeys=[];localStorage.setItem('naimanga-middle-job',JSON.stringify(job));return pollJob();},saveSettings(payload,key){return launchJob(projectPath()+'/settings',payload,'현재 이야기 설정을 저장하고 있습니다.',{clear:[key]});},renderPage(id,payload={}){return startRender(state.project.pages.find(p=>p.id===id),payload);},setBusy(value){externalBusy=value;if(state.project)updateControls()},resume(){const saved=localStorage.getItem('naimanga-middle-job');batch=readDraft('page-batch');if(batch?.project_id!==state.project?.id)batch=null;if(saved){try{currentJob=JSON.parse(saved);clearKeys=currentJob.clearKeys||[];if(currentJob.project_id===state.project?.id)pollJob()}catch{}}}};


function stopBatch(){batch=null;storageRemove('page-batch');}
async function beginBatch(){
 if(isBusy()||!state.project)return;
 const ids=state.project.pages.filter(p=>!p.image_url||p.stale).map(p=>p.id);
 if(!ids.length){notify('모든 페이지에 완성된 그림이 있습니다.');return}
 batch={project_id:state.project.id,remaining:ids,total:ids.length};writeDraft('page-batch',batch);
 await nextBatch();
}
async function nextBatch(){
 if(!batch)return;
 if(batch.project_id!==state.project.id){stopBatch();return}
 const id=batch.remaining.shift();writeDraft('page-batch',batch);
 if(!id){stopBatch();notify('모든 페이지의 그림 생성을 마쳤습니다.');updateControls();return}
 const page=state.project.pages.find(p=>p.id===id);
 if(!page||page.image_url&&!page.stale){await nextBatch();return}
 const started=await startRender(page);
 if(!started&&batch){stopBatch();updateControls();}
}

const layouts={auto:'이야기에 맞춰 자동',top:'위쪽 큰 컷',middle:'가운데 큰 컷',bottom:'아래쪽 큰 컷'};
const errorText=e=>e.message||String(e);
function sameOriginURL(value){try{const u=new URL(value,location.origin);return u.origin===location.origin&&u.pathname.startsWith('/files/')?u.href:null}catch{return null}}
async function startRender(page,payload={}){
 if(isBusy()||!page)return;state.busy=true;updateControls();
 const data={reroll:!!page.image_url,...payload};
 try{notify('이미지 생성 가능 여부를 확인하고 있습니다.');if(!await window.ImageCostUI.confirm({project_id:state.project.id,page_id:page.id,payload:data})){notify('생성을 취소했습니다.');return}}
 catch(e){notify(errorText(e));return}finally{state.busy=false;updateControls()}
 return launchJob(projectPath()+'/pages/'+page.id+'/render',data,'그림을 생성하고 있습니다.');
}
function renderPages(){
  $('generateAll').onclick=beginBatch;
  const target=$('pagePreview'),project=state.project;target.replaceChildren();const trash=el('details');trash.id='trashBox';const summary=el('summary','','삭제한 페이지 '),num=el('span');num.id='trashCount';summary.append(num);const list=el('div');list.id='trashList';trash.append(summary,list);target.append(trash);renderTrash();if(!project.pages.length){const empty=el('div','empty');empty.append(el('h3','','페이지가 아직 없습니다.'),el('p','','연결이 자연스러운 컷들을 모아 보세요.'));target.append(empty);return;}
  project.pages.forEach((page,index)=>{
    const card=el('article','box page-card'),body=el('div','page-body');const head=el('div','page-card-heading');head.append(el('h3','',page.title||((index+1)+'페이지')));body.append(head);
    card.dataset.pageId=page.id;
    const ordered=page.panel_ids.map(id=>project.panels.findIndex(p=>p.id===id)+1).filter(n=>n>0);body.append(el('p','page-meta',ordered.map(n=>n+'컷').join(' · ')),el('p','page-meta',layouts[page.layout]||'자동 배치'));card.append(body);
    const imageURL=page.image_url?sameOriginURL(page.image_url):null;
    if(imageURL){const link=document.createElement('a');link.href=imageURL;link.target='_blank';link.rel='noopener';link.setAttribute('aria-label',(index+1)+'페이지 원본 크게 보기');const image=document.createElement('img');image.src=imageURL;image.alt=(index+1)+'페이지 · '+ordered.length+'컷 만화';image.className='page-image';image.loading='lazy';link.append(image);card.append(link);}else card.append(el('div','page-placeholder','이 컷들을 한 장에 배치할 준비가 됐습니다.'));
    if(page.stale)card.append(el('p','stale','컷이나 구성이 바뀌어 이전 그림과 다릅니다. 현재 내용으로 다시 그려 주세요. 이전 그림 버전은 보존됩니다.'));
    if(page.error)card.append(el('p','page-error',page.error));const actions=el('div','page-actions'),statusNames={draft:'구성 완료',rendered:'그림 생성 완료',failed:'생성 확인 필요'};
    actions.append(el('span','page-meta',statusNames[page.status]||'구성 완료'));const button=el('button','primary small','생성');button.type='button';button.dataset.renderPage=page.id;button.dataset.mutating='';button.addEventListener('click',()=>startRender(page));button.title=page.image_url?'새 시드로 다시 그리기':'이 페이지 그림 생성';head.append(button);card.append(actions);
    const tools=el('div','page-tools'),studio=el('a','hint','이미지 편집실에서 열기');studio.href='/image-editor?project='+encodeURIComponent(project.id)+'&page='+encodeURIComponent(page.id);studio.target='_blank';studio.rel='noopener';const reader=el('a','hint','이 페이지 크게 보기');reader.href='/reader?project='+encodeURIComponent(project.id)+'&page='+encodeURIComponent(page.id);reader.target='_blank';reader.rel='noopener';tools.append(reader,studio,pageEditor(page,index),promptEditor(page,index));card.append(tools);target.append(card);
  });
}
function pageEditor(page,index){
  const path=projectPath()+'/pages/'+encodeURIComponent(page.id),key=draftID('page-edit',page.id),draft=readDraft(key)||{},box=fold('컷 구성 · 그림 버전 · 페이지 삭제',key),form=el('form');
  box.append(el('p','hint','구성을 바꾸면 수동 페이지 프롬프트는 초기화됩니다. 이전 그림은 버전으로 남으며 새 구성은 다시 그려야 합니다.'));
  const chosen=new Set(draft.panel_ids||page.panel_ids),picker=el('div','panel-picker'),count=el('p','hint'),checks=[];
  box.append(action('현재 선택한 컷 가져오기',()=>{if(!state.selected.size){notify('이야기 카드에서 페이지에 담을 컷을 먼저 선택해 주세요.',{error:true});return;}chosen.clear();for(const id of state.selected)chosen.add(id);sync();persist();}));
  for(const [i,panel] of state.project.panels.entries()){const label=el('label'),input=document.createElement('input');input.type='checkbox';input.dataset.mutating='';input.dataset.pagePanel=panel.id;input.checked=chosen.has(panel.id);label.append(input,el('span','',(i+1)+'컷 · '+(panel.description_ko||'장면').slice(0,70)));picker.append(label);checks.push(input);input.addEventListener('change',()=>{if(input.checked)chosen.add(panel.id);else chosen.delete(panel.id);sync();persist();});}
  form.append(picker,count);const layout=control(form,'페이지 컷 분배',draft.layout||page.layout||'auto',{options:layouts});
  function values(){return {panel_ids:state.project.panels.filter(p=>chosen.has(p.id)).map(p=>p.id),layout:layout.value};}
  function persist(){writeDraft(key,values());}function sync(){checks.forEach(input=>{input.checked=chosen.has(input.dataset.pagePanel);});count.textContent='선택한 컷 '+chosen.size+' · 이야기 순서대로 배치';}
  layout.addEventListener('change',persist);sync();form.append(action('페이지 구성 적용',()=>form.requestSubmit(),{primary:true}));
  form.addEventListener('submit',event=>{event.preventDefault();const payload=values();if(!payload.panel_ids.length){notify('한 컷 이상 선택해 주세요.',{error:true});return;}persist();mutate(path+'/edit',payload,'페이지 구성을 바꿨습니다. 현재 구성으로 다시 그려 주세요.',{clear:[key]});});box.append(form);
  const renders=Array.isArray(page.renders)?page.renders:[];
  if(renders.length){const history=fold('이전 그림 선택 · '+renders.length+'개',key+'-versions'),options={};renders.forEach((record,i)=>{options[i]='그림 '+(i+1)+' · '+(record.engine==='gpt_image_edit'?'GPT 이미지 보정':'NAI · 시드 '+(record.seed??'미상'))+' · '+dateLabel(record.created_at||record.created);});
    const current=Number.isInteger(page.selected_render_index)?page.selected_render_index:renders.length-1,version=control(history,'표시할 그림 버전',String(current),{options});
    for(const option of version.options){const record=renders[Number(option.value)];if(['failed','uncertain','started'].includes(record.status))option.disabled=true;}
    const detail=el('p','hint');history.append(detail);const choose=action('이 그림을 현재 버전으로 선택',()=>mutate(path+'/version',{index:Number(version.value)},'선택한 이전 그림을 표시합니다. 이야기 컷은 그대로입니다.'));
    function versionInfo(){const record=renders[Number(version.value)];detail.textContent=record?'그림 '+(Number(version.value)+1)+' 선택 · 컷 내용은 바꾸지 않습니다.':'';choose.dataset.retryable=record&&!['failed','uncertain','started'].includes(record.status)?'yes':'no';updateControls();}
    version.addEventListener('change',versionInfo);history.append(choose);versionInfo();box.append(history);
  }
  const remove=action('이 페이지 삭제',()=>mutate(path+'/delete',{},(index+1)+'페이지를 삭제한 페이지 목록으로 옮겼습니다. 언제든 복원할 수 있습니다.'));remove.classList.add('danger');box.append(el('div','divider'),remove,el('p','hint','컷 원문과 이전 그림은 삭제하지 않습니다. 아래 ‘삭제한 페이지’에서 복원할 수 있습니다.'));return box;
}
function promptEditor(page,index){
  const path=projectPath()+'/pages/'+encodeURIComponent(page.id),key=draftID('page-prompt',page.id),box=fold('고급 · 실제 이미지 프롬프트 편집',key),area=el('div');let loading=false,loaded=false;
  box.append(el('p','hint','실제로 이미지에 보내는 영어 프롬프트입니다. 인물 프롬프트의 개수와 배치 위치는 유지하고 문구만 수정합니다.'),area);
  async function load(){
    if(!box.open||loading||loaded||false)return;loading=true;area.replaceChildren(el('p','hint','이미지 프롬프트를 불러오는 중…'));
    try{const preview=await api(path+'/prompt');loaded=true;draw(preview);}catch(err){area.replaceChildren(el('p','job-error',errorText(err)));const retry=el('button','small','프롬프트 다시 불러오기');retry.type='button';retry.addEventListener('click',load);area.append(retry);}finally{loading=false;}
  }
  function draw(preview){
    area.replaceChildren();const draft=readDraft(key),compatibleDraft=draft&&draft.source_sha256===preview.source_sha256,draftValue=compatibleDraft?draft.overrides:null;
    if(preview.overrides_stale)area.append(el('p','stale','저장된 수동 프롬프트가 이전 컷을 기준으로 합니다. 아래 자동 프롬프트를 저장하거나 초기화한 뒤 다시 그려 주세요.'));
    if(draft&&!compatibleDraft){const old=el('details');old.append(el('summary','draft-note','컷이 바뀌어 이전 작성 입력을 적용하지 않았습니다 · 보존된 입력 보기'),el('pre','job-raw',JSON.stringify(draft.overrides,null,2)));area.append(old);}
    if(compatibleDraft)area.append(el('p','draft-note','저장하지 않은 프롬프트 입력을 복원했습니다.'));
    area.append(el('p','hint','모델 '+(preview.model||'설정값')+' · '+(preview.width||'?')+' × '+(preview.height||'?')+' · 현재 시드 '+(preview.seed??'미상')));
    const form=el('form','prompt-fields'),prompt=control(form,'전체 프롬프트 · 영어',draftValue?.prompt??preview.prompt,{rows:5,required:true}),negative=control(form,'전체 제외 프롬프트 · 영어',draftValue?.negative_prompt??preview.negative_prompt,{rows:3}),captions=[];
    (preview.characters||[]).forEach((character,i)=>{const group=el('div','prompt-caption'),number=state.project.panels.findIndex(p=>p.id===character.panel_id)+1;group.append(el('strong','hint',(number>0?number+'컷':'영역 '+(i+1))+' · '+(character.actor||'배경·사물')));
      const cp=control(group,'영역 '+(i+1)+' 프롬프트',draftValue?.characters?.[i]?.prompt??character.prompt,{rows:4,required:true}),cn=control(group,'영역 '+(i+1)+' 제외 프롬프트',draftValue?.characters?.[i]?.negative_prompt??character.negative_prompt,{rows:2});captions.push({prompt:cp,negative:cn});form.append(group);});
    const seed=control(form,'다음 그림의 시드 · 비우면 새 시드',compatibleDraft?(draft.seed??''):'',{kind:'input'});seed.inputMode='numeric';seed.placeholder='0 ~ 4294967295';
    function overrides(){return {prompt:prompt.value,negative_prompt:negative.value,characters:captions.map(c=>({prompt:c.prompt.value,negative_prompt:c.negative.value}))};}
    function persist(){writeDraft(key,{source_sha256:preview.source_sha256,overrides:overrides(),seed:seed.value});}
    form.addEventListener('input',persist);const buttons=el('div','actions-wrap');buttons.append(action('프롬프트 저장',()=>form.requestSubmit(),{primary:true}),action('자동 프롬프트로 초기화',()=>mutate(path+'/prompt',{prompt_overrides:null},'수동 프롬프트를 초기화했습니다. 현재 컷의 자동 프롬프트를 사용합니다.',{clear:[key]})));
    buttons.append(action('작성한 프롬프트로 새 그림',()=>{if(!form.reportValidity())return;const text=seed.value.trim();if(text&&(!/^\d+$/.test(text)||Number(text)>4294967295)){notify('시드는 0부터 4294967295 사이의 정수로 입력해 주세요.',{error:true});seed.focus();return;}persist();const payload={reroll:true,prompt_overrides:overrides()};if(text)payload.seed=Number(text);startRender(page,payload);},{auth:true}));form.append(buttons);
    form.addEventListener('submit',event=>{event.preventDefault();persist();mutate(path+'/prompt',{prompt_overrides:overrides()},'이미지 프롬프트를 저장했습니다. 새 그림을 만들 때 적용합니다.',{clear:[key]});});area.append(form);
    for(const input of [prompt,negative,...captions.flatMap(c=>[c.prompt,c.negative])]){input.dataset.tagInput='';window.TagCompletion?.attach?.(input);}updateControls();
  }
  box.addEventListener('toggle',load);if(box.open)load();return box;
}
function renderTrash(){
  const pages=Array.isArray(state.project.deleted_pages)?state.project.deleted_pages:[];$('trashBox').classList.toggle('hidden',!pages.length);$('trashCount').textContent='· '+pages.length+'개';$('trashList').replaceChildren();
  pages.forEach((record,index)=>{const page=record.page||record,card=el('div','job-card');card.append(el('h3','job-title',page.title||'삭제한 페이지 '+(index+1)),el('p','hint',(page.panel_ids?.length||0)+'컷 · '+dateLabel(record.deleted_at||page.deleted_at)),action('페이지 복원',()=>mutate(projectPath()+'/pages/'+encodeURIComponent(page.id)+'/restore',{},'삭제한 페이지를 원래 위치로 복원했습니다.')));$('trashList').append(card);});
}

function visibleDialogue(value){if(typeof value==='string')return value;if(value&&typeof value==='object'){const text=value.text_ko??value.text??value.dialogue_ko??value.dialogue??'';const speaker=value.speaker_ko??value.speaker??'';return speaker?String(speaker)+' : '+String(text):String(text);}return '';}
function draftID(kind,id){return 'iterative-'+kind+'-'+state.project.id+'-'+id;}
function readDraft(key){try{return JSON.parse(storageGet(key)||'null');}catch{return null;}}
function writeDraft(key,value){storageSet(key,JSON.stringify(value));}
function fold(label,key){const box=el('details','editor');box.open=state.open.has(key);box.append(el('summary','',label));box.addEventListener('toggle',()=>{if(box.open)state.open.add(key);else state.open.delete(key);});return box;}
function control(host,label,value,{kind='textarea',options=null,rows=2,required=false}={}){
  const wrapper=el('label','field',label),input=document.createElement(options?'select':kind);input.dataset.mutating='';
  if(options)for(const [key,text] of Object.entries(options)){const option=el('option','',text);option.value=key;input.append(option);}
  else{input.maxLength=kind==='textarea'?40000:5000;if(kind==='textarea')input.rows=rows;}
  input.value=value??'';input.required=required;if(!options&&!label.includes('시드'))input.dataset.tagInput='';wrapper.append(input);host.append(wrapper);return input;
}
function action(label,handler,{primary=false,auth=false}={}){const button=el('button',(primary?'primary ':'')+'small',label);button.type='button';button.dataset.mutating='';if(auth)button.dataset.auth='';button.addEventListener('click',handler);return button;}
function dateLabel(value){if(!value)return '시간 미상';const date=new Date(value);return Number.isNaN(date.getTime())?'시간 미상':date.toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});}

function panelCard(panel,index){
  const card=el('article','story-card'),top=el('div','card-top'),anchorLabel=el('label','radio-label'),radio=document.createElement('input');
  card.dataset.panelId=panel.id;
  radio.type='radio';radio.name='anchor';radio.dataset.panelAnchor=panel.id;radio.setAttribute('aria-label',(index+1)+'컷을 추가 기준으로 선택');radio.addEventListener('change',()=>{state.anchor=panel.id;updateAnchor();});anchorLabel.append(radio,el('span','',(index+1)+'컷 · 기준 선택'));
  const selectLabel=el('label','check-label'),checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.dataset.panelSelect=panel.id;
  checkbox.addEventListener('change',()=>{if(checkbox.checked)state.selected.add(panel.id);else state.selected.delete(panel.id);updateSelection();});
  selectLabel.append(checkbox,el('span','','페이지에 담기'));top.append(anchorLabel,selectLabel);card.append(top);
  const tags=el('div','tags'),intent=panel.origin?.intent;if(intents[intent])tags.append(el('span','tag',intents[intent]));
  const dialogues=Array.isArray(panel.dialogues_ko)?panel.dialogues_ko.map(visibleDialogue).filter(t=>t.trim()):[];tags.append(el('span','tag'+(dialogues.length?'':' silent'),dialogues.length?'대사 '+dialogues.length+'개':'무대사'));
  const pageLabels=state.project.pages.map((p,i)=>p.panel_ids.includes(panel.id)?(i+1)+'페이지':null).filter(Boolean);if(pageLabels.length)tags.append(el('span','tag',pageLabels.join(' · ')));
  if(panel.display_language==='en')tags.append(el('span','tag','설명 영어'+(panel.dialogue_language==='ko'?' · 대사 한글':panel.dialogue_language==='en'&&dialogues.length?' · 대사 영어':'')));
  card.append(tags,el('p','description',panel.description_ko||'장면 설명이 아직 없습니다.'));
  if(dialogues.length){const box=el('div','dialogues');for(const text of dialogues)box.append(el('p','',text));card.append(box);}else card.append(el('p','no-dialogue','말 없이 행동과 표정으로 보여주는 컷'));
  const details=el('details','camera'),dl=el('dl');details.append(el('summary','','시점·배경·상태'));
  for(const [label,value] of [['초점',panel.camera_ko?.focus],['거리',panel.camera_ko?.framing],['각도',panel.camera_ko?.angle],['배경',panel.background_ko],['상태',panel.state_ko]])if(value)dl.append(el('dt','',label),el('dd','',String(value)));
  details.append(dl);card.append(details,panelEditor(panel,index));
  card.tabIndex=0;card.title='마우스 오른쪽 클릭: 앞뒤 이야기 늘리기';
  card.addEventListener('contextmenu',event=>{if(event.target.closest('input,textarea,select,a,[contenteditable]'))return;event.preventDefault();openExpand(panel.id);});
  card.addEventListener('keydown',event=>{if(event.target!==card)return;if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10'){event.preventDefault();openExpand(panel.id);}});
  return card;
}

async function openExpand(panelId){
 if(isBusy()){notify('진행 중인 작업이 끝난 뒤 컷을 추가할 수 있습니다.');return}
 const openingProject=state.project.id;
 let customChoices=[];try{customChoices=await api('/api/prompts/choices')}catch(e){notify('내 프롬프트 목록을 불러오지 못했습니다. '+e.message)}
 if(isBusy()||state.project.id!==openingProject)return;
 const panel=state.project.panels.find(p=>p.id===panelId);if(!panel)return;
 state.anchor=panelId;updateAnchor();
 const pid=state.project.id,key=draftID('expand',panelId),draft=readDraft(key)||{};
 let dialog=$('expandDialog');if(!dialog){dialog=el('dialog');dialog.id='expandDialog';dialog.setAttribute('aria-labelledby','expandDialogTitle');document.body.append(dialog)}
 dialog.replaceChildren();const head=el('div','modal-head'),title=el('h2','','앞뒤 이야기 늘리기');title.id='expandDialogTitle';
 const close=el('button','close','×');close.type='button';close.setAttribute('aria-label','앞뒤 이야기 늘리기 닫기');close.onclick=()=>dialog.close();head.append(title,close);dialog.append(head);
 const form=el('form','body'),index=state.project.panels.findIndex(p=>p.id===panelId);
 form.append(el('p','expand-anchor',(index+1)+'컷 기준 · '+(panel.description_ko||panel.description||'')));
 const pair=el('div','two');form.append(pair);
 const position=control(pair,'추가 위치',draft.position||'after',{options:{after:'기준 컷 뒤',before:'기준 컷 앞'}});
 const count=control(pair,'추가 컷 수',draft.count||1,{kind:'input',required:true});count.type='number';count.min='1';count.step='1';delete count.dataset.tagInput;
 form.append(el('p','hint','기존 컷은 유지하고 선택한 컷의 앞이나 뒤에 새 컷을 삽입합니다. 같은 위치에서 반복할 수 있습니다.'));
 const intent=control(form,'어떤 내용으로 늘릴까요?',draft.custom_profile_id?'preset:'+draft.custom_profile_id:draft.intent||'action',{options:{story:'이야기 진행 · 새 사건',dialogue:'대화 · 말과 반응',action:'행동 연결 · 준비와 과정',emphasis:'강조 · 직전·직후 동작, 다른 각도',...Object.fromEntries(customChoices.map(row=>['preset:'+row.id,'내 프롬프트 · '+row.title]))}});
 if(!intent.value)intent.value='action';
 const help=el('p','hint');form.append(help);
 const dialogue=control(form,'새 컷의 대사',draft.dialogue||'auto',{options:{auto:'NAI가 컷마다 결정',with:'대사 넣기',none:'대사 없이'}});
 const actionHint=control(form,'원하는 행동 · 선택',draft.action_hint||'',{kind:'input'}),stateChange=control(form,'이번 컷의 상태 변화 · 선택',draft.state_change||'',{kind:'input'});
 actionHint.dataset.tagInput='';stateChange.dataset.tagInput='';actionHint.maxLength=stateChange.maxLength=2000;
 const instruction=control(form,'추가 지시 · 선택',draft.instruction||'',{rows:3});instruction.maxLength=5000;
 const selectedProfile=()=>customChoices.find(row=>'preset:'+row.id===intent.value);
 const values=()=>({anchor_id:panelId,position:position.value,count:Number(count.value),intent:selectedProfile()?.base_intent||intent.value,...(selectedProfile()?{custom_profile_id:selectedProfile().id}:{}),dialogue:dialogue.value,action_hint:actionHint.value.trim(),state_change:stateChange.value.trim(),instruction:instruction.value.trim()});
 const persist=()=>writeDraft(key,values());form.addEventListener('input',persist);form.addEventListener('change',persist);
 const explain=()=>help.textContent=selectedProfile()?'선택한 내 프롬프트로 이번 컷을 작성합니다. 다른 단계의 설정은 그대로 사용합니다.':intent.value==='emphasis'?'기준 컷의 직전·직후 작은 동작을 다른 각도로 보여줍니다. 착장 변경은 행동 연결을 선택하세요.':'앞뒤 장면의 행동과 상태가 자연스럽게 연결되도록 요청합니다.';intent.addEventListener('change',explain);explain();
 const buttons=el('div','actions-wrap'),cancel=el('button','','닫기');cancel.type='button';cancel.onclick=()=>dialog.close();
 const submit=el('button','primary','선택한 위치에 추가하기');submit.type='submit';submit.dataset.mutating='';buttons.append(cancel,submit);form.append(buttons);dialog.append(form);
 form.onsubmit=async event=>{event.preventDefault();if(isBusy()||pid!==state.project.id)return;persist();dialog.close();await launchJob('/api/story/projects/'+pid+'/expand',values(),'기준 컷 앞뒤에 새 이야기를 추가하고 있습니다.',{clear:[key]});};
 dialog.showModal();position.focus();
}

function panelEditor(panel,index){
  const path=projectPath()+'/panels/'+encodeURIComponent(panel.id),key=draftID('panel-edit',panel.id),saved=readDraft(key),initial=saved||panel;
  const box=fold('컷 수정 · 다시 요청 · 이전 버전',key),form=el('form');box.append(el('p','hint','수정은 이 컷을 교체합니다. 이전 내용은 버전으로 보존됩니다. 연결된 페이지는 다시 그려야 하며, 수동 페이지 프롬프트는 최신 컷 기준으로 초기화됩니다.'));
  if(saved)form.append(el('p','draft-note','저장하지 않은 수정 입력을 복원했습니다.'));
  const description=control(form,'장면 설명',initial.description_ko,{required:true}),background=control(form,'배경',initial.background_ko,{required:true}),dialogues=control(form,'짧은 대사 한 줄 · 비우면 무대사',(initial.dialogues_ko||[]).map(visibleDialogue).join('\n'));
  const cameraBox=el('div','two');form.append(cameraBox);const focus=control(cameraBox,'초점',initial.camera_ko?.focus,{kind:'input',required:true}),framing=control(cameraBox,'거리·구도',initial.camera_ko?.framing,{kind:'input',required:true});
  const angle=control(form,'카메라 각도',initial.camera_ko?.angle,{kind:'input',required:true}),visualState=control(form,'이 컷의 상태 · 착장과 소지품',initial.state_ko);
  for(const input of [description,background,visualState])input.maxLength=6000;for(const input of [focus,framing,angle])input.maxLength=2000;dialogues.maxLength=1000;
  function value(){return {description_ko:description.value,background_ko:background.value,dialogues_ko:dialogues.value.split(/\r?\n/).map(t=>t.trim()).filter(Boolean),camera_ko:{focus:focus.value,framing:framing.value,angle:angle.value},state_ko:visualState.value};}
  form.addEventListener('input',()=>writeDraft(key,value()));const buttons=el('div','actions-wrap'),save=action('수정한 컷 저장',()=>form.requestSubmit(),{primary:true,auth:true});
  buttons.append(save,action('수정 입력 되돌리기',()=>{storageRemove(key);renderProject();}));form.append(buttons);
  form.addEventListener('submit',event=>{event.preventDefault();if(isBusy())return;const payload=value();writeDraft(key,payload);if(payload.dialogues_ko.length>1){notify('한 컷의 대사는 짧은 한 줄로 입력해 주세요. 비워 두면 무대사입니다.',{error:true});dialogues.focus();return;}launchJob(path+'/edit',payload,(index+1)+'컷의 수정 내용을 반영하고 있습니다.',{clear:[key]});});box.append(form);
  const rerollKey=draftID('panel-reroll',panel.id),rDraft=readDraft(rerollKey)||{},reroll=fold('같은 장면을 다시 요청',rerollKey);
  const mode=control(reroll,'요청 방식',rDraft.mode||'camera',{options:{camera:'카메라만 다시 선택',variation:'장면을 다르게 요청'}}),explain=el('p','hint');reroll.append(explain);
  const instruction=control(reroll,'다시 요청할 내용',rDraft.instruction||''),dialogue=control(reroll,'대사 선택',rDraft.dialogue||'auto',{options:{auto:'NAI가 결정',with:'대사 넣기',none:'대사 없이'}});
  function sync(){explain.textContent=mode.value==='camera'?'행동·착장·소지품은 유지하고 같은 순간의 카메라를 바꿉니다.':'현재 장면 안에서 행동과 표현을 다르게 요청합니다. 앞뒤 컷과 이어지도록 원하는 점을 적어 주세요.';writeDraft(rerollKey,{mode:mode.value,instruction:instruction.value,dialogue:dialogue.value});}
  mode.addEventListener('change',sync);instruction.addEventListener('input',sync);dialogue.addEventListener('change',sync);explain.textContent=mode.value==='camera'?'행동·착장·소지품은 유지하고 같은 순간의 카메라를 바꿉니다.':'현재 장면 안에서 행동과 표현을 다르게 요청합니다.';
  reroll.append(action('이 컷 다시 요청',()=>{sync();launchJob(path+'/reroll',{mode:mode.value,instruction:instruction.value,dialogue:dialogue.value},'현재 컷의 새 버전을 요청합니다. 이전 버전은 보존됩니다.');},{primary:true,auth:true}));box.append(reroll);
  if(Array.isArray(panel.history)&&panel.history.length){
    const history=fold('이전 컷 버전 '+panel.history.length+'개',key+'-history'),options={};panel.history.forEach((entry,i)=>{options[i]='버전 '+(i+1)+' · '+dateLabel(entry.saved_at||entry.created_at)+(entry.reason?' · '+entry.reason:'');});
    const version=control(history,'복원할 컷 버전','0',{options}),preview=el('div','version-preview');history.append(preview);
    function show(){const record=panel.history[Number(version.value)],previous=record?.panel||record?.snapshot||record;preview.textContent=previous?.description_ko||'이 버전의 설명을 확인할 수 없습니다.';}
    version.addEventListener('change',show);show();history.append(action('이 버전으로 컷 복원',()=>mutate(path+'/restore',{version:Number(version.value)},'이전 컷 버전을 복원했습니다. 연결된 페이지를 다시 그려 주세요.')));box.append(history);
  }return box;
}

})();
