"""Compare sealed source and moved-bundle parking jobs without model calls."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS
from traffic_agent.map_data import scenario_path
from verify_experiment import verify


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def compare(source, portable):
    source, portable = source.resolve(), portable.resolve()
    audits = [verify(path, archived=True, write=False) for path in (source, portable)]
    if not all(a['passed'] and a['completed_scenarios'] == 4 and a['module_executions'] == 28 for a in audits):
        raise ValueError('两份任务均须通过4场景、28调用的封存核验')
    a, b = (load(path/'state.json') for path in (source, portable))
    if any(state['status'] != 'completed' for state in (a, b)):
        raise ValueError('任务未完成')
    if a['task'] != b['task'] or a['task'].get('max_new_network_oversaturated_km') != 0.1:
        raise ValueError('任务卡或安全门槛不一致')
    for name in ('source_inputs.json', 'scopes.json'):
        if load(source/name) != load(portable/name):
            raise ValueError(f'{name}不一致')
    if load(source/'diagnosis.json')['selected'] != load(portable/'diagnosis.json')['selected']:
        raise ValueError('基准诊断选区不一致')
    if a['recommendation'] != b['recommendation']:
        raise ValueError('最终推荐不一致')
    if len(a['runs']) != len(b['runs']):
        raise ValueError('场景数不一致')
    arrays = {}
    for left, right in zip(a['runs'], b['runs']):
        if left['name'] != right['name'] or left['changes'] != right['changes']:
            raise ValueError('场景名称或措施不一致')
        if left.get('comparison') != right.get('comparison'):
            raise ValueError('保存的约束与指标比较不一致')
        matches = {name: digest(scenario_path(source, left)/'result'/filename) ==
                         digest(scenario_path(portable, right)/'result'/filename)
                   for name, filename in OUTPUTS.items()}
        if not all(matches.values()):
            raise ValueError(f"{left['name']}原始路段数组不一致")
        arrays[left['name']] = matches
    return dict(kind='same_machine_moved_bundle_exact_replay', new_model_calls=0,
                source_job=a['id'], portable_job=b['id'], task_exact=True, source_inputs_exact=True,
                scope_exact=True, selected_area_exact=True, recommendation=a['recommendation'],
                actions_exact=True, comparisons_exact=True, raw_result_arrays_exact=arrays,
                source_archive_sha256=digest(source/'archive_manifest.json'),
                portable_archive_sha256=digest(portable/'archive_manifest.json'),
                limitation='同机另目录同输入复现；不能证明异机可运行、跨需求稳健或官方评分提高')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_job', type=Path)
    parser.add_argument('portable_job', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.source_job, args.portable_job)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(source=report['source_job'], portable=report['portable_job'],
                          scenarios=list(report['raw_result_arrays_exact'])), ensure_ascii=False))
