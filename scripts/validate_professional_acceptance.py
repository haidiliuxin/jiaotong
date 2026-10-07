"""Frozen expanded acceptance, offline only. No secret or model access."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from traffic_agent.knowledge_retrieval import retrieve, requested_topics, verify, VERSION

FIXTURE=ROOT/'tests/fixtures/professional_acceptance_v1.json'
FROZEN_SHA='6127f8718c10461e2d24e18c71d4b012c5a2ef4436169bb71576e5030e3ad323'


def evaluate(version):
    raw=FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=FROZEN_SHA:raise ValueError('冻结验收题已改变，不能沿用本次验收口径')
    cases=json.loads(raw)['retrieval_cases'];rows=[]
    for case in cases:
        bundle=retrieve(case['request'],'initial',case.get('scenario'),version=version)
        topics=sorted(requested_topics(case['request'],version))
        hits=[h['id'] for h in bundle['hits']]
        passed=(topics==case['topics'] if 'topics' in case else case['expected_hit'] in hits)
        replay=verify(bundle,case['request'],'initial',case.get('scenario'))
        rows.append(dict(id=case['id'],passed=passed,topics=topics,hits=hits,replay=replay))
    return dict(version=version,fixture_sha256=FROZEN_SHA,total=len(rows),passed=sum(r['passed'] for r in rows),
        failed_ids=[r['id'] for r in rows if not r['passed']],cases=rows,
        boundary=json.loads(raw)['boundary'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    report=dict(previous=evaluate('curated-knowledge-rag-v2'),current=evaluate(VERSION))
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:{f:v[f] for f in ('version','total','passed','failed_ids')} for k,v in report.items()},ensure_ascii=False))
    return 0 if report['current']['passed']==report['current']['total'] else 1


if __name__=='__main__':raise SystemExit(main())
