"""Integrity-gated launcher for the relocatable Windows application bundle."""
import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

MANIFEST = 'runtime_manifest.json'
MUTABLE = '智能体实验'
KIND = 'portable_transtar_runtime_v1'
LEGACY_PLANNING_PROFILE = 'llm_planning_candidate_v1'
PLANNING_PROFILE = 'llm_planning_candidate_v2'
LEGACY_PLANNING_FILES = {
    'traffic_agent/dispatch.py', 'traffic_agent/dispatch_web.py', 'traffic_agent/llm_client.py',
    'traffic_agent/llm_settings.py', 'traffic_agent/natural_plan.py', 'traffic_agent/plan_compiler.py',
    'traffic_agent/feedback_planner.py', 'traffic_agent/programme_review.py', 'traffic_agent/oneway_planning.py',
    'traffic_agent/initial_planner.py', 'traffic_agent/planning_scope.py', 'traffic_agent/planning_attempt.py',
    'traffic_agent/planning_budget.py',
    'traffic_agent/planning_audit.py', 'traffic_agent/research_evidence.py', 'traffic_agent/spatial_planning.py',
    'traffic_agent/static/index.html', 'traffic_agent/static/app.js', 'traffic_agent/static/dispatch.js',
    'traffic_agent/static/planner_view.js', 'traffic_agent/static/map_view.js',
    'scripts/verify_programme.py', 'scripts/verify_experiment.py', 'scripts/validate_observed_planning.py',
    '开始使用.md',
}
PLANNING_FILES = LEGACY_PLANNING_FILES | {
    'traffic_agent/planning_response.py', 'traffic_agent/study_handoff.py',
    'traffic_agent/professional_review.py',
    'traffic_agent/knowledge_retrieval.py', 'traffic_agent/knowledge.json',
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('运行包路径无效')
    posix, windows = PurePosixPath(name), PureWindowsPath(name)
    if posix.is_absolute() or windows.is_absolute() or '..' in posix.parts or posix.as_posix() != name:
        raise ValueError('运行包路径越界或未规范化')
    if any(p.rstrip(' .') != p or PureWindowsPath(p).is_reserved() for p in posix.parts):
        raise ValueError('运行包包含Windows保留名称')
    return name


def verify_bundle(root):
    root = Path(root).resolve()
    manifest = json.loads((root/MANIFEST).read_text(encoding='utf-8'))
    if manifest.get('kind') != KIND or not isinstance(manifest.get('files'), dict) or not manifest['files']:
        raise ValueError('运行包清单版本或文件集合无效')
    files = manifest['files']
    names = [safe_name(name) for name in files]
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError('运行包文件名大小写冲突')
    if MANIFEST in files or any(PurePosixPath(n).parts[0] == MUTABLE for n in names):
        raise ValueError('清单不得封存自身或可写实验区')
    required = {'launch.cmd','python/python.exe','python/python312.dll','python/python312._pth','python/python312.zip','python/pyvenv.cfg',
                'traffic_agent/__main__.py','traffic_agent/portable.py','start.ps1',
                'traffic_agent/portable_compiler.py','portable-tools/gcc.exe','toolchain/bin/gcc.exe'}
    if not required <= files.keys():
        raise ValueError('缺少启动器、Python或智能体源码')
    profile = manifest.get('application_profile')
    if profile not in (None, LEGACY_PLANNING_PROFILE, PLANNING_PROFILE):
        raise ValueError('运行包应用配置版本无效')
    required_planning=PLANNING_FILES if profile==PLANNING_PROFILE else LEGACY_PLANNING_FILES if profile==LEGACY_PLANNING_PROFILE else set()
    if manifest.get('planning_capabilities',{}).get('knowledge_retrieval_protocol') in ('curated-knowledge-rag-v2','curated-knowledge-rag-v3'):
        required_planning=required_planning | {'traffic_agent/feasibility_search.py'}
    if manifest.get('planning_capabilities',{}).get('conclusion_protocol') in ('measured-conclusion-v16','measured-conclusion-v17','measured-conclusion-v18'):
        required_planning=required_planning | {'traffic_agent/evidence_support.py'}
    if manifest.get('planning_capabilities', {}).get('feedback_instruction_protocol') == 'stage-planning-instructions-v1':
        required_planning=required_planning | {'traffic_agent/planning_instructions.py'}
    if manifest.get('planning_capabilities', {}).get('conclusion_protocol') in ('measured-conclusion-v2', 'measured-conclusion-v3','measured-conclusion-v4','measured-conclusion-v5','measured-conclusion-v6','measured-conclusion-v7','measured-conclusion-v8','measured-conclusion-v9','measured-conclusion-v10','measured-conclusion-v11','measured-conclusion-v12','measured-conclusion-v13','measured-conclusion-v14','measured-conclusion-v15','measured-conclusion-v16','measured-conclusion-v17','measured-conclusion-v18'):
        required_planning=required_planning | {'traffic_agent/executed_facts.py'}
    if manifest.get('planning_capabilities', {}).get('conclusion_protocol') in ('measured-conclusion-v3','measured-conclusion-v4','measured-conclusion-v5','measured-conclusion-v6','measured-conclusion-v7','measured-conclusion-v8','measured-conclusion-v9','measured-conclusion-v10','measured-conclusion-v11','measured-conclusion-v12','measured-conclusion-v13','measured-conclusion-v14','measured-conclusion-v15','measured-conclusion-v16','measured-conclusion-v17','measured-conclusion-v18'):
        required_planning=required_planning | {'traffic_agent/measured_numbers.py'}
    if manifest.get('planning_capabilities', {}).get('conclusion_protocol') in ('measured-conclusion-v6','measured-conclusion-v7','measured-conclusion-v8','measured-conclusion-v9','measured-conclusion-v10','measured-conclusion-v11','measured-conclusion-v12','measured-conclusion-v13','measured-conclusion-v14','measured-conclusion-v15','measured-conclusion-v16','measured-conclusion-v17','measured-conclusion-v18'):
        required_planning=required_planning | {'traffic_agent/public_claims.py'}
    if manifest.get('planning_capabilities', {}).get('conclusion_protocol') in ('measured-conclusion-v13','measured-conclusion-v14','measured-conclusion-v15','measured-conclusion-v16','measured-conclusion-v17','measured-conclusion-v18'):
        required_planning=required_planning | {'traffic_agent/metric_assertions.py','traffic_agent/hypothesis_contract.py'}
    if manifest.get('planning_capabilities', {}).get('hypothesis_review_scope_protocol') == 'hypothesis-review-scope-v1':
        required_planning=required_planning | {'traffic_agent/review_scope.py','traffic_agent/study_comparison.py'}
    if manifest.get('planning_capabilities', {}).get('finding_reference_schema')=='scoped-measured-findings-v2':
        required_planning=required_planning | {'traffic_agent/research_evidence.py','traffic_agent/hypothesis_contract.py'}
    if manifest.get('planning_capabilities', {}).get('study_comparison_protocol')=='study-comparison-v2':
        required_planning=required_planning | {'traffic_agent/study_comparison.py','traffic_agent/hypothesis_contract.py'}
    if manifest.get('planning_capabilities', {}).get('planning_input_protocol') in ('referenced-planning-input-v1','referenced-planning-input-v2'):
        required_planning=required_planning | {'traffic_agent/planning_payload.py'}
    if manifest.get('planning_capabilities', {}).get('choice_rationale_protocol')=='choice-rationale-v1':
        required_planning=required_planning | {'traffic_agent/choice_rationale.py','traffic_agent/choice_evidence.py'}
    if manifest.get('planning_capabilities', {}).get('risk_review_protocol') in ('observed-risk-boundary-v1','observed-risk-boundary-v2'):
        required_planning=required_planning | {'traffic_agent/risk_review.py'}
    if manifest.get('planning_capabilities', {}).get('hypothesis_contract_protocol') == 'hypothesis-measurement-v1':
        required_planning=required_planning | {'traffic_agent/hypothesis_contract.py'}
    if manifest.get('planning_capabilities', {}).get('choice_basis_protocol') in ('observed-choice-basis-v1','observed-choice-basis-v2','observed-choice-basis-v3','observed-choice-basis-v4','observed-choice-basis-v5','observed-choice-basis-v6','observed-choice-basis-v7'):
        required_planning=required_planning | {'traffic_agent/choice_evidence.py','traffic_agent/static/choice_view.js'}
    if manifest.get('planning_capabilities', {}).get('choice_basis_protocol') in ('observed-choice-basis-v6','observed-choice-basis-v7'):
        required_planning=required_planning | {'traffic_agent/candidate_comparison.py'}
    if manifest.get('planning_capabilities', {}).get('question_wording_protocol') in ('bounded-question-wording-v1','bounded-question-wording-v2','bounded-question-wording-v3','bounded-question-wording-v4','bounded-question-wording-v5','bounded-question-wording-v6'):
        required_planning=required_planning | {'traffic_agent/question_wording.py'}
    if manifest.get('planning_capabilities', {}).get('question_wording_protocol') == 'bounded-question-wording-v6':
        required_planning=required_planning | {'traffic_agent/hypothesis_contract.py'}
    if manifest.get('planning_capabilities', {}).get('direction_semantics_protocol')=='oneway-direction-v4':
        required_planning=required_planning | {'traffic_agent/direction_claims.py'}
    if manifest.get('planning_capabilities', {}).get('action_execution_protocol') in ('action-execution-v1','action-execution-v2'):
        required_planning=required_planning | {'traffic_agent/action_execution.py'}
    if (manifest.get('planning_capabilities', {}).get('question_authoring_protocol')=='named-question-authoring-v1'
            or manifest.get('planning_capabilities', {}).get('planning_version') in ('natural-strategy-plan-v10','natural-strategy-plan-v11')):
        required_planning=required_planning | {'traffic_agent/question_authoring.py','traffic_agent/hypothesis_contract.py'}
    if manifest.get('planning_capabilities', {}).get('planning_version') in ('natural-strategy-plan-v6','natural-strategy-plan-v7','natural-strategy-plan-v8','natural-strategy-plan-v9','natural-strategy-plan-v10','natural-strategy-plan-v11'):
        required_planning=required_planning | {'traffic_agent/study_objectives.py'}
    if manifest.get('planning_capabilities', {}).get('planning_version') in ('natural-strategy-plan-v7','natural-strategy-plan-v8','natural-strategy-plan-v9','natural-strategy-plan-v10','natural-strategy-plan-v11'):
        required_planning=required_planning | {'traffic_agent/study_depth.py'}
    if manifest.get('planning_capabilities', {}).get('planning_version') in ('natural-strategy-plan-v8','natural-strategy-plan-v9','natural-strategy-plan-v10','natural-strategy-plan-v11'):
        required_planning=required_planning | {'traffic_agent/hypothesis_contract.py'}
    if manifest.get('planning_capabilities', {}).get('initial_hypothesis_correction_protocol') in ('initial-hypothesis-correction-v1','initial-hypothesis-correction-v2','initial-hypothesis-correction-v3'):
        required_planning=required_planning | {'traffic_agent/initial_hypothesis_review.py'}
    if (manifest.get('planning_capabilities', {}).get('initial_hypothesis_correction_protocol') in ('initial-hypothesis-correction-v2','initial-hypothesis-correction-v3')
            or manifest.get('planning_capabilities', {}).get('hypothesis_policy_patch_protocol') == 'hypothesis-policy-patch-v1'):
        required_planning=required_planning | {'traffic_agent/hypothesis_policy_patch.py'}
    if (manifest.get('planning_capabilities', {}).get('initial_review_protocol') in ('initial-plan-review-v5','initial-plan-review-v6')
            or manifest.get('planning_capabilities', {}).get('initial_hypothesis_correction_protocol') == 'initial-hypothesis-correction-v3'
            or manifest.get('planning_capabilities', {}).get('hypothesis_field_patch_protocol') == 'hypothesis-field-patch-v1'):
        required_planning=required_planning | {'traffic_agent/hypothesis_field_patch.py'}
    if not required_planning <= files.keys():
        raise ValueError('智能规划运行包缺少模型接口、规划界面或整个任务核验器')
    actual = set()
    for path in root.rglob('*'):
        if path.is_file():
            relative = path.relative_to(root)
            if relative.parts[0] == MUTABLE or relative.as_posix() == MANIFEST:
                continue
            actual.add(safe_name(relative.as_posix()))
    if actual != set(files):
        missing, extra = set(files)-actual, actual-set(files)
        raise ValueError(f'运行包文件集合改变：缺少{sorted(missing)[:3]}，额外{sorted(extra)[:3]}')
    total = 0
    for name, expected in files.items():
        path = (root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f'运行包文件链接越界：{name}')
        if path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']:
            raise ValueError(f'运行包内容改变：{name}')
        with path.open('rb') as stream:
            if stream.read(64).startswith(b'version https://git-lfs.github.com/spec/v1'):
                raise ValueError(f'运行包仍含LFS指针：{name}')
        total += expected['bytes']
    return dict(passed=True, kind=KIND, files=len(files), bytes=total,
                code_commit=manifest['code_commit'], python_version=manifest['python_version'],
                manifest_sha256=digest(root/MANIFEST), new_model_calls=0,
                meaning='不可变文件完整性检查；不是数字签名、模型授权检查或真实求解证明')


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--verify-only',action='store_true')
    args, remaining = parser.parse_known_args()
    root = Path(__file__).resolve().parents[1]
    try:
        print('正在核对便携运行包文件完整性…',flush=True)
        verification = verify_bundle(root)
        if sys.version_info[:2] != (3,12) or not Path(sys.executable).resolve().is_relative_to(root/'python'):
            raise ValueError('请通过运行包内的launch.cmd或python/python.exe启动')
        if not sys.flags.isolated or any(not Path(p).resolve().is_relative_to(root) for p in sys.path):
            raise ValueError('Python模块路径未隔离到本运行包')
        import site
        if site.ENABLE_USER_SITE or not sys.dont_write_bytecode:
            raise ValueError('用户包隔离或子解释器字节缓存策略未生效')
        from .data import save_json
        from .runner import now
        verification.update(checked_at=now(), executable=sys.executable, sys_path=sys.path,
                            isolated=True, user_site_enabled=site.ENABLE_USER_SITE,
                            bytecode_writes_disabled=sys.dont_write_bytecode,
                            cwd=str(Path.cwd()), root=str(root))
        record = root/MUTABLE/'portable_launch'/f'{uuid.uuid4().hex}.json'
        save_json(record,verification)
        print(json.dumps(dict(record=str(record),**verification),ensure_ascii=False,indent=2),flush=True)
        if args.verify_only:
            return 0
        # Avoid attributing a parent checkout's HEAD to an extracted distribution.
        os.environ['GIT_CEILING_DIRECTORIES'] = str(root.parent)
        if not any(a=='--port' or a.startswith('--port=') for a in remaining):
            remaining += ['--port','8790']
        if not any(a=='--compiler-bin' or a.startswith('--compiler-bin=') for a in remaining):
            from .portable_compiler import compiler_environment
            environment, compiler = compiler_environment(root)
            os.environ.update(environment)
            verification['bundled_compiler']=compiler
            save_json(record,verification)
        from .startup import main as start
        sys.argv = [sys.argv[0]] + remaining
        return start()
    except (ValueError,OSError,KeyError) as exc:
        print(f'便携启动失败：{exc}',file=sys.stderr,flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
