// Regression: background history polling must not turn a failed new request
// into an old experiment's successful result. No network/model/native calls.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const elements=new Map(),events={},documentEvents={},timers=[];
const $=id=>{
  if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',textContent:'',disabled:false,hidden:false,
    dataset:{},handlers:{},addEventListener(type,fn){this.handlers[type]=fn},querySelectorAll(){return []},showModal(){},close(){}});
  return elements.get(id);
};
$('matcher').value='model';
let previewCalls=0;
const row={job_id:'old_job',matcher:'model',status:'completed',experiments:[{runs:4},{runs:4}],
  progress:[{status:'completed',completed_run_count:4},{status:'completed',completed_run_count:4}],programme_review:{complete:true},model_usage:{tokens:10}};
const context={$,document:{body:{classList:{toggle(){}},append(){}},createElement(){return $('dialog')},addEventListener(name,fn){documentEvents[name]=fn}},
  window:{trafficPlannerView:{render:()=>'<b>OLD_COMPLETED_PLAN</b>',renderAttempts:()=>'',
    programmeStatus:require('../traffic_agent/static/planner_view.js').programmeStatus},addEventListener:(name,fn)=>events[name]=fn},
  setInterval:fn=>timers.push(fn),selected:'old_job',current:{state:{status:'completed'}},launching:false,
  lastRender:'',lastHistory:0,payload:()=>({}),showPane(){},showPlan(){},error(){},escapeHTML:x=>String(x),
  refresh:async()=>{},poll:async()=>{},api:async path=>{
    if(path==='/api/dispatch')return [row];
    if(path==='/api/planning-attempts')return [];
    if(path==='/api/dispatch/preview'){previewCalls++;throw Error('模型返回占位内容');}
    throw Error('Unexpected request: '+path);
  }};
vm.createContext(context);
vm.runInContext(fs.readFileSync('traffic_agent/static/dispatch.js','utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await tick();
  assert($('planner-result').innerHTML.includes('OLD_COMPLETED_PLAN'));
  assert($('programme-status').innerHTML.includes('整个任务已完成'));
  assert(!$('programme-status').hidden);
  $('matcher').value='rules';$('matcher').onchange();
  assert($('view-mode-note').textContent.includes('当前输入：规则模式；右侧查看：大模型任务'));
  $('matcher').value='model';$('matcher').onchange();
  $('request').value='新规划';
  await $('run').handlers.click();
  assert($('planner-result').innerHTML.includes('本次未启动仿真'));
  assert($('plan-expand').disabled);
  assert(!$('attempt-status').hidden);
  assert($('attempt-status').textContent.includes('本次规划失败'));
  assert($('view-mode-note').textContent.includes('old_job'));
  assert($('view-mode-note').textContent.includes('另一个实验'));
  for(let i=0;i<3;i++){timers.forEach(fn=>fn());await tick();}
  await events['traffic-job-selected'](); // Delayed initial map load, not user selection.
  assert($('planner-result').innerHTML.includes('本次未启动仿真'));
  assert(!$('dispatch-summary').textContent.includes('已完成'));
  assert($('programme-status').hidden);
  assert(!$('programme-status').innerHTML.includes('已完成'));
  assert.equal(previewCalls,1);
  await $('dispatch-records').onclick({target:{closest:()=>({dataset:{dispatchJob:'old_job'}})}});
  assert($('attempt-status').hidden);
  assert($('planner-result').innerHTML.includes('OLD_COMPLETED_PLAN'));
  assert.equal(previewCalls,1);
  console.log('Failed new planning stays visible across polling and delayed map loading; explicit history selection restores old result without a model request');
  assert(!$('programme-status').hidden);
  assert($('programme-status').innerHTML.includes('整个任务已完成'));
  context.api=async path=>path==='/api/dispatch/preview'?{status:'needs_input',matcher:'model',reason:'修正后预算不足',missing:['需要缩小范围'],
    initial_model_usages:[{tokens:null},{tokens:120}],model_usage:{tokens:120}}:[];
  $('request').value='新的本地修正样例';
  await $('run').handlers.click();
  assert($('dispatch-summary').textContent.includes('初始规划 2 次请求，已知 120 tokens，部分用量未知'));
  assert($('dispatch-summary').textContent.includes('已计入总预算'));
  assert(!$('dispatch-summary').textContent.includes('未自动重试'));
  assert($('mode-note').textContent.includes('初始规划至多修正1次'));
  assert($('mode-note').textContent.includes('交接与最终复核共享1次修正'));
  assert($('mode-note').textContent.includes('预留剩余研究预算'));
  console.log('Initial correction cost includes failed and unknown-usage requests');
  const mapActions=[];
  context.window.trafficMap={selectRun:async run=>mapActions.push(['run',run]),focusIndices:indices=>mapActions.push(['focus',...indices])};
  await documentEvents.click({target:{closest:selector=>selector==='[data-region-job]'?
    {dataset:{regionJob:'chosen_job',regionRun:'baseline',regionIndices:'3,2'}}:null}});
  assert.equal(context.selected,'chosen_job');assert.deepEqual(mapActions,[['run','baseline'],['focus',3,2]]);
  assert.equal(previewCalls,1);
  console.log('Selection observation links switch to the cited job/run and focus its directions without a model call');
  context.current={state:{id:'chosen_job',task:{scenario:'parking'},status:'completed'}};
  context.api=async()=>[];
  await events['traffic-job-selected']();
  assert($('view-mode-note').textContent.includes('规则任务'));
  assert($('view-mode-note').textContent.includes('chosen_job'));
  console.log('Direct rule jobs retain their actual source even without a dispatch record');
  const failedAttempt={id:'preview_failed',status:'model_failed',request:'原始问题保持不变',error:'HTTP 401',requested:true,usage:null};
  context.api=async path=>path==='/api/planning-attempts'?[failedAttempt]:[];
  $('request').value='';
  vm.runInContext(fs.readFileSync('traffic_agent/static/dispatch.js','utf8'),context);
  await tick();
  assert.equal($('request').value,'原始问题保持不变');
  assert(!$('attempt-status').hidden);
  assert($('attempt-status').textContent.includes('preview_failed'));
  assert($('attempt-status').textContent.includes('未取得可执行规划'));
  assert.equal(previewCalls,1);
  console.log('Reload restores the last failed initial attempt and original question without a paid request');
  const probePaths=[];
  context.api=async path=>{
    probePaths.push(path);
    if(path==='/api/settings/llm')return {base_url:'https://saved.example/v1',model:'saved-model',wire_api:'chat_completions',timeout_s:45,max_output_tokens:1600,key_configured:true};
    if(path==='/api/settings/llm/test')return {ok:true,requests:[{tokens:null},{tokens:12}]};
    throw Error('Unexpected settings request: '+path);
  };
  await $('llm-settings-open').onclick();
  assert($('llm-result').textContent.includes('https://saved.example/v1'));
  assert($('llm-result').textContent.includes('saved-model'));
  assert.deepEqual(probePaths,['/api/settings/llm']);
  await $('llm-test').onclick();
  assert($('llm-result').textContent.includes('已知 12 tokens'));
  assert($('llm-result').textContent.includes('部分用量未知'));
  assert.equal(probePaths.filter(p=>p.endsWith('/test')).length,1);
  console.log('Opening settings is read-only; explicit probe keeps unavailable token counts unknown');
  const initialProbeCount=probePaths.filter(p=>p.endsWith('/test')).length;
  for(const [id,value] of [['url','https://unsaved.example/v1'],['model','unsaved-model'],['wire','responses'],
    ['timeout',60],['tokens',2048],['key-action','replace']]){
    const old=$('llm-'+id).value;$('llm-'+id).value=value;
    await $('llm-test').onclick();
    assert($('llm-result').textContent.includes('先保存'));
    assert.equal(probePaths.filter(p=>p.endsWith('/test')).length,initialProbeCount);
    $('llm-'+id).value=old;
  }
  console.log('Unsaved service/model/protocol/budget/key changes cannot probe an old configuration');
  let releaseProbe,heldProbeCount=0;
  context.api=async path=>{
    assert.equal(path,'/api/settings/llm/test');heldProbeCount++;
    return new Promise(resolve=>releaseProbe=resolve);
  };
  const firstProbe=$('llm-test').onclick();
  await tick();
  assert($('llm-save').disabled);
  assert($('llm-model').disabled);
  await $('llm-test').onclick();
  await $('llm-form').onsubmit({preventDefault(){}});
  assert.equal(heldProbeCount,1);
  releaseProbe({ok:true,requests:[{tokens:9}]});await firstProbe;
  assert(!$('llm-test').disabled);assert(!$('llm-save').disabled);assert(!$('llm-model').disabled);
  console.log('In-flight probe blocks duplicate probes and saves, then restores controls');
  let releaseSave,saveCount=0;
  const saved={base_url:'https://saved.example/v1',model:'new-saved-model',wire_api:'chat_completions',timeout_s:45,max_output_tokens:1600,key_configured:true};
  context.fetch=async(path,options)=>{
    assert.equal(path,'/api/settings/llm');assert.equal(options.method,'PUT');saveCount++;
    assert.equal(JSON.parse(options.body).config.model,'new-saved-model');
    assert.equal(JSON.parse(options.body).config.base_url,'https://saved.example/v1/');
    return new Promise(resolve=>releaseSave=()=>resolve({ok:true,json:async()=>saved}));
  };
  $('llm-model').value='new-saved-model';
  $('llm-url').value='https://saved.example/v1/';
  const saving=$('llm-form').onsubmit({preventDefault(){}});await tick();
  await $('llm-form').onsubmit({preventDefault(){}});
  await $('llm-test').onclick();
  assert.equal(saveCount,1);assert.equal(heldProbeCount,1);
  releaseSave();await saving;
  assert.equal($('llm-url').value,saved.base_url);
  context.api=async path=>{assert.equal(path,'/api/settings/llm/test');heldProbeCount++;return {ok:true,requests:[{tokens:4}]};};
  await $('llm-test').onclick();assert.equal(heldProbeCount,2);
  assert(!$('llm-save').disabled);assert(!$('llm-key-action').disabled);assert($('llm-key').disabled);
  console.log('Save blocks overlapping paid actions and refreshes the saved snapshot before probing');
  let releaseSettingsRead,settingsReadCount=0;
  context.api=async path=>{
    assert.equal(path,'/api/settings/llm');settingsReadCount++;
    return new Promise(resolve=>releaseSettingsRead=resolve);
  };
  const opening=$('llm-settings-open').onclick();await tick();
  const reopening=$('llm-settings-open').onclick();await tick();
  assert.equal(settingsReadCount,1);
  await $('llm-test').onclick();
  await $('llm-form').onsubmit({preventDefault(){}});
  assert.equal(settingsReadCount,1);assert.equal(saveCount,1);
  assert($('llm-save').disabled);
  releaseSettingsRead(saved);await opening;await reopening;
  assert(!$('llm-save').disabled);assert(!$('llm-test').disabled);
  console.log('Pending settings read serializes reopening, saves and probes without stale overwrites');
  const recoveryPaths=[];
  context.api=async(path,body)=>{
    recoveryPaths.push(path);
    if(path==='/api/settings/llm')return {...saved,key_configured:false,settings_corrupt:true};
    if(path==='/api/settings/llm/reset'){assert.deepEqual(Object.keys(body),[]);return {...saved,key_configured:false,settings_corrupt:false,backup_name:'llm.corrupt-offline.json'};}
    throw Error('Recovery must not call a model');
  };
  await $('llm-settings-open').onclick();
  assert(!$('llm-reset').hidden);
  assert($('llm-result').textContent.includes('损坏'));
  await $('llm-test').onclick();
  await $('llm-form').onsubmit({preventDefault(){}});
  assert.equal(recoveryPaths.length,1);assert.equal(saveCount,1);
  await $('llm-reset').onclick();
  assert($('llm-reset').hidden);assert.equal($('llm-key').value,'');
  assert($('llm-result').textContent.includes('备份'));
  assert.deepEqual(recoveryPaths,['/api/settings/llm','/api/settings/llm/reset']);
  console.log('Corrupt settings offer explicit backup/reset; reads, failed save/test and reset never contact a model');
})().catch(e=>{console.error(e);process.exitCode=1});
