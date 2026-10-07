"""Unzip once and test an application bundle without host Python/Git/gcc paths.

This does not run TranStar. A real native acceptance job is a separate step.
"""
import argparse
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import save_json
from traffic_agent.portable import digest, safe_name, verify_bundle
from traffic_agent.portable_compiler import compiler_environment
from traffic_agent.runner import now


def check_planning_runtime(manifest,planning,root,poison):
    """Check what the bundled interpreter loaded, never the host's imports."""
    if (planning['config']['key_configured'] or not Path(planning['settings_path']).resolve().is_relative_to(poison.resolve())
            or any(not Path(p).resolve().is_relative_to(root.resolve()) for p in planning['modules'].values())):
        raise ValueError('规划模块或API配置隔离失败；没有调用服务')
    expected=manifest.get('planning_capabilities')
    if expected is not None and (not isinstance(expected,dict) or not expected or planning.get('capabilities')!=expected):
        raise ValueError('包内实际加载的规划协议与构建清单不符；没有调用服务')
    return dict(verified=expected is not None,
                note='包内已加载协议与构建清单相同，不代表线上或原生闭环通过' if expected is not None else
                     '旧包未记录规划协议；仅完成模块和配置隔离检查')


def validate(archive,root,evidence,reuse_extracted=False):
    if (root.exists() and not reuse_extracted) or evidence.exists():
        raise FileExistsError('解压目录或验收记录已存在；使用新目录，保留旧记录')
    if reuse_extracted and not root.is_dir():
        raise ValueError('复用模式要求既有完整解压目录')
    root.mkdir(parents=True,exist_ok=reuse_extracted)
    evidence.mkdir(parents=True)
    state=dict(status='running',started=now(),archive=str(archive),archive_sha256=digest(archive),
               extracted_to=str(root),new_model_calls=0,commands=[],checks={},
               test_script_sha256=digest(Path(__file__)),reuse_extracted=reuse_extracted,
               scope='同一台电脑的移动目录和隔离环境检查，尚未真实求解')
    record=evidence/'environment_verification.json'
    save_json(record,state)
    try:
        with zipfile.ZipFile(archive) as z:
            names=[safe_name(i.filename) for i in z.infolist()]
            if len({n.casefold() for n in names}) != len(names):
                raise ValueError('ZIP含重复文件名')
            if any(stat.S_ISLNK(i.external_attr >> 16) for i in z.infolist()):
                raise ValueError('ZIP不接受文件链接')
            if reuse_extracted:
                if z.read('runtime_manifest.json') != (root/'runtime_manifest.json').read_bytes():
                    raise ValueError('既有解压目录的清单与指定ZIP不一致')
            else:
                z.extractall(root)
        state['checks']['initial_integrity']=verify_bundle(root)
        save_json(record,state)
        print(f'EXTRACTED {root}',flush=True)
        poison=evidence/'poison_user'
        marker=poison/'user_site_executed.txt'
        for relative in ('Python312/site-packages','Python/Python312/site-packages'):
            folder=poison/relative
            folder.mkdir(parents=True)
            (folder/'000_isolation_probe.pth').write_text(
                f'import pathlib; pathlib.Path({str(marker)!r}).write_text("user site was executed")\n',encoding='utf-8')
        env=dict(os.environ,PATH=str(Path(os.environ['SystemRoot'])/'System32'),
                 APPDATA=str(poison),LOCALAPPDATA=str(poison/'Local'),PYTHONUSERBASE=str(poison),
                 PYTHONHOME='Z:/missing-python-home',PYTHONPATH='Z:/missing-modules',
                 HTTP_PROXY='http://127.0.0.1:1',HTTPS_PROXY='http://127.0.0.1:1')

        def execute(name,args,environment=env,timeout=90,cwd=evidence):
            proc=subprocess.run([str(a) for a in args],cwd=cwd,env=environment,stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW)
            (evidence/f'{name}.stdout.bin').write_bytes(proc.stdout)
            (evidence/f'{name}.stderr.bin').write_bytes(proc.stderr)
            (evidence/f'{name}.stdout.txt').write_text(proc.stdout.decode('utf-8',errors='replace'),encoding='utf-8')
            (evidence/f'{name}.stderr.txt').write_text(proc.stderr.decode('utf-8',errors='replace'),encoding='utf-8')
            state['commands'].append(dict(name=name,argv=[str(a) for a in args],cwd=str(cwd),returncode=proc.returncode))
            save_json(record,state)
            if proc.returncode:
                raise RuntimeError(f'{name}退出码{proc.returncode}；保留原始输出供检查')
            return proc.stdout

        # A positive control proves this fake user-site .pth really runs in an
        # ordinary interpreter; the bundle must reject it before execution.
        control=dict(env)
        control.pop('PYTHONHOME'); control.pop('PYTHONPATH')
        execute('user_site_control',[Path(sys.base_prefix)/'python.exe','-c','import site; print(site.ENABLE_USER_SITE)'],control)
        if not marker.is_file():
            raise ValueError('用户site正对照未生效，不能宣称隔离探针有效')
        marker.unlink()  # Only this test's known, regenerable marker.
        python=root/'python/python.exe'
        code=('import json,sys,site,traffic_agent.native_access_bridge,traffic_agent.native_turn_bridge; '
              'print(json.dumps(dict(executable=sys.executable,sys_path=sys.path,isolated=sys.flags.isolated,'
              'user_site=site.ENABLE_USER_SITE,no_bytecode=sys.dont_write_bytecode)))')
        probe=json.loads(execute('child_without_B',[python,'-c',code]))
        if marker.exists() or not probe['isolated'] or probe['user_site'] or not probe['no_bytecode']:
            raise ValueError('子解释器隔离或缓存策略失败')
        if any(not Path(p).resolve().is_relative_to(root) for p in probe['sys_path']):
            raise ValueError('子解释器读到了包外模块路径')
        if list(root.rglob('*.pyc')):
            raise ValueError('子解释器仍在写入未登记的字节缓存')
        state['checks']['child_without_B']=probe
        manifest=json.loads((root/'runtime_manifest.json').read_text('utf-8'))
        if manifest.get('application_profile'):
            # Import through the isolated bundled interpreter; do not call the
            # provider or read the developer's protected user configuration.
            code=('import importlib,json; from traffic_agent.llm_settings import Settings; '
                  'names=["traffic_agent.natural_plan","traffic_agent.initial_planner","traffic_agent.feedback_planner","traffic_agent.oneway_planning",'
                  '"traffic_agent.programme_review","scripts.verify_programme","scripts.validate_observed_planning"]; '
                  'print(json.dumps(dict(modules={n:importlib.import_module(n).__file__ for n in names},'
                  'capabilities=getattr(importlib.import_module("traffic_agent.natural_plan"),"capabilities",lambda:None)(),'
                  'config=Settings().public(),settings_path=str(Settings().path))))')
            planning=json.loads(execute('planning_modules_without_key',[python,'-c',code]))
            state['checks']['planning_protocols']=check_planning_runtime(manifest,planning,root,poison)
            state['checks']['planning_without_key']=planning
        execute('bundle_gate',[python,'-m','traffic_agent.portable','--verify-only'])
        for label,card in (('parking','parking_quickstart_v2.json'),('turn','turn_access_grid.json'),('oneway','oneway_changjiang.json')):
            folder=root/'智能体实验/startup'
            before=set(folder.glob('*.json'))
            execute(f'check_{label}',[python,'-m','traffic_agent.portable','--mode','check','--task',root/'examples'/card])
            created=set(folder.glob('*.json'))-before
            if len(created)!=1:
                raise ValueError('启动检查记录不唯一')
            check=json.loads(created.pop().read_text(encoding='utf-8'))
            if check['passed'] is not True or check['code_commit']:
                raise ValueError('依赖未通过或误读取了包外Git仓库')
            if label!='parking' and not Path(check['dependencies']['compiler']['path']).resolve().is_relative_to(root/'portable-tools'):
                raise ValueError('编译器回退到包外位置')
            state['checks'][f'check_{label}']=check
        compiler_updates, compiler_record=compiler_environment(root,env)
        compiler_env=dict(env,**compiler_updates)
        state['checks']['compiler_path']=compiler_record
        shutil.copy2(root/'scripts/native_assignment_wrapper.c',evidence/'wrapper.c')
        execute('compile_wrapper',[root/'portable-tools/gcc.exe','-municode','-static','-Os','wrapper.c','-o','wrapper.exe'],compiler_env)
        binary=(evidence/'wrapper.exe').read_bytes()
        offset=struct.unpack_from('<I',binary,0x3c)[0]
        if binary[:2]!=b'MZ' or binary[offset:offset+4]!=b'PE\0\0' or struct.unpack_from('<H',binary,offset+4)[0]!=0x8664:
            raise ValueError('包装器不是预定的Windows x64程序')
        state['checks']['wrapper']=dict(sha256=digest(evidence/'wrapper.exe'),machine='x86_64',
                                        source_sha256=digest(evidence/'wrapper.c'))
        state['checks']['final_integrity']=verify_bundle(root)
        if marker.exists():
            raise ValueError('验收过程中用户site被执行')
        state['status']='passed'
    except BaseException as exc:
        state.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        state['finished']=now()
        save_json(record,state)
        print(json.dumps(dict(record=str(record),status=state['status'],error=state.get('error')),ensure_ascii=False),flush=True)
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='移动目录验收便携Python、用户site隔离和原生编译依赖')
    parser.add_argument('archive',type=Path)
    parser.add_argument('root',type=Path)
    parser.add_argument('evidence',type=Path)
    parser.add_argument('--reuse-extracted',action='store_true',help='复用已解压副本；仍重核ZIP清单及全部不可变文件')
    args=parser.parse_args()
    validate(args.archive.resolve(),args.root.resolve(),args.evidence.resolve(),args.reuse_extracted)
