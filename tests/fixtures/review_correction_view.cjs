// Synthetic UI example only; never a saved native job or a provider response.
module.exports={version:'natural-strategy-plan-v5',goal:'最终复核修正 · 离线界面样例',reason:'仅验证显示状态，不是交通实测或真实模型回复。',budget:6,allocated_runs:6,
  experiments:[{title:'离线复核样例',task:{road_name:'合成道路'},runs:6,target_method:'界面检查',rationale:'当前画面不发起模型请求。'}],
  progress:[{job_id:'OFFLINE_REVIEW',status:'completed',run_count:6,runs:6}],
  model_budget:{used:6,limit:6,known_tokens:600},planning_trace:[{
    ordinal:4,job_id:'OFFLINE_REVIEW',kind:'route',status:'accepted',action:'stop',usage:{tokens:100},
    review_correction:{first_attempt:{status:'rejected',usage:{tokens:80},validation:{code:'unmeasured_finding',message:'判断依据只能引用已测检验；未测结果是证据缺口，应写入解释，不能混入finding_ids。'},
      proposal_excerpt:{action:'stop',programme_updates:[{hypothesis_id:'H1',judgment:'contradicted',finding_ids:['study_1.benefit','study_1.strength'],explanation:'界面样例：把未测强度误放入了实测依据。'}]}}}
  }]};
