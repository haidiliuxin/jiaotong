const $ = id => document.getElementById(id);
const names = {created:'已创建',running:'运行中',completed:'已完成',incomplete:'研究未完成',failed:'运行失败',unreadable:'记录不可读',needs_validation:'需验证模型能力',no_candidate:'没有合适候选'};
const events = {completion_check:'研究完成核对',model_decision:'模型选择实验',model_review:'模型复核调度',model_policy_assessment:'模型候选实测反馈',turn_access_ready:'禁转分配环境就绪',turn_access_verified:'禁转分轮与参考已核验',hard_access_ready:'单向执行环境就绪',hard_access_verified:'车型分配与输入转换已核验',oneway_policy_knowledge:'单向政策依据',factorial_assessment:'单项与组合评估',sensitivity_feedback:'需求反馈',conditional_recommendation:'条件建议',tool_start:'开始计算',tool_end:'计算完成',prepare_scenario:'创建场景',knowledge_retrieval:'检索依据',diagnosis:'瓶颈诊断',feedback:'反馈决策',recommendation:'形成建议',failure:'任务失败',stop:'停止搜索',capability_gate:'能力检查'};
let selected = null, current = null, lastHistory = 0, lastRender = '', launching = false, networkError = '';
const escapeHTML = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = x => x == null ? '—' : Number(x).toLocaleString('zh-CN',{maximumFractionDigits:4});
function showDataset(value){
  const label=String(value||'案例路网').slice(0,80);
  $('dataset-context').textContent=label;
  $('dataset-coordinate').textContent=`${label} / CASE NETWORK`;
  $('dataset-title').textContent=`${label} · 城市路网推演`;
  $('dataset-footer').textContent=`${label} · 仿真数据 · 非实时交通`;
}
window.showDataset=showDataset;
function error(message=''){ $('error').hidden=!message; $('error').textContent=message; }
function taskArea(task){
  const parts=[];
  if(task.road_name)parts.push(task.road_name);
  if(task.turn?.length)parts.push('禁转 '+task.turn.join('→'));
  if(task.links?.length)parts.push((task.scenario==='oneway'?'关闭 ':task.turn?'核心 ':'')+task.links.map(pair=>pair.join('→')).join('、'));
  else if(task.turn?.length)parts.push('核心 '+task.turn.slice(1).join('→'));
  return parts.join(' · ')||'新基准自动选点';
}
function eventDetail(e){
  const d=e.data;
  if(e.event==='completion_check')return d.complete?'必需实测、同条件对照及模型复核全部完成':d.unanswered.join('；');
  if(e.event==='model_decision'||e.event==='model_review')return `${d.selected?.title||'停止新增实验'} · ${d.reason||d.status}`;
  if(e.event==='model_policy_assessment')return `${d.name} · ${d.eligible?'通过既定约束':d.violations.join('；')}`;
  if(e.event==='search_action')return `${d.name} · ${d.reason}`;
  if(e.event==='search_assessment')return `${d.name} · ${d.eligible?'通过既定约束':d.violations.join('；')}`;
  if(e.event==='turn_access_ready')return `路口 ${d.via} 的分配环境已准备`;
  if(e.event==='turn_access_verified')return `${d.scenario}：四车型五轮分配及原版参考检查通过`;
  return d.tool||d.module||d.reason||d.error||d.name||d.scenario||(e.event==='hard_access_ready'?'仅处理本任务新增禁行方向':e.event==='diagnosis'?`入选 ${d.selected?.length||0} 条路段`:(e.event==='knowledge_retrieval'||e.event==='oneway_policy_knowledge')?`${d.length} 条模型与治理依据`:JSON.stringify(d));
}
function drawTurnResponses(){
  const s=current.state;
  const rows=s.turn_access_profile?s.runs.flatMap(r=>(r.turn_response?.movements||[]).map(m=>({run:r,movement:m}))):[];
  $('turn-response-panel').hidden=!rows.length;
  $('turn-responses').innerHTML=rows.map(({run,movement})=>{
    const cells=['car','taxi','motor','truck'].map(mode=>{
      const flow=movement.flows[mode];
      return `<td>${number(flow.before)} → ${number(flow.after)}${flow.positive_baseline?'':'<br><small>未达正流量门槛</small>'}</td>`;
    }).join('');
    return `<tr><td>${escapeHTML(run.name)}<br><small>${escapeHTML(movement.turn.join('→'))}</small></td>${cells}<td>${movement.passed?'响应通过':'响应未通过'}<br><small>${run.comparison?.eligible?'指标通过':'指标未通过'}</small></td></tr>`;
  }).join('');
}
async function api(path, body){const response=await fetch(path,body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {}); const result=await response.json(); if(!response.ok)throw Error(result.error||`请求失败 ${response.status}`); return result;}
function payload(){const overrides={recommendation_version:'tolerance-v2',search_mode:$('robust-search').checked?'robust':'standard',max_links:Number($('links').value),max_runs:Number($('budget').value),demand_factors:$('sensitivity').checked?[0.9,1,1.1]:[1],repeat_baseline:$('repeat').checked,max_neighbor_time_increase_pct:Number($('neighbor').value),max_new_oversaturated_km:Number($('overload').value)};if($('road').value.trim())overrides.road_name=$('road').value.trim();if($('network-overload').value.trim())overrides.max_new_network_oversaturated_km=Number($('network-overload').value);return {request:$('request').value,overrides};}
function showPlan(plan){$('preview-card').open=true;const task=plan.task;const possible=plan.capability.state==='executable';$('plan-panel').innerHTML=`<h3>${'新任务预览 · 任务卡 · '+escapeHTML(plan.capability.name)}</h3><div class="task-card"><div><span>研究范围</span><b>${escapeHTML(taskArea(task))}</b></div><div><span>最多治理 / 仿真预算</span><b>${task.max_links} 条 / ${task.max_runs} 次</b></div><div><span>小汽车需求系数</span><b>${task.demand_factors.join(' / ')}</b></div></div><p>${possible?(task.turn?'每个需求测齐基准、停车、禁转及组合；检查四车型实际响应、邻区与全网指标，无合格候选则保留基准。':task.scenario==='oneway'?'每个需求分别计算无治理、双向停车、单方向禁行及组合，并检查禁行响应和邻区影响；无合格候选则保留基准。':task.search_mode==='robust'?'主动稳健搜索：先测高需求，按已测失败缩小范围；同一措施必须在全部需求通过才可统一推荐。每次决策写入后再实际调用 TranStar。':'将真实执行基准、候选及反馈；所有结果经新输出和输入一致性校验。'):'本任务不会调用停车治理流程。先完成：'+escapeHTML(plan.capability.requires.join('；'))}</p><details><summary>完整参数与知识依据</summary><pre>${escapeHTML(JSON.stringify(plan,null,2))}</pre></details>`;}
async function refresh(){
  const jobs=await api('/api/jobs');
  const usable=jobs.filter(j=>j.status!=='unreadable');
  if(jobs.some(j=>j.id===selected&&j.status==='unreadable')){
    selected=null;current=null;lastRender='';error('所选任务记录不可读，请选择其他历史任务');
    ['result-panel','conditional-panel','record-task-panel','turn-response-panel','report-link'].forEach(id=>{$(id).hidden=true;});
    $('events').innerHTML='';$('decisions').innerHTML='';$('robust-analysis').innerHTML='';
    const tradeoffs=$('tradeoffs');if(tradeoffs)tradeoffs.innerHTML='';
    $('recommendation').textContent='';$('event-count').textContent='0';$('progress-bar').style.width='0%';
  }
  if(!selected){const requested=new URLSearchParams(location.search).get('job');selected=usable.find(j=>j.id===requested)?.id||usable.find(j=>j.status==='completed')?.id||usable[0]?.id||null;}
  $('history').innerHTML=jobs.length?jobs.map(j=>`<button data-job="${escapeHTML(j.id)}" aria-current="${selected===j.id}"${j.status==='unreadable'?' disabled':''}>${escapeHTML(j.id)}<small><span class="job-state">${escapeHTML(names[j.status]||j.status)}</span><span>${j.status==='unreadable'?escapeHTML(j.error||'记录不可读'):escapeHTML(j.run_count||0)+' 次仿真'}</span></small></button>`).join(''):'尚无任务记录';lastHistory=Date.now();
  if(!selected){$('status').textContent='待开始';$('job-title').textContent=$('matcher').value==='model'?'描述交通问题，开始智能规划':'先预览任务，再开始仿真';$('job-progress').textContent=jobs.length?'历史任务记录不可读 · 当前只展示案例路网':'尚无实验记录 · 当前只展示案例路网';await window.trafficMap.overview();}
}
function draw(){if(!current)return;const s=current.state;$('job-title').textContent=s.id;$('status').textContent=names[s.status]||s.status;$('status').dataset.status=s.status;if(s.status==='completed'&&current.goal_review?.complete===false)$('status').textContent='仿真完成 · 研究未完整';if(current.conclusion_review?.status==='needs_review'){$('status').textContent='仿真记录已保留 · 结论待核对';$('status').dataset.status='incomplete';}const runs=s.runs.filter(r=>r.metrics);$('result-panel').hidden=!runs.length;const area=$('scope').value;$('metrics').innerHTML=runs.map(r=>{const m=r.metrics[area];return `<tr class="${s.status==='completed'&&r.name===s.recommendation?'winner':''}"><td>${escapeHTML(r.name)}<br><small>小汽车OD × ${r.demand_factor}</small></td><td>${number(m.weighted_speed_kmh)}</td><td>${number(m.running_time_pcu_h)}</td><td>${number(m.oversaturated_km)}</td></tr>`}).join('');$('recommendation').textContent=s.status==='completed'?`基准需求推荐：${s.recommendation}。${s.robust_across_tested_demand===true?'在已测需求水平通过约束。':s.robust_across_tested_demand===false?'部分需求水平未通过约束，推荐有适用条件。':(s.sensitivity.length?'已测额外需求；主推荐为基准，不声明治理稳健性。':'尚未验证额外需求水平。')}`:`最近记录：${s.active_scenario||names[s.status]||s.status}；已开始 ${s.run_count} 次仿真。`;$('decisions').innerHTML=s.decisions.length?s.decisions.map(d=>`<div class="decision"><b>读取 ${escapeHTML(d.observed_run)}</b><p>${escapeHTML(d.reason)}</p><p>约束：${escapeHTML(d.violations.join('；')||'通过')} · ${escapeHTML(d.execution_reason||(d.will_execute?'追加仿真':'不再追加该候选'))}</p></div>`).join(''):'<p class="hint">首轮结果完成后生成反馈。</p>';$('events').innerHTML=(current.events_skipped?'<p class="hint">已跳过 '+number(current.events_skipped)+' 条不可读日志记录。</p>':'')+current.events.slice().reverse().map(e=>{const d=e.data;const detail=eventDetail(e);return `<div class="event"><time>${new Date(e.time).toLocaleTimeString('zh-CN',{hour12:false})}</time><span>${events[e.event]||escapeHTML(e.event)} · ${escapeHTML(detail)}</span></div>`}).join('');$('report-link').hidden=!s.finished||s.status==='needs_validation';$('report-link').href=`/api/jobs/${s.id}/report.html`;if(s.error)error(s.error);else if(s.status==='needs_validation')error(s.missing.join('；'));}
function professionalImpact(view){
  if(!view?.policies?.length)return '';
  const scope=view.evidence_scope||{};
  const repeated={not_requested:'未要求基准复跑',not_completed:'基准复跑未完成',exact_match:'基准复跑一致',
    inconsistent_or_unverified:'基准复跑不一致或未经证实'}[scope.repeat_status]||'复跑情况未知';
  const candidates=view.policies.map(p=>`<div class="decision"><b>${escapeHTML(p.run)} · 需求 ×${escapeHTML(p.demand_factor)}</b>${p.status==='measured'?`<p>核心有向路段 ${escapeHTML(p.worsened_link_count)} / ${escapeHTML(p.core_link_count)} 条耗时增加；正增量 ${number(p.gross_added_pcu_h)} pcu·h，改善量 ${number(p.gross_saved_pcu_h)} pcu·h，净变化 ${number(p.net_change_pcu_h)} pcu·h。</p>${(p.largest_increases||[]).map(l=>`<small>${escapeHTML(l.name)} ${escapeHTML(l.from_node)}→${escapeHTML(l.to_node)}：+${number(l.added_pcu_h)} pcu·h</small>`).join('')}`:`<p>局部配对未验证：${escapeHTML(p.gap)}</p>`}</div>`).join('');
  return `<section class="professional-impact"><h3>局部影响与证据边界</h3><p>${escapeHTML(view.rule)}</p>${candidates}<p class="hint">已设需求 ${escapeHTML((scope.demand_levels||[]).map(x=>'×'+x).join('、')||'无')}；${escapeHTML(repeated)}。本任务记录未证明现场标定或独立验证，也未估计统计置信区间。</p><p class="hint">${escapeHTML(view.boundary)}</p><details><summary>后续证据建议 · 不自动追加仿真</summary><ul>${(view.next_checks||[]).map(x=>`<li>${escapeHTML(x.action)}</li>`).join('')}</ul></details></section>`;
}
function drawConditional(){
  let panel=$('tradeoffs');
  if(!panel){panel=document.createElement('section');panel.id='tradeoffs';$('conditional-panel').before(panel);}
  panel.innerHTML='<h3>原任务规则与不利影响</h3><p>'+escapeHTML(current.selection_rule||current.state.recommendation_rule||'按原任务规则')+'</p><p>通过约束不等于所有指标改善；以下是相同需求基准下的恶化项。净增与毛新增分别核算。</p>'+(current.tradeoffs||[]).map(item=>'<p><b>'+escapeHTML(item.scenario)+' · OD × '+escapeHTML(item.demand_factor)+'</b>：'+escapeHTML(item.warnings.join('；'))+'</p>').join('');
  panel.innerHTML+=professionalImpact(current.professional_review);
  const s=current.state;
  $('conditional-panel').hidden=!s.sensitivity.length;
  $('conditional').innerHTML=s.sensitivity.map(item=>{
    const a=item.adaptation;
    const condition=s.recommendation_by_car_demand?.[String(item.car_demand_factor)] || '尚在验证';
    return `<div class="decision"><b>小汽车需求 × ${item.car_demand_factor}</b><p>原推荐措施：${escapeHTML(item.comparison.note||item.comparison.violations.join('；')||'通过当前约束')}</p>${a?`<p>反馈：${escapeHTML(a.reason)}。${escapeHTML(a.comparison?.violations.join('；')||'')}</p>`:''}<p>该需求下建议：${escapeHTML(condition)}</p></div>`;
  }).join('');
  $('report-link').hidden=!s.finished;
}
function drawRecordedTask(){
  const task=current.state.task;
  const panel=$('record-task-panel');
  panel.hidden=!task;
  if(!task)return;
  const area=taskArea(task);
  panel.innerHTML=`<h3>当前实验的实际任务</h3><p>${escapeHTML(task.request)}</p><div class="task-card"><div><span>研究范围</span><b>${escapeHTML(area)}</b></div><div><span>最多治理 / 仿真预算</span><b>${task.max_links} 条 / ${task.max_runs} 次</b></div><div><span>小汽车需求系数 / 基准复跑</span><b>${task.demand_factors.join(' / ')} · ${task.repeat_baseline?'复跑':'不复跑'}</b></div></div><details><summary>已保存的完整参数</summary><pre>${escapeHTML(JSON.stringify(task,null,2))}</pre></details>`;
}
function showPane(name){
  document.querySelectorAll('[data-pane]').forEach(button=>{const active=button.dataset.pane===name;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;$('pane-'+button.dataset.pane).hidden=!active;});
}
function drawOriginalAnalysis(){
  const state=current.state;
  if(state.task.search_mode==='robust'){
    $('decisions').innerHTML=`<div class="decision"><b>主动实验控制器</b><p>${escapeHTML(state.robust_search?.stop_reason||'按已保存协议逐步执行，当前没有最终推荐。')}</p></div>`+(state.search_trace||[]).map(t=>`<div class="decision"><b>${t.sequence}. ${escapeHTML(t.name)}</b><p>${escapeHTML(t.reason)}</p>${t.observed_run?`<p>读取结果：${escapeHTML(t.observed_run)}</p>`:''}${t.ranking.length?`<p>联合治理观测的耗时减少量：${t.ranking.map(r=>`${r.from_node}→${r.to_node} ${number(r.saved_time_pcu_h)} pcu·h`).join('；')}。下一步仍须实际仿真确认。</p>`:''}<small>决策写入于 ${new Date(t.decided_at).toLocaleTimeString('zh-CN',{hour12:false})}</small></div>`).join('');
  }
  const a=current.analysis;if(!a){$('robust-analysis').innerHTML='<p class="empty">本次服务尚未提供稳健分析，请重启新版服务。</p>';return;}
  $('robust-analysis').innerHTML=`<div class="analysis-winner"><small>${a.finalized?'已测需求内的筛选结论':'阶段观察 · 不作稳健推荐'}</small><strong>${escapeHTML(a.recommendation||'暂不推荐统一措施')}</strong><p>${escapeHTML(a.reason)}</p></div><p class="hint">${a.eligible_count} 项通过全部设定需求 · ${a.frontier_count} 项位于权衡前沿。${state.task.search_mode==='robust'?'主动搜索以此规则形成统一建议；条件建议另行列出。':'此为新增稳健分析，原实验结论保留。'}</p>`+
    a.policies.map(p=>`<div class="policy-card ${p.feasible?'':'rejected'}"><h4>${escapeHTML(p.name)}<span>${p.feasible?(p.frontier?'权衡前沿':'存在更优权衡'):p.checks.some(c=>!c.accepted)?'约束未通过':p.missing.length?'证据未齐':'不可推荐'}</span></h4><div class="factor-chips">${a.factors.map(f=>{const c=p.checks.find(c=>c.factor===f);return `<span class="${!c?'missing':c.accepted?'':'fail'}" title="${escapeHTML(!c?'缺少配对实验':c.accepted?'通过原实验约束':c.violations.join('；'))}">×${f} ${!c?'—':c.accepted?'✓':'×'}</span>`;}).join('')}</div>${p.worst?`<p>最差全网耗时 ${p.worst[0]>0?'+':''}${number(p.worst[0])}% · 最差邻区 ${p.worst[1]>0?'+':''}${number(p.worst[1])}%</p><p>最差邻区新增过饱和 ${number(p.worst[2])} km</p>`:''}${p.reasons.length?`<p>${escapeHTML(p.reasons.join('；'))}</p>`:''}${p.missing.length?`<p>缺少需求配对：${p.missing.join(' / ')}</p>`:''}${p.dominated_by.length?`<p>${escapeHTML(p.dominated_by.join('、'))} 在三项最差代价上均不高，且至少一项更低。</p>`:''}<button class="quiet" data-policy="${escapeHTML(p.name)}">在地图查看 ↗</button></div>`).join('')+
    `<details><summary>筛选原理与证据边界</summary><p class="hint">${escapeHTML(a.rule)} 权衡前沿指没有另一合格措施在三项代价上同时不差且至少一项更好。缺测不插值，不用平均收益抵消约束失败。</p></details>`;
}
function drawAnalysis(){
  if(current.state.task.scenario==='diagnosis'){
    const observations=current.state.diagnostic_observations||[];
    $('robust-analysis').innerHTML='<p class="hint">仅诊断本次仿真现状；没有执行治理，不计算治理收益。</p>'+observations.map(o=>`<div class="analysis-winner"><small>小汽车需求 × ${o.car_demand_factor}</small><strong>${o.inspected_links} 条路段已读取</strong><p>${o.overloaded_links} 条有向路段过饱和 · ${o.roads_count} 个道路名称</p></div><div class="table-scroll"><table><thead><tr><th>道路</th><th>耗时 pcu·h</th><th>过饱和 km</th></tr></thead><tbody>${o.top_roads.map(r=>`<tr><td>${escapeHTML(r.name)}</td><td>${number(r.time_pcu_h)}</td><td>${number(r.overloaded_km)}</td></tr>`).join('')}</tbody></table></div>`).join('');
    $('recommendation').textContent='只读诊断已保存；路段耗时排序不等同于拥堵原因。';
    $('decisions').innerHTML='';return;
  }
  drawOriginalAnalysis();
  const state=current.state,v=current.decision_review;
  if(!v||state.status!=='completed')return;
  const selection=state.task.demand_factors.length===1?state.selection_analysis:v.selection;
  if(!selection)return;
  const chosen=state.task.demand_factors.length===1?selection.recommendation:v.recommendation;
  const original=$('robust-analysis').innerHTML;
  $('robust-analysis').innerHTML=`<div class="analysis-winner"><small>容差规则 v2 · ${state.task.recommendation_version==='tolerance-v2'?'本任务采用':'历史结果只读复评'}</small><strong>${escapeHTML(chosen||'无完整合格措施')}</strong><p>原任务冻结建议：${escapeHTML(state.recommendation)}。${state.task.demand_factors.length===1?'仅基准需求，不能据此宣称跨需求稳健。':'此处比较全部设定需求下的同一措施。'}</p><p>${escapeHTML(selection.rule)}</p><p>近似相同候选：${escapeHTML(selection.equivalent_candidates.join('、')||'无')}。</p>${chosen?`<button class="quiet" data-policy="${escapeHTML(chosen)}">查看此项的实测地图 ↗</button>`:''}<details><summary>复杂度和差异明细</summary>${selection.candidates.map(c=>`<p>${escapeHTML(c.name)}：${c.complexity[0]} 项措施，其中 ${c.complexity[1]} 项停车1级；相对选中项差异 [${c.difference_from_selected.map(x=>Number(x).toPrecision(6)).join(' / ')}]（全网个百分点 / 邻区个百分点 / 新增km）。</p>`).join('')}<p>措施数是排序假设，不是工程费用。历史任务的冻结结果与原始数据均未修改。</p></details></div>`+original;
}
async function poll(){
  if(!selected)return;const id=selected,result=await api(`/api/jobs/${id}`);if(id!==selected)return;
  const changed=current?.state.id!==id;current=result;showDataset(result.dataset);if(networkError){if($('error').textContent===networkError)error(current.state.error||'');networkError='';}
  if(changed){error();window.dispatchEvent(new Event('traffic-job-selected'));}const version=JSON.stringify(result);if(version!==lastRender){draw();drawConditional();drawRecordedTask();drawTurnResponses();drawAnalysis();lastRender=version;}window.trafficMap.update(current.state);
  const s=current.state,done=s.runs.filter(r=>r.metrics&&r.execution?.status==='completed').length,budget=s.task?.max_runs||0;
  $('job-progress').textContent=`已完成 ${done} / 预算 ${budget} 场景${s.active_scenario&&s.status==='running'?' · '+s.active_scenario:''}`;
  $('progress-bar').style.width=`${budget?Math.min(100,done/budget*100):0}%`;
  $('event-count').textContent=current.events.length;$('connection').textContent='本地服务已连接';
  $('run').disabled=launching||window.dispatchRunning||s.status==='running';$('run').textContent=s.status==='running'||window.dispatchRunning?'任务正在计算…':$('matcher').value==='model'?'自动编排并运行':'匹配并开始 ↗';
  document.querySelectorAll('[data-job]').forEach(b=>b.setAttribute('aria-current',String(b.dataset.job===selected)));
}
$('oneway-template').addEventListener('click',()=>{$('request').value='分析单向组织与停车组合；路段：长江村路；方向：3237→3236；预算：13';$('road').value='长江村路';$('links').value=2;$('budget').value=13;$('sensitivity').checked=true;$('repeat').checked=true;error();$('plan-panel').innerHTML='<p>已载入单向组合模板。请生成任务卡，核对关闭方向、双向停车对照及 13 次仿真预算。</p>';});
$('turn-template').addEventListener('click',()=>{$('request').value='分析禁转与停车组合；路段：红景路；转向：4797→5990→5991；预算：13';$('road').value='红景路';$('links').value=1;$('budget').value=13;$('sensitivity').checked=true;$('repeat').checked=true;error();$('plan-panel').innerHTML='<p>已载入禁转组合模板。请生成任务卡，核对转向、出口停车核心及 13 次仿真预算。</p>';});
$('history').addEventListener('click',async e=>{const item=e.target.closest('[data-job]');if(item&&!item.disabled){selected=item.dataset.job;error();try{await poll();}catch(e){error(e.message)}}});$('refresh').onclick=()=>refresh().then(poll).catch(e=>error(e.message));$('scope').onchange=draw;


$('parking-template').onclick=()=>{$('request').value='分析停车干扰拥堵，比较治理强度并检查周边影响';$('road').value='';$('links').value=3;$('budget').value=11;$('robust-search').checked=true;$('sensitivity').checked=true;$('repeat').checked=true;error();$('plan-panel').innerHTML='<p>已载入主动稳健停车治理配置，请预览任务卡。</p>';};
['oneway-template','turn-template'].forEach(id=>$(id).addEventListener('click',()=>{$('robust-search').checked=false;}));
$('robust-search').onchange=()=>{if($('robust-search').checked){$('sensitivity').checked=true;$('budget').value=Math.max(11,Number($('budget').value));}};
$('sensitivity').addEventListener('change',()=>{if(!$('sensitivity').checked)$('robust-search').checked=false;});
$('scope').onchange=()=>{draw();drawAnalysis();};
Object.assign(events,{search_action:'安排下一次实验',search_assessment:'实测约束评估',search_stop:'搜索结束',diagnostic_complete:'现状诊断完成'});
document.querySelectorAll('.template').forEach(button=>button.addEventListener('click',()=>document.querySelectorAll('.template').forEach(b=>b.classList.toggle('active',b===button))));
document.querySelectorAll('[data-pane]').forEach(button=>{
  button.onclick=()=>showPane(button.dataset.pane);
  button.onkeydown=e=>{const tabs=[...document.querySelectorAll('[data-pane]')],i=tabs.indexOf(button);let next;if(e.key==='ArrowRight')next=tabs[(i+1)%tabs.length];else if(e.key==='ArrowLeft')next=tabs[(i+tabs.length-1)%tabs.length];else if(e.key==='Home')next=tabs[0];else if(e.key==='End')next=tabs.at(-1);if(next){e.preventDefault();showPane(next.dataset.pane);next.focus();}};
});
$('robust-analysis').onclick=e=>{const b=e.target.closest('[data-policy]');if(!b)return;const select=$('map-run');if([...select.options].some(o=>o.value===b.dataset.policy)){select.value=b.dataset.policy;select.dispatchEvent(new Event('change'));showPane('situation');}};
$('archive-open').onclick=()=>$('detail-dialog').showModal();$('archive-close').onclick=()=>$('detail-dialog').close();
$('presentation').onclick=()=>{const active=document.body.classList.toggle('focus-map');$('presentation').setAttribute('aria-pressed',String(active));$('presentation').textContent=active?'返回控制台':'专注地图';window.trafficMap.redraw();};
function clockTick(){$('clock').textContent=new Date().toLocaleTimeString('zh-CN',{hour12:false});}clockTick();setInterval(clockTick,1000);
async function tick(){try{if(!lastHistory||Date.now()-lastHistory>15000)await refresh();if(!selected)$('connection').textContent='本地服务已连接 · 尚无实验';await poll();}catch(e){$('connection').textContent='服务连接中断 · 显示最后记录';networkError=e.message;error(networkError);}finally{setTimeout(tick,3000);}}
tick();
