"""Read-only post-hoc review; never updates a sealed job or calls a model."""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.archive import verify_archive
from traffic_agent.conclusion_review import assess_state
from traffic_agent.planning_audit import verify_planning


def review(job, output):
    job=Path(job).resolve();output=Path(output).resolve()
    if output.is_relative_to(job) or output.exists():
        raise ValueError('核对结果必须写入原件目录之外的新文件')
    archive=verify_archive(job)
    state_path=job/'state.json'
    before=state_path.read_bytes()
    state=json.loads(before)
    diagnosis=json.loads((job/'diagnosis.json').read_text(encoding='utf-8'))
    audit=verify_planning(state,diagnosis)
    result=dict(created=datetime.now(timezone.utc).isoformat(),job=job.name,
                kind='posthoc_readonly_conclusion_review',new_model_calls=0,new_native_runs=0,
                recorded_status=state['status'],state_sha256=hashlib.sha256(before).hexdigest(),
                archive_files=len(archive['files']),execution_audit=audit,
                conclusion_review=assess_state(state),
                meaning='读取封存原件后的独立结论核对；不是新模型回复、重新仿真或新协议线上验收。')
    assert state_path.read_bytes()==before
    verify_archive(job)
    result['original_archive_unchanged']=True
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=review(args.job,args.output)
    print(json.dumps(dict(job=result['job'],recorded_status=result['recorded_status'],
                         conclusion_status=result['conclusion_review']['status'],issues=len(result['conclusion_review']['issues']),
                         original_archive_unchanged=result['original_archive_unchanged'],new_model_calls=0,new_native_runs=0),ensure_ascii=False))
