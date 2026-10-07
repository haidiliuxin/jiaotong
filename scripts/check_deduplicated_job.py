"""Restore a content-addressed sealed job in another directory and audit it."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.frozen_study_audit import verify_frozen_study
from verify_experiment import verify


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_agent_audit(audit, package, state):
    """Optional repeat/search checks are required only by the frozen task."""
    if (audit.get('passed') is not True or audit.get('portable_archive_verified') is not True
            or audit.get('completed_scenarios') != package['completed_scenarios']
            or audit.get('module_executions') != package['module_executions']):
        raise ValueError('恢复任务的原生结果复核失败')
    repeated=audit.get('repeat_arrays_exact')
    if (state['task'].get('repeat_baseline') and not repeated) or (repeated is not None and not all(v is True for v in repeated.values())):
        raise ValueError('约定的同输入复跑缺失或结果不一致')
    if state.get('planner_trace') or state.get('target_planning_protocol'):
        check=audit.get('model_planning_audit',{})
        if check.get('passed') is not True:raise ValueError('恢复任务缺少模型观测决策审计')
        return 'model_observation_audit',check
    if state['task'].get('search_mode')=='robust' and not state.get('model_grid'):
        check=audit.get('active_search_audit',{})
        required=('protocol_frozen_before_first_run','raw_observations_verified','chronological_decisions',
                  'budget_and_stopping_verified','complete_demand_recommendation_verified','report_exports_present')
        if any(check.get(k) is not True for k in required):raise ValueError('恢复任务缺少完整规则搜索审计')
        return 'rule_search_audit',check
    return 'fixed_task_native_audit',None


def check(archive, output, restore_to=None):
    archive, output = archive.resolve(), output.resolve()
    if output.exists():
        raise ValueError('异目录复核记录已存在，不覆盖')
    package = json.loads((archive.parent / 'package.json').read_text(encoding='utf-8'))
    audit_mode = package.get('audit_mode', 'agent')
    if (package['format'] != 'sealed-job-content-addressed-v1'
            or audit_mode not in ('agent', 'frozen_region_study')
            or package['archive'] != archive.name or package['zip_sha256'] != sha(archive)
            or not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{6}', package['job'])):
        raise ValueError('去重包格式、名称或摘要不符')
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or set(names) != {'manifest.json'} | {
                n for n in names if re.fullmatch(r'objects/[0-9a-f]{64}', n)}:
            raise ValueError('去重包成员名称异常或重复')
        if bundle.testzip():
            raise ValueError('去重包CRC检查失败')
        manifest = json.loads(bundle.read('manifest.json'))
        files = manifest['files']
        if (manifest['format'] != package['format'] or manifest['job'] != package['job']
                or manifest['source_archive_sha256'] != package['source_archive_sha256']
                or len(files) != package['files']):
            raise ValueError('任务映射与发布记录不符')
        hashes = {v['sha256'] for v in files.values()}
        if (len(hashes) != package['unique_objects']
                or len(files) - len(hashes) != package['duplicates_elided']
                or set(names) != {'manifest.json'} | {'objects/' + h for h in hashes}):
            raise ValueError('对象数量与路径映射不符')
        parent = archive.parent.resolve()
        temp = Path(tempfile.mkdtemp(prefix='dedup_job_check_', dir=parent)).resolve()
        if not temp.is_relative_to(parent):
            raise ValueError('临时解压路径越界')
        try:
            objects = temp / 'objects'
            objects.mkdir()
            for hash_value in sorted(hashes):
                target = objects / hash_value
                with bundle.open('objects/' + hash_value) as source, target.open('wb') as sink:
                    shutil.copyfileobj(source, sink)
                if sha(target) != hash_value:
                    raise ValueError(f'对象SHA-256核验失败: {hash_value}')
            restored = temp / package['job']
            restored.mkdir()
            for name, record in files.items():
                relative = PurePosixPath(name)
                if (relative.is_absolute() or '..' in relative.parts or '\\' in name
                        or ':' in name or not relative.parts):
                    raise ValueError(f'路径映射越界: {name}')
                target = restored.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(restored.resolve()):
                    raise ValueError(f'恢复路径越界: {name}')
                source = objects / record['sha256']
                if source.stat().st_size != record['bytes']:
                    raise ValueError(f'文件大小与对象不符: {name}')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            if sha(restored / 'archive_manifest.json') != package['source_archive_sha256']:
                raise ValueError('原封存清单没有按字节复原')
            if audit_mode == 'frozen_region_study':
                audit = verify_frozen_study(restored)
                if (audit != json.loads((restored / 'portable_verification.json').read_text(encoding='utf-8'))
                        or audit['physical_scenes'] != package['completed_scenarios']
                        or audit['native_module_calls'] != package['module_executions']
                        or not audit['repeatability'] or not audit['raw_inputs_outputs_verified']
                        or not audit['prospective_selection_rebuilt']):
                    raise ValueError('恢复研究的原始模型输出或决策复核失败')
            else:
                audit = verify(restored, archived=True, write=False)
                state=json.loads((restored/'state.json').read_text(encoding='utf-8'))
                decision_kind,decision_check=check_agent_audit(audit,package,state)
            result = dict(status='passed', format=package['format'], archive=archive.name,
                          archive_sha256=package['zip_sha256'], restored_job=package['job'],
                          files=len(files), unique_objects=len(hashes),
                          completed_scenarios=package['completed_scenarios'],
                          module_executions=package['module_executions'],
                          repeat_arrays_exact=(audit['repeatability'] if audit_mode == 'frozen_region_study'
                                               else audit['repeat_arrays_exact']),
                          portable_archive_verified=(audit['passed'] if audit_mode == 'frozen_region_study'
                                                     else audit['portable_archive_verified']),
                          decision_replay=(audit['decisions_replayed'] if audit_mode == 'frozen_region_study'
                                           else decision_check),
                          decision_audit_type='frozen_region_study' if audit_mode=='frozen_region_study' else decision_kind,
                          new_model_calls=0,
                          scope='同机独立目录从每个SHA对象无损恢复完整任务，严格只读重算输入、输出、约束与决策')
            if restore_to is not None:
                destination = restore_to.resolve()
                if destination.exists():
                    raise ValueError('指定的永久恢复目录已存在，不覆盖')
                shutil.copytree(restored, destination)
                check_destination = (verify_frozen_study(destination) if audit_mode == 'frozen_region_study'
                                     else verify(destination, archived=True, write=False))
                if not check_destination['passed']:
                    raise ValueError('永久恢复目录复核失败')
                result['restored_to'] = str(destination)
        finally:
            if temp.is_relative_to(parent) and temp.name.startswith('dedup_job_check_'):
                shutil.rmtree(temp)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--restore-to', type=Path, help='复核通过后保留完整任务目录，不覆盖已有路径')
    args = parser.parse_args()
    print(json.dumps(check(args.archive, args.output, args.restore_to), ensure_ascii=False))
