/* Explicit paid actions only. Refreshing the map never contacts a model. */
(() => {
  let matched = null, matchedInput = '', busy = false, displayedKey='', previewFailure=false, attemptsKey='', attemptsHydrated=false;
  const emptyPlanning=$('planner-result').innerHTML;
  const stateNames = {ready:'可调度',needs_input:'待补充',needs_validation:'能力待验证',starting:'启动中',running:'计算中',created:'已创建',completed:'已完成',incomplete:'研究未完成',failed:'失败',no_candidate:'无候选',queued:'待执行',skipped:'未执行',interrupted:'执行中断 · 不自动重跑',stopped:'规划提前停止'};
  const progressDetail = (result,group) => {
    if(result.status==='completed')return group.status==='no_candidate'?'无候选':group.recommendation||group.job_id;
    const runs=group.completed_run_count??group.run_count??0;
    if(result.status==='no_candidate')return `${runs}个场景已测 · 无候选`;
    if(['incomplete','failed','stopped','interrupted'].includes(result.status))return `${runs}个场景已测 · 整项未完成`;
    return runs?`${runs}个场景已测 · 仍在研究`:group.job_id;
  };
  const input = () => $('matcher').value==='model'?{request:$('request').value,matcher:'model'}:{...payload(),matcher:'rules'};
  let activeMode='rules', drafts={rules:$('request').value,model:''}, viewedMode=null;
  function attemptStatus(text=''){
    $('attempt-status').textContent=text;
    $('attempt-status').hidden=!text;
  }
  function modeNote(){
    const inputMode=$('matcher').value==='model'?'大模型自主规划':'规则模式';
    const id=current?.state?.id||selected;
    const viewed=id?`${viewedMode?(viewedMode==='model'?'大模型任务':'规则任务'):'来源待核对'} ${id}`:'尚未选择实验';
    $('view-mode-note').textContent=`当前输入：${inputMode}；右侧查看：${viewed}。${previewFailure&&id?'本次规划未完成，地图保留的是另一个实验，图中收益不属于本次请求。':''}`;
  }
  function modeChanged(){
    drafts[activeMode]=$('request').value;activeMode=$('matcher').value;
    const model=activeMode==='model';$('request').value=drafts[activeMode];
    document.body.classList.toggle('model-mode',model);$('left-panel-title').textContent=model?'新建交通规划':'研究控制台';
    for(const id of ['rule-templates','rule-settings','preview','preview-card','history-block'])$(id).hidden=model;
    $('rule-settings').disabled=model;$('model-intro').hidden=!model;$('planning-help').hidden=!model;
    $('request').rows=model?8:3;
    $('mode-note').textContent=model?'先规划，再读取新结果决定后续实验。默认最多15次仿真、7次模型请求；初始规划至多修正1次，交接与最终复核共享1次修正，并预留剩余研究预算。所有修正计入总额，接口失败即停止。':'按手动配置匹配任务并运行。规则模式不调用模型。';
    $('run').textContent=model?'自动编排并运行':'匹配并开始 ↗';
    if(!selected)$('job-title').textContent=model?'描述交通问题，开始智能规划':'先预览任务，再开始仿真';
    modeNote();
    matched=null;matchedInput='';previewFailure=false;displayedKey='';attemptStatus();modeNote();error();
  }
  $('matcher').onchange=modeChanged;modeChanged();
  function programme(result){
    const headline=window.trafficPlannerView.programmeStatus(result);
    if($('programme-status').innerHTML!==headline)$('programme-status').innerHTML=headline;
    $('programme-status').hidden=!headline;
    const key=JSON.stringify(result);if(key===displayedKey)return;displayedKey=key;
    const remember=root=>new Map([...root.querySelectorAll('details[data-plan-detail]')].map(d=>[d.dataset.planDetail,d.open]));
    const restore=(root,saved)=>root.querySelectorAll('details[data-plan-detail]').forEach(d=>{if(saved.has(d.dataset.planDetail))d.open=saved.get(d.dataset.planDetail);});
    const opened=remember($('planner-result'));
    $('planner-result').innerHTML=window.trafficPlannerView.render(result)||emptyPlanning;
    if(result.job_id)restore($('planner-result'),opened);
    $('plan-expand').disabled=!result.experiments?.length;
    if($('plan-dialog')?.open){
      const top=$('plan-dialog').scrollTop,expanded=remember($('plan-document'));
      $('plan-document').innerHTML=$('planner-result').innerHTML;
      restore($('plan-document'),expanded);
      $('plan-dialog').scrollTop=top;
    }
  }
  function summary(result) {
    viewedMode=result.matcher;modeNote();
    const usages = result.initial_model_usages || (result.model_usage?[result.model_usage]:[]);
    const known=usages.reduce((sum,u)=>sum+(u.tokens??0),0),unknown=usages.some(u=>u.tokens==null);
    $('dispatch-summary').textContent = `${stateNames[result.status] || result.status} · ${result.matcher === 'model' ? 'Agent策略编排' : '规则匹配'}。${result.reason} ${result.missing.join('；')}${usages.length ? ` 初始规划 ${usages.length} 次请求，已知 ${known} tokens${unknown?'，部分用量未知':''}；已计入总预算。` : ''}`;
    programme(result);
    if (result.plan && result.matcher!=='model') showPlan(result.plan);
    else {$('preview-card').open=true;$('plan-panel').textContent=result.missing.join('；');}
  }
  async function match() {
    const body=input(), key=JSON.stringify(body);
    if(matched && matchedInput===key)return matched;
    matched=null;matchedInput='';previewFailure=false;attemptsHydrated=true;
    attemptStatus(body.matcher==='model'?'本次模型规划中；尚无本次仿真结果。':'本次正在匹配规则任务。');modeNote();
    showPane('planning');
    $('dispatch-summary').textContent=body.matcher==='model'?'正在校验输入与道路范围，通过后请求模型规划…':'正在匹配案例情景…';
    if(body.matcher==='model'){$('programme-status').hidden=true;$('programme-status').innerHTML='';$('planner-result').innerHTML='<div class="plan-waiting"><b>正在规划研究路线</b><progress aria-label="等待模型规划"></progress><p>模型将给出待验证假设、策略取舍与实验顺序。返回后校验预算和工具权限，再开始仿真。</p><p>前提或提议预算存在可修正问题时，最多向模型反馈修正1次；治理设计与原约束必须保留，修正计入7次总上限。接口失败即停止。当前尚无新结论或交通结果。</p></div>';displayedKey='';}
    const result=await api('/api/dispatch/preview',body);
    matched=result;matchedInput=key;attemptStatus(result.status==='ready'?'本次计划已校验；等待提交仿真。':'本次尚未启动仿真，请查看规划结果。');summary(result);return result;
  }
  async function action(run) {
    if(busy || launching)return;
    if(window.dispatchRunning){error('策略实验仍在执行，请完成后再提交新任务。');return;}
    busy=true;launching=true;$('preview').disabled=true;$('run').disabled=true;
    try{
      error();const result=await match();summary(result);
      if(run && result.status==='ready'){
        const job=await api('/api/dispatch/run',{ticket:result.ticket});
        matched.job_id=job.id;
        selected=job.id;current=null;lastRender='';lastHistory=0;
        attemptStatus('本次已提交仿真；后续以本次新基准和实测结果为准。');
        $('dispatch-summary').textContent=`已提交 ${job.job_ids?.length||1} 组实验；先计算新基准，再依据实测结果推进，模型请求受总上限约束。`;
        // The worker creates its job directory asynchronously; normal polling follows.
        showPane('planning');await records();
      }
    }catch(e){
      if(/任务卡.*(过期|不存在)|源场景.*变化/.test(e.message)){matched=null;matchedInput='';}
      previewFailure=true;displayedKey='';error(e.message);
      attemptStatus('本次规划失败；已停止追加请求。'+e.message);modeNote();
      $('dispatch-summary').textContent='本次规划或调度未完成；已停止追加请求。'+e.message;
      if(!matched){$('planner-result').innerHTML=`<div class="plan-error"><b>本次未启动仿真</b><p>${escapeHTML(e.message)}</p><p>本次请求没有产生新的交通结果；地图仍保留当前查看内容，请以图上的数据来源标注为准。</p></div>`;$('plan-expand').disabled=true;}
    }
    finally{busy=false;launching=false;$('preview').disabled=false;$('run').disabled=window.dispatchRunning||current?.state.status==='running';attemptRecords();}
  }
  $('task-form').addEventListener('submit',e=>{e.preventDefault();action(false);});
  $('run').addEventListener('click',()=>action(true));
  async function records(){
    try{
      const rows=await api('/api/dispatch');
      window.dispatchRunning=rows.some(r=>['starting','running'].includes(r.status));
      $('run').disabled=busy||launching||window.dispatchRunning||current?.state.status==='running';
      const active=rows.find(r=>r.job_id===selected||r.job_ids?.includes(selected));
      viewedMode=active?.matcher||(current?(current.state.programme_protocol?'model':'rules'):null);modeNote();
      if(!active)$('programme-status').hidden=true;
      if(active && !previewFailure && (!busy||matched?.job_id===active.job_id) && (!matched||matched.job_id===active.job_id)){viewedMode=active.matcher;modeNote();$('dispatch-summary').textContent=`${active.matcher==='model'?(active.experiments?.length?'模型规划':'历史模型分类'):'规则匹配'} · ${stateNames[active.status]||active.status}。${active.model_usage?`${active.model_budget?`模型已请求 ${active.model_budget.used}/${active.model_budget.limit} 次，已知 ${active.model_budget.known_tokens} tokens`:`历史初始规划 ${active.model_usage.tokens??'未知'} tokens`}。`:''}`;programme(active);}
      $('dispatch-records').innerHTML=rows.slice(0,3).map(r=>r.progress?.length?`<div class="programme-progress"><b>${escapeHTML(stateNames[r.status]||r.status)} · ${r.progress.filter(x=>['completed','no_candidate'].includes(x.status)).length}/${r.progress.length}组</b>${r.progress.map(p=>`<button class="quiet" data-dispatch-job="${escapeHTML(p.job_id)}" ${['queued','skipped'].includes(p.status)?'disabled':''}>${escapeHTML(stateNames[p.status]||p.status)} · ${escapeHTML(p.title)}<small>${escapeHTML(progressDetail(r,p))}</small></button>`).join('')}</div>`:`<button class="quiet" data-dispatch-job="${escapeHTML(r.job_id)}">${escapeHTML(stateNames[r.status]||r.status)} · ${escapeHTML(r.job_id)}<small>${r.matcher==='model'?'模型匹配':'规则匹配'} / TranStar</small></button>`).join('');
    }catch(e){$('dispatch-records').textContent='调度记录暂不可用';}
  }
  async function attemptRecords(){
    try{const rows=await api('/api/planning-attempts'),key=JSON.stringify(rows);if(key!==attemptsKey){attemptsKey=key;$('planning-attempts').innerHTML=window.trafficPlannerView.renderAttempts(rows);}
      if(!attemptsHydrated&&!busy){
        attemptsHydrated=true;
        const latest=rows[0];
        if(latest&&['model_failed','validation_failed'].includes(latest.status)){
          attemptStatus(`最近一次初始模型规划失败（${latest.id}）；未取得可执行规划。${latest.error||''}`);
          if(!$('request').value&&$('matcher').value==='model'){$('request').value=latest.request||'';drafts.model=$('request').value;}
        }
      }
    }
    catch(e){$('planning-attempts').textContent='初始规划记录暂不可用';attemptsKey='';}
  }
  $('dispatch-records').onclick=async e=>{const b=e.target.closest('[data-dispatch-job]');if(b){selected=b.dataset.dispatchJob;matched=null;matchedInput='';displayedKey='';previewFailure=false;attemptsHydrated=true;attemptStatus();error();try{await refresh();await poll();await records();}catch(e){error(e.message)}}};
  document.addEventListener('click',async e=>{
    if(e.target.closest('[data-job]')){previewFailure=false;matched=null;matchedInput='';displayedKey='';attemptsHydrated=true;attemptStatus();records();}
    const area=e.target.closest('[data-region-job]');
    if(area){
      try{selected=area.dataset.regionJob;matched=null;matchedInput='';previewFailure=false;attemptsHydrated=true;attemptStatus();current=null;lastRender='';await poll();await records();await window.trafficMap.selectRun(area.dataset.regionRun);window.trafficMap.focusIndices(area.dataset.regionIndices.split(',').map(Number));if($('plan-dialog').open)$('plan-dialog').close();showPane('situation');}catch(e){error(e.message);}
      return;
    }
    const b=e.target.closest('[data-observation-ref]');if(!b)return;
    const [job,run]=b.dataset.observationRef.split(':');
    try{selected=job;matched=null;matchedInput='';previewFailure=false;attemptsHydrated=true;attemptStatus();current=null;lastRender='';await poll();await records();window.trafficMap.selectRun(run);if($('plan-dialog').open)$('plan-dialog').close();showPane('situation');}catch(e){error(e.message);}
  });
  records();attemptRecords();setInterval(()=>{records();attemptRecords();},4000);
  window.addEventListener('traffic-job-selected',records);

  const planDialog=document.createElement('dialog');planDialog.id='plan-dialog';
  planDialog.innerHTML='<div class="dialog-heading"><h2>完整交通研究规划</h2><button id="plan-close" aria-label="关闭完整规划">×</button></div><div id="plan-document" class="dialog-content"></div>';
  document.body.append(planDialog);$('plan-expand').disabled=true;
  $('plan-expand').onclick=()=>{$('plan-document').innerHTML=$('planner-result').innerHTML;planDialog.showModal();};
  $('plan-close').onclick=()=>planDialog.close();

  const dialog=document.createElement('dialog');dialog.id='llm-dialog';
  dialog.innerHTML=`<div class="dialog-heading"><div><div class="eyebrow">MODEL CONNECTION</div><h2>API 设置</h2></div><button id="llm-close" aria-label="关闭 API 设置">×</button></div>
    <form id="llm-form" class="dialog-content">
    <p>密钥由本机当前 Windows 用户加密保存。保存不发起线上请求。密钥必须由所填服务签发；转发服务与模型官方服务的地址、密钥及模型权限需要分别匹配。</p>
    <label for="llm-url">API 地址</label><input id="llm-url" type="url" required autocomplete="off">
    <label for="llm-wire">接口类型</label><select id="llm-wire"><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option></select>
    <label for="llm-model">模型</label><input id="llm-model" required autocomplete="off">
    <div class="pair"><div><label for="llm-timeout">超时 / 秒</label><input id="llm-timeout" type="number" min="5" max="120" required></div><div><label for="llm-tokens">输出上限 / tokens</label><input id="llm-tokens" type="number" min="256" max="4096" required></div></div>
    <label for="llm-key-action">密钥操作</label><select id="llm-key-action"><option value="keep">保留已有密钥</option><option value="replace">填写 / 更换密钥</option><option value="clear">清除本机密钥</option></select>
    <label for="llm-key">新密钥</label><input id="llm-key" type="password" autocomplete="new-password" disabled placeholder="不回显已保存密钥">
    <p id="llm-key-status" class="hint"></p><div class="run-actions"><button id="llm-save" type="submit" class="primary">保存设置</button><button id="llm-test" type="button" class="secondary">测试已保存配置</button></div>
    <button id="llm-reset" type="button" class="secondary" hidden>备份损坏配置并重置</button>
    <p class="hint">手动测试会消耗少量额度：Chat 为1次工具探针，Responses 为2次往返。不会自动测试或自动重试。</p><p id="llm-result" role="status"></p></form>`;
  document.body.append(dialog);
  const settingFields=[['url','base_url'],['wire','wire_api'],['model','model'],['timeout','timeout_s'],['tokens','max_output_tokens']];
  let savedSettings=null,settingsBusy=false;
  function settingsInput(){return {base_url:$('llm-url').value.trim(),wire_api:$('llm-wire').value,
    model:$('llm-model').value.trim(),timeout_s:Number($('llm-timeout').value),max_output_tokens:Number($('llm-tokens').value)};}
  function settingsPending(){const input=settingsInput();return !savedSettings||settingFields.some(([,key])=>input[key]!==savedSettings[key])||$('llm-key-action').value!=='keep'||Boolean($('llm-key').value);}
  function settingsLock(value){
    settingsBusy=value;
    for(const id of ['save','test','reset','key-action',...settingFields.map(([id])=>id)])$('llm-'+id).disabled=value;
    $('llm-key').disabled=value||$('llm-key-action').value!=='replace';
  }
  $('llm-settings-open').onclick=async()=>{
    if(settingsBusy)return;
    settingsLock(true);
    try{
      const c=await api('/api/settings/llm');
      savedSettings=c;
      for(const [id,key] of settingFields)$('llm-'+id).value=c[key];
      $('llm-key').value='';$('llm-key').disabled=true;$('llm-key-action').value='keep';$('llm-result').textContent='';
      $('llm-key-status').textContent=c.key_configured?'本机已有加密密钥':'尚未配置密钥';dialog.showModal();
      $('llm-reset').hidden=!c.settings_corrupt;
      $('llm-result').textContent=c.settings_corrupt?'本机配置损坏或不可读。请点击“备份损坏配置并重置”，保留原文件后恢复默认配置并清除凭据，再重新保存服务和密钥。重置不发起模型请求。':`当前已保存：${c.base_url} · ${c.model} · ${c.wire_api}；配置尚未在本次设置窗口测试。`;
    }catch(e){error(e.message)}finally{settingsLock(false);}
  };
  function clearInput(){$('llm-key').value='';}
  $('llm-close').onclick=()=>dialog.close();dialog.addEventListener('close',clearInput);
  $('llm-key-action').onchange=()=>{$('llm-key').disabled=$('llm-key-action').value!=='replace';clearInput();};
  $('llm-form').onsubmit=async e=>{
    e.preventDefault();if(settingsBusy)return;
    if(savedSettings?.settings_corrupt){$('llm-result').textContent='配置损坏，请先备份损坏配置并重置；未保存或发起模型请求。';return;}
    settingsLock(true);
    try{
      const body={config:settingsInput(),key_action:$('llm-key-action').value};
      if(body.key_action==='replace')body.api_key=$('llm-key').value.trim();
      const response=await fetch('/api/settings/llm',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      delete body.api_key;clearInput();const result=await response.json();if(!response.ok)throw Error(result.error);
      savedSettings=result;
      for(const [id,key] of settingFields)$('llm-'+id).value=result[key];
      matched=null;matchedInput='';$('llm-key-action').value='keep';$('llm-key').disabled=true;
      $('llm-key-status').textContent=result.key_configured?'本机已有加密密钥':'密钥已清除';$('llm-result').textContent='设置已保存；没有发起模型请求。';
    }catch(e){clearInput();$('llm-result').textContent=e.message;}finally{settingsLock(false);}
  };
  $('llm-test').onclick=async()=>{
    if(settingsBusy)return;
    if(savedSettings?.settings_corrupt){$('llm-result').textContent='配置损坏，请先备份损坏配置并重置；未发起模型请求。';return;}
    if(settingsPending()){$('llm-result').textContent='配置存在未保存的修改，请先保存再测试；未发起模型请求。';return;}
    settingsLock(true);$('llm-result').textContent=`正在测试已保存配置：${savedSettings.base_url} · ${savedSettings.model} · ${savedSettings.wire_api}…`;
    try{const result=await api('/api/settings/llm/test',{});const unknown=result.requests.some(r=>r.tokens==null);$('llm-result').textContent=`连接与工具协议通过；${result.requests.length} 次请求，已知 ${result.requests.reduce((n,r)=>n+(r.tokens??0),0)} tokens${unknown?'，部分用量未知':''}。`;}
    catch(e){$('llm-result').textContent=e.message;}finally{settingsLock(false);}
  };
  $('llm-reset').onclick=async()=>{
    if(settingsBusy||!savedSettings?.settings_corrupt)return;
    settingsLock(true);
    try{
      const result=await api('/api/settings/llm/reset',{});savedSettings=result;
      for(const [id,key] of settingFields)$('llm-'+id).value=result[key];
      clearInput();$('llm-key-action').value='keep';$('llm-reset').hidden=true;
      matched=null;matchedInput='';$('llm-key-status').textContent='尚未配置密钥';
      $('llm-result').textContent=`原文件已在本机备份为 ${result.backup_name}；配置已重置，请重新填写并保存服务与密钥。没有发起模型请求。`;
    }catch(e){$('llm-result').textContent=e.message;}finally{settingsLock(false);}
  };
})();
