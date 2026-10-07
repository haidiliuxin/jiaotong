const assert=require('node:assert/strict');
const {render}=require('../traffic_agent/static/choice_view.js');
const road={index:7,name:'测试路',from_node:12,to_node:34,speed_kmh:8.125,vc:1.6,parking:3,flow_pcu_h:1700};
const option={title:'测试路 12→34',closed_direction:[12,34],roads:[road],baseline_car_flow:946.55,topology:{eligible:true,bus_routes_checked:3}};
const payload={allowed_actions:{selected:option,alternative:{...option,title:'备选方向'}},
  observations:[{ref:'synthetic:baseline',run:'baseline',demand_factor:1,changes:[]},
    {ref:'synthetic:negative',run:'negative',demand_factor:1,changes:[{}],eligible:false,
      delta:{core:{running_time_change_pct:-54},neighbor:{running_time_change_pct:.00001497}},
      new_neighbor_oversaturated_km:.056,violations:['周边代价超限']}],
  context:{choice_observations:{sources:[
    {id:'chosen',kind:'target',ref:'synthetic:baseline',action:'selected'},
    {id:'other',kind:'target',ref:'synthetic:baseline',action:'alternative'},
    {id:'prior',kind:'trial',ref:'synthetic:negative'}]}}};
const entry={status:'accepted',ordinal:3,job_id:'synthetic',action:'selected',summary:'模型原文只是一种解释',reason:'未来收益待验证',input:payload,
  selection_basis:{observed_ids:['chosen','other','prior'],working_hypothesis:'假设需要检验',
    disconfirming_result:'核心未改善则放弃',alternative_action:'alternative',alternative_tradeoff:'效果未知，暂缓实测'}};
const html=render({planning_trace:[entry]});
for(const text of ['为什么安排这次试验','946.55 veh/h','速度 km/h','8.13','1.60','待验证假设','什么结果会否定',
  '本轮暂缓：备选方向','备选效果尚未实测','-54.00%','0.00001497','0.056 km','约束未通过','周边代价超限',
  'data-region-job="synthetic"','data-region-run="baseline"','data-region-indices="7"','data-observation-ref="synthetic:negative"',
  '所选候选的基准','备选候选的基准','上一轮实测反馈','拟关闭','可达不代表治理有效','以上为模型解释']){
  // Tiny nonzero costs must remain nonzero; precision need not invent digits.
  if(text==='0.00001497')assert(html.includes('0.0000150%')||html.includes('0.00001497%'));
  else assert(html.includes(text),text);
}
assert(!html.includes('946.55 pcu/h'));
const separated=structuredClone(entry);
separated.selection_basis.observed_ids=['chosen','prior'];
separated.selection_basis.alternative_observed_id='other';
const separatedHtml=render({planning_trace:[separated]});
assert(separatedHtml.includes('暂缓备选所据观测'));
assert(separatedHtml.includes('备选候选的基准'));
assert(separatedHtml.includes('上一轮实测反馈'));
const unified=structuredClone(separated);
unified.selection_basis.observed_ids=['chosen'];
unified.evidence_refs=['synthetic:baseline','synthetic:negative'];
unified.input.context.choice_observations.recent_trial='prior';
const unifiedHtml=render({planning_trace:[unified]});
assert(unifiedHtml.includes('本轮引用的最近配对反馈'));
assert(unifiedHtml.includes('0.056 km'));
unified.evidence_refs=['synthetic:baseline'];
assert(!render({planning_trace:[unified]}).includes('本轮引用的最近配对反馈'));
const related=structuredClone(entry);
related.input.context.choice_observations.sources[2].related_actions=['alternative'];
const relatedHtml=render({planning_trace:[related]});
assert(relatedHtml.includes('上一轮实测反馈 · 与暂缓备选同区域'));
assert(relatedHtml.includes('备选效果尚未实测'));
assert.equal(render({planning_trace:[{...entry,status:'rejected'}]}),'');
assert.equal(render({planning_trace:[{status:'accepted',reason:'旧记录'}]}),'');
assert.equal(render({}),'');
const missing=structuredClone(entry);
missing.input.allowed_actions.selected.baseline_car_flow=null;
missing.input.allowed_actions.selected.roads[0].speed_kmh=null;
missing.selection_basis.alternative_action=null;
const unavailable=render({planning_trace:[missing]});
assert(unavailable.includes('未提供'));assert(unavailable.includes('本轮没有其他合法候选'));
assert(!unavailable.includes('0.00 veh/h'));
const escaped=structuredClone(entry);
escaped.input.allowed_actions.selected.title='<script>alert(1)</script>';
escaped.selection_basis.working_hypothesis='<img src=x onerror=alert(2)>';
escaped.selection_basis.alternative_tradeoff='" onclick="alert(3)';
const safe=render({planning_trace:[escaped]});
assert(safe.includes('&lt;script&gt;'));assert(safe.includes('&lt;img'));
assert(!safe.includes('<script>'));assert(!safe.includes('<img'));assert(!safe.includes('" onclick="'));
const area=structuredClone(entry);area.input.context.choice_observations.sources=[{id:'chosen',kind:'area',ref:'synthetic:baseline',region:'a'}];
area.input.observations[0].spatial={regions:[{id:'a',title:'已测区域',weighted_speed_kmh:7.8,roads:[road]}]};
area.selection_basis.observed_ids=['chosen'];
assert(render({planning_trace:[area]}).includes('加权速度 7.80 km/h'));
area.input.context.choice_observations.sources[0]={id:'chosen',kind:'road',ref:'synthetic:baseline',direction:[12,34]};
area.input.observations[0].core_roads=[road,{...road,index:8,from_node:99,speed_kmh:888}];
const directed=render({planning_trace:[area]});assert(directed.includes('12→34'));assert(!directed.includes('888.00'));
const integrated=require('../traffic_agent/static/planner_view.js').render({version:'natural-strategy-plan-v5',
  goal:'合成流程',assessment:[{hypothesis:'待验证',evidence_needed:'新观测'}],experiments:[],planning_trace:[entry]});
assert(integrated.includes('为什么安排这次试验'));
const authored=structuredClone(escaped);
authored.input.context.choice_rationale_protocol='choice-rationale-v1';
authored.reason='';authored.selection_reason='待检验假设（尚未证实）：'+authored.selection_basis.working_hypothesis;
const authoredHtml=render({planning_trace:[authored]});
assert(authoredHtml.includes('研究假设、否证条件和备选取舍来自本轮模型'));
assert(authoredHtml.includes('&lt;img'));assert(!authoredHtml.includes('<img'));
assert(!authoredHtml.includes('模型原文取舍'));
assert(html.includes('模型原文取舍')); // Old recorded prose stays available.
const authoredPanel=require('../traffic_agent/static/planner_view.js').render({version:'natural-strategy-plan-v10',status:'running',
  goal:'合成流程',assessment:[{hypothesis:'待检验',evidence_needed:'新观测'}],experiments:[],planning_trace:[authored],
  job_id:'synthetic',active_job:'synthetic',progress:[{job_id:'synthetic',status:'running'}]});
for(const text of ['本轮研究思路','待检验假设（尚未证实）','&lt;img','下一步理由'])assert(authoredPanel.includes(text),text);
assert(!authoredPanel.includes('<img'));
console.log('Selection view: original units/values, map references, negative feedback, alternatives, escaping and legacy boundaries passed');

const comparisonEntry=structuredClone(authored);
comparisonEntry.input.context.choice_observations.recent_comparisons={reference_ref:'synthetic:negative',reference_demand_factor:1,
  groups:[{actions:['selected'],interpretation:'换到不同治理方向；等级减弱。位置与等级同时改变，不能单独归因于其中一项。'},
    {actions:['alternative'],interpretation:'治理方向相同；等级减弱。实际收益仍需实跑<script>'}],scope:'仅比较参数，不预测效果。'};
const comparisonHtml=render({planning_trace:[comparisonEntry]});
for(const text of ['这次对照改变了什么 · 由参数生成','暂缓备选','不能单独归因','仍需实跑&lt;script&gt;',
  '仅比较参数，不预测效果。','data-observation-ref="synthetic:negative"'])assert(comparisonHtml.includes(text),text);
assert(!comparisonHtml.includes('<script>'));assert(!authoredHtml.includes('这次对照改变了什么'));
comparisonEntry.candidate_comparison={reference_direction_count:3,chosen:{direction_count:2},alternative:{direction_count:1}};
comparisonEntry.selection_basis.working_hypothesis='原模型把所选两条误写为三条，原话保留。';
const countsHtml=render({planning_trace:[comparisonEntry]});
for(const t of ['实际治理3条方向','2条治理方向','1条治理方向','原模型把所选两条误写为三条'])assert(countsHtml.includes(t),t);
console.log('Parameter comparisons show the selected and deferred design separately without inventing effects or altering historical views');
comparisonEntry.input.context.choice_observations.recent_comparisons.groups[0].demand_summary='计划需求×1；参照需求×1。计划包含参照需求；尚无该候选的已完成结果。<script>';
comparisonEntry.selection_basis.working_hypothesis='原模型误称换区候选使用不同需求，原话保留。';
const demandHtml=render({planning_trace:[comparisonEntry]});
for(const t of ['计划需求×1；参照需求×1','尚无该候选的已完成结果','原模型误称换区候选','&lt;script&gt;'])assert(demandHtml.includes(t),t);
assert(!demandHtml.includes('<script>'));
const group=comparisonEntry.input.context.choice_observations.recent_comparisons.groups[0];
const summary=group.demand_summary;delete group.demand_summary;group.demand_design_id='demand_1';
comparisonEntry.input.context.choice_observations.recent_comparisons.demand_designs=[{id:'demand_1',summary}];
assert.equal(render({planning_trace:[comparisonEntry]}),demandHtml);
