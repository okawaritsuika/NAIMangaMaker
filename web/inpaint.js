(function(global){
  'use strict';
  global.InpaintEditor={create({stage,image,toolbar,getRow,isBusy,onChange,say,key}){
    const make=(tag,text)=>{const el=document.createElement(tag);if(text)el.textContent=text;return el;};
    const style=make('style');style.textContent='.inpaint-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px;background:#f4efe2;border:1px solid #d9cdb7;border-radius:10px;margin:8px 0}.inpaint-tools[hidden]{display:none}.inpaint-tools label{display:flex;align-items:center;gap:6px;font-size:12px}.inpaint-tools input[type=range]{width:90px;padding:0}.inpaint-mask{position:absolute;inset:0;width:100%;height:100%;opacity:.45;touch-action:none;cursor:crosshair}.inpaint-tools button{font-size:12px;padding:7px 10px;white-space:nowrap}';document.head.append(style);
    const toggle=make('button','부분 수정 · 인페인팅');toggle.type='button';toggle.id='inpaintToggle';toggle.setAttribute('aria-pressed','false');toolbar.append(toggle);
    const tools=make('div');tools.className='inpaint-tools';tools.hidden=true;toolbar.after(tools);
    const canvas=make('canvas');canvas.className='inpaint-mask';canvas.hidden=true;canvas.setAttribute('aria-label','수정할 영역을 칠하는 마스크');stage.append(canvas);
    const cursorLayer=make('div'),cursor=make('div');cursorLayer.className='inpaint-cursor-layer';cursor.className='inpaint-cursor';cursor.hidden=true;cursorLayer.setAttribute('aria-hidden','true');cursorLayer.append(cursor);stage.append(cursorLayer);
    style.textContent+='.inpaint-mask{cursor:none}.inpaint-cursor-layer{position:absolute;inset:0;overflow:hidden;pointer-events:none}.inpaint-cursor{position:absolute;border:1px solid #fff;box-shadow:0 0 0 1px #222,inset 0 0 0 1px #222;border-radius:50%;box-sizing:border-box;transform:translate(-50%,-50%);pointer-events:none}.inpaint-cursor[data-eraser=true]{border-style:dashed}';
    const ctx=canvas.getContext('2d',{willReadFrequently:true});
    const brush=make('button','브러시'),eraser=make('button','지우개'),undo=make('button','되돌리기'),clear=make('button','모두 지우기');
    [brush,eraser,undo,clear].forEach(b=>b.type='button');
    const size=make('input');size.type='range';size.min=4;size.max=200;size.value=40;size.setAttribute('aria-label','브러시 크기');
    const sizeText=make('span','40px'),sizeLabel=make('label','크기');sizeLabel.append(size,sizeText);
    const strength=make('input');strength.type='range';strength.min='.01';strength.max='1';strength.step='.01';strength.value='.6';strength.setAttribute('aria-label','인페인팅 변경 강도');
    const strengthText=make('span','0.60'),strengthLabel=make('label','변경 강도');strengthLabel.append(strength,strengthText);
    const feather=make('input');feather.type='range';feather.min='0';feather.max='32';feather.step='1';feather.value='8';feather.setAttribute('aria-label','인페인팅 경계 부드럽게');
    const featherText=make('span','8px'),featherLabel=make('label','경계 부드럽게');featherLabel.append(feather,featherText);
    const help=make('span','파란 영역만 수정 · 현재 프롬프트 사용 · 원본 크기 유지');help.style.cssText='font-size:12px;flex-basis:100%';
    tools.append(brush,eraser,undo,clear,sizeLabel,strengthLabel,featherLabel,help);
    let active=false,parent=null,history=[],drawing=false,last=null,erasing=false,hasMask=false,revision=0;
    let pointer=null;
    function hideCursor(){pointer=null;cursor.hidden=true;}
    function showCursor(event=pointer){
      if(!event||!active||isBusy()||event.pointerType==='touch'){hideCursor();return;}
      const r=canvas.getBoundingClientRect(),x=event.clientX-r.left,y=event.clientY-r.top;
      if(!r.width||!r.height||x<0||y<0||x>=r.width||y>=r.height){hideCursor();return;}
      pointer={clientX:event.clientX,clientY:event.clientY,pointerType:event.pointerType};
      cursor.style.left=(x/r.width*100)+'%';cursor.style.top=(y/r.height*100)+'%';
      cursor.style.width=(Number(size.value)*r.width/canvas.width)+'px';cursor.style.height=(Number(size.value)*r.height/canvas.height)+'px';cursor.hidden=false;
    }
    function mode(value){erasing=value;cursor.dataset.eraser=String(value);brush.setAttribute('aria-pressed',String(!value));eraser.setAttribute('aria-pressed',String(value));}mode(false);
    function store(){if(!parent)return;try{localStorage.setItem(key(),JSON.stringify({parent,mask:canvas.toDataURL(),strength:strength.value,feather:feather.value,hasMask}));}catch{say('마스크를 브라우저에 저장하지 못했어요. 이 화면에서는 계속 편집할 수 있습니다.','error');}}
    function reset(){ctx.clearRect(0,0,canvas.width,canvas.height);history=[];hasMask=false;}
    function remember(){revision++;history.push(ctx.getImageData(0,0,canvas.width,canvas.height));if(history.length>8)history.shift();}
    function update(){toggle.disabled=isBusy()||!getRow()?.verified||!getRow()?.image_sha256||image.hidden;for(const b of [brush,eraser,undo,clear,size,strength,feather])b.disabled=isBusy();undo.disabled=isBusy()||!history.length;if(isBusy())hideCursor();}
    function close(){active=false;hideCursor();canvas.hidden=true;tools.hidden=true;toggle.setAttribute('aria-pressed','false');onChange(false);}
    function sync(){const row=getRow();if(active&&(row?.id!==parent?.id||row?.image_sha256!==parent?.sha)){store();close();say('표시한 그림이 바뀌어 부분 수정 모드를 종료했어요. 새 그림에서 부분 수정을 켜면 새 마스크로 시작합니다.');}update();}
    toggle.addEventListener('click',()=>{
      if(isBusy())return;if(active){store();close();return;}
      const row=getRow();if(!row?.verified||!row.image_sha256||!image.complete||!image.naturalWidth){say('원본 그림을 다 불러온 뒤 부분 수정을 눌러 주세요.','error');return;}
      if(parent?.id!==row.id||parent?.sha!==row.image_sha256){canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;reset();parent={id:row.id,sha:row.image_sha256};
        try{const saved=JSON.parse(localStorage.getItem(key())||'null');if(saved?.parent?.id===parent.id&&saved.parent.sha===parent.sha){const restoring={...parent,revision};const restored=new Image();restored.onload=()=>{if(parent?.id===restoring.id&&parent?.sha===restoring.sha&&revision===restoring.revision&&restored.width===canvas.width&&restored.height===canvas.height){ctx.drawImage(restored,0,0);hasMask=!!saved.hasMask;}};restored.src=saved.mask;strength.value=saved.strength||'.6';strengthText.textContent=Number(strength.value).toFixed(2);feather.value=saved.feather??'8';featherText.textContent=feather.value+'px';}}catch{}
      }
      active=true;canvas.hidden=false;tools.hidden=false;toggle.setAttribute('aria-pressed','true');onChange(true);update();
    });
    brush.onclick=()=>mode(false);eraser.onclick=()=>mode(true);
    size.oninput=()=>{sizeText.textContent=size.value+'px';showCursor();};strength.oninput=()=>{strengthText.textContent=Number(strength.value).toFixed(2);store();};feather.oninput=()=>{featherText.textContent=feather.value+'px';store();};
    undo.onclick=()=>{if(isBusy()||!history.length)return;ctx.putImageData(history.pop(),0,0);hasMask=true;store();update();};
    clear.onclick=()=>{if(isBusy())return;remember();ctx.clearRect(0,0,canvas.width,canvas.height);hasMask=false;store();update();};
    function point(event){const r=canvas.getBoundingClientRect();return{x:(event.clientX-r.left)*canvas.width/r.width,y:(event.clientY-r.top)*canvas.height/r.height};}
    function stroke(p){ctx.globalCompositeOperation=erasing?'destination-out':'source-over';ctx.strokeStyle='#1687ff';ctx.fillStyle='#1687ff';ctx.lineWidth=Number(size.value);ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();if(last){ctx.moveTo(last.x,last.y);ctx.lineTo(p.x,p.y);ctx.stroke();}else{ctx.arc(p.x,p.y,Number(size.value)/2,0,Math.PI*2);ctx.fill();}last=p;hasMask=true;}
    canvas.onpointerdown=event=>{if(isBusy()||!active||event.button!==0)return;event.preventDefault();showCursor(event);remember();drawing=true;last=null;canvas.setPointerCapture(event.pointerId);stroke(point(event));};
    canvas.onpointermove=event=>{showCursor(event);if(drawing&&!isBusy())stroke(point(event));};
    canvas.onpointerenter=showCursor;canvas.onpointerleave=hideCursor;
    new ResizeObserver(()=>showCursor()).observe(canvas);
    window.addEventListener('scroll',hideCursor,true);window.addEventListener('blur',hideCursor);
    function finish(event){if(!drawing)return;drawing=false;last=null;if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);store();update();}
    canvas.onpointerup=finish;canvas.onpointercancel=event=>{finish(event);hideCursor();};
    image.addEventListener('load',sync);
    return {get active(){return active;},sync,update,payload(){
      if(!active)return null;
      if(drawing)throw new Error('마스크를 다 칠한 뒤 생성해 주세요.');
      const row=getRow();if(row?.id!==parent?.id||row?.image_sha256!==parent?.sha)throw new Error('원본이 바뀌었어요. 부분 수정 모드를 다시 켜 주세요.');
      const pixels=ctx.getImageData(0,0,canvas.width,canvas.height),out=make('canvas');out.width=canvas.width;out.height=canvas.height;let count=0;
      for(let i=0;i<pixels.data.length;i+=4){const v=pixels.data[i+3]>=128?255:0;count+=v?1:0;pixels.data[i]=pixels.data[i+1]=pixels.data[i+2]=v;pixels.data[i+3]=255;}
      if(!count)throw new Error('수정할 부분을 먼저 칠해 주세요.');out.getContext('2d').putImageData(pixels,0,0);
      return {render_id:parent.id,image_sha256:parent.sha,mask:out.toDataURL('image/png').split(',')[1],strength:Number(strength.value),feather:Number(feather.value)};
    },size(){return {width:canvas.width,height:canvas.height};}};
  }};
})(window);
