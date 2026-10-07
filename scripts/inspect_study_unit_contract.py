"""Read-only comparison of a rejected draft's old and current budget permissions."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.natural_plan import limits
from traffic_agent.planning_budget import initial_costs,verify_initial_costs


def inspect(directory):
    directory=Path(directory);paths=[directory/'acceptance.json',directory/'plan.json']
    original={str(p):p.read_bytes() for p in paths}
    accepted,plan=[json.loads(original[str(p)]) for p in paths]
    if accepted['native_launched'] or plan['status']!='needs_input':raise ValueError('需要未启动原生仿真的原始失败提议')
    previous=plan['budget_check'];verify_initial_costs(previous,accepted['request'])
    tasks=[dict(g['conditions'],request=accepted['request']) for g in previous['groups']]
    entries=[{k:g[k] for k in ('study','title','target_ref')} for g in previous['groups']]
    current=initial_costs(tasks,entries,limits(accepted['request']),previous['model']['used'],accepted['request'])
    assert current['native']==previous['native'] and current['model']==previous['model']
    assert all(p.read_bytes()==original[str(p)] for p in paths)
    return dict(kind='offline_study_unit_permission_inspection',original_status=accepted['status'],
        original_sha256={p:hashlib.sha256(b).hexdigest() for p,b in original.items()},
        original_budget_check=previous,current_budget_permissions=current,original_bytes_unchanged=True,
        corrected_model_reply_generated=False,paid_calls=0,new_native_scenes=0,
        meaning='仅计算未执行提议的修正权限；仍是8场景提议，未生成修正答复、未重启任务，不构成闭环通过。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=inspect(args.case)
    with args.output.open('x',encoding='utf-8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(original_status=result['original_status'],native_cost=result['current_budget_permissions']['native'],
        may_omit=[g['may_omit'] for g in result['current_budget_permissions']['groups']],paid_calls=0)))
