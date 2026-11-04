/* Language navigation preserves scene identity; it never transforms source data. */
(()=>{
 const link=document.getElementById('language-switch');if(!link)return;
 const studio=!!window.STUDIO;
 if(studio){
  const query=new URLSearchParams(location.search),requested=Number(query.get('frame'));
  if(query.has('frame')&&Number.isInteger(requested)&&requested>=0&&requested<CAM.views.length)setFrame(requested);
  if(query.has('object'))window.chooseAnnotatedObject(query.get('object'));
 }
 link.addEventListener('click',()=>{
  const url=new URL(link.href,location.href);url.hash=location.hash;
  if(studio){url.searchParams.set('frame',S.frame);if(S.objectToken)url.searchParams.set('object',S.objectToken);}
  else if(window.WB){url.hash=new URLSearchParams({case:state.selected,frame:state.frame,tab:state.tab}).toString();}
  link.href=url.href;
 });
})();
