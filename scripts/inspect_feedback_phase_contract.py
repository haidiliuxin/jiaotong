"""Check stage permissions against sealed failed inputs, without paid/native I/O.

The selection-only reply below is an explicit local structural fixture. It is
not a model-generated correction, a continuation, or new traffic evidence.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.feedback_planner import PlanningSession,REVIEW_PROTOCOL,digest


def inspect(snapshot):
    snapshot=Path(snapshot)
    before=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    with zipfile.ZipFile(snapshot) as archive:
        manifest=json.loads(archive.read('incomplete_snapshot_manifest.json'))
        owner=manifest['job_ids'][0]
        name=f'智能体实验/dispatches/{owner}.json'
        data=archive.read(name);expected=manifest['files'][name]
        if len(data)!=expected['bytes'] or hashlib.sha256(data).hexdigest()!=expected['sha256']:
            raise ValueError('封存调度与原清单不符')
        original=json.loads(data)
    turn=original['planning_trace'][-1]
    if (original['status']!='incomplete' or turn['kind']!='oneway_target'
            or turn['validation']['code']!='premature_programme_review'
            or turn.get('proposal_capture',{}).get('excerpted') is not False):
        raise ValueError('需要具有完整公开提议的原始选点阶段失败')
    payload=turn['input'];proposal=turn['proposal_excerpt']
    if ('stop' in payload['allowed_actions'] or proposal['action'] not in payload['allowed_actions']
            or not payload['context']['programme_final_group']
            or not proposal['programme_updates'] or not proposal['programme_summary']):
        raise ValueError('原始失败不符合本项阶段权限边界')
    variants=[]
    for original_reply in (True,False):
        calls=[]
        class LocalFixture:
            def request(self,instructions,inputs,tools,choice):
                current=json.loads(inputs[0]['content']);calls.append(current)
                properties=tools[0]['parameters']['properties']
                if any(k in properties for k in ('programme_updates','programme_summary')):
                    raise AssertionError('Selection-only tool still exposes final programme fields')
                if current['observations']!=payload['observations'] or current['allowed_actions']!=payload['allowed_actions']:
                    raise AssertionError('Preserved observations or menu changed')
                if current['context']['programme_evidence']!=payload['context']['programme_evidence']:
                    raise AssertionError('Future evidence introduced')
                if original_reply:value=copy.deepcopy(proposal)
                else:
                    value=dict(action=proposal['action'],summary='离线结构样例：只使用封存选点菜单，不生成新的模型结论。',
                        reason='核对选路阶段不要求整个任务复核；没有调用远程服务。',
                        risk='原任务第二组治理没有执行，原记录仍未完成。',
                        next_check='后续真实完整研究仍须完成约定的单项与组合实测。',evidence_refs=payload['evidence_refs'][-1:])
                    for field in ('cost_acknowledgments','hypothesis_updates'):
                        if field in properties:value[field]=[]
                return dict(calls=[dict(name='choose_next_experiment',arguments=json.dumps(value,ensure_ascii=False))],
                    metadata=dict(tokens=None,response_model='OFFLINE_STRUCTURAL_FIXTURE'))
        session=PlanningSession(LocalFixture(),payload['request'])
        session.used=payload['model_budget']['used']
        result=session.decide(turn['kind'],turn['job_id'],payload['observations'],payload['allowed_actions'],
                              payload['context'],allow_stop=False)
        if len(calls)!=1:raise AssertionError('Unexpected local fixture requests')
        if result['status']!=('rejected' if original_reply else 'accepted'):
            raise AssertionError('Stage validation differs from expected boundary')
        if original_reply and (result['validation']['code']!='argument_fields' or 'selected' in result):
            raise AssertionError('Original premature conclusion was accepted')
        variants.append(dict(kind='original_rejected_proposal' if original_reply else 'explicit_selection_only_fixture',
            status=result['status'],validation=result['validation'],
            permissions=result['input']['context']['review_permissions'],
            observations_unchanged=True,allowed_actions_unchanged=True,
            observations_sha256=digest(result['input']['observations']),
            programme_evidence_unchanged=True,future_results_supplied=False))
    if hashlib.sha256(snapshot.read_bytes()).hexdigest()!=before:raise AssertionError('Original snapshot changed')
    return dict(kind='offline_feedback_phase_contract_check_v1',review_protocol=REVIEW_PROTOCOL,
        original_source_commit=manifest['source_commit'],original_job=owner,original_status=original['status'],
        original_rejection=turn['validation']['code'],original_snapshot_sha256=before,original_snapshot_unchanged=True,
        paid_calls=0,new_native_scenes=0,local_fixture_calls=2,variants=variants,
        meaning='本地结构检查，不是新的模型回复或交通结果。原提议仍被拒绝；选路样例仅验证当前工具字段，原研究仍未完成。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('检查输出已存在，不覆盖证据')
    result=inspect(args.snapshot)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(passed=True,paid_calls=0,new_native_scenes=0,original_status=result['original_status']),ensure_ascii=False))


if __name__=='__main__':main()
