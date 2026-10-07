"""Re-read raw outputs and replay every prospective study decision without models."""
import json
import math
import re
from datetime import datetime
from pathlib import Path

from .archive import original_scenario, verify_archive
from .data import OUTPUTS, PROJECT, changed_input_files, input_fingerprints, numeric, results, save_json
from .decision_analysis import policy_key
from .runner import MODULES, validate_source
from .task import Task
from .study import STRATEGIES, candidate_actions, freeze_regions, protocol
from .study_runner import (HISTORY, render_report, sha, strategy_result, study_task, view_record)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def seconds(value):
    return datetime.fromisoformat(value).timestamp()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def replay(task, selection, state, *, arms=STRATEGIES, controller_factory=candidate_actions, budget=10):
    """Replay only audited per-strategy views. No cross-strategy observation access."""
    traces = state['trace']
    cursor, cache, observed_times = 0, {}, {}
    by_name = {r['name']: r for r in state['runs']}
    require(len(by_name) == len(state['runs']), '物理场景名称重复')
    for region in selection['regions']:
        entry = state['regions'][region['id']]
        if region['status'] != 'selected':
            require(entry == {'status': 'no_candidate'}, '无候选层被替换或补测')
            continue
        require(entry['status'] == 'completed', '区域尚未完成')
        strategies = entry['strategies']
        require(set(strategies) == set(arms), '遗漏对照策略')
        base_views = {r['demand_factor']: r for r in strategies[arms[0]]['runs']
                      if not r['changes'] and r['name'] != 'baseline_repeat'}
        require(set(base_views) == set(task.demand_factors), '需求基准未配齐')
        controllers = {s: controller_factory(s, region, task, base_views) for s in arms}
        sent = {s: None for s in arms}
        completed_names = {s: [] for s in arms}
        while controllers:
            batch = []
            for strategy, controller in list(controllers.items()):
                try:
                    expected = controller.send(sent[strategy])
                except StopIteration as end:
                    saved = strategies[strategy]
                    require(end.value == saved['summary'], '逻辑预算或停止原因不能重放')
                    require(len(saved['runs']) == end.value['logical_scenes'] <= budget, '逻辑场景计费不符')
                    require(saved['result'] == strategy_result(task, saved['runs'], end.value), '统一推荐或风险不能重算')
                    del controllers[strategy]
                    continue
                require(cursor < len(traces), '决策轨迹不完整')
                trace = traces[cursor]
                cursor += 1
                require(trace['sequence'] == cursor, '决策序号不连续')
                require({k: trace.get(k) for k in expected} == expected, '下一步措施未由本策略已测观察生成')
                require(set(trace) == set(expected) | {'decided_at', 'sequence', 'physical_run', 'shared', 'observed_at'}, '决策字段不符')
                if sent[strategy] is not None:
                    require(seconds(trace['decided_at']) >= observed_times[sent[strategy]['name']], '继续下一需求的决定早于上一需求结果')
                for name in expected['observed_runs']:
                    require(name in observed_times and seconds(trace['decided_at']) >= observed_times[name], '反馈决定早于其观察')
                batch.append(trace)
            new_starts = []
            for trace in batch:
                physical = by_name[trace['physical_run']]
                key = (policy_key(trace['changes']), trace['factor'])
                require(physical['changes'] == trace['changes'] and physical['demand_factor'] == trace['factor'], '共享措施或需求不一致')
                require(trace['shared'] == (key in cache), '物理共享计数不符')
                if key in cache:
                    require(cache[key] == physical['name'], '相同输入重复计算或错配缓存')
                else:
                    require(physical['name'] == f'case_{len(cache)+1:03d}', '额外物理计算或场景顺序不符')
                    cache[key] = physical['name']
                    new_starts.append(seconds(physical['execution']['started']))
                require(seconds(trace['observed_at']) >= seconds(physical['execution']['finished']), '观察早于真实输出')
                require(seconds(trace['decided_at']) <= seconds(trace['observed_at']), '先观察后补写决定')
                observed_times[trace['name']] = seconds(trace['observed_at'])
                matches = [r for r in strategies[trace['strategy']]['runs'] if r['name'] == trace['name']]
                require(len(matches) == 1 and matches[0]['physical_run'] == trace['physical_run'], '策略观察引用不唯一')
                sent[trace['strategy']] = matches[0]
                completed_names[trace['strategy']].append(trace['name'])
            if new_starts:
                require(all(seconds(t['decided_at']) < min(new_starts) for t in batch), '同轮各策略未在读取新候选前共同登记决定')
        for strategy in arms:
            require([r['name'] for r in strategies[strategy]['runs'] if r['changes']] == completed_names[strategy], '观察列表有额外候选或遗漏')
    require(cursor == len(traces), '存在无法重放的额外决定')
    require(len(cache)+4 == state['run_count'], '总物理场景不等于治理并集加基准及复跑')
    return dict(decisions=cursor, unique_policy_scenes=len(cache),
                shared_observations=sum(t['shared'] for t in traces),
                logical_scenes=sum(e['summary']['logical_scenes'] for r in state['regions'].values()
                                   for e in r.get('strategies', {}).values()))


def verify(job, archived=False, write=True):
    job = Path(job).resolve()
    archive = verify_archive(job) if archived else None
    state, source = read(job/'state.json'), read(job/'source_inputs.json')
    frozen, selection, context = (read(job/name) for name in ('protocol.json', 'selection.json', 'execution_context.json'))
    # Explicit adapters keep the sealed v1 algorithm and audit semantics intact.
    task_factory, protocol_factory, report_factory = study_task, protocol, render_report
    arms, controller_factory, history, budget = STRATEGIES, candidate_actions, HISTORY, 10
    kind, audit_version = 'prospective_region_study', 'prospective-study-audit-v1'
    extra_critical = ()
    registry = None
    registry_path = None
    if state.get('kind') == 'prospective_backtracking_challenge':
        from . import challenge, challenge_runner
        task_factory, protocol_factory = challenge.challenge_task, challenge.protocol
        report_factory = challenge_runner.render_report
        arms, controller_factory = challenge.STRATEGIES, challenge.candidate_actions
        history, budget = challenge_runner.HISTORY, challenge.LOGICAL_BUDGET
        kind, audit_version = challenge.KIND, 'backtracking-challenge-audit-v1'
        extra_critical = ('challenge', 'challenge_runner')
        registry_path = PROJECT/'config/challenge_history.json'
        registry = read(registry_path)
    elif state.get('kind') == 'prospective_network_guard_challenge':
        from . import guard_challenge, guard_challenge_runner
        task_factory, protocol_factory = guard_challenge.challenge_task, guard_challenge.protocol
        report_factory = guard_challenge_runner.render_report
        arms, controller_factory = guard_challenge.STRATEGIES, guard_challenge.candidate_actions
        history, budget = guard_challenge_runner.HISTORY, guard_challenge.LOGICAL_BUDGET
        kind, audit_version = guard_challenge.KIND, 'network-guard-challenge-audit-v1'
        extra_critical = ('guard_challenge', 'guard_challenge_runner')
        registry_path = guard_challenge_runner.REGISTRY
        registry = read(registry_path)
    elif state.get('kind') == 'prospective_network_guard_challenge_native_recovery':
        from . import guard_challenge_resilient, guard_challenge_resilient_runner, guard_challenge_runner
        task_factory, protocol_factory = guard_challenge_resilient.challenge_task, guard_challenge_resilient.protocol
        report_factory = guard_challenge_resilient_runner.render_report
        arms, controller_factory = guard_challenge_resilient.STRATEGIES, guard_challenge_resilient.candidate_actions
        history, budget = guard_challenge_runner.HISTORY, guard_challenge_resilient.LOGICAL_BUDGET
        kind, audit_version = guard_challenge_resilient.KIND, 'network-guard-challenge-native-recovery-audit-v2'
        extra_critical = ('guard_challenge', 'guard_challenge_runner', 'guard_challenge_resilient',
                          'guard_challenge_resilient_runner', 'native_recovery')
        registry_path = guard_challenge_runner.REGISTRY
        registry = read(registry_path)
    elif state.get('kind') == 'prospective_network_guard_challenge_vehicle_cache_recovery':
        from . import guard_challenge_resilient_v3, guard_challenge_resilient_runner_v3, guard_challenge_runner
        task_factory, protocol_factory = guard_challenge_resilient_v3.challenge_task, guard_challenge_resilient_v3.protocol
        report_factory = guard_challenge_resilient_runner_v3.render_report
        arms, controller_factory = guard_challenge_resilient_v3.STRATEGIES, guard_challenge_resilient_v3.candidate_actions
        history, budget = guard_challenge_runner.HISTORY, guard_challenge_resilient_v3.LOGICAL_BUDGET
        kind, audit_version = guard_challenge_resilient_v3.KIND, 'network-guard-challenge-vehicle-cache-audit-v3'
        extra_critical = ('guard_challenge', 'guard_challenge_runner', 'guard_challenge_resilient_v3',
                          'guard_challenge_resilient_runner_v3', 'native_recovery_v3')
        registry_path = guard_challenge_runner.REGISTRY
        registry = read(registry_path)
    task = task_factory()
    Task.from_dict(state['task'])
    require(state['status'] == 'completed' and state['kind'] == kind, '前瞻研究尚未完成')
    require(state['task'] == task.as_dict(), '逻辑任务卡改变')
    require({k: v for k, v in frozen.items() if k != 'registered_at'} == protocol_factory(task, frozen['exclusions']), '事前协议改变或版本不符')
    require(context['protocol_sha256'] == sha(job/'protocol.json') == selection['protocol_sha256'], '协议摘要不一致')
    require(context['source_inputs_sha256'] == sha(job/'source_inputs.json'), '原输入摘要改变')
    require([x['job'] for x in frozen['exclusions']] == list(history), '历史区域排除集合改变')
    critical = ('agent', 'data', 'runner', 'task', 'study', 'study_runner', 'selection', 'decision_analysis', 'robust_search') + extra_critical
    require({f'traffic_agent/{name}.py' for name in critical} <= context['code_sha256'].keys(), '运行算法快照不全')
    for name, expected in context['code_sha256'].items():
        path = job/'code_snapshot'/name
        require(path.resolve().is_relative_to((job/'code_snapshot').resolve()) and sha(path) == expected, '算法快照改变')
    for name in critical:
        require(sha(PROJECT/f'traffic_agent/{name}.py') == context['code_sha256'][f'traffic_agent/{name}.py'], '核验算法与运行快照不同，需显式版本适配')
    if registry is not None:
        registry_relative = registry_path.relative_to(PROJECT).as_posix()
        require(context['code_sha256'].get(registry_relative) == sha(registry_path), '历史范围登记快照不符')
    require(set(context['native_sha256']) == set(MODULES), '原生模块摘要不全')
    runs = state['runs']
    require(len(runs) == state['run_count'] <= state['physical_budget'] == frozen['max_physical_scenes'], '物理预算不符')
    require([r['name'] for r in runs[:3]] == ['baseline', 'baseline_1.1', 'baseline_0.9'] and runs[-1]['name'] == 'baseline_repeat', '基准或复跑顺序不符')
    require([r['name'] for r in runs[3:-1]] == [f'case_{i:03d}' for i in range(1, len(runs)-3)], '物理治理场景不连续')
    base, by_name = runs[0], {r['name']: r for r in runs}
    base_path = (job/base['scenario']).resolve()
    require(base_path.is_relative_to(job/'scenarios'), '基准场景路径越界')
    require(state['source_validation'] == validate_source(base_path), '原始路网、OD或固定分配参数核验不符')
    baseline_od = numeric(base_path/'result/ODCar.tsd')
    baseline_manage = (base_path/'data/manageL.txt').read_bytes()
    first_start = seconds(base['execution']['started'])

    def mtime(path):
        return archive['files'][path.relative_to(job).as_posix()]['mtime_ns']/1e9 if archive else path.stat().st_mtime

    require(seconds(frozen['registered_at']) <= seconds(context['captured_at']) < first_start, '协议登记晚于基准计算')
    require(seconds(state['started']) <= seconds(frozen['registered_at']) and
            seconds(state['finished']) >= seconds(runs[-1]['execution']['finished']), '任务起止时间与真实调用不符')
    require(mtime(job/'protocol.json') < first_start and mtime(job/'execution_context.json') < first_start, '协议文件在基准后被改写')
    require(seconds(base['execution']['finished']) <= seconds(selection['frozen_at']), '选区未依据新基准')
    policies = [r for r in runs if r['changes']]
    if policies:
        first_policy = min(seconds(r['execution']['started']) for r in policies)
        require(seconds(selection['frozen_at']) < first_policy and mtime(job/'selection.json') < first_policy, '选区晚于治理输出')
    rows_by_name, evidence = {}, []
    prior_finish = first_start
    required_outputs = {f'result/{n}' for n in OUTPUTS.values()} | {'cache/CapLinkVehMan.dat', 'cache/Assign_car.od'}
    for run in runs:
        path = (job/run['scenario']).resolve()
        require(path.is_relative_to(job/'scenarios'), '场景路径越界')
        manifest = read(job/'logs'/run['name']/'run.json')
        expected_path = original_scenario(archive, run['scenario']) if archive else str(path)
        require(manifest == run['execution'] and manifest['scenario'] == expected_path, '执行记录与独立日志或场景不符')
        require(manifest['status'] == 'completed' and tuple(m['module'] for m in manifest['modules']) == MODULES, '原生模块未完整执行')
        start, finish = seconds(manifest['started']), seconds(manifest['finished'])
        require(prior_finish <= start <= finish, '原生场景顺序不符')
        prior_finish = finish
        module_end = start
        for module in manifest['modules']:
            require(module['status'] == 'completed' and module['returncode'] == 0 and type(module['pid']) is int and module['pid'] > 0, '原生模块退出异常')
            require(module_end <= seconds(module['started']) <= seconds(module['finished']) <= finish, '原生模块时间不符')
            module_end = seconds(module['finished'])
            for stream in ('stdout', 'stderr'):
                folder = job/'logs'/run['name']
                raw = (folder/f"{module['module']}.{stream}.bin").read_bytes().decode('gb18030', errors='replace')
                # The Windows runner uses text-mode write_text: each LF is
                # translated even if the native stream already contained CRLF.
                # Compare preserved bytes, independent of the audit machine OS.
                saved = (folder/f"{module['module']}.{stream}.txt").read_bytes()
                require(raw.replace('\n', '\r\n').encode('utf-8') == saved and '[error]' not in raw.lower(), '模型原始输出含错误或转码日志不一致')
        require(set(manifest['fresh_outputs']) == required_outputs, '缺少新鲜输出证据')
        for relative, stamp in manifest['fresh_outputs'].items():
            output = path/relative
            stat = output.stat()
            actual_ns = archive['files'][output.relative_to(job).as_posix()]['mtime_ns'] if archive else stat.st_mtime_ns
            require(stat.st_size == stamp['bytes'] > 0 and actual_ns == stamp['mtime_ns'] and actual_ns/1e9 >= start-2, '输出陈旧或运行后改变')
        before = read(job/'logs'/run['name']/'inputs_before.json')
        require(input_fingerprints(path) == before and manifest['input_changes_during_run'] == [], '模型输入被改写')
        changed = {k for k in source.keys() | before.keys() if source.get(k) != before.get(k)}
        require(changed == changed_input_files(run['changes'], run['demand_factor']), '额外输入变化')
        expected_manage = baseline_manage
        for change in run['changes']:
            require(set(change) == {'from_node', 'to_node', 'old_parking', 'parking'} and change['old_parking'] in (3, 4) and change['parking'] in (1, 2), '治理越出登记措施')
            pattern = (rb'(?m)^(' + str(change['from_node']).encode() + rb'[ \t]+' + str(change['to_node']).encode() + rb'[ \t]+\d+[ \t]+)'
                       + str(change['old_parking']).encode() + rb'([ \t]+\d+[ \t]*\r?$)')
            expected_manage, count = re.subn(pattern, lambda m: m[1]+str(change['parking']).encode()+m[2], expected_manage)
            require(count == 1, '停车原值或路段唯一性不符')
        require((path/'data/manageL.txt').read_bytes() == expected_manage, '管理文件有未登记编辑')
        od, assigned = numeric(path/'result/ODCar.tsd'), numeric(path/'cache/Assign_car.od')
        require(len(od) == len(assigned) == len(baseline_od) and min(assigned) >= 0, '分配OD维数或非负性不符')
        factor = run['demand_factor']
        require(all(math.isclose(v, b*factor, rel_tol=1e-8, abs_tol=1e-7) for v, b in zip(od, baseline_od)), '需求矩阵并非逐元素缩放')
        require(sum(assigned) == manifest['assigned_car_od_sum'] and sum(od) == manifest['source_car_od_sum'], '分配OD总量与实际缓存不符')
        require(manifest['assigned_to_source_ratio'] == sum(assigned)/sum(od), '内部与源OD比例不符')
        internal_factor = sum(assigned)/base['execution']['assigned_car_od_sum']
        require(math.isclose(internal_factor, factor, rel_tol=1e-5), '原生分配需求响应不符')
        rows = results(path)
        require(len(rows) == manifest['result_rows'] and run['metrics'] is None, '原始物理场景被混入单区指标')
        rows_by_name[run['name']] = rows
        evidence.append(dict(name=run['name'], modules=len(MODULES), rows=len(rows), demand_factor=factor,
                             internal_factor=internal_factor, changed_inputs=sorted(changed)))
    excluded = set()
    for exclusion in frozen['exclusions']:
        path = (job/exclusion['file']).resolve()
        require(path.is_relative_to(job/'exclusions') and sha(path) == exclusion['sha256'], '历史排除范围改变')
        old_scope = read(path)
        if registry is not None:
            registered = next(e for e in registry['entries'] if e['job'] == exclusion['job'])
            require(old_scope == registered['scope'] and registered['geometry_sha256'] == source['data/geom.txt'], '排除范围与事前历史登记不一致')
        excluded.update(old_scope['core']+old_scope['neighbor'])
    recomputed_selection = freeze_regions(rows_by_name['baseline'], task, excluded)
    require(recomputed_selection == {k: v for k, v in selection.items() if k not in ('frozen_at', 'baseline', 'protocol_sha256')}, '冻结选区不能从新基准重建')
    require(set(state['regions']) == {r['id'] for r in selection['regions']}, '区域被遗漏或增加')
    require(state['baseline_names'] == {'1.0': 'baseline', '1.1': 'baseline_1.1', '0.9': 'baseline_0.9'}, '基准映射错误')
    for region in selection['regions']:
        if region['status'] != 'selected':
            continue
        for entry in state['regions'][region['id']]['strategies'].values():
            baseline_names = [r['name'] for r in entry['runs'] if not r['changes']]
            require(baseline_names == ['baseline', 'baseline_1.1', 'baseline_0.9', 'baseline_repeat'], '策略基准或复跑计费不符')
            for view in entry['runs']:
                physical = by_name[view['physical_run']]
                paired = rows_by_name[state['baseline_names'][str(float(physical['demand_factor']))]] if physical['changes'] else None
                expected = view_record(physical, rows_by_name[physical['name']], region, task, paired, view['name'])
                require(view == expected, '区域指标、约束或风险与原始数组不一致')
    identical = len(rows_by_name['baseline']) == len(rows_by_name['baseline_repeat']) and all(
        a[k] == b[k] for a, b in zip(rows_by_name['baseline'], rows_by_name['baseline_repeat']) for k in OUTPUTS)
    require(identical and state['repeatability'] == dict(exact_match=True, arrays=list(OUTPUTS), row_count=len(rows_by_name['baseline']),
                                                      note='确定性复现核对，不当作独立统计样本'), '基准复跑不一致')
    replayed = replay(task, selection, state, arms=arms, controller_factory=controller_factory, budget=budget)
    events = [json.loads(line) for line in (job/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    recovery = None
    if kind in ('prospective_network_guard_challenge_native_recovery',
                'prospective_network_guard_challenge_vehicle_cache_recovery'):
        if kind == 'prospective_network_guard_challenge_native_recovery':
            from .native_recovery import verify_retries
        else:
            from .native_recovery_v3 import verify_retries
        recovery = verify_retries(job, state, original_job=archive['original_job'] if archive else None)
        retry_events = [e for e in events if e['event'] == 'native_retry']
        require(len(retry_events) == len(state['native_retries']), '原生重试追加日志数量不符')
        for entry, event in zip(state['native_retries'], retry_events):
            require(event['data'] == entry, '原生重试追加日志内容不符')
            name = entry['logical_name']
            later = [r for r in state['native_retries'] if r['logical_name'] == name and r['attempt'] == entry['attempt'] + 1]
            next_start = later[0]['failed_started'] if later else by_name[name]['execution']['started']
            require(seconds(entry['failed_finished']) <= seconds(event['time']) <= seconds(next_start),
                    '原生失败登记不是在失败后、下一次计算前完成')
    else:
        require(not any(e['event'] == 'native_retry' for e in events), '旧版研究不能包含原生重试')
    decisions = [e for e in events if e['event'] == 'study_decision']
    observations = [e for e in events if e['event'] == 'study_observation']
    require(len(decisions) == len(observations) == len(state['trace']), '追加事件日志有遗漏或额外观察')
    for trace, decision, observation in zip(state['trace'], decisions, observations):
        require(decision['data'] == {k: v for k, v in trace.items() if k not in ('physical_run', 'shared', 'observed_at')}, '事前追加日志与状态决定不符')
        require(seconds(trace['decided_at']) <= seconds(decision['time']) <= seconds(trace['observed_at']) <= seconds(observation['time']), '追加日志时间不符')
        data = observation['data']
        strategy = state['regions'][trace['region']]['strategies'][trace['strategy']]
        view = next(r for r in strategy['runs'] if r['name'] == trace['name'])
        require(data == dict(sequence=trace['sequence'], physical_run=trace['physical_run'], shared=trace['shared'],
                             eligible=view['comparison']['eligible'], violations=view['comparison']['violations']), '观察日志与原始评估不符')
    require((job/'report.md').read_text(encoding='utf-8') == report_factory(state, selection), '报告与核验状态不一致')
    verification = dict(passed=True, version=audit_version, physical_scenes=len(runs),
                        native_module_calls=len(runs)*len(MODULES) + (recovery['failed_native_module_calls'] if recovery else 0),
                        regions=len([r for r in selection['regions'] if r['status']=='selected']),
                        state_sha256=sha(job/'state.json'), protocol_sha256=sha(job/'protocol.json'),
                        selection_sha256=sha(job/'selection.json'),
                        raw_inputs_outputs_verified=True, prospective_selection_rebuilt=True,
                        decisions_replayed=replayed, repeatability=True, runs=evidence,
                        meaning='原始文件重算、时序与代码摘要核对；不是数字签名、独立机器重跑或统计显著性证明')
    if recovery is not None:
        verification['native_recovery'] = recovery
    if write:
        save_json(job/('portable_verification.json' if archived else 'verification.json'), verification)
    return verification
