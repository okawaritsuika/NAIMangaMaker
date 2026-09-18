(() => {
 const box=document.getElementById('workHistory'),list=document.getElementById('historyList'),latest=document.getElementById('latestJob');let busy=false,timer=null;
 const names={create:'새 이야기',expand:'컷 추가',compose:'페이지 추가',edit_panel:'컷 수정',reroll_panel:'컷 다시 요청',render:'그림 생성',direct:'전개 판단'};
 const statuses={complete:'완료',failed:'실패',interrupted:'중단',queued:'대기',running:'진행 중'};
 function diagnosticDetails(d){
  const details=document.createElement('details'),summary=document.createElement('summary'),info=document.createElement('p'),advice=document.createElement('p');
  details.className='job-diagnostic';summary.textContent='오류 상세 · 해결 방법';
  info.textContent=d.stage_label+' · '+d.summary;advice.textContent=d.next_action;details.append(summary,info,advice);
  if(d.detail){const detail=document.createElement('pre');detail.className='job-error-detail';detail.textContent=d.detail;details.append(detail)}
  const actions=document.createElement('div'),copy=document.createElement('button'),feedback=document.createElement('span'),note=document.createElement('p');
  const reportText='NAIMangaMaker 오류 정보\n'+JSON.stringify(d.report,null,2);
  actions.className='run-actions';copy.type='button';copy.textContent='문의용 정보 복사';feedback.setAttribute('role','status');
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(reportText);feedback.textContent='복사했어요.'}catch{feedback.textContent='아래 복사 내용에서 직접 선택해 복사해 주세요.'}};
  actions.append(copy,feedback);note.className='diagnostic-note';note.textContent='버전·실패 단계·오류 번호와 위치만 복사합니다. API 키·이야기 원문·위 상세 문구는 제외됩니다.';
  const preview=document.createElement('details'),label=document.createElement('summary'),report=document.createElement('pre');
  label.textContent='복사 내용 보기';report.textContent=reportText;preview.append(label,report);details.append(actions,note,preview);return details;
 }
 function card(j,isLatest=false){const row=document.createElement('article'),title=document.createElement('strong'),message=document.createElement('p');title.textContent=(isLatest?'최근 작업 · ':'')+(names[j.kind]||j.kind)+' · '+(statuses[j.status]||j.status);message.textContent=j.message||'';row.append(title,message);
  if(j.status==='failed'||j.status==='interrupted'){
   if(j.diagnostic)row.append(diagnosticDetails(j.diagnostic));
   const actions=document.createElement('div');actions.className='run-actions';
   for(const [label,restart,allowed] of [['실패 단계 재시도',false,j.retryable],['이 요청 처음부터',true,j.restartable]]){if(!allowed)continue;const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=async()=>{actions.querySelectorAll('button').forEach(n=>n.disabled=true);try{const result=await api('/api/story/jobs/'+j.id+'/retry','POST',{restart});await window.ComicMiddle.trackJob({id:result.job_id,project_id:j.project_id});await refresh()}catch(e){message.textContent=e.message;actions.querySelectorAll('button').forEach(n=>n.disabled=false)}};actions.append(b)}
   if(j.retry_job_id){const b=document.createElement('button');b.type='button';b.textContent='이어진 재시도 확인';b.onclick=()=>window.ComicMiddle.trackJob({id:j.retry_job_id,project_id:j.project_id});actions.append(b)}
   row.append(actions);if(j.retry_conflict){const reason=document.createElement('p');reason.textContent=j.retry_conflict;row.append(reason)}
  }
  if(j.raw?.available){const detail=document.createElement('details'),summary=document.createElement('summary'),raw=document.createElement('pre');summary.textContent='받은 원문 확인';detail.append(summary,raw);detail.addEventListener('toggle',async()=>{if(!detail.open||raw.textContent)return;try{const r=await api('/api/story/jobs/'+j.id+'/response');raw.textContent=r.text||JSON.stringify(r.result,null,2)}catch(e){raw.textContent=e.message}});row.append(detail)}return row;
 }
 async function refresh(){if(busy)return;busy=true;clearTimeout(timer);try{const {jobs}=await api('/api/story/jobs');const pid=document.getElementById('storyProjects').value,visible=pid?jobs.filter(j=>j.project_id===pid):jobs;document.getElementById('historyCount').textContent=visible.length+'건';const j=visible[0];latest.hidden=!j;if(j){const signature=JSON.stringify(j);if(latest.dataset.signature!==signature){latest.replaceChildren(card(j,true));latest.dataset.signature=signature}}if(box.open){list.replaceChildren(...visible.map(j=>card(j)));if(!visible.length)list.textContent='저장된 작업 기록이 없습니다.'}if(visible.some(j=>(['queued','running'].includes(j.status)||j.automatic_retry_pending)))timer=setTimeout(refresh,1500)}catch(e){if(box.open)list.textContent=e.message}finally{busy=false}}
 box.addEventListener('toggle',refresh);
 document.addEventListener('middle-refresh',refresh);
 document.addEventListener('project-render',refresh);
 document.addEventListener('job-update',refresh);
 document.addEventListener('job-message',()=>{clearTimeout(timer);timer=setTimeout(refresh,250)});
 document.getElementById('storyProjects').addEventListener('change',()=>{clearTimeout(timer);timer=setTimeout(refresh,50)});
 window.addEventListener('focus',refresh);
 // Keep one short-lived notice when the latest progress message is off screen.
 const toast=document.createElement('aside'),toastText=document.createElement('span'),dismiss=document.createElement('button');
 toast.id='progressToast';toast.hidden=true;toast.setAttribute('aria-label','최근 진행사항');
 toastText.setAttribute('role','status');toastText.setAttribute('aria-live','polite');
 dismiss.type='button';dismiss.textContent='×';dismiss.setAttribute('aria-label','진행 알림 닫기');
 toast.append(toastText,dismiss);document.body.append(toast);
 let notice=null,noticeTimer=null,lastMessage='';
 const sources=()=>[document.getElementById('runMessage'),latest.querySelector('article>p'),document.getElementById('middleMessage')].filter(Boolean);
 function onScreen(node){if(!node||!node.getClientRects().length)return false;const r=node.getBoundingClientRect();return r.bottom>0&&r.top<window.innerHeight&&r.right>0&&r.left<window.innerWidth;}
 function positionNotice(){
  const visible=notice&&sources().some(node=>node.textContent.trim()===notice.message&&onScreen(node));
  toast.hidden=!notice||notice.closed||Date.now()>notice.until||visible||!!document.querySelector('dialog[open]');
 }
 function announce(message){
  message=String(message||'').trim();if(!message||message===lastMessage){positionNotice();return;}
  lastMessage=message;notice={message,until:Date.now()+6500,closed:false};toastText.textContent=message;
  clearTimeout(noticeTimer);noticeTimer=setTimeout(positionNotice,6600);positionNotice();
 }
 dismiss.onclick=()=>{if(notice)notice.closed=true;positionNotice();};
 document.addEventListener('job-message',event=>announce(event.detail));
 const progress=document.getElementById('storyProgress');
 for(const target of [latest,progress])new MutationObserver(()=>{
  const node=target===latest?latest.querySelector('article>p'):document.getElementById('runMessage');
  if(!target.hidden&&node)announce(node.textContent);else positionNotice();
 }).observe(target,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['hidden']});
 window.addEventListener('scroll',positionNotice,{capture:true,passive:true});
 window.addEventListener('resize',positionNotice);
 document.addEventListener('toggle',positionNotice,true);
 document.getElementById('storyProjects').addEventListener('change',()=>{notice=null;lastMessage='';clearTimeout(noticeTimer);positionNotice();});
 refresh();
})();
