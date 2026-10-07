/* Local SHP geometry and actual paired outputs. No tiles, synthetic traffic, or model execution. */
(() => {
  const el = id => document.getElementById(id);
  const safe = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (x,d=3) => {
    if(x==null)return '—';const n=Number(x);
    if(n!==0&&Math.abs(n)<10**(-d))return Math.abs(n)<0.0001?n.toExponential(1):n.toLocaleString('zh-CN',{maximumSignificantDigits:2});
    return n.toLocaleString('zh-CN',{maximumFractionDigits:d});
  };
  const canvases = [el('map-before'),el('map-after')], panes = document.querySelector('.map-panes');
  const semantic=window.trafficMapView;
  const colors = {
    neutral:[[Infinity,'#a4adb5','道路底图']],
    speed:[[10,'#b83d4d','≤10'],[20,'#df6845','10–20'],[40,'#c68a24','20–40'],[Infinity,'#4d9675','>40 km/h']],
    vc:[[.8,'#a4adb5','V/C <0.8'],[1,'#c68a24','0.8–1'],[1.5,'#df6845','1–1.5'],[Infinity,'#b83d4d','>1.5']],
    change:[[0,'#238879','提速'],[1,'#c2c8cc','基本不变'],[2,'#c45351','降速']],
    impact:[[0,'#238879','耗时减少'],[1,'#c2c8cc','基本不变'],[2,'#c45351','耗时增加']]
  };
  let userRun=false;
  let state, signature='', data=null, referenceData=null, referenceCacheKey='', picked=null, ticket=0, controller=null, frame=0, mode='after';
  let view={x:0,y:0,span:1000}, searchResults=[], screenPaths=[[],[]];
  const box = links => {const b=[Infinity,Infinity,-Infinity,-Infinity];for(const l of links)for(const p of l.paths)for(const [x,y] of p){b[0]=Math.min(b[0],x);b[1]=Math.min(b[1],y);b[2]=Math.max(b[2],x);b[3]=Math.max(b[3],y);}return b;};
  function viewport(canvas){
    const w=canvas.clientWidth,h=canvas.clientHeight;
    if(mode==='split')return {w,h,left:16,right:w-16,top:65,bottom:h-40};
    return {w,h,left:24,right:w-24,top:145,bottom:h-(data?.reference?80:208)};
  }
  const activeCanvas=()=>canvases[mode==='before'?0:1];
  function fit(links,padding=1.25){if(!links.length)return;const b=box(links),p=viewport(activeCanvas()),w=Math.max(100,p.right-p.left),h=Math.max(100,p.bottom-p.top);view={x:(b[0]+b[2])/2,y:(b[1]+b[3])/2,span:Math.max(160,b[2]-b[0],(b[3]-b[1])*w/h)*padding};schedule();}
  function schedule(){if(!frame)frame=requestAnimationFrame(()=>{frame=0;draw(0);draw(1);});}
  const linkColor=(link,side)=>data.reference?'#b1b6bc':semantic.color(link,side,el('map-metric').value);
  function projection(canvas){const p=viewport(canvas),s=Math.max(100,p.right-p.left)/view.span,cx=(p.left+p.right)/2,cy=(p.top+p.bottom)/2;return {...p,s,cx,cy,point:q=>[(q[0]-view.x)*s+cx,(view.y-q[1])*s+cy]};}
  function draw(side){
    const canvas=canvases[side];if(!data||!canvas.clientWidth)return;
    const {w,h,s,cx,cy,left,right,top,bottom,point}=projection(canvas),dpr=Math.min(devicePixelRatio||1,2);
    if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);}
    const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
    ctx.fillStyle='#f0f1ef';ctx.fillRect(0,0,w,h);
    screenPaths[side]=[];const paths=[];
    for(const link of data.links){
      const b=link.bounds;if(b[2]<view.x-cx/s||b[0]>view.x+(w-cx)/s||b[3]<view.y-(h-cy)/s||b[1]>view.y+cy/s)continue;
      for(const line of link.paths){const offset=Math.min(3.5,7*s);const points=line.map(point).map((p,i,arr)=>{const a=arr[Math.max(0,i-1)],b=arr[Math.min(arr.length-1,i+1)],dx=b[0]-a[0],dy=b[1]-a[1],n=Math.hypot(dx,dy)||1;return [p[0]-dy/n*offset,p[1]+dx/n*offset];});paths.push({link,points});}
    }
    paths.sort((a,b)=>(a.link.group==='core'||a.link.id===picked)-(b.link.group==='core'||b.link.id===picked));
    ctx.lineCap='round';ctx.lineJoin='round';const trace=p=>{ctx.beginPath();p.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));};const labelBoxes=[];
    for(const {link,points} of paths){
      const closure=semantic.motorClosure(link,side);
      trace(points);if(view.span<16000){ctx.strokeStyle='#ffffff';ctx.lineWidth=link.group==='network'?4:6;ctx.stroke();}
      if(link.group==='core'||link.id===picked){ctx.strokeStyle=link.id===picked?'#2563eb':'#88a8d3';ctx.lineWidth=link.id===picked?6:5;ctx.stroke();}
      ctx.strokeStyle=linkColor(link,side);ctx.lineWidth=link.group==='network'?(view.span>16000?1.1:2.8):4;ctx.stroke();
      if(side&&link.treatments?.length){ctx.save();ctx.strokeStyle=closure?closure.color:'#3b6cac';ctx.lineWidth=closure?2.5:1.5;ctx.setLineDash(closure?[4,4]:[5,7]);ctx.stroke();ctx.restore();}
      screenPaths[side].push({link,points});
      const a=points[Math.max(0,Math.floor(points.length/2)-1)],b=points[Math.floor(points.length/2)],length=Math.hypot(b[0]-a[0],b[1]-a[1]);
      if(closure&&view.span<25000){const x=(a[0]+b[0])/2,y=(a[1]+b[1])/2;ctx.save();ctx.fillStyle=closure.color;ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.beginPath();ctx.moveTo(x-4,y);ctx.lineTo(x+4,y);ctx.stroke();ctx.restore();}
      else if(length>42&&view.span<14000){const x=(a[0]+b[0])/2,y=(a[1]+b[1])/2,angle=Math.atan2(b[1]-a[1],b[0]-a[0]);ctx.save();ctx.translate(x,y);ctx.rotate(angle);ctx.fillStyle=linkColor(link,side);ctx.beginPath();ctx.moveTo(5,0);ctx.lineTo(-3,-3);ctx.lineTo(-3,3);ctx.closePath();ctx.fill();ctx.restore();}
      if(view.span<14000&&length>65&&link.name&&(link.group!=='network'||length>105)){
        const text=link.name,x=(a[0]+b[0])/2,y=(a[1]+b[1])/2-11;ctx.font='11px "Microsoft YaHei",sans-serif';const tw=ctx.measureText(text).width;
        if(x<left+tw/2||x+tw/2>right||y<top||y>bottom||labelBoxes.some(z=>Math.abs(z[0]-x)<(z[2]+tw)/2+10&&Math.abs(z[1]-y)<22))continue;
        labelBoxes.push([x,y,tw]);ctx.fillStyle='#fafbf9eb';ctx.fillRect(x-tw/2-4,y-11,tw+8,16);ctx.fillStyle='#535e68';ctx.textAlign='center';ctx.fillText(text,x,y);
      }
    }
    // Labels use the same real directed edge in both panes, making numeric differences visible.
    if(!data.reference){
      const targets=[...data.links.filter(l=>l.id===picked),...data.links.filter(l=>l.treatments?.length),...data.links.filter(l=>l.group==='core')];
      const used=new Set(), placed=[];
      for(const link of targets){
        if(used.has(link.id)||placed.length>=3)continue;used.add(link.id);
        const points=link.paths[0],q=points[Math.floor(points.length/2)],start=points[0];
        const [x,y]=point([(q[0]+start[0])/2,(q[1]+start[1])/2]);
        const width=Math.min(155,w-32),height=42;
        if(x<10||x>w-10||y<0||y>h||(link.id!==picked&&(y<top+14||y>bottom-32)))continue;
        const lx=Math.max(8,Math.min(w-width-8,x-width/2)),ly=link.id===picked?Math.max(top-16,Math.min(bottom-height-4,y-48)):y-48;
        if(placed.some(b=>Math.abs(b[0]-lx)<width+8&&Math.abs(b[1]-ly)<height+8))continue;
        placed.push([lx,ly]);const values=side?link.after:link.before;
        ctx.fillStyle='#fffffff5';ctx.strokeStyle='#d1d9de';ctx.lineWidth=1;ctx.beginPath();ctx.roundRect(lx,ly,width,height,5);ctx.fill();ctx.stroke();
        ctx.textAlign='left';ctx.fillStyle='#35424e';ctx.font='bold 11px "Microsoft YaHei",sans-serif';
        ctx.fillText((link.name||'未命名道路').slice(0,8)+(semantic.motorClosure(link,side)?' · 禁行':side&&link.treatments.length?' · 治理':''),lx+8,ly+15);
        ctx.fillStyle=semantic.statuses[link.status[side?'after':'before']].color;ctx.font='11px "Microsoft YaHei",sans-serif';
        ctx.fillText(`${fmt(values[0],1)} km/h · V/C ${semantic.vcLabel(values[2])}`,lx+8,ly+32);
        ctx.strokeStyle='#74879b';ctx.beginPath();ctx.moveTo(x,ly+height);ctx.lineTo(x,y);ctx.stroke();
      }
    }
    // Node markers use case coordinates. No fabricated vehicles or moving traffic.
    if(data.turn&&view.span<18000){ctx.font='11px "Microsoft YaHei",sans-serif';ctx.textAlign='left';for(const node of data.turn){const link=data.links.find(l=>l.start===node||l.end===node);if(!link)continue;const coord=link.start===node?link.paths[0][0]:link.paths.at(-1).at(-1),[x,y]=point(coord);ctx.fillStyle='#59789f';ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#fffffff0';ctx.fillRect(x+6,y-17,ctx.measureText(String(node)).width+6,17);ctx.fillStyle='#3b4f68';ctx.fillText(String(node),x+9,y-5);}}
    const selectedLink=data.links.find(l=>l.id===picked);if(selectedLink){const [x,y]=point(selectedLink.paths[0][0]);ctx.strokeStyle='#2563eb';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.stroke();}
    const nx=left+13,ny=top+8;ctx.textAlign='center';ctx.font='11px sans-serif';ctx.fillStyle='#707b85';ctx.fillText('N',nx,ny);ctx.beginPath();ctx.moveTo(nx,ny+5);ctx.lineTo(nx-3,ny+15);ctx.lineTo(nx+3,ny+15);ctx.fill();
    const desired=70/s,unit=10**Math.floor(Math.log10(desired)),distance=([5,2,1].find(n=>n*unit<=desired)||1)*unit,px=distance*s;
    ctx.strokeStyle='#707b85';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(left+4,bottom-14);ctx.lineTo(left+4+px,bottom-14);ctx.stroke();ctx.textAlign='left';ctx.font='10px sans-serif';ctx.fillText(data.meters?(distance>=1000?`${fmt(distance/1000)} km`:`${fmt(distance)} m`):`${fmt(distance)} 坐标单位`,left+4,bottom-21);
  }
  function legend(){
    if(data?.comparison&&el('map-comparison-note'))el('map-comparison-note').textContent=semantic.caption(data.comparison,el('map-metric').value,data.links.length);
    if(data?.reference){el('map-colors').innerHTML='<span><i style="background:#b1b6bc"></i>案例道路几何 · 尚无交通指标</span>';el('map-before-name').textContent='';el('map-message').textContent='可缩放、拖动或搜索道路；新任务会读取自己的仿真指标。';return;}
    el('map-colors').innerHTML=colors[el('map-metric').value].map(c=>`<span><i style="background:${c[1]}"></i>${c[2]}</span>`).join('');el('map-before-name').textContent=data?`${data.baseline} · OD × ${data.factor}`:'';if(data)el('map-message').textContent=['change','impact'].includes(el('map-metric').value)?`${data.selected} − ${data.baseline} · 基准图为零变化参照`:(el('map-metric').value==='speed'?'按实际速度着色 · 两图共用色标与视角。点击道路可查看速度、流量和 V/C。':el('map-metric').value==='neutral'?'道路几何底图；切换路段状态或速度查看交通信息。':'V/C > 1 表示过饱和；实测分配结果，不是实时排队。滚轮缩放，点击道路查看指标。');
    if(data?.comparison.motor_closed_links)el('map-colors').innerHTML+='<span><i style="background:#963c47"></i>⊖ 机动车方向禁行</span>';
  }
  function options(links){searchResults=links.slice(0,200);el('map-link').innerHTML=searchResults.map(l=>`<option value="${l.id}">${safe(l.name||'未命名道路')} · ${l.start}→${l.end}${l.group==='core'?' · 核心':''}</option>`).join('');}
  function inspect(link){
    if(!link)return;picked=link.id;selectionCard(link);if(!searchResults.some(l=>l.id===picked))options([link,...searchResults]);el('map-link').value=String(picked);
    if(data.reference){el('map-detail').innerHTML=`<h4>${safe(link.name||'未命名道路')} · ${link.start} → ${link.end}</h4><p>${fmt(link.length)} km · ${link.geometry==='shp'?'案例SHP折线':'节点直连'}。</p><p class="empty">仅展示道路几何。速度、流量和治理前后差异将在本次基准完成后显示。</p>`;schedule();return;}
    const rows=[['速度 km/h',0],['流量 pcu/h',1],['V/C',2],['停车类别',3]].map(([label,i])=>`<tr><td>${label}</td><td>${i===2?semantic.vcLabel(link.before[i]):fmt(link.before[i])}</td><td>${i===2?semantic.vcLabel(link.after[i]):fmt(link.after[i])}</td><td>${i===3?'—':fmt(link.after[i]-link.before[i],i===2?5:3)}</td></tr>`).join('');
    const closure=semantic.motorClosure(link,1);
    const access=closure?`<p><b>${safe(closure.label)}</b> · ${safe(closure.response)}</p><p>仅关闭这一方向的小汽车、出租车、摩托车和货车，不关闭反向，不改变步行与非机动车路权。下表保留汇总输出，速度或低V/C不表示允许机动车通行。</p>`:'';
    el('map-detail').innerHTML=`<h4>${safe(link.name||'未命名道路')} · ${link.start} → ${link.end}</h4>${access}<div class="table-scroll"><table><thead><tr><th>指标</th><th>基准</th><th>方案</th><th>变化</th></tr></thead><tbody>${rows}</tbody></table></div><p>${{core:'固定核心区路段',neighbor:'固定相邻影响区',network:'全网其他路段'}[link.group]} · ${fmt(link.length)} km · ${link.geometry==='shp'?'案例SHP折线':'节点直连'}。停车为类别编码。</p>`;schedule();
  }
  function selectionCard(link){
    el('map-selection').hidden=!link||data.reference;
    if(!link||data.reference)return;
    const side=mode==='before'?'before':'after',v=link[side],closure=semantic.motorClosure(link,side==='after'),status=closure||semantic.statuses[link.status[side]], delta=link.delta.speed_kmh;
    el('map-selection').innerHTML=`<b>${safe(link.name||'未命名道路')} <small>${link.start} → ${link.end}</small></b><span style="color:${status.color}">${safe(status.label)} · ${closure?safe(closure.response):fmt(v[0],1)+' km/h'}</span>${data.comparison.comparable?`<small>${closure?'汇总':''}速度 ${fmt(link.before[0],1)} → ${fmt(link.after[0],1)} km/h（${delta>0?'+':''}${fmt(delta,1)}）</small>`:''}${link.treatments.length?`<small>${safe(link.treatments.join(' · '))}</small>`:''}`;
  }
  function comparisonControls(){
    const c=data.comparison;el('map-comparison').hidden=false;
    const details=c.comparable?`<div class="map-focus-links"><button data-focus="treated">治理 <b>${c.treated_links}</b></button><button data-focus="improved">耗时减少 <b>${c.improved_links}</b></button><button data-focus="worsened">耗时增加 <b>${c.worsened_links}</b></button><button data-focus="max-improved">最大改善 ↗</button><button data-focus="max-worsened">最大恶化 ↗</button><button data-focus="difference">看变化图 ↗</button></div>`:`<div class="map-focus-links"><button data-focus="congested">过饱和 <b>${c.counts.after.overloaded+c.counts.after.severe}</b> 条</button><button data-focus="core">定位研究区</button></div>`;
    const outcome=c.kind==='policy'?semantic.policyOutcome(state?.task,state?.runs?.find(r=>r.name===data.selected)):null;
    const notes=[];
    if(outcome?.target_met!=null)notes.push(`核心速度 ${fmt(outcome.core_speed_kmh,2)} / 追加治理目标 ${fmt(outcome.target_speed_kmh,2)} km/h（${outcome.target_met?'已达目标':'未达目标'}；与推荐约束分开判断）`);
    if(outcome?.network_oversaturated_net_km>0)notes.push(`全网过饱和净增 ${fmt(outcome.network_oversaturated_net_km)} km`);
    if(outcome?.network_oversaturated_gross_km!=null)notes.push(`全网毛新增 ${fmt(outcome.network_oversaturated_gross_km)} km`);
    el('map-comparison').innerHTML=`<p id="map-comparison-note">${safe(semantic.caption(c,el('map-metric').value,data.links.length))}</p>${notes.length?`<p class="map-outcome-note">${safe(notes.join('；'))}</p>`:''}${details}`;
    document.querySelectorAll('[data-view]').forEach(button=>button.disabled=button.dataset.view!=='after'&&!c.comparable);
    el('map-after-label').textContent=c.comparable?'候选方案':'现状';
    document.querySelector('[data-view="after"]').textContent=c.comparable?'方案':'现状';
    for(const option of el('map-metric').options)option.disabled=!c.comparable&&['change','impact'].includes(option.value);
    if(!c.comparable){mode='after';panes.dataset.mode=mode;if(['change','impact'].includes(el('map-metric').value))el('map-metric').value='vc';}
    document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===mode)));
  }
  function insights(){
    const c=data.comparison;
    el('hotspot-title').textContent=c.comparable?'变化最明显的方向':'现状瓶颈方向';el('hotspot-unit').textContent=c.comparable?'Δ qL/v':'速度 · V/C';
    el('hotspot-hint').textContent=c.comparable?'按耗时变化绝对值排序；包含改善和恶化':'按路段耗时排序；点击查看本次仿真状态';
    el('hotspots').innerHTML=c.highlights.map((l,i)=>`<button class="hotspot" data-link="${l.id}"><span class="rank">${i+1}</span><span class="road"><b>${safe(l.name||'未命名道路')}</b><small>${l.start} → ${l.end} · ${safe((semantic.motorClosure(l,1)||semantic.statuses[l.status.after]).label)}</small></span><em class="${l.delta.time_pcu_h<0?'good':'bad'}">${c.comparable?(l.delta.time_pcu_h>0?'+':'')+fmt(l.delta.time_pcu_h,2):fmt(l.after[0],1)}<small>${c.comparable?'pcu·h':'km/h'}</small></em></button>`).join('')||'<p class="empty">暂无超过显示阈值的路段变化</p>';
    if(!c.comparable){
      const m=data.metrics.after.network;
      el('map-kpis').innerHTML=[['读取有向路段',data.links.length,'条'],['全网过饱和',fmt(m.oversaturated_km,2),'km'],['全网平均速度',fmt(m.weighted_speed_kmh,1),'km/h']].map(([label,value,unit])=>`<div><span>${label}</span><strong>${value}<small>${unit}</small></strong></div>`).join('');
      el('network-count').textContent=`${data.links.length.toLocaleString()} 条有向道路 · 现状 OD × ${data.factor}`;
      return;
    }
    const rows=[[data.regions?.length>1?'候选区并集速度变化':'核心区速度变化','core','weighted_speed_kmh',true],['全网耗时变化','network','running_time_pcu_h',false],['邻区耗时变化','neighbor','running_time_pcu_h',false]];
    el('map-kpis').innerHTML=rows.map(([label,scope,key,higher])=>{const a=data.metrics.before[scope][key],b=data.metrics.after[scope][key],delta=a>0&&b!=null?(b/a-1)*100:null;return `<div><span>${label}</span><strong class="${delta==null||Math.abs(delta)<1e-6?'':(higher?delta>0:delta<0)?'good':'bad'}">${delta==null?'—':(delta>0?'+':'')+fmt(delta,2)}<small>${delta==null?'':'%'}</small></strong></div>`;}).join('');
    el('network-count').textContent=`${data.links.length.toLocaleString()} 条有向道路 · 同需求 OD × ${data.factor}`;
  }
  function clearDetails(){el('map-selection').hidden=true;el('map-comparison').hidden=true;el('map-kpis').innerHTML=['核心区速度变化','全网耗时变化','邻区耗时变化'].map(s=>`<div><span>${s}</span><strong>—</strong></div>`).join('');el('hotspots').innerHTML='<p class="empty">等待所选方案的空间输出</p>';el('map-detail').innerHTML='<p class="empty">等待所选方案路段数据</p>';el('map-link').innerHTML='';el('map-source').textContent='';el('network-count').textContent='路网尚未载入';}
  function referenceControls(reference){
    el('map-metric').disabled=reference;el('map-fit').disabled=reference;el('map-kpis').hidden=reference;
    el('map-core-key').hidden=reference;
    el('map-mode-note').textContent=reference?'案例几何 · 等待本次仿真':'基于已完成仿真 · 非实时路况';
    el('map-after-label').textContent=reference?'案例底图':'方案';
    canvases[1].setAttribute('aria-label',`${reference?'案例':'方案'}路网地图，方向键平移，加减键缩放`);
    document.querySelectorAll('[data-view]').forEach(button=>{button.disabled=reference;if(button.dataset.view==='after')button.textContent=reference?'案例底图':'方案';});
    if(reference){el('map-colors').innerHTML='<span><i style="background:#b1b6bc"></i>案例道路几何 · 尚无交通指标</span>';mode='after';panes.dataset.mode=mode;document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===mode)));}
  }
  async function overview(){
    const key=state?.id||'configured';
    if(referenceCacheKey!==key){referenceCacheKey=key;referenceData=null;if(data?.reference)data=null;}
    if(data?.reference){el('map-condition').textContent=state?'案例底图 · 等待本次基准':'案例底图 · 尚无实验';return;}
    const t=++ticket;controller?.abort();controller=new AbortController();data=null;picked=null;clearDetails();el('map-content').hidden=true;referenceControls(true);
    el('map-condition').textContent='正在读取案例底图';el('map-condition').classList.remove('rejected');el('map-message').textContent='读取本地案例道路几何，不调用交通模型…';
    try{
      if(!referenceData){const url=state?`/api/reference-map?job=${encodeURIComponent(state.id)}`:'/api/reference-map';const response=await fetch(url,{signal:controller.signal});const result=await response.json();if(!response.ok)throw Error(result.error);if(t!==ticket)return;referenceData=result;}
      if(t!==ticket)return;data=referenceData;window.showDataset?.(data.dataset);for(const link of data.links)link.bounds=box([link]);el('map-content').hidden=false;
      el('map-condition').textContent=state?'案例底图 · 等待本次基准':'案例底图 · 尚无实验';el('map-after-name').textContent='';
      el('map-source').textContent=`${data.coordinate_system}；${data.matched_shapes}/${data.links.length} 条匹配案例SHP。${data.matched_shapes<data.links.length?data.geometry_note+'。':''}未读取历史仿真指标。`;
      el('network-count').textContent=`${data.links.length.toLocaleString()} 条案例道路 · 仅几何`;
      el('hotspots').innerHTML='<p class="empty">首次基准完成后展示真实路段指标</p>';el('map-detail').innerHTML='<p class="empty">可点击或搜索道路，先查看位置与方向</p>';
      options(data.links);fit(data.links,1.1);legend();
    }catch(e){if(t===ticket&&e.name!=='AbortError'){el('map-condition').textContent='案例底图不可用';el('map-message').textContent=e.message;el('network-count').textContent='案例底图读取失败';}}
  }
  async function load(){
    if(!state)return;const id=state.id,name=el('map-run').value,t=++ticket;controller?.abort();controller=new AbortController();data=null;picked=null;referenceControls(false);clearDetails();el('map-content').hidden=true;el('map-message').textContent='正在读取所选方案与同需求基准的实际路网输出…';el('map-condition').textContent='正在载入';el('map-condition').classList.remove('rejected');
    try{const response=await fetch(`/api/jobs/${id}/map?run=${encodeURIComponent(name)}`,{signal:controller.signal});const result=await response.json();if(!response.ok)throw Error(result.error);if(t!==ticket)return;data=result;window.showDataset?.(data.dataset);for(const l of data.links)l.bounds=box([l]);el('map-content').hidden=false;
      el('map-condition').textContent=`OD × ${data.factor} · ${data.eligible===false?'候选被拒绝':data.eligible===true?'候选通过约束':'现状观测'}`;el('map-condition').classList.toggle('rejected',data.eligible===false);el('map-after-name').textContent=data.selected;
      el('map-source').textContent=`${data.coordinate_system}；${data.matched_shapes}/${data.links.length} 条匹配案例SHP。${data.matched_shapes<data.links.length?data.geometry_note+'。':''}地图仅解释已完成的模型输出。`;
      comparisonControls();const study=data.links.filter(l=>l.group!=='network');options(study.sort((a,b)=>(b.group==='core')-(a.group==='core')));inspect(study.find(l=>l.treatments.length)||study[0]||data.links[0]);const treated=study.filter(l=>l.treatments.length);fit(treated.length?treated:study.length?study:data.links,treated.length?3:1.35);legend();insights();
    }catch(e){if(t===ticket&&e.name!=='AbortError'){el('map-condition').textContent='地图未就绪';el('map-message').textContent='地图无法显示：'+e.message;}}
  }
  function update(next){
    const runs=next.runs.filter(r=>r.metrics&&r.execution?.status==='completed'),key=next.id+'|'+next.status+'|'+next.recommendation+'|'+runs.map(r=>r.name+':'+String(r.comparison?.eligible)).join('|');state=next;if(key===signature)return;const different=!signature.startsWith(next.id+'|');signature=key;if(different)userRun=false;
    const previous=el('map-run').value;el('map-run').disabled=!runs.length;el('map-run').innerHTML=runs.map(r=>`<option value="${safe(r.name)}">${r.demand_factor} · ${safe(r.name)}${next.status==='completed'&&r.name===next.recommendation?' · 原推荐':''}</option>`).join('');
    if(!runs.length){overview();return;}
    el('map-run').value=!different&&userRun&&runs.some(r=>r.name===previous)?previous:semantic.preferredRun(runs,next.recommendation);
    if(different||!data||previous!==el('map-run').value||data.eligible!==(runs.find(r=>r.name===el('map-run').value)?.comparison?.eligible??null))load();
  }
  function zoom(mult){view.span=Math.max(60,Math.min(600000,view.span*mult));schedule();}
  el('map-run').onchange=()=>{userRun=true;load();};el('map-metric').onchange=()=>{legend();schedule();};el('map-fit').onclick=()=>data&&fit(data.links.filter(l=>l.group!=='network'));el('map-all').onclick=()=>data&&fit(data.links,1.1);el('map-plus').onclick=()=>zoom(.7);el('map-minus').onclick=()=>zoom(1/.7);
  el('map-link').onchange=()=>{const link=data?.links.find(l=>l.id===Number(el('map-link').value));if(link){inspect(link);fit([link],3);}};
  el('hotspots').onclick=e=>{const button=e.target.closest('[data-link]');if(!button||!data)return;const link=data.links.find(l=>l.id===Number(button.dataset.link));if(link){inspect(link);fit([link],3);}};
  el('map-comparison').onclick=e=>{const b=e.target.closest('[data-focus]');if(!b||!data)return;if(b.dataset.focus==='difference'){mode='after';panes.dataset.mode=mode;el('map-metric').value='impact';document.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.view===mode)));const changed=data.links.filter(l=>l.changed);if(changed.length)fit(changed,1.25);legend();schedule();return;}if(b.dataset.focus.startsWith('max-')){const link=semantic.extreme(data.links,b.dataset.focus.slice(4));if(link){inspect(link);fit([link],3);}else el('map-message').textContent='当前对照没有这一类路段，未改变地图。';return;}const rules={treated:l=>l.treatments.length,improved:l=>l.delta.time_pcu_h<-.01,worsened:l=>l.delta.time_pcu_h>.01,congested:l=>l.after[2]>1,core:l=>l.group==='core'};const links=data.links.filter(rules[b.dataset.focus]);if(links.length){fit(links,1.3);inspect(links[0]);}else el('map-message').textContent='当前对照没有这一类路段，未改变地图。';};
  function search(){if(!data)return;const q=el('map-search').value.trim().toLowerCase().replaceAll('->','→');const matches=data.links.filter(l=>`${l.name} ${l.start}→${l.end}`.toLowerCase().includes(q));if(!q||!matches.length){el('map-message').textContent='未找到匹配道路，请输入道路名称或节点编号。';return;}options(matches);inspect(matches[0]);fit(matches,2);el('map-message').textContent=`找到 ${matches.length} 条有向路段${matches.length>200?'，列表显示前200条':''}，可从右侧列表选择。`;}
  el('map-find').onclick=search;el('map-search').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();search();}};
  document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{mode=button.dataset.view;if(mode==='split'&&['neutral','vc'].includes(el('map-metric').value)){el('map-metric').value='speed';legend();}panes.dataset.mode=mode;document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));if(data&&!data.reference)selectionCard(data.links.find(l=>l.id===picked));schedule();});
  canvases.forEach((canvas,side)=>{
    let drag=null;
    canvas.addEventListener('wheel',e=>{if(!data)return;e.preventDefault();zoom(e.deltaY>0?1.15:1/1.15);},{passive:false});
    canvas.onpointerdown=e=>{if(!data||drag||!e.isPrimary||e.button!==0)return;canvas.focus({preventScroll:true});canvas.setPointerCapture(e.pointerId);drag={id:e.pointerId,x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false};};
    canvas.onpointermove=e=>{if(!drag||e.pointerId!==drag.id)return;const s=projection(canvas).s;view.x-=(e.clientX-drag.x)/s;view.y+=(e.clientY-drag.y)/s;drag.moved ||= Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)>4;drag.x=e.clientX;drag.y=e.clientY;schedule();};
    canvas.onpointerup=e=>{if(!drag||e.pointerId!==drag.id)return;const click=!drag.moved;drag=null;if(!click)return;const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;let nearest=null,best=9;
      for(const item of screenPaths[side])for(let i=1;i<item.points.length;i++){const a=item.points[i-1],b=item.points[i],dx=b[0]-a[0],dy=b[1]-a[1],t=Math.max(0,Math.min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy||1))),d=Math.hypot(x-a[0]-t*dx,y-a[1]-t*dy);if(d<best){best=d;nearest=item.link;}}inspect(nearest);};
    canvas.onpointercancel=canvas.onlostpointercapture=()=>drag=null;
    canvas.onkeydown=e=>{if(!data)return;const amount=view.span*.12;if(e.key==='ArrowLeft')view.x-=amount;else if(e.key==='ArrowRight')view.x+=amount;else if(e.key==='ArrowUp')view.y+=amount;else if(e.key==='ArrowDown')view.y-=amount;else if(e.key==='+'||e.key==='=')zoom(.7);else if(e.key==='-')zoom(1/.7);else return;e.preventDefault();schedule();};
    new ResizeObserver(schedule).observe(canvas);
  });
  legend();window.trafficMap={update,overview,redraw:schedule,focusIndices:indices=>{const links=data?.links.filter(l=>indices.includes(l.id))||[];if(links.length){fit(links,2);inspect(links[0]);}},selectRun:name=>{if([...el('map-run').options].some(o=>o.value===name)){el('map-run').value=name;userRun=true;return load();}}};
})();
