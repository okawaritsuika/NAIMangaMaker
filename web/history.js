(() => {
 const box=document.getElementById('workHistory'),list=document.getElementById('historyList'),latest=document.getElementById('latestJob');let busy=false,timer=null;
 const names={create:'새 이야기',expand:'컷 추가',compose:'페이지 추가',edit_panel:'컷 수정',reroll_panel:'컷 다시 요청',render:'그림 생성',direct:'전개 판단'};
 const statuses={complete:'완료',failed:'실패',interrupted:'중단',queued:'대기',running:'진행 중'};
 function card(j,isLatest=false){const row=document.createElement('article'),title=document.createElement('strong'),message=document.createElement('p');title.textContent=(isLatest?'최근 작업 · ':'')+(names[j.kind]||j.kind)+' · '+(statuses[j.status]||j.status);message.textContent=j.message||'';row.append(title,message);
  if(j.status==='failed'||j.status==='interrupted'){
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
 refresh();
})();
