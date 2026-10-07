"""Re-solve archived pre-transform inputs to check source-allocation assumptions."""
import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, read_text, save_json
from traffic_agent.runner import EXPERIMENTS, now, runtime_lock, setup_runtime
from scripts.probe_node_cost import fingerprints
from scripts.verify_turn_terminals import balances, compare_balance


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--all-stages',action='store_true')
    args=parser.parse_args()
    source=EXPERIMENTS/'probes/20260908_092003_full_turn_access'
    job=EXPERIMENTS/'probes'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_turn_reference')
    job.mkdir(parents=True,exist_ok=False)
    state=dict(status='created',started=now(),source=str(source),runs=[],all_stages=args.all_stages)
    save_json(job/'state.json',state)
    print(f'PROBE {job}',flush=True)
    active=None
    with runtime_lock(EXPERIMENTS):
        runtime=setup_runtime(job)
        config=runtime/'files/runinfo_vc6.txt'
        original=config.read_bytes()
        try:
            for mode in ('car','taxi','motor','truck'):
                files=sorted((source/'bridge_runs/baseline').glob(f'*_{mode}_*/manifest.json'))
                for stage,file in enumerate(files if args.all_stages else files[:1],1):
                    name=f'{mode}_{stage}'
                    archive=file.parent
                    manifest=json.loads(file.read_text(encoding='utf-8'))
                    scenario=job/'scenarios'/name
                    shutil.copytree(archive/'original_inputs',scenario)
                    before=fingerprints(scenario)
                    if before!=manifest['input_hashes']:
                        raise ValueError('原始输入与冻结摘要不一致')
                    logs=job/'logs'/name
                    logs.mkdir(parents=True)
                    save_json(logs/'inputs_before.json',before)
                    config.write_bytes(('scenario_dir\r\n'+scenario.as_posix()+'\r\n').encode('gb18030'))
                    record=dict(name=name,mode=mode,stage=stage,archive=str(archive),status='running',started=now())
                    state['status']='running';state['runs'].append(record)
                    stamp=time.time_ns()
                    with (logs/'stdout.bin').open('wb') as out,(logs/'stderr.bin').open('wb') as err:
                        active=subprocess.Popen([str(runtime/'programs'/f'NodeAssignModel_{mode}.exe')],cwd=runtime/'programs',
                                                stdin=subprocess.DEVNULL,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
                        record['pid']=active.pid;save_json(job/'state.json',state)
                        record['returncode']=active.wait(timeout=60)
                    target=scenario/f'cache/volume_{mode}.dat'
                    if record['returncode'] or not target.exists() or target.stat().st_mtime_ns<stamp:
                        raise ValueError('原生参考求解失败')
                    after=fingerprints(scenario)
                    if {p for p in before.keys()|after.keys() if before.get(p)!=after.get(p)}!={f'cache/volume_{mode}.dat'}:
                        raise ValueError('原生参考改写输入')
                    ft=[tuple(map(int,l.split())) for l in read_text(scenario/'cache/FT.dat').splitlines()]
                    reference=numeric(target);adapted=numeric(archive/'expanded_volume.dat')
                    bv,be=balances(ft,reference);av,ae=balances(ft,adapted)
                    comparison=compare_balance(ft,adapted,reference)
                    save_json(logs/'node_balance_comparison.json',comparison)
                    save_json(logs/'files_after.json',after)
                    record.update(status='completed',finished=now(),reference_via_net=bv[5990],adapted_via_net=av[5990],
                                  via_rounding_bound=be[5990]+ae[5990],failed_balance_nodes=comparison['failed_nodes'],
                                  max_balance_difference=comparison['max_balance_difference'])
                    save_json(job/'state.json',state)
                    print(json.dumps(record,ensure_ascii=False),flush=True)
            state['status']='completed'
        except BaseException as exc:
            state.update(status='failed',error=f'{type(exc).__name__}: {exc}');raise
        finally:
            if active is not None and active.poll() is None:
                active.kill();active.wait(timeout=10)
            config.write_bytes(original)
            state['finished']=now();save_json(job/'state.json',state)


if __name__=='__main__':main()
