"""Private native turn adapter; archives raw solving before physical-edge mapping."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.circulation import restrictions
from traffic_agent.data import numeric, read_text, save_json
from traffic_agent.runner import now
from traffic_agent.turn_graph import transform

MODES = ('car', 'taxi', 'motor', 'truck')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def bridge(mode, config):
    if mode not in MODES:
        raise ValueError('未知原生车型')
    runtime = Path(config['runtime'])
    scenario = Path(read_text(runtime/'files/runinfo_vc6.txt').splitlines()[1])
    folder = Path(config['archive'])/f'{time.time_ns()}_{mode}_{os.getpid()}'
    folder.mkdir(parents=True, exist_ok=False)
    scratch = folder/'scenario'
    via = config['via']
    manifest = dict(schema_version=2, status='running', started=now(), mode=mode, pid=os.getpid(),
                    outer_scenario=str(scenario), via=via, base_turns=config['base_turns'],
                    added_turns=config['added_turns'], no_uturn=True,
                    bridge_source_sha256=digest(Path(__file__).read_bytes()),
                    graph_source_sha256=digest(Path(__file__).with_name('turn_graph.py').read_bytes()))
    save_json(folder/'manifest.json', manifest)
    active = None
    try:
        actual_turns = {t for t in restrictions(scenario/'data/ManageN.txt') if t[1] == via}
        base = set(map(tuple, config['base_turns']))
        added = set(map(tuple, config['added_turns']))
        if base & added or actual_turns != base | added:
            raise ValueError('场景禁转与已冻结规则不一致')
        names = ('data/number.txt', f'params/AsignParameter_{mode}.dat', 'cache/NodeInZone.dat',
                 'cache/Shortest_No.dat', 'cache/rnode.dat', 'cache/tv.dat', 'cache/n_r.dat',
                 'cache/szn.dat', f'cache/Assign_{mode}.od', f'cache/Rwm_{mode}.dat', 'cache/FT.dat')
        raw = {name: (scenario/name).read_bytes() for name in names}
        if any((scenario/name).read_bytes() != data for name, data in raw.items()):
            raise ValueError('读取窗口内原生输入变化')
        parameters = raw[f'params/AsignParameter_{mode}.dat'].decode('ascii').splitlines()
        if parameters[:3] != [f'Assign_{mode}.od', f'Rwm_{mode}.dat', f'volume_{mode}.dat']:
            raise ValueError('未验证的原生输入文件名')
        ft = [tuple(map(int, line.split())) for line in raw['cache/FT.dat'].splitlines()]
        no_uturn = {(u, via, u) for u, v in ft if v == via and (via, u) in set(ft)}
        changed, mapping = transform(raw, mode, via, base | added | no_uturn)
        for name, data in raw.items():
            path = folder/'original_inputs'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            reference = folder/'reference_scenario'/name
            reference.parent.mkdir(parents=True, exist_ok=True)
            reference.write_bytes(data)
        for name, data in changed.items():
            path = scratch/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        save_json(folder/'graph_mapping.json', mapping)
        before = {name: digest(data) for name, data in changed.items()}
        save_json(folder/'native_inputs.json', before)
        programs = folder/'runtime/programs'
        programs.mkdir(parents=True)
        for filename in ('M1-StarNet.exe', 'date1.txt', 'date2.txt'):
            shutil.copy2(runtime/'programs'/filename, programs/filename)
        child_config = folder/'runtime/files/runinfo_vc6.txt'
        child_config.parent.mkdir()
        child_config.write_bytes(('scenario_dir\r\n'+scratch.as_posix()+'\r\n').encode('gb18030'))
        exe = Path(config['native_programs'])/f'NodeAssignModel_{mode}.exe'
        manifest.update(input_hashes={name: digest(data) for name, data in raw.items()},
                        native_executable_sha256=digest(exe.read_bytes()),
                        vendor_support_hashes={f: digest((programs/f).read_bytes()) for f in ('M1-StarNet.exe','date1.txt','date2.txt')},
                        original_rows=len(ft), native_rows=len(mapping['edges']))
        save_json(folder/'manifest.json', manifest)
        # Establish the original solver's actual source/sink balance on these
        # exact frozen costs. NodeInZone does not imply a uniform allocation in
        # every shipped mode; do not transfer a car-only assumption to motors.
        reference = folder/'reference_scenario'
        child_config.write_bytes(('scenario_dir\r\n'+reference.as_posix()+'\r\n').encode('gb18030'))
        reference_stamp = time.time_ns()
        with (folder/'reference_stdout.bin').open('wb') as out, (folder/'reference_stderr.bin').open('wb') as err:
            active = subprocess.Popen([str(exe)], cwd=programs, stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
            manifest['reference_pid'] = active.pid
            save_json(folder/'manifest.json', manifest)
            manifest['reference_returncode'] = active.wait(timeout=60)
        reference_output = reference/f'cache/volume_{mode}.dat'
        if (manifest['reference_returncode'] or not reference_output.exists()
                or reference_output.stat().st_mtime_ns < reference_stamp
                or any(b'[error]' in (folder/f'reference_{stream}.bin').read_bytes().lower() for stream in ('stdout','stderr'))
                or any((reference/name).read_bytes() != data for name,data in raw.items())):
            raise ValueError('同输入原生参考未完成或改写输入')
        manifest['reference_output_sha256'] = digest(reference_output.read_bytes())
        child_config.write_bytes(('scenario_dir\r\n'+scratch.as_posix()+'\r\n').encode('gb18030'))
        save_json(folder/'manifest.json', manifest)
        stamp = time.time_ns()
        with (folder/'stdout.bin').open('wb') as out, (folder/'stderr.bin').open('wb') as err:
            active = subprocess.Popen([str(exe)], cwd=programs, stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
            manifest['native_pid'] = active.pid
            save_json(folder/'manifest.json', manifest)
            manifest['returncode'] = active.wait(timeout=60)
        output = scratch/f'cache/volume_{mode}.dat'
        if manifest['returncode'] or not output.exists() or output.stat().st_mtime_ns < stamp or any(
                b'[error]' in (folder/f'{stream}.bin').read_bytes().lower() for stream in ('stdout', 'stderr')):
            raise ValueError('原生程序失败或缺少新输出')
        if any(digest((scratch/name).read_bytes()) != h for name, h in before.items()):
            raise ValueError('原生程序改写派生输入')
        values = numeric(output)
        lines = output.read_bytes().splitlines(keepends=True)
        if len(values) != len(mapping['edges']) or len(lines) != len(values) or min(values) < 0:
            raise ValueError('原生输出维数或数值错误')
        physical = [None]*len(ft)
        turns = []
        for edge, line, value in zip(mapping['edges'], lines, values):
            if edge['physical_index'] is not None:
                physical[edge['physical_index']] = line
            elif edge['turn']:
                turns.append(dict(turn=edge['turn'], flow=value))
        if any(line is None for line in physical):
            raise ValueError('原始道路映射不完整')
        expanded = b''.join(physical)
        (folder/'expanded_volume.dat').write_bytes(expanded)
        save_json(folder/'turn_flows.json', turns)
        temporary = scenario/f'cache/volume_{mode}.turn.tmp'
        temporary.write_bytes(expanded)
        temporary.replace(scenario/f'cache/volume_{mode}.dat')
        manifest.update(status='completed', native_output_sha256=digest(output.read_bytes()),
                        expanded_output_sha256=digest(expanded))
    except BaseException as exc:
        manifest.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if active is not None and active.poll() is None:
            active.kill()
            active.wait(timeout=10)
        manifest['finished'] = now()
        save_json(folder/'manifest.json', manifest)


if __name__ == '__main__':
    bridge(sys.argv[1], json.loads(Path(os.environ['TRAFFIC_AGENT_BRIDGE_CONFIG']).read_text(encoding='utf-8')))
