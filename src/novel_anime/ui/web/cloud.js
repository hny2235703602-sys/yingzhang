(function(){
/* Cloud adapter: reuse the approved layout, persist via the existing business service. */
let cloudBusy=false;
async function api(path,body){const r=await fetch('/api/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const value=await r.json();if(!r.ok)throw Error(value.error||'云端请求失败');return value;}
async function refreshCloud(){const state=await api('state');data.novels=state.novels;data.profiles=state.profiles;data.image_profile=state.image_profile||{};if(state.templates)data.templates=state.templates;data.media_templates=state.media_templates||{};if(state.default_profile_id&&data.profiles.some(p=>p.id===state.default_profile_id&&p.enabled!==false))data.activeProfile=state.default_profile_id;else if(!data.profiles.some(p=>p.id===data.activeProfile&&p.enabled!==false))data.activeProfile=data.profiles.find(p=>p.enabled!==false)?.id||'';}
window.persist = function (){return true;}
async function guarded(action){if(cloudBusy)return;cloudBusy=true;try{await action();}catch(e){notify(e.message||'操作失败，请重试。');}finally{cloudBusy=false;}}
window.saveBook = async function (){return guarded(async()=>{if(!editDraft)return;const title=$('edit-title').value.trim();if(!title){notify('请填写作品名称。');return;}await api('work',{id:editingId,title,style:$('edit-style').value,cover:editDraft.cover});await refreshCloud();closeEditor();renderShelf();notify('作品已保存到云端。');});}
const originalOpenEditor=openEditor;
window.openEditor=function(id=null){if(id){refreshCloud().then(()=>{if(editingId===id)paintCharacterSuggestions();}).catch(()=>{});}originalOpenEditor(id);paintCharacterSuggestions();};
function paintCharacterSuggestions(){const novel=data.novels.find(item=>item.id===editingId);const suggestions=novel?.character_suggestions||[];if(!suggestions.length)return;const saved=(editDraft.characters||[]).map(row=>`<tr><td>${esc(row.name)}</td><td>${esc(row.appearance)}</td><td>${esc(row.costume)}</td><td>${esc(row.source||'')}</td></tr>`).join('');const pending=suggestions.map(row=>`<tr><td>${esc(row.name)}</td><td>${esc(row.appearance)}</td><td>${esc(row.costume)}</td><td>${esc(row.source)} · 待采用</td></tr>`).join('');$('cast-body').innerHTML=saved+pending+`<tr><td class="empty" colspan="4"><button type="button" class="btn" id="adopt-cast">采用脚本里的人物外观</button></td></tr>`;$('adopt-cast').onclick=()=>guarded(async()=>{await api('adopt-characters',{work_id:editingId});await refreshCloud();const fresh=data.novels.find(item=>item.id===editingId);editDraft.characters=fresh?.characters||[];renderCast(editDraft.characters);notify('角色资料已采用，之后新的脚本会带上这些形象。');});};
window.saveChapter = async function (){return guarded(async()=>{const title=$('chapter-title').value.trim(),text=$('chapter-text').value;if(!title||!text.trim()){notify('请填写章节标题与正文。');return;}const old=chapter();const result=await api('chapter',{work_id:currentId,id:old?.id,revision:old?.revision,title,text,context:$('chapter-context').value});await refreshCloud();chapterId=result.id;dirty=false;renderBook();notify('章节已保存到云端。');});}
window.removeBook = function (){notify('为保护现有章节与任务，作品删除暂未开放。');}
const originalRenderHome=renderHome,originalRenderModels=renderModels,originalRenderBook=renderBook,originalRender=render;
function cloudCopy(root=document){const substitutions=[['配置原型 · 未连接','云端配置'],['当前浏览器中保存的作品','云端数据库中保存的作品'],['当前是交互原型','云端工作台'],['数据仅保存在此浏览器。模型请求与视频生成尚未接入云端应用，页面不会发送付费请求。','作品与章节保存在云端。文本改编由独立执行器处理；图片与视频尚未开放。'],['个模型配置','个模型配置'],['真实 API 与视频服务','视频服务待接入'],['未连接','待配置'],['浏览器原型','云端保存'],['保存可在刷新后恢复。清除浏览器数据会删除本地记录，建议定期导出备份。','已保存内容可跨浏览器恢复。正文修改请及时保存；同一章节并发修改会检查版本。'],['已保存至浏览器','已保存至云端'],['保存提示词草案','提示词草案 · 尚未接入'],['仅保存在浏览器，尚未接入实际生成流程。','提示词草案暂不写入真实生成流程。']];const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n;while(n=walker.nextNode()){if(['SCRIPT','STYLE'].includes(n.parentElement?.tagName))continue;let text=n.nodeValue;for(const [a,b]of substitutions)text=text.replaceAll(a,b);n.nodeValue=text;}}
window.renderHome = function (){originalRenderHome();cloudCopy($('workspace'));}
window.render = function (){
 if($('settings-tabs'))$('settings-tabs').classList.add('hidden');
 originalRender();cloudCopy();
 if(route==='image'){renderImageStudio();return;}
 if(current()&&['overview','video'].includes(route)){
  $('page-title').textContent=current().title;
  $('breadcrumb').textContent='创作空间 / '+labels[route]+' / 作品';
  if(route==='overview')renderOverview();
  else renderVideoPlan();
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
  const plan=selected?await api('video/'+selected).catch(()=>({groups:[]})):{groups:[]};
  if(!active())return;
  const groupsByShot={};(plan.groups||[]).forEach(group=>{group.shot_ids.forEach(id=>{groupsByShot[id]=group;});});
  const videoCell=shot=>{const group=groupsByShot[shot.shot_id];if(!group)return '<p class="muted">视频待生成</p>';const pass=(group.passes||[]).find(item=>(item.shot_ids||[]).includes(shot.shot_id))||(group.passes||[])[0]||{};return `<p><b>${esc(group.group_id)}</b> · ${group.seconds} 秒</p><p class="muted">${group.shot_ids.length} 镜 · 第 ${pass.index||1} 段 ${pass.frames||group.frames} 帧</p><p class="muted">提示词见「视频制作」</p>`;};
  const rows=result.shots.map(shot=>{
   const frame=shot.frame||'';
   const details=[frame,shot.action].filter(Boolean).map(value=>`<p style="white-space:pre-wrap">${esc(value)}</p>`).join('');
   const dialogue=shot.dialogue?`<p>台词：${esc(shot.dialogue)}</p>`:'';
   const subjects=(shot.subjects||[]).map(person=>({name:'人物 · '+person.name,description:person.description,images:[...(person.images||[]),...shot.image_source.images.filter(image=>image.selected&&image.kind==='character'&&image.subject_id===person.character_id).map(image=>({...image,kind:'shots'}))],kind:'characters'}));
   subjects.push({name:'环境',description:shot.setting||'',images:shot.image_source.images.filter(image=>image.selected&&image.kind==='environment'),kind:'shots'});
   const selected=shot.image_source.images.filter(image=>image.selected&&(!image.kind||image.kind==='composition'));
   return subjects.map((subject,index)=>`<tr>${index===0?`<td rowspan="${subjects.length}"><b>${esc(shot.order)}. ${esc(frame||shot.action)}</b><p class="muted">${esc([shot.shot_size,shot.camera,shot.duration_seconds+' 秒'].filter(Boolean).join(' · '))}</p>${details}${dialogue}${selected.length?`<details><summary>已采用的完整镜头画面</summary>${selected.map(image=>`<img src="/assets/shots/${esc(image.asset_id)}.img" alt="${esc(frame||shot.action)}完整镜头画面" style="width:100%;max-height:260px;object-fit:contain">`).join('')}</details>`:''}</td>`:''}<td><b>${esc(subject.name)}</b><p style="white-space:pre-wrap">${esc(subject.description)}</p></td><td>${subject.images.length?subject.images.map(image=>`<a href="/assets/${image.kind==='shots'?'shots':subject.kind}/${esc(image.asset_id)}.img" target="_blank" rel="noopener"><img src="/assets/${image.kind==='shots'?'shots':subject.kind}/${esc(image.asset_id)}.img" alt="${esc(subject.name)}参考图" style="width:100%;max-height:240px;object-fit:contain"></a>`).join(''):`<p class="muted">${subject.kind==='characters'?'暂无已采用的人物图片':'暂无独立环境图片'}</p>`}</td>${index===0?`<td rowspan="${subjects.length}">${videoCell(shot)}</td>`:''}</tr>`).join('');
  }).join('');
  const note=result.run?`<p class="muted">分镜版本：${esc(result.run.updated_at)} · ${esc(statuses[result.run.status]||result.run.status)}</p>`:'';
  $('overview-results').innerHTML=note+`<div style="overflow-x:auto"><table class="overview-table" style="width:100%;min-width:960px;table-layout:fixed;border-collapse:collapse;background:white"><colgroup><col style="width:32%"><col style="width:30%"><col style="width:25%"><col style="width:13%"></colgroup><thead><tr><th scope="col">分镜</th><th scope="col">人物/环境</th><th scope="col">参考图</th><th scope="col">视频</th></tr></thead><tbody>${rows||`<tr><td colspan="4">${empty(selected?'本章还没有分镜':'作品还没有章节',selected?'请先在故事脚本中生成本章镜头。':'请先在章节原文中保存章节。')}</td></tr>`}</tbody></table></div>`;
  if(!$('overview-table-style')){const style=document.createElement('style');style.id='overview-table-style';style.textContent='.overview-table th,.overview-table td{padding:20px;text-align:left;vertical-align:top;border:1px solid var(--line);overflow-wrap:anywhere}.overview-table th{background:#edf2e8;color:#50624f}.overview-table td p{font-size:13px;line-height:1.9}.overview-table td b{font-size:14px}';document.head.appendChild(style);}
 }catch(error){if(active())$('overview-results').innerHTML=empty('暂时无法读取章节产出',error.message||'请点击刷新重试。');}
}
let videoRequest=0;
async function renderVideoPlan(){
 const work=current();if(!work)return;
 const request=++videoRequest,chapters=work.chapters||[];
 if(!chapters.some(item=>item.id===chapterId))chapterId=chapters[0]?.id||null;
 const selectedChapter=chapterId;
 const active=()=>route==='video'&&currentId===work.id&&chapterId===selectedChapter&&request===videoRequest;
 $('workspace').innerHTML=`<div class="section-head"><div><div class="eyebrow">VIDEO PROMPTS</div><h2>视频提示词</h2><p>按目标时长把相邻分镜拼成一组，再编译成 ComfyUI 可直接粘贴的提示词与参数。</p></div><span class="badge">Wan2.2 TI2V-5B</span></div><div class="chapter-bar"><label>当前章节<select id="video-chapter">${chapters.length?chapters.map(item=>`<option value="${esc(item.id)}" ${item.id===selectedChapter?'selected':''}>${esc(item.title)}</option>`).join(''):'<option value="">暂无章节</option>'}</select></label><button class="btn primary" type="button" id="archive-video">保存到视频档案</button></div><div id="video-plan" aria-live="polite"><p class="muted">正在读取本章分镜…</p></div>`;
 $('video-chapter').onchange=e=>{chapterId=e.target.value;renderVideoPlan();};
 $('archive-video').onclick=()=>window.openVideoArchiveSave(work.id);
 if(!selectedChapter){$('video-plan').innerHTML=emptyTable(['段','时长 / 帧数','覆盖分镜','首帧'],'作品还没有章节','请先保存章节并生成故事脚本。');return;}
 try{
  const plan=await api('video/'+selectedChapter);if(!active())return;
  if(!plan.groups.length){$('video-plan').innerHTML=emptyTable(['段','时长 / 帧数','覆盖分镜','首帧'],'本章还没有分镜','请先在故事脚本中生成脚本，再回来编译视频提示词。');return;}
  const preset=plan.preset||{};
  const groupCard=group=>{
   const passes=group.passes.map(item=>`<tr><td>${item.index}</td><td>${item.seconds} 秒 / ${item.frames} 帧</td><td>${esc(item.shot_ids.join('、'))}</td><td>${item.continuation?'接上一段末帧':'首帧来自分镜图'}</td></tr>`).join('');
   return `<section class="panel" style="margin-bottom:20px"><div class="section-head"><div><h3>${esc(group.group_id)} · ${group.seconds} 秒 · ${group.shot_ids.length} 个分镜</h3><p class="muted">分镜：${esc(group.shot_ids.join('、'))} · 合计 ${group.frames} 帧（${preset.fps||24} fps）</p></div>${group.seconds>plan.target_seconds?badge('超出目标时长 '+plan.target_seconds+' 秒'):badge('符合目标时长')}</div><h4>整组提示词</h4><div class="video-prompt-columns"><div><label class="field">中文提示词<textarea rows="18" readonly>${esc(group.prompt)}</textarea></label><button class="btn" type="button" data-copy-prompt="${esc(group.group_id)}">复制中文提示词</button></div><div><label class="field">英文提示词<textarea rows="18" readonly placeholder="选择语言模型后，点击生成英文提示词。">${esc(group.prompt_en||'')}</textarea></label><div class="actions"><button class="btn" type="button" data-copy-prompt="${esc(group.group_id)}-en" ${group.prompt_en?'':'disabled'}>复制英文提示词</button><button class="btn primary" type="button" data-generate-english="${esc(group.group_id)}">${group.prompt_en?'重新生成英文':'生成英文提示词'}</button></div></div></div><h4>分段生成</h4><div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse"><thead><tr><th scope="col">段</th><th scope="col">时长 / 帧数</th><th scope="col">覆盖分镜</th><th scope="col">首帧</th></tr></thead><tbody>${passes}</tbody></table></div>${group.passes.map(item=>`<details style="margin-top:10px"><summary class="muted">第 ${item.index} 段提示词 · ${item.seconds} 秒 / ${item.frames} 帧</summary><label class="field"><textarea rows="4" readonly>${esc(item.prompt)}</textarea></label><div class="actions"><button class="btn" type="button" data-copy-pass="${esc(group.group_id)}-${item.index}">复制第 ${item.index} 段</button></div></details>`).join('')}</section>`;
  };
  const params=`<section class="panel"><h3>工作流参数 · ${esc(preset.name||'')}</h3><table style="width:100%;border-collapse:collapse"><tbody><tr><th scope="row">分辨率</th><td>${preset.width} × ${preset.height}</td></tr><tr><th scope="row">帧率</th><td>${preset.fps} fps，length 取 4n+1</td></tr><tr><th scope="row">单段帧数</th><td>${preset.frames}（约 ${Math.round((preset.frames||121)/(preset.fps||24)*100)/100} 秒）</td></tr><tr><th scope="row">采样</th><td>steps ${preset.sampler.steps} · cfg ${preset.sampler.cfg} · ${esc(preset.sampler.sampler_name)} / ${esc(preset.sampler.scheduler)} · shift ${preset.sampler.shift}</td></tr><tr><th scope="row">模型</th><td>${esc(preset.env.unet)}<br>${esc(preset.env.clip)}<br>${esc(preset.env.vae)}</td></tr><tr><th scope="row">输出</th><td>${esc(preset.output.format)} · ${esc(preset.output.codec)} · ${preset.output.fps} fps</td></tr></tbody></table><p class="muted">来源：<a href="${esc(preset.source)}" target="_blank" rel="noopener">ComfyUI 官方 Wan2.2 示例</a>。在 ComfyUI 里加载 image_to_video_wan22_5B.json，首帧接分镜图，把上面的提示词和帧数填进对应节点即可。</p><details style="margin-top:12px"><summary>负面提示词（官方默认）</summary><label class="field"><textarea rows="4" readonly>${esc(plan.negative)}</textarea></label></details></section>`;
  const settings=`<section class="panel"><h3>分组与画幅</h3><p class="muted">目标时长是每组的期望长度，不是硬上限；下一镜放进来会超过单组上限就先收组，放得下就整镜并入。</p><div class="field-row"><label class="field">目标时长（秒）<input id="video-target" type="number" min="5" max="60" step="1" value="${plan.target_seconds}"></label><label class="field">单组上限（秒）<input id="video-max" type="number" min="5" max="60" step="1" value="${plan.max_seconds}"></label><label class="field">画幅<select id="video-ratio">${['16:9','9:16','1:1'].map(r=>`<option value="${r}" ${r===plan.aspect_ratio?'selected':''}>${r}</option>`).join('')}</select></label></div><div class="actions"><button class="btn primary" type="button" id="video-save">保存分组与画幅</button><span class="muted">保存后重新编译，不创建视频任务。</span></div></section>`;
  $('video-plan').innerHTML=`<p class="muted">分镜版本：${esc(plan.run.updated_at)} · 共 ${plan.groups.length} 组，目标 ${plan.target_seconds} 秒一组，最多 ${plan.max_seconds} 秒；单镜不拆分，凑不够就整镜并入。</p><label class="field">英文提示词使用的语言模型<select id="video-language-model">${data.profiles.filter(p=>p.enabled!==false).map(p=>`<option value="${esc(p.id)}" ${p.id===data.activeProfile?'selected':''}>${esc(p.name)} · ${esc(p.model)}</option>`).join('')||'<option value="">请先在模型配置中添加语言模型</option>'}</select><small>点击生成时调用模型；刷新页面沿用已保存的英文结果。</small></label>${plan.groups.map(groupCard).join('')}${settings}${params}`;
  $('video-save').onclick=()=>guarded(async()=>{
   const target=Number($('video-target').value),ceiling=Number($('video-max').value);
   if(!(target>=5&&target<=60)||!(ceiling>=5&&ceiling<=60)){notify('目标时长与单组上限需在 5 到 60 秒之间。');return;}
   if(target>ceiling){notify('目标时长不能大于单组上限。');return;}
   await api('video-config',{target_seconds:target,max_seconds:ceiling,aspect_ratio:$('video-ratio').value});
   if(active())renderVideoPlan();
   notify('分组与画幅已保存。');
  });
  document.querySelectorAll('[data-generate-english]').forEach(button=>{button.onclick=()=>guarded(async()=>{
   const profile=$('video-language-model').value;
   if(!profile){notify('请先在模型配置中添加语言模型。');return;}
   const label=button.textContent;button.disabled=true;button.textContent='正在生成英文…';
   try{await api('video-prompt-english',{chapter_id:selectedChapter,group_id:button.dataset.generateEnglish,profile_id:profile});if(active())await renderVideoPlan();notify('英文提示词已生成并保存。');}
   finally{button.disabled=false;button.textContent=label;}
  });});
  const copy=async(text,note)=>{try{await navigator.clipboard.writeText(text);notify(note+'已复制。');}catch{notify('浏览器未允许复制，请手动选中文本。');}};
  const texts={};plan.groups.forEach(group=>{texts[group.group_id]=group.prompt;texts[group.group_id+'-en']=group.prompt_en||'';group.passes.forEach(item=>{texts[group.group_id+'-'+item.index]=item.prompt;});});
  document.querySelectorAll('[data-copy-prompt],[data-copy-pass]').forEach(button=>{button.onclick=()=>copy(texts[button.dataset.copyPrompt||button.dataset.copyPass]||'','提示词');});
 }catch(error){if(active())$('video-plan').innerHTML=empty('暂时无法编译视频提示词',error.message||'请稍后刷新。');}
}
let imageRequest=0;
const studioDraft={positive:'',negative:'',count:1,references:[]};
const studioAssetUrl=image=>'/assets/studio/'+encodeURIComponent(image.asset_id)+'.img';
async function renderImageStudio(){
 const request=++imageRequest,active=()=>route==='image'&&request===imageRequest;
 const configured=!!data.image_profile?.key_configured||data.image_profile?.backend==='mflux';
 $('workspace').hidden=false;
 $('workspace').innerHTML=`<div class="section-head"><div><div class="eyebrow">IMAGE STUDIO</div><h2>图像创作</h2><p>手动粘贴或编写提示词，按需上传参考图。</p></div>${badge(configured?'生图模型已配置':'请先配置生图模型',configured?'':'amber')}</div><section class="panel"><label class="field">正向提示词 <small>必填</small><textarea id="studio-positive" rows="8" maxlength="20000" required placeholder="在这里粘贴或编写希望生成的画面：主体、场景、画风、光线和构图……">${esc(studioDraft.positive)}</textarea></label><label class="field">反向提示词 <small>选填</small><textarea id="studio-negative" rows="3" maxlength="10000" placeholder="填写希望避免的内容，例如：模糊、水印、多余肢体……">${esc(studioDraft.negative)}</textarea><small>反向提示词作为文字约束提交，效果取决于模型。</small></label><section style="margin:24px 0"><div class="section-head"><div><h3>参考图 · 可选 <small class="muted" id="studio-reference-count"></small></h3><p>最多 6 张 PNG / JPG / JPEG，每张不超过 8 MB。不选也能生成。按选择顺序编号，可在正向提示词中使用“图一”“图二”。</p></div><label class="btn" style="cursor:pointer">＋ 上传参考图<input id="studio-upload" type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" hidden></label></div><div id="studio-references"></div></section><div class="actions"><label>生成数量 <select id="studio-count">${[1,2,3,4].map(n=>`<option value="${n}" ${n===studioDraft.count?'selected':''}>${n} 张</option>`).join('')}</select></label><button class="btn primary" type="button" id="studio-generate" ${configured?'':'disabled'}>开始生成</button><button class="btn" type="button" id="studio-refresh">刷新记录与模型配置</button><span class="muted" id="studio-status" role="status">${configured?'提示词由你填写，点击后提交生成。':'请先在模型配置中配置生图模型。'}</span></div></section><section style="margin-top:24px"><div class="section-head"><div><h3>生成记录</h3><p>最新生成的排最前面，记录最多保留 8 张，超出自动清理（包括已存入图库的记录）；图库中的图片独立保留。</p></div></div><div id="studio-results" aria-live="polite">正在读取图片…</div></section>`;
 const capture=()=>{studioDraft.positive=$('studio-positive').value;studioDraft.negative=$('studio-negative').value;studioDraft.count=Number($('studio-count').value);dirty=true;};
 $('studio-positive').oninput=capture;$('studio-negative').oninput=capture;$('studio-count').onchange=capture;
 const body=()=>({positive_prompt:studioDraft.positive.trim(),negative_prompt:studioDraft.negative.trim(),count:studioDraft.count,reference_assets:studioDraft.references.slice()});
 const paintReferences=()=>{
  if(!active())return;
  $('studio-reference-count').textContent=`已选 ${studioDraft.references.length} / 6`;
  $('studio-references').innerHTML=studioDraft.references.length?`<div style="display:flex;gap:14px;flex-wrap:wrap">${studioDraft.references.map((id,index)=>`<article style="width:130px"><img src="${studioAssetUrl({asset_id:id})}" alt="参考图${index+1}" style="width:130px;height:110px;object-fit:contain;background:#f3f5ef;border-radius:8px"><div class="actions"><b>图${'一二三四五六'[index]}</b><button class="btn" type="button" data-studio-remove="${esc(id)}">移除</button></div></article>`).join('')}</div>`:'<p class="muted">未选择参考图，使用提示词生成。</p>';
  document.querySelectorAll('[data-studio-remove]').forEach(button=>button.onclick=()=>{studioDraft.references=studioDraft.references.filter(id=>id!==button.dataset.studioRemove);dirty=true;paintReferences();notify('参考图编号已更新，请核对提示词。');});
 };
 const paintResults=async()=>{
  const images=await api('image-studio');if(!active())return;
  const available=new Set(images.map(image=>image.asset_id));
  const generated=images.filter(image=>image.purpose==='generated');
  $('studio-results').innerHTML=generated.length?`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:18px">${generated.map(image=>`<article class="panel" style="padding:14px"><a href="${studioAssetUrl(image)}" target="_blank" rel="noopener"><img src="${studioAssetUrl(image)}" alt="生成图片" style="width:100%;height:240px;object-fit:contain;background:#f3f5ef;border-radius:8px"></a><p class="muted">${image.width} × ${image.height} · ${esc(image.model)}</p><div class="actions"><a class="btn" href="${studioAssetUrl(image)}" download="${esc(image.filename)}">下载原图</a><button class="btn" type="button" data-studio-use="${esc(image.asset_id)}">用作参考图</button><button class="btn" type="button" data-studio-collect="${esc(image.asset_id)}">＋ 保存到图库</button></div><details style="margin-top:12px"><summary>生成记录 · ${image.reference_assets.length} 张参考图</summary><h4>正向提示词</h4><p style="white-space:pre-wrap">${esc(image.positive_prompt)}</p><h4>反向提示词</h4><p style="white-space:pre-wrap">${esc(image.negative_prompt)||'未填写'}</p>${image.reference_assets.length?`<div style="display:flex;gap:8px;flex-wrap:wrap">${image.reference_assets.map((id,index)=>available.has(id)?`<a href="${studioAssetUrl({asset_id:id})}" target="_blank" rel="noopener"><img src="${studioAssetUrl({asset_id:id})}" alt="图${index+1}" style="width:70px;height:70px;object-fit:contain"></a>`:`<span class="muted">图${index+1} · 原生成记录已清理</span>`).join('')}</div>`:''}</details></article>`).join('')}</div>`:emptyTable(['图片','正向提示词','反向提示词','操作'],'还没有生成图片','填写正向提示词后点击「开始生成」，参考图可以不选。');
  document.querySelectorAll('[data-studio-collect]').forEach(button=>button.onclick=()=>window.openLibraryImageSave(button.dataset.studioCollect,button));
  document.querySelectorAll('[data-studio-use]').forEach(button=>button.onclick=()=>guarded(async()=>{if(studioDraft.references.length>=6){notify('最多选择 6 张参考图。');return;}button.disabled=true;try{const result=await api('studio-reference',{asset_id:button.dataset.studioUse});studioDraft.references.push(...result.asset_ids);dirty=true;paintReferences();notify('已添加为参考图，生成记录清理后仍可使用。');}finally{if(button.isConnected)button.disabled=false;}}));
 };
 paintReferences();
 $('studio-generate').onclick=()=>guarded(async()=>{
  capture();if(!studioDraft.positive.trim()){notify('请填写正向提示词。');$('studio-positive').focus();return;}
  const payload=body(),button=$('studio-generate'),status=$('studio-status');button.disabled=true;button.textContent='正在生成…';status.textContent='提示词已提交，正在生成图片…';
  try{const result=await api('studio-generate',payload);if(active()){await paintResults();status.textContent=`已生成 ${result.asset_ids.length} 张图片。`;dirty=false;}notify(`已生成 ${result.asset_ids.length} 张图片。`);}
  catch(error){if(status.isConnected)status.textContent=error.message;throw error;}
  finally{if(button.isConnected){button.disabled=!configured;button.textContent='开始生成';}}
 });
 $('studio-upload').onchange=e=>guarded(async()=>{
  const input=e.target,file=input.files[0];if(!file)return;
  try{
   if(studioDraft.references.length>=6){notify('最多选择 6 张参考图，请先移除。');return;}
   const mime=/\.jpe?g$/i.test(file.name)?'image/jpeg':/\.png$/i.test(file.name)?'image/png':file.type==='image/jpg'?'image/jpeg':file.type;
   if(!['image/png','image/jpeg'].includes(mime)||file.size>8000000){notify('请上传 8 MB 以内的 PNG、JPG 或 JPEG。');return;}
   capture();
   const dataUrl=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('图片读取失败，请重试。'));reader.readAsDataURL(file);});
   const result=await api('studio-upload',{data_url:String(dataUrl).replace(/^data:[^;]*;/,'data:'+mime+';')});
   studioDraft.references.push(...result.asset_ids);paintReferences();notify('参考图已上传并选中。');
  }finally{input.value='';}
 });
 $('studio-refresh').onclick=()=>guarded(async()=>{capture();await refreshCloud();if(active())await renderImageStudio();});
 try{await paintResults();}catch(error){if(active())$('studio-results').textContent=error.message;}
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
const textPromptNames={script:'故事脚本'};
const promptPages={image:'图像创作配置',text:'故事脚本配置',video:'视频制作配置'};
function savedPromptValues(category){return category==='text'?{...data.templates}:{template:data.media_templates?.[category]||''};}
function promptsDirty(){return Object.entries(promptDrafts).some(([category,values])=>Object.entries(values).some(([key,value])=>value!==savedPromptValues(category)[key]));}
window.renderPrompts=function(){
 const category=promptCategory;
 renderSettingsTabs(promptPages,category,next=>{promptCategory=next;renderPrompts();});
 const values=promptDrafts[category]||(promptDrafts[category]=savedPromptValues(category));
 const names=category==='text'?textPromptNames:{template:category==='image'?'全局图片风格与应用场景':'视频生成提示词'};
 const help=category==='image'?'统一描述图片风格、应用场景、构图偏好和画面限制。人物与环境描述由所选分镜自动带入，无需在这里填写角色或地点。保存后用于下一次生成。':category==='text'?'保存后用于新提交的文本任务；已提交任务保留原提示词。':'这段文字作为风格与限制拼进每段视频提示词；画面与动作仍来自分镜，视频生成本身尚未提交到 ComfyUI。';
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
 $('workspace').innerHTML=`<section class="panel" style="max-width:820px;margin:auto"><form id="simple-model" autocomplete="off"><div class="section-head"><div><h2>文本模型配置</h2><p>用于把章节原文生成故事脚本。</p></div><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" role="switch" aria-label="启用文本模型" id="model-enabled" ${p.enabled!==false?'checked':''}></label></div>${data.profiles.length>1?`<label class="field">当前配置<select id="simple-profile">${data.profiles.map(x=>`<option value="${esc(x.id)}" ${x.id===p.id?'selected':''}>${esc(x.name)} · ${esc(x.model)}</option>`).join('')}</select></label>`:''}<label class="field">API 地址<input id="simple-url" type="url" required value="${esc(p.url||'')}" placeholder="https://api.example.com/v1"><small>填写服务根地址，系统会请求该地址下的 chat/completions。</small></label><div style="display:grid;grid-template-columns:minmax(0,1fr) 120px;gap:16px"><label class="field">模型名称<select id="simple-name" required>${modelOptions}</select><small>填写 API 地址和 API Key 后自动获取列表，再从下拉框选择。</small></label><label class="field">温度<input id="simple-temperature" type="number" min="0" max="2" step="0.1" required value="${p.temperature??1}"><small>0 到 2，部分模型只接受 1。</small></label></div><label class="field">超时（秒）<input id="simple-timeout" type="number" min="10" max="3600" required value="${p.timeout||120}"><small>10 到 3600 秒。本机大模型输出慢，建议 1800 以上。</small></label><label class="field">思考强度<select id="simple-effort"><option value="" ${!p.reasoning_effort?'selected':''}>默认（不发送该参数）</option><option value="none" ${p.reasoning_effort==='none'?'selected':''}>none · 关闭思考</option><option value="minimal" ${p.reasoning_effort==='minimal'?'selected':''}>minimal</option><option value="low" ${p.reasoning_effort==='low'?'selected':''}>low</option><option value="medium" ${p.reasoning_effort==='medium'?'selected':''}>medium</option><option value="high" ${p.reasoning_effort==='high'?'selected':''}>high</option></select><small>思考型模型（Qwen3、o 系列）会先生成长推理再出正文，容易占满输出预算；本机模型跑不动时选 none。</small></label><label class="field">API Key<div style="display:flex;gap:8px;align-items:center"><div class="key-wrap"><input id="simple-key" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${p.key_configured?'已配置，留空则不修改':'填写 API Key'}"><button type="button" id="key-clear" aria-label="清除 API Key">×</button></div><button class="btn" type="button" style="white-space:nowrap" id="toggle-key" aria-label="显示输入的密钥">显示</button></div><small id="key-status">${p.key_configured?'已配置':'尚未配置'}。填写后仅显示后四位；已保存的密钥不会完整回显。</small></label><div class="actions"><button class="btn primary" type="submit">保存设置</button><span class="muted" id="model-feedback" role="status"></span></div></form></section>`;
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
  return {id:p.id||'online',url:$('simple-url').value.trim(),model:model||(forList?(p.model||'catalog'):''),timeout:Number($('simple-timeout').value),temperature:Number($('simple-temperature').value),enabled:$('model-enabled').checked,reasoning_effort:$('simple-effort')?.value||'',key:typedKey,clear_key:clearKey};
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
 if($('simple-profile'))$('simple-profile').onchange=e=>{const profile=e.target.value;guarded(async()=>{await api('select-profile',{profile_id:profile});data.activeProfile=profile;await refreshCloud();renderModels();notify('默认语言模型已切换。');});};
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

const statuses={stopped:'已停止',stop_requested:'正在停止',stage_complete:'本步已完成',pending:'等待执行',running:'执行中',pause_requested:'请求暂停',paused:'已暂停',succeeded:'已完成',review_required:'待核实',failed:'失败',unknown:'执行状态待核实'};
const pipeline=[['prepare','准备原文'],['script','故事脚本']];
const stageNames={script:'故事脚本'};
const stagePages={script:'script'};
function stageKey(stage){const name=String(stage||'prepare').split(' ')[0];return pipeline.some(([key])=>key===name)?name:'prepare';}
function stageDetail(stage){const raw=String(stage||'prepare').trim();const key=stageKey(raw);const label=pipeline.find(([id])=>id===key)[1];const rest=raw.slice(key.length).trim();if(!rest)return label;if(/^\d+\/\d+$/.test(rest))return `${label} ${rest}`;return `${label} · ${rest}`;}
function stepStates(run){const result=run?.result||{};const done={prepare:Array.isArray(result.paragraphs)&&result.paragraphs.length>0,script:!!result.script};const current=run?stageKey(run.stage):'';const live=!!run&&['pending','running','pause_requested','stop_requested'].includes(run.status);const failed=!!run&&['failed','unknown'].includes(run.status);const paused=!!run&&run.status==='paused';return pipeline.map(([key,label])=>{let state='等待';if(key===current&&live)state=run.status==='pending'?'即将开始':run.status==='pause_requested'?'正在暂停':run.status==='stop_requested'?'正在停止':'正在执行';else if(key===current&&paused)state='已暂停';else if(key===current&&failed)state='失败';else if(done[key])state='已完成';return {key,label,state,current:!!run&&key===current};});}
function runningLine(run){if(!run)return '';const name=stageDetail(run.stage);if(run.status==='pending')return `即将开始：${name}`;if(run.status==='running')return `正在执行：${name}`;if(run.status==='pause_requested')return `正在暂停：${name}`;if(run.status==='stop_requested')return `正在停止：${name}`;if(run.status==='stopped')return `已停止：${name}`;if(run.status==='paused')return `已暂停：${name}`;if(run.status==='failed'||run.status==='unknown')return `停在：${name}`;if(run.status==='review_required')return '脚本已生成，待审阅';if(run.status==='succeeded')return '脚本已生成';return name;}
function paintRunNow(line,active){const note=document.querySelector(`[data-text-page="${tab}"] #run-now`);if(!note)return;note.textContent=line;note.style.color=active?'#2d503d':'';note.style.fontWeight=active?'600':'400';}
function statusColumn(){return document.getElementById('page-status');}
function progressStyles(){
 if(document.getElementById('pipeline-step-style'))return;
 const style=document.createElement('style');style.id='pipeline-step-style';
 style.textContent=`.pipeline-steps{list-style:none;margin:16px 0;padding:0;font-size:13px}.pipeline-step{position:relative;display:flex;align-items:center;gap:12px;min-height:42px;color:#8a968d}.pipeline-step:not(:last-child):after{content:"";position:absolute;left:7px;top:23px;bottom:-19px;width:2px;background:#d6dfd2}.pipeline-dot{position:relative;flex:0 0 16px;width:16px;height:16px;box-sizing:border-box;border:2px solid #8a968d;border-radius:50%;background:#f4f6ef;padding:0;z-index:1}.pipeline-dot.is-running:before{content:"";position:absolute;inset:-5px;border-radius:50%;border:2px solid #cedbc8;border-top-color:#437c50;border-right-color:#76a968;animation:pipeline-spin 1s linear infinite;box-shadow:0 0 8px #8aaf7666}.pipeline-dot.is-complete{background:#617654;border-color:#617654}.pipeline-dot.is-complete:after{content:"✓";position:absolute;inset:0;font-size:10px;line-height:12px;text-align:center;color:#f4f6ef}.pipeline-step.is-running{color:#2d503d;font-weight:600}.pipeline-step.is-complete{color:#617654}.pipeline-step.is-failed{color:#9a5348}.pipeline-step-state{margin-left:auto;font-size:11px;font-weight:400}.pipeline-help{font-size:11px;line-height:1.8;color:#7c897b;margin:12px 0}@keyframes pipeline-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.pipeline-dot.is-running:before{animation-duration:3s}}`;
 document.head.appendChild(style);
}
async function startScriptRun(){
 if(dirty){notify('请先保存当前编辑。');return;}
 if(!chapterId){notify('请先保存章节原文。');return;}
 const profile=data.activeProfile||data.profiles[0]?.id;
 if(!profile){notify('请先配置文本模型。');return;}
 if(!confirm('将按「时长、分镜、动作、台词」生成本章故事脚本。模型调用可能计费。'))return;
 guarded(async()=>{
  await api('run',{chapter_id:chapterId,profile_id:profile,style:current().style,aspect_ratio:'16:9',preference:'',idempotency_key:uid(),stage:'script'});
  dirty=false;tab='script';renderBook();notify('故事脚本已提交，完成后可逐行修改。');
 });
}
function openStagePage(){if(!leaveDraft())return;dirty=false;tab='script';renderBook();}
async function showProgress(){
 const aside=statusColumn();if(!aside)return;progressStyles();
 const id=chapterId;clearTimeout(showProgress.timer);
 if(showProgress.chapter!==id){showProgress.signature='';showProgress.chapter=id;aside.dataset.ready='';}
 const paint=run=>{
  const steps=stepStates(run),line=runningLine(run);
  const live=!!run&&['pending','running','pause_requested','stop_requested'].includes(run.status);
  const active=live||run?.status==='paused';
  const attention=active||!!run&&['failed','unknown'].includes(run.status);
  paintRunNow(attention?line:'',active);
  const signature=run?`${run.id}|${run.status}|${run.stage}|${run.revision}|${run.updated_at||''}|${run.error_message||''}`:'none';
  if(signature===showProgress.signature&&aside.dataset.ready==='1'){
   if(live)showProgress.timer=setTimeout(showProgress,3000);return;
  }
  showProgress.signature=signature;aside.dataset.ready='1';
  if(tab==='script'&&!dirty)renderResults();
  const actions=run&&['pending','running'].includes(run.status)?`<button class="btn" data-run-action="pause" data-run-id="${run.id}" type="button">暂停</button>`:run&&['paused','pause_requested'].includes(run.status)?`<button class="btn" data-run-action="resume" data-run-id="${run.id}" type="button">继续</button>`:run&&['failed','unknown','stopped'].includes(run.status)?`<button class="btn" data-run-action="retry" data-run-id="${run.id}" type="button">继续未完成部分 · 可能计费</button>`:'';
  const rows=steps.map(step=>{
   const executing=live&&step.current;
   return `<li class="pipeline-step ${executing?'is-running':''} ${step.state==='已完成'?'is-complete':''} ${step.state==='失败'?'is-failed':''}"><span class="pipeline-dot ${executing?'is-running':''} ${step.state==='已完成'?'is-complete':''}"></span><span>${esc(step.label)}</span><span class="pipeline-step-state">${esc(step.state)}</span></li>`;
  }).join('');
  aside.innerHTML=`<div class="eyebrow">生成进度</div><h3>${esc(line||'保存章节后生成脚本')}</h3><ul class="pipeline-steps">${rows}</ul><p class="pipeline-help">上传章节后只需生成一次故事脚本；每次修改都会保存为新版本，旧版本随时可查看。</p>${run&&run.status==='failed'&&run.error_message?`<p class="muted">${esc(run.error_message)}</p>`:''}<div class="actions"><button class="btn primary" id="start-script-run" type="button" ${live||!id?'disabled':''}>${run&&run.result?.script?'重新生成故事脚本':'生成故事脚本'}</button>${actions}</div><p class="muted" id="run-now">${esc(live?line:'')}</p><button class="btn" id="refresh-progress" type="button">刷新进度</button>`;
  const start=aside.querySelector('#start-script-run');if(start)start.onclick=startScriptRun;
  aside.querySelector('#refresh-progress').onclick=showProgress;
  aside.querySelectorAll('[data-run-action]').forEach(button=>{
   button.onclick=()=>guarded(async()=>{await api(button.dataset.runAction,{id:button.dataset.runId});showProgress();});
  });
  if(live)showProgress.timer=setTimeout(showProgress,3000);
 };
 try{const runs=id?await api('runs/'+id):[];if(route!=='text'||chapterId!==id)return;paint(runs[0]||null);}catch(error){const asideNow=statusColumn();if(asideNow)asideNow.innerHTML=`<div class="eyebrow">生成进度</div><p class="muted">${esc(error.message||'暂时无法读取进度。')}</p>`;}
}
function ensureRunButton(){
 if(tab!=='source')return;
 const host=document.querySelector('[data-text-page="source"]');
 if(!host||host.querySelector('#submit-cloud-task'))return;
 const button=document.createElement('button');button.type='button';button.className='btn primary';button.id='submit-cloud-task';button.textContent='生成故事脚本';button.onclick=startScriptRun;
 host.querySelector('.actions')?.appendChild(button);
}
window.renderBook = function (){if(tab==='tasks')tab='source';originalRenderBook();cloudCopy($('text-pages'));document.querySelector('[data-text="tasks"]')?.remove();const key=tab;const finish=()=>{if(route==='text'&&tab===key){if(key==='source')ensureRunButton();showProgress();}};if(key==='script')renderResults().finally(finish);else finish();}
function scriptTable(run){
 const result=run?.result?.script||{},characters=result.characters||[],rows=result.shots||[];
 if(!rows.length)return emptyTable(['编号','时长','分镜','动作','台词'],'这一版还没有故事脚本','换一个执行版本，或等当前任务保存后再打开。');
 const names=Object.fromEntries(characters.map(person=>[person.character_id,person.name]));
 const total=rows.reduce((sum,row)=>sum+Number(row.duration_seconds||0),0);
 const issues=run?.validation_issues||[];
 return `<p class="muted">共 ${rows.length} 镜 · 规划时长约 ${total} 秒。景别只取远景、全景、中景、近景、特写；分镜写成「景别 · 运镜 · 画面描述」。</p><div style="overflow-x:auto"><table style="width:100%;min-width:1000px;table-layout:fixed;border-collapse:collapse"><colgroup><col style="width:78px"><col style="width:88px"><col style="width:30%"><col style="width:22%"><col style="width:22%"></colgroup><thead><tr><th scope="col">编号</th><th scope="col">时长</th><th scope="col">分镜</th><th scope="col">动作</th><th scope="col">台词</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${esc(row.shot_id||'')}</td><td>${esc(row.duration_seconds)} 秒</td><td><b>${esc([row.shot_size,row.camera].filter(Boolean).join(' · '))}</b><p style="white-space:pre-wrap">${esc(row.frame||'')}</p>${row.setting?`<p class="muted">场景：${esc(row.setting)}</p>`:''}${(row.character_ids||[]).length?`<p class="muted">人物：${esc((row.character_ids||[]).map(id=>names[id]||id).join('、'))}</p>`:''}</td><td style="white-space:pre-wrap">${esc(row.action||'')}</td><td style="white-space:pre-wrap">${esc(row.dialogue||'—')}</td></tr>`).join('')}</tbody></table></div>${characters.length?`<h3>本章人物外观</h3><p class="muted">${characters.map(person=>`${esc(person.name)}（${esc(person.character_id)}）：${esc([person.appearance,person.costume].filter(Boolean).join('；'))}`).join('<br>')}</p>`:''}${issues.length?`<div class="notice"><b>待核实</b><span>${issues.map(issue=>esc(issue)).join('；')}</span></div>`:''}`;
}
const selectedResultRuns={};
async function renderResults(){
 const id=chapterId,key=tab;if(!id)return;
 try{
  const runs=await api('runs/'+id);
  if(route!=='text'||chapterId!==id||tab!==key)return;
  const host=document.querySelector(`[data-text-page="${key}"]`);
  const available=runs.filter(run=>(run.result?.script?.shots||[]).length);
  const head=runs[0];
  const resultKey=id+':'+key;
  const preferred=selectedResultRuns[resultKey];
  const selected=available.find(run=>run.id===preferred)||available[0];
  const live=!!head&&['pending','running','pause_requested','stop_requested'].includes(head.status);
  const versionOptions=available.map(run=>`<option value="${esc(run.id)}" ${run===selected?'selected':''}>${esc(statuses[run.status]||run.status)} · ${esc(run.updated_at)}${run.stale_stages?.length?' · 章节已更新':''}</option>`).join('');
  host.innerHTML=chapterBar()+`<section class="panel"><div class="section-head"><div><h2>故事脚本</h2><p>时长 · 分镜 · 动作 · 台词</p></div></div><div class="notice">上传章节后生成脚本。检查无误可导出；需要修改时逐行编辑并保存为新版本。</div><div class="actions" style="margin-bottom:18px"><button class="btn primary" type="button" id="generate-script" ${live?'disabled':''}>${available.length?'重新生成故事脚本':'生成故事脚本'}</button></div>${available.length?`<label class="field">执行版本<select id="result-run">${versionOptions}</select></label><div id="result-version-note"></div><div id="result-view"></div><div id="stage-editor"></div>`:emptyTable(['编号','时长','分镜','动作','台词'],head?'故事脚本正在生成':'还没有故事脚本',head?'任务已提交，完成后这里会按行显示时长、分镜、动作与台词。':'点击上方按钮，用当前章节生成故事脚本。')}</section>`;
  host.querySelector('#chapter-select').onchange=e=>{if(!leaveDraft()){e.target.value=chapterId||'';return;}dirty=false;chapterId=e.target.value||null;renderBook();};
  const generate=host.querySelector('#generate-script');if(generate)generate.onclick=startScriptRun;
  const select=host.querySelector('#result-run');
  if(!select)return;
  const show=()=>{
   const run=available.find(item=>item.id===select.value);
   const stale=!!run.stale_stages?.length,historical=run.id!==head?.id;
   host.querySelector('#result-version-note').innerHTML=stale?'<div class="notice">章节原文已更新，这份历史结果保留供参考，需要重新生成后才能用于图片生成。</div>':historical?'<p class="muted">正在查看历史版本；编辑只会基于最新版本创建。</p>':'';
   host.querySelector('#result-view').innerHTML=`<div class="actions" style="margin-bottom:18px"><button class="btn" type="button" id="collect-script">＋ 保存到分镜词库</button></div>`+scriptTable(run);
   $('collect-script').onclick=()=>window.saveLibraryOutput({work_id:currentId,kind:'prompts',run_id:run.id,revision:run.revision},$('collect-script'));
   const editor=host.querySelector('#stage-editor');editor.innerHTML='';
   if(!historical&&!stale&&!live){
    editor.innerHTML=`<details style="margin-top:24px"><summary>逐行编辑 · 保存为新版本</summary><p class="muted">一行一镜。时长单位秒；景别只能选择；台词照抄原文并含说话人，没有台词留空。</p><div id="script-editor"></div><button class="btn primary" type="button" id="save-script-rows" style="margin-top:16px">保存为新版本</button></details>`;
    const rows=(run.result?.script?.shots||[]).map(row=>({...row}));
    const sizes=['远景','全景','中景','近景','特写'];
    $('script-editor').innerHTML=`<div style="display:grid;gap:16px">${rows.map((row,index)=>`<article style="border:1px solid #e4eadc;border-radius:8px;padding:14px" data-script-row="${index}"><div style="display:flex;gap:12px;flex-wrap:wrap"><label class="field" style="flex:0 0 90px">时长 / 秒<input data-field="duration_seconds" type="number" min="1" max="120" step="0.5" value="${esc(row.duration_seconds)}"></label><label class="field" style="flex:0 0 120px">景别<select data-field="shot_size">${sizes.map(size=>`<option ${size===row.shot_size?'selected':''}>${size}</option>`).join('')}</select></label><label class="field" style="flex:1 1 180px">运镜<input data-field="camera" value="${esc(row.camera||'')}"></label><label class="field" style="flex:1 1 180px">场景与环境<input data-field="setting" value="${esc(row.setting||'')}"></label></div><label class="field">画面描述 · 只写镜头里看得见的内容<textarea data-field="frame" rows="2">${esc(row.frame||'')}</textarea></label><label class="field">动作<textarea data-field="action" rows="2">${esc(row.action||'')}</textarea></label><label class="field">台词 · 含说话人，没有台词留空<textarea data-field="dialogue" rows="2">${esc(row.dialogue||'')}</textarea></label><label class="field">出场人物编号 · 逗号分隔<input data-field="character_ids" value="${esc((row.character_ids||[]).join('，'))}"></label><p class="muted">${esc(row.shot_id||'')} · 第 ${index+1} 行</p></article>`).join('')}</div>`;
    $('save-script-rows').onclick=()=>guarded(async()=>{
     const shots=rows.map(row=>{
      const card=document.querySelector(`[data-script-row="${rows.indexOf(row)}"]`);
      const value=field=>card.querySelector(`[data-field="${field}"]`).value;
      return {...row,duration_seconds:Number(value('duration_seconds'))||row.duration_seconds,shot_size:value('shot_size'),camera:value('camera').trim(),frame:value('frame').trim(),action:value('action').trim(),dialogue:value('dialogue').trim(),setting:value('setting').trim(),character_ids:value('character_ids').split(/[,，;；\s]+/).map(part=>part.trim()).filter(Boolean)};
     });
     const script={characters:run.result?.script?.characters||[],shots};
     await api('save-script',{run_id:run.id,revision:run.revision,script});
     dirty=false;renderBook();notify('已保存新的脚本版本，请核对画面连贯与台词还原。');
    });
   }
  };
  select.onchange=()=>{if(!leaveDraft()){select.value=selectedResultRuns[resultKey]||selected?.id;return;}dirty=false;selectedResultRuns[resultKey]=select.value;show();};show();
 }catch(error){notify(error.message);}
}

// Reload the existing click listeners with cloud implementations (function bindings are shared).
async function boot(){try{await refreshCloud();dirty=false;route='home';render();document.querySelector('.prototype-tag').textContent='云端数据库 · 在线';document.querySelector('.sidebar-footer p').textContent='云端持久化 · 文本独立执行器';document.querySelector('.sidebar-footer .btn').textContent='↓ 导出云端数据';}catch(e){$('workspace').innerHTML=empty('暂时无法连接云端数据库',e.message);}}
boot();

})();
