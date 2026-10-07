"""Conservative capacity estimates, checked again before each scene copy."""
import shutil
from pathlib import Path

from .runner import RUNTIME_SOURCE
from .task import Task

RESERVE_BYTES = 512 * 1024 * 1024
VERSION = 'storage-budget-v1'


def tree_bytes(directory):
    return sum(path.stat().st_size for path in Path(directory).rglob('*') if path.is_file())


def estimate(tasks, source, root, *, runtime_source=RUNTIME_SOURCE, run_limit=None, include_runtime=True):
    """Budget an input copy plus output growth; bridge scenes also retain transforms."""
    cards = [task if isinstance(task, Task) else Task.from_dict(task) for task in tasks]
    if not cards or (run_limit is not None and (type(run_limit) is not int or run_limit < 1)):
        raise ValueError('磁盘估算需要任务及有效场景数量')
    root = Path(root)
    source_size = tree_bytes(source)
    bridges = [task.scenario == 'oneway' or (task.scenario == 'circulation' and task.turn is not None) for task in cards]
    runs = [run_limit if run_limit is not None else task.max_runs for task in cards]
    multipliers = [8 if bridge else 2 for bridge in bridges]
    copies = (sum(bridges) + int(not all(bridges) and not (root/'runtime').exists())) if include_runtime else 0
    runtime_size = tree_bytes(runtime_source) if copies else 0
    scene_bytes = source_size * sum(n*m for n,m in zip(runs,multipliers))
    required = copies * runtime_size + scene_bytes + RESERVE_BYTES
    existing = root
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    return dict(version=VERSION, passed=free >= required, free_bytes=free,
                estimated_required_bytes=required, source_bytes=source_size, budget_runs=sum(runs),
                scenario_copy_multiplier=max(multipliers), runtime_copy_bytes=copies*runtime_size,
                reserve_bytes=RESERVE_BYTES,
                basis='普通场景源文件×2；单向/显式禁转×8；新运行环境实际字节；另预留512MiB。估算不能保证原生输出大小或未来可用空间。')


def ensure_space(tasks, source, root, **kwargs):
    try:
        result = estimate(tasks, source, root, **kwargs)
    except OSError:
        raise RuntimeError('无法读取磁盘容量或源文件大小；未开始新的原生场景') from None
    if not result['passed']:
        raise RuntimeError(f"磁盘空间不足：可用{result['free_bytes']/1024**3:.2f}GiB，"
                           f"按预算估算需{result['estimated_required_bytes']/1024**3:.2f}GiB；请释放空间或减少场景预算，未开始新的原生场景")
    return result
