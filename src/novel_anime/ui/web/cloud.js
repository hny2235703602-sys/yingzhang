(function(){
/* Cloud adapter: reuse the approved layout, persist via the existing business service. */
let cloudBusy=false;
async function api(path,body){const r=await fetch('/api/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const value=await r.json();if(!r.ok)throw Error(value.error||'云端请求失败');return value;}
async function refreshCloud(){const state=await api('state');data.novels=state.novels;data.profiles=state.profiles;if(state.templates)data.templates=state.templates;if(!data.profiles.some(p=>p.id===data.activeProfile))data.activeProfile=data.profiles[0]?.id||'';}
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
window.render = function (){originalRender();cloudCopy();if(route==='prompts')renderCloudPrompts();}
function renderCloudPrompts(){
 const form=$('prompt-form');
 form.querySelector('[type=submit]').textContent='保存提示词';
 form.querySelector('.actions .muted').textContent='保存后用于新提交的任务；已提交任务保留原提示词。';
 document.querySelectorAll('.prompt-grid textarea').forEach(t=>{t.style.minHeight='320px';t.addEventListener('input',()=>dirty=true);});
 form.addEventListener('submit',e=>{e.preventDefault();e.stopImmediatePropagation();guarded(async()=>{const templates={};for(const stage of ['analysis','outline','shots','review'])templates[stage]=$('prompt-'+stage).value;await api('prompts',templates);data.templates=templates;dirty=false;notify('提示词已保存，新提交的任务将使用此版本。');});},true);
}
window.renderModels = function (){
 const p=data.profiles.find(p=>p.id===data.activeProfile)||data.profiles[0]||{};
 if(!document.getElementById('cloud-key-style')){
  const style=document.createElement('style');
  style.id='cloud-key-style';
  style.textContent='.key-wrap{position:relative;flex:1;min-width:0}.key-wrap input{padding-right:34px}#key-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);width:22px;height:22px;margin:0;padding:0;border:0;border-radius:50%;background:transparent;color:#6d7b70;font-size:18px;line-height:20px;cursor:pointer}#key-clear:hover:not(:disabled){background:#e6eee7;color:#243028}#key-clear:disabled{opacity:.35;cursor:default}#key-clear:active{transform:translateY(-50%)}';
  document.head.appendChild(style);
 }
 const modelOptions=p.model?`<option value="">请选择模型</option><option value="${esc(p.model)}" selected>${esc(p.model)}</option>`:'<option value="">请选择模型</option>';
 $('workspace').innerHTML=`<section class="panel" style="max-width:820px;margin:auto"><form id="simple-model" autocomplete="off"><div class="section-head"><div><h2>启用模型解析</h2><p>关闭后禁止使用此配置提交新任务，正在执行的任务不受影响。</p></div><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" role="switch" aria-label="启用模型解析" id="model-enabled" ${p.enabled!==false?'checked':''}></label></div>${data.profiles.length>1?`<label class="field">当前配置<select id="simple-profile">${data.profiles.map(x=>`<option value="${esc(x.id)}" ${x.id===p.id?'selected':''}>${esc(x.name)} · ${esc(x.model)}</option>`).join('')}</select></label>`:''}<label class="field">API 地址<input id="simple-url" type="url" required value="${esc(p.url||'')}" placeholder="https://api.example.com/v1"><small>填写服务根地址，系统会请求该地址下的 chat/completions。</small></label><div style="display:grid;grid-template-columns:minmax(0,1fr) 120px;gap:16px"><label class="field">模型名称<select id="simple-name" required>${modelOptions}</select><small>填写 API 地址和 API Key 后自动获取列表，再从下拉框选择。</small></label><label class="field">温度<input id="simple-temperature" type="number" min="0" max="2" step="0.1" required value="${p.temperature??1}"><small>0 到 2，部分模型只接受 1。</small></label></div><label class="field">超时（秒）<input id="simple-timeout" type="number" min="10" max="600" required value="${p.timeout||120}"><small>10 到 600 秒。上下文和输出 token 不再设上限，由模型服务自己的窗口决定。</small></label><label class="field">API Key<div style="display:flex;gap:8px;align-items:center"><div class="key-wrap"><input id="simple-key" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${p.key_configured?'已配置，留空则不修改':'填写 API Key'}"><button type="button" id="key-clear" aria-label="清除 API Key">×</button></div><button class="btn" type="button" style="white-space:nowrap" id="toggle-key" aria-label="显示输入的密钥">显示</button></div><small id="key-status">${p.key_configured?'已配置':'尚未配置'}。填写后仅显示后四位；已保存的密钥不会完整回显。</small></label><div class="actions"><button class="btn primary" type="submit">保存设置</button><span class="muted" id="model-feedback" role="status"></span></div></form></section>`;
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

const statuses={pending:'等待执行',running:'执行中',pause_requested:'请求暂停',paused:'已暂停',succeeded:'已完成',review_required:'待审阅',failed:'失败',unknown:'执行状态待核实'};
const pipeline=[['prepare','准备原文'],['analysis','本章分析'],['outline','改编大纲'],['shots','分镜工作台'],['review','复核']];
function stageKey(stage){const name=String(stage||'prepare').split(' ')[0];return pipeline.some(([key])=>key===name)?name:'prepare';}
function stageDetail(stage,run){const raw=String(stage||'prepare').trim();const key=stageKey(raw);const label=pipeline.find(([id])=>id===key)[1];const result=run?.result||{};const scenes=(result.outline?.scenes||[]).length;if(key==='review'&&scenes){const done=Object.keys(result.review_parts||{}).length;return `${label} ${Math.min(done+(result.review?0:1),scenes)}/${scenes}`;}if(key==='shots'&&scenes){const done=Object.keys(result.shot_batches||{}).length;if(done&&done<scenes)return `${label} ${Math.min(done+1,scenes)}/${scenes}`;}const rest=raw.slice(key.length).trim();if(!rest)return label;if(key==='analysis'&&/^\d+\/\d+$/.test(rest))return `${label} ${rest}`;return `${label} · ${rest}`;}
function stepStates(run){const result=run?.result||{};const done={prepare:Array.isArray(result.paragraphs)&&result.paragraphs.length>0,analysis:!!result.analysis,outline:!!result.outline,shots:Array.isArray(result.shots)&&result.shots.length>0,review:!!result.review};const current=run?stageKey(run.stage):'';const live=!!run&&['pending','running','pause_requested'].includes(run.status);const failed=!!run&&['failed','unknown'].includes(run.status);const paused=!!run&&run.status==='paused';const finished=!!run&&['succeeded','review_required'].includes(run.status);return pipeline.map(([key,label])=>{let state='等待';if(finished||done[key]&&key!==current)state='已完成';else if(key===current&&live)state=run.status==='pending'?'即将开始':run.status==='pause_requested'?'正在暂停':'正在执行';else if(key===current&&paused)state='已暂停';else if(key===current&&failed)state='失败';else if(done[key])state='已完成';return {key,label,state,current:!!run&&key===current};});}
function runningLine(run,steps){if(!run)return '';const name=stageDetail(run.stage,run);if(run.status==='pending')return `即将开始：${name}`;if(run.status==='running')return `正在执行：${name}`;if(run.status==='pause_requested')return `正在暂停：${name}`;if(run.status==='paused')return `已暂停：${name}`;if(run.status==='failed'||run.status==='unknown')return `停在：${name}`;if(run.status==='review_required')return '全部阶段已完成，待审阅';if(run.status==='succeeded')return '全部阶段已完成';return steps.find(step=>step.current)?.label||'';}
function startRun(){if(dirty){notify('请先保存章节，再开始改编。');return;}if(!chapterId){notify('请先保存章节。');return;}const profile=data.activeProfile||data.profiles[0]?.id;if(!profile){notify('请先在模型配置中保存并启用模型。');return;}if(!confirm('将按顺序执行本章分析、改编大纲、分镜和复核。此操作可能产生 API 费用。'))return;guarded(async()=>{await api('run',{chapter_id:chapterId,profile_id:profile,style:current().style,aspect_ratio:'16:9',preference:'',idempotency_key:uid()});notify('任务已提交，页面会显示正在执行的阶段。');showProgress();});}
function ensureRunButton(){if(tab!=='source')return;const host=document.querySelector('[data-text-page="source"]');if(!host||host.querySelector('#submit-cloud-task'))return;const button=document.createElement('button');button.type='button';button.className='btn primary';button.id='submit-cloud-task';button.textContent='开始文本改编';button.onclick=startRun;const note=document.createElement('span');note.id='run-now';note.className='muted';const actions=host.querySelector('.actions');if(actions){actions.appendChild(button);actions.appendChild(note);}else{const head=host.querySelector('.section-head');if(head){head.appendChild(button);head.appendChild(note);}}}
function paintRunNow(line,active){const note=document.querySelector(`[data-text-page="${tab}"] #run-now`);if(!note)return;note.textContent=line;note.style.color=active?'#2d503d':'';note.style.fontWeight=active?'600':'400';}
function statusColumn(){return document.getElementById('page-status');}
async function showProgress(){const aside=statusColumn();if(!aside)return;const id=chapterId,key=tab;clearTimeout(showProgress.timer);if(showProgress.chapter!==id){showProgress.signature='';showProgress.chapter=id;aside.dataset.ready='';}const paint=run=>{const steps=stepStates(run);const line=runningLine(run,steps);const active=!!run&&['pending','running','pause_requested','paused'].includes(run.status);const attention=active||!!run&&['failed','unknown'].includes(run.status);paintRunNow(attention?line:'',active);const signature=run?`${run.id}|${run.status}|${run.stage}|${run.updated_at||''}|${run.error_message||''}|${Object.keys(run.result?.review_parts||{}).length}|${Object.keys(run.result?.shot_batches||{}).length}`:'none';if(signature===showProgress.signature&&aside.dataset.ready==='1'){if(run&&['pending','running','pause_requested'].includes(run.status))showProgress.timer=setTimeout(showProgress,3000);return;}showProgress.signature=signature;aside.dataset.ready='1';const actions=run&&['pending','running'].includes(run.status)?`<button class="btn" data-run-action="pause" data-run-id="${run.id}" type="button">暂停</button>`:run&&['paused','pause_requested'].includes(run.status)?`<button class="btn" data-run-action="resume" data-run-id="${run.id}" type="button">继续</button>`:run&&['failed','unknown'].includes(run.status)?`<button class="btn" data-run-action="retry" data-run-id="${run.id}" type="button">重试 · 可能计费</button>`:'';const rows=steps.map(step=>{const on=step.state==='正在执行'||step.state==='即将开始'||step.state==='正在暂停';const mark=step.state==='已完成'?'✓':on?'●':step.state==='失败'?'!':'○';const color=step.state==='失败'?'#9a5348':on?'#2d503d':step.state==='已完成'?'#6f835e':'#8a968d';return `<li style="display:flex;gap:8px;align-items:baseline;padding:7px 0;color:${color};font-weight:${on?600:400}"><span>${mark}</span><span>${esc(step.label)}</span><span style="margin-left:auto;font-size:11px;font-weight:400">${esc(step.state)}</span></li>`;}).join('');aside.innerHTML=`<div class="eyebrow">改编进度</div><h3>${esc(line||'尚未开始')}</h3><ul style="list-style:none;margin:8px 0 0;padding:0;font-size:13px">${rows}</ul>${run&&run.status==='failed'&&run.error_message?`<p class="muted" style="margin-top:12px">${esc(run.error_message)}</p>`:''}<div class="actions">${actions}<button class="btn" id="refresh-progress" type="button">刷新</button></div>`;aside.querySelector('#refresh-progress').onclick=showProgress;aside.querySelectorAll('[data-run-action]').forEach(b=>b.onclick=()=>{if(b.dataset.runAction==='retry'&&!confirm('重试可能产生新的模型费用，确定继续？'))return;guarded(async()=>{await api(b.dataset.runAction,{id:b.dataset.runId});showProgress();});});if(run&&['pending','running','pause_requested'].includes(run.status))showProgress.timer=setTimeout(showProgress,3000);};if(!id){paint(null);return;}try{const runs=await api('runs/'+id);if(chapterId!==id||tab!==key)return;paint(runs[0]||null);}catch(e){if(chapterId===id&&tab===key)aside.innerHTML=`<div class="eyebrow">改编进度</div><h3>进度暂时无法读取</h3><p class="muted">${esc(e.message||'请稍后刷新。')}</p>`;}}
window.renderBook = function (){if(tab==='tasks')tab='source';originalRenderBook();cloudCopy($('text-pages'));document.querySelector('[data-text="tasks"]')?.remove();const key=tab;const finish=()=>{if(tab===key){if(key==='source')ensureRunButton();showProgress();}};if(['analysis','outline','shots'].includes(key))renderResults().finally(finish);else finish();}
function stageHtml(key,result){const missing=empty(key==='outline'?'这一版还没有改编大纲':key==='shots'?'这一版还没有镜头':'这一版还没有本章分析','换一个执行版本，或等当前任务保存后再打开。');if(!result)return missing;if(key==='outline'){const outline=result.outline;if(!outline)return missing;const scenes=(outline.scenes||[]).map((scene,index)=>`<article style="padding:16px 0;border-top:1px solid #e4eadc"><h3 style="margin:0 0 6px">${index+1}. ${esc(scene.title||scene.scene_id||'场景')}</h3><p class="muted">${esc([scene.location,scene.time].filter(Boolean).join(' · '))}</p><p style="white-space:pre-wrap;margin:8px 0 0">${esc(scene.script||'')}</p></article>`).join('');const changes=(outline.changes||[]).map(item=>`<li>${esc(item)}</li>`).join('');return `<p style="white-space:pre-wrap">${esc(outline.synopsis||'')}</p>${changes?`<h3>改编取舍</h3><ul>${changes}</ul>`:''}${scenes||missing}`;}if(key==='shots'){const shots=result.shots||[];if(!shots.length)return missing;const titles=Object.fromEntries((result.outline?.scenes||[]).map(scene=>[scene.scene_id,scene.title]));let sceneId='';return shots.map(shot=>{const heading=shot.scene_id===sceneId?'':(sceneId=shot.scene_id,`<h3 style="margin:22px 0 8px">${esc(titles[shot.scene_id]||shot.scene_id||'场景')}</h3>`);const dialogue=(shot.dialogue||[]).map(line=>`<p>对白：${esc(line)}</p>`).join('');return `${heading}<article style="padding:12px 0;border-top:1px solid #e4eadc"><b>${esc(shot.order||'')}. ${esc(shot.title||shot.shot_id||'')}</b><p class="muted">${esc([shot.shot_size,shot.camera,shot.duration_seconds?`${shot.duration_seconds} 秒`:''].filter(Boolean).join(' · '))}</p><p>${esc(shot.story_purpose||'')}</p><p>${esc(shot.action||'')}</p><p style="white-space:pre-wrap">${esc(shot.video_prompt||'')}</p>${dialogue}</article>`;}).join('');}const analysis=result.analysis;if(!analysis)return missing;const characters=(analysis.characters||[]).map(item=>`<li><b>${esc(item.name||'')}</b> ${esc(item.description||'')}</li>`).join('');const events=(analysis.events||[]).map(item=>`<li>${esc(item.description||'')}</li>`).join('');return `<p style="white-space:pre-wrap">${esc(analysis.summary||'')}</p>${analysis.core_conflict?`<p><b>核心冲突</b> ${esc(analysis.core_conflict)}</p>`:''}${characters?`<h3>人物</h3><ul>${characters}</ul>`:''}${events?`<h3>事件</h3><ul>${events}</ul>`:''}`;}
async function renderResults(){const id=chapterId,key=tab;if(!id)return;try{const runs=await api('runs/'+id);if(chapterId!==id||tab!==key)return;const hasStage=result=>key==='shots'?Array.isArray(result?.shots)&&result.shots.length>0:!!result?.[key];const available=runs.filter(run=>hasStage(run.result));const host=document.querySelector(`[data-text-page="${key}"]`);if(!available.length){host.innerHTML=chapterBar()+`<section class="panel">${empty(key==='outline'?'还没有改编大纲':key==='shots'?'还没有镜头':'还没有本章分析','当前章节还没有保存这一阶段的结果。')}</section>`;return;}host.innerHTML=chapterBar()+`<section class="panel"><div class="section-head"><h2>${key==='analysis'?'本章分析':key==='outline'?'改编大纲':'分镜工作台'}</h2>${badge(available[0].status==='review_required'?'待审阅':'已保存结果')}</div><label class="field">执行版本<select id="result-run">${available.map((run,index)=>`<option value="${index}">${esc(statuses[run.status]||run.status)} · ${esc(run.updated_at)}</option>`).join('')}</select></label><div id="result-view"></div></section>`;const select=host.querySelector('#result-run');const view=host.querySelector('#result-view');const show=()=>{view.innerHTML=stageHtml(key,available[Number(select.value)].result);};select.onchange=show;show();host.querySelector('#chapter-select').addEventListener('change',e=>{if(!leaveDraft()){e.target.value=chapterId||'';return;}dirty=false;chapterId=e.target.value||null;renderBook();});}catch(e){notify(e.message);}}
// Reload the existing click listeners with cloud implementations (function bindings are shared).
async function boot(){try{await refreshCloud();dirty=false;route='home';render();document.querySelector('.prototype-tag').textContent='云端数据库 · 在线';document.querySelector('.sidebar-footer p').textContent='云端持久化 · 文本独立执行器';document.querySelector('.sidebar-footer .btn').textContent='↓ 导出云端数据';}catch(e){$('workspace').innerHTML=empty('暂时无法连接云端数据库',e.message);}}
boot();

})();
