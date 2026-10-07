const assert = require('node:assert/strict');
const {render} = require('../traffic_agent/static/planner_view.js');
const sample = {
  version:'natural-strategy-plan-v2',goal:'<img src=x onerror=alert(1)>',reason:'待验证',budget:15,allocated_runs:13,
  assessment:[{hypothesis:'停车干扰',evidence_needed:'新基准'}],
  alternatives:[{approach:'稳健搜索',tradeoff:'预留完整需求',decision:'selected'}],
  experiments:[{title:'稳健搜索',task:{road_name:'测试路'},runs:13,target_method:'诊断',rationale:'比较',
    feedback_reserve:6,design:{matrix:['基准','治理'],factors:[.9,1,1.1],stages:[{title:'实际观测',detail:'读取新仿真'}],criteria:['核心收益'],recovery:'保留基准',stop:'失败停止'}}],
  progress:[{status:'no_candidate',run_count:1,runs:13,review:{outcome:'无候选',completed_runs:1,rejected_count:0,rejection_reasons:[],observations:[]}}]
};
const html=render(sample);
const retrievedHtml=render({...sample,knowledge_retrieval:{version:'curated-knowledge-rag-v1',hits:[
  {id:'parking.categories',text:'停车类别仅是情景参数<script>',source:'手册第31页<img>'}]}});
for(const phrase of ['本地知识检索依据','【K:parking.categories】','停车类别仅是情景参数&lt;script&gt;',
  '手册第31页&lt;img&gt;','治理效果仍以本次原生仿真为准'])assert(retrievedHtml.includes(phrase),phrase);
assert(!retrievedHtml.includes('<script>'));assert(!retrievedHtml.includes('<img>'));
const executionTrace={ordinal:1,status:'accepted',kind:'oneway_target',job_id:'typed',action:'oneway_1_2',
  selected:{title:'单向完整对照'},reason:'按观测选择此位置',next_check:'停车2级 → 单向 → 组合；尚无新效果',next_check_origin:'action-execution-v1',
  input:{observations:[],evidence_refs:[],model_budget:{remaining:2},context:{action_execution:{version:'action-execution-v1',
    native_budget:{current_study:{limit:4,used:1,completed:1,remaining:3},programme_allocated:{limit:7,used:4,completed:4,remaining:3}},
    actions:{oneway_1_2:{kind:'factorial_target',closed_direction:[1,2],retained_direction:[2,1],sequence:[
      {name:'S1_parking',policy:'停车',parking_level:2,demand_factor:1},
      {name:'S2_oneway',policy:'单向<script>',parking_level:null,demand_factor:1},
      {name:'S3_combined',policy:'停车＋单向',parking_level:2,demand_factor:1}]}}}}}};
const executionHtml=render({...sample,planning_trace:[executionTrace]});
for(const phrase of ['实际安排 · 由所选动作定义生成','模型请求剩余 2 次（含本次）','原生场景另计','已用 1 / 上限 4','已用 4 / 上限 7','查看安排顺序 · 3 个原生场景','拟禁行 1→2 · 保留 2→1','单向&lt;script&gt;'])assert(executionHtml.includes(phrase),phrase);
assert(!executionHtml.includes('<script>'));
assert(!executionHtml.includes('接下来核对'));
const summaryTrace=structuredClone(executionTrace);
summaryTrace.input.context.action_execution.version='action-execution-v2';
summaryTrace.summary='选择待执行方向1→2 <script>；新效果未知';
summaryTrace.summary_origin='action-execution-v2';summaryTrace.next_check_origin='action-execution-v2';
const summaryHtml=render({...sample,planning_trace:[summaryTrace]});
assert(summaryHtml.includes('所选动作 · 由执行定义生成'));
assert(summaryHtml.includes('选择待执行方向1→2 &lt;script&gt;；新效果未知'));
assert(summaryHtml.includes('查看安排顺序 · 3 个原生场景'));
assert(!summaryHtml.includes('读到的结果'));
assert(!summaryHtml.includes('<script>'));
const rejectedExecution=structuredClone(summaryTrace);
rejectedExecution.status='rejected';rejectedExecution.action='stop';
rejectedExecution.proposed_execution={version:'action-execution-v2',action:'oneway_1_2',summary:rejectedExecution.summary,next_check:rejectedExecution.next_check};
for(const field of ['summary','summary_origin','next_check','next_check_origin'])delete rejectedExecution[field];
const rejectedExecutionHtml=render({...sample,planning_trace:[rejectedExecution]});
assert(rejectedExecutionHtml.includes('被拒动作提议 · 未执行'));
assert(rejectedExecutionHtml.includes('选择待执行方向1→2 &lt;script&gt;；新效果未知'));
assert(!rejectedExecutionHtml.includes('所选动作 · 由执行定义生成'));
const legacyNext=render({...sample,planning_trace:[{...executionTrace,next_check_origin:undefined,input:{observations:[],evidence_refs:[],context:{}}}]});
assert(legacyNext.includes('接下来核对'));
assert(!legacyNext.includes('实际执行定义'));
console.log('Typed next actions keep request and native budgets separate, expose actual order and distinguish generated text from legacy model prose');
assert(render({...sample,version:'natural-strategy-plan-v8'}).includes('基于实测的智能规划'));
assert(render({...sample,version:'natural-strategy-plan-v9'}).includes('基于实测的智能规划'));
const evaluation={version:'hypothesis-measurement-v1',test:'core_time_decrease',policies:'parking',quantifier:'any',rule:'冻结核心耗时低于基准，与推荐资格分开判断。',scope:[{study_id:'study_1',road:'离线甲路<script>',demand_factors:[1]}]};
const typedHypothesis={id:'H1',hypothesis:'至少一项措施降低核心耗时',evidence_needed:'同需求配对',evaluation};
const typedFinding={id:'hypothesis.H1',hypothesis_id:'H1',question:typedHypothesis.hypothesis,evaluation,status:'supported',expected_judgment:'supported',answer:'核心有收益但未通过推荐约束。',evidence_refs:['synthetic:baseline','synthetic:parking'],measurements:[{study_id:'study_1',road:'离线甲路<script>',passed:true,policies:[{runs:['parking'],passed:true,cases:[{passed:true,demand_factor:1,values:{baseline_pcu_h:100,candidate_pcu_h:46,core_time_change_pct:-54},evidence_refs:['synthetic:baseline','synthetic:parking']}]}]}]};
const typedHtml=render({...sample,assessment:[typedHypothesis],progress:[{job_id:'synthetic',review:{research_review:{evidence:{findings:[typedFinding]},hypotheses:[typedHypothesis],updates:[],reviewed:false}}}]});
for(const text of ['原问题是否得到回答','事前判据','配对检验：满足事前判据','模型复核：尚未提交或未通过','46.000 pcu·h','-54.000%','同一措施须覆盖全部约定需求','data-observation-ref="synthetic:parking"','离线甲路&lt;script&gt;'])assert(typedHtml.includes(text),text);
assert(!typedHtml.includes('<script>'));
const unknownHtml=render({...sample,assessment:[typedHypothesis],progress:[{job_id:'synthetic',review:{research_review:{evidence:{findings:[{...typedFinding,expected_judgment:'unresolved',status:'unmeasured',measurements:[{road:'未知区域',passed:null,gap:'证据缺口<script>',policies:[]}]}]},hypotheses:[typedHypothesis],updates:[]}}}]});
assert(unknownHtml.includes('配对检验：证据仍不足'));
assert(unknownHtml.includes('证据缺口&lt;script&gt;'));
const runningCriterion=render({...sample,assessment:[typedHypothesis],planning_trace:[{status:'awaiting_model',kind:'parking',ordinal:1,job_id:'synthetic',input:{observations:[],evidence_refs:[],context:{programme_evidence:{groups:[],findings:[{...typedFinding,measurements:undefined}]}}}}]});
assert(runningCriterion.includes('最近一次模型决策所读的观测'));
assert(runningCriterion.includes('配对检验：满足事前判据'));
assert(runningCriterion.includes('尚未形成最终总复核'));
assert(!html.includes('<img'));
for(const text of ['&lt;img','问题判断','策略比较','执行路线','实际观测','方案必须满足','反馈预留 6','无候选','本组实测反馈','失败停止']) assert(html.includes(text),text);
const blocked=render({...sample,experiments:[],missing:['预算不足']});
assert(blocked.includes('本次没有启动仿真'));
assert(!blocked.includes('strategy-step'));
const depth=render({...sample,version:'natural-strategy-plan-v7',research_depth:{version:'research-depth-v1',intent:'compare_candidates',minimum_candidates:2,demand_validation:true,comparison_quote:'比较两个停车方案<script>',demand_quote:'检验需求变化'},planning_trace:[{input:{context:{completion_guard:{required_candidate_comparisons:2,measured_candidate_comparisons:1}}}}]});
for(const value of ['本次研究深度','至少实测 2 个','本组已测齐 1 个','每个约定需求条件','比较两个停车方案&lt;script&gt;'])assert(depth.includes(value),value);
assert(!depth.includes('<script>'));
const currentDepth=render({...sample,version:'natural-strategy-plan-v7',research_depth:{version:'research-depth-v3',intent:'compare_candidates',candidate_domain:'parking',minimum_candidates:2,demand_validation:false,comparison_quote:'比较两个停车方案',demand_quote:''}});
assert(currentDepth.includes('至少实测 2 个不同停车方案'));
const previousDepth=render({...sample,version:'natural-strategy-plan-v7',research_depth:{version:'research-depth-v2',intent:'demand_robustness',minimum_candidates:1,demand_validation:true,demand_quote:'需求系数0.9和1.0'}});
assert(previousDepth.includes('每个约定需求条件都要有自己的基准与治理配对'));
assert.equal(render({goal:'旧分类',experiments:null}),'');
const live=render({...sample,version:'natural-strategy-plan-v3',model_budget:{used:3,limit:6,known_tokens:500},
  planning_trace:[{ordinal:2,status:'rejected',kind:'parking',reason:'<script>bad()</script>',
    arguments_diagnostic:{version:'tool-arguments-diagnostic-v1',kind:'json_syntax',length:18,offset:9},input:{evidence_refs:['new:baseline']}}]});
for(const text of ['基于实测的智能规划','模型请求 3 / 6','调用失败 · 已停止','查看 baseline','&lt;script&gt;',
  'JSON语法错误 · 18字符 · 错误位置9；原始内容未保存'])assert(live.includes(text),text);
assert(!live.includes('<script>'));
const haltedPulse=render({...sample,job_id:'programme',status:'incomplete',active_job:null,
  progress:[{job_id:'parking',status:'completed',completed_run_count:3,study_comparison:{factors:[{policies:[{run:'parking_1',status:'admissible'},{run:'parking_2',status:'admissible'}],combination_contrasts:[]}]}},
    {job_id:'oneway',title:'单向研究',status:'incomplete',completed_run_count:1,study_comparison:{factors:[{policies:[],combination_contrasts:[]}]}}],
  planning_trace:[{job_id:'oneway',status:'rejected',action:'stop',proposal_excerpt:{action:'oneway_3237_3236'},input:{allowed_actions:{oneway_3237_3236:{title:'拟禁行3237→3236'}}},
    validation:{message:'方向说明冲突<script>'},next_check:'继续执行未测组合'}]});
for(const text of ['当前动作与实际进度','拟禁行3237→3236，校验未通过，未执行','4 个场景完成；2 个治理场景已有配对','为什么停止','方向说明冲突&lt;script&gt;'])assert(haltedPulse.includes(text),text);
assert(!haltedPulse.match(/<section class="current-pulse"[\s\S]*?<\/section>/)[0].includes('继续执行未测组合'));
assert(!haltedPulse.includes('<script>'));
const malformedPulse=render({...sample,job_id:'programme',status:'incomplete',active_job:'oneway',
  progress:[{job_id:'oneway',status:'incomplete',completed_run_count:1}],
  planning_trace:[{job_id:'oneway',status:'rejected',action:'stop',validation:{code:'arguments_json',message:'工具参数无效'}}]});
assert(malformedPulse.includes('模型回复未形成可执行动作，未执行治理'));
assert(!malformedPulse.includes('提议 stop'));
const formatTurn={job_id:'parking',ordinal:2,status:'awaiting_model',kind:'parking',
  input:{context:{},model_budget:{remaining:3},evidence_refs:['new:baseline']},
  review_correction:{version:'parking-arguments-correction-v1',
    first_attempt:{status:'rejected',usage:{tokens:30},validation:{message:'工具参数无法解析<script>'},
      arguments_diagnostic:{version:'tool-arguments-diagnostic-v1',kind:'json_syntax',length:18,offset:18}}}};
const formatCorrection=render({...sample,status:'running',job_id:'programme',active_job:'parking',
  progress:[{job_id:'parking',status:'running',completed_run_count:2}],planning_trace:[formatTurn]});
for(const text of ['工具格式修正','正在修正工具格式','首次仅保存解析诊断','首次请求未通过',
  '两次请求均计入原总预算','回复与用量待返回','1次用量尚未知','&lt;script&gt;'])assert(formatCorrection.includes(text),text);
assert(!formatCorrection.includes('原生对照已齐，使用同一份观测重新复核'));
assert(!formatCorrection.includes('正在修正最终复核'));
const choiceCorrection=render({...sample,planning_trace:[{...formatTurn,
  review_correction:{...formatTurn.review_correction,version:'candidate-choice-correction-v1'}}]});
assert(choiceCorrection.includes('选点引用修正'));
assert(choiceCorrection.includes('保留原选动作与备选'));
const nextGroupPulse=render({...sample,job_id:'programme',status:'running',active_job:'oneway',
  progress:[{job_id:'parking',status:'completed',completed_run_count:3},{job_id:'oneway',title:'单向新基准',status:'running',completed_run_count:0}],
  planning_trace:[{job_id:'parking',status:'accepted',action:'parking_1',selected:{title:'上组停车候选'},input:{}}]});
assert(nextGroupPulse.includes('正在计算 单向新基准'));
assert(!nextGroupPulse.includes('已选定 上组停车候选'));
const finishedPulse=render({...sample,job_id:'programme',status:'completed',progress:[{job_id:'parking',status:'completed',completed_run_count:2,
  review:{outcome:'按已测条件形成建议'},study_comparison:{factors:[{policies:[{status:'unmeasured'}],combination_contrasts:[]}]}}],
  planning_trace:[{job_id:'parking',status:'accepted',action:'stop',stop_basis:'完成必需比较后停止'}]});
assert(finishedPulse.includes('结束依据</b>完成必需比较后停止'));
assert(finishedPulse.includes('尚无治理配对'));
assert(!finishedPulse.includes('必需对照或复核尚未完成'));
const stop=render({...sample,version:'natural-strategy-plan-v7',model_budget:{used:5,limit:6,known_tokens:62495},
  planning_trace:[{ordinal:3,job_id:'synthetic',status:'accepted',kind:'parking',action:'stop',
    selected:{title:'完成结果复核'},reason:'required_candidate_comparisons=2已测齐<script>',
    stop_basis:'本组已完成2个不同候选；未测等级不算已验证。'}]});
assert(stop.includes('停止依据'));
assert(stop.includes('本组已完成2个不同候选'));
assert(stop.includes('查看模型原始理由'));
assert(stop.includes('required_candidate_comparisons=2已测齐&lt;script&gt;'));
assert(!stop.includes('<script>'));
console.log('Planner rendering, negative evidence and escaping passed');
const complete=render({...sample,version:'natural-strategy-plan-v4',progress:[{...sample.progress[0],status:'completed',title:'强度研究',job_id:'new',review:{...sample.progress[0].review,goal_review:{complete:true,
  requirements:[{label:'同方向停车1级 / 2级完整对照',fulfilled:true},{label:'完整模型复核',fulfilled:true}],strength_matrix:[{demand_factor:1,levels:[{level:1,run:'parking_1',speed_kmh:23.38,comparison:{delta:{neighbor:{running_time_change_pct:.018}}}},{level:2,run:'parking_2',speed_kmh:18}]}]}}}]});
for(const text of ['研究目标已完成','2 / 2','23.38','18.00','0.018%','同方向对照','data-observation-ref="new:parking_2"'])assert(complete.includes(text),text);
const unfinished=render({...sample,progress:[{status:'incomplete',run_count:2,review:{goal_review:{complete:false,unanswered:['未完成同方向比较']}}}]});
assert(unfinished.includes('研究未完成'));
assert(!unfinished.includes('研究目标已完成'));
console.log('Completion requirements and measured strength comparison passed');
const spatial=render({...sample,version:'natural-strategy-plan-v5',planning_trace:[{job_id:'fresh',ordinal:1,status:'accepted',kind:'parking',input:{observations:[{run:'trial',demand_factor:1,spatial:{inspected_links:32060,overloaded_links:2,
  regions:[{id:'area_2',title:'乙路<script>',indices:[1],roads:[{}],weighted_speed_kmh:9}],outside_hotspots:[{name:'其他路',from_node:90,to_node:91,speed_kmh:15,vc:1.5}]},regional_checks:[{region:'area_2',time_change_pct:11.1,passed:false}]}]}}]});
for(const text of ['各候选区域','32060','区域代价超限','11.100%','乙路&lt;script&gt;','data-region-indices="1"','不代表已穷举','其他路'])assert(spatial.includes(text),text);
assert(!spatial.includes('<script>'));
console.log('Regional observations, rejection evidence and map targets passed');
const regionalTrial=render({...sample,version:'natural-strategy-plan-v5',planning_trace:[{job_id:'fresh',input:{observations:[{
  run:'parking_1',demand_factor:1,changes:[{from_node:1,to_node:2,parking:2}],eligible:false,violations:['乙区恶化'],
  delta:{core:{running_time_change_pct:-10},neighbor:{running_time_change_pct:0.8}},
  spatial:{inspected_links:3,overloaded_links:2,outside_hotspots:[],regions:[{id:'area_1',title:'甲路',indices:[0],roads:[{from_node:1,to_node:2}],weighted_speed_kmh:25},{id:'area_2',title:'乙路',indices:[1],roads:[{from_node:20,to_node:21}],weighted_speed_kmh:9}]},
  regional_checks:[{region:'area_1',time_change_pct:-30,passed:true},{region:'area_2',time_change_pct:11.1,passed:false}]
}]}}]});
for(const text of ['区域选择与实测结果','候选区并集耗时 -10.000%','区域最大耗时增幅 +11.100%（乙路）','乙区恶化','data-region-run="parking_1"','data-region-indices="0"'])assert(regionalTrial.includes(text),text);
console.log('Cross-area comparison and actual selected map run passed');
const findings=render({...sample,planning_trace:[],progress:[{job_id:'new',review:{research_review:{reviewed:true,
  hypotheses:[{id:'H1',hypothesis:'更强措施是否更好<script>'}],updates:[{hypothesis_id:'H1',judgment:'contradicted',finding_ids:['strength'],explanation:'强措施导致邻区损失'}],
  evidence:{findings:[{id:'strength',question:'停车1级是否更合适？',status:'measured',answer:'固定约束优先于核心速度',evidence_refs:['new:strong','new:weak'],measurements:[{preferred_level:2,levels:[{level:1,feasible:false},{level:2,feasible:true,worst:[-.05,0,0]}]}]},
    {id:'robustness',question:'需求波动是否仍有效？',status:'unmeasured',answer:'单需求不能证明稳健',evidence_refs:[]}]}}}}]});
for(const text of ['假设如何经受实测检验','优先2级','1级：未通过约束','2级：最差全网耗时 -0.050%','模型判断：不支持','单需求不能证明稳健','证据不足','&lt;script&gt;','data-observation-ref="new:strong"'])assert(findings.includes(text),text);
assert(!findings.includes('<script>'));
console.log('Evidence-derived findings and separately attributed model hypothesis review passed');

const {renderAttempts}=require('../traffic_agent/static/planner_view.js');
const attempts=renderAttempts([{started:'now',status:'model_failed',requested:true,usage:null,error:'<script>failure</script>'},
 {status:'validation_failed',requested:false,error:'预算不足'},
 {status:'validation_failed',requested:true,usage:{tokens:2819},error:'占位内容'}]);
for(const text of ['用量未知','0 次模型请求','2819 tokens','本次未取得可执行规划','&lt;script&gt;'])assert(attempts.includes(text),text);
assert(!attempts.includes('<script>'));
assert(!attempts.includes('已完成'));
console.log('Initial attempt history preserves failures, unknown cost and escaped text');

const programme=require('./fixtures/programme_view.cjs');
const whole=render(programme);
for(const text of ['整个研究任务','各组实测与复核齐全','道路禁行适配','转向状态图适配','单项与组合实测对照',
  '-10.000%','+8.000%','控制未生效','组合额外项 +5.000 pcu·h','模型判断：不支持',
  'data-observation-ref="LOCAL_ONEWAY_FIXTURE:combined"','data-observation-ref="LOCAL_TURN_FIXTURE:baseline"'])assert(whole.includes(text),text);
const partial=structuredClone(programme);
partial.programme_review.complete=false;partial.programme_review.reviewed=false;
partial.programme_review.summary='<script>待复核</script>';partial.programme_review.updates=[];
partial.programme_review.unanswered=['尚未完成整个任务的模型复核'];
const pending=render(partial);
assert(pending.includes('等待整体复核'));
assert(pending.includes('尚未完成整个任务的模型复核'));
assert(pending.includes('&lt;script&gt;'));
assert(!pending.includes('<script>'));
assert(!pending.includes('各组实测与复核齐全'));
console.log('Whole programme review, factorial feedback and incomplete status passed');
const {programmeStatus}=require('../traffic_agent/static/planner_view.js');
const partialHeader={...partial,job_id:'LOCAL_ONEWAY_FIXTURE',status:'incomplete',allocated_runs:8,
  progress:[{status:'completed',completed_run_count:4},{status:'incomplete',completed_run_count:1}]};
const partialHeadline=programmeStatus(partialHeader);
for(const text of ['整个任务未完成','1 / 2 组完成','已完成 5 / 8 场景','仅指地图当前查看'])assert(partialHeadline.includes(text),text);
assert(!partialHeadline.includes('整个任务已完成'));
assert(programmeStatus({...partialHeader,status:'completed'}).includes('整个任务未完成'));
assert(programmeStatus({...partialHeader,status:'running'}).includes('整个任务进行中'));
assert(programmeStatus({...programme,job_id:'LOCAL_ONEWAY_FIXTURE',status:'completed'}).includes('整个任务已完成'));
assert(programmeStatus({...programme,job_id:'LOCAL_ONEWAY_FIXTURE',status:'completed',conclusion_review:{status:'needs_review'}}).includes('整个任务未完成'));
assert.equal(programmeStatus(programme),''); // A proposal is not an executed programme.
assert.equal(programmeStatus({...partialHeader,experiments:[partialHeader.experiments[0]]}),'');
const liveHeader={...partialHeader,status:'running',progress:[{status:'completed',completed_run_count:4},{status:'running',completed_run_count:2,run_count:9}]};
assert(programmeStatus(liveHeader).includes('已完成 6 / 8 场景'));
assert(!programmeStatus(liveHeader).includes('整个任务已完成'));
assert(programmeStatus({...liveHeader,progress:[{completed_run_count:4},{run_count:9}]}).includes('已确认 4 / 8 场景 · 1 组计数待读取'));
assert(programmeStatus({...liveHeader,progress:[{run_count:9},{completed_run_count:-1}]}).includes('已完成 — / 8 场景 · 2 组计数待读取'));
console.log('Persistent whole-programme status never confuses a finished group with final completion');

const rejectedDraft=renderAttempts([{status:'validation_failed',requested:true,usage:{tokens:111},error:'重复安排相同实验',
  proposal:{goal:'比较两处停车拥堵',reason:'<script>不允许执行</script>',studies:[
    {study:'standard',strategies:['parking'],road:'',target:'auto',demand_profile:'base',rationale:'新基准定位区域一'},
    {study:'standard',strategies:['parking'],road:'',target:'auto',demand_profile:'base',rationale:'新基准定位区域二'}],missing:[]}}]);
for(const text of ['模型提议摘要 · 未经执行验证','全网自动选区','新基准定位区域一','新基准定位区域二','111 tokens','本次未取得可执行规划','&lt;script&gt;'])assert(rejectedDraft.includes(text),text);
assert(!rejectedDraft.includes('<script>'));
assert(!rejectedDraft.includes('<button'));
assert(!rejectedDraft.includes('研究已完成'));
console.log('Rejected draft remains inspectable without an execution action or success label');

const targetView=render({...sample,planning_trace:[{kind:'oneway_target',job_id:'fresh',ordinal:1,status:'accepted',action:'oneway_3_4',
  input:{allowed_actions:{oneway_1_2:{title:'甲路 <script>',closed_direction:[1,2],baseline_car_flow:401,
    roads:[{index:0,from_node:1,to_node:2,speed_kmh:10,vc:2,parking:3},{index:1,from_node:2,to_node:1,speed_kmh:11,vc:1.9,parking:3}],
    topology:{bus_routes_checked:7,modal_detours:{car:{max_extra_km:.6}}}},
    oneway_3_4:{title:'乙路单向与停车对照',closed_direction:[3,4],baseline_car_flow:380,
    roads:[{index:2,from_node:3,to_node:4,speed_kmh:12,vc:1.8,parking:3},{index:3,from_node:4,to_node:3,speed_kmh:15,vc:1.3,parking:4}],
    topology:{bus_routes_checked:7,modal_detours:{car:{max_extra_km:.2}}}}}}}]});
for(const text of ['单向目标如何选定','模型选定','未试验位置','拟关闭方向','保留通行方向','V/C 1.80','380.0 veh/h','0.20 km',
  'data-region-run="baseline"','data-region-indices="2,3"','未选位置没有治理实测','&lt;script&gt;'])assert(targetView.includes(text),text);
assert(!targetView.includes('<script>'));
assert.equal((targetView.match(/class="target-selected"/g)||[]).length,1);
console.log('One-way target choices display real baseline attributes without claiming untested benefit');

const explicitBlocker=render({status:'needs_validation',goal:'调整信号',reason:'当前执行器没有信号控制接口',
  experiments:[],assessment:[],missing:['信号配时尚未接入执行','<script>输入需校验</script>']});
for(const text of ['当前能力无法执行','本次没有启动仿真','信号配时尚未接入执行','&lt;script&gt;'])assert(explicitBlocker.includes(text),text);
assert(!explicitBlocker.includes('<script>'));assert(!explicitBlocker.includes('研究已完成'));assert(!explicitBlocker.includes('已编排'));
const explicit=renderAttempts([{status:'validation_failed',requested:true,error:'执行分支为空',
  usage:{tokens:1000,input_tokens:800,output_tokens:200,reasoning_tokens:50,finish_reason:'tool_calls',tool_call_count:1,request_schema_sha256:'PRIVATE_SCHEMA'},
  proposal:{intent:'execute',goal:'区域比较',reason:'待校验',studies:[],missing:[]}}]);
for(const text of ['本次决策：安排研究','工具调用已返回','输入 tokens 800','输出 tokens 200','其中推理 tokens 50','规划是否有效以校验结果为准'])assert(explicit.includes(text),text);
assert(!explicit.includes('PRIVATE_SCHEMA'));assert(!explicit.includes('研究已完成'));
assert(!renderAttempts([{status:'model_failed',requested:true,usage:{tokens:null}}]).includes('接口记录：'));
console.log('Explicit blockers and bounded interface diagnostics remain separate from successful execution');

const scopeCard=render({...sample,experiments:sample.experiments.map(x=>({...x,task:{...x.task,scenario:'parking'}})),execution_scope:{search_scope:'whole_dataset',roads:[],
  limit_note:'每个独立候选最多改变1条有向路段；不同区域是独立实验。',regional_design:{regions_per_parking_group:2}}});
for(const text of ['范围与执行条件','研究范围：整个案例路网','每个独立候选最多改变1条','每组比较 2 个区域','可用候选等待新基准观测'])assert(scopeCard.includes(text),text);
const citedQuestion=renderAttempts([{status:'needs_input',requested:true,proposal:{intent:'needs_input',goal:'定位学校',studies:[],
  missing:['请给出道路'],question_citations:[{need:'unresolved_location',source_quote:'学校门口<script>'}]}}]);
assert(citedQuestion.includes('模型要求核对的原文：'));assert(citedQuestion.includes('学校门口&lt;script&gt;'));assert(!citedQuestion.includes('<script>'));
console.log('Execution scope and quoted clarification grounds are visible and escaped');
const reviewedAttempts=[{status:'validation_failed',phase:'initial',requested:true,error:'自动选区不需要先指定道路<script>',usage:{tokens:80}},
  {status:'ready',phase:'validation_correction',requested:true,explanation:'修正后安排两个区域比较',usage:{tokens:120}}];
const initialReview=render({...sample,initial_planning_attempts:reviewedAttempts});
for(const text of ['规划修正 · 1次上限','2 次请求均计入总预算','初始提议未通过','规划已通过执行校验','第一次失败已保留',
                   '80 tokens','120 tokens','&lt;script&gt;'])assert(initialReview.includes(text),text);
assert(!initialReview.includes('<script>'));
const correctedBlocked=render({status:'needs_input',goal:'区域比较',missing:['总调用预算不足'],initial_planning_attempts:reviewedAttempts.map((a,i)=>i?{...a,status:'needs_input'}:a)});
assert(correctedBlocked.includes('规划修正 · 1次上限'));assert(correctedBlocked.includes('本次没有启动仿真'));
assert(!correctedBlocked.includes('规划通过校验'));assert(!correctedBlocked.includes('不会自动重试'));
const reviewedHistory=renderAttempts(reviewedAttempts);
for(const text of ['初始提议 · 第1次请求','校验后修正 · 第2次请求','本次未取得可执行规划','后续执行另见调度记录'])assert(reviewedHistory.includes(text),text);
console.log('Bounded initial correction keeps original failure visible and never implies native success');

const costResult=render({...sample,conclusion_review:{status:'needs_review',scope:'有限实测核对',
  costs:[{ref:'fresh:parking_2',run:'parking_2',roads:['七东线<script>'],demand_factor:1,constraint_passed:true,has_measured_cost:true,
    neighbor_new_links:[{index:10814,name:'18208路',from_node:4084,to_node:3239,vc:1.00092}],core_time_change_pct:-16.66,neighbor_time_change_pct:.011,neighbor_new_oversaturated_km:.056,neighbor_net_oversaturated_km:.056,network_net_oversaturated_km:.056}],
  hypothesis_gaps:[{hypothesis_id:'H1',missing:'未测排队长度'}],issues:[{message:'阈值内增加仍不是零新增'}]}});
for(const text of ['收益之外，还发生了什么','结论待核对','约束通过 · 有实测代价','邻区毛新增过饱和','+0.056 km','未测排队长度',
  'data-observation-ref="fresh:parking_2"','data-region-indices="10814"','定位新增过饱和：18208路','已保存的决策记录','阈值内增加仍不是零新增','&lt;script&gt;'])assert(costResult.includes(text),text);
assert(!costResult.includes('<script>'));
console.log('Measured costs remain visible despite constraint eligibility and conflicting model conclusions');
const conflictedGroup=render({...sample,conclusion_review:{status:'needs_review',costs:[],issues:[{message:'保留实际代价'}]},
  progress:[{status:'incomplete',recorded_status:'completed',run_count:3,review:{outcome:'有收益但存在代价',
    completed_runs:3,rejected_count:0,conclusion_review:{status:'needs_review'}}}],
  planning_trace:[{job_id:'j',ordinal:3,status:'accepted',action:'stop',
    selected:{title:'完成结果复核；仅保留已验证结论'},summary:'这是保留的原始回复',
    conclusion_review:{status:'needs_review'},verification_notes:['原回复与实测代价不符']}]
});
for(const text of ['实测已记录 · 结论待核对','结果复核存在冲突','这是保留的原始回复','原回复与实测代价不符'])assert(conflictedGroup.includes(text),text);
assert(!conflictedGroup.includes('研究已完成'));
assert(!conflictedGroup.includes('完成结果复核；仅保留已验证结论'));
console.log('Group completion and final-turn labels agree with derived conclusion conflicts without hiding the original reply');

const routingResult=render({...sample,planning_trace:[{kind:'route',status:'accepted',action:'study_3',ordinal:2,job_id:'offline',
  input:{context:{route_budget:{requests_after_this_decision:3,choices:{
    study_2:{title:'停车组<script>',required_requests:4,feasible:false},
    study_3:{title:'现状诊断',required_requests:3,feasible:true}
  }}}}}]});
for(const text of ['下一组如何安排','还可用 <b>3</b> 次模型请求','全部剩余研究及最终复核','暂缓顺序','模型选定',
  '仍在必做计划中','停车组&lt;script&gt;'])assert(routingResult.includes(text),text);
assert.equal((routingResult.match(/class="route-chosen"/g)||[]).length,1);
assert(!routingResult.includes('<script>'));
assert(!live.includes('下一组如何安排'));
console.log('Study routing explains budget-feasible choices while retaining deferred required groups');

const handoffResult=render({...sample,planning_trace:[{kind:'parking',status:'accepted',action:'study_2',ordinal:2,job_id:'offline',
  selected:{title:'乙路单向对照'},input:{context:{study_handoff:{version:'study-handoff-v1',actions:['study_2']},
    route_budget:{requests_after_this_decision:3,choices:{study_2:{title:'乙路单向对照',required_requests:2,feasible:true}}}}}}]});
for(const text of ['本组已复核 · 转入下一组','同时确认了当前组的假设与实测代价','后续组使用自己的新基准',
  '模型复核当前组并选定后续研究'])assert(handoffResult.includes(text),text);
assert(!routingResult.includes('本组已复核 · 转入下一组'));

const pendingObservation={run:'baseline',demand_factor:1,changes:[],spatial:{inspected_links:3,overloaded_links:2,outside_hotspots:[],
  regions:[{id:'area_1',title:'甲路',indices:[0,1],weighted_speed_kmh:10,roads:[{index:0,from_node:1,to_node:2},{index:1,from_node:2,to_node:1}]}]}};
const pendingChoice={job_id:'fresh',kind:'parking',status:'accepted',action:'p1',ordinal:1,
  selected:{title:'甲路单方向停车1级<script>',changes:[{from_node:1,to_node:2,parking:1}],region:'area_1'},input:{observations:[pendingObservation]}};
const executing={...sample,status:'running',progress:[{job_id:'fresh',status:'running'}],planning_trace:[pendingChoice]};
const selectedPending=render(executing);
for(const text of ['已选定 · 等待治理结果','尚无对应的完整实测结果','定位待验证动作','data-region-indices="0"','&lt;script&gt;'])assert(selectedPending.includes(text),text);
assert(!selectedPending.includes('等待模型选择第一个区域'));assert(!selectedPending.includes('<script>'));
const haltedChoice=render({...executing,status:'failed',progress:[{job_id:'fresh',status:'failed'}]});
assert(haltedChoice.includes('已选定 · 研究中断'));assert(!haltedChoice.includes('已选定 · 等待治理结果'));
const awaitingChoice=render({...executing,planning_trace:[{...pendingChoice,status:'awaiting_model',selected:undefined}]});
assert(awaitingChoice.includes('模型正在比较候选区域'));assert(!awaitingChoice.includes('已选定 ·'));
const returnedPolicy={...pendingObservation,run:'parking_1',changes:pendingChoice.selected.changes,eligible:false,
  delta:{core:{running_time_change_pct:-10},neighbor:{running_time_change_pct:1}},violations:['邻区恶化']};
const returnedChoice=render({...executing,planning_trace:[pendingChoice,{...pendingChoice,status:'awaiting_model',selected:undefined,input:{observations:[pendingObservation,returnedPolicy]}}]});
assert(!returnedChoice.includes('已选定 · 等待治理结果'));assert(returnedChoice.includes('data-region-run="parking_1"'));
const secondChoice=render({...executing,planning_trace:[pendingChoice,{...pendingChoice,selected:{title:'另测停车2级',changes:[{from_node:1,to_node:2,parking:2}]},input:{observations:[pendingObservation,returnedPolicy]}}]});
assert(secondChoice.includes('已选定 · 等待治理结果'));assert(secondChoice.includes('另测停车2级'));assert(secondChoice.includes('data-region-run="parking_1"'));
const safeExcerpt=render({...sample,planning_trace:[{status:'rejected',kind:'parking',job_id:'fresh',ordinal:1,
  reason:'最终复核遗漏初始假设，研究未完成',proposal_excerpt:{summary:'<script>公开提议</script>'},proposal_capture:{excerpted:true}}]});
for(const text of ['遗漏初始假设','查看被拒绝的工具提议','仅保留公开工具字段','部分内容有省略或截短','这份提议未获执行','&lt;script&gt;'])assert(safeExcerpt.includes(text),text);
assert(!safeExcerpt.includes('<script>'));
console.log('Accepted pending actions, failed execution, returned observations and safe rejection excerpts remain distinct');
const unknownReview=render({...sample,conclusion_review:{status:'not_reviewed',costs:[],issues:[],
  unreviewed:[{message:'未取得可核对的完整结论；不能推定具体事实冲突。'}]}});
assert(unknownReview.includes('模型复核未通过'));assert(unknownReview.includes('不能推定具体事实冲突'));
assert(!unknownReview.includes('类结论冲突'));assert(!unknownReview.includes('原回复在下方完整保留'));
console.log('Missing rejected content is not displayed as a proven factual contradiction');
const overBudget={version:'initial-plan-budget-v1',fits:false,correction_feasible:true,native:{required:7,limit:6},model:{required:6,used:1,limit:6},groups:[
  {title:'停车治理',conditions:{road_name:'甲路<script>',scenario:'parking'},native_runs:2,model_requests:2,may_omit:false},
  {title:'额外体检',conditions:{road_name:'乙路',scenario:'diagnosis'},native_runs:1,model_requests:1,may_omit:true},
  {title:'单向组合',conditions:{road_name:'乙路',scenario:'oneway'},native_runs:4,model_requests:2,may_omit:false}
]};
const correctedBudget={...overBudget,fits:true,correction_feasible:false,native:{required:6,limit:6},model:{required:6,used:2,limit:6},groups:overBudget.groups.filter(g=>!g.may_omit)};
const budgetSteps=[{status:'needs_input',requested:true,explanation:'提议需要7次，但预算6次',budget_check:overBudget},
  {status:'ready',requested:true,explanation:'模型撤回额外体检，保留两组治理对照',budget_check:correctedBudget}];
const budgetView=render({...sample,initial_planning_attempts:budgetSteps});
for(const text of ['提议超出预算 · 尚未执行','本地预算核算通过','7 / 6','6 / 6','Vehicle1 独立基准','Vehicle 独立基准','额外体检，可由模型重新考虑','甲路&lt;script&gt;','模型撤回额外体检'])assert(budgetView.includes(text),text);
assert(!budgetView.includes('<script>'));
const budgetHistory=require('../traffic_agent/static/planner_view.js').renderAttempts(budgetSteps.map(a=>({...a,started:'2026-09-27T00:00:00Z'})));
assert(budgetHistory.includes('提议超出预算'));assert(budgetHistory.includes('7 / 6'));
const singleBudget=render({...sample,status:'needs_input',experiments:[],missing:['预算不足'],budget_check:overBudget});
assert(singleBudget.includes('提议超出预算'));assert(singleBudget.includes('本次没有启动仿真'));
console.log('Initial budget repair exposes costs, different engines, original rejection and bounded correction');

const correctionFixture=structuredClone(require('./fixtures/review_correction_view.cjs'));
correctionFixture.planning_trace[0].review_correction.first_attempt.validation.message+=' <script>';
correctionFixture.planning_trace[0].review_correction.first_attempt.proposal_excerpt.risk='<img src=x>';
const corrected=render(correctionFixture);
for(const text of ['最终复核修正 · 1次上限','首次复核未通过','修正后的复核已通过校验','2 次请求 · 已知 180 tokens','模型请求 6 / 6','同一份观测','此处没有新增仿真','&lt;script&gt;','&lt;img','首次失败保留'])assert(corrected.includes(text),text);
assert(!corrected.includes('<script>'));assert(!corrected.includes('<img'));
const correcting=structuredClone(correctionFixture);correcting.planning_trace[0].status='awaiting_model';delete correcting.planning_trace[0].usage;
const awaitingCorrection=render(correcting);
for(const text of ['正在依据反馈修正复核','回复与用量待返回','1次用量尚未知'])assert(awaitingCorrection.includes(text),text);
assert(!awaitingCorrection.includes('修正后的复核已通过'));
correcting.planning_trace[0].status='rejected';
assert(render(correcting).includes('修正仍未通过，研究未完成'));
console.log('Bounded final correction keeps both attempts, usage and incomplete states visible');

const waitingLabel=structuredClone(correctionFixture);
waitingLabel.progress[0].status='running';waitingLabel.planning_trace[0].status='awaiting_model';
assert(render(waitingLabel).includes('正在修正最终复核'));assert(!render(waitingLabel).includes('正在仿真'));
delete waitingLabel.planning_trace[0].review_correction;
waitingLabel.planning_trace[0].input={allowed_actions:{stop:{}}};
assert(render(waitingLabel).includes('正在复核实测结果'));
waitingLabel.planning_trace[0].input.allowed_actions.policy={};
assert(render(waitingLabel).includes('等待模型选择下一步'));
console.log('Native execution and pending model decisions have distinct group status labels');

const handoffRepair=structuredClone(correctionFixture);
handoffRepair.progress[0].status='running';
const handoffTurn=handoffRepair.planning_trace[0];
handoffTurn.status='awaiting_model';delete handoffTurn.usage;
handoffTurn.review_correction.version='study-review-correction-v1';
handoffTurn.review_correction.first_attempt.proposal_excerpt.action='study_2';
handoffTurn.review_correction.first_attempt.conclusion_check={issues:[{message:'核心实测范围错误 <script>'}]};
handoffTurn.input={context:{study_handoff:{actions:['study_2']},route_budget:{requests_after_this_decision:3,
  choices:{study_2:{title:'下一独立研究',required_requests:2,feasible:true}}}}};
const handoffRepairHtml=render(handoffRepair);
for(const text of ['本组交接复核修正 · 1次上限','正在修正本组交接复核','后续研究尚未执行','共享这一次修正机会','保留原选组 · 等待复核','2</b> 次模型请求（已计入本次修正）','核心实测范围错误 &lt;script&gt;'])assert(handoffRepairHtml.includes(text),text);
assert(!handoffRepairHtml.includes('正在修正最终复核'));assert(!handoffRepairHtml.includes('正在仿真'));assert(!handoffRepairHtml.includes('<script>'));
handoffTurn.status='rejected';
assert(render(handoffRepair).includes('修正失败 · 未转入'));
handoffTurn.status='accepted';handoffTurn.action='study_2';
const handoffPassed=render(handoffRepair);
assert(handoffPassed.includes('模型选定'));assert(handoffPassed.includes('修正后的复核已通过校验'));
assert(handoffPassed.includes('后续研究尚未执行'));
console.log('Local handoff recovery shows the actual repair stage, frozen next group, reserved budget and original conflict');

const componentBudget=structuredClone(overBudget);componentBudget.version='initial-plan-budget-v2';
componentBudget.groups[1]={...componentBudget.groups[1],title:'额外独立停车',conditions:{road_name:'乙路',scenario:'parking'},native_runs:2,model_requests:2,
  omission:{kind:'extra_factorial_component',source_quote:'乙路的单向与停车组合<script>',reason:'组合仍保留自己的四项对照，不共享基准。'}};
const componentHtml=render({...sample,status:'needs_input',experiments:[],missing:['提议超过预算'],budget_check:componentBudget});
for(const text of ['额外独立停车，可由模型重新考虑','乙路的单向与停车组合&lt;script&gt;','不共享基准','由模型重新考虑标记的额外研究','模型规划未符合预算','原始请求、预算及失败提议已保留'])assert(componentHtml.includes(text),text);
assert(!componentHtml.includes('<script>'));
assert(!componentHtml.includes('请在左侧补充'));
const stillOver=render({status:'needs_input',goal:'未通过预算',missing:['修正提议仍超出预算'],initial_planning_attempts:budgetSteps.map(a=>({...a,status:'needs_input',budget_check:overBudget}))});
assert(stillOver.includes('模型规划未符合预算'));assert(stillOver.includes('修正提议仍超出预算'));assert(!stillOver.includes('请在左侧补充'));
console.log('Extra component drafts show the user quote, separate baselines and bounded revision permission');

const diagnosticScope=render({...sample,diagnosis_constraints:{only:false,roads:['只诊断道路<script>']},
  execution_scope:{search_scope:'named_roads',roads:['只诊断道路<script>','治理道路'],limit_note:'各自建立新基准',regional_design:{regions_per_parking_group:1}}});
assert(diagnosticScope.includes('只做现状诊断：只诊断道路&lt;script&gt;'));
assert(!diagnosticScope.includes('只做现状诊断：全部研究范围'));assert(!diagnosticScope.includes('<script>'));
console.log('Road-specific observation-only permissions are visible without banning other studies');

const preciseCost=render({...sample,conclusion_review:{status:'needs_review',issues:[{message:'实际停车2级，模型写成1级'}],
  executed_policies:[{ref:'j:S1_parking',label:'停车2级<script>'}],costs:[{ref:'j:S1_parking',run:'S1_parking',
    roads:['乙路'],constraint_passed:true,has_measured_cost:true,demand_factor:1,core_time_change_pct:-31.5,
    neighbor_time_change_pct:.0000149734122656,neighbor_new_oversaturated_km:0,neighbor_net_oversaturated_km:0,network_net_oversaturated_km:0}]}});
for(const text of ['实际执行：停车2级&lt;script&gt;','+0.0000150%','结论待核对'])assert(preciseCost.includes(text),text);
assert(!preciseCost.includes('<script>'));assert(!preciseCost.includes('+0.000%'));
console.log('Measured action labels and tiny positive costs remain visible next to conflicting model text');

const contrastView=render({...sample,progress:[{...sample.progress[0],title:'甲路<script>',study_comparison:{scope:'独立对照',factors:[{
  demand_factor:1,baseline_ref:'fresh:baseline',policies:[{ref:'fresh:S1_parking',run:'S1_parking',label:'停车2级',status:'admissible',core_time_change_pct:-31,neighbor_time_change_pct:.0000149734}],
  combination_contrasts:[{single_label:'停车2级',single_status:'admissible',combined_status:'rejected',core_time_difference_pcu_h:-12,neighbor_time_difference_pcu_h:4,network_time_difference_pcu_h:null}]
}]}}]});
for(const text of ['实测方案怎么选','甲路&lt;script&gt;','停车2级','+0.0000150%','单项：通过约束 · 组合：未通过','-12.000','+4.000','未提供','单位 pcu·h','data-observation-ref="fresh:S1_parking"'])assert(contrastView.includes(text),text);
assert(!contrastView.includes('<script>'));
console.log('Independent study comparisons preserve actual levels, rejection and tiny measured costs');

const measuredGroups=[{version:'study-comparison-v2',study_id:'study_1',job_id:'parking',title:'仅停车组',
  coverage_text:'新基准1场、仅停车2场；已测需求×1',completed_scenes:[{ref:'parking:parking_2',kind:'parking',demand_factor:1}],
  scope:'同组独立比较',factors:[]},
{version:'study-comparison-v2',study_id:'study_2',job_id:'oneway',title:'单向组<script>',
  coverage_text:'新基准1场、仅停车1场、仅交通组织1场、停车＋交通组织1场；已测需求×1',
  completed_scenes:[{ref:'oneway:S3_combined',kind:'combined',demand_factor:1}],scope:'不得相加收益',factors:[{
    demand_factor:1,baseline_ref:'oneway:baseline',combination_contrasts:[],policies:[{
      ref:'oneway:S3_combined',run:'S3_combined',label:'停车2级＋单向',status:'rejected',core_time_change_pct:-85,neighbor_time_change_pct:171.8985,
      checks:{control_applicable:true,control_passed:true,core_time_decreased:true,neighbor_limits_passed:false,recommendation_admissible:false},
      control_directions:[{closed:[3237,3236],retained:[3236,3237]}],
      neighbor_limits:{neighbor_new_oversaturated_km:1.03054,time_limit_pct:.5,new_oversaturated_limit_km:.1}
    }]}]}];
const separateResult={...sample,progress:measuredGroups.map(g=>({job_id:g.job_id,title:g.title,study_comparison:g})),
  planning_trace:[{status:'accepted',kind:'route',action:'stop',ordinal:5,stop_basis_origin:'completed-study-comparisons',
    stop_basis:'仅停车组完成2个停车候选；单向组完成单项及组合。',reason:'保留的模型错误原话<script>',input:{context:{}}}],
  programme_review:{complete:true,summary:'原模型整体解释<script>',comparisons:measuredGroups,updates:[],unanswered:[],
    evidence:{scope:'独立实测',findings:[],groups:measuredGroups.map(g=>({id:g.study_id,title:g.title,execution_complete:true,finding_ids:[]}))}}};
const separateHtml=render(separateResult);
for(const text of ['实际完成 · 来自场景记录','新基准1场、仅停车2场','data-observation-ref="parking:parking_2"','仅停车 · parking_2 · 需求 ×1',
  '禁行 3237→3236；保留 3236→3237','<dt>原生控制响应</dt><dd>通过</dd>','<dt>配对核心耗时</dt><dd>下降</dd>',
  '<dt>邻区两项门槛</dt><dd>超门槛</dd>','<dt>推荐资格</dt><dd>不具备</dd>','耗时增幅 ≤ 0.5%','毛新增 ≤ 0.1 km',
  '停止依据 · 来自逐组实测清单','查看模型原始理由','保留的模型错误原话&lt;script&gt;','查看模型整体解释（原文）'])assert(separateHtml.includes(text),text);
assert(!separateHtml.includes('<script>'));
const missingControl=structuredClone(separateResult);
missingControl.progress[1].study_comparison.factors[0].policies[0].checks.control_passed=null;
assert(render(missingControl).includes('<dt>原生控制响应</dt><dd>未证实</dd>'));
missingControl.progress[1].study_comparison.factors[0].policies[0].checks.control_applicable=false;
assert(render(missingControl).includes('不涉及禁行／禁转'));
console.log('Measured group coverage, actual directions, four separate checks and original model explanations remain distinct and escaped');

const scopedHandoff={kind:'parking',status:'accepted',action:'study_2',ordinal:2,job_id:'offline',
  input:{context:{review_scope:{mode:'programme_final_only'},study_handoff:{actions:['study_2']},
    route_budget:{requests_after_this_decision:3,choices:{study_2:{title:'乙路',required_requests:2,feasible:true}}}}}};
const scopedHandoffHtml=render({...sample,planning_trace:[scopedHandoff]});
assert(scopedHandoffHtml.includes('初始假设保留至总任务最终复核'));
assert(!scopedHandoffHtml.includes('同时确认了当前组的假设'));
const invalidatedHandoffHtml=render({...sample,planning_trace:[{...scopedHandoff,conclusion_review:{status:'needs_review'}}]});
for(const text of ['已转入下一组 · 原复核待核对','本组复核仍未完成'])assert(invalidatedHandoffHtml.includes(text),text);
assert(!invalidatedHandoffHtml.includes('本组已复核 · 转入下一组'));
console.log('Scoped handoff keeps historical execution separate from a disputed review');

const interpreted=structuredClone(typedFinding);
interpreted.model_question='模型原提问<script>';
interpreted.question='每个约定需求下，冻结核心总耗时是否下降？';
interpreted.evaluation.interpretation={version:'bounded-question-wording-v1',question:interpreted.question,boundary:'未发现冲突不是语义认证',limits:['固定核心不等于整条道路']};
const interpretedHtml=render({...sample,progress:[{job_id:'synthetic',review:{research_review:{evidence:{findings:[interpreted]},updates:[]}}}]});
for(const t of ['原问题与实际检验','模型原提问&lt;script&gt;','冻结核心总耗时是否下降','口径与适用边界','未发现冲突不是语义认证','固定核心不等于整条道路'])assert(interpretedHtml.includes(t),t);
assert(!interpretedHtml.includes('<script>'));
assert(typedHtml.includes('原问题是否得到回答'));
console.log('Operational questions, preserved original wording and explicit semantic limits render separately; legacy headings stay unchanged');

const generatedQuestion=structuredClone(interpreted);
generatedQuestion.evaluation.interpretation.origin='model_structured_definition';
generatedQuestion.evaluation.interpretation.boundary='由模型所选定义生成，原用户请求另行保留';
const generatedQuestionHtml=render({...sample,version:'natural-strategy-plan-v10',
  assessment:[{...typedHypothesis,authoring:{version:'named-question-authoring-v1'}}],
  progress:[{job_id:'synthetic',review:{research_review:{evidence:{findings:[generatedQuestion]},updates:[]}}}]});
for(const t of ['基于实测的智能规划','由模型定义生成的研究问题','<b>模型定义的问题</b>','原用户请求另行保留'])assert(generatedQuestionHtml.includes(t),t);
assert(!generatedQuestionHtml.includes('<b>模型原提问</b>'));
assert(!generatedQuestionHtml.includes('<script>'));
console.log('Generated questions have explicit provenance while verbatim questions retain their original label');

const separated=structuredClone(interpreted);
separated.evaluation.interpretation.version='bounded-question-wording-v2';
separated.evaluation.interpretation.wording_notes=[{source:'hypothesis',kind:'separate_criteria',text:'判定与是否通过推荐约束分开<script>'}];
const separatedHtml=render({...sample,progress:[{job_id:'synthetic',review:{research_review:{evidence:{findings:[separated]},updates:[]}}}]});
for(const t of ['独立判定说明','判定与是否通过推荐约束分开&lt;script&gt;','不表示另一项已经通过','实际结果分别检验'])assert(separatedHtml.includes(t),t);
assert(!separatedHtml.includes('<script>'));assert(!interpretedHtml.includes('独立判定说明'));
console.log('Independent-judgment notes remain attributable and escaped without implying a second condition passed; legacy wording stays unchanged');

const criterionDraft={goal:'离线判据修正',studies:[],assessment:[{hypothesis:'原问题<script>',evidence_needed:'独立新基准',criterion_status:'invalid',criterion:{test:'core_advantage_over_parking',policies:'parking',quantifier:'all',studies:[1]}}]};
const criterionAttempt={status:'validation_failed',started:'2026-09-28T00:00:00Z',requested:true,phase:'initial',usage:{tokens:100},proposal:criterionDraft,
  hypothesis_check:{version:'initial-hypothesis-correction-v1',correction_feasible:true,issues:[{hypothesis_id:'H1',message:'不能与自身比较<script>'}]}};
const criterionAttemptsHtml=require('../traffic_agent/static/planner_view.js').renderAttempts([criterionAttempt]);
for(const t of ['开跑前发现的判据冲突','不能与自身比较&lt;script&gt;','原问题与当时判据','原问题&lt;script&gt;','核心耗时优于仅停车','针对：仅停车','参数搭配未通过','1 次模型请求'])assert(criterionAttemptsHtml.includes(t),t);
assert(!criterionAttemptsHtml.includes('<script>'));
const criterionRepairHtml=render({...sample,initial_planning_attempts:[criterionAttempt,{...criterionAttempt,status:'ready',hypothesis_check:null,usage:{tokens:120}}]});
assert(criterionRepairHtml.includes('2 次请求均计入总预算'));assert(criterionRepairHtml.includes('第一次失败已保留'));
console.log('Criterion conflicts, exact rejected policy parameters and both pre-execution requests remain visible and escaped');

const patchAttempt={...criterionAttempt,status:'ready',hypothesis_check:null,proposal_origin:'original_plus_model_policy_patch',
  policy_patch:{version:'hypothesis-policy-patch-v1',status:'applied',selections:{H2:'combined'}}};
const patchHtml=require('../traffic_agent/static/planner_view.js').renderAttempts([patchAttempt]);
for(const t of ['模型只修正检验对象','H2','组合措施','原方案已保留','不要求模型重述','原方案＋本次字段选择 · 未经执行验证'])assert(patchHtml.includes(t),t);
assert(!patchHtml.includes('模型提议摘要'));
assert(render({...sample,initial_planning_attempts:[criterionAttempt,patchAttempt]}).includes('模型只修正检验对象'));
const rejectedPatchHtml=require('../traffic_agent/static/planner_view.js').renderAttempts([{...patchAttempt,proposal:null,status:'validation_failed',
  policy_patch:{version:'hypothesis-policy-patch-v1',status:'rejected',selections:{'H2<script>':'bad<script>'}}}]);
assert(rejectedPatchHtml.includes('字段修正未通过，未应用到原方案'));assert(!rejectedPatchHtml.includes('<script>'));
assert(!rejectedPatchHtml.includes('原方案已保留'));
console.log('Actual field selections and compiled proposal origin are distinct; rejected patches do not imply application');
const quantifierAttempt={...patchAttempt,proposal_origin:'original_plus_model_field_patch',
  policy_patch:{version:'hypothesis-field-patch-v1',status:'applied',selections:{H1:{quantifier:'any'},H2:{policies:'combined',quantifier:'all'}}}};
const quantifierHtml=require('../traffic_agent/static/planner_view.js').renderAttempts([quantifierAttempt]);
for(const t of ['模型修正判据字段','量词：至少一个已试措施','量词：全部已试措施','措施：组合措施','量词字段按原话纠正','原方案＋本次字段选择'])assert(quantifierHtml.includes(t),t);
assert(!quantifierHtml.includes('模型提议摘要'));
const rejectedQuantifierHtml=require('../traffic_agent/static/planner_view.js').renderAttempts([{...quantifierAttempt,status:'validation_failed',proposal:null,
  policy_patch:{version:'hypothesis-field-patch-v1',status:'rejected',selections:{'H1<script>':{'quantifier<script>':'<script>'}}}}]);
assert(!rejectedQuantifierHtml.includes('<script>'));assert(rejectedQuantifierHtml.includes('字段修正未通过'));
console.log('Quantifier corrections retain original-meaning and compiled-origin labels; nested fields remain escaped and failures visible');

const typedReadReview=render({...sample,
  public_claim_review:{version:'typed-public-claims-v1',status:'needs_review',issues:[{message:'毛新增里程符号冲突<script>'}]},
  conclusion_review:{status:'no_detected_conflict',costs:[],issues:[],hypothesis_gaps:[],scope:'旧协议'},
  programme_review:{summary:'公开模型总结与结构化实测存在冲突；原文保留在逐轮记录中。',
    complete:false,reviewed:true,evidence:{groups:[],findings:[],scope:'单组新基准'},hypotheses:[],updates:[],unanswered:[]},
  progress:[{status:'incomplete',public_claim_review:{status:'needs_review'},review:{}}]});
for(const t of ['公开结论待核对','模型结论待核对','毛新增里程符号冲突&lt;script&gt;','公开模型总结与结构化实测存在冲突'])assert(typedReadReview.includes(t),t);
assert(!typedReadReview.includes('<script>'));

const posthocReview={status:'needs_review',read_only:true,job_id:'j',ordinal:3,
  issues:[{field:'hypothesis_updates:H3',message:'耗时为微小正值，不是0<script>'}]};
const sourceHypotheses=['H1','H3'].map(id=>({id,hypothesis:id+'原问题'}));
const sourceUpdates=sourceHypotheses.map(h=>({hypothesis_id:h.id,judgment:'supported',explanation:h.id+'保留的原解释',finding_ids:['hypothesis.'+h.id]}));
const sourceEvidence={groups:[],scope:'当前实测',findings:sourceHypotheses.map(h=>({id:'hypothesis.'+h.id,hypothesis_id:h.id,
  question:h.hypothesis,answer:'配对值通过',expected_judgment:'supported',status:'supported',measurements:[],evidence_refs:[],
  evaluation:{rule:'冻结门槛',scope:[],policies:'all',quantifier:'any'}}))};
const posthocHtml=render({...sample,job_id:'j',status:'incomplete',recorded_status:'completed',public_claim_review:posthocReview,
  conclusion_review:{status:'no_detected_conflict',costs:[],issues:[]},
  programme_review:{summary:'后续核验待核对',recorded_summary:'原总结',complete:false,evidence:sourceEvidence,hypotheses:sourceHypotheses,updates:sourceUpdates},
  progress:[{job_id:'j',status:'incomplete',completed_run_count:4,review:{research_review:{job:'j',evidence:sourceEvidence,hypotheses:sourceHypotheses,updates:sourceUpdates}}}],
  planning_trace:[{job_id:'j',ordinal:3,status:'accepted',action:'stop',reason:'四项同需求对照齐全',
    selected:{title:'完成结果复核；仅保留已验证结论'},public_claim_review:posthocReview}]});
for(const t of ['原结束依据','四项同需求对照齐全','当前核验问题','结果复核存在冲突','H3保留的原解释','配对检验：满足事前判据'])assert(posthocHtml.includes(t),t);
assert(!posthocHtml.includes('为什么停止'));
assert(!posthocHtml.includes('完成结果复核；仅保留已验证结论'));
assert.equal((posthocHtml.match(/这段解释待核对/g)||[]).length,3);
assert.equal((posthocHtml.match(/H1保留的原解释/g)||[]).length,3);
assert(!posthocHtml.includes('<script>'));
console.log('Read-only audit flags the specific hypothesis and final turn while preserving the original stop reason');

const riskTurn={job_id:'j',ordinal:1,status:'accepted',action:'parking_x',risk:'已返回一项治理代价',
  risk_review:{version:'observed-risk-boundary-v1',measured_summary:'1项代价增加；具体值保留原观测',
    unverified:[{hypothesis:'是否可能出现动态排队<img>',basis_refs:['j:parking_1'],
      verification_needed:'需要另行采集排队时间序列<script>'}],boundary:'所引观测不证明风险已经发生。'}};
const riskHtml=render({...sample,planning_trace:[riskTurn]});
for(const t of ['实测代价 · 来自观测','模型待验证风险','还需怎样验证','提出问题所据：parking_1',
  'data-observation-ref="j:parking_1"','&lt;img&gt;','&lt;script&gt;','不证明风险已经发生'])assert(riskHtml.includes(t),t);
assert(!riskHtml.includes('<script>'));assert(!riskHtml.includes('<img>'));
const noExtraRisk=structuredClone(riskTurn);noExtraRisk.risk_review.unverified=[];
assert(render({...sample,planning_trace:[noExtraRisk]}).includes('不表示没有风险'));
const legacyRisk={...riskTurn};delete legacyRisk.risk_review;
assert(render({...sample,planning_trace:[legacyRisk]}).includes('模型代价与风险说明 · 原文'));
console.log('Observed costs, model risk hypotheses and required verification stay distinct, attributed and escaped');

const typedRisk=structuredClone(riskTurn);typedRisk.risk_review.version='observed-risk-boundary-v2';
typedRisk.risk_review.unverified[0].verification_origin='actual_input_and_model_selected_evidence_ids';
typedRisk.risk_review.unverified[0].verification_needed='本轮已提供：新增过饱和路段明细；需要另行取得证据：动态排队或回堵证据';
const typedRiskHtml=render({...sample,planning_trace:[typedRisk]});
for(const t of ['验证所需证据','模型选择类型，提供情况来自本轮输入','本轮已提供：新增过饱和路段明细',
  '需要另行取得证据：动态排队或回堵证据'])assert(typedRiskHtml.includes(t),t);
assert(!typedRiskHtml.includes('还需怎样验证 · 模型原文'));
assert(riskHtml.includes('还需怎样验证 · 模型原文'));
console.log('Evidence availability is attributed to the actual input, while historical model wording stays original');

typedRisk.risk_review.observed_detail_coverage=[{ref:'j:parking_1',text:'已提供4条／总计4条（完整）',provided:4,total:4,complete:true}];
typedRisk.risk_review.unverified[0].hypothesis='原模型误称只是部分节选，保留此原话。';
const coverageHtml=render({...sample,planning_trace:[typedRisk]});
for(const t of ['该轮模型实际收到的新增过饱和明细','已提供4条／总计4条（完整）','原模型误称只是部分节选',
  '不是动态排队或回堵证据'])assert(coverageHtml.includes(t),t);
console.log('Exact input coverage is displayed beside unmodified model claims without certifying or rewriting them');
