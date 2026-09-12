(() => {
 const by=id=>document.getElementById(id),keys=['style_prompt','negative_prompt','steps','scale','sampler','width','height','cfg_rescale','noise_schedule','color_mode'],numeric=new Set(['steps','scale','width','height','cfg_rescale']);let saved=null;
 function values(){return Object.fromEntries(keys.map(k=>[k,numeric.has(k)?Number(by(k).value):by(k).value]))}
 function resolution(){const size=by('width').value+'x'+by('height').value;document.querySelectorAll('[data-size]').forEach(n=>n.setAttribute('aria-pressed',String(n.dataset.size===size)))}
 function fill(data){for(const k of keys)by(k).value=data[k]??(k==='color_mode'?'prompt':'');resolution()}
 function summary(){by('savedNumbers').textContent=`현재 저장값 ${saved.width} × ${saved.height} · Steps ${saved.steps} · CFG ${saved.scale} · ${saved.sampler} · ${saved.color_mode==='monochrome'?'흑백':'프롬프트대로'}`;by('savedStyle').textContent=saved.style_prompt||'그림체 미지정'}
 function stash(){try{localStorage.setItem('naimanga-image-settings-draft',JSON.stringify(values()))}catch{}}
 let presets=[],styleBusy=false,deleteArmed='';
 function selected(){return presets.find(row=>row.id===by('stylePreset').value)}
 function styleMessage(text,error=false){by('styleStatus').textContent=text;by('styleStatus').className='style-hint'+(error?' error':'')}
 function styleControls(){const has=!!selected();for(const id of ['stylePreset','styleName','styleCreate'])by(id).disabled=styleBusy;for(const id of ['styleLoad','styleUpdate','styleDelete'])by(id).disabled=styleBusy||!has;by('styleDelete').textContent=deleteArmed?'한 번 더 눌러 삭제':'삭제'}
 function fillStyles(rows,id=''){presets=rows;by('stylePreset').replaceChildren(new Option('그림체 선택',''),...rows.map(row=>new Option(row.name,row.id)));by('stylePreset').value=id;deleteArmed='';styleControls()}
 by('stylePreset').onchange=()=>{deleteArmed='';by('styleName').value=selected()?.name||'';styleMessage('');styleControls()};
 by('styleLoad').onclick=async()=>{
  const row=selected();if(!row||styleBusy)return;
  const draft=values();styleBusy=true;deleteArmed='';styleControls();by('imageFields').disabled=true;styleMessage('그림체를 적용하는 중…');
  try{
   const result=await api('/api/image-settings','POST',{settings:row.settings});
   saved=result.settings;fill({...draft,...row.settings});summary();
   const current=values(),changed=keys.some(k=>current[k]!==saved[k]);
   if(changed)stash();else localStorage.removeItem('naimanga-image-settings-draft');
   styleMessage('“'+row.name+'”을 불러와 기본값에 저장했습니다.');
   by('imageStatus').textContent=changed?'생성 수치에 저장 전 변경사항이 있습니다.':'기본값에 적용되었습니다.';
  }catch(e){styleMessage(e.message,true)}finally{styleBusy=false;by('imageFields').disabled=false;styleControls()}
 };
 const promptTabs=[by('styleTab'),by('negativeTab')];
 const styleManager=document.querySelector('.style-manager');
 document.addEventListener('click',e=>{if(!styleManager.contains(e.target))styleManager.open=false});
 styleManager.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();styleManager.open=false;styleManager.querySelector('summary').focus()}});
 function activateTab(tab){for(const item of promptTabs){const active=item===tab;item.setAttribute('aria-selected',String(active));item.tabIndex=active?0:-1;by(item.getAttribute('aria-controls')).hidden=!active}}
 for(const tab of promptTabs){tab.onclick=()=>activateTab(tab);tab.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const next=promptTabs[e.key==='Home'?0:e.key==='End'?1:1-promptTabs.indexOf(tab)];activateTab(next);next.focus()}}
 async function saveStyle(overwrite){
  if(styleBusy)return;const name=by('styleName').value.trim(),row=selected();
  if(!name){styleMessage('그림체 이름을 입력해 주세요.',true);by('styleName').focus();return}
  if(overwrite&&!row)return;
  const settings=Object.fromEntries(['style_prompt','negative_prompt','color_mode'].map(k=>[k,by(k).value]));
  styleBusy=true;deleteArmed='';styleControls();
  try{const result=await api('/api/image-styles','POST',{name,settings,...(overwrite?{id:row.id}:{})});fillStyles(result.styles,result.saved_id);styleMessage('“'+name+'”을 '+(overwrite?'덮어썼습니다.':'저장했습니다.'));by('styleName').value=name}
  catch(e){styleMessage(e.message,true)}finally{styleBusy=false;styleControls()}
 }
 by('styleCreate').onclick=()=>saveStyle(false);by('styleUpdate').onclick=()=>saveStyle(true);
 by('styleDelete').onclick=async()=>{const row=selected();if(!row||styleBusy)return;if(deleteArmed!==row.id){deleteArmed=row.id;styleControls();styleMessage('“'+row.name+'”을 삭제하려면 삭제 버튼을 한 번 더 누르세요.');return}styleBusy=true;styleControls();try{const result=await api('/api/image-styles/'+row.id+'/delete','POST',{confirmed:true});fillStyles(result.styles);by('styleName').value='';styleMessage('저장한 그림체를 삭제했습니다. 현재 입력과 기본값은 유지됩니다.')}catch(e){styleMessage(e.message,true)}finally{styleBusy=false;deleteArmed='';styleControls()}};
 document.querySelectorAll('[data-size]').forEach(n=>n.onclick=()=>{const [w,h]=n.dataset.size.split('x');by('width').value=w;by('height').value=h;resolution();stash();by('imageStatus').textContent='변경사항이 있습니다.'});
 by('imageSettingsForm').addEventListener('input',e=>{if(!keys.includes(e.target.id))return;resolution();stash();by('imageStatus').textContent='변경사항이 있습니다.'});
 by('imageReset').onclick=()=>{fill(saved);by('stylePreset').value='';by('styleName').value='';deleteArmed='';styleMessage('');styleControls();localStorage.removeItem('naimanga-image-settings-draft');by('imageStatus').textContent='저장된 값으로 되돌렸습니다.'};
 by('imageSettingsForm').onsubmit=async e=>{e.preventDefault();const data=values();if(data.width*data.height>3145728){by('imageStatus').textContent='가로 × 세로는 3,145,728픽셀 이하여야 합니다.';return}by('imageFields').disabled=true;try{const r=await api('/api/image-settings','POST',{settings:data});saved=r.settings;fill(saved);summary();localStorage.removeItem('naimanga-image-settings-draft');by('imageStatus').textContent='저장했습니다.'}catch(error){by('imageStatus').textContent=error.message}finally{by('imageFields').disabled=false}};
 (async()=>{try{const r=await api('/api/image-settings');saved=r.settings;fillStyles(r.styles||[]);for(const [key,list] of [['sampler',r.samplers],['noise_schedule',r.noise_schedules]])by(key).replaceChildren(...list.map(v=>new Option(v,v)));fill(saved);summary();try{const draft=JSON.parse(localStorage.getItem('naimanga-image-settings-draft')||'null');if(draft){fill({...saved,...draft});by('imageStatus').textContent='저장 전 입력을 복원했습니다.'}}catch{}by('imageFields').disabled=false}catch(e){by('savedNumbers').textContent='설정을 불러오지 못했습니다.';by('imageStatus').textContent=e.message}})();
})();
