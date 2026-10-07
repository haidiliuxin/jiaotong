"""Finish one already-started portable extension: wait, audit, copy, compare.

Never starts or retries a model. The watched process must exit before sealing.
"""
import argparse
import ctypes
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.archive_existing_portable_run import audit
from scripts.compare_portable_extension import compare
from scripts.verify_experiment import verify
from traffic_agent.data import PROJECT, save_json
from traffic_agent.runner import now
from traffic_agent.portable import digest


def watch(protocol_path,name,job_id,pid,environment,evidence,historical):
    protocol=json.loads(protocol_path.read_text(encoding='utf-8'))
    root=Path(protocol['runtime_root']).resolve()
    job=root/'智能体实验/jobs'/job_id
    if job.resolve().parent!=(root/'智能体实验/jobs').resolve():raise ValueError('Job path outside runtime')
    expected=next(t for t in protocol['tasks'] if t['name']==name)['task']
    destination=PROJECT/'智能体实验/portable_runs'/job_id
    if evidence.exists() or destination.exists():raise ValueError('Refuse to overwrite watcher evidence or published job')
    state=json.loads((job/'state.json').read_text(encoding='utf-8'))
    if state['task']!=expected:raise ValueError('Task differs from registered protocol')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
    kernel.WaitForSingleObject.restype=ctypes.c_ulong
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if not handle and ctypes.get_last_error()!=87:raise ctypes.WinError(ctypes.get_last_error())
    evidence.mkdir(parents=True)
    record=dict(status='watching',started=now(),job_id=job_id,extension=name,pid=pid,
                job=str(job),protocol_sha256=digest(protocol_path),new_model_calls=0,
                scope='等待已启动任务，完成后核验复制；不启动或重试模型')
    def persist():save_json(evidence/'watcher.json',record)
    persist()
    try:
        deadline=time.monotonic()+4*3600
        last=None
        while True:
            state=json.loads((job/'state.json').read_text(encoding='utf-8'))
            progress=(state['status'],state['run_count'],state.get('active_scenario'))
            if progress!=last:
                record.update(observed_at=now(),observed_status=progress[0],started_scenarios=progress[1],active_scenario=progress[2])
                persist();print(json.dumps(record,ensure_ascii=False),flush=True);last=progress
            if state['status'] not in ('created','running','completed'):raise ValueError('Model stopped: '+state['status'])
            result=kernel.WaitForSingleObject(handle,0) if handle else 0
            if result==0:
                if state['status']!='completed':raise ValueError('Process exited without a completed job')
                break
            if result!=258:raise ctypes.WinError(ctypes.get_last_error())
            if time.monotonic()>deadline:raise TimeoutError('Observation exceeded four hours; model was not killed or restarted')
            time.sleep(30)
        record['status']='auditing';persist()
        acceptance=audit(root,job_id,environment,evidence/'audit',protocol_path,name)
        if acceptance['status']!='passed':raise ValueError('Acceptance did not pass')
        shutil.copytree(job,destination,ignore=lambda folder,names:['runtime'] if Path(folder)==job and 'runtime' in names else [])
        verification=verify(destination,archived=True,write=False)
        save_json(evidence/'copied_archive_verification.json',verification)
        record.update(status='comparing',copied_to=str(destination),archive_sha256=digest(destination/'archive_manifest.json'))
        persist()
        comparison=compare(historical,destination,evidence/'historical_comparison')
        record.update(status='completed',all_arrays_numerically_exact=comparison['all_arrays_numerically_exact'])
    except BaseException as error:
        record.update(status='failed',error=f'{type(error).__name__}: {error}')
        raise
    finally:
        if handle:kernel.CloseHandle(handle)
        record['finished']=now();persist()
    return record


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('protocol',type=Path)
    parser.add_argument('extension',choices=('oneway','turn'))
    parser.add_argument('job_id')
    parser.add_argument('pid',type=int)
    for name in ('environment','evidence','historical'):parser.add_argument(name,type=Path)
    args=parser.parse_args()
    watch(args.protocol.resolve(),args.extension,args.job_id,args.pid,args.environment.resolve(),args.evidence.resolve(),args.historical.resolve())
