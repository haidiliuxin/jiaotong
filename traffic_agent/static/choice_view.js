/* Public selection rationale. Every displayed number comes from frozen inputs. */
(() => {
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const n=(v,unit='',digits=2)=>Number.isFinite(v)?`${v!==0&&Math.abs(v)<.5*10**-digits?v.toPrecision(3):v.toFixed(digits)}${unit}`:'未提供';
  function comparisonCard(entry){
    const relation=entry.input.context.choice_observations.recent_comparisons;
    if(!relation)return '';
    const actual=entry.candidate_comparison;
    const rows=[['所选候选',entry.action,actual?.chosen],['暂缓备选',entry.selection_basis.alternative_action,actual?.alternative]].map(([label,action,facts])=>{
      const group=relation.groups.find(g=>g.actions.includes(action));
      const count=Number.isInteger(facts?.direction_count)?`${facts.direction_count}条治理方向；`:'';
      const demand=group?.demand_summary||relation.demand_designs?.find(d=>d.id===group?.demand_design_id)?.summary;
      return group?`<tr><th>${label}</th><td>${count}${esc(group.interpretation)}${demand?`<p>${esc(demand)}</p>`:''}</td></tr>`:'';
    }).join('');
    const referenceCount=Number.isInteger(actual?.reference_direction_count)?`；实际治理${actual.reference_direction_count}条方向`:'';
    return `<section class="candidate-comparison"><h5>这次对照改变了什么 · 由参数生成</h5><p>相对已完成的 <button data-observation-ref="${esc(relation.reference_ref)}">${esc(relation.reference_ref.split(':').at(-1))} · 需求 × ${esc(relation.reference_demand_factor)} ↗</button>${referenceCount}</p><table><tbody>${rows}</tbody></table><small>${esc(relation.scope)}</small></section>`;
  }
  function sourceCard(source,payload,chosen,alternative){
    const o=payload.observations.find(x=>x.ref===source.ref);if(!o)return '';
    const option=payload.allowed_actions[source.action];let rows=[],title='',extra='';
    if(source.kind==='target'){
      if(!option)return '';
      rows=option.roads||[];title=option.title;
      extra=`<p>拟关闭方向小汽车量：<b>${n(option.baseline_car_flow,' veh/h')}</b></p><small>图检查：${option.topology?.eligible?'可开展试验':'尚未通过'}；${esc(option.topology?.bus_routes_checked??'未知')} 条公交线路已检查。可达不代表治理有效。</small>`;
    }else if(source.kind==='area'){
      const area=o.spatial?.regions?.find(a=>a.id===source.region);if(!area)return '';
      rows=area.roads||[];title=area.title;
      extra=`<p>该候选区加权速度 ${n(area.weighted_speed_kmh,' km/h')}</p>`;
    }else if(source.kind==='road'){
      rows=(o.core_roads||[]).filter(r=>r.from_node===source.direction[0]&&r.to_node===source.direction[1]);
      title=rows[0]?.name||'冻结核心方向';
    }else if(source.kind==='trial'){
      title='已完成试验的配对反馈';
      extra=`<p>核心耗时变化 ${n(o.delta?.core?.running_time_change_pct,'%')} · 邻区 ${n(o.delta?.neighbor?.running_time_change_pct,'%')}</p><p>邻区毛新增过饱和 ${n(o.new_neighbor_oversaturated_km,' km',3)} · ${o.eligible===true?'约束通过':o.eligible===false?'约束未通过':'尚未判定'}</p>${o.violations?.length?`<small>${o.violations.map(esc).join('；')}</small>`:''}`;
    }
    const indices=rows.map(r=>r.index).filter(Number.isInteger);
    const actions=source.actions||[source.action],isChosen=actions.includes(chosen),isAlternative=actions.includes(alternative);
    const role=source.kind==='trial'?((source.related_actions||[]).includes(alternative)?'上一轮实测反馈 · 与暂缓备选同区域':'上一轮实测反馈'):isChosen&&isAlternative?'所选与备选共用的基准':isChosen?'所选候选的基准':'备选候选的基准';
    const attrs=indices.length?`data-region-job="${esc(source.ref.split(':')[0])}" data-region-run="${esc(o.run)}" data-region-indices="${esc(indices.join(','))}"`:`data-observation-ref="${esc(source.ref)}"`;
    return `<div class="choice-observation"><small>${role}</small><header><b>${esc(title)}</b><button ${attrs}>${esc(o.run)} · 需求 × ${esc(o.demand_factor)} ↗</button></header>${rows.length?`<table><thead><tr><th>方向</th><th>速度 km/h</th><th>V/C</th><th>停车级</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.from_node)}→${esc(r.to_node)}${source.kind==='target'&&option.closed_direction?` <small>${r.from_node===option.closed_direction[0]&&r.to_node===option.closed_direction[1]?'拟关闭':'保留通行'}</small>`:''}</td><td>${n(r.speed_kmh)}</td><td>${n(r.vc)}</td><td>${esc(r.parking??'未知')}</td></tr>`).join('')}</tbody></table>`:''}${extra}</div>`;
  }
  function render(result){
    const entries=(result.planning_trace||[]).filter(t=>t.status==='accepted'&&t.selection_basis&&t.input?.context?.choice_observations);
    if(!entries.length)return '';
    return `<section class="choice-evidence"><h4>为什么安排这次试验</h4><p class="hint">以下是模型选点时可见的观测。数值来自输入记录；机制假设和备选取舍另列，后续仍须实测。</p>${[...entries].reverse().map((t,i)=>{
      const b=t.selection_basis,p=t.input,sources=p.context.choice_observations.sources;
      const alternative=p.allowed_actions[b.alternative_action];
      const primary=b.observed_ids.map(id=>sources.find(s=>s.id===id)).filter(Boolean).map(s=>sourceCard(s,p,t.action,b.alternative_action)).join('');
      const separateAlternative=b.alternative_observed_id&&!b.observed_ids.includes(b.alternative_observed_id)
        ?sources.find(s=>s.id===b.alternative_observed_id):null;
      const alternativeEvidence=separateAlternative?`<h5>暂缓备选所据观测</h5>${sourceCard(separateAlternative,p,t.action,b.alternative_action)}`:'';
      const recentId=p.context.choice_observations.recent_trial;
      const recentSource=recentId&&!b.observed_ids.includes(recentId)?sources.find(s=>s.id===recentId):null;
      const recentEvidence=recentSource&&(t.evidence_refs||[]).includes(recentSource.ref)
        ?`<h5>本轮引用的最近配对反馈</h5>${sourceCard(recentSource,p,t.action,b.alternative_action)}`:'';
      const original=p.context.choice_rationale_protocol==='choice-rationale-v1'?'<small>研究假设、否证条件和备选取舍来自本轮模型，效果仍须由后续实测检验。</small>':`<details><summary>模型原文取舍</summary><p>${esc(t.summary)}</p><p>${esc(t.reason)}</p><small>以上为模型解释，不表示未测的机制已经得到证实。</small></details>`;
      return `<details data-plan-detail="choice-basis-${esc(t.job_id)}-${esc(t.ordinal)}" ${i===0?'open':''}><summary><b>第 ${esc(t.ordinal)} 轮 · ${esc(p.allowed_actions[t.action]?.title||t.action)}</b></summary><h5>已观测到什么</h5>${primary}${recentEvidence}${alternativeEvidence}${comparisonCard(t)}<div class="choice-hypothesis"><b>待验证假设</b><p>${esc(b.working_hypothesis)}</p><b>什么结果会否定这个思路</b><p>${esc(b.disconfirming_result)}</p></div><div class="choice-alternative"><b>${alternative?'本轮暂缓：'+esc(alternative.title):'本轮没有其他合法候选'}</b><p>${esc(b.alternative_tradeoff)}</p>${alternative?'<small>备选效果尚未实测，不能因未选就判为无效。</small>':''}</div>${original}</details>`;
    }).join('')}</section>`;
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={render};
  else window.trafficChoiceView={render};
})();
