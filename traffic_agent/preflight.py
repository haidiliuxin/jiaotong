"""Read-only startup checks for the selected task; never starts TranStar."""
import hashlib
import os
import shutil
import subprocess
import sys

from .runner import MODULES, RUNTIME_SOURCE, validate_source
from .task import capability_for
from .vehicle1 import VEHICLE1_SHA256


def check(task, source, runtime_source=RUNTIME_SOURCE):
    source_check = validate_source(source)
    bridge = task.scenario == 'oneway' or (task.scenario == 'circulation' and task.turn is not None)
    filenames = list(MODULES)
    if bridge:
        filenames = ['M6AssignVehicle1.exe' if name == 'M6AssignVehicle.exe' else name for name in filenames]
        filenames += [f'NodeAssignModel_{mode}.exe' for mode in ('car', 'taxi', 'motor', 'truck')]
    errors, binaries = [], {}
    if os.name != 'nt':
        errors.append('实际模型运行需要Windows；本平台仍可运行规则测试和任务预览')
    for filename in filenames:
        path = runtime_source/'programs'/filename
        if not path.is_file():
            binaries[filename] = 'missing'
            errors.append(f'缺少运行程序：{filename}')
            continue
        with path.open('rb') as stream:
            header = stream.read(64)
        if header[:2] != b'MZ':
            binaries[filename] = 'lfs_pointer' if header.startswith(b'version https://git-lfs') else 'not_pe'
            errors.append(f'{filename}不是完整Windows程序；若为Git克隆，请先git lfs pull')
        else:
            binaries[filename] = 'pe_present'
    if bridge and binaries.get('M6AssignVehicle1.exe') == 'pe_present':
        with (runtime_source/'programs/M6AssignVehicle1.exe').open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != VEHICLE1_SHA256:
                errors.append('Vehicle1版本不是已验证的参数读取器版本')
    compiler = dict(required=bridge, path=None, target=None)
    if bridge:
        compiler['path'] = shutil.which('gcc')
        if not compiler['path']:
            errors.append('本任务需要PATH中的MinGW gcc以编译原生调用包装器')
        else:
            try:
                result = subprocess.run([compiler['path'], '-dumpmachine'], capture_output=True, timeout=10,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                compiler['target'] = result.stdout.decode('ascii', errors='replace').strip()
                if result.returncode or 'mingw' not in compiler['target'].lower():
                    errors.append('gcc未报告MinGW目标；请按操作说明配置Windows编译器')
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(f'无法检查gcc：{type(exc).__name__}')
    return dict(preflight_passed=not errors, source_validation=source_check, task_scenario=task.scenario,
                capability=capability_for(task), python=sys.version.split()[0], python_executable=sys.executable,
                platform=os.name, runtime_source=str(runtime_source), programs=binaries, compiler=compiler, errors=errors,
                limits='只检查启动依赖和源数据，不调用模型、不证明试用授权当前有效，也不替代场景实际响应验收')
