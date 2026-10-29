(function(){
'use strict';
const pages={
 'library-prompts':{kind:'prompts',name:'分镜词库',eyebrow:'STORYBOARD COLLECTION',description:'把打磨好的分镜留在这里。按章节查阅，随时复制，让每一次创作都有迹可循。',unit:'份分镜',empty:'还没有收藏分镜词',hint:'在创作空间生成故事脚本后，点击「保存到分镜词库」。'},
 'library-images':{kind:'images',name:'角色图库',eyebrow:'CHARACTER GALLERY',description:'同一人物的不同形象，收在同一行。跨章节整理，无限延展你的角色与画面收藏。',unit:'张图片',empty:'还没有收藏图片',hint:'在图像创作的生成记录中，选择喜欢的图片并点击「保存到图库」。'},
 'library-videos':{kind:'videos',name:'视频档案',eyebrow:'MOTION ARCHIVE',description:'以章节为线索，收好故事的每一段动态。一个章节一行，多个版本并排回看。',unit:'个视频',empty:'还没有归档视频',hint:'从这里手动选择章节，保存 MP4、WebM 或视频直链。'}
};
Object.entries(pages).forEach(([key,value])=>{labels[key]=value.name;});
let requestId=0,counts={},entries=[],galleryPeople=[],query='',selectedSnapshot='',pendingSave=false;
const isLibrary=()=>!!pages[route];
async function libraryApi(path,body){
 const response=await fetch('/api/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
 const result=await response.json();if(!response.ok)throw Error(result.error||'资料库请求失败，请重试。');return result;
}
const prettyDate=value=>new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const beforeRender=window.render,beforeOpenBook=window.openBook;
window.render=function(){
 if(!isLibrary()){requestId++;$('back-novels').setAttribute('aria-label','返回作品库');beforeRender();return;}
 renderLibrary();
};
window.openBook=function(id){
 if(!isLibrary()){beforeOpenBook(id);return;}
 if(!leaveDraft())return;closeEditor();currentId=id;chapterId=current()?.chapters[0]?.id||null;selectedSnapshot='';render();
};
$('back-novels').addEventListener('click',event=>{
 if(!isLibrary())return;event.stopImmediatePropagation();if(!leaveDraft())return;closeEditor();currentId=null;chapterId=null;query='';render();
},true);
window.saveLibraryOutput=async function(body,button){
 if(pendingSave)return;pendingSave=true;const text=button?.textContent;
 if(button){button.disabled=true;button.textContent='正在保存…';}
 try{
  const result=await libraryApi('library-save',body);
  if(button){button.textContent='✓ 已存入资料库';button.disabled=true;}
  notify(result.existing?'该内容已在资料库中，无需重复保存。':'已保存到资料库，可从左侧按作品查看。');
 }catch(error){notify(error.message);if(button){button.textContent=text;button.disabled=false;}}
 finally{pendingSave=false;}
};
function pageHead(page,work){
 return `<div class="section-head library-heading"><div><div class="eyebrow">${page.eyebrow}</div><h2>${esc(work?work.title:page.name)}</h2><p>${esc(page.description)}</p></div>${work?`<div class="actions"><button class="btn" type="button" id="library-to-studio">前往创作空间 ↗</button>${page.kind==='images'?'<button class="btn primary" type="button" id="gallery-add-person">＋ 新增人物</button>':''}${page.kind==='videos'?'<button class="btn primary" type="button" id="library-import">＋ 保存视频</button>':''}</div>`:badge('按作品收藏')}</div>`;
}
function bindHead(page,work){
 if(!work)return;
 $('library-to-studio').onclick=()=>{if(!leaveDraft())return;closeEditor();route=page.kind==='prompts'?'text':page.kind==='images'?'image':'video';currentId=work.id;tab=page.kind==='prompts'?'script':'source';chapterId=chapterId||work.chapters[0]?.id||null;render();};
 if($('gallery-add-person'))$('gallery-add-person').onclick=()=>openGalleryPerson(work.id);
 if($('library-import'))$('library-import').onclick=()=>openVideoArchiveSave(work.id);
}
async function renderLibrary(){
 const page=pages[route],work=current(),request=++requestId;
 document.querySelectorAll('[data-nav]').forEach(button=>{const active=button.dataset.nav===route;button.classList.toggle('active',active);if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});
 $('page-title').textContent=work?work.title:page.name;
 $('breadcrumb').textContent='资料库 / '+page.name+(work?' / 作品':'');
 $('back-novels').classList.toggle('hidden',!work);$('back-novels').setAttribute('aria-label','返回资料库书架');
 ['novel-shelf','book-layout','text-tabs','settings-tabs','page-note'].forEach(id=>$(id)?.classList.add('hidden'));
 document.querySelector('.main').classList.remove('has-tabs');$('workspace').hidden=false;
 $('workspace').innerHTML=pageHead(page,work)+'<p class="muted library-loading" role="status">正在整理收藏…</p>';
 bindHead(page,work);
 try{
  if(work){
   const result=await Promise.all([libraryApi('library/'+encodeURIComponent(work.id)),page.kind==='images'?libraryApi('gallery-people/'+encodeURIComponent(work.id)):Promise.resolve([])]);if(request!==requestId)return;
   [entries,galleryPeople]=result;
   const items=entries.filter(entry=>entry.kind===page.kind);
   $('workspace').innerHTML=pageHead(page,work)+`<div class="library-summary"><strong>${items.length}</strong><span>${page.unit}已收藏 · ${page.kind==='prompts'?'独立保留每次收藏的分镜版本':page.kind==='images'?'按人物归档，不按章节分组；每行图片数量不限':'同章视频并排查看，便于比较不同版本'}<br>已收藏内容会长期保留，随时回来查看。</span></div><div id="library-content"></div>`;
   bindHead(page,work);
   if(page.kind==='prompts')paintPrompts(items,work);else paintMedia(items,page,work);
  }else{
   counts=await libraryApi('library-counts');if(request!==requestId)return;
   $('workspace').innerHTML=pageHead(page,null)+`<div class="library-tools"><label><span class="sr-only">搜索作品</span><input id="library-search" type="search" placeholder="搜索作品名称…" value="${esc(query)}"></label><span class="muted">${data.novels.length} 部作品 · 点击封面查看收藏</span></div><div id="library-covers" class="covers library-covers"></div>`;
   paintShelf(page);$('library-search').oninput=event=>{query=event.target.value;paintShelf(page);};
  }
 }catch(error){if(request===requestId){$('workspace').innerHTML=pageHead(page,work)+empty('暂时无法读取资料库',error.message,'<button class="btn" type="button" id="library-retry">重新读取</button>');bindHead(page,work);$('library-retry').onclick=()=>renderLibrary();}}
}
function paintShelf(page){
 const books=data.novels.filter(work=>work.title.toLowerCase().includes(query.trim().toLowerCase()));
 $('library-covers').innerHTML=books.map(work=>{
  const count=counts[work.id]?.[page.kind]||0;
  return `<article class="cover-card ${count?'':'library-zero'}"><button type="button" class="cover" data-open="${esc(work.id)}" aria-label="打开${esc(work.title)}的${page.name}">${work.cover?`<img class="cover-img" src="${esc(work.cover)}" alt="${esc(work.title)}封面">`:`<div class="cover-pattern" aria-hidden="true"><small>映章 · 私藏资料</small><strong>${esc(work.title)}</strong><i>STORIES WORTH KEEPING</i></div>`}</button><div class="book-caption"><div><b>${esc(work.title)}</b><small>${count?`${count} ${page.unit} · 已收藏`:'尚无收藏 · 等待故事入藏'}</small></div></div></article>`;
 }).join('')||empty(data.novels.length?'没有找到这部作品':'先开启你的第一部作品',data.novels.length?'试试其他作品名称。':'在创作空间创建作品，生成后即可选择收藏。',data.novels.length?'':'<button class="btn primary" type="button" data-action="library">前往创作空间</button>');
}
function promptText(shot,people){
 const names=Object.fromEntries(people.map(person=>[person.character_id,person.name]));
 return [ [shot.shot_size,shot.camera,shot.frame].filter(Boolean).join(' · '),
  shot.setting?'场景：'+shot.setting:'',
  (shot.character_ids||[]).length?'人物：'+shot.character_ids.map(id=>names[id]||id).join('、'):'',
  shot.action?'动作：'+shot.action:'',shot.dialogue?'台词：'+shot.dialogue:'',
  shot.duration_seconds?'时长：'+shot.duration_seconds+' 秒':''
 ].filter(Boolean).join('\n');
}
async function copyText(value){try{await navigator.clipboard.writeText(value);notify('已复制分镜提示词。');}catch{notify('浏览器未允许复制，可选中提示词手动复制。');}}
function paintPrompts(items,work){
 const chapters=work.chapters||[];
 if(!chapters.some(chapter=>chapter.id===chapterId))chapterId=chapters[0]?.id||null;
 const versions=items.filter(entry=>entry.chapter_id===chapterId);
 const selected=versions.find(entry=>entry.id===selectedSnapshot)||versions[0];selectedSnapshot=selected?.id||'';
 const shots=selected?.content.script.shots||[],groups=selected?.content.groups||[];
 const host=$('library-content');
 const tableRows=groups.map(group=>{
  const members=group.shot_prompts||[];
  return members.map((shot,shotIndex)=>`<tr>${shotIndex===0?`<td rowspan="${members.length}"><b>${esc(group.group_id)}</b><p class="muted">${esc(group.seconds)} 秒 · ${members.length} 镜</p></td><td rowspan="${members.length}" data-copy-text="${esc(group.prompt)}"><pre>${esc(group.prompt)}</pre></td>`:''}<td data-copy-text="${esc(shot.prompt)}"><div class="section-head"><b>${esc(shot.shot_id)} <span class="muted">· ${esc(shot.duration_seconds)} 秒</span></b></div><pre>${esc(shot.prompt)}</pre></td><td data-copy-text="${esc(shot.keyframe_prompt||'')}"><pre>${esc(shot.keyframe_prompt||'')}</pre></td></tr>`).join('');
 }).join('');
 host.innerHTML=`<div class="chapter-bar"><label>选择章节<select id="library-chapter">${chapters.map(chapter=>`<option value="${esc(chapter.id)}" ${chapter.id===chapterId?'selected':''}>${esc(chapter.title)}${items.some(item=>item.chapter_id===chapter.id)?' · 已收藏':''}</option>`).join('')||'<option value="">暂无章节</option>'}</select></label>${versions.length?`<label>收藏版本<select id="library-version">${versions.map((entry,index)=>`<option value="${esc(entry.id)}" ${entry.id===selectedSnapshot?'selected':''}>${index===0?'最新收藏 · ':''}${prettyDate(entry.created_at)} · v${entry.content.revision}</option>`).join('')}</select></label>`:''}${groups.length?'<button class="btn library-export" type="button" id="library-export">导出 Excel</button>':''}</div>${groups.length?`<p class="muted">共 ${groups.length} 个片段组 · ${shots.length} 条分镜 · 右键单元格可复制 · 收藏于 ${esc(prettyDate(selected.created_at))} · ${esc(selected.content.chapter_title)}</p><div class="library-prompts"><table class="library-prompt-table" data-tree-label="组内分镜"><colgroup><col style="width:110px"><col style="width:32%"><col style="width:30%"><col></colgroup><thead><tr><th scope="col">片段组号</th><th scope="col">每组提示词</th><th scope="col">每个分镜提示词</th><th scope="col">关键帧提示词</th></tr></thead><tbody>${tableRows}</tbody></table></div><div class="actions" style="margin-top:24px"><button class="btn remove" type="button" data-remove-library="${esc(selected.id)}">移除此收藏版本</button><span class="muted">移除收藏不影响创作空间中的脚本。</span></div>`:emptyTable(['片段组号','每组提示词','每个分镜提示词','关键帧提示词'],'本章还没有收藏分镜词','在创作空间选择脚本版本，点击「保存到分镜词库」即可归档。')}`;
 $('library-chapter').onchange=event=>{chapterId=event.target.value;selectedSnapshot='';paintPrompts(items,work);};
 if($('library-version'))$('library-version').onchange=event=>{selectedSnapshot=event.target.value;paintPrompts(items,work);};
 if($('library-export'))$('library-export').onclick=async()=>{
  const button=$('library-export');button.disabled=true;button.textContent='正在导出…';
  try{
   const response=await fetch('/api/library-export?'+new URLSearchParams({work_id:work.id,entry_id:selected.id}));
   if(!response.ok){const error=await response.json();throw Error(error.error||'导出失败，请重试。');}
   const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
   link.href=url;link.download=(selected.content.chapter_title||'本章')+'_分镜_v'+selected.content.revision+'_'+selected.id.slice(0,8)+'.xlsx';
   document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
   notify('已导出当前章节收藏版本的 Excel。');
  }catch(error){notify(error.message);}finally{button.disabled=false;button.textContent='导出 Excel';}
 };
 bindRemove(host,work);
}
function paintMedia(items,page,work){
 const host=$('library-content');
 if(!items.length&&!(page.kind==='images'&&galleryPeople.length)){host.innerHTML=emptyTable([page.kind==='images'?'人物名称':'章节名',...Array.from({length:3},(_,index)=>(page.kind==='images'?'图片':'视频')+' '+String(index+1).padStart(2,'0'))],page.empty,page.hint,page.kind==='videos'?'<button class="btn primary" type="button" id="empty-import-video">＋ 手动保存第一段视频</button>':'');if($('empty-import-video'))$('empty-import-video').onclick=()=>openVideoArchiveSave(work.id);return;}
 const groups=new Map();
 if(page.kind==='images')galleryPeople.forEach(person=>groups.set('character:'+person.name,[]));
 // New image collections append to the right of each person row.
 const ordered=page.kind==='videos'?items.slice().sort((a,b)=>work.chapters.findIndex(c=>c.id===a.chapter_id)-work.chapters.findIndex(c=>c.id===b.chapter_id)):items.slice().sort((a,b)=>a.created_at.localeCompare(b.created_at)||a.id.localeCompare(b.id));
 ordered.forEach(entry=>{const key=page.kind==='images'?entry.content.image_kind+':'+entry.content.name:entry.chapter_id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(entry);});
 const columns=Math.max(3,...Array.from(groups.values(),group=>group.length+(page.kind==='images'?1:0)));
 host.innerHTML=`<p class="muted">${groups.size} ${page.kind==='images'?'组人物 / 画面':'个章节'} · 向右滚动可查看全部版本</p><div class="library-table-wrap" tabindex="0" role="region" aria-label="${page.name}素材表格"><table class="library-table"><thead><tr><th class="library-row-label" scope="col">${page.kind==='images'?'人物名称':'章节名'}</th>${Array.from({length:columns},(_,index)=>`<th scope="col">${page.kind==='images'?'图片':'视频'} ${String(index+1).padStart(2,'0')}</th>`).join('')}</tr></thead><tbody>${Array.from(groups.entries(),([key,group])=>{
  const first=group[0];const name=page.kind==='images'?key.slice(key.indexOf(':')+1):(work.chapters.find(chapter=>chapter.id===first.chapter_id)?.title||first.content.chapter_title);
  const addImage=page.kind==='images'?`<label class="btn library-add-image">＋ 添加图片<input type="file" data-gallery-upload data-gallery-name="${esc(name)}" data-gallery-kind="${esc(key.slice(0,key.indexOf(':')))}" accept=".png,.jpg,.jpeg,image/png,image/jpeg" multiple hidden></label>`:'';
  return `<tr><th class="library-row-label" scope="row">${esc(name)}<small>${group.length} ${page.unit}${page.kind==='images'&&!key.startsWith('character:')?'<br>非人物素材':''}</small></th>${group.map(entry=>`<td class="library-media">${page.kind==='images'?`<a href="${esc(entry.content.url)}" target="_blank" rel="noopener"><img src="${esc(entry.content.url)}" alt="${esc(name)}参考图" loading="lazy"></a>`:`<video src="${esc(entry.content.url)}" controls preload="metadata" aria-label="${esc(entry.content.title)}"></video><p>${esc(entry.content.title)}</p>`}<p class="muted">收藏于 ${esc(prettyDate(entry.created_at))}</p><div class="actions"><a class="btn" href="${esc(entry.content.url)}" target="_blank" rel="noopener">${page.kind==='images'?'查看原图':'打开视频'} ↗</a><button class="btn remove" type="button" data-remove-library="${esc(entry.id)}">移除收藏</button></div>${entry.content.prompt?`<details style="margin-top:12px"><summary class="muted">生成提示词</summary><p>${esc(entry.content.prompt)}</p></details>`:''}</td>`).join('')}${Array.from({length:columns-group.length},(_,index)=>`<td><div class="library-empty-cell">${index===0&&addImage?addImage:'—'}</div></td>`).join('')}</tr>`;
 }).join('')}</tbody></table></div>`;
 host.querySelectorAll('video').forEach(video=>video.addEventListener('error',()=>{if(!video.nextElementSibling?.classList.contains('video-error')){const note=document.createElement('p');note.className='muted video-error';note.textContent='暂时无法播放，请打开视频检查地址或文件。';video.after(note);}}));
 bindGalleryUploads(host,work);
 bindRemove(host,work);
}
let galleryUploadBusy=false;
function bindGalleryUploads(host,work){
 host.querySelectorAll('[data-gallery-upload]').forEach(input=>input.onchange=async()=>{
  if(galleryUploadBusy)return;
  const files=Array.from(input.files||[]);if(!files.length)return;
  const mimeOf=file=>/\.jpe?g$/i.test(file.name)?'image/jpeg':/\.png$/i.test(file.name)?'image/png':file.type==='image/jpg'?'image/jpeg':file.type;
  if(files.some(file=>!['image/png','image/jpeg'].includes(mimeOf(file))||file.size>8000000)){notify('请选择 PNG、JPG 或 JPEG，每张不超过 8 MB。');input.value='';return;}
  galleryUploadBusy=true;let saved=0;
  host.querySelectorAll('[data-gallery-upload]').forEach(element=>element.disabled=true);
  input.parentElement.classList.add('uploading');
  try{
   for(const file of files){
    const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(Error('无法读取图片，请重新选择。'));reader.readAsDataURL(file);});
    const upload=await libraryApi('studio-upload',{data_url:encoded.replace(/^data:[^;]*;/,'data:'+mimeOf(file)+';')});
    await libraryApi('library-save',{kind:'images',work_id:work.id,asset_id:upload.asset_ids[0],image_kind:input.dataset.galleryKind,name:input.dataset.galleryName});saved++;
   }
   notify(`已添加 ${saved} 张图片到「${input.dataset.galleryName}」行末尾。`);
  }catch(error){notify((saved?`已添加 ${saved} 张；`:'')+error.message);}
  finally{galleryUploadBusy=false;input.value='';if(route==='library-images'&&currentId===work.id)await renderLibrary();}
 });
}
function bindRemove(host,work){host.querySelectorAll('[data-remove-library]').forEach(button=>button.onclick=async()=>{
 if(!confirm('从资料库移除这份收藏？创作空间中的原始内容会保留。'))return;
 button.disabled=true;try{await libraryApi('library-remove',{work_id:work.id,id:button.dataset.removeLibrary});notify('已移除收藏。');if(isLibrary()&&currentId===work.id)renderLibrary();}catch(error){notify(error.message);button.disabled=false;}
});}
function openGalleryPerson(workId){
 $('gallery-person-dialog')?.remove();
 const dialog=document.createElement('dialog');dialog.id='gallery-person-dialog';dialog.className='library-dialog';
 dialog.innerHTML='<form id="gallery-person-form"><h2>新增图库人物</h2><p class="muted">创建人物空行，之后保存图片时可选择放入这一行。</p><label class="field">人物名称<input id="gallery-person-name" required maxlength="120" placeholder="填写人物名称"></label><p class="import-error" id="gallery-person-error" role="alert"></p><div class="actions"><button class="btn" type="button" id="gallery-person-cancel">取消</button><button class="btn primary" type="submit" id="gallery-person-submit">创建人物行</button></div></form>';
 document.body.appendChild(dialog);dialog.showModal();$('gallery-person-name').focus();let busy=false;
 $('gallery-person-cancel').onclick=()=>{if(!busy){dialog.close();dialog.remove();}};
 dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 $('gallery-person-form').onsubmit=async event=>{event.preventDefault();if(busy)return;busy=true;$('gallery-person-submit').disabled=true;
  try{const result=await libraryApi('gallery-person-create',{work_id:workId,name:$('gallery-person-name').value.trim()});dialog.close();dialog.remove();notify(result.existing?'该人物行已存在。':'已创建人物行，可以开始收藏图片。');if(route==='library-images'&&currentId===workId)await renderLibrary();}
  catch(error){$('gallery-person-error').textContent=error.message;$('gallery-person-submit').disabled=false;}
  finally{busy=false;}
 };
}
window.openLibraryImageSave=function(assetId,sourceButton){
 if(!data.novels.length){notify('请先在故事脚本中创建作品，再收藏图片。');return;}
 $('library-image-dialog')?.remove();
 const dialog=document.createElement('dialog');dialog.id='library-image-dialog';dialog.className='library-dialog';
 dialog.innerHTML=`<form id="library-image-form"><h2>保存到角色图库</h2><p class="muted">选择图库人物，图片会追加到该人物行的末尾。也可以在这里新建人物。</p><label class="field">所属作品<select id="library-image-work">${data.novels.map(work=>`<option value="${esc(work.id)}" ${work.id===currentId?'selected':''}>${esc(work.title)}</option>`).join('')}</select></label><label class="field">素材类型<select id="library-image-kind"><option value="character">人物形象</option><option value="environment">环境与场景</option><option value="composition">分镜画面</option></select></label><label class="field" id="library-person-field">选择图库人物<select id="library-image-person" required disabled><option value="">正在读取人物…</option></select></label><label class="field" id="library-name-field" hidden>人物 / 素材名称<input id="library-image-name" maxlength="120" placeholder="填写新人物名称"></label><p class="import-error" id="library-image-error" role="alert"></p><div class="actions"><button class="btn" type="button" id="library-image-cancel">取消</button><button class="btn primary" type="submit" id="library-image-submit" disabled>保存到图库</button></div></form>`;
 document.body.appendChild(dialog);dialog.showModal();let busy=false,namesRequest=0,people=[],ready=false;
 const syncFields=()=>{const character=$('library-image-kind').value==='character',newPerson=$('library-image-person').value==='__new__';$('library-person-field').hidden=!character;$('library-image-person').required=character;$('library-image-person').disabled=!character||!ready;$('library-name-field').hidden=character&&!newPerson;$('library-image-name').required=!character||newPerson;$('library-image-submit').disabled=busy||(character&&!ready);};
 const paintNames=async()=>{const request=++namesRequest;ready=false;people=[];$('library-image-error').textContent='';$('library-image-person').innerHTML='<option value="">正在读取人物…</option>';syncFields();
  try{const saved=await libraryApi('gallery-people/'+encodeURIComponent($('library-image-work').value));if(request!==namesRequest||!dialog.isConnected)return;people=saved;ready=true;$('library-image-person').innerHTML='<option value="" selected disabled>请选择人物</option>'+people.map(person=>`<option value="${esc(person.id)}">${esc(person.name)}</option>`).join('')+'<option value="__new__">＋ 新建人物</option>';if(!people.length)$('library-image-person').value='__new__';syncFields();}
  catch(error){if(request===namesRequest&&dialog.isConnected){$('library-image-error').textContent=error.message;$('library-image-person').innerHTML='<option value="">读取失败，请重新选择作品</option>';syncFields();}}
 };
 $('library-image-work').onchange=()=>{$('library-image-name').value='';paintNames();};
 $('library-image-person').onchange=()=>{$('library-image-name').value='';syncFields();};
 $('library-image-kind').onchange=()=>{const kind=$('library-image-kind').value;$('library-image-name').value=kind==='environment'?'环境与场景':kind==='composition'?'分镜画面':'';syncFields();};
 $('library-image-cancel').onclick=()=>{if(!busy){dialog.close();dialog.remove();}};dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 $('library-image-form').onsubmit=async event=>{event.preventDefault();if(busy)return;const kind=$('library-image-kind').value,person=kind==='character'?people.find(person=>person.id===$('library-image-person').value):null;
  const body={kind:'images',work_id:$('library-image-work').value,asset_id:assetId,image_kind:kind,name:person?.name||$('library-image-name').value.trim(),...(person?{person_id:person.id}:{})};busy=true;syncFields();$('library-image-cancel').disabled=true;
  try{const result=await libraryApi('library-save',body);dialog.close();dialog.remove();notify(result.existing?'此图已存入该作品的图库。':'图片已保存到图库对应人物行的末尾。');if(sourceButton?.isConnected)sourceButton.textContent='✓ 已存入图库';if(route==='library-images'&&currentId===body.work_id)await renderLibrary();}
  catch(error){$('library-image-error').textContent=error.message;$('library-image-cancel').disabled=false;}
  finally{busy=false;if(dialog.isConnected)syncFields();}
 };
 paintNames();
};
window.openVideoArchiveSave=function(workId){
 const work=data.novels.find(item=>item.id===workId);if(!work)return;
 if(!work.chapters.length){notify('请先为作品保存章节，再按章节保存视频。');return;}
 document.getElementById('library-video-dialog')?.remove();
 const dialog=document.createElement('dialog');dialog.id='library-video-dialog';dialog.className='library-dialog';
 dialog.innerHTML=`<form id="library-video-form"><h2>手动保存到视频档案</h2><p class="muted">${esc(work.title)} · 核对章节并选择你确认要归档的成片；视频提示词不会自动保存为视频。</p><fieldset id="library-video-fields"><label class="field">归属章节（请手动选择）<select id="library-import-chapter" required>${'<option value="" selected disabled>请选择章节</option>'+work.chapters.map(chapter=>`<option value="${esc(chapter.id)}">${esc(chapter.title)}</option>`).join('')}</select></label><label class="field">视频名称<input id="library-import-title" required maxlength="160" placeholder="例如：初遇 · 第一版"></label><label class="field">选择视频文件<input id="library-import-file" type="file" accept="video/mp4,video/webm,.mp4,.webm"><small>MP4 / WebM，单个文件不超过 48 MB；每章可保存多个视频。</small></label><label class="field">或粘贴视频直链<input id="library-import-url" type="url" placeholder="https://…/video.mp4"><small>文件与直链二选一。直链需要长期有效，支持浏览器直接播放。</small></label></fieldset><p class="import-error" id="library-import-error" role="alert"></p><div class="actions"><button class="btn" type="button" id="library-import-cancel">取消</button><button class="btn primary" type="submit" id="library-import-submit">保存到视频档案</button></div></form>`;
 document.body.appendChild(dialog);dialog.showModal();
 let busy=false;
 dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 $('library-import-cancel').onclick=()=>{if(!busy){dialog.close();dialog.remove();}};
 $('library-import-file').onchange=()=>{const file=$('library-import-file').files[0];if(file&&!$('library-import-title').value)$('library-import-title').value=file.name.replace(/\.[^.]+$/,'').slice(0,160);};
 $('library-video-form').onsubmit=async event=>{
  event.preventDefault();if(busy)return;
  const file=$('library-import-file').files[0],url=$('library-import-url').value.trim(),error=$('library-import-error');
  if(!file&&!url){error.textContent='请选择视频文件，或填写视频直链。';return;}
  if(file&&url){error.textContent='请只选择视频文件或直链中的一种。';return;}
  if(file&&(!/\.(mp4|webm)$/i.test(file.name)||file.size>48000000)){error.textContent='请选择 48 MB 以内的 MP4 或 WebM 文件。';return;}
  const body={work_id:workId,kind:'videos',chapter_id:$('library-import-chapter').value,title:$('library-import-title').value.trim(),url};
  busy=true;error.textContent='';$('library-video-fields').disabled=true;$('library-import-submit').disabled=true;$('library-import-cancel').disabled=true;$('library-import-submit').textContent='正在保存…';
  try{
   if(file){const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(Error('无法读取视频文件，请重新选择。'));reader.readAsDataURL(file);});body.data_url=encoded.replace(/^data:[^;]*;/,'data:video/'+(/\.webm$/i.test(file.name)?'webm':'mp4')+';');}
   const result=await libraryApi('library-save',body);dialog.close();dialog.remove();notify(result.existing?'该视频已在视频档案中。':'视频已按所选章节保存到视频档案。');if(isLibrary()&&currentId===workId)renderLibrary();
  }catch(failure){error.textContent=failure.message;$('library-video-fields').disabled=false;$('library-import-submit').disabled=false;$('library-import-cancel').disabled=false;$('library-import-submit').textContent='保存到视频档案';}
  finally{busy=false;}
 };
};
})();
