(() => {
 const by=id=>document.getElementById(id),keys=['style_prompt','negative_prompt','steps','scale','sampler','width','height','cfg_rescale','noise_schedule'],numeric=new Set(['steps','scale','width','height','cfg_rescale']);let saved=null;
 function values(){return Object.fromEntries(keys.map(k=>[k,numeric.has(k)?Number(by(k).value):by(k).value]))}
 function resolution(){const size=by('width').value+'x'+by('height').value;document.querySelectorAll('[data-size]').forEach(n=>n.setAttribute('aria-pressed',String(n.dataset.size===size)))}
 function fill(data){for(const k of keys)by(k).value=data[k];resolution()}
 function summary(){by('savedNumbers').textContent=`현재 저장값 ${saved.width} × ${saved.height} · Steps ${saved.steps} · CFG ${saved.scale} · ${saved.sampler}`;by('savedStyle').textContent=saved.style_prompt||'그림체 미지정'}
 function stash(){try{localStorage.setItem('naimanga-image-settings-draft',JSON.stringify(values()))}catch{}}
 document.querySelectorAll('[data-size]').forEach(n=>n.onclick=()=>{const [w,h]=n.dataset.size.split('x');by('width').value=w;by('height').value=h;resolution();stash();by('imageStatus').textContent='변경사항이 있습니다.'});
 by('imageSettingsForm').addEventListener('input',()=>{resolution();stash();by('imageStatus').textContent='변경사항이 있습니다.'});
 by('imageReset').onclick=()=>{fill(saved);localStorage.removeItem('naimanga-image-settings-draft');by('imageStatus').textContent='저장된 값으로 되돌렸습니다.'};
 by('imageSettingsForm').onsubmit=async e=>{e.preventDefault();const data=values();if(data.width*data.height>3145728){by('imageStatus').textContent='가로 × 세로는 3,145,728픽셀 이하여야 합니다.';return}by('imageFields').disabled=true;try{const r=await api('/api/image-settings','POST',{settings:data});saved=r.settings;fill(saved);summary();localStorage.removeItem('naimanga-image-settings-draft');by('imageStatus').textContent='저장했습니다.'}catch(error){by('imageStatus').textContent=error.message}finally{by('imageFields').disabled=false}};
 (async()=>{try{const r=await api('/api/image-settings');saved=r.settings;for(const [key,list] of [['sampler',r.samplers],['noise_schedule',r.noise_schedules]])by(key).replaceChildren(...list.map(v=>new Option(v,v)));fill(saved);summary();try{const draft=JSON.parse(localStorage.getItem('naimanga-image-settings-draft')||'null');if(draft){fill({...saved,...draft});by('imageStatus').textContent='저장 전 입력을 복원했습니다.'}}catch{}by('imageFields').disabled=false}catch(e){by('savedNumbers').textContent='설정을 불러오지 못했습니다.';by('imageStatus').textContent=e.message}})();
})();
