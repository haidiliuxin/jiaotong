const assert=require('node:assert/strict');
const {preferredRun,color,statuses}=require('../traffic_agent/static/map_view.js');
const runs=[{name:'baseline',changes:[]},{name:'bad',changes:[{parking:2}]},{name:'best',changes:[{parking:1}]}];
assert.equal(preferredRun(runs,'best'),'best');
assert.equal(preferredRun(runs,'baseline'),'best'); // inspect a rejected candidate, not baseline against itself
assert.equal(preferredRun(runs.slice(0,1),'baseline'),'baseline');
const link={before:[10,100,1.9],after:[35,120,.9],status:{before:'severe',after:'near_capacity'},delta:{speed_kmh:25,time_pcu_h:-2}};
assert.notEqual(color(link,0,'vc'),color(link,1,'vc'));
assert.equal(color(link,0,'impact'),'#a4adb5');
assert.equal(color(link,1,'impact'),'#238879');
assert.equal(color({...link,delta:{speed_kmh:-5,time_pcu_h:2}},1,'change'),'#c45351');
assert.equal(statuses.severe.label,'严重过饱和');
console.log('Map default selection, paired status colors and signed changes passed');
const {vcLabel}=require('../traffic_agent/static/map_view.js');
assert.equal(vcLabel(1.00092),'1.00092');assert.equal(vcLabel(.999983),'0.99998');
assert.equal(vcLabel(1.00000001),'略高于1');assert.equal(vcLabel(.999999999),'略低于1');
assert.equal(vcLabel(1),'1');assert.equal(vcLabel(null),'—');
console.log('Near-saturation labels preserve the measured side of the V/C=1 boundary');
const {caption}=require('../traffic_agent/static/map_view.js');
const paired={kind:'policy',comparable:true,changed_links:31,reason:'旧说明'};
assert(caption(paired,'change',32060).includes('颜色表示速度增减；基准图是零变化参照'));
assert(!caption(paired,'change',32060).includes('饱和状态'));
assert(caption(paired,'vc',32060).includes('状态相同不代表速度没有变化'));
assert.equal(caption({...paired,kind:'repeat',reason:'同输入复跑；不是治理收益'},'speed',32),'同输入复跑；不是治理收益 指标有变化 31 / 32 条。');
assert.equal(caption({kind:'diagnosis',comparable:false,reason:'只有诊断'},'neutral',32),'只有诊断');
console.log('Map caption matches selected metric and preserves diagnosis/repeat limitations');
const {motorClosure}=require('../traffic_agent/static/map_view.js');
const closed={...link,motor_closure:{response:'passed',closed_direction:[1,2]}};
assert.equal(motorClosure(closed,0),null); // The baseline must retain its original access.
assert.equal(motorClosure(closed,1).label,'机动车方向禁行');
assert.equal(motorClosure(closed,1).response,'四类机动车响应已核对');
assert.equal(motorClosure({...closed,motor_closure:{response:'failed'}},1).response,'禁行响应未通过');
assert.equal(motorClosure({...closed,motor_closure:{response:'not_checked'}},1).response,'禁行响应待核对');
assert.equal(motorClosure({...link,after:[50,0,0]},1),null); // Zero flow is not a closure.
assert.equal(color(closed,1,'speed'),color(link,1,'speed')); // Keep raw metric colors.
assert(caption({...paired,motor_closed_links:1},'speed',32).includes('汇总速度与V/C不表示允许通行'));
console.log('Executed motor closures are candidate-only overlays; missing or failed response remains explicit');
const {extreme}=require('../traffic_agent/static/map_view.js');
const changes=[{id:1,delta:{time_pcu_h:-1}},{id:2,delta:{time_pcu_h:-7}},
  {id:3,delta:{time_pcu_h:2}},{id:4,delta:{time_pcu_h:9}}];
assert.equal(extreme(changes,'improved').id,2);
assert.equal(extreme(changes,'worsened').id,4);
assert.equal(extreme(changes.slice(0,2),'worsened'),null);
assert.equal(changes[0].id,1); // Sorting the focus list never reorders source map data.
console.log('Largest measured improvement and worsening are directly locatable');
const {policyOutcome}=require('../traffic_agent/static/map_view.js');
const executed={execution:{status:'completed'},metrics:{core:{weighted_speed_kmh:12.18}},
  comparison:{eligible:true,delta:{core:{running_time_change_pct:-23.3397},network:{oversaturated_km_change:.631}}}};
const outcome=policyOutcome({target_speed_kmh:25},executed);
assert.equal(outcome.target_met,false);
assert.equal(outcome.eligible,true); // Target speed is distinct from the frozen recommendation gate.
assert.equal(outcome.core_time_change_pct,-23.3397);
assert.equal(outcome.network_oversaturated_net_km,.631);
assert.equal(outcome.network_oversaturated_gross_km,null); // No invented gross increase from a net delta.
assert.equal(policyOutcome({target_speed_kmh:25},{...executed,execution:{status:'running'}}),null);
assert.equal(policyOutcome({},{execution:{status:'completed'}}),null);
assert.equal(policyOutcome({},executed).target_met,null); // Missing targets must not be treated as achieved.
console.log('Measured improvements, speed targets, eligibility and net/gross risks remain separate');
