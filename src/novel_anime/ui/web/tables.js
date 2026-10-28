(function(){
'use strict';
// Preserve native links, media players, text selection and existing event bindings.
// One adapter also covers tables inserted by asynchronous generation results.
let contextMenu=null,contextCell=null;
function closeContextMenu(restoreFocus=false){
 const cell=contextCell;contextMenu?.remove();contextMenu=null;contextCell=null;
 if(restoreFocus&&cell?.isConnected)cell.focus({preventScroll:true});
}
function cellText(cell){
 if(cell.hasAttribute('data-copy-text'))return cell.getAttribute('data-copy-text');
 const prompts=Array.from(cell.querySelectorAll('pre')).map(node=>node.textContent);
 if(prompts.length)return prompts.join('\n\n');
 const copy=cell.cloneNode(true);copy.querySelectorAll('button,.table-resize-handle').forEach(node=>node.remove());
 return copy.textContent.trim();
}
async function copyCell(cell){
 const value=cellText(cell);
 try{await navigator.clipboard.writeText(value);notify('已复制单元格内容。');}
 catch{notify('浏览器未允许复制，请选中文字后手动复制。');}
}
function selectRow(cell){
 const table=cell.closest('table'),row=cell.parentElement;
 table.querySelectorAll('.table-row-selected-cell').forEach(item=>item.classList.remove('table-row-selected-cell'));
 Array.from(table.rows).forEach(candidate=>{
  if(candidate.rowIndex>row.rowIndex)return;
  Array.from(candidate.cells).forEach(item=>{
   if(candidate===row||candidate.rowIndex+item.rowSpan>row.rowIndex)item.classList.add('table-row-selected-cell');
  });
 });
}
function openContextMenu(cell,x,y){
 closeContextMenu();contextCell=cell;cell.tabIndex=-1;
 const table=cell.closest('table');table.querySelector('.table-cell-selected')?.classList.remove('table-cell-selected');cell.classList.add('table-cell-selected');
 const menu=document.createElement('div');contextMenu=menu;menu.className='table-context-menu';menu.setAttribute('role','menu');menu.setAttribute('aria-label','表格操作');
 menu.innerHTML='<button type="button" role="menuitem" data-menu-row>选择本行</button><button type="button" role="menuitem" data-menu-copy>复制单元格</button>';
 document.body.appendChild(menu);
 const bounds=menu.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(x,window.innerWidth-bounds.width-8))+'px';menu.style.top=Math.max(8,Math.min(y,window.innerHeight-bounds.height-8))+'px';
 menu.querySelector('[data-menu-row]').onclick=()=>{selectRow(cell);closeContextMenu(true);};
 menu.querySelector('[data-menu-copy]').onclick=()=>{closeContextMenu(true);copyCell(cell);};
 const buttons=Array.from(menu.querySelectorAll('button'));buttons[0].focus({preventScroll:true});
 menu.onkeydown=event=>{
  if(event.key==='Escape'){event.preventDefault();closeContextMenu(true);}
  else if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();const index=buttons.indexOf(document.activeElement);buttons[(index+(event.key==='ArrowDown'?1:buttons.length-1))%buttons.length].focus();}
  else if(event.key==='Tab')closeContextMenu();
 };
}
document.addEventListener('pointerdown',event=>{if(contextMenu&&!contextMenu.contains(event.target))closeContextMenu();},true);
document.addEventListener('contextmenu',()=>closeContextMenu(),true);
document.addEventListener('scroll',()=>closeContextMenu(),true);
window.addEventListener('resize',()=>closeContextMenu());window.addEventListener('blur',()=>closeContextMenu());
function enhanceTable(table){
 if(table.dataset.tableEnhanced)return;
 table.dataset.tableEnhanced='true';table.classList.add('studio-table');
 const frame=document.createElement('div');frame.className='vtable-frame';frame.tabIndex=0;frame.setAttribute('role','region');
 const heading=table.closest('section,.panel,.cast')?.querySelector('h2,h3');
 frame.setAttribute('aria-label',(heading?.textContent?.trim()||'数据表格')+'，可横向滚动');
 table.parentNode.insertBefore(frame,table);frame.appendChild(table);
 // A merged first cell represents a storyboard parent and its subject rows.
 for(const tbody of table.tBodies){
  const rows=Array.from(tbody.rows);
  rows.forEach((row,index)=>{
   const first=row.cells[0],span=first?.rowSpan||1;
   if(span<=1)return;
   const children=rows.slice(index+1,index+span),merged=Array.from(row.cells).filter(cell=>cell.rowSpan>1);
   const treeLabel=table.dataset.treeLabel||'分镜的人物与环境';
   const toggle=document.createElement('button');toggle.type='button';toggle.className='table-tree-toggle';toggle.setAttribute('aria-expanded','true');toggle.setAttribute('aria-label','收起'+treeLabel);toggle.innerHTML='<span aria-hidden="true">▶</span>';
   first.prepend(toggle);children.forEach(child=>child.classList.add('table-tree-child'));
   toggle.onclick=()=>{const expanded=toggle.getAttribute('aria-expanded')!=='true';toggle.setAttribute('aria-expanded',String(expanded));toggle.setAttribute('aria-label',(expanded?'收起':'展开')+treeLabel);children.forEach(child=>{child.hidden=!expanded;});merged.forEach(cell=>{cell.rowSpan=expanded?span:1;});};
  });
 }
 if(table.tHead){
  const heads=table.tHead.rows[table.tHead.rows.length-1]?.cells||[];
  Array.from(heads).forEach((head,index)=>{
   if(head.colSpan>1)return;
   const handle=document.createElement('span');handle.className='table-resize-handle';handle.setAttribute('aria-hidden','true');head.appendChild(handle);
   handle.addEventListener('pointerdown',event=>{
    event.preventDefault();event.stopPropagation();
    const startX=event.clientX,startWidth=head.getBoundingClientRect().width,tableWidth=table.getBoundingClientRect().width;
    const columns=table.querySelectorAll('colgroup col'),column=columns[index];
    // Freeze measured column widths before dragging so adjacent columns keep their width.
    Array.from(heads).forEach((cell,i)=>{const width=cell.getBoundingClientRect().width;if(columns[i])columns[i].style.width=width+'px';cell.style.width=width+'px';});
    handle.setPointerCapture(event.pointerId);handle.classList.add('resizing');document.body.classList.add('table-is-resizing');
    const move=event=>{const width=Math.max(76,startWidth+event.clientX-startX);head.style.width=width+'px';head.style.minWidth=width+'px';if(column)column.style.width=width+'px';table.style.setProperty('width',tableWidth+width-startWidth+'px','important');table.style.minWidth=tableWidth+width-startWidth+'px';};
    const stop=()=>{handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',stop);handle.removeEventListener('pointercancel',stop);handle.classList.remove('resizing');document.body.classList.remove('table-is-resizing');};
    handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',stop);handle.addEventListener('pointercancel',stop);
   });
  });
 }
 table.addEventListener('contextmenu',event=>{
  if(event.target.closest('input,textarea,select,video'))return;
  const cell=event.target.closest('td,th');if(!cell||cell.closest('table')!==table)return;
  event.preventDefault();openContextMenu(cell,event.clientX,event.clientY);
 });
 table.addEventListener('keydown',event=>{
  if(event.key!=='ContextMenu'&&!(event.shiftKey&&event.key==='F10'))return;
  const cell=event.target.closest('td,th');if(!cell)return;
  event.preventDefault();const rect=cell.getBoundingClientRect();openContextMenu(cell,rect.left+20,rect.top+20);
 });
 table.addEventListener('click',event=>{
  if(event.target.closest('button,a,input,select,textarea,video,summary,details,.table-resize-handle'))return;
  const cell=event.target.closest('td,th');if(!cell||cell.closest('table')!==table)return;
  table.querySelector('.table-cell-selected')?.classList.remove('table-cell-selected');cell.classList.add('table-cell-selected');
 });
}
function update(){if(contextCell&&!contextCell.isConnected)closeContextMenu();document.querySelectorAll('table:not([data-table-enhanced])').forEach(enhanceTable);}
const observer=new MutationObserver(update);observer.observe(document.querySelector('.app')||document.body,{childList:true,subtree:true});update();
})();
