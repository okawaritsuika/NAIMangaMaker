/* Local character preset completion. Explicit selection copies only the three text fields. */
(function(global) {
  'use strict';
  const attached = new WeakMap();
  let nextId = 0;
  const folded = value => String(value || '').normalize('NFKC').toLocaleLowerCase().trim();
  function matches(rows, query) {
    const text=folded(query);
    return rows.filter(row => row && !row.archived && typeof row.name==='string')
      .map((row,index) => {const names=[row.name,...(Array.isArray(row.aliases)?row.aliases:[])].map(folded);const rank=!text?2:names.includes(text)?0:names.some(name=>name.startsWith(text))?1:names.some(name=>name.includes(text))?2:3;return {row,index,rank};})
      .filter(item=>item.rank<3).sort((a,b)=>a.rank-b.rank||a.index-b.index).slice(0,10).map(item=>item.row);
  }
  function copyToInputs(row, inputs) {
    const fields=[inputs.nameInput,inputs.personalityInput,inputs.appearanceInput];
    if(fields.some(field=>!field||field.disabled||field.readOnly||(typeof field.matches==='function'&&field.matches(':disabled')))) return false;
    if(!row||typeof row.name!=='string'||typeof row.personality!=='string'||typeof row.appearance!=='string') return false;
    const values=[row.name,row.personality,row.appearance];
    // Publish events after all values change, so draft handlers observe one complete preset.
    fields.forEach((field,index)=>{field.value=values[index];});
    if(inputs.genderInput){inputs.genderInput.value=row.gender||'auto';inputs.genderInput.dispatchEvent(new Event('input',{bubbles:true}));}
    fields.forEach(field=>{const EventType=(field.ownerDocument&&field.ownerDocument.defaultView&&field.ownerDocument.defaultView.Event)||global.Event;field.dispatchEvent(new EventType('input',{bubbles:true}));});
    return true;
  }
  function style(doc) {
    if(doc.getElementById('character-library-completion-style')) return;
    const sheet=doc.createElement('style');sheet.id='character-library-completion-style';
    sheet.textContent='.character-library-popup{position:fixed;z-index:2147482900;width:360px;max-height:420px;overflow:auto;background:#fff;color:#20362a;border:1px solid #a8bdaa;border-radius:11px;box-shadow:0 9px 35px #15301e25;padding:10px;font:13px/1.5 system-ui,sans-serif;box-sizing:border-box}.character-library-popup[hidden]{display:none}.character-library-popup-head{display:flex;gap:10px;justify-content:space-between;align-items:center;font-size:12px;margin:0 2px 7px}.character-library-popup-head a{color:#24694f;text-decoration:underline;font-size:11px}.character-library-option{border:1px solid #dbe4dc;border-radius:8px;padding:10px;margin-top:7px;background:#fbfcfa;cursor:pointer}.character-library-option[aria-selected=true]{background:#edf6ec;border-color:#76a680}.character-library-option-name{font-weight:750;margin:0 0 3px;font-size:13px}.character-library-option-detail{font-size:11px;line-height:1.6;color:#607266;margin:3px 0;white-space:pre-wrap;overflow-wrap:anywhere}.character-library-load{display:block;width:100%;padding:6px 10px;margin-top:8px;font:600 12px system-ui,sans-serif;background:#edf4ec;color:#285235;border:1px solid #b9cdbb;border-radius:6px;cursor:pointer}.character-library-empty{color:#65766b;padding:12px 3px;font-size:12px}.character-library-hint{font-size:10px;color:#67796c;margin:8px 2px 1px}.character-library-popup :focus-visible{outline:3px solid #a2cbaa;outline-offset:2px}';
    doc.head.appendChild(sheet);
  }
  function attach(inputs) {
    if(!inputs||!inputs.nameInput||!inputs.personalityInput||!inputs.appearanceInput) return null;
    const input=inputs.nameInput;
    if(attached.has(input)) return attached.get(input);
    const doc=input.ownerDocument;
    if(!doc||![input,inputs.personalityInput,inputs.appearanceInput].every(field=>/^(INPUT|TEXTAREA)$/.test(field.tagName))) return null;
    style(doc);
    const popup=doc.createElement('div');popup.id='character-library-popup-'+(++nextId);popup.className='character-library-popup';popup.hidden=true;
    const heading=doc.createElement('div');heading.className='character-library-popup-head';
    const title=doc.createElement('strong');title.textContent='저장된 캐릭터';
    const manage=doc.createElement('a');manage.href='/characters';manage.target='_blank';manage.rel='noopener';manage.textContent='프리셋 관리';heading.append(title,manage);
    const list=doc.createElement('div');list.id=popup.id+'-list';list.setAttribute('role','listbox');list.setAttribute('aria-label','캐릭터 프리셋');
    const help=doc.createElement('p');help.className='character-library-hint';help.textContent='불러오면 이름·성격·외형 입력이 바뀝니다. 작품의 인물 ID는 유지됩니다.';
    popup.append(heading,list,help);(input.closest('dialog')||doc.body).appendChild(popup);
    const previous={};
    for(const key of ['aria-autocomplete','aria-haspopup','aria-controls','aria-expanded','aria-activedescendant']) previous[key]=input.getAttribute(key);
    input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-haspopup','listbox');input.setAttribute('aria-controls',list.id);input.setAttribute('aria-expanded','false');
    let rows=[],visible=[],selected=-1,serial=0,destroyed=false,composing=false,applying=false,fetching=false;
    function close() {serial++;fetching=false;popup.hidden=true;selected=-1;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');}
    function position() {
      if(popup.hidden) return;
      if(input.isConnected===false) {destroy();return;}
      const rect=input.getBoundingClientRect();const width=Math.min(Math.max(rect.width,310),Math.max(220,global.innerWidth-16));
      popup.style.width=width+'px';popup.style.left=Math.max(8,Math.min(rect.left,global.innerWidth-width-8))+'px';
      const height=Math.min(popup.scrollHeight||350,420);
      popup.style.top=(global.innerHeight-rect.bottom<Math.min(height,160)&&rect.top>height?rect.top-height-5:Math.max(8,Math.min(rect.bottom+5,global.innerHeight-height-8)))+'px';
    }
    function showMessage(message) {visible=[];selected=-1;list.replaceChildren();const p=doc.createElement('p');p.className='character-library-empty';p.textContent=message;list.appendChild(p);popup.hidden=false;input.setAttribute('aria-expanded','true');position();}
    function highlight() {Array.from(list.children).forEach((el,index)=>el.setAttribute('aria-selected',String(index===selected)));const active=list.children[selected];if(active){input.setAttribute('aria-activedescendant',active.id);active.scrollIntoView({block:'nearest'});}else input.removeAttribute('aria-activedescendant');}
    function choose(index) {const row=visible[index];applying=true;const copied=copyToInputs(row,inputs);applying=false;if(copied){close();if(typeof inputs.onSelect==='function')inputs.onSelect(row);}return copied;}
    function render() {
      if(destroyed||input.disabled||input.readOnly||composing) return close();
      visible=matches(rows,input.value);selected=-1;list.replaceChildren();
      if(!visible.length) return showMessage(rows.length?'이 이름이나 별칭과 일치하는 프리셋이 없어요.':'저장된 프리셋이 없어요. 프리셋 관리에서 캐릭터를 추가해 보세요.');
      visible.forEach((row,index)=>{
        const option=doc.createElement('div');option.id=popup.id+'-option-'+index;option.className='character-library-option';option.setAttribute('role','option');option.setAttribute('aria-selected','false');
        const name=doc.createElement('p');name.className='character-library-option-name';name.textContent=row.name;option.appendChild(name);
        for(const [label,value] of [['별칭',(row.aliases||[]).join(', ')],['성격',row.personality],['외형',row.appearance]]) {if(!value)continue;const line=doc.createElement('p');line.className='character-library-option-detail';line.textContent=label+' · '+(value.length>160?value.slice(0,160)+'…':value);line.title=value;option.appendChild(line);}
        const button=doc.createElement('button');button.type='button';button.className='character-library-load';button.textContent='이 캐릭터 불러오기';button.addEventListener('click',event=>{event.preventDefault();choose(index);});option.appendChild(button);
        list.appendChild(option);
      });
      popup.hidden=false;input.setAttribute('aria-expanded','true');position();
    }
    async function refresh() {
      if(destroyed||input.disabled||input.readOnly) return;
      const request=++serial;fetching=true;showMessage('캐릭터 목록을 불러오고 있어요.');
      try {const response=await global.fetch('/api/characters',{method:'GET',cache:'no-store'});const data=await response.json();if(!response.ok||!Array.isArray(data.characters))throw new Error('목록을 읽지 못했습니다.');if(destroyed||request!==serial)return;fetching=false;rows=data.characters;render();}
      catch {if(!destroyed&&request===serial){fetching=false;showMessage('목록을 불러오지 못했어요. 이름 입력칸을 다시 선택하면 갱신합니다.');}}
    }
    function changed() {if(!applying&&!composing&&!fetching)render();}
    function keydown(event) {if(popup.hidden||composing||event.isComposing)return;if(event.key==='Escape'){event.preventDefault();close();}else if(['ArrowDown','ArrowUp'].includes(event.key)&&visible.length){event.preventDefault();selected=event.key==='ArrowDown'?Math.min(selected+1,visible.length-1):selected<0?visible.length-1:Math.max(0,selected-1);highlight();}else if(event.key==='Enter'&&selected>=0){event.preventDefault();choose(selected);}}
    function outside(event) {if(event.target!==input&&!popup.contains(event.target))close();}
    function compositionStart(){composing=true;close();}
    function compositionEnd(){composing=false;refresh();}
    function libraryChanged(){if(doc.activeElement===input||popup.contains(doc.activeElement))refresh();else rows=[];}
    const listeners=[[input,'focus',refresh],[input,'input',changed],[input,'keydown',keydown],[input,'compositionstart',compositionStart],[input,'compositionend',compositionEnd],[doc,'pointerdown',outside],[doc,'focusin',outside],[global,'resize',position],[global,'scroll',position,true],[global,'character-library-changed',libraryChanged]];
    for(const [target,event,handler,capture]of listeners)target.addEventListener(event,handler,capture);
    function destroy(){if(destroyed)return;destroyed=true;close();for(const[target,event,handler,capture]of listeners)target.removeEventListener(event,handler,capture);popup.remove();for(const[key,value]of Object.entries(previous))value===null?input.removeAttribute(key):input.setAttribute(key,value);attached.delete(input);}
    const handle={close,refresh,destroy};attached.set(input,handle);return handle;
  }
  global.CharacterLibrary={attach};
  if(typeof module==='object'&&module.exports)module.exports={matches,copyToInputs,attach};
})(typeof window==='object'?window:globalThis);
