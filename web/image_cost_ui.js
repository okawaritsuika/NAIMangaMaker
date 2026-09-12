(function(global){
  'use strict';
  let pending=false;
  async function confirmGeneration({project_id,page_id,payload}){
    if(pending)return false;
    pending=true;
    delete payload.anlas_confirmed;
    try{
      const response=await fetch('/api/story/projects/'+encodeURIComponent(project_id)+'/pages/'+encodeURIComponent(page_id)+'/image-cost',{
        method:'POST',headers:{'Content-Type':'application/json','X-NAIMangaMaker':'1'},body:JSON.stringify(payload)});
      const result=await response.json();
      if(!response.ok)throw new Error(result.error||'생성 비용을 확인하지 못했어요.');
      if(!result.can_generate){await notice(result,false);return false;}
      // Keep the displayed resolution/steps fixed even if another tab edits the page.
      payload.image_settings={...(payload.image_settings||{}),width:result.width,height:result.height,steps:result.steps};
      if(!result.requires_confirmation)return true;
      const approved=await notice(result,true);
      if(approved)payload.anlas_confirmed=true;
      return approved;
    }finally{pending=false;}
  }
  function notice(result,allow){
    return new Promise(resolve=>{
      const dialog=document.createElement('dialog');
      dialog.style.cssText='max-width:520px;width:calc(100% - 40px);padding:26px;border:2px solid #c27b20;border-radius:16px;color:#253630;background:#fff;font:16px/1.6 system-ui;box-sizing:border-box';
      const heading=document.createElement('h2');heading.textContent=allow?'Anlas를 사용하는 생성입니다':'생성 전에 확인해 주세요';heading.style.marginTop='0';
      const message=document.createElement('p');message.textContent=result.message;
      const size=document.createElement('p');size.textContent=result.width+' × '+result.height+' px · Steps '+result.steps;
      dialog.append(heading,message,size);
      for(const reason of result.reasons||[]){const p=document.createElement('p');p.textContent='• '+reason;dialog.append(p);}
      if(allow){const note=document.createElement('p');note.textContent='정확한 소모량은 현재 확인할 수 없습니다. 계속하면 보유 Anlas가 차감될 수 있습니다.';dialog.append(note);}
      const actions=document.createElement('div');actions.style.cssText='display:flex;gap:12px;justify-content:flex-end;flex-wrap:wrap';
      const cancel=document.createElement('button');cancel.textContent=allow?'취소 · 설정 바꾸기':'닫기';cancel.type='button';cancel.style.padding='12px';
      let completed=false;
      function finish(value){if(completed)return;completed=true;dialog.close();dialog.remove();resolve(value);}
      cancel.addEventListener('click',()=>finish(false));actions.append(cancel);
      if(allow){const proceed=document.createElement('button');proceed.type='button';proceed.textContent='Anlas 사용하고 생성';proceed.style.cssText='padding:12px;background:#8d4a00;color:white;border:0;border-radius:6px';proceed.addEventListener('click',()=>finish(true));actions.append(proceed);}
      dialog.addEventListener('cancel',e=>{e.preventDefault();finish(false);});
      dialog.append(actions);document.body.append(dialog);dialog.showModal();cancel.focus();
    });
  }
  global.ImageCostUI={confirm:confirmGeneration};
})(window);
