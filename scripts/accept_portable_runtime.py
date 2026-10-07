"""Join one existing study, then run and seal one portable parking acceptance.

This single-use pipeline never restarts a study, retries a model, or kills a
still-running child when an observation deadline expires.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import PROJECT, input_fingerprints, save_json
from traffic_agent.portable import MANIFEST, digest, verify_bundle
from traffic_agent.runner import DEFAULT_SOURCE, now


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def accept(root, environment_record, predecessor, evidence, wait_seconds):
    evidence.mkdir(parents=True,exist_ok=False)
    record=evidence/'acceptance.json'
    state=dict(status='checking',started=now(),root=str(root),predecessor=str(predecessor),
               environment_record=str(environment_record),environment_sha256=digest(environment_record),
               script_sha256=digest(Path(__file__)),commands=[],new_model_calls=0,
               scope='同机解压目录的隔离环境停车真实验收；不是异机求解或禁转/单向完整响应验收')
    save_json(record,state)
    try:
        validation=read(environment_record)
        if validation['status']!='passed' or Path(validation['extracted_to']).resolve()!=root:
            raise ValueError('需要该解压目录已通过环境及真实编译检查的记录')
        if validation['checks']['final_integrity']['manifest_sha256']!=digest(root/MANIFEST):
            raise ValueError('解压目录清单与环境检查记录不一致')
        state['initial_integrity']=verify_bundle(root)
        manifest=read(root/MANIFEST)
        state['code_commit']=manifest['code_commit']
        deadline=time.monotonic()+wait_seconds
        state['status']='waiting_for_study'
        save_json(record,state)
        while True:
            previous=read(predecessor/'state.json')
            if previous.get('kind')!='prospective_backtracking_challenge':
                raise ValueError('前序不是指定的真实三策略挑战')
            if previous['status']=='failed':
                raise RuntimeError('前序实验失败；保留现场，不自动推进新模型')
            if previous['status']=='completed' and (predecessor/'archive_manifest.json').is_file():
                verified=read(predecessor/'verification.json')
                if verified.get('passed') is not True:
                    raise ValueError('前序核验未通过')
                state['predecessor_archive_sha256']=digest(predecessor/'archive_manifest.json')
                break
            if time.monotonic()>=deadline:
                raise TimeoutError('前序仍未完成封存；未启动新模型，不重启前序任务')
            time.sleep(min(5,max(0,deadline-time.monotonic())))

        env=dict(os.environ,PATH=str(Path(os.environ['SystemRoot'])/'System32'),
                 APPDATA=str(environment_record.parent/'poison_user'),
                 PYTHONUSERBASE=str(environment_record.parent/'poison_user'),
                 PYTHONHOME='Z:/missing-python-home',PYTHONPATH='Z:/missing-modules',
                 HTTP_PROXY='http://127.0.0.1:1',HTTPS_PROXY='http://127.0.0.1:1')
        python=root/'python/python.exe'

        def execute(name,args,timeout):
            with (evidence/f'{name}.stdout.bin').open('xb') as stdout, (evidence/f'{name}.stderr.bin').open('xb') as stderr:
                proc=subprocess.Popen([str(a) for a in args],cwd=root,env=env,stdin=subprocess.DEVNULL,
                                      stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
                command=dict(name=name,argv=[str(a) for a in args],pid=proc.pid,started=now(),cwd=str(root))
                state['commands'].append(command)
                save_json(record,state)
                try:
                    command['returncode']=proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    command['observation_timeout']=True
                    raise TimeoutError(f'{name}观察超时；进程{proc.pid}可能仍运行，禁止自动重试') from exc
                finally:
                    command['observed_at']=now()
                    save_json(record,state)
                if command['returncode']:
                    raise RuntimeError(f'{name}退出{command["returncode"]}；保留全部输出')

        before=set((root/'智能体实验/jobs').glob('*/state.json'))
        state['status']='running_model'
        state['new_model_calls']=None  # Unknown until the completed job is read.
        execute('parking_run',[python,'-B','-m','traffic_agent.portable','--mode','run'],7200)
        created=set((root/'智能体实验/jobs').glob('*/state.json'))-before
        if len(created)!=1:
            raise ValueError('本次新增任务不唯一；不推测应封存哪个任务')
        job=created.pop().parent
        result=read(job/'state.json')
        state['job']=str(job)
        state['new_model_calls']=sum(len(r['execution']['modules']) for r in result['runs'])
        if result['status']!='completed' or len(result['runs'])!=4 or state['new_model_calls']!=28:
            raise ValueError('停车四场景/28次原生调用未完整完成')
        state['status']='auditing'
        execute('seal',[python,'-B',root/'scripts/verify_experiment.py',job,'--seal'],300)
        execute('archive_recheck',[python,'-B',root/'scripts/verify_experiment.py',job,'--archive'],300)
        state['verification']=read(job/'portable_verification.json')
        state['job_archive_sha256']=digest(job/'archive_manifest.json')
        if input_fingerprints(root/DEFAULT_SOURCE.relative_to(PROJECT))!=manifest['source_inputs']:
            raise ValueError('原始案例输入发生变化')
        state['source_inputs_unchanged']=True
        state['final_integrity']=verify_bundle(root)
        if (environment_record.parent/'poison_user/user_site_executed.txt').exists():
            raise ValueError('运行过程中用户site隔离探针被执行')
        state['status']='passed'
    except BaseException as exc:
        state.update(status='attention_required' if isinstance(exc,TimeoutError) else 'failed',
                     error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        state['finished_or_yielded']=now()
        save_json(record,state)
        print(json.dumps(dict(record=str(record),status=state['status'],job=state.get('job'),
                              new_model_calls=state['new_model_calls'],error=state.get('error')),ensure_ascii=False),flush=True)
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='前序挑战封存后，执行一次便携停车真实验收并独立核验')
    for name in ('root','environment_record','predecessor','evidence'):
        parser.add_argument(name,type=Path)
    parser.add_argument('--wait-seconds',type=int,default=10800)
    args=parser.parse_args()
    if not 0<=args.wait_seconds<=21600:
        parser.error('等待期限为0至21600秒')
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    accept(args.root.resolve(),args.environment_record.resolve(),args.predecessor.resolve(),
           args.evidence.resolve(),args.wait_seconds)
