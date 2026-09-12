(() => {
 const box=document.getElementById('workHistory'),list=document.getElementById('historyList');let busy=false;
 const names={create:'새 이야기',expand:'컷 추가',compose:'페이지 추가',edit_panel:'컷 수정',reroll_panel:'컷 다시 요청',render:'그림 생성',direct:'전개 판단'};
 const statuses={complete:'완료',failed:'실패',interrupted:'중단',queued:'대기',running:'진행 중'};
 async function refresh(){if(!box.open||busy)return;busy=true;try{const {jobs}=await api('/api/story/jobs');document.getElementById('historyCount').textContent=jobs.length+'건';list.replaceChildren();for(const j of jobs){const row=document.createElement('article'),title=document.createElement('strong'),message=document.createElement('p');title.textContent=(names[j.kind]||j.kind)+' · '+(statuses[j.status]||j.status);message.textContent=j.message||'';row.append(title,message);if(j.raw?.available){const detail=document.createElement('details'),summary=document.createElement('summary'),raw=document.createElement('pre');summary.textContent='받은 원문 확인';detail.append(summary,raw);detail.addEventListener('toggle',async()=>{if(!detail.open||raw.textContent)return;try{const r=await api('/api/story/jobs/'+j.id+'/response');raw.textContent=r.text||JSON.stringify(r.result,null,2)}catch(e){raw.textContent=e.message}});row.append(detail)}list.append(row)}if(!jobs.length)list.textContent='저장된 작업 기록이 없습니다.'}catch(e){list.textContent=e.message}finally{busy=false}}
 box.addEventListener('toggle',refresh);
 document.addEventListener('middle-refresh',refresh);
})();
