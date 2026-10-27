(function(){
/* Cloud adapter: reuse the approved layout, persist via the existing business service. */
let cloudBusy=false;
async function api(path,body){const r=await fetch('/api/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const value=await r.json();if(!r.ok)throw Error(value.error||'云端请求失败');return value;}
async function refreshCloud(){const state=await api('state');data.novels=state.novels;data.profiles=state.profiles;data.image_profile=state.image_profile||{};if(state.templates)data.templates=state.templates;data.media_templates=state.media_templates||{};if(!data.profiles.some(p=>p.id===data.activeProfile))data.activeProfile=data.profiles[0]?.id||'';}
window.persist = function (){return true;}
async function guarded(action){if(cloudBusy)return;cloudBusy=true;try{await action();}catch(e){notify(e.message||'操作失败，请重试。');}finally{cloudBusy=false;}}
window.saveBook = async function (){return guarded(async()=>{if(!editDraft)return;const title=$('edit-title').value.trim();if(!title){notify('请填写作品名称。');return;}await api('work',{id:editingId,title,style:$('edit-style').value,cover:editDraft.cover});await refreshCloud();closeEditor();renderShelf();notify('作品已保存到云端。');});}
const originalOpenEditor=openEditor;
window.openEditor=function(id=null){if(id){refreshCloud().then(()=>{if(editingId===id)paintCharacterSuggestions();}).catch(()=>{});}originalOpenEditor(id);paintCharacterSuggestions();};
function paintCharacterSuggestions(){const novel=data.novels.find(item=>item.id===editingId);const suggestions=novel?.character_suggestions||[];if(!suggestions.length)return;const saved=(editDraft.characters||[]).map(row=>`<tr><td>${esc(row.name)}</td><td>${esc(row.appearance)}</td><td>${esc(row.costume)}</td><td>${esc(row.source||'')}</td></tr>`).join('');const pending=suggestions.map(row=>`<tr><td>${esc(row.name)}</td><td>${esc(row.appearance)}</td><td>${esc(row.costume)}</td><td>${esc(row.source)} · 待采用</td></tr>`).join('');$('cast-body').innerHTML=saved+pending+`<tr><td class="empty" colspan="4"><button type="button" class="btn" id="adopt-cast">采用本章分析的角色</button></td></tr>`;$('adopt-cast').onclick=()=>guarded(async()=>{await api('adopt-characters',{work_id:editingId});await refreshCloud();const fresh=data.novels.find(item=>item.id===editingId);editDraft.characters=fresh?.characters||[];renderCast(editDraft.characters);notify('角色资料已采用，之后新的改编会带上这些形象。');});};
window.saveChapter = async function (){return guarded(async()=>{const title=$('chapter-title').value.trim(),text=$('chapter-text').value;if(!title||!text.trim()){notify('请填写章节标题与正文。');return;}const old=chapter();const result=await api('chapter',{work_id:currentId,id:old?.id,revision:old?.revision,title,text,context:$('chapter-context').value});await refreshCloud();chapterId=result.id;dirty=false;renderBook();notify('章节已保存到云端。');});}
window.removeBook = function (){notify('为保护现有章节与任务，作品删除暂未开放。');}
const originalRenderHome=renderHome,originalRenderModels=renderModels,originalRenderBook=renderBook,originalRender=render;
function cloudCopy(root=document){const substitutions=[['配置原型 · 未连接','云端配置'],['当前浏览器中保存的作品','云端数据库中保存的作品'],['当前是交互原型','云端工作台'],['数据仅保存在此浏览器。模型请求与视频生成尚未接入云端应用，页面不会发送付费请求。','作品与章节保存在云端。文本改编由独立执行器处理；图片与视频尚未开放。'],['个模型配置','个模型配置'],['真实 API 与视频服务','视频服务待接入'],['未连接','待配置'],['浏览器原型','云端保存'],['保存可在刷新后恢复。清除浏览器数据会删除本地记录，建议定期导出备份。','已保存内容可跨浏览器恢复。正文修改请及时保存；同一章节并发修改会检查版本。'],['已保存至浏览器','已保存至云端'],['保存提示词草案','提示词草案 · 尚未接入'],['仅保存在浏览器，尚未接入实际生成流程。','提示词草案暂不写入真实生成流程。']];const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n;while(n=walker.nextNode()){if(['SCRIPT','STYLE'].includes(n.parentElement?.tagName))continue;let text=n.nodeValue;for(const [a,b]of substitutions)text=text.replaceAll(a,b);n.nodeValue=text;}}
window.renderHome = function (){originalRenderHome();cloudCopy($('workspace'));}
window.render = function (){
 if($('settings-tabs'))$('settings-tabs').classList.add('hidden');
 originalRender();cloudCopy();
 if(current()&&['overview','image','video'].includes(route)){
  $('page-title').textContent=current().title;
  $('breadcrumb').textContent='创作空间 / '+labels[route]+' / 作品';
  if(route==='overview')renderOverview();
  else if(route==='image')renderShotImages();
  else $('workspace').innerHTML=empty('视频制作工作台待接入','该作品的视频制作流程暂未开放。');
 }
};
let overviewRequest=0;
async function renderOverview(){
 const work=current();if(!work)return;
 const request=++overviewRequest;
 const chapters=work.chapters||[];
 if(!chapters.some(chapter=>chapter.id===chapterId))chapterId=chapters[0]?.id||null;
 const selected=chapterId;
 $('page-title').textContent=work.title;
 $('breadcrumb').textContent='创作空间 / 总览 / 作品';
 $('workspace').hidden=false;
 $('workspace').innerHTML=`<div class="chapter-bar"><label>章节筛选<select id="overview-chapter">${chapters.length?chapters.map(chapter=>`<option value="${esc(chapter.id)}" ${chapter.id===selected?'selected':''}>${esc(chapter.title)}</option>`).join(''):'<option value="">暂无章节</option>'}</select></label><button type="button" class="btn" id="overview-refresh">刷新产出</button></div><div id="overview-results" aria-live="polite"><p class="muted">正在读取分镜与素材…</p></div>`;
 $('overview-chapter').onchange=e=>{chapterId=e.target.value;renderOverview();};
 $('overview-refresh').onclick=()=>renderOverview();
 const active=()=>route==='overview'&&currentId===work.id&&chapterId===selected&&request===overviewRequest;
 try{
  const result=selected?await api('overview/'+selected):{shots:[],run:null};
  if(!active())return;
  const rows=result.shots.map(shot=>{
   const details=[shot.composition,shot.action,shot.start_state?'起始：'+shot.start_state:'',shot.end_state?'结束：'+shot.end_state:''].filter(Boolean).map(value=>`<p style="white-space:pre-wrap">${esc(value)}</p>`).join('');
   const dialogue=(shot.dialogue||[]).map(value=>`<p>对白：${esc(value)}</p>`).join('');
   const subjects=(shot.subjects||[]).map(person=>({name:'人物 · '+person.name,description:person.description,images:[...(person.images||[]),...shot.image_source.images.filter(image=>image.selected&&image.kind==='character'&&image.subject_id===person.character_id).map(image=>({...image,kind:'shots'}))],kind:'characters'}));
   subjects.push({name:'环境',description:shot.environment_prompt||'',images:shot.image_source.images.filter(image=>image.selected&&image.kind==='environment'),kind:'shots'});
   const selected=shot.image_source.images.filter(image=>image.selected&&(!image.kind||image.kind==='composition'));
   return subjects.map((subject,index)=>`<tr>${index===0?`<td rowspan="${subjects.length}"><b>${esc(shot.order)}. ${esc(shot.title)}</b><p class="muted">${esc([shot.shot_size,shot.camera,shot.duration_seconds+' 秒'].filter(Boolean).join(' · '))}</p>${details}${dialogue}${selected.length?`<details><summary>已采用的完整镜头画面</summary>${selected.map(image=>`<img src="/assets/shots/${esc(image.asset_id)}.img" alt="${esc(shot.title)}完整镜头画面" style="width:100%;max-height:260px;object-fit:contain">`).join('')}</details>`:''}</td>`:''}<td><b>${esc(subject.name)}</b><p style="white-space:pre-wrap">${esc(subject.description)}</p></td><td>${subject.images.length?subject.images.map(image=>`<a href="/assets/${image.kind==='shots'?'shots':subject.kind}/${esc(image.asset_id)}.img" target="_blank" rel="noopener"><img src="/assets/${image.kind==='shots'?'shots':subject.kind}/${esc(image.asset_id)}.img" alt="${esc(subject.name)}参考图" style="width:100%;max-height:240px;object-fit:contain"></a>`).join(''):`<p class="muted">${subject.kind==='characters'?'暂无已采用的人物图片':'暂无独立环境图片'}</p>`}</td>${index===0?`<td rowspan="${subjects.length}"><p class="muted">视频待生成</p></td>`:''}</tr>`).join('');
  }).join('');
  const note=result.run?`<p class="muted">分镜版本：${esc(result.run.updated_at)} · ${esc(statuses[result.run.status]||result.run.status)}</p>`:'';
  $('overview-results').innerHTML=note+`<div style="overflow-x:auto"><table class="overview-table" style="width:100%;min-width:960px;table-layout:fixed;border-collapse:collapse;background:white"><colgroup><col style="width:32%"><col style="width:30%"><col style="width:25%"><col style="width:13%"></colgroup><thead><tr><th scope="col">分镜</th><th scope="col">人物/环境</th><th scope="col">参考图</th><th scope="col">视频</th></tr></thead><tbody>${rows||`<tr><td colspan="4">${empty(selected?'本章还没有分镜':'作品还没有章节',selected?'请先在分镜拆解中生成本章镜头。':'请先在分镜拆解中保存章节。')}</td></tr>`}</tbody></table></div>`;
  if(!$('overview-table-style')){const style=document.createElement('style');style.id='overview-table-style';style.textContent='.overview-table th,.overview-table td{padding:20px;text-align:left;vertical-align:top;border:1px solid var(--line);overflow-wrap:anywhere}.overview-table th{background:#edf2e8;color:#50624f}.overview-table td p{font-size:13px;line-height:1.9}.overview-table td b{font-size:14px}';document.head.appendChild(style);}
 }catch(error){if(active())$('overview-results').innerHTML=empty('暂时无法读取章节产出',error.message||'请点击刷新重试。');}
}
let imageRequest=0;
const chosenShots={},imageTargets={},imageReferenceSelections={};
async function renderShotImages(){
 const work=current();if(!work)return;
 const request=++imageRequest,chapters=work.chapters||[];
 if(!chapters.some(item=>item.id===chapterId))chapterId=chapters[0]?.id||null;
 const selectedChapter=chapterId;
 const active=()=>route==='image'&&currentId===work.id&&chapterId===selectedChapter&&request===imageRequest;
 const globalPrompt=data.media_templates?.image||'';
 const configured=!!data.image_profile?.key_configured||data.image_profile?.backend==='mflux';
 $('workspace').innerHTML=`<div class="section-head"><div><div class="eyebrow">IMAGE STUDIO</div><h2>图像创作工作台</h2><p>先准备角色与环境，再用参考图生成完整镜头。每类图片独立保存、独立采用。</p></div>${badge(configured?'生图模型已配置':'请先配置生图模型',configured?'':'amber')}</div><div class="chapter-bar"><label>当前章节<select id="image-chapter">${chapters.length?chapters.map(item=>`<option value="${esc(item.id)}" ${item.id===selectedChapter?'selected':''}>${esc(item.title)}</option>`).join(''):'<option value="">暂无章节</option>'}</select></label><button class="btn" type="button" id="image-refresh">刷新分镜与配置</button></div><div id="image-studio" aria-live="polite"><p class="muted">正在读取章节分镜…</p></div>`;
 $('image-chapter').onchange=e=>{if(!leaveDraft()){e.target.value=selectedChapter||'';return;}dirty=false;chapterId=e.target.value;renderShotImages();};
 $('image-refresh').onclick=()=>{if(!leaveDraft())return;dirty=false;guarded(async()=>{await refreshCloud();if(active())renderShotImages();});};
 if(!selectedChapter){$('image-studio').innerHTML=empty('作品还没有章节','请先保存章节并生成分镜。');return;}
 try{
  const overview=await api('overview/'+selectedChapter);if(!active())return;
  if(!overview.shots.length){$('image-studio').innerHTML=empty('本章还没有分镜','请先在分镜拆解中生成本章镜头。');return;}
  const shotKey=work.id+':'+selectedChapter;
  let shot=overview.shots.find(item=>item.shot_id===chosenShots[shotKey])||overview.shots[0];
  const paint=()=>{
   chosenShots[shotKey]=shot.shot_id;
   const source=shot.image_source,subjects=source.subjects||[],targetKey=overview.run.id+':'+shot.shot_id;
   const target=imageTargets[targetKey]||{kind:subjects.length?'character':'environment',subject_id:subjects[0]?.character_id||''};
   if(target.kind==='character'&&!subjects.some(person=>person.character_id===target.subject_id))target.subject_id=subjects[0]?.character_id||'';
   imageTargets[targetKey]=target;
   const kind=target.kind,subjectId=kind==='character'?target.subject_id:'',person=subjects.find(item=>item.character_id===subjectId);
   const label=kind==='character'?(person?.name||'角色')+' · 角色图':kind==='environment'?'环境图':'整体画面';
   const targetId=targetKey+':'+kind+':'+subjectId;
   const images=source.images.filter(image=>(image.kind||'composition')===kind&&(image.subject_id||'')===subjectId);
   const eligible=source.images.filter(image=>kind==='composition'||image.kind===kind&&(kind!=='character'||image.subject_id===subjectId));
   if(!imageReferenceSelections[targetId])imageReferenceSelections[targetId]=eligible.filter(image=>image.selected&&image.kind!=='composition').map(image=>image.asset_id);
   const referenceIds=imageReferenceSelections[targetId].filter(id=>eligible.some(image=>image.asset_id===id));
   imageReferenceSelections[targetId]=referenceIds;
   const figureNames=['图一','图二','图三','图四','图五','图六'];
   const assetUrl=image=>'/assets/shots/'+encodeURIComponent(image.asset_id)+'.img';
   const role=image=>image.kind==='character'?((subjects.find(item=>item.character_id===image.subject_id)?.name||'角色')+' · 角色参考'):image.kind==='environment'?'环境参考':'构图参考';
   const description=kind==='character'?person?.prompt||'':kind==='environment'?source.environment_prompt:[source.character_prompt,source.environment_prompt,source.shot_prompt].filter(Boolean).join('\n\n');
   const card=image=>`<article class="panel" style="padding:14px"><a href="${assetUrl(image)}" target="_blank" rel="noopener"><img src="${assetUrl(image)}" alt="${esc(label)}" style="display:block;width:100%;height:240px;object-fit:contain;background:#f3f5ef;border-radius:8px"></a><p class="muted">${image.width} × ${image.height} · ${esc(image.model)}</p><div class="actions">${image.selected?badge('已采用'):`<button type="button" class="btn" data-adopt-image="${esc(image.asset_id)}">采用此图</button>`}</div><details style="margin-top:12px"><summary class="muted">生成记录 · ${image.reference_assets?.length||0} 张参考图</summary><p style="white-space:pre-wrap" class="muted">${esc(image.prompt)}</p></details></article>`;
   $('image-studio').innerHTML=`<section class="panel"><label class="field">选择分镜<select id="image-shot">${overview.shots.map(item=>`<option value="${esc(item.shot_id)}" ${item.shot_id===shot.shot_id?'selected':''}>${item.order}. ${esc(item.title)}</option>`).join('')}</select></label><p class="muted">${esc(shot.action||'')} · ${esc(shot.shot_size||'')}</p><div class="actions" role="group" aria-label="图片类型" style="margin:20px 0">${[['character','① 角色图'],['environment','② 环境图'],['composition','③ 整体画面']].map(([value,name])=>`<button class="btn ${value===kind?'primary':''}" type="button" data-image-kind="${value}" aria-pressed="${value===kind}" ${value==='character'&&!subjects.length?'disabled':''}>${name}</button>`).join('')}</div>${kind==='character'?`<label class="field">选择角色<select id="image-subject">${subjects.map(item=>`<option value="${esc(item.character_id)}" ${item.character_id===subjectId?'selected':''}>${esc(item.name)}</option>`).join('')}</select></label>`:''}<h3>${esc(label)}</h3><p class="muted">${kind==='character'?'只生成当前角色的独立形象，保持发型、服装与配饰一致。':kind==='environment'?'只生成环境与陈设，不包含人物。':'依据分镜安排人物、动作和构图，可结合角色与环境参考图。'}</p><details><summary>查看${kind==='character'?'沿用后的角色外观':kind==='environment'?'环境描述':'镜头描述'}</summary><p style="white-space:pre-wrap">${esc(description)||'暂无描述，可上传参考图或填写补充要求。'}</p></details><section style="margin-top:24px"><div class="section-head"><div><h3>参考图 <small class="muted"><span id="reference-count">已选 ${referenceIds.length} / 6</span></small></h3><p>选中后按图一、图二编号，补充要求可直接使用编号。更改勾选后请核对编号。上传图片自动归入当前${kind==='character'?'角色':kind==='environment'?'环境':'构图'}参考。</p></div><label class="btn" style="cursor:pointer">＋ 上传参考图<input id="upload-layer-image" type="file" accept="image/png,image/jpeg" style="display:none"></label></div>${eligible.length?`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(125px,1fr));gap:12px">${eligible.map(image=>`<label style="padding:10px;border:1px solid ${referenceIds.includes(image.asset_id)?'#416f4e':'#dce4d7'};border-radius:10px;background:white"><img src="${assetUrl(image)}" alt="${esc(role(image))}" style="width:100%;height:100px;object-fit:contain;display:block"><b data-reference-label="${esc(image.asset_id)}" style="display:block;margin:6px 0">${referenceIds.includes(image.asset_id)?figureNames[referenceIds.indexOf(image.asset_id)]:'未选'}</b><span style="font-size:12px"><input type="checkbox" data-image-reference="${esc(image.asset_id)}" ${referenceIds.includes(image.asset_id)?'checked':''}> ${esc(role(image))}${image.selected?' · 已采用':''}</span></label>`).join('')}</div>`:empty('暂无参考图',kind==='composition'?'可先生成并采用角色图、环境图，或直接上传构图参考。':'可直接按描述生成，也可上传参考图。')}</section><label class="field" style="margin-top:24px">本次补充要求<textarea id="layer-extra" rows="3" maxlength="2000" placeholder="例如：保留图一的人脸和服装，使用图二的环境；人物调整为正面站姿。"></textarea></label><details><summary>全局风格与用途</summary><p style="white-space:pre-wrap">${esc(globalPrompt)}</p></details><div class="actions" style="margin-top:20px"><label>生成数量 <select id="layer-count"><option value="1">1 张</option><option value="2">2 张</option><option value="3">3 张</option><option value="4">4 张</option></select></label><button class="btn primary" type="button" id="generate-layer-image" ${configured?'':'disabled'}>生成${kind==='character'?'角色图':kind==='environment'?'环境图':'整体画面'}</button><span class="muted" id="layer-image-status" role="status">${configured?'仅生成当前类型，其余图片保留。':'请先配置生图模型。'}</span></div></section><section style="margin-top:24px"><h3>${esc(label)} · 图片版本</h3><p class="muted">${images.length} 张 · 采用版本会同步到总览对应位置。</p>${images.length?`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:18px">${images.map(card).join('')}</div>`:empty('尚无图片','生成或上传后，图片会保存在这里。')}</section>`;
   const change=fn=>{if(!leaveDraft())return;dirty=false;fn();paint();};
   $('image-shot').onchange=e=>change(()=>{shot=overview.shots.find(item=>item.shot_id===e.target.value);});
   document.querySelectorAll('[data-image-kind]').forEach(button=>button.onclick=()=>change(()=>{target.kind=button.dataset.imageKind;}));
   if($('image-subject'))$('image-subject').onchange=e=>change(()=>{target.subject_id=e.target.value;});
   $('layer-extra').oninput=()=>{dirty=!!$('layer-extra').value;};
   document.querySelectorAll('[data-image-reference]').forEach(input=>input.onchange=()=>{
    const prior=imageReferenceSelections[targetId];
    const values=input.checked?[...prior,input.dataset.imageReference]:prior.filter(id=>id!==input.dataset.imageReference);
    if(values.length>6){input.checked=false;notify('最多选择 6 张参考图。');return;}
    imageReferenceSelections[targetId]=values;
    document.querySelectorAll('[data-reference-label]').forEach(label=>{const index=values.indexOf(label.dataset.referenceLabel);label.textContent=index>=0?figureNames[index]:'未选';});
    $('reference-count').textContent=`已选 ${values.length} / 6`;
    if($('layer-extra').value.trim())notify('参考图编号已更新，请核对补充要求中的图一、图二。');
   });
   const body=()=>({run_id:overview.run.id,shot_id:shot.shot_id,source_hash:source.source_hash,global_prompt:globalPrompt,kind,subject_id:subjectId,reference_assets:imageReferenceSelections[targetId],extra:$('layer-extra').value.trim(),count:Number($('layer-count').value)});
   $('generate-layer-image').onclick=()=>guarded(async()=>{
    const button=$('generate-layer-image'),note=$('layer-image-status');button.disabled=true;button.textContent='正在生成…';note.textContent='生成中，参考图与提示词已提交。';
    try{const result=await api('generate-shot-image',body());if(active()){dirty=false;await renderShotImages();}notify(`已生成 ${result.asset_ids.length} 张图片。`);}
    catch(error){if(note.isConnected)note.textContent=error.message;throw error;}
    finally{if(button.isConnected){button.disabled=!configured;button.textContent='重新生成';}}
   });
   $('upload-layer-image').onchange=e=>guarded(async()=>{
    const file=e.target.files[0];if(!file)return;
    if(!['image/png','image/jpeg'].includes(file.type)||file.size>8000000){notify('请上传 8 MB 以内的 PNG 或 JPEG。');return;}
    const dataUrl=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});
    const result=await api('upload-shot-image',{...body(),reference_assets:[],data_url:dataUrl});
    imageReferenceSelections[targetId]=[...new Set([...imageReferenceSelections[targetId],...result.asset_ids])].slice(-6);
    if(active()){dirty=false;await renderShotImages();}notify('参考图已上传并选中。');
   });
   document.querySelectorAll('[data-adopt-image]').forEach(button=>button.onclick=()=>guarded(async()=>{
    if(!leaveDraft())return;
    await api('select-shot-image',{run_id:overview.run.id,shot_id:shot.shot_id,asset_id:button.dataset.adoptImage});
    delete imageReferenceSelections[targetKey+':composition:'];
    if(active()){dirty=false;await renderShotImages();}notify('已采用，角色、环境和整体画面各自保留采用版本。');
   }));
  };paint();
 }catch(error){if(active())$('image-studio').innerHTML=empty('暂时无法读取分镜',error.message||'请稍后刷新。');}
}

let modelCategory='image';
let promptCategory='image';
function renderSettingsTabs(pages,category,onSelect){
 let nav=$('settings-tabs');
 if(!nav){nav=document.createElement('nav');nav.id='settings-tabs';nav.className='subnav';$('text-tabs').after(nav);}
 nav.classList.remove('hidden');nav.setAttribute('aria-label',route==='models'?'模型配置页面':'提示词配置页面');
 nav.innerHTML=Object.entries(pages).map(([key,label])=>`<button type="button" data-settings-page="${key}" class="${key===category?'active':''}" ${key===category?'aria-current="page"':''}>${label}</button>`).join('');
 nav.querySelectorAll('button').forEach(button=>button.onclick=()=>onSelect(button.dataset.settingsPage));
}
function showModelPage(){
 $('simple-model').closest('section').classList.toggle('hidden',modelCategory!=='text');
 const imagePanel=$('image-model-form').closest('section');
 imagePanel.classList.toggle('hidden',modelCategory!=='image');imagePanel.style.margin='0 auto';
 renderSettingsTabs({image:'生图模型配置',text:'文本模型配置'},modelCategory,category=>{modelCategory=category;showModelPage();});
}
const promptDrafts={};
const textPromptNames={analysis:'本章分析',outline:'改编大纲',shots:'分镜与提示词',review:'结果复核'};
const promptPages={image:'图像创作配置',text:'分镜拆解配置',video:'视频制作配置'};
function savedPromptValues(category){return category==='text'?{...data.templates}:{template:data.media_templates?.[category]||''};}
function promptsDirty(){return Object.entries(promptDrafts).some(([category,values])=>Object.entries(values).some(([key,value])=>value!==savedPromptValues(category)[key]));}
window.renderPrompts=function(){
 const category=promptCategory;
 renderSettingsTabs(promptPages,category,next=>{promptCategory=next;renderPrompts();});
 const values=promptDrafts[category]||(promptDrafts[category]=savedPromptValues(category));
 const names=category==='text'?textPromptNames:{template:category==='image'?'全局图片风格与应用场景':'视频生成提示词'};
 const help=category==='image'?'统一描述图片风格、应用场景、构图偏好和画面限制。人物与环境描述由所选分镜自动带入，无需在这里填写角色或地点。保存后用于下一次生成。':category==='text'?'保存后用于新提交的文本任务；已提交任务保留原提示词。':'视频提示词可独立保存；视频生成服务尚未接入，此配置暂不参与生成。';
 $('workspace').innerHTML=`<div class="section-head"><div><div class="eyebrow">SETTINGS / PROMPTS</div><h2>${promptPages[category]}</h2><p>${help}</p></div></div><form id="prompt-form"><div class="prompt-grid" ${category==='text'?'':'style="grid-template-columns:1fr"'}>${Object.entries(names).map(([key,label])=>`<section class="panel"><h3>${label}</h3><label class="field"><span class="sr-only">${label}</span><textarea id="prompt-${key}" rows="14" maxlength="20000" required>${esc(values[key]||'')}</textarea></label></section>`).join('')}</div><div class="actions"><button class="btn primary" type="submit">保存${promptPages[category]}</button><span class="muted" role="status" id="prompt-feedback">切换分类会保留本次未保存的编辑。</span></div></form>`;
 for(const key of Object.keys(names))$('prompt-'+key).oninput=e=>{values[key]=e.target.value;dirty=promptsDirty();};
 $('prompt-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{
  const submitted=Object.fromEntries(Object.keys(names).map(key=>[key,$('prompt-'+key).value.trim()]));
  await api('prompts',{category,...submitted});
  if(category==='text')data.templates=submitted;else{data.media_templates=data.media_templates||{};data.media_templates[category]=submitted.template;}
  promptDrafts[category]=submitted;dirty=promptsDirty();renderPrompts();notify(promptPages[category]+'已保存。');
 });};
};

window.renderModels = function (){
 const p=data.profiles.find(p=>p.id===data.activeProfile)||data.profiles[0]||{};
 if(!document.getElementById('cloud-key-style')){
  const style=document.createElement('style');
  style.id='cloud-key-style';
  style.textContent='.key-wrap{position:relative;flex:1;min-width:0}.key-wrap input{padding-right:34px}#key-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);width:22px;height:22px;margin:0;padding:0;border:0;border-radius:50%;background:transparent;color:#6d7b70;font-size:18px;line-height:20px;cursor:pointer}#key-clear:hover:not(:disabled){background:#e6eee7;color:#243028}#key-clear:disabled{opacity:.35;cursor:default}#key-clear:active{transform:translateY(-50%)}';
  document.head.appendChild(style);
 }
 const modelOptions=p.model?`<option value="">请选择模型</option><option value="${esc(p.model)}" selected>${esc(p.model)}</option>`:'<option value="">请选择模型</option>';
 $('workspace').innerHTML=`<section class="panel" style="max-width:820px;margin:auto"><form id="simple-model" autocomplete="off"><div class="section-head"><div><h2>文本模型配置</h2><p>用于章节分析、改编大纲、分镜和文本复核。</p></div><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" role="switch" aria-label="启用文本模型" id="model-enabled" ${p.enabled!==false?'checked':''}></label></div>${data.profiles.length>1?`<label class="field">当前配置<select id="simple-profile">${data.profiles.map(x=>`<option value="${esc(x.id)}" ${x.id===p.id?'selected':''}>${esc(x.name)} · ${esc(x.model)}</option>`).join('')}</select></label>`:''}<label class="field">API 地址<input id="simple-url" type="url" required value="${esc(p.url||'')}" placeholder="https://api.example.com/v1"><small>填写服务根地址，系统会请求该地址下的 chat/completions。</small></label><div style="display:grid;grid-template-columns:minmax(0,1fr) 120px;gap:16px"><label class="field">模型名称<select id="simple-name" required>${modelOptions}</select><small>填写 API 地址和 API Key 后自动获取列表，再从下拉框选择。</small></label><label class="field">温度<input id="simple-temperature" type="number" min="0" max="2" step="0.1" required value="${p.temperature??1}"><small>0 到 2，部分模型只接受 1。</small></label></div><label class="field">超时（秒）<input id="simple-timeout" type="number" min="10" max="600" required value="${p.timeout||120}"><small>10 到 600 秒。上下文和输出 token 不再设上限，由模型服务自己的窗口决定。</small></label><label class="field">API Key<div style="display:flex;gap:8px;align-items:center"><div class="key-wrap"><input id="simple-key" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${p.key_configured?'已配置，留空则不修改':'填写 API Key'}"><button type="button" id="key-clear" aria-label="清除 API Key">×</button></div><button class="btn" type="button" style="white-space:nowrap" id="toggle-key" aria-label="显示输入的密钥">显示</button></div><small id="key-status">${p.key_configured?'已配置':'尚未配置'}。填写后仅显示后四位；已保存的密钥不会完整回显。</small></label><div class="actions"><button class="btn primary" type="submit">保存设置</button><span class="muted" id="model-feedback" role="status"></span></div></form></section>`;
 $('workspace').insertAdjacentHTML('beforeend',`<section class="panel" style="max-width:820px;margin:20px auto"><form id="image-model-form" autocomplete="off"><div class="section-head"><div><h2>生图模型配置</h2><p>可使用 OpenAI 兼容 API 或本机 FLUX.2。</p></div>${badge(data.image_profile?.backend==='mflux'?'本地 FLUX.2':data.image_profile?.key_configured?'已配置':'待配置',data.image_profile?.backend==='mflux'||data.image_profile?.key_configured?'':'amber')}</div><label class="field">运行方式<select id="image-profile-backend"><option value="openai" ${data.image_profile?.backend==='mflux'?'':'selected'}>OpenAI 兼容 API</option><option value="mflux" ${data.image_profile?.backend==='mflux'?'selected':''}>本地 FLUX.2（mflux）</option></select></label><label class="field">配置名称<input id="image-profile-name" maxlength="100" required value="${esc(data.image_profile?.name||'FLUX.2-klein-4B Q8')}"></label><div id="image-api-fields"><label class="field">API Base URL<input id="image-profile-url" type="url" placeholder="https://api.example.com/v1" value="${esc(data.image_profile?.base_url||'')}"><small>使用 OpenAI 兼容的 /images/generations 接口。</small></label></div><div id="image-local-fields"><div class="field-row"><label class="field">模型 ID<input id="image-profile-model" maxlength="200" value="${esc(data.image_profile?.model||'mflux-community/flux2-klein-4b-mflux-q8')}"></label><label class="field">基础模型<input id="image-profile-base-model" value="${esc(data.image_profile?.base_model||'flux2-klein-4b')}"></label></div><label class="field">mflux 可执行文件<input id="image-profile-executable" value="${esc(data.image_profile?.executable||'/Users/apple/flux2-klein/.venv/bin/mflux-generate-flux2')}"><small>本机 FLUX.2-klein 通过 mflux 在 Apple Silicon 上运行。</small></label></div><div class="field-row"><label class="field">超时（秒）<input id="image-profile-timeout" type="number" min="10" max="1800" value="${data.image_profile?.timeout||600}" required></label><label class="field" id="image-key-field">API Key<input id="image-profile-key" type="password" autocomplete="new-password" placeholder="${data.image_profile?.key_configured?'已配置，留空保持不变':'填写生图 API Key'}"><small id="image-profile-key-state">${data.image_profile?.key_configured?`已配置，尾号 ${esc(data.image_profile.key_tail||'')}`:'尚未配置'}</small></label></div><div class="actions"><button class="btn primary" type="submit">保存生图配置</button><button class="btn" type="button" id="image-profile-clear" ${data.image_profile?.key_configured?'':'disabled'}>清除 API Key</button><span class="muted" id="image-profile-feedback" role="status"></span></div></form></section>`);
 showModelPage();
 const backendSelect=$('image-profile-backend');const updateBackend=()=>{const local=backendSelect.value==='mflux';$('image-local-fields').hidden=!local;$('image-api-fields').hidden=local;$('image-key-field').hidden=local;$('image-profile-url').required=!local;$('image-profile-model').required=local;};backendSelect.onchange=updateBackend;updateBackend();
 $('image-model-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{const local=backendSelect.value==='mflux';let base_url='';if(!local){const url=new URL($('image-profile-url').value.trim());if(!['https:','http:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw Error('API 地址格式无效。');base_url=url.href.replace(/\/$/,'');}await api('image-profile',{name:$('image-profile-name').value.trim(),backend:local?'mflux':'openai',base_url,model:$('image-profile-model').value.trim(),base_model:$('image-profile-base-model').value.trim(),executable:$('image-profile-executable').value.trim(),timeout:Number($('image-profile-timeout').value),api_key_env:'YINGZHANG_IMAGE_API_KEY',api_key:$('image-profile-key').value});await refreshCloud();renderModels();notify('生图模型配置已保存。');});};
 $('image-profile-clear').onclick=()=>guarded(async()=>{await api('image-profile',{name:$('image-profile-name').value.trim(),backend:backendSelect.value,base_url:$('image-profile-url').value.trim(),model:$('image-profile-model').value.trim(),base_model:$('image-profile-base-model').value.trim(),executable:$('image-profile-executable').value.trim(),timeout:Number($('image-profile-timeout').value),api_key_env:'YINGZHANG_IMAGE_API_KEY',api_key:'',clear_key:true});await refreshCloud();renderModels();notify('生图模型 API Key 已清除。');});
 let typedKey='';
 let clearKey=false;
 let revealed=false;
 let listedFor='';
 let listing='';
 let fetchToken=0;
 const mask=value=>value?('••••••••'+(value.length>4?value.slice(-4):value)):'';
 const paintKey=()=>{
  const input=$('simple-key');
  if(revealed&&typedKey)input.value=typedKey;
  else if(typedKey)input.value=mask(typedKey);
  else if(!clearKey&&p.key_configured)input.value='••••••••'+(p.key_tail||'');
  else input.value='';
  $('key-clear').disabled=!(typedKey||(!clearKey&&p.key_configured));
 };
 const payload=forList=>{
  const model=$('simple-name').value.trim();
  return {id:p.id||'online',url:$('simple-url').value.trim(),model:model||(forList?(p.model||'catalog'):''),timeout:Number($('simple-timeout').value),temperature:Number($('simple-temperature').value),enabled:$('model-enabled').checked,key:typedKey,clear_key:clearKey};
 };
 const fillSelect=models=>{
  const select=$('simple-name');
  if(!select?.isConnected)return;
  const current=select.value||p.model||'';
  const ids=Array.isArray(models)?models.slice():[];
  if(current&&!ids.includes(current))ids.unshift(current);
  select.innerHTML=`<option value="">请选择模型</option>`+ids.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('');
  select.value=current&&ids.includes(current)?current:'';
 };
 const loadModels=async()=>{
  const select=$('simple-name');
  const url=$('simple-url').value.trim();
  const hasKey=!!typedKey||(!clearKey&&p.key_configured);
  if(!select||!url||!hasKey)return;
  let parsed;
  try{parsed=new URL(url);}catch{return;}
  if(parsed.protocol!=='https:'||parsed.username||parsed.password||parsed.search||parsed.hash){
   $('model-feedback').textContent='请填写不含账号、查询参数的 HTTPS API 地址。';
   return;
  }
  const signature=url+'\n'+(typedKey||(clearKey?'':'saved'));
  if(signature===listedFor||signature===listing)return;
  listing=signature;
  const token=++fetchToken;
  $('model-feedback').textContent='正在获取模型列表…';
  try{
   const result=await api('simple-profile',{...payload(true),list_models:true});
   if(!select.isConnected||token!==fetchToken)return;
   listedFor=signature;
   listing='';
   fillSelect(result.models);
   $('model-feedback').textContent=`已获取 ${result.models.length} 个模型，请从下拉列表选择。`;
  }catch(e){
   if(!select.isConnected||token!==fetchToken)return;
   listing='';
   $('model-feedback').textContent=e.message||'获取模型列表失败，请核对地址与密钥。';
  }
 };
 if($('simple-profile'))$('simple-profile').onchange=e=>{data.activeProfile=e.target.value;renderModels();};
 $('toggle-key').onclick=()=>{
  if(!typedKey){notify('已保存的密钥不会完整回显，仅显示后四位。');return;}
  revealed=!revealed;
  $('toggle-key').textContent=revealed?'隐藏':'显示';
  paintKey();
 };
 $('key-clear').onmousedown=e=>e.preventDefault();
 $('key-clear').onclick=()=>{
  typedKey='';
  revealed=false;
  $('toggle-key').textContent='显示';
  if(p.key_configured)clearKey=true;
  listedFor='';
  listing='';
  paintKey();
  $('key-status').textContent=p.key_configured?'保存设置后清除密钥。':'请填写 API Key 并保存。';
 };
 $('simple-key').onfocus=()=>{if(!(revealed&&typedKey))$('simple-key').value=typedKey;};
 $('simple-key').oninput=()=>{
  typedKey=$('simple-key').value;
  clearKey=false;
  listedFor='';
  $('key-clear').disabled=!(typedKey||(!clearKey&&p.key_configured));
  $('key-status').textContent=typedKey?'已输入新密钥，离开输入框后仅显示后四位。':(p.key_configured?'已保存密钥，留空则保留。':'请填写 API Key 并保存。');
 };
 $('simple-key').onblur=()=>{paintKey();loadModels();};
 $('simple-url').onblur=()=>loadModels();
 $('simple-model').onsubmit=e=>{
  e.preventDefault();
  if(!$('simple-name').value.trim()){notify('请先从列表选择模型。');return;}
  guarded(async()=>{await api('simple-profile',payload(false));typedKey='';await refreshCloud();renderModels();notify('模型设置已保存，下一次任务使用新配置。');});
 };
 paintKey();
 loadModels();
}

const statuses={stopped:'已停止',stop_requested:'正在停止',stage_complete:'本步已完成，待确认',pending:'等待执行',running:'执行中',pause_requested:'请求暂停',paused:'已暂停',succeeded:'已完成',review_required:'待审阅',failed:'失败',unknown:'执行状态待核实'};
const pipeline=[['prepare','准备原文'],['analysis','本章分析'],['outline','改编大纲'],['shots','分镜工作台'],['review','复核']];
function stageKey(stage){const name=String(stage||'prepare').split(' ')[0];return pipeline.some(([key])=>key===name)?name:'prepare';}
function stageDetail(stage,run){const raw=String(stage||'prepare').trim();const key=stageKey(raw);const label=pipeline.find(([id])=>id===key)[1];const result=run?.result||{};const scenes=(result.outline?.scenes||[]).length;if(key==='review'&&scenes){const done=Object.keys(result.review_parts||{}).length;return `${label} ${Math.min(done+(result.review?0:1),scenes)}/${scenes}`;}if(key==='shots'&&scenes){const done=Object.keys(result.shot_batches||{}).length;if(done&&done<scenes)return `${label} ${Math.min(done+1,scenes)}/${scenes}`;}const rest=raw.slice(key.length).trim();if(!rest)return label;if(key==='analysis'&&/^\d+\/\d+$/.test(rest))return `${label} ${rest}`;return `${label} · ${rest}`;}
function stepStates(run){const result=run?.result||{};const done={prepare:Array.isArray(result.paragraphs)&&result.paragraphs.length>0,analysis:!!result.analysis,outline:!!result.outline,shots:stageFinished(run,'shots'),review:!!result.review};const current=run?stageKey(run.stage):'';const live=!!run&&['pending','running','pause_requested','stop_requested'].includes(run.status);const failed=!!run&&['failed','unknown'].includes(run.status);const paused=!!run&&run.status==='paused';const finished=!!run&&['succeeded','review_required'].includes(run.status);return pipeline.map(([key,label])=>{let state='等待';if((finished&&!run.document?.target_stage)||done[key]&&key!==current)state='已完成';else if(key===current&&live)state=run.status==='pending'?'即将开始':run.status==='pause_requested'?'正在暂停':run.status==='stop_requested'?'正在停止':'正在执行';else if(key===current&&paused)state='已暂停';else if(key===current&&failed)state='失败';else if(done[key])state='已完成';return {key,label,state,current:!!run&&key===current};});}
function runningLine(run,steps){if(!run)return '';const name=stageDetail(run.stage,run);if(run.status==='pending')return `即将开始：${name}`;if(run.status==='running')return `正在执行：${name}`;if(run.status==='pause_requested')return `正在暂停：${name}`;if(run.status==='stop_requested')return `正在停止：${name}`;if(run.status==='stopped')return `已停止：${name}`;if(run.status==='paused')return `已暂停：${name}`;if(run.status==='failed'||run.status==='unknown')return `停在：${name}`;if(run.status==='stage_complete')return `${stageNames[(run.document?.selected_stages||[run.document?.target_stage]).at(-1)]||name}已完成，${stageApproved(run,(run.document?.selected_stages||[run.document?.target_stage]).at(-1))?'已确认，可手动进入下一步':'等待确认'}`;if(run.status==='review_required')return '复核已完成，待审阅';if(run.status==='succeeded')return '全部阶段已完成';return steps.find(step=>step.current)?.label||'';}
function paintRunNow(line,active){const note=document.querySelector(`[data-text-page="${tab}"] #run-now`);if(!note)return;note.textContent=line;note.style.color=active?'#2d503d':'';note.style.fontWeight=active?'600':'400';}
function statusColumn(){return document.getElementById('page-status');}
const selectedPipelineSteps=new Map();
function progressStyles(){
 if(document.getElementById('pipeline-step-style'))return;
 const style=document.createElement('style');style.id='pipeline-step-style';
 style.textContent=`.pipeline-steps{list-style:none;margin:16px 0;padding:0;font-size:13px}.pipeline-step{position:relative;display:flex;align-items:center;gap:12px;min-height:42px;color:#8a968d}.pipeline-step:not(:last-child):after{content:"";position:absolute;left:7px;top:23px;bottom:-19px;width:2px;background:#d6dfd2}.pipeline-dot{position:relative;flex:0 0 16px;width:16px;height:16px;box-sizing:border-box;border:2px solid #8a968d;border-radius:50%;background:#f4f6ef;padding:0;cursor:pointer;z-index:1}.pipeline-dot[aria-pressed="true"]{background:#2d503d;border-color:#2d503d}.pipeline-dot:focus-visible{outline:3px solid #a6bd90;outline-offset:4px}.pipeline-dot:disabled{cursor:default}.pipeline-dot.is-running:before{content:"";position:absolute;inset:-5px;border-radius:50%;border:2px solid #cedbc8;border-top-color:#437c50;border-right-color:#76a968;animation:pipeline-spin 1s linear infinite;box-shadow:0 0 8px #8aaf7666}.pipeline-step.is-running{color:#2d503d;font-weight:600}.pipeline-step.is-complete{color:#617654}.pipeline-step.is-failed{color:#9a5348}.pipeline-step-state{margin-left:auto;font-size:11px;font-weight:400}.pipeline-dot.is-complete:not([aria-pressed="true"]):after{content:"✓";position:absolute;inset:0;font-size:10px;line-height:12px;color:#617654}.pipeline-help{font-size:11px;line-height:1.8;color:#7c897b;margin:12px 0}.pipeline-steps .pipeline-dot{appearance:none}@keyframes pipeline-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.pipeline-dot.is-running:before{animation-duration:3s}}`;
 document.head.appendChild(style);
}
async function launchSelectedSteps(run){
 if(dirty){notify('请先保存当前编辑。');return;}
 if(!chapterId){notify('请先保存章节原文。');return;}
 const chosen=selectedPipelineSteps.get(chapterId)||[];
 const stages=pipeline.map(([stage])=>stage).filter(stage=>stage!=='prepare'&&chosen.includes(stage));
 if(!stages.length){notify('请选择至少一个生成步骤；准备原文会随所选步骤自动执行。');return;}
 const first=pipeline.findIndex(([stage])=>stage===stages[0]);
 if(!stages.every((stage,index)=>pipeline[first+index]?.[0]===stage)){notify('请选择连续相邻的步骤，不相邻步骤请分开启动。');return;}
 const previous=pipeline.slice(1,first).map(([stage])=>stage);
 if(!previous.every(stage=>stageApproved(run,stage))){notify('请先完成并确认前置步骤，或将前置步骤一起勾选。');return;}
 const profile=data.activeProfile||data.profiles[0]?.id;
 if(!profile){notify('请先配置文本模型。');return;}
 if(!confirm(`即将执行：${stages.map(stage=>stageNames[stage]).join(' → ')}。所选步骤将连续执行，中间逐步保存，全部完成后等待你检查确认。模型调用可能计费。`))return;
 guarded(async()=>{
  await api('run',{chapter_id:chapterId,profile_id:profile,style:current().style,aspect_ratio:'16:9',preference:'',idempotency_key:uid(),stage:stages[0],selected_stages:stages,source_run_id:run?.id,source_revision:run?.revision});
  openStagePage(stages[0]);notify('已启动所选步骤，每一步完成后都会保存。');
 });
}
async function showProgress(){
 const aside=statusColumn();if(!aside)return;progressStyles();
 const id=chapterId,key=tab;clearTimeout(showProgress.timer);
 if(showProgress.chapter!==id){showProgress.signature='';showProgress.chapter=id;aside.dataset.ready='';}
 const paint=run=>{
  const steps=stepStates(run),line=runningLine(run,steps);
  const live=!!run&&['pending','running','pause_requested','stop_requested'].includes(run.status);
  const active=live||run?.status==='paused';
  const attention=active||!!run&&['failed','unknown'].includes(run.status);
  paintRunNow(attention?line:'',active);
  if(!selectedPipelineSteps.has(id)&&run?.document?.selected_stages)selectedPipelineSteps.set(id,[...run.document.selected_stages]);
  const chosen=selectedPipelineSteps.get(id)||[];
  const signature=(run?`${run.id}|${run.status}|${run.stage}|${run.updated_at||''}|${run.error_message||''}`:'none')+'|'+chosen.join(',');
  if(signature===showProgress.signature&&aside.dataset.ready==='1'){
   if(live)showProgress.timer=setTimeout(showProgress,3000);return;
  }
  showProgress.signature=signature;aside.dataset.ready='1';
  if(['analysis','outline','shots'].includes(key)&&!dirty)renderResults();
  const actions=run&&['pending','running'].includes(run.status)?`<button class="btn" data-run-action="pause" data-run-id="${run.id}" type="button">暂停</button>`:run&&['paused','pause_requested'].includes(run.status)?`<button class="btn" data-run-action="resume" data-run-id="${run.id}" type="button">继续</button>`:run&&['failed','unknown','stopped'].includes(run.status)?`<button class="btn" data-run-action="retry" data-run-id="${run.id}" type="button">继续未完成部分 · 可能计费</button>`:'';
  const rows=steps.map(step=>{
   const executing=live&&step.current;
   return `<li class="pipeline-step ${executing?'is-running':''} ${step.state==='已完成'?'is-complete':''} ${step.state==='失败'?'is-failed':''}"><button type="button" class="pipeline-dot ${executing?'is-running':''} ${step.state==='已完成'?'is-complete':''}" data-select-stage="${step.key}" aria-label="选择${esc(step.label)}" aria-pressed="${chosen.includes(step.key)}" ${live?'disabled':''}></button><span>${esc(step.label)}</span><span class="pipeline-step-state">${esc(step.state)}</span></li>`;
  }).join('');
  aside.innerHTML=`<div class="eyebrow">分步执行进度</div><h3>${esc(line||'选择步骤后启动')}</h3><ul class="pipeline-steps">${rows}</ul><p class="pipeline-help">点击圆点选择步骤，绿色表示已选；相邻步骤可一起启动。准备原文会自动执行，每一步独立保存。</p>${run&&run.status==='failed'&&run.error_message?`<p class="muted">${esc(run.error_message)}</p>`:''}<div class="actions"><button class="btn primary" id="start-selected-stages" type="button" ${live||!id||!chosen.some(stage=>stage!=='prepare')?'disabled':''}>启动</button>${actions}${run&&['pending','running','pause_requested','paused'].includes(run.status)?`<button class="btn" type="button" data-run-action="stop" data-run-id="${run.id}">停止</button>`:''}<button class="btn" id="refresh-progress" type="button">刷新</button></div>`;
  aside.querySelector('#refresh-progress').onclick=showProgress;
  aside.querySelector('#start-selected-stages').onclick=()=>launchSelectedSteps(run);
  aside.querySelectorAll('[data-select-stage]').forEach(button=>button.onclick=()=>{
   const stage=button.dataset.selectStage;
   selectedPipelineSteps.set(id,chosen.includes(stage)?chosen.filter(item=>item!==stage):[...chosen,stage]);
   showProgress.signature='';paint(run);
  });
  aside.querySelectorAll('[data-run-action]').forEach(button=>button.onclick=()=>{
   if(button.dataset.runAction==='stop'&&!confirm('停止此任务并保留已保存结果；已发出的模型请求需等待返回，不再执行后续步骤。确定停止？'))return;
   if(button.dataset.runAction==='retry'&&!confirm('将跳过已保存结果，继续所选步骤中未完成的部分；新请求可能计费，确定继续？'))return;
   guarded(async()=>{await api(button.dataset.runAction,{id:button.dataset.runId});showProgress();});
  });
  if(live)showProgress.timer=setTimeout(showProgress,3000);
 };
 if(!id){paint(null);return;}
 try{const runs=await api('runs/'+id);if(route!=='text'||chapterId!==id||tab!==key)return;paint(runs[0]||null);}
 catch(error){if(chapterId===id&&tab===key)aside.innerHTML=`<div class="eyebrow">分步执行进度</div><h3>进度暂时无法读取</h3><p class="muted">${esc(error.message||'请稍后刷新。')}</p>`;}
}
window.renderBook = function (){if(tab==='tasks')tab='source';originalRenderBook();cloudCopy($('text-pages'));document.querySelector('[data-text="tasks"]')?.remove();const key=tab;const finish=()=>{if(route==='text'&&tab===key){if(key==='source')ensureRunButton();showProgress();}};if(['analysis','outline','shots'].includes(key))renderResults().finally(finish);else finish();}
function stageHtml(key,result){const missing=empty(key==='outline'?'这一版还没有改编大纲':key==='shots'?'这一版还没有镜头':'这一版还没有本章分析','换一个执行版本，或等当前任务保存后再打开。');if(!result)return missing;if(key==='outline'){const outline=result.outline;if(!outline)return missing;const scenes=(outline.scenes||[]).map((scene,index)=>`<article style="padding:16px 0;border-top:1px solid #e4eadc"><h3 style="margin:0 0 6px">${index+1}. ${esc(scene.title||scene.scene_id||'场景')}</h3><p class="muted">${esc([scene.location,scene.time].filter(Boolean).join(' · '))}</p><p style="white-space:pre-wrap;margin:8px 0 0">${esc(scene.script||'')}</p></article>`).join('');const changes=(outline.changes||[]).map(item=>`<li>${esc(item)}</li>`).join('');return `<p style="white-space:pre-wrap">${esc(outline.synopsis||'')}</p>${changes?`<h3>改编取舍</h3><ul>${changes}</ul>`:''}${scenes||missing}`;}if(key==='shots'){const shots=result.shots||[];if(!shots.length)return missing;const titles=Object.fromEntries((result.outline?.scenes||[]).map(scene=>[scene.scene_id,scene.title]));let sceneId='';return shots.map(shot=>{const heading=shot.scene_id===sceneId?'':(sceneId=shot.scene_id,`<h3 style="margin:22px 0 8px">${esc(titles[shot.scene_id]||shot.scene_id||'场景')}</h3>`);const dialogue=(shot.dialogue||[]).map(line=>`<p>对白：${esc(line)}</p>`).join('');return `${heading}<article style="padding:12px 0;border-top:1px solid #e4eadc"><b>${esc(shot.order||'')}. ${esc(shot.title||shot.shot_id||'')}</b><p class="muted">${esc([shot.shot_size,shot.camera,shot.duration_seconds?`${shot.duration_seconds} 秒`:''].filter(Boolean).join(' · '))}</p><p>${esc(shot.story_purpose||'')}</p><p>${esc(shot.action||'')}</p><details><summary>视频提示词</summary><p style="white-space:pre-wrap">${esc(shot.video_prompt||'')}</p></details>${dialogue}<div style="margin-top:16px;padding-top:12px;border-top:1px dashed #dce4d7"><b>角色外观</b>${(shot.character_prompts||[]).length?(shot.character_prompts||[]).map(person=>`<p style="white-space:pre-wrap">${esc((result.analysis?.characters||[]).find(item=>item.character_id===person.character_id)?.name||person.character_id)}：${esc(person.prompt||'')}</p>`).join(''):`<p style="white-space:pre-wrap;min-height:1em">${esc(shot.character_prompt||'')}</p>`}<b>环境描述</b><p style="white-space:pre-wrap;min-height:1em">${esc(shot.environment_prompt||'')}</p></div></article>`;}).join('');}const analysis=result.analysis;if(!analysis)return missing;const characters=(analysis.characters||[]).map(item=>`<li><b>${esc(item.name||'')}</b> ${esc(item.description||'')}</li>`).join('');const events=(analysis.events||[]).map(item=>`<li>${esc(item.description||'')}</li>`).join('');return `<p style="white-space:pre-wrap">${esc(analysis.summary||'')}</p>${analysis.core_conflict?`<p><b>核心冲突</b> ${esc(analysis.core_conflict)}</p>`:''}${characters?`<h3>人物</h3><ul>${characters}</ul>`:''}${events?`<h3>事件</h3><ul>${events}</ul>`:''}`;}
const stageNames={analysis:'本章分析与角色资料',outline:'改编大纲',shots:'分镜',review:'复核'};
const nextStage={analysis:'outline',outline:'shots',shots:'review'};
const stagePages={analysis:'analysis',outline:'outline',shots:'shots',review:'shots'};
function stageFinished(run,key){return (run?.completed_stages||[]).includes(key);}
function stageApproved(run,key){return stageFinished(run,key)&&!!run?.result?.confirmed_stages?.[key]&&!run?.stale_stages?.includes(key);}
function openStagePage(stage){if(!leaveDraft())return;dirty=false;tab=stagePages[stage]||'analysis';renderBook();}
function submitStage(stage,source){
 if(dirty){notify('请先保存当前编辑。');return;}
 if(!chapterId){notify('请先保存章节。');return;}
 const profile=data.activeProfile||data.profiles[0]?.id;
 if(!profile){notify('请先配置文本模型。');return;}
 if(!confirm(`只执行「${stageNames[stage]}」，完成后等待你确认，不会自动进入下一步。此请求可能产生模型费用。`))return;
 guarded(async()=>{
  await api('run',{chapter_id:chapterId,profile_id:profile,style:current().style,aspect_ratio:'16:9',preference:'',idempotency_key:uid(),stage,source_run_id:source?.id,source_revision:source?.revision});
  openStagePage(stage);notify('本步骤已提交，完成后请审阅并确认。');
 });
}
function ensureRunButton(){
 if(tab!=='source')return;
 const host=document.querySelector('[data-text-page="source"]');
 if(!host||host.querySelector('#submit-cloud-task'))return;
 const button=document.createElement('button');button.type='button';button.className='btn';button.id='submit-cloud-task';button.textContent='下一步：本章分析与角色资料';button.onclick=()=>{if(!chapterId){notify('请先保存章节。');return;}openStagePage('analysis');};
 host.querySelector('.actions')?.appendChild(button);
}
function stageControls(head,key){
 const busy=head&&['pending','running','pause_requested','stop_requested'].includes(head.status);
 const prerequisites={analysis:[],outline:['analysis'],shots:['analysis','outline'],review:['analysis','outline','shots']};
 const enabled=stage=>!busy&&prerequisites[stage].every(prior=>stageApproved(head,prior));
 const currentResult=head?.result?.[key];
 const confirmed=stageApproved(head,key);
 const canConfirm=!busy&&stageFinished(head,key)&&!head?.stale_stages?.includes(key)&&!confirmed;
 let html=`<div class="notice">每次只执行当前步骤。完成后先检查并确认，再手动开始下一步；旧版本和已保存进度都会保留。</div><div class="actions" style="margin-bottom:18px"><button class="btn primary" type="button" data-start-stage="${key}" ${enabled(key)?'':'disabled'}>${currentResult?'重新生成':'生成'}${stageNames[key]}</button>`;
 if(currentResult)html+=`<button class="btn" type="button" id="confirm-stage" ${canConfirm?'':'disabled'}>${confirmed?'已确认本阶段':'确认本阶段结果'}</button>`;
 const next=nextStage[key];
 if(next)html+=`<button class="btn" type="button" data-start-stage="${next}" ${enabled(next)?'':'disabled'}>下一步：${next==='review'?'执行复核':'生成'+stageNames[next]}</button>`;
 html+='</div>';
 if(key==='shots'&&head?.result?.review)html+=`<section class="notice"><div><b>复核结果</b>${(head.result.review.issues||[]).length?head.result.review.issues.map(issue=>`<p>${esc(issue)}</p>`).join(''):'<p>模型未报告复核问题。</p>'}${(head.validation_issues||[]).length?`<p>${esc(head.validation_issues.join('；'))}</p>`:''}</div></section>`;
 return html;
}
const selectedResultRuns={};
async function renderResults(){
 const id=chapterId,key=tab;if(!id)return;
 try{
  const runs=await api('runs/'+id);
  if(route!=='text'||chapterId!==id||tab!==key)return;
  const host=document.querySelector(`[data-text-page="${key}"]`),head=runs[0];
  const available=runs.filter(run=>key==='shots'?(run.result?.shots||[]).length:!!run.result?.[key]);
  const resultKey=id+':'+key;
  const preferred=selectedResultRuns[resultKey];
  const selected=available.find(run=>run.id===preferred)||available[0];
  const versionOptions=available.map(run=>`<option value="${esc(run.id)}" ${run===selected?'selected':''}>${esc(statuses[run.status]||run.status)} · ${esc(run.updated_at)}${run.stale_stages?.includes(key)?' · 依据已更新':''}</option>`).join('');
  host.innerHTML=chapterBar()+`<section class="panel"><div class="section-head"><div><h2>${key==='shots'?'分镜工作台':stageNames[key]}</h2><p>生成 → 检查 → 确认 → 手动进入下一步</p></div></div>${stageControls(head,key)}${available.length?`<label class="field">执行版本<select id="result-run">${versionOptions}</select></label><div id="result-version-note"></div><div id="result-view"></div><div id="stage-editor"></div>`:empty('还没有'+stageNames[key],key==='analysis'?'点击上方按钮，仅生成本章分析。':'请先在上一步确认结果，再生成当前阶段。')}</section>`;
  host.querySelector('#chapter-select').onchange=e=>{if(!leaveDraft()){e.target.value=chapterId||'';return;}dirty=false;chapterId=e.target.value||null;renderBook();};
  host.querySelectorAll('[data-start-stage]').forEach(button=>button.onclick=()=>submitStage(button.dataset.startStage,head));
  const confirmButton=host.querySelector('#confirm-stage');
  if(confirmButton)confirmButton.onclick=()=>{if(dirty){notify('请先保存编辑。');return;}guarded(async()=>{await api('confirm-stage',{run_id:head.id,stage:key,revision:head.revision});renderBook();notify('本阶段已确认，可以手动进入下一步。');});};
  const select=host.querySelector('#result-run');
  if(!select)return;
  const show=()=>{
   const run=available.find(item=>item.id===select.value);
   const stale=run.stale_stages?.includes(key),historical=run.id!==head?.id;
   host.querySelector('#result-version-note').innerHTML=stale?'<div class="notice">上游依据已更新，这份历史结果保留供参考，需要重新生成后才能用于后续步骤。</div>':historical?'<p class="muted">正在查看历史版本。上方执行与确认按钮仅操作当前版本。</p>':!stageFinished(run,key)?'<p class="muted">已保存部分结果，当前阶段尚未全部完成。</p>':(stageApproved(run,key)?'<p class="muted">本阶段已确认，可手动开始下一步。</p>':'<p class="muted">本阶段结果已保存，未确认前不会进入下一步。</p>');
   host.querySelector('#result-view').innerHTML=stageHtml(key,run.result);
   const editor=host.querySelector('#stage-editor');editor.innerHTML='';
   if(['analysis','outline'].includes(key)&&!historical&&!stale&&stageFinished(run,key)&&!['pending','running','pause_requested','stop_requested'].includes(run.status)){
    editor.innerHTML=`<details style="margin-top:24px"><summary>编辑本阶段结果</summary><p class="muted">以 JSON 编辑，保留字段和引用编号。保存为新版本后需重新确认，下游旧结果继续保留。</p><label class="field"><textarea id="stage-edit-json" rows="16">${esc(JSON.stringify(run.result[key],null,2))}</textarea></label><button class="btn" type="button" id="save-stage-edit">保存为新版本</button></details>`;
    $('stage-edit-json').oninput=()=>dirty=true;
    $('save-stage-edit').onclick=()=>guarded(async()=>{let value;try{value=JSON.parse($('stage-edit-json').value);}catch{throw Error('JSON 格式不正确，请检查后保存。');}await api('edit-stage',{run_id:run.id,stage:key,revision:run.revision,value});dirty=false;delete selectedResultRuns[resultKey];renderBook();notify('已保存新版本，请检查并确认。');});
   }
  };
  select.onchange=()=>{if(!leaveDraft()){select.value=selectedResultRuns[resultKey]||selected?.id;return;}dirty=false;selectedResultRuns[resultKey]=select.value;show();};show();
 }catch(error){notify(error.message);}
}

// Reload the existing click listeners with cloud implementations (function bindings are shared).
async function boot(){try{await refreshCloud();dirty=false;route='home';render();document.querySelector('.prototype-tag').textContent='云端数据库 · 在线';document.querySelector('.sidebar-footer p').textContent='云端持久化 · 文本独立执行器';document.querySelector('.sidebar-footer .btn').textContent='↓ 导出云端数据';}catch(e){$('workspace').innerHTML=empty('暂时无法连接云端数据库',e.message);}}
boot();

})();
