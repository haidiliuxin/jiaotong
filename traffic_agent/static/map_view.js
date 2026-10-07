/* Pure display semantics shared by the canvas and deterministic checks. */
(() => {
  const statuses={normal:{label:'通行余量充足',color:'#a4adb5'},near_capacity:{label:'接近饱和',color:'#c68a24'},overloaded:{label:'过饱和',color:'#df6845'},severe:{label:'严重过饱和',color:'#b83d4d'}};
  function preferredRun(runs,recommendation){return runs.find(r=>r.name===recommendation&&r.changes?.length)?.name||runs.filter(r=>r.changes?.length).at(-1)?.name||runs.at(-1)?.name;}
  function vcLabel(value){
    if(!Number.isFinite(value))return '—';
    const text=value.toLocaleString('zh-CN',{maximumFractionDigits:5});
    return text==='1'&&value!==1?(value>1?'略高于1':'略低于1'):text;
  }
  function color(link,side,metric){
    const v=(side?link.after:link.before);
    if(metric==='neutral')return '#a4adb5';
    if(metric==='vc')return statuses[link.status[side?'after':'before']].color;
    if(metric==='speed')return v[0]<=10?'#b83d4d':v[0]<=20?'#df6845':v[0]<=40?'#c68a24':'#4d9675';
    if(!side)return '#a4adb5';
    const change=metric==='impact'?link.delta.time_pcu_h:-link.delta.speed_kmh;
    const threshold=metric==='impact'?.01:.1;
    return change>threshold?'#c45351':change< -threshold?'#238879':'#c2c8cc';
  }
  function motorClosure(link,side){
    if(!side||!link.motor_closure)return null;
    const response={passed:'四类机动车响应已核对',failed:'禁行响应未通过',not_checked:'禁行响应待核对'};
    return {label:'机动车方向禁行',color:'#963c47',response:response[link.motor_closure.response]||response.not_checked};
  }
  function extreme(links,kind){
    const eligible=links.filter(l=>Number.isFinite(l.delta?.time_pcu_h)&&
      (kind==='improved'?l.delta.time_pcu_h<-.01:l.delta.time_pcu_h>.01));
    return eligible.sort((a,b)=>kind==='improved'?a.delta.time_pcu_h-b.delta.time_pcu_h:
      b.delta.time_pcu_h-a.delta.time_pcu_h)[0]||null;
  }
  function caption(comparison,metric,total){
    const colorMeaning={vc:'颜色表示饱和状态；状态相同不代表速度没有变化。',speed:'颜色表示实际速度，两图共用色标。',impact:'颜色表示耗时增减；基准图是零变化参照。',change:'颜色表示速度增减；基准图是零变化参照。',neutral:'灰色只表示道路几何，不反映运行状况。'};
    const reason=comparison.kind==='policy'&&comparison.changed_links?'两图来自本任务同需求的基准与候选。'+colorMeaning[metric]:comparison.reason;
    return reason+(comparison.comparable?` 指标有变化 ${comparison.changed_links} / ${total} 条。`:'')+(comparison.motor_closed_links?' 禁行方向另作标记，汇总速度与V/C不表示允许通行。':'');
  }
  function policyOutcome(task,run){
    if(run?.execution?.status!=='completed'||!run.comparison)return null;
    const finite=value=>Number.isFinite(value)?value:null;
    const speed=finite(run.metrics?.core?.weighted_speed_kmh),target=finite(task?.target_speed_kmh);
    return {core_speed_kmh:speed,target_speed_kmh:target,target_met:speed!=null&&target!=null?speed>=target:null,
      eligible:typeof run.comparison.eligible==='boolean'?run.comparison.eligible:null,
      core_time_change_pct:finite(run.comparison.delta?.core?.running_time_change_pct),
      network_oversaturated_net_km:finite(run.comparison.delta?.network?.oversaturated_km_change),
      network_oversaturated_gross_km:finite(run.comparison.new_network_oversaturated_km)};
  }
  const api={statuses,preferredRun,color,caption,vcLabel,motorClosure,extreme,policyOutcome};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else window.trafficMapView=api;
})();
