"""Third version of native recovery for the unchanged guarded policy challenge.

No controller, region-selection, budget, demand, or acceptance rule changes.
Only narrowly classified native execution failures may be retried before any
strategy observes a result; every failed attempt remains separately auditable.
"""
from .guard_challenge import (
    LOGICAL_BUDGET, MAX_CANDIDATES, MAX_PHYSICAL, STRATEGIES,
    candidate_actions, challenge_task, fixed_policies)
from .guard_challenge import protocol as original_protocol
from .native_recovery_v3 import CACHE_FILES, MAX_RETRIES_PER_SCENE, MAX_RETRIES_TOTAL

VERSION = 'network-guard-equal-budget-v3-vehicle-cache-recovery'
KIND = 'prospective_network_guard_challenge_vehicle_cache_recovery'


def protocol(task, exclusions):
    result = original_protocol(task, exclusions)
    result.update(
        version=VERSION,
        native_recovery=dict(
            per_scene_extra_attempts=MAX_RETRIES_PER_SCENE,
            total_extra_attempts=MAX_RETRIES_TOTAL,
            allowed_cache_files=list(CACHE_FILES),
            trigger='只有M6AssignVehicle.exe明确报告本场景cache下预登记的Rwm或volume车型文件打开故障时才原输入重试',
            isolation='失败场景和日志原字节移入native_attempts；从原案例重新生成同名场景，比较执行前输入摘要',
            accounting='失败尝试未产生可评估结果，不消耗任一策略逻辑场景；额外原生调用全部披露',
            no_selection='不按治理是否通过决定重试；其他原生故障、重复失败超限、输入变动或审计失败均终止任务'))
    result['limitations'].append('三次旧任务已因不同原生文件故障中止；本版是观察到部分治理结果后的执行可靠性修订，不称完全未见治理输出的盲测')
    return result
