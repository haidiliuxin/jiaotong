"""Inspect a preserved rejected reply against measured-reference tool permissions.

No network request, native execution, reply correction, or historical write.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.research_evidence import REFERENCE_SCHEMA,hypothesis_update_schema,validate_updates
from traffic_agent.planning_response import ResponseValidationError


def inspect(path):
    path=Path(path);original=path.read_bytes();record=json.loads(original)
    turn=record['planning_trace'][-1]
    if (record['status']!='incomplete' or turn['validation']['code']!='unmeasured_finding'
            or turn.get('proposal_capture',{}).get('excerpted') is not False):
        raise ValueError('需要保留完整公开提议的原始引用失败记录')
    context=turn['input']['context'];proposal=turn['proposal_excerpt'];checks=[]
    for field,hypotheses,evidence in [('hypothesis_updates','initial_hypotheses','research_evidence'),
                                      ('programme_updates','programme_hypotheses','programme_evidence')]:
        schema=hypothesis_update_schema(context[hypotheses],context[evidence],True)
        refs=schema['items']['properties']['finding_ids']
        allowed=refs['items'].get('enum',[])
        gaps=[f['id'] for f in context[evidence]['findings'] if f['id'] not in allowed]
        rejected=[]
        for update in proposal[field]:
            forbidden=[k for k in update['finding_ids'] if k not in allowed]
            if forbidden:rejected.append(dict(hypothesis_id=update['hypothesis_id'],finding_ids=forbidden))
        try:
            validate_updates(proposal[field],context[hypotheses],context[evidence],True,measured_only=True)
            outcome='structurally_valid'
        except ResponseValidationError as exc:outcome=exc.code
        checks.append(dict(field=field,allowed_measured_ids=allowed,gap_ids_retained_in_context=gaps,
                           forbidden_original_references=rejected,original_reply_check=outcome))
    if checks[-1]['original_reply_check']!='unmeasured_finding':raise ValueError('原始引用错误未保持拒绝')
    if path.read_bytes()!=original:raise ValueError('历史原件发生变化')
    return dict(kind='offline_hypothesis_reference_inspection',schema=REFERENCE_SCHEMA,
        original_job=record['job_id'],original_status=record['status'],original_sha256=hashlib.sha256(original).hexdigest(),
        checks=checks,original_bytes_unchanged=True,corrected_reply_generated=False,paid_calls=0,new_native_scenes=0,
        meaning='只核查新工具的已测引用列表；未测缺口仍保留，原提议仍被拒。不是模型修正或完整真实验收。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dispatch',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=inspect(args.dispatch)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(original_status=result['original_status'],paid_calls=0,original_reply_still_rejected=True)))
