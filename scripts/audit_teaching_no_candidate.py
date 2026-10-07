"""Read-only explanation of a preregistered teaching pilot with no eligible road."""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import PROJECT, input_fingerprints, parking_candidates, results
from traffic_agent.runner import MODULES, validate_source
from traffic_agent.study_runner import sha
from traffic_agent.task import Task


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def audit(job, registration):
    job, registration = Path(job).resolve(), Path(registration).resolve()
    state, protocol = read(job/'state.json'), read(registration/'protocol.json')
    outcome, source_inputs = read(registration/'outcome.json'), read(job/'source_inputs.json')
    if (state['status'] != outcome['status'] or state['status'] != 'no_candidate'
            or len(state['runs']) != state['run_count'] or state['run_count'] != 1
            or state['task'] != protocol['task'] or outcome['new_model_calls'] != len(MODULES)):
        raise ValueError('任务不是本协议下一次完整基准后的无候选终态')
    run = state['runs'][0]
    if run['name'] != 'baseline' or run['changes'] or run['demand_factor'] != 1:
        raise ValueError('唯一物理场景不是原需求无治理基准')
    scenario = (job/run['scenario']).resolve()
    if not scenario.is_relative_to(job/'scenarios'):
        raise ValueError('基准场景路径越出任务')
    manifest = read(job/'logs/baseline/run.json')
    if (manifest != run['execution'] or manifest['status'] != 'completed'
            or manifest['scenario'] != str(scenario)
            or tuple(x['module'] for x in manifest['modules']) != MODULES
            or any(x['status'] != 'completed' or x['returncode'] != 0 for x in manifest['modules'])):
        raise ValueError('原生七模块执行记录不完整')
    source = Path(protocol['source']).resolve()
    if (input_fingerprints(source) != protocol['source_inputs']
            or source_inputs != protocol['source_inputs']
            or input_fingerprints(scenario) != source_inputs
            or read(job/'logs/baseline/inputs_before.json') != source_inputs
            or state.get('source') != str(source)
            or state['source_validation'] != protocol['source_validation']
            or state['source_validation'] != validate_source(source)):
        raise ValueError('源输入、基准输入或软件参数不一致')
    if (datetime.fromisoformat(protocol['registered_at']) >= datetime.fromisoformat(manifest['started'])
            or (registration/'protocol.json').stat().st_mtime >= datetime.fromisoformat(manifest['started']).timestamp()):
        raise ValueError('预登记晚于首次原生模型')
    for relative, digest in protocol['code_sha256'].items():
        frozen = (registration/'code_snapshot'/relative).resolve()
        if (not frozen.is_relative_to(registration/'code_snapshot')
                or sha(frozen) != digest or sha(PROJECT/relative) != digest):
            raise ValueError(f'代码快照改变：{relative}')
    for relative, stamp in manifest['fresh_outputs'].items():
        path = scenario/relative
        if (path.stat().st_size != stamp['bytes'] or path.stat().st_mtime_ns != stamp['mtime_ns']
                or stamp['mtime_ns']/1e9 < datetime.fromisoformat(manifest['started']).timestamp()-2):
            raise ValueError(f'新输出改变：{relative}')
    rows = results(scenario)
    old_rows = results(source)
    task = Task.from_dict(state['task'])
    criteria = (
        ('纳入评价的实体路段', lambda r: r['included']),
        ('2或3类道路', lambda r: r['road_class'] in (2, 3)),
        ('原停车影响3或4级', lambda r: r['parking'] in (3, 4)),
        ('长度0.1至1.5km', lambda r: 0.1 <= r['length_km'] <= 1.5),
        ('流量至少500pcu/h', lambda r: r['flow_pcu_h'] >= 500),
        ('软件VC在1至4之间', lambda r: 1 < r['vc'] <= 4),
        ('速度低于40km/h', lambda r: r['speed_kmh'] < 40),
        ('有道路名称且非匝道', lambda r: bool(r['name']) and '匝道' not in r['name']),
        ('排除纯数字路名', lambda r: re.fullmatch(r'\d+路', r['name']) is None),
    )
    remaining, waterfall = rows, []
    for label, predicate in criteria:
        remaining = [row for row in remaining if predicate(row)]
        waterfall.append(dict(condition=label, remaining=len(remaining)))
    native = parking_candidates(rows, task)
    supplied = parking_candidates(old_rows, task)
    if native or remaining or read(job/'diagnosis.json')['selected']:
        raise ValueError('诊断结果与原生输出重算不一致')
    old_top = supplied[0] if supplied else None
    same_pair = next((r for r in rows if old_top and (r['from_node'], r['to_node']) ==
                      (old_top['from_node'], old_top['to_node'])), None)
    fields = ('from_node', 'to_node', 'name', 'road_class', 'parking', 'length_km',
              'flow_pcu_h', 'vc', 'speed_kmh')
    return dict(status='baseline_only_no_candidate', job=state['id'],
                source='软件自带教学案例4', source_links=len(rows),
                native_scenarios=1, native_module_calls=len(manifest['modules']),
                registration_before_native=True, source_inputs_exact=True,
                code_snapshot_exact=True, fresh_outputs_verified=True,
                candidate_filter_waterfall=waterfall,
                new_baseline_candidates=len(native), supplied_old_output_candidates=len(supplied),
                supplied_old_top={k: old_top[k] for k in fields} if old_top else None,
                same_road_in_new_baseline={k: same_pair[k] for k in fields} if same_pair else None,
                conclusion='新原生基准没有满足冻结停车诊断规则的路段，按协议不启动治理或需求敏感性仿真',
                limitation='软件自带旧输出只解释事前案例选择；本次不构成跨数据集完整闭环或治理收益证据')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    parser.add_argument('registration', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = audit(args.job, args.registration)
    data = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        if args.output.exists():
            raise FileExistsError('不覆盖已有诊断审计')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    print(data)
