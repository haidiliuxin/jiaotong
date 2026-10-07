/* Render structured planning and recorded evidence; never calls a model. */
(() => {
  const choiceView=typeof module!=='undefined'&&module.exports?require('./choice_view.js'):window.trafficChoiceView;
  const esc = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names = {queued:'待执行',starting:'准备中',running:'正在仿真',completed:'研究已完成',incomplete:'研究未完成',no_candidate:'无候选',failed:'执行失败',skipped:'未执行',interrupted:'已中断'};
  function hypothesisWarnings(result,id,job){
    const reviews=[result.public_claim_review,result.conclusion_review];
    const messages=reviews.flatMap(r=>(r?.issues||[]).filter(i=>
      ['hypothesis_updates:'+id,'programme_updates:'+id].includes(i.field)&&
      (!job||!(i.job_id||r.job_id)||(i.job_id||r.job_id)===job)).map(i=>i.message));
    return [...new Set(messages)];
  }
  function modelExplanation(result,update,label,job){
    const warnings=hypothesisWarnings(result,update.hypothesis_id,job);
    return `<small>${warnings.length?'原模型判断 · 解释待核对：':''}${esc(label)}</small><p>${esc(update.explanation)}</p>${warnings.map(m=>`<p class="evidence-gap"><b>这段解释待核对</b> ${esc(m)}</p>`).join('')}`;
  }
  const turnNeedsReview=t=>t.conclusion_review?.status==='needs_review'||t.public_claim_review?.status==='needs_review';
  function criterionScope(h){
    const e=h?.evaluation;if(!e)return '';
    const policies={all:'全部治理类型',parking:'停车措施',traffic_control:'交通组织单项',combined:'组合措施',control_and_combined:'交通组织及组合（不含仅停车）'};
    const w=e.interpretation;
    const wording=w?`${h.model_question?`<p><b>${w.origin==='model_structured_definition'?'模型定义的问题':'模型原提问'}</b> ${esc(h.model_question)}</p>`:''}<p><b>本次实际检验</b> ${esc(w.question)}</p><details><summary>口径与适用边界</summary><p>${esc(w.boundary)}</p>${(w.wording_notes||[]).map(n=>`<p><b>独立判定说明</b> ${esc(n.text)}<br><small>这句说明将判定分开，不表示另一项已经通过；实际结果分别检验。</small></p>`).join('')}${w.limits.map(x=>`<p>${esc(x)}</p>`).join('')}</details>`:'';
    return `<div class="criterion-scope">${wording}<p><b>事前判据</b> ${esc(e.rule)}</p><p>范围：${e.scope.map(s=>`${esc(s.road||'全网候选范围')} · 第${esc(s.study_id.split('_').at(-1))}组 · 需求 × ${esc(s.demand_factors.join(' / '))}`).join('；')}</p><p>${esc(policies[e.policies])} · 每组${e.quantifier==='any'?'至少一个':'所有'}已试措施满足，同一措施须覆盖全部约定需求。仅适用于本次试验集合。</p></div>`;
  }
  function hypothesisChecks(result){
    const saved=result.programme_review;
    const local=(result.progress||[]).map(p=>p.review?.research_review).filter(Boolean);
    const latest=[...(result.planning_trace||[])].reverse().find(t=>t.input?.context?.programme_evidence?.findings?.some(f=>f.evaluation));
    const sources=saved?[saved]:latest?[{evidence:latest.input.context.programme_evidence,updates:latest.programme_updates||latest.hypothesis_updates||[]}]:local;
    const labels={supported:'满足事前判据',contradicted:'不满足事前判据',unresolved:'证据仍不足'};
    const number=(v,unit='')=>Number.isFinite(v)?`${v!==0&&Math.abs(v)<.0005?v.toPrecision(3):v.toFixed(3)}${unit}`:'未测';
    const passed=v=>v===true?'满足':v===false?'不满足':'未知';
    const values=v=>{
      if('core_time_change_pct' in v)return `核心耗时 ${number(v.baseline_pcu_h)} → ${number(v.candidate_pcu_h)} pcu·h，变化 ${number(v.core_time_change_pct,'%')}`;
      if('constraint_passed' in v)return `推荐约束：${passed(v.constraint_passed)}；控制响应：${passed(v.control_response)}`;
      if('control_response' in v)return `原生控制响应：${passed(v.control_response)}；此项不判断核心收益或推荐资格`;
      if('neighbor_time_change_pct' in v)return `邻区耗时 ${number(v.neighbor_time_change_pct,'%')}（上限 ${number(v.time_limit_pct,'%')}）；毛新增过饱和 ${number(v.neighbor_new_oversaturated_km,' km')}（上限 ${number(v.new_oversaturated_limit_km,' km')}）`;
      if(v.combination_minus_singles_pcu_h)return `组合减去停车 / 交通组织单项：${v.combination_minus_singles_pcu_h.map(x=>number(x)).join(' / ')} pcu·h`;
      if('candidate_minus_parking_pcu_h' in v)return `本措施减去仅停车的核心耗时：${number(v.candidate_minus_parking_pcu_h)} pcu·h`;
      return '尚无可判定的配对值';
    };
    const cards=sources.flatMap(s=>(s.evidence?.findings||[]).filter(f=>f.evaluation).map(f=>{
      const update=(s.updates||[]).find(u=>u.hypothesis_id===f.hypothesis_id);
      return `<article class="hypothesis-check"><header><b>${esc(f.question)}</b><span class="criterion-verdict ${esc(f.expected_judgment)}">配对检验：${esc(labels[f.expected_judgment])}</span></header>${criterionScope(f)}<p>${esc(f.answer)}</p><details data-plan-detail="criterion-${esc(f.id)}"><summary>查看逐组、逐措施的判定与地图</summary>${(f.measurements||[]).map(g=>`<section><b>${esc(g.road||'全网候选范围')} · ${passed(g.passed)}</b>${g.gap?`<p>${esc(g.gap)}</p>`:''}${g.policies.map(p=>`<div class="criterion-policy"><small>${p.runs.length?p.runs.map(esc).join(' / '):'该措施尚未完成'} · ${passed(p.passed)}</small>${p.cases.map(c=>`<p>需求 × ${esc(c.demand_factor)} · ${passed(c.passed)}<br>${esc(values(c.values))}${c.gap?`<br>${esc(c.gap)}`:''}<span class="observation-refs">${c.evidence_refs.map(ref=>`<button data-observation-ref="${esc(ref)}">${esc(ref.split(':').at(-1))} ↗</button>`).join('')}</span></p>`).join('')}</div>`).join('')}</section>`).join('')||'<p>完整逐项数据在实测完成后显示。</p>'}</details><div class="criterion-model">${update?modelExplanation(result,update,'模型复核：'+labels[update.judgment]):'模型复核：尚未提交或未通过。'}</div></article>`;
    }));
    const grounded=sources.some(s=>s.evidence?.findings?.some(f=>f.evaluation?.interpretation));
    return cards.length?`<section class="hypothesis-checks"><h4>${grounded?'原问题与实际检验':'原问题是否得到回答'}</h4><p>开跑前固定指标与范围；配对数据检验判据，模型解释收益、代价和未解决的问题。</p>${!saved&&latest?'<p class="hint">当前为最近一次模型决策所读的观测；新结果进入下一轮后更新，尚未形成最终总复核。</p>':''}${cards.join('')}<p class="hint">判据由模型在初始方案中明确；此处不认证任意文字解释或现实因果。核心范围可能包含多个候选区，不能直接当作某一条道路的效果。</p></section>`:'';
  }
  function programmeStatus(result){
    const total=result.experiments?.length||0;
    if(!result.job_id||total<2)return '';
    const progress=result.progress||[];
    const complete=result.status==='completed'&&result.programme_review?.complete===true&&result.conclusion_review?.status!=='needs_review';
    const running=['starting','running','created','queued'].includes(result.status);
    const done=progress.filter(p=>p.status==='completed'&&p.review?.goal_review?.complete!==false&&p.review?.research_review?.reviewed!==false&&p.review?.conclusion_review?.status!=='needs_review').length;
    const counted=progress.filter(p=>Number.isInteger(p.completed_run_count)&&p.completed_run_count>=0);
    const runs=counted.reduce((sum,p)=>sum+p.completed_run_count,0),missing=Math.max(0,total-counted.length);
    const budget=result.allocated_runs??result.experiments.reduce((sum,e)=>sum+(e.runs||0),0);
    const countText=missing?`${counted.length?'已确认 '+runs:'已完成 —'} / ${esc(budget)} 场景 · ${missing} 组计数待读取`:`已完成 ${runs} / ${esc(budget)} 场景`;
    return `<div class="programme-headline" data-status="${complete?'completed':running?'running':'incomplete'}"><b>整个任务${complete?'已完成':running?'进行中':'未完成'}</b><small>${done} / ${total} 组完成 · ${countText}</small>${!complete&&!running?'<span>下方状态仅指地图当前查看的研究组。</span>':''}</div>`;
  }
  function currentPulse(result){
    const progress=result.progress||[];
    if(!result.job_id||!progress.length)return '';
    const current=progress.find(p=>p.job_id===result.active_job)||[...progress].reverse().find(p=>!['queued','skipped'].includes(p.status));
    const turn=[...(result.planning_trace||[])].reverse().find(t=>t.job_id===current?.job_id);
    const rejectedAction=turn?.proposal_excerpt?.action||turn?.proposed_decision?.action;
    const proposed=turn?.status==='rejected'?rejectedAction:turn?.action;
    const title=turn?.selected?.title||turn?.input?.allowed_actions?.[proposed]?.title||proposed;
    const failed=['incomplete','failed','stopped','interrupted'].includes(result.status);
    const ended=failed||['completed','no_candidate'].includes(result.status);
    const action=turn?.status==='rejected'?(rejectedAction?`提议 ${title||'下一动作'}，校验未通过，未执行`:'模型回复未形成可执行动作，未执行治理'):
      turn?.status==='awaiting_model'?'模型正在读取本组观测':turn?.action==='stop'?'停止追加试验':
      turn?.status==='accepted'&&title?`已选定 ${title}`:current?.status==='running'?`正在计算 ${current.title||'本组新基准'}`:'等待下一项实测动作';
    const counted=progress.filter(p=>Number.isInteger(p.completed_run_count)&&p.completed_run_count>=0);
    const count=counted.length===progress.length?`${counted.reduce((n,p)=>n+p.completed_run_count,0)} 个场景完成`:'完成场景数待读取';
    const policies=progress.reduce((n,p)=>n+(p.study_comparison?.factors||[]).reduce((m,f)=>
      m+(f.policies||[]).filter(policy=>['admissible','rejected'].includes(policy.status)).length,0),0);
    const observed=`${count}；${policies?`${policies} 个治理场景已有配对`:'尚无治理配对'}`;
    const conflict=result.public_claim_review?.issues?.[0]?.message||result.conclusion_review?.issues?.[0]?.message;
    const readOnlyConflict=conflict&&(result.public_claim_review?.read_only||result.recorded_status==='completed');
    const why=readOnlyConflict?(turn?.stop_basis||turn?.reason||result.programme_review?.recorded_summary||'原记录未提供独立的停止理由'):
      conflict||(failed?(turn?.validation?.message||current?.reason||result.programme_review?.unanswered?.[0]||'必需对照或复核尚未完成'):
      ended?(turn?.stop_basis||current?.review?.outcome||result.programme_review?.summary||'按本次完成的研究条件结束'):
      turn?.selection_reason||(turn?.next_check_origin?turn?.reason:turn?.next_check)||'等待本次新结果，再由模型决定下一步');
    return `<section class="current-pulse" data-status="${esc(result.status)}"><h4>当前动作与实际进度</h4><div><p><b>当前动作</b>${esc(action)}</p><p><b>实际结果</b>${esc(observed)}</p><p><b>${readOnlyConflict?'原结束依据':failed?'为什么停止':ended?'结束依据':'下一步理由'}</b>${esc(why)}</p>${readOnlyConflict?`<p><b>当前核验问题</b>${esc(conflict)}</p>`:''}</div><small>仅按本次调度记录和已完成场景显示；未执行的提议没有治理效果。</small></section>`;
  }
  function completion(result){
    const reviews=(result.progress||[]).filter(p=>p.review?.goal_review?.requirements?.length).map(p=>({title:p.title,job:p.job_id,...p.review.goal_review}));
    const latest=(result.planning_trace||[]).at(-1);
    if(latest?.input?.context?.goal_review?.requirements?.length&&!reviews.some(r=>r.job===latest.job_id))reviews.push({title:'当前研究',job:latest.job_id,...latest.input.context.goal_review});
    const number=(x,d=2)=>Number.isFinite(x)?Number(x).toFixed(d):'—';
    return reviews.map(r=>`<section class="completion-card"><div><b>${result.conclusion_review?.status==='needs_review'?'实测已记录 · 结论待核对':r.complete?'研究目标已完成':'研究完成进度'}</b><span>${r.requirements.filter(c=>c.fulfilled).length} / ${r.requirements.length}</span></div><small>${esc(r.title)}</small><ul>${r.requirements.map(c=>`<li class="${c.fulfilled?'fulfilled':'pending'}"><span>${c.fulfilled?'✓':'○'}</span>${esc(c.label)}</li>`).join('')}</ul>${(r.strength_matrix||[]).map(row=>`<div class="strength-result"><b>${row.region_title?esc(row.region_title)+' · ':'同方向对照 · '}需求 × ${esc(row.demand_factor)}</b><table><thead><tr><th>等级</th><th>${row.speed_scope==='treated'?'治理方向速度':'核心速度'}</th><th>邻区耗时变化</th></tr></thead><tbody>${row.levels.map(c=>`<tr><td>${c.run?`<button data-observation-ref="${esc(r.job+':'+c.run)}" title="查看本项实测地图">${c.level}级 ↗</button>`:`${c.level}级 · 待测`}</td><td>${number(c.speed_kmh)} <small>km/h</small></td><td>${number(c.comparison?.delta?.neighbor?.running_time_change_pct,3)}${c.comparison?'%':''}</td></tr>`).join('')}</tbody></table><small>1级措施强于2级；实际收益和邻区代价须分别比较。</small></div>`).join('')}</section>`).join('');
  }
  function executionDefinition(t){
    const spec=t.input?.context?.action_execution;
    if(!['action-execution-v1','action-execution-v2'].includes(spec?.version))return '';
    const model=(t.review_correction?.input||t.input).model_budget;
    const action=t.status==='rejected'?(t.proposal_excerpt?.action||t.proposed_decision?.action):t.action;
    const planned=spec.actions?.[action];
    const budgets=[['current_study','本组'],['programme_allocated','全任务已分配']].map(([key,label])=>{
      const b=spec.native_budget?.[key];
      return b?`<p>${label}原生场景：已用 ${esc(b.used)} / 上限 ${esc(b.limit)}，其中完成 ${esc(b.completed)}，剩余 ${esc(b.remaining)}</p>`:'';
    }).join('');
    const sequence=planned?.sequence||[];
    const direction=planned?.closed_direction?`<p>拟禁行 ${planned.closed_direction.map(esc).join('→')} · 保留 ${planned.retained_direction.map(esc).join('→')}</p>`:'';
    return `<section class="execution-definition"><b>实际执行定义 · ${t.status==='accepted'?'已选择，效果待实测':'尚未执行'}</b><p>该次决策输入：模型请求剩余 ${esc(model?.remaining??'—')} 次（含本次）；原生场景另计。</p>${budgets}${direction}${sequence.length?`<details><summary>查看安排顺序 · ${sequence.length} 个原生场景</summary><ol>${sequence.map(s=>`<li>需求 ×${esc(s.demand_factor)} · ${esc(s.policy)}${s.parking_level!=null?`（停车${esc(s.parking_level)}级）`:''}</li>`).join('')}</ol></details>`:''}<small>此处按冻结动作定义显示安排，不代表这些场景已经完成；实际完成情况见观测记录。</small></section>`;
  }
  function evidence(p){
    const r=p?.review;if(!r)return '';
    return `<div class="plan-evidence"><b>本组实测反馈</b><p>${esc(r.outcome)}</p><small>完成 ${esc(r.completed_runs)} 次 · ${esc(r.rejected_count)} 项比较未通过约束</small>${r.rejection_reasons?.length?`<ul>${r.rejection_reasons.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}${(r.observations||[]).map(o=>`<p>需求 × ${esc(o.car_demand_factor)}：读取 ${esc(o.inspected_links)} 条有向路段，${esc(o.overloaded_links)} 条过饱和。</p>`).join('')}</div>`;
  }
  function factorialTable(finding,job){
    if(!finding.id.endsWith('factorial'))return '';
    const pct=x=>Number.isFinite(x)?`${x>0?'+':''}${x.toFixed(3)}%`:'—';
    return (finding.measurements||[]).map(row=>`<div class="factorial-result"><b>需求 × ${esc(row.factor)}</b>${row.baseline?`<button data-observation-ref="${esc(job+':'+row.baseline)}">同需求基准 ↗</button>`:''}<div class="table-scroll"><table><thead><tr><th>措施</th><th>全网耗时</th><th>邻区耗时</th><th>检查</th></tr></thead><tbody>${row.cells.map(c=>`<tr><td>${c.run?`<button data-observation-ref="${esc(job+':'+c.run)}">${esc(c.title)} ↗</button>`:esc(c.title)+' · 未测'}</td><td>${pct(c.delta?.network?.running_time_change_pct)}</td><td>${pct(c.delta?.neighbor?.running_time_change_pct)}</td><td>${!c.run?'待测':c.eligible?'通过':c.control_response===false?'控制未生效':'未通过'}</td></tr>`).join('')}</tbody></table></div>${Number.isFinite(row.interaction_pcu_h)?`<p>组合额外项 ${row.interaction_pcu_h>0?'+':''}${row.interaction_pcu_h.toFixed(3)} pcu·h；以同需求基准计算，不能将单项收益直接相加。</p>`:''}</div>`).join('');
  }
  function measuredCoverage(g){
    if(g.version!=='study-comparison-v2')return '';
    const names={baseline:'新基准',baseline_repeat:'基准复跑',parking:'仅停车',traffic_control:'仅交通组织',combined:'停车＋交通组织'};
    return `<div class="measured-coverage"><b>实际完成 · 来自场景记录</b><p>${esc(g.coverage_text)}</p>${g.completed_scenes?.length?`<details><summary>定位这些实际场景</summary>${g.completed_scenes.map(s=>`<button data-observation-ref="${esc(s.ref)}">${esc(names[s.kind]||s.kind)} · ${esc(s.ref.split(':').at(-1))} · 需求 ×${esc(s.demand_factor)} ↗</button>`).join('')}</details>`:''}</div>`;
  }
  function separateChecks(p,n){
    const c=p.checks;if(!c)return '';
    const state=(v,yes,no)=>v===true?yes:v===false?no:'未证实';
    const limits=p.neighbor_limits||{},limit=(v,u)=>Number.isFinite(v)?esc(v)+u:'未提供';
    const directions=(p.control_directions||[]).map(x=>Array.isArray(x.closed)&&Array.isArray(x.retained)?`禁行 ${x.closed.map(esc).join('→')}；保留 ${x.retained.map(esc).join('→')}`:Array.isArray(x.turn)?`禁转 ${x.turn.map(esc).join('→')}`:'').filter(Boolean);
    return `${directions.length?`<p class="control-directions">本场执行参数：${directions.join('；')}</p>`:''}<dl class="separate-checks"><div><dt>原生控制响应</dt><dd>${c.control_applicable===false?'不涉及禁行／禁转':state(c.control_passed,'通过','未通过')}</dd></div><div><dt>配对核心耗时</dt><dd>${state(c.core_time_decreased,'下降','未下降')}</dd></div><div><dt>邻区两项门槛</dt><dd>${state(c.neighbor_limits_passed,'门槛内','超门槛')}</dd></div><div><dt>推荐资格</dt><dd>${state(c.recommendation_admissible,'通过','不具备')}</dd></div></dl><p class="hint">邻区毛新增过饱和：${n(limits.neighbor_new_oversaturated_km,' km')}；冻结上限：耗时增幅 ≤ ${limit(limits.time_limit_pct,'%')}，毛新增 ≤ ${limit(limits.new_oversaturated_limit_km,' km')}。</p>`;
  }
  function measuredComparison(result){
    const groups=(result.progress||[]).filter(p=>p.study_comparison).map(p=>({...p.study_comparison,title:p.title}));
    if(!groups.length)return '';
    const status={admissible:'通过约束',rejected:'未通过',unmeasured:'证据不足'};
    const n=(v,unit)=>Number.isFinite(v)?`${v>0?'+':''}${v!==0&&Math.abs(v)<.0005?v.toPrecision(3):v.toFixed(3)}${unit}`:'未提供';
    const link=(ref,label)=>`<button data-observation-ref="${esc(ref)}">${esc(label)} ↗</button>`;
    return `<section class="measured-comparison"><header><h4>实测方案怎么选</h4><span>按本组新基准逐项比较</span></header><p>先看是否通过全部约束，再看收益与代价。点击措施可在地图上核对对应路段。</p>${groups.map(g=>`<article><h5>${esc(g.title||g.road||'当前研究')}</h5>${measuredCoverage(g)}${g.factors.map(f=>`<div class="comparison-factor"><b>需求 × ${esc(f.demand_factor)}</b> ${f.baseline_ref?link(f.baseline_ref,'本组基准'):'基准待测'}${f.policies.length?`<div class="comparison-policies">${f.policies.map(p=>`<div class="comparison-policy"><header>${link(p.ref,p.label)}<strong class="comparison-status ${esc(p.status)}">${esc(status[p.status]||'证据不足')}</strong></header><small>${esc(p.run)}</small><dl><div><dt>核心耗时变化</dt><dd>${n(p.core_time_change_pct,'%')}</dd></div><div><dt>邻区耗时变化</dt><dd>${n(p.neighbor_time_change_pct,'%')}</dd></div></dl>${separateChecks(p,n)}</div>`).join('')}</div>`:'<p class="hint">尚无已完成的治理对照。</p>'}${f.combination_contrasts.map(c=>`<div class="combination-contrast"><b>组合相对${esc(c.single_label)}</b><p>单项：${esc(status[c.single_status])} · 组合：${esc(status[c.combined_status])}</p><dl><div><dt>核心耗时差</dt><dd>${n(c.core_time_difference_pcu_h,'')}</dd></div><div><dt>邻区耗时差</dt><dd>${n(c.neighbor_time_difference_pcu_h,'')}</dd></div><div><dt>全网耗时差</dt><dd>${n(c.network_time_difference_pcu_h,'')}</dd></div></dl><small>组合 − 单项，单位 pcu·h；负值表示组合更低。</small></div>`).join('')}</div>`).join('')}<small class="hint">${esc(g.scope)}</small></article>`).join('')}</section>`;
  }
  function programme(result){
    const saved=result.programme_review;
    const latest=[...(result.planning_trace||[])].reverse().find(t=>t.input?.context?.programme_evidence);
    const evidence=saved?.evidence||latest?.input.context.programme_evidence;if(!evidence)return '';
    const judgments={supported:'模型判断：支持',contradicted:'模型判断：不支持',unresolved:'仍待验证'};
    return `<section class="programme-review"><header><h4>整个研究任务</h4><span>${(result.conclusion_review?.status==='needs_review'||result.public_claim_review?.status==='needs_review')?'模型结论待核对':saved?.complete?'各组实测与复核齐全':saved?.reviewed?'已复核 · 对照仍有缺口':'等待整体复核'}</span></header>${saved?.comparisons?.some(g=>g.version==='study-comparison-v2')?`<details class="decision-source"><summary>查看模型整体解释（原文）</summary><p>${esc(saved.summary)}</p></details>`:`<p>${esc(saved?.summary||'当前各组证据持续汇入；尚未形成整个任务的最终结论。')}</p>`}${evidence.groups.map(g=>`<article><div><b>${esc(g.title)}</b><span>${g.execution_complete?'约定实测齐全':'实测未齐'}</span></div><small>${esc(g.engine)}</small>${saved?.comparisons?.find(c=>c.study_id===g.id)?.coverage_text?`<p>实际完成：${esc(saved.comparisons.find(c=>c.study_id===g.id).coverage_text)}</p>`:''}${evidence.findings.filter(f=>g.finding_ids.includes(f.id)&&f.id.endsWith('.factorial')).map(f=>`<details data-plan-detail="programme-grid-${esc(g.id)}"><summary>单项与组合实测对照</summary>${factorialTable(f,g.job_id)||'<p>等待完整配对表；当前已完成数据可在逐轮记录中查看。</p>'}</details>`).join('')}</article>`).join('')}${(saved?.updates||[]).map(u=>`<div class="programme-verdict"><b>${esc(saved.hypotheses.find(h=>h.id===u.hypothesis_id)?.hypothesis||u.hypothesis_id)}</b>${modelExplanation(result,u,judgments[u.judgment])}<small>依据：${u.finding_ids.map(id=>esc(evidence.findings.find(f=>f.id===id)?.question||id)).join(' / ')||'没有足够检验'}</small></div>`).join('')}${saved?.unanswered?.length?`<ul>${saved.unanswered.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}<p class="hint">${esc(evidence.scope)}</p></section>`;
  }
  function conclusion(result){
    const r=result.conclusion_review;if(!r||(r.status==='not_reviewed'&&!r.costs?.length&&!r.unreviewed?.length))return '';
    const n=(v,d=3)=>Number.isFinite(v)?`${v>0?'+':''}${v!==0&&Math.abs(v)<.5*10**(-d)?v.toPrecision(3):v.toFixed(d)}`:'未提供';
    const policies=new Map((r.executed_policies||[]).map(p=>[p.ref,p.label]));
    const issues=[...new Set([...(r.issues||[]),...(result.public_claim_review?.issues||[])].map(i=>i.message))];
    return `<section class="conclusion-review ${issues.length?'needs-review':''}"><header><h4>收益之外，还发生了什么</h4><span>${issues.length?'结论待核对':r.unreviewed?.length?'模型复核未通过':'实测代价核对'}</span></header><p>通过约束表示代价在设定门槛内，仍需保留实际增加的指标。模型已保存的决策记录在下方列出。</p>${(r.unreviewed||[]).map(u=>`<p class="evidence-gap">${esc(u.message)}</p>`).join('')}${issues.length?`<p class="evidence-gap"><b>发现 ${issues.length} 类结论冲突，不能视为完整结论。</b></p>`:''}${(r.costs||[]).map(c=>`<article><header><button data-observation-ref="${esc(c.ref)}">${esc(c.roads?.join('、')||c.run)} · ${esc(c.run)} ↗</button><span>${c.constraint_passed?'约束通过':'约束未通过'}${c.has_measured_cost?' · 有实测代价':''}</span></header>${policies.has(c.ref)?`<p class="executed-policy">实际执行：${esc(policies.get(c.ref))}</p>`:''}<small>需求 × ${esc(c.demand_factor)} · 核心范围耗时 ${n(c.core_time_change_pct)}%</small><dl><div><dt>邻区耗时</dt><dd class="${c.neighbor_time_change_pct>0?'cost-increase':''}">${n(c.neighbor_time_change_pct)}%</dd></div><div><dt>邻区毛新增过饱和</dt><dd class="${c.neighbor_new_oversaturated_km>0?'cost-increase':''}">${n(c.neighbor_new_oversaturated_km)} km</dd></div><div><dt>邻区过饱和净变化</dt><dd>${n(c.neighbor_net_oversaturated_km)} km</dd></div><div><dt>全网过饱和净变化</dt><dd class="${c.network_net_oversaturated_km>0?'cost-increase':''}">${n(c.network_net_oversaturated_km)} km</dd></div></dl>${(c.neighbor_new_links||[]).map(l=>`<button class="adverse-road" data-region-job="${esc(c.ref.split(':')[0])}" data-region-run="${esc(c.run)}" data-region-indices="${esc(l.index)}">定位新增过饱和：${esc(l.name)} ${esc(l.from_node)}→${esc(l.to_node)} · V/C ${Number(l.vc).toFixed(3)} ↗</button>`).join('')}${c.neighbor_new_link_count>10?`<small>列出10 / ${esc(c.neighbor_new_link_count)}条，完整清单保存在原始比较记录。</small>`:''}</article>`).join('')}<p class="hint">毛新增只计新过饱和路段；净变化还包含其他路段改善，两者不能互相替代。核心范围为该组冻结范围，不一定只是被治理道路。</p>${issues.length?`<details class="evidence-caveat"><summary>查看模型解释与证据冲突</summary>${issues.map(i=>`<p>${esc(i)}</p>`).join('')}<small>历史执行状态与封存报告保持原样；此处为读取原件后的独立核对。</small></details>`:''}${(r.hypothesis_gaps||[]).map(g=>`<p class="evidence-gap">${esc(g.hypothesis_id)} · ${esc(g.missing)}</p>`).join('')}<details><summary>这项核对的范围</summary><p>${esc(r.scope)}</p></details></section>`;
  }
  function research(result){
    const reviews=(result.progress||[]).filter(p=>p.review?.research_review).map(p=>({job:p.job_id,...p.review.research_review}));
    const t=[...(result.planning_trace||[])].reverse().find(t=>t.input?.context?.research_evidence);
    if(t&&!reviews.some(r=>r.job===t.job_id))reviews.push({job:t.job_id,evidence:t.input.context.research_evidence,hypotheses:t.input.context.initial_hypotheses||[],updates:t.hypothesis_updates||[],reviewed:t.status==='accepted'&&t.action==='stop'});
    const status={supported:'实测支持',not_supported:'未获支持',observed:'发现反例',not_observed:'暂未发现',measured:'已有配对',unmeasured:'证据不足'};
    const judgment={supported:'模型判断：支持',contradicted:'模型判断：不支持',unresolved:'模型判断：仍待验证'};
    const n=x=>Number.isFinite(x)?`${x>0?'+':''}${x.toFixed(3)}%`:'—';
    return reviews.map(r=>`<section class="research-review"><h4>假设如何经受实测检验</h4><p class="hint">检验结果由本组配对数据计算；模型解释另列，不把未测项当结论。</p>${r.evidence.findings.map(f=>`<details data-plan-detail="finding-${esc(r.job)}-${esc(f.id)}" ${['benefit','strength'].includes(f.id)?'open':''}><summary><b>${esc(f.question)}</b><span class="finding-state ${esc(f.status)}">${esc(status[f.status]||f.status)}</span></summary><p>${esc(f.answer)}</p>${factorialTable(f,r.job)}${f.id==='strength'?(f.measurements||[]).map(m=>`<p class="measured-choice">本组${m.preferred_level?`优先${m.preferred_level}级`:'未形成合格建议'}；${m.levels.map(l=>`${l.level}级${l.feasible?`：最差全网耗时 ${n(l.worst?.[0])}`:'：未通过约束'}`).join('，')}</p>`).join(''):''}<div class="observation-refs">${f.evidence_refs.map(ref=>`<button data-observation-ref="${esc(ref)}">${esc(ref.split(':').at(-1))} ↗</button>`).join('')}</div></details>`).join('')}${r.updates.length?`<div class="hypothesis-verdicts"><h5>模型对初始假设的本组复核</h5>${r.updates.map(u=>`<article><b>${esc(r.hypotheses.find(h=>h.id===u.hypothesis_id)?.hypothesis||u.hypothesis_id)}</b>${modelExplanation(result,u,judgment[u.judgment],r.job)}<small>依据：${u.finding_ids.map(id=>esc(r.evidence.findings.find(f=>f.id===id)?.question||id)).join(' / ')||'没有足够的可计算检验'}</small></article>`).join('')}</div>`:''}<p class="hint">${r.review_scope?.mode==='programme_final_only'?(r.reviewed?'本组实测与代价已复核；初始假设在整个任务结束后统一判断。':'本组实测与代价待复核；初始假设保留至总任务最终复核。'):(r.reviewed?'已逐项复核原假设；仍未验证的内容保留。':'等待模型完成逐项复核。')} 结论仅适用于本组实测条件。</p></section>`).join('');
  }
  function spatial(result){
    const trace=[...(result.planning_trace||[])].reverse().find(t=>t.input?.observations?.some(o=>o.spatial));
    const o=trace?.input.observations.filter(o=>o.spatial).at(-1);if(!o)return '';
    const policies=trace.input.observations.filter(r=>r.spatial&&r.changes?.length),pct=x=>Number.isFinite(x)?`${x>0?'+':''}${x.toFixed(3)}%`:'—';
    const choice=[...(result.planning_trace||[])].reverse().find(t=>t.job_id===trace.job_id&&t.kind==='parking'&&t.status==='accepted'&&t.selected?.changes?.length);
    const samePolicy=p=>p.changes.length===choice.selected.changes.length&&choice.selected.changes.every(c=>p.changes.some(r=>r.from_node===c.from_node&&r.to_node===c.to_node&&r.parking===c.parking));
    const pending=choice&&!policies.some(samePolicy),groupStatus=result.progress?.find(p=>p.job_id===trace.job_id)?.status||result.status;
    const halted=['failed','incomplete','interrupted','stopped','skipped','no_candidate'].includes(groupStatus);
    const selectedAreas=pending?o.spatial.regions.filter(a=>a.roads.some(r=>choice.selected.changes.some(c=>c.from_node===r.from_node&&c.to_node===r.to_node))):[];
    const selectedIndices=selectedAreas.flatMap(a=>a.roads.filter(r=>choice.selected.changes.some(c=>c.from_node===r.from_node&&c.to_node===r.to_node)).map(r=>r.index)).filter(Number.isInteger);
    const pendingCard=pending?`<article class="area-trial"><header><b>${esc(choice.selected.title)}</b><span>${halted?'已选定 · 研究中断':'已选定 · 等待治理结果'}</span></header><p>动作已通过参数校验，尚无对应的完整实测结果；此时不能判断治理收益。</p>${selectedIndices.length?`<button data-region-job="${esc(trace.job_id)}" data-region-run="${esc(o.run)}" data-region-indices="${esc(selectedIndices.join(','))}">定位待验证动作 · 查看已有观测 ↗</button>`:''}</article>`:'';
    const trials=policies.map(p=>{const treated=p.spatial.regions.filter(a=>a.roads.some(r=>p.changes.some(c=>c.from_node===r.from_node&&c.to_node===r.to_node)));const worst=[...(p.regional_checks||[])].sort((a,b)=>b.time_change_pct-a.time_change_pct)[0];return `<article class="area-trial ${p.eligible===false?'area-worse':''}"><header><button data-region-job="${esc(trace.job_id)}" data-region-run="${esc(p.run)}" data-region-indices="${esc(treated.flatMap(r=>r.indices).join(','))}">${esc(treated.map(a=>a.title).join(' / '))} ↗</button><span>${p.eligible?'约束通过':'未通过'}</span></header><small>停车${esc(p.changes[0].parking)}级 · ${p.changes.length}个方向 · 需求 × ${esc(p.demand_factor)}</small><p>候选区并集耗时 ${pct(p.delta?.core?.running_time_change_pct)} · 邻区 ${pct(p.delta?.neighbor?.running_time_change_pct)}</p><p>区域最大耗时增幅 ${pct(worst?.time_change_pct)}${worst?`（${esc(p.spatial.regions.find(a=>a.id===worst.region)?.title||worst.region)}）`:''}</p>${p.violations?.length?`<small>${p.violations.map(esc).join('；')}</small>`:''}</article>`;}).join('');
    return `<section class="spatial-review"><h4>区域选择与实测结果</h4>${pendingCard}${trials||(!pending?`<p>${!halted&&trace.status==='awaiting_model'?'新基准已建立，模型正在比较候选区域。':'本组尚无已完成的区域治理实测。'}</p>`:'')}<details open><summary>各候选区域的本轮观测</summary><p>读取 ${esc(o.spatial.inspected_links)} 条路段 · ${esc(o.spatial.overloaded_links)} 条过饱和 · 需求 × ${esc(o.demand_factor)}</p>${o.spatial.regions.map(r=>{const check=(o.regional_checks||[]).find(c=>c.region===r.id);return `<div class="observed-area ${check?.passed===false?'area-worse':''}"><b>${esc(r.title)}</b><span>${Number(r.weighted_speed_kmh).toFixed(2)} km/h</span><small>${r.roads.length} 个方向${check?` · 耗时 ${pct(check.time_change_pct)} · ${check.passed?'区域约束通过':'区域代价超限'}`:' · 新基准，尚无治理效果'}</small><button data-region-job="${esc(trace.job_id)}" data-region-run="${esc(o.run)}" data-region-indices="${esc(r.indices.join(','))}">在地图查看该区 ↗</button></div>`;}).join('')}</details><details><summary>候选区之外的过饱和热点</summary>${o.spatial.outside_hotspots.map(r=>`<p>${esc(r.name)} ${esc(r.from_node)}→${esc(r.to_node)} · ${Number(r.speed_kmh).toFixed(1)} km/h · V/C ${Number(r.vc).toFixed(2)}</p>`).join('')||'<p>本轮没有目录外过饱和路段。</p>'}<p>这是本次批次输出的全网扫描；当前合法动作只覆盖冻结候选区，不代表已穷举所有治理位置。</p></details></section>`;
  }
  function onewayTargets(result){
    return (result.planning_trace||[]).filter(t=>t.kind==='oneway_target').map(t=>{
      const candidates=Object.entries(t.input?.allowed_actions||{}).filter(([,v])=>v.closed_direction&&v.roads).map(([id,c])=>{
        const [from,to]=c.closed_direction;
        const roads=[c.roads.find(r=>r.from_node===from&&r.to_node===to),c.roads.find(r=>r.from_node===to&&r.to_node===from)].filter(Boolean);
        return [id,{...c,roads}];
      });
      const n=(x,d=1)=>Number.isFinite(x)?x.toFixed(d):'—';
      return `<section class="oneway-targets"><h4>单向目标如何选定</h4><p>模型比较本次新基准的合法方向，选择试验位置。图检查通过只表示可以试验，收益要由后续单项与组合对照验证。</p>${candidates.map(([id,c])=>{
        const chosen=t.status==='accepted'&&t.action===id,detours=Object.values(c.topology?.modal_detours||{});
        return `<article class="${chosen?'target-selected':''}"><header><b>${esc(c.title)}</b><span>${chosen?'模型选定':t.status==='accepted'?'未试验位置':'待选择'}</span></header><div class="target-roads">${c.roads.map((r,i)=>`<div><small>${i===0?'拟关闭方向':'保留通行方向'} ${esc(r.from_node)}→${esc(r.to_node)}</small><b>${n(r.speed_kmh)} <small>km/h</small></b><span>V/C ${n(r.vc,2)} · 停车${esc(r.parking)}级</span></div>`).join('')}</div><p>拟关闭方向小汽车量 ${n(c.baseline_car_flow)} veh/h<br>各车型最大新增绕行 ${n(Math.max(0,...detours.map(d=>d.max_extra_km)),2)} km · 已检查 ${esc(c.topology?.bus_routes_checked??'—')} 条公交线路</p><button data-region-job="${esc(t.job_id)}" data-region-run="baseline" data-region-indices="${esc(c.roads.map(r=>r.index).join(','))}">在新基准地图查看 ↗</button></article>`;
      }).join('')||'<p>没有可执行选点菜单，研究保持未完成。</p>'}<p class="hint">未选位置没有治理实测，不能将其当成已淘汰的无效方案。</p></section>`;
    }).join('');
  }
  function routing(turn){
    const guard=turn.input?.context?.route_budget,choices=Object.entries(guard?.choices||{});
    if(!choices.length)return '';
    const repairing=turn.review_correction?.version==='study-review-correction-v1';
    const remaining=Math.max(0,guard.requests_after_this_decision-(repairing?1:0));
    const retained=turn.review_correction?.first_attempt?.proposal_excerpt?.action;
    const handoff=turn.input?.context?.study_handoff,transferred=turn.status==='accepted'&&handoff?.actions?.includes(turn.action);
    const conflict=turn.conclusion_review?.status==='needs_review',scoped=turn.input?.context?.review_scope?.mode==='programme_final_only';
    const title=transferred?(conflict?'已转入下一组 · 原复核待核对':'本组已复核 · 转入下一组'):'复核本组并安排下一组';
    const explanation=transferred&&conflict?'当时接受这次回复并转入后续研究；当前核对发现解释冲突，本组复核仍未完成。':transferred?(scoped?'这次模型回复确认了本组观测与实测代价；初始假设保留至总任务最终复核。':'这次模型回复同时确认了当前组的假设与实测代价，并选定后续研究。'):(scoped?'模型须先确认本组实测代价，再安排下一组；初始假设在全部研究结束后统一复核。':'模型须先确认当前组的假设与实测代价，才能选择下一组；也可在预算内继续当前组试验。');
    return `<section class="route-schedule">${handoff?`<div class="study-handoff-note"><b>${title}</b><p>${explanation}后续组使用自己的新基准，不能把分组收益相加。</p></div>`:''}<h5>下一组如何安排</h5><p>本轮决策后还可用 <b>${esc(remaining)}</b> 次模型请求${repairing?"（已计入本次修正）":""}。下方预算包含全部剩余研究及最终复核。</p><table><thead><tr><th>下一研究组</th><th>后续最低请求</th><th>本轮安排</th></tr></thead><tbody>${choices.map(([id,c])=>{const chosen=turn.status==='accepted'&&turn.action===id;return `<tr class="${chosen?'route-chosen':c.feasible?'':'route-deferred'}"><td>${esc(c.title)}</td><td>${esc(c.required_requests)} 次</td><td>${chosen?'模型选定':repairing?(id===retained?(turn.status==='rejected'?'修正失败 · 未转入':'保留原选组 · 等待复核'):'修正不更换组'):c.feasible?'可选':'暂缓顺序'}</td></tr>`;}).join('')}</tbody></table>${choices.some(([,c])=>!c.feasible)?'<p class="hint">暂缓的是执行顺序：先做该组会超出剩余额度。它仍在必做计划中，后续继续执行。</p>':''}</section>`;
  }
  function reviewCorrection(turn){
    const correction=turn.review_correction;if(!correction)return '';
    const handoff=correction.version==='study-review-correction-v1';
    const argumentsOnly=correction.version==='parking-arguments-correction-v1';
    const choice=correction.version==='candidate-choice-correction-v1';
    const title=argumentsOnly?'工具格式修正':choice?'选点引用修正':handoff?'本组交接复核修正':'最终复核修正';
    const scope=argumentsOnly?'首次工具参数无法解析，使用同一份观测和动作菜单重新提交完整决策。首次仅保存解析诊断。':choice?'选点观测引用缺漏，使用同一份观测补全引用并保留原选动作与备选。':handoff?'本组对照已齐，使用同一份观测修正解释并保留原选定的下一组。当时后续研究尚未执行，后续仍须使用自己的新基准。':'原生对照已齐，使用同一份观测重新复核。';
    const first=correction.first_attempt||{},usages=[first.usage?.tokens,turn.usage?.tokens];
    const known=usages.filter(Number.isFinite).reduce((sum,n)=>sum+n,0),unknown=usages.filter(n=>!Number.isFinite(n)).length;
    const outcome=turn.status==='accepted'?(argumentsOnly||choice?'修正后的决策已通过校验':'修正后的复核已通过校验'):turn.status==='rejected'?'修正仍未通过，研究未完成':argumentsOnly||choice?'模型正在依据反馈修正决策':'模型正在依据反馈修正复核';
    return `<section class="review-correction" aria-label="${title}"><h5>${title} · 1次上限</h5><p>${scope}两次请求均计入原总预算，此处没有新增仿真。${handoff?"交接与最终复核共享这一次修正机会。":""}</p><ol><li><b>${argumentsOnly||choice?'首次请求未通过':'首次复核未通过'}</b><p>${esc(first.validation?.message||first.reason||'原提议未通过校验')}</p>${(first.conclusion_check?.issues||[]).length?`<ul>${first.conclusion_check.issues.slice(0,4).map(issue=>`<li>${esc(issue.message)}</li>`).join('')}</ul>`:''}<small>${esc(first.validation?.code||'')} · ${Number.isFinite(usages[0])?esc(usages[0])+' tokens':'用量未知'}</small><details class="rejected-proposal"><summary>查看首次被拒绝的公开提议</summary><pre>${esc(JSON.stringify(first.proposal_excerpt||{},null,2))}</pre></details></li><li><b>${esc(outcome)}</b>${turn.status==='rejected'?`<p>${esc(turn.validation?.message||turn.reason||'')}</p>`:''}<small>${Number.isFinite(usages[1])?esc(usages[1])+' tokens':turn.status==='awaiting_model'?'回复与用量待返回':'用量未知'}</small></li></ol><p class="hint">2 次请求 · 已知 ${esc(known)} tokens${unknown?' · '+esc(unknown)+'次用量尚未知':''}。首次失败保留；通过校验不代表证明了治理收益。</p></section>`;
  }
  function progressLabel(progress,traces){
    if(progress.public_claim_review?.status==='needs_review')return '实测已记录 · 公开结论待核对';
    if(progress.review?.conclusion_review?.status==='needs_review')return '实测已记录 · 结论待核对';
    const latest=traces.slice().reverse().find(t=>t.job_id===progress.job_id);
    if(progress.status==='running'&&latest?.status==='awaiting_model'){
      if(latest.review_correction){
        const version=latest.review_correction.version;
        return version==='parking-arguments-correction-v1'?'正在修正工具格式':version==='candidate-choice-correction-v1'?'正在修正选点引用':version==='study-review-correction-v1'?'正在修正本组交接复核':'正在修正最终复核';
      }
      return Object.keys(latest.input?.allowed_actions||{}).join(',')==='stop'?'正在复核实测结果':'等待模型选择下一步';
    }
    return names[progress.status]||progress.status;
  }
  function decisionReason(turn){
    if(turn.stop_basis)return `<p><span>${turn.stop_basis_origin==='completed-study-comparisons'?'停止依据 · 来自逐组实测清单':'停止依据'}</span>${esc(turn.stop_basis)}</p>${turn.reason?`<details class="decision-source"><summary>查看模型原始理由</summary><p>${esc(turn.reason)}</p></details>`:''}`;
    if(turn.selection_reason)return `<p><span>本轮研究思路</span>${esc(turn.selection_reason)}</p><small>所据观测、否证条件和备选取舍见“为什么安排这次试验”。</small>`;
    const d=turn.arguments_diagnostic;
    const kind={json_syntax:'JSON语法错误',non_finite:'包含非有限数值',not_text:'参数不是文本'}[d?.kind];
    const format=kind?`<p><span>工具参数格式</span>${esc(kind)}${Number.isInteger(d.length)?` · ${esc(d.length)}字符`:''}${Number.isInteger(d.offset)?` · 错误位置${esc(d.offset)}`:''}；原始内容未保存。</p>`:'';
    return (turn.reason?`<p><span>这次选择</span>${esc(turn.reason)}</p>`:'')+format;
  }
  function riskReview(turn){
    const review=turn.risk_review;
    if(!review)return turn.risk?`<p><span>模型代价与风险说明 · 原文</span>${esc(turn.risk)}</p>`:'';
    const coverage=review.observed_detail_coverage||[];
    const coverageHtml=coverage.length?`<div><h5>该轮模型实际收到的新增过饱和明细</h5>${coverage.map(r=>`<p><button data-observation-ref="${esc(r.ref)}">${esc(r.ref.split(':').at(-1))} ↗</button> ${esc(r.text)}</p>`).join('')}<small>这是静态过饱和清单，不是动态排队或回堵证据。</small></div>`:'';
    return `<section class="risk-review"><p><span>实测代价 · 来自观测</span>${esc(review.measured_summary)}</p>${coverageHtml}<h5>模型待验证风险</h5>${review.unverified.length?review.unverified.map(r=>`<article><p>${esc(r.hypothesis)}</p><p><b>${r.verification_origin?'验证所需证据 · 模型选择类型，提供情况来自本轮输入：':'还需怎样验证 · 模型原文：'}</b>${esc(r.verification_needed)}</p><div class="observation-refs">${r.basis_refs.map(ref=>`<button type="button" data-observation-ref="${esc(ref)}">提出问题所据：${esc(ref.split(':').at(-1))} ↗</button>`).join('')}</div></article>`).join(''):'<p>模型未额外列出待验证风险，不表示没有风险。</p>'}<small>${esc(review.boundary)}</small></section>`;
  }
  function feedback(result){
    const rows=result.planning_trace||[],budget=result.model_budget;
    if(!budget&&!rows.length)return '';
    const status={pending:'准备观测',awaiting_model:'模型正在判断',accepted:'动作参数已校验',rejected:'调用失败 · 已停止',stopped:'已停止新增试验',blocked:'无法完成必需比较'};
    return `<section class="live-planning"><div class="live-plan-heading"><h4>观察 → 判断 → 实验 → 复核</h4>${budget?`<small>模型请求 ${esc(budget.used)} / ${esc(budget.limit)} · 已知 ${esc(budget.known_tokens??0)} tokens${budget.unknown_usage?' · 部分用量未知':''}</small>`:''}</div>${rows.length?rows.slice().reverse().map((t,i)=>`<details data-plan-detail="turn-${esc(t.job_id)}-${esc(t.ordinal)}" class="model-turn ${esc(t.status)}" ${i<2?'open':''}><summary><span class="turn-number">${esc(t.ordinal)}</span><b>${esc(turnNeedsReview(t)&&t.action==='stop'?'结果复核存在冲突':t.selected?.title||status[t.status])}</b><small>${esc(turnNeedsReview(t)?'结论待核对':status[t.status]||t.status)}</small></summary><div class="turn-body">${routing(t)}${reviewCorrection(t)}${executionDefinition(t)}${t.proposed_execution?`<details class="rejected-proposal"><summary>被拒动作提议 · 未执行</summary><p>${esc(t.proposed_execution.summary)}</p><p>${esc(t.proposed_execution.next_check)}</p><small>由该次提议的动作定义生成；没有执行这一提议。</small></details>`:""}${t.input?.context?.completion_guard&&!t.input.context.completion_guard.stop_allowed?'<p class="completion-rule">本轮有必需比较尚未完成，执行器未开放停止动作。</p>':''}${t.summary?`<p><span>${t.summary_origin?"所选动作 · 由执行定义生成":"读到的结果"}</span>${esc(t.summary)}</p>`:''}${decisionReason(t)}${riskReview(t)}${t.next_check?`<p><span>${t.next_check_origin?"实际安排 · 由所选动作定义生成":"接下来核对"}</span>${esc(t.next_check)}</p>`:''}${(t.proposal_excerpt||t.proposed_decision)?`<details class="rejected-proposal"><summary>查看被拒绝的工具提议</summary>${t.proposal_capture?`<p class="hint">仅保留公开工具字段，已过滤凭据和其他返回内容${t.proposal_capture.excerpted?"；部分内容有省略或截短":""}。这份提议未获执行。</p>`:""}<pre>${esc(JSON.stringify(t.proposal_excerpt||t.proposed_decision,null,2))}</pre></details>`:''}${t.status==='awaiting_model'&&!t.review_correction?'<p>新结果已完成，正在等待模型选择下一步；地图和观测仍可浏览。</p>':''}${(t.verification_notes||[]).length?`<div class="evidence-caveat"><b>执行器核对</b>${t.verification_notes.map(n=>`<p>${esc(n)}</p>`).join('')}</div>`:''}<div class="observation-refs">${(t.evidence_refs||t.input?.evidence_refs||[]).map(ref=>`<button type="button" data-observation-ref="${esc(ref)}">查看 ${esc(ref.split(':').at(-1))} ↗</button>`).join('')}</div><small>${esc(t.input?.context?.study_handoff?.actions?.includes(t.action)?'模型复核当前组并选定后续研究':t.kind==='parking'?'模型选择治理实验':t.kind==='oneway_target'?'模型依据新基准选择单向位置':'模型复核与研究调度')} · ${t.review_correction?'本次修正回复 ':''}${esc(t.usage?.tokens??'—')} tokens</small></div></details>`).join(''):'<p class="hint">先计算本次新基准。实际观测完成后，模型的选择与证据引用会逐轮出现在这里。</p>'}</section>`;
  }
  function scopeSummary(result){
    const scope=result.execution_scope;
    if(!scope||!result.experiments?.length)return '';
    const parking=result.experiments.some(x=>x.task?.scenario==='parking');
    const diagnostic=result.diagnosis_constraints;
    const diagnosticScope=diagnostic?.only?'全部研究范围':(diagnostic?.roads||[]).map(esc).join('、');
    return `<section class="plan-block"><h4>范围与执行条件</h4><p>研究范围：${scope.search_scope==='named_roads'?scope.roads.map(esc).join('、'):'整个案例路网'}</p>${diagnosticScope?`<p class="diagnosis-scope"><b>只做现状诊断：${diagnosticScope}</b><br>该范围仅观察现状，不安排治理实验。</p>`:''}${parking?`<p>${esc(scope.limit_note)}</p><p>每组比较 ${esc(scope.regional_design.regions_per_parking_group)} 个区域；具体治理位置和可用候选等待新基准观测，规划前不预设收益。</p>`:diagnostic?.only?'<p>读取速度、流量与V/C，形成现状诊断。</p>':'<p>定位方式以本组执行路线为准；治理效果等待本次实测。</p>'}</section>`;
  }
  function researchDepth(result){
    const depth=result.research_depth;if(!['research-depth-v1','research-depth-v2','research-depth-v3'].includes(depth?.version))return '';
    const names={find_one:'找到可用方案即可复核结束',compare_candidates:'比较多个实测备选',demand_robustness:'检验需求变化下的效果',adaptive:'按实测反馈决定研究深度'};
    const guard=[...(result.planning_trace||[])].reverse().find(t=>t.input?.context?.completion_guard)?.input.context.completion_guard;
    const noun=depth.candidate_domain==='parking'?'停车方案':'治理方案';
    const progress=guard?.required_candidate_comparisons?`；本组已测齐 ${esc(guard.measured_candidate_comparisons)} 个`:'';
    return `<section class="plan-block research-depth"><h4>本次研究深度</h4><p>${esc(names[depth.intent]||names.adaptive)}</p>${depth.minimum_candidates>1?`<p>至少实测 ${esc(depth.minimum_candidates)} 个不同${noun}${progress}。未测备选不计入比较。</p>`:''}${depth.demand_validation?'<p>每个约定需求条件都要有自己的基准与治理配对。</p>':''}${depth.comparison_quote?`<small>原文：${esc(depth.comparison_quote)}</small>`:''}${depth.demand_quote?`<small>原文：${esc(depth.demand_quote)}</small>`:''}</section>`;
  }
  function hypothesisDraft(rows){
    const tests={core_time_decrease:'核心耗时低于无治理基准',admissible:'通过全部推荐约束',neighbor_limits:'邻区两项代价合格',control_response:'原生控制响应生效',core_advantage_over_parking:'核心耗时优于仅停车',combination_core_advantage:'组合核心耗时分别优于两个单项',unavailable:'本接口不能直接检验'};
    const policies={all:'全部治理类型',parking:'仅停车',traffic_control:'交通组织单项',combined:'组合措施',control_and_combined:'交通组织及组合（不含仅停车）'};
    return rows?.length?`<details class="rejected-proposal"><summary>原问题与当时判据</summary>${rows.map((h,i)=>`<article><b>H${i+1} · ${esc(h.hypothesis)}</b><p>需要证据：${esc(h.evidence_needed)}</p>${h.criterion?`<p>检验：${esc(tests[h.criterion.test]||h.criterion.test)}<br>针对：${esc(policies[h.criterion.policies]||h.criterion.policies)} · ${h.criterion.quantifier==='all'?'全部已试措施':'至少一个已试措施'}<br>提议研究组：${(h.criterion.studies||[]).map(esc).join('、')}</p>`:'<p>当时未保留有效判据参数，不能事后补填。</p>'}${h.criterion_status==='invalid'?'<p class="evidence-gap">参数搭配未通过，不代表已执行。</p>':''}</article>`).join('')}</details>`:'';
  }
  function hypothesisReview(check){
    if(!check?.issues?.length)return '';
    return `<section class="evidence-caveat"><h5>开跑前发现的判据冲突</h5>${check.issues.map(i=>`<p><b>${esc(i.hypothesis_id)}</b> · ${esc(i.message)}</p>`).join('')}<p class="hint">${check.correction_feasible?'本次预算允许修正一次；实际是否修正见下一条请求记录。':'没有可用修正机会，保留失败。'}${check.version==='initial-hypothesis-correction-v3'?'仅修正获准的措施或量词字段，使其与保留的原问题一致；原文、指标与实验安排保持。':'修正只可调整被检验的措施类型；原问题、判据类型、量词与实验安排保持。'}此时尚未执行仿真。</p></section>`;
  }
  function policyPatch(receipt){
    if(!['hypothesis-policy-patch-v1','hypothesis-field-patch-v1'].includes(receipt?.version))return '';
    const fields=receipt.version==='hypothesis-field-patch-v1';
    const policies={all:'全部治理类型',parking:'仅停车',traffic_control:'交通组织单项',combined:'组合措施',control_and_combined:'交通组织及组合（不含仅停车）'};
    const applied=receipt.status==='applied';
    const choices=Object.entries(receipt.selections||{}).map(([id,value])=>`<p><b>${esc(id)}</b> → ${fields?Object.entries(value||{}).map(([key,v])=>`${esc(({policies:'措施',quantifier:'量词'})[key]||key)}：${esc((key==='policies'?policies:{all:'全部已试措施',any:'至少一个已试措施'})[v]||v)}`).join('；'):esc(policies[value]||value)}</p>`).join('');
    return `<section class="evidence-caveat policy-patch"><h5>${fields?'模型修正判据字段':'模型只修正检验对象'}</h5>${choices}<p>${applied?'原方案已保留，仅应用上述字段选择；完整方案是否通过校验见本次状态。':receipt.status==='rejected'?'字段修正未通过，未应用到原方案。':'字段修正尚未完成校验。'}</p><p class="hint">${fields?'量词字段按原话纠正，不改变原命题；原问题、证据需求、指标和实验安排保持。':'原问题、证据需求、量词和实验安排由程序保持，不要求模型重述。'}第一次失败仍保留，本次尚未执行仿真。</p></section>`;
  }
  function initialReview(result){
    const attempts=result.initial_planning_attempts||[];
    if(attempts.length<2)return budgetCheck(result.budget_check);
    const status={ready:'规划已通过执行校验',needs_input:'需要补充实际条件',needs_validation:'能力待验证',validation_failed:'仍未通过校验',model_failed:'调用失败'};
    return `<section class="plan-block initial-review"><h4>规划修正 · 1次上限</h4><p class="hint">以下是仿真前的决策与校验。${attempts.filter(a=>a.requested).length} 次请求均计入总预算，尚不能证明治理有效。</p><ol>${attempts.map((a,i)=>`<li><b>${i===0?'初始提议未通过':a.budget_check?.fits===false?'修正提议仍超出预算':esc(status[a.status]||a.status)}</b><p>${esc(a.error||a.explanation)}</p>${budgetCheck(a.budget_check)}${hypothesisReview(a.hypothesis_check)}${policyPatch(a.policy_patch)}${hypothesisDraft(a.proposal?.assessment)}${i===1?'<small>依据原始请求与实际校验反馈重新决策；第一次失败已保留。</small>':''}<p class="hint">${esc(a.usage?.tokens??'用量未知')}${a.usage?.tokens!=null?' tokens':''}</p></li>`).join('')}</ol></section>`;
  }
  function budgetCheck(check){
    if(!check?.groups?.length)return '';
    return `<section class="initial-cost ${check.fits?'fits':'over-budget'}"><h5>${check.fits?'本地预算核算通过':'提议超出预算 · 尚未执行'}</h5><div class="cost-totals"><span>最低仿真 <b>${esc(check.native?.required)} / ${esc(check.native?.limit)}</b> 次</span><span>最低模型请求 <b>${esc(check.model?.required)} / ${esc(check.model?.limit)}</b> 次</span></div><table><thead><tr><th>研究 / 范围</th><th>仿真</th><th>后续请求</th></tr></thead><tbody>${check.groups.map(g=>`<tr><td><b>${esc(g.title)}</b><small>${esc(g.conditions?.road_name||'整个案例路网')} · ${['oneway','circulation'].includes(g.conditions?.scenario)?'Vehicle1':'Vehicle'} 独立基准</small>${g.may_omit?`<small class="cost-optional">${g.omission?.kind==='extra_factorial_component'?'额外独立停车，可由模型重新考虑':'额外体检，可由模型重新考虑'}</small>${g.omission?`<small>原文：${esc(g.omission.source_quote)}</small><small>${esc(g.omission.reason)}</small>`:''}`:''}</td><td>${esc(g.native_runs)}</td><td>${esc(g.model_requests)}</td></tr>`).join('')}</tbody></table><p class="hint">治理组已含本组新基准与必需对照；模型总数含初始请求及结果复核。不同引擎各自计算，不共享基准结果。${check.correction_feasible?'可在保留必需研究与条件的前提下，由模型重新考虑标记的额外研究；最多修正一次并计入总额度。':''}</p></section>`;
  }
  function knowledge(result){
    const initial=result.knowledge_retrieval;
    const latest=[...(result.planning_trace||[])].reverse().find(t=>t.input?.context?.knowledge_retrieval)?.input.context.knowledge_retrieval;
    const bundles=[['初始规划',initial],['最近一次模型决策',latest]].filter(([,bundle])=>bundle?.version==='curated-knowledge-rag-v1');
    if(!bundles.length)return '';
    return `<section class="plan-block knowledge-retrieval"><h4>本地知识检索依据</h4><p class="hint">以下是模型当轮收到的方法摘录；治理效果仍以本次原生仿真为准。</p>${bundles.map(([label,bundle])=>`<details><summary>${esc(label)} · ${esc((bundle.hits||[]).length)} 条</summary>${(bundle.hits||[]).map(hit=>`<article><b>【K:${esc(hit.id)}】</b><p>${esc(hit.text)}</p><small>来源：${esc(hit.source)}</small></article>`).join('')||'<p>本轮没有匹配的资料。</p>'}</details>`).join('')}</section>`;
  }
  function render(result){
    const entries=result.experiments||[],progress=result.progress||[];
    if(!entries.length&&result.missing?.length){
      const overBudget=result.budget_check?.fits===false||(result.initial_planning_attempts||[]).at(-1)?.budget_check?.fits===false;
      return `<article class="model-plan"><header class="plan-lead"><span class="plan-kicker">${overBudget?'模型规划未符合预算':result.status==='needs_validation'?'当前能力无法执行':'需要明确执行条件'}</span><h3>${esc(result.goal)}</h3><p>${esc(result.reason)}</p></header>${initialReview(result)}${knowledge(result)}<div class="plan-error"><b>本次没有启动仿真</b><ul>${result.missing.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div><p class="hint">${overBudget?'本次规划已停止，原始请求、预算及失败提议已保留；尚未得到可执行方案。':'本次已停止。请在左侧补充或调整请求后重新提交；不会擅自更改治理目标。'}</p></article>`;
    }
    if(!entries.length&&!result.assessment?.length)return '';
    return `<article class="model-plan"><header class="plan-lead"><span class="plan-kicker">${['natural-strategy-plan-v3','natural-strategy-plan-v4','natural-strategy-plan-v5','natural-strategy-plan-v6','natural-strategy-plan-v7','natural-strategy-plan-v8','natural-strategy-plan-v9','natural-strategy-plan-v10','natural-strategy-plan-v11'].includes(result.version)?'基于实测的智能规划':result.version==='natural-strategy-plan-v2'?'历史模型规划 · 固定执行':'历史策略编排'}</span><h3>${esc(result.goal)}</h3>${result.planning_trace?.length?`<details data-plan-detail="initial" class="initial-rationale"><summary>初始规划依据</summary><p>${esc(result.reason)}</p></details>`:`<p>${esc(result.reason)}</p>`}<div class="plan-budget"><span>${entries.length} 组研究</span><span>安排 ${esc(result.allocated_runs??0)} / 上限 ${esc(result.budget)} 次</span>${result.unallocated_runs?`<span>未分配 ${esc(result.unallocated_runs)} 次</span>`:''}</div></header>${currentPulse(result)}${progress.some(p=>p.review?.goal_review?.unanswered?.length)?`<div class="evidence-caveat"><b>研究未完成，以下问题仍缺少证据</b>${progress.flatMap(p=>p.review?.goal_review?.unanswered||[]).map(n=>`<p>${esc(n)}</p>`).join('')}</div>`:''}${initialReview(result)}${knowledge(result)}${scopeSummary(result)}${researchDepth(result)}${choiceView?choiceView.render(result):""}${hypothesisChecks(result)}${measuredComparison(result)}${conclusion(result)}${programme(result)}${completion(result)}${research(result)}${spatial(result)}${onewayTargets(result)}${feedback(result)}${(result.normalization_notes||[]).map(x=>`<p class="recovery-note">执行校验：${esc(x)}</p>`).join('')}
      ${result.assessment?.length?`<section class="plan-block"><h4><span>01</span>初始问题判断 · 不等同于结论</h4>${result.assessment.map(x=>`<div class="hypothesis">${x.authoring?'<small>由模型定义生成的研究问题</small>':''}<b>${esc(x.hypothesis)}</b><p>需要证据：${esc(x.evidence_needed)}</p>${criterionScope(x)}</div>`).join('')}</section>`:''}
      ${result.alternatives?.length?`<section class="plan-block"><h4><span>02</span>策略比较与取舍</h4>${result.alternatives.map(x=>`<div class="alternative ${x.decision==='deferred'?'deferred':''}"><div><b>${esc(x.approach)}</b><span>${x.decision==='selected'?'采用':'暂缓'}</span></div><p>${esc(x.tradeoff)}</p></div>`).join('')}</section>`:''}
      ${entries.length?`<section class="plan-block"><h4><span>03</span>执行路线与预算</h4>${entries.map((x,i)=>{const d=x.design,p=progress[i];return `<section class="strategy-step"><div class="step-heading"><span class="step-number">${i+1}</span><div><h5>${esc(x.title)}</h5><small>${esc(x.task.road_name||'全网')} · ${x.runs} 次上限 · ${esc(x.target_method)}</small></div></div><p>${esc(x.rationale)}</p>${d?`<div class="design-chips">${d.matrix.map(m=>`<span>${esc(m)}</span>`).join('')}</div><p>小汽车需求 × ${esc(d.factors.join(' / '))}${x.feedback_reserve?` · 反馈预留 ${x.feedback_reserve} 次`:' · 未预留追加候选'}</p><details data-plan-detail="execution-${i}" open><summary>如何执行与验证</summary><ol class="execution-stages">${d.stages.map(s=>`<li><b>${esc(s.title)}</b><p>${esc(s.detail)}</p></li>`).join('')}</ol></details><details data-plan-detail="criteria-${i}"><summary>方案必须满足的条件</summary><ul class="criteria-list">${d.criteria.map(c=>`<li>${esc(c)}</li>`).join('')}</ul></details>${d.completion?`<p class="completion-rule">完成条件：${esc(d.completion)}</p>`:''}<div class="recovery-note">未达预期时：${esc(d.recovery)}</div>`:''}<div class="plan-run-status"><strong>${p?esc(progressLabel(p,result.planning_trace||[])):'已编排 · 等待提交'}</strong>${p?(p.status==='running'?` · 本组上限 ${p.runs??x.runs} 次`:` · 已启动 ${p.run_count??0} / ${p.runs??x.runs} 次`):''}</div>${evidence(p)}</section>`;}).join('')}</section>`:''}
      ${(result.missing||[]).length?`<div class="plan-error"><b>本次没有启动仿真</b><ul>${result.missing.map(m=>`<li>${esc(m)}</li>`).join('')}</ul></div>`:''}
      <p class="plan-boundary">${esc(result.planning_basis||'各组使用自己的新基准，不跨适配器混算收益。')}<br>${esc(entries[0]?.design?.stop||'保留失败、无候选和无收益结果。')}</p></article>`;
  }
  function proposal(draft,origin){
    if(!draft)return '';
    const controls={parking:'停车',oneway:'单向',turn:'禁转'},studies={standard:'策略对照',diagnosis:'现状诊断',robust:'稳健研究',demand_stress:'需求压力',spillover:'邻区保护'};
    const profiles={base:'标准需求',variation:'需求波动',surge:'需求增加'};
    return `<div class="attempt-proposal"><b>${['original_plus_model_policy_patch','original_plus_model_field_patch'].includes(origin)?'原方案＋本次字段选择 · 未经执行验证':'模型提议摘要 · 未经执行验证'}</b><p>${esc(draft.goal)}</p>${draft.intent?`<p>本次决策：${esc(({execute:"安排研究",needs_input:"提出具体问题",unsupported:"必需能力未接入"})[draft.intent]||"未知")}</p>`:""}<p>${esc(draft.reason)}</p><ol>${(draft.studies||[]).map(s=>`<li><b>${esc(studies[s.study]||s.study||'未指定研究')} · ${esc(s.road||'全网自动选区')}</b><small>${esc(s.strategies?.length?s.strategies.map(x=>controls[x]||x).join('＋'):s.study==='diagnosis'?'只诊断':'未指定措施')} · ${esc(profiles[s.demand_profile]||s.demand_profile||'未指定需求')}</small>${s.target&&s.target!=='auto'?`<small>目标引用：${esc(s.target)}</small>`:''}<p>${esc(s.rationale)}</p></li>`).join('')}</ol>${hypothesisDraft(draft.assessment)}${draft.missing?.length?`<p>模型列出的缺口：${draft.missing.map(esc).join('；')}</p>`:''}${draft.question_citations?.length?`<p>模型要求核对的原文：${draft.question_citations.map(x=>'“'+esc(x.source_quote)+'”').join('；')}</p>`:''}${draft.unsupported?.length?`<p>模型指出的能力缺口：${draft.unsupported.map(x=>esc(({signal:"信号配时",construction:"施工容量调整",transit:"公交优先",staggered:"分时错峰",combined_oneway_turn:"同时叠加单向与禁转",other:"其他未接入控制"})[x]||x)).join("；")}</p>`:""}<p class="hint">以上方案不代表已执行或通过；实际安排以本地校验后的任务卡为准。</p></div>`;
  }
  function renderAttempts(rows){
    const status={ready:'已生成规划',needs_input:'返回待补充',needs_validation:'能力待验证',validation_failed:'规划校验未通过',model_failed:'模型调用失败',preparing:'本地检查中',awaiting_model:'等待模型',validating:'校验回复中'};
    const time=value=>Number.isNaN(Date.parse(value))?value:new Date(value).toLocaleString('zh-CN',{hour12:false});
    return rows.length?rows.map(r=>`<details class="planning-attempt"><summary><b>${esc(r.budget_check&&!r.budget_check.fits?'提议超出预算':status[r.status]||r.status)}</b><small>${esc(time(r.started))}</small></summary><p>${r.phase?`<b>${r.phase==='validation_correction'?'校验后修正 · 第2次请求':'初始提议 · 第1次请求'}</b>`:''}</p><p>${esc(r.request)}</p><p>${esc(r.error||r.explanation||'当时未记录具体说明；不能据此判断失败原因。')}</p>${budgetCheck(r.budget_check)}${hypothesisReview(r.hypothesis_check)}${policyPatch(r.policy_patch)}${proposal(r.proposal,r.proposal_origin)}${responseInfo(r.usage)}<small>${r.requested?`1 次模型请求 · ${Number.isFinite(r.usage?.tokens)?esc(r.usage.tokens)+' tokens':'用量未知'}`:'0 次模型请求 · 本地检查'}${r.status==='ready'?' · 后续执行另见调度记录':' · 本次未取得可执行规划'}</small></details>`).join(''):'<p class="hint">尚无初始规划记录。</p>';
  }
  function responseInfo(usage){
    if(!usage)return '';
    const ends={tool_calls:'工具调用已返回',stop:'回复已结束',length:'输出达到本次上限',content_filter:'回复受限',completed:'回复已完成',incomplete:'回复未完成',failed:'服务返回失败',cancelled:'请求已取消'};
    const facts=[],end=ends[usage.finish_reason]||ends[usage.response_status];
    if(end)facts.push(end);
    for(const [key,label] of [['tool_call_count','工具调用'],['input_tokens','输入 tokens'],['output_tokens','输出 tokens'],['reasoning_tokens','其中推理 tokens']]){
      if(Number.isInteger(usage[key])&&usage[key]>=0)facts.push(`${label} ${usage[key]}`);
    }
    return facts.length?`<p class="hint">接口记录：${facts.map(esc).join(' · ')}。规划是否有效以校验结果为准。</p>`:'';
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={render,renderAttempts,programmeStatus};
  else window.trafficPlannerView={render,renderAttempts,programmeStatus};
})();
