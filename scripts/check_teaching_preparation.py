"""Probe teaching-case input editing without starting any TranStar module."""
import argparse
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmark_teaching_transfer import SOURCE, task
from traffic_agent.data import (OUTPUTS, PROJECT, changed_input_files, diagnose,
                                geometry, input_fingerprints, numeric, prepare, results)
from traffic_agent.runner import now, validate_source


def probe(output):
    if output.exists():
        raise FileExistsError('准备兼容性证据已存在，拒绝覆盖')
    validation = validate_source(SOURCE)
    source_inputs = input_fingerprints(SOURCE)
    # Shipped outputs select only a valid edit for this format probe. The real
    # transfer study must choose its policy from its newly executed baseline.
    selected = diagnose(results(SOURCE), task())['selected']
    if not selected:
        raise ValueError('软件自带旧输出没有可用于输入格式探针的停车路段')
    row = selected[0]
    edit = dict(from_node=row['from_node'], to_node=row['to_node'],
                old_parking=row['parking'], parking=2)
    factor = 0.9
    with tempfile.TemporaryDirectory(prefix='teaching-prepare-') as directory:
        scenario = Path(directory)/'scenario'
        changes = prepare(SOURCE, scenario, [edit], factor)
        prepared_inputs = input_fingerprints(scenario)
        changed = {key for key in set(source_inputs) | set(prepared_inputs)
                   if source_inputs.get(key) != prepared_inputs.get(key)}
        if changed != changed_input_files([edit], factor):
            raise ValueError(f'教学案例场景输入差异超出白名单：{changed}')
        matches = [r for r in geometry(scenario)
                   if (r['from_node'], r['to_node']) == (edit['from_node'], edit['to_node'])]
        if len(matches) != 1 or matches[0]['parking'] != 2:
            raise ValueError('教学案例停车字段没有正确写入')
        original_od = sum(numeric(SOURCE/'result/ODCar.tsd'))
        prepared_od = sum(numeric(scenario/'result/ODCar.tsd'))
        if not math.isclose(prepared_od, original_od*factor, rel_tol=1e-8, abs_tol=1e-6):
            raise ValueError('教学案例小汽车OD缩放不一致')
        if any((scenario/'result'/name).exists() for name in OUTPUTS.values()):
            raise ValueError('复制场景仍留有旧评价输出')
        if input_fingerprints(SOURCE) != source_inputs:
            raise ValueError('准备探针改动了教学案例原输入')
    record = dict(version='teaching-case4-input-preparation-probe-v1', checked_at=now(),
                  source=SOURCE.relative_to(PROJECT).as_posix(), source_validation=validation,
                  source_input_sha256=hashlib.sha256(json.dumps(source_inputs, sort_keys=True).encode()).hexdigest(),
                  probe_edit=edit, demand_factor=factor, prepared_input_differences=sorted(changed),
                  original_car_od_sum=original_od, prepared_car_od_sum=prepared_od,
                  edits=changes, old_outputs_removed=True, source_inputs_unchanged=True,
                  new_model_calls=0,
                  meaning='仅用软件自带旧输出选择一条格式探针；不作为新任务选点或治理效果证据',
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', type=Path,
                        default=PROJECT/'docs/validation/20260923_teaching_prepare_probe.json')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    result = probe(args.output.resolve())
    print(json.dumps(dict(output=str(args.output), source_links=result['source_validation']['directed_links'],
                          changed=result['prepared_input_differences'], new_model_calls=0), ensure_ascii=False))
