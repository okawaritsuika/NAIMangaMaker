(() => {
 const $=id=>document.getElementById(id),studio=document.querySelector('main.studio');
 let project=null,opened=false,scrollY=0;
 let openFromLink=new URLSearchParams(location.search).get('view')==='comic';
 function draw(){
  $('largeReaderTitle').textContent=project?.title||'만화 크게 보기';
  const host=$('largeReaderImage');host.replaceChildren();
  const rows=project?.pages||[];
  for(const [index,page] of rows.entries()){
   const figure=document.createElement('figure');figure.className='large-reader-page';
   const caption=document.createElement('figcaption');caption.textContent=(index+1)+'페이지';figure.append(caption);
   let url=null;try{const candidate=new URL(page.image_url,location.origin);if(page.image_url&&candidate.origin===location.origin&&candidate.pathname.startsWith('/files/'))url=candidate.href;}catch{}
   if(url){const img=document.createElement('img');img.src=url;img.alt=(index+1)+'페이지';img.loading='lazy';figure.append(img);}
   else{const empty=document.createElement('p');empty.className='large-reader-empty';empty.textContent='아직 그림이 없습니다. 오른쪽 카드에서 생성할 수 있습니다.';figure.append(empty);}
   host.append(figure);
  }
  if(!rows.length){const empty=document.createElement('p');empty.className='large-reader-empty';empty.textContent='아직 구성된 페이지가 없습니다.';host.append(empty);}
 }
 function toggle(){
  opened=!opened;if(opened)scrollY=window.scrollY;
  studio.classList.toggle('reading-large',opened);$('largeReader').hidden=!opened;
  $('openLargeReader').textContent=opened?'작업실로 돌아가기':'만화 크게 보기';
  $('openLargeReader').setAttribute('aria-expanded',String(opened));
  if(opened){draw();$('largeReader').focus({preventScroll:true});$('largeReader').scrollIntoView({block:'start'});}
  else{$('openLargeReader').focus({preventScroll:true});window.scrollTo(0,scrollY);}
 }
 $('openLargeReader').onclick=toggle;
 $('largeReader').addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();toggle()}});
 window.InlineReader={update(value){project=value;$('openLargeReader').disabled=!project;if(openFromLink){openFromLink=false;toggle()}else if(opened)draw()}};
})();
