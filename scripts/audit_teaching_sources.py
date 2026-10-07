"""Read-only survey of shipped teaching cases before a new-model transfer pilot."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmark_teaching_transfer import task
from traffic_agent.data import OUTPUTS, PROJECT, diagnose, input_fingerprints, results
from traffic_agent.runner import now, validate_source


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def survey(output):
    if output.exists():
        raise FileExistsError('只读调查文件已存在，不覆盖')
    entries = []
    for number in (2, 3, 4):
        source = PROJECT/f'TranStar城市交通版V4.8 - 试用版/examples/教学案例{number}/基础路网'
        before = input_fingerprints(source)
        validation = validate_source(source)
        rows = results(source)
        selected = diagnose(rows, task())['selected']
        if input_fingerprints(source) != before:
            raise ValueError(f'教学案例{number}源输入在调查期间改变')
        entries.append(dict(case=number, source=source.relative_to(PROJECT).as_posix(),
                            validation=validation, source_files=len(before),
                            source_input_sha256=hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
                            supplied_output_sha256={key: digest(source/'result'/filename)
                                                    for key, filename in OUTPUTS.items()},
                            supplied_output_candidates=len(selected),
                            candidate_directions=[[r['from_node'], r['to_node']] for r in selected]))
    record = dict(version='shipped-teaching-source-survey-v1', captured_at=now(),
                  script_sha256=digest(Path(__file__)), new_model_calls=0,
                  meaning='只读取软件自带初始输出用于预选另一套数据；正式选区必须依据随后新运行的原生基准',
                  chosen_case=4, entries=entries)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8'))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', type=Path,
                        default=PROJECT/'docs/validation/20260923_teaching_source_survey.json')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    value = survey(args.output.resolve())
    print(json.dumps(dict(output=str(args.output), candidates={x['case']: x['supplied_output_candidates']
                                                         for x in value['entries']},
                          new_model_calls=value['new_model_calls']), ensure_ascii=False))
