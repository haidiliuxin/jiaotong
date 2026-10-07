"""Freeze baseline-only exclusion scopes for the guarded prospective challenge.

Reads scope and geometry hashes, never treatment results. Run once before the
new protocol or simulations are started; existing registry is not overwritten.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import PROJECT

EXPERIMENTS = PROJECT / '智能体实验'
OUTPUT = PROJECT / 'config/guard_challenge_history.json'
OLD = PROJECT / 'config/challenge_history.json'
PRIOR_CHALLENGE = EXPERIMENTS / 'challenges/20260922_034228_3141eb'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    if OUTPUT.exists():
        raise FileExistsError(f'事前排除登记已存在：{OUTPUT}')
    previous = read(OLD)
    entries = list(previous['entries'])
    geometry = {entry['geometry_sha256'] for entry in entries}
    if len(geometry) != 1:
        raise ValueError('登记历史路网摘要不一致')
    geometry = geometry.pop()
    source = read(PRIOR_CHALLENGE/'source_inputs.json')
    if source['data/geom.txt'] != geometry:
        raise ValueError('上一挑战路网与登记历史不同')
    selection_path = PRIOR_CHALLENGE/'selection.json'
    selection = read(selection_path)
    for region in selection['regions']:
        if region['status'] != 'selected':
            continue
        scope = region['scope']
        entries.append(dict(job=f'challenges/{PRIOR_CHALLENGE.name}/selection.json#{region["id"]}',
                            source_scope_sha256=sha(selection_path), geometry_sha256=geometry,
                            scope={name: scope[name] for name in ('core', 'neighbor')}))
    registered = {entry['job'] for entry in entries}
    recent = list(EXPERIMENTS.glob('jobs/20260923_*/scopes.json'))
    recent += list(EXPERIMENTS.glob('portable_runs/*/scopes.json'))
    for path in sorted(recent, key=lambda p: p.as_posix()):
        identifier = path.relative_to(EXPERIMENTS).as_posix()
        if identifier in registered:
            continue
        inputs = read(path.parent/'source_inputs.json')
        if inputs['data/geom.txt'] != geometry:
            raise ValueError(f'新近范围路网不兼容：{identifier}')
        scope = read(path)
        if not {'core', 'neighbor'} <= scope.keys():
            raise ValueError(f'范围字段缺失：{identifier}')
        entries.append(dict(job=identifier, source_scope_sha256=sha(path),
                            geometry_sha256=geometry,
                            scope={name: scope[name] for name in ('core', 'neighbor')}))
    if len({entry['job'] for entry in entries}) != len(entries):
        raise ValueError('排除登记存在重复路径')
    frozen = dict(version=1,
                  meaning='新挑战开始前冻结的历史范围；只读取范围与路网摘要，不读取治理结果',
                  parent_registry_sha256=sha(OLD),
                  previous_challenge_selection_sha256=sha(selection_path),
                  entries=entries)
    OUTPUT.write_bytes(json.dumps(frozen, ensure_ascii=False, indent=2,
                                  allow_nan=False).encode('utf-8'))
    print(json.dumps(dict(entries=len(entries), added=len(entries)-len(previous['entries']),
                          excluded_indices=len({i for e in entries for i in e['scope']['core']+e['scope']['neighbor']}),
                          registry_sha256=sha(OUTPUT)), ensure_ascii=False))


if __name__ == '__main__':
    main()
