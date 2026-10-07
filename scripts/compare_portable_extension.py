"""Read-only comparison of equivalent full extension experiments across runtimes."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_experiment import verify
from traffic_agent.archive import digest
from traffic_agent.data import OUTPUTS, input_fingerprints, numeric, save_json
from traffic_agent.oneway import FLOW_FILES
from traffic_agent.runner import now
from traffic_agent.task import Task


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def array_difference(left, right):
    differences = [abs(a-b) for a,b in zip(left,right)]
    return dict(left_rows=len(left),right_rows=len(right),exact=left==right,
                differing_rows=sum(a!=b for a,b in zip(left,right))+abs(len(left)-len(right)),
                max_paired_absolute_difference=max(differences,default=0))


def align(left,right):
    """Reject unmatched policy/input controls before making same-run claims."""
    a,b=(asdict(Task.from_dict(s['task'])) for s in (left,right))
    for key in ('request','recommendation_version'):
        a.pop(key);b.pop(key)
    if a != b:
        raise ValueError('除了请求文本与推荐版本以外，任务参数不相同')
    groups=[]
    for state in (left,right):
        indexed={r['name']:r for r in state['runs']}
        if len(indexed)!=len(state['runs']):raise ValueError('场景名称重复')
        groups.append(indexed)
    if groups[0].keys()!=groups[1].keys():raise ValueError('完整场景集合不同')
    pairs=[]
    for name,old in groups[0].items():
        new=groups[1][name]
        if old['changes']!=new['changes'] or old['demand_factor']!=new['demand_factor']:
            raise ValueError('同名场景的实际措施或需求不一致')
        pairs.append((old,new))
    return pairs


def compare(left,right,output):
    left,right,output=(p.resolve() for p in (left,right,output))
    if output==left or output==right or output.is_relative_to(left) or output.is_relative_to(right):
        raise ValueError('报告不能写入原实验目录')
    if output.exists():raise ValueError('拒绝覆盖已有比较结果')
    states=[load(p/'state.json') for p in (left,right)]
    pairs=align(*states)
    if not (right/'archive_manifest.json').exists():raise ValueError('新的便携实验必须先完成封存')
    audits=[verify(p,archived=(p/'archive_manifest.json').exists(),write=False) for p in (left,right)]
    if load(left/'scopes.json')!=load(right/'scopes.json'):raise ValueError('固定评价范围不同')
    records=[]
    for old,new in pairs:
        oldpath,newpath=left/old['scenario'],right/new['scenario']
        if input_fingerprints(oldpath)!=input_fingerprints(newpath):
            raise ValueError(f"{old['name']}输入字节不同，不能当作等条件复跑")
        arrays={}
        for filename in list(OUTPUTS.values())+list(FLOW_FILES.values()):
            paths=[p/'result'/filename for p in (oldpath,newpath)]
            arrays[filename]=dict(array_difference(*(numeric(p) for p in paths)),
                                  left_sha256=digest(paths[0]),right_sha256=digest(paths[1]))
        records.append(dict(name=old['name'],demand_factor=old['demand_factor'],
                            inputs_identical=True,arrays=arrays))
    exact=all(a['exact'] for r in records for a in r['arrays'].values())
    report=dict(recorded_at=now(),status='compared',new_model_calls=0,
                left=str(left),right=str(right),left_state_sha256=digest(left/'state.json'),
                right_state_sha256=digest(right/'state.json'),right_archive_sha256=digest(right/'archive_manifest.json'),
                verification_passed=all(a['passed'] for a in audits),scopes_identical=True,
                completed_scenarios=len(records),compared_arrays=sum(len(r['arrays']) for r in records),
                all_arrays_numerically_exact=exact,
                recommendation_versions=[s['task'].get('recommendation_version','legacy-v1') for s in states],
                conditional_recommendations=[s['recommendation_by_car_demand'] for s in states],
                explanation=('输入与九类结果数组完全相同；推荐差异应与预先声明的规则版本分别讨论。' if exact else
                             '相同输入的结果存在差异，不能把推荐变化只归因于推荐规则。'),
                limits='同一台物理电脑及同一赛事数据库的完整流程复现，不是异机、跨数据集或现实标定证明。',
                records=records)
    output.mkdir(parents=True)
    for label,value in zip(('left_audit','right_audit'),audits):save_json(output/(label+'.json'),value)
    save_json(output/'comparison.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('left','right','output'):parser.add_argument(name,type=Path)
    args=parser.parse_args();compare(args.left,args.right,args.output)
