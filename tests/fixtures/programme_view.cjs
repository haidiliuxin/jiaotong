// Deliberately synthetic UI data; never a saved native job or model reply.
const groups=[
  {id:'study_1',job_id:'LOCAL_ONEWAY_FIXTURE',title:'停车与单向对照',engine:'TranStar Vehicle1＋道路禁行适配'},
  {id:'study_2',job_id:'LOCAL_TURN_FIXTURE',title:'停车与禁转对照',engine:'TranStar Vehicle1＋转向状态图适配'}
];
const findings=groups.map(g=>({id:g.id+'.factorial',question:g.title+'：单项与组合的取舍',status:'measured',answer:'合成数据：组合未通过邻区约束。',evidence_refs:[g.job_id+':baseline',g.job_id+':combined'],
  measurements:[{factor:1,baseline:'baseline',interaction_pcu_h:5,cells:[
    {title:'停车',run:'parking',eligible:true,delta:{network:{running_time_change_pct:-10},neighbor:{running_time_change_pct:0}}},
    {title:g.id==='study_1'?'单向':'禁转',run:'control',eligible:false,control_response:false,delta:{network:{running_time_change_pct:0},neighbor:{running_time_change_pct:0}}},
    {title:g.id==='study_1'?'停车＋单向':'停车＋禁转',run:'combined',eligible:false,control_response:true,delta:{network:{running_time_change_pct:-5},neighbor:{running_time_change_pct:8}}}
  ]}]}));
module.exports={version:'natural-strategy-plan-v5',goal:'多策略研究的整体复核',reason:'此样本仅检查展示，不作真实效果证明。',budget:8,allocated_runs:8,
  experiments:groups.map(g=>({title:g.title,task:{road_name:'本地合成道路'},runs:4,target_method:'界面样例',rationale:'仅用于展示检查'})),
  progress:groups.map(g=>({job_id:g.job_id,status:'completed',runs:4,run_count:4})),assessment:[],planning_trace:[],programme_review:{complete:true,reviewed:true,
    summary:'合成示例：两组组合措施都出现邻区代价，不能因为措施更多就推荐。单项停车通过样例约束；控制响应失败需要保留。',
    evidence:{groups:groups.map(g=>({...g,execution_complete:true,finding_ids:[g.id+'.factorial']})),findings,
      scope:'各组采用各自的新基准。不能混合不同适配器的收益，也不能推断联合实施效果。'},
    hypotheses:[{id:'H1',hypothesis:'叠加措施可能更有效'}],
    updates:[{hypothesis_id:'H1',judgment:'contradicted',finding_ids:findings.map(f=>f.id),explanation:'合成检查：组合的邻区耗时增加 8%，未通过样例约束。'}],unanswered:[]}};
