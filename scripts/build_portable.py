"""Build a new immutable runtime ZIP from installed, locally verified inputs."""
import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import PROJECT, input_fingerprints
from traffic_agent.portable import KIND, MANIFEST, PLANNING_PROFILE, digest, safe_name, verify_bundle
from traffic_agent.runner import DEFAULT_SOURCE, MODULES, RUNTIME_SOURCE, now, validate_source
from traffic_agent.natural_plan import capabilities


def copy_tree(source, destination, ignored=()):
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if any(part in ignored for part in relative.parts) or path.suffix in ('.pyc','.pyo'):
            continue
        if path.is_file():
            if not path.resolve().is_relative_to(source.resolve()):
                raise ValueError(f'构建源存在越界文件链接：{path}')
            target = destination/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target)


def package_python(python_root, py):
    """Zip stdlib so site startup itself cannot create untracked byte caches.

    Native wrappers launch a fresh interpreter without our launcher's -B flag.
    The bundled site hook applies to those children too. Isolated ._pth paths
    and disabled user site keep installed host packages outside this runtime.
    """
    py.mkdir()
    for name in ('python.exe','pythonw.exe','python3.dll','python312.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt'):
        shutil.copy2(python_root/name,py/name)
    for name in ('DLLs','tcl'):
        copy_tree(python_root/name,py/name,ignored=('__pycache__',))
    with zipfile.ZipFile(py/'python312.zip','x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        library=python_root/'Lib'
        for path in sorted(library.rglob('*')):
            relative=path.relative_to(library)
            if any(p in ('site-packages','__pycache__','test') for p in relative.parts):
                continue
            if path.is_file() and path.suffix not in ('.pyc','.pyo') and path.name not in ('sitecustomize.py','usercustomize.py'):
                z.write(path,relative.as_posix())
        z.writestr('sitecustomize.py','import sys\nsys.dont_write_bytecode = True\n')
    (py/'python312._pth').write_text('python312.zip\n.\nDLLs\n..\nimport site\n',encoding='utf-8')
    # CPython 3.12's ._pth isolated flag alone still permits the user-site
    # directory when import site is enabled. venv() processes this local
    # marker before user-site .pth files; no developer-machine home is stored.
    (py/'pyvenv.cfg').write_text('include-system-site-packages = false\n',encoding='utf-8')


def check_application_credentials(root):
    """No per-user credential file or literal service key in shipped app files."""
    files=[]
    for directory in ('traffic_agent','config','examples','scripts'):
        files.extend(p for p in (root/directory).rglob('*') if p.is_file())
    files.extend(p for p in (root/'start.ps1',root/'开始使用.md') if p.is_file())
    pattern=re.compile(rb'(?<![A-Za-z0-9_])sk[-_][A-Za-z0-9_-]{20,}')
    for path in files:
        if path.name.casefold() in ('llm.json','.env') or pattern.search(path.read_bytes()):
            raise ValueError('应用文件包含本机凭据或疑似服务密钥；停止打包，不输出内容')
    return dict(passed=True,application_files=len(files),scope='应用源码、配置、示例、脚本及使用说明；本机用户配置目录不进入构建源')


def build(output, python_root, compiler_root):
    output = output.resolve()
    archive = output.with_suffix('.zip')
    if output.exists() or archive.exists():
        raise FileExistsError('输出或同名ZIP已存在；不覆盖已发布运行包')
    checked = subprocess.run(['git','status','--porcelain','--','traffic_agent','scripts','examples','config','start.ps1'],
                             cwd=PROJECT,capture_output=True,text=True,check=True)
    if checked.stdout.strip():
        raise ValueError('先提交运行源码，再构建可追溯运行包')
    commit = subprocess.run(['git','rev-parse','HEAD'],cwd=PROJECT,capture_output=True,text=True,check=True).stdout.strip()
    version = subprocess.run([str(python_root/'python.exe'),'-I','-c','import sys; print(sys.version)'],
                             capture_output=True,text=True,check=True).stdout.strip()
    if not version.startswith('3.12.'):
        raise ValueError('交付环境须为已验证的Python 3.12')
    source_check = validate_source(DEFAULT_SOURCE)
    print(f'BUILD {output} | {commit}',flush=True)
    output.mkdir(parents=True,exist_ok=False)
    for name in ('traffic_agent','examples','config'):
        copy_tree(PROJECT/name,output/name,ignored=('__pycache__',))
    for name in ('start.ps1','scripts/verify_experiment.py','scripts/verify_programme.py','scripts/validate_observed_planning.py',
                 'scripts/native_assignment_wrapper.c','scripts/portable_gcc_launcher.c'):
        target=output/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(PROJECT/name,target)
    copy_tree(DEFAULT_SOURCE,output/DEFAULT_SOURCE.relative_to(PROJECT))
    copy_tree(RUNTIME_SOURCE,output/RUNTIME_SOURCE.relative_to(PROJECT))
    copy_tree(PROJECT/'南京市案例数据库/南京路网shp',output/'南京市案例数据库/南京路网shp')
    print('Copied native programs and original case bytes',flush=True)
    py=output/'python'
    package_python(python_root,py)
    if not (compiler_root/'bin/gcc.exe').is_file() or not (compiler_root/'licenses').is_dir():
        raise ValueError('缺少MinGW编译器或随附许可证目录')
    copy_tree(compiler_root,output/'toolchain',ignored=('__pycache__',))
    (output/'portable-tools').mkdir()
    # Build in a temporary working directory: gcc gets an ASCII source
    # basename, and the generated executable is copied without rewriting it.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='luce-gcc-launcher-') as temp:
        build_dir=Path(temp)
        shutil.copy2(PROJECT/'scripts/portable_gcc_launcher.c',build_dir/'launcher.c')
        subprocess.run([str(compiler_root/'bin/gcc.exe'),'-municode','-static','-Os','launcher.c','-o','gcc.exe'],
                                cwd=build_dir,capture_output=True,check=True)
        shutil.copy2(build_dir/'gcc.exe',output/'portable-tools/gcc.exe')
    print('Copied isolated Python and MinGW including licenses',flush=True)
    (output/'launch.cmd').write_bytes(b'@echo off\r\nsetlocal\r\n"%~dp0python\\python.exe" -B -m traffic_agent.portable %*\r\nset "launch_result=%ERRORLEVEL%"\r\nif not "%launch_result%"=="0" pause\r\nexit /b %launch_result%\r\n')
    guide = '''# 路策智能规划便携候选包

本包是当前源码的可移动候选版本。新结论检查已用真实服务对既有原生观测单独复核；当前源码尚未完成一次新的模型规划加原生执行全闭环，也未在另一台物理电脑验收。文件完整性与同机环境检查不等于交通治理有效，也不等于整套产品已完成。

在本目录双击 launch.cmd，或运行 launch.cmd --mode serve。默认网址 http://127.0.0.1:8790/。
保留启动窗口；在窗口按 Ctrl+C 停止服务。模型运行期间先等待任务结束。

无需安装系统Python、Git或MinGW；Python标准库、原生运行文件、完整案例、地图和编译器均在本包内。
只在Windows运行。软件仍通过原生授权机制正常检查；依赖检查不代表真实求解已通过。

## 自然语言智能规划

大厅默认“大模型自主规划”，只需在输入框描述道路、问题、允许措施、需求和预算，然后点击“自动编排并运行”。无需先选择场景。
先在右上角“API 设置”中填写地址、接口类型、模型和自己的密钥。当前指定服务为 https://tokenrhythm.studio/v1 ，接口 Chat Completions，模型 deepseek-v4-flash。
密钥保存在本机当前 Windows 用户的受保护配置中，不随包迁移，也不会写入实验报告。另一台电脑需要重新填写；同一 Windows 用户的多个副本共用这份用户配置，保存／清除会影响这些副本。
“测试连接”和“自动编排并运行”会消耗模型额度。不要反复测试连接；未配置密钥时可以查看地图、档案、做本地依赖检查，或显式切换规则模式。

当前支持路网体检、停车强度／区域比较、单向与停车组合、指定道路的禁转对照，以及需求压力、稳健和邻区保护研究。它们是 TranStar 批次实验，不是连续实时交通控制；施工、信号配时和公交优先没有接入底层执行。
停车由模型依据新基准与后续结果选择位置和等级；单向由模型依据新基准的合法候选选择试验方向，再执行完整单项与组合对照。禁转位置在初始规划中确定。不同适配器分别标注，不混算收益。
新任务默认最多15次仿真、7次模型请求，用户可以在文字中降低仿真预算。费用与失败在右栏保留；空规划、错误回复或超时不会自动重试，也不会静默切成规则结果。历史任务仍按其冻结的6次协议回放。
初始提议的定位前提错误，或预算存在保留治理设计的修正空间时，最多向模型反馈修正一次，计入新任务7次总额；原提议、拒绝原因和成本明细保留。修正不能删去必做研究、降低条件或提高预算，接口失败不重试。
当前组完成必需对照后，模型可在一次回复中核对本组观测与实测代价、选择下一研究组。多组任务的原始假设在全部研究结束后统一复核，后续组仍须运行自己的新基准；没有完整实测和最终复核时保持未完成，即使局部改善也不能标为通过。
本组交接与最终复核共享一次修正机会，计入新任务7次模型请求上限。交接修正须本组对照已齐，并为剩余研究及最终复核保留足够额度；保留原选定下一组，不增加本组仿真、不改实测或阈值。首次错误保留，第二次仍失败则保持未完成。
地图对照、结果解释及限额验收步骤见同目录《智能规划操作与验收指南.md》。

可以先尝试描述：“请只做路网体检，需求系数：1，最多2次仿真，不复跑。”这是真实任务，会消耗模型额度和一次原生计算；不是免费演示。

```powershell
# 只核对交付文件，不求解
./python/python.exe -B -m traffic_agent.portable --verify-only
# 环境、模型程序、案例、写入位置及编译器检查
./python/python.exe -B -m traffic_agent.portable --mode check
./python/python.exe -B -m traffic_agent.portable --mode check --task examples/turn_access_grid.json
# 新建四场景停车真实验收，约需数分钟，以实际运行时间为准
./python/python.exe -B -m traffic_agent.portable --mode run
# 选择全网毛新增过饱和上限为0.1km的示例任务；会新建真实模型任务
./python/python.exe -B -m traffic_agent.portable --mode run --task examples/parking_network_guard_quickstart.json
# 对实际生成的任务独立重算并封存；用真实任务编号替换占位符
./python/python.exe -B scripts/verify_experiment.py 智能体实验/jobs/<任务编号> --seal
# 大模型整个研究任务的原件和时序核验；必须已经完整结束
./python/python.exe -B scripts/verify_programme.py <总任务编号> --no-write
```

所有新输出写在本包的“智能体实验”目录，原始案例保持不变。请完整解压到可写目录，不要在ZIP内部直接启动。
启动前检查 runtime_manifest.json 中所有不可变文件的大小和SHA256；若文件缺失、改变或仍是LFS指针，将停止启动。
Python只从包内加载模块；包内启动钩子对原生适配器调用的子解释器同样关闭字节缓存写入。路径可移动，但不得编辑已封存程序、源数据或清单来修复校验失败。

本包提供规则CLI和大模型网页流程；上面 --mode run 是规则任务，不调用大模型，不能把它的成功计为大模型规划通过。显式禁转及单向任务的依赖检查会使用所附MinGW，实际可用性仍由每次模型响应和约束核对决定。
网页左侧可选“全网毛新增过饱和上限”；留空沿用原决策约束。示例任务卡的0.1km只是本次验证假设，不是赛事官方阈值。
编译器位于中文目录时，启动器在临时目录创建指向包内编译器的英文路径映射，并在正常退出时移除映射。编译代理只增加路径定位参数，原模型和原包装器源码保持不变。若当前临时目录无法提供英文路径，请解压到纯英文目录后启动。
前瞻研究批处理、出版PDF和统计绘图的开发工具仍从完整仓库使用；本包不包含历史数十GB实验。

runtime_manifest.json 标明源代码提交、规划协议、Python版本、原生程序和输入摘要。新机器请保留portable_launch、startup和完整JOB记录。
同机移动目录验证不能当作另一台电脑重跑。图表及报告中的交通结果只代表赛事调整案例。
Python许可证在python/LICENSE.txt；MinGW许可证在toolchain/licenses；原生软件使用其原有许可。
'''
    (output/'开始使用.md').write_text(guide,encoding='utf-8')
    shutil.copy2(PROJECT/'docs/智能规划操作与验收指南.md',output/'智能规划操作与验收指南.md')
    credentials_check=check_application_credentials(output)
    files={}
    for path in sorted(output.rglob('*')):
        if path.is_file():
            name=safe_name(path.relative_to(output).as_posix())
            files[name]=dict(bytes=path.stat().st_size,sha256=digest(path))
    manifest=dict(kind=KIND,created_at=now(),code_commit=commit,python_version=version,
                  application_profile=PLANNING_PROFILE,release_status='candidate_pending_live_acceptance',
                  planning_capabilities=capabilities(),
                  credential_scan=credentials_check,
                  python_layout='isolated_pth_zipped_stdlib_no_bytecode_for_children',
                  compiler_path_policy='transient_ascii_junction_with_no_canonical_prefixes',
                  source_validation=source_check,source_inputs=input_fingerprints(DEFAULT_SOURCE),
                  native_sha256={m:digest(RUNTIME_SOURCE/'programs'/m) for m in MODULES},
                  compiler_target=subprocess.run([str(compiler_root/'bin/gcc.exe'),'-dumpmachine'],capture_output=True,text=True,check=True).stdout.strip(),
                  compiler_version=subprocess.run([str(compiler_root/'bin/gcc.exe'),'-dumpfullversion'],capture_output=True,text=True,check=True).stdout.strip(),
                  files=files,meaning='运行文件完整性清单；不包含新机器真实运行证明，非数字签名')
    (output/MANIFEST).write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(verify_bundle(output),ensure_ascii=False),flush=True)
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                z.write(path,path.relative_to(output).as_posix())
    print(json.dumps(dict(archive=str(archive),bytes=archive.stat().st_size,sha256=digest(archive)),ensure_ascii=False),flush=True)
    return output


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='制作新的便携主程序，保留案例与原生依赖')
    parser.add_argument('output',type=Path)
    parser.add_argument('--python-root',type=Path,default=Path(sys.base_prefix))
    parser.add_argument('--compiler-root',type=Path,default=Path('C:/mingw64'))
    args=parser.parse_args()
    build(args.output,args.python_root.resolve(),args.compiler_root.resolve())
