"""Diagnostic-only configuration derived from the shipped x64 Vehicle1 reader.

Not a general schema claim. See docs/诊断证据/Vehicle1参数与阻抗读取.md.
"""
import hashlib
import re

VEHICLE1_SHA256 = "5ea5b879b462a40bbe33ceb89b8fc23da1e286626946c22fe34ba651911e26a9"
DERIVED_FILES = {"params/Mparam635.txt"} | {f"params/AsignParameter_{mode}.dat" for mode in ("car", "taxi", "motor", "truck")}
MODAL_OUTPUTS = tuple(f"cache/{prefix}_{mode}.dat" for prefix in ("Ljt", "Rwm", "volume")
                      for mode in ("car", "taxi", "motor", "truck"))
UNSUPPORTED_TURN_OUTPUTS = tuple(f"result/TVolNode{mode}.tsd" for mode in ("car", "taxi", "motor", "truck", "Veh"))


def prepare_parameters(scenario, executable):
    if hashlib.sha256(executable.read_bytes()).hexdigest() != VEHICLE1_SHA256:
        raise ValueError("Vehicle1二进制版本与已核对参数读取器不符")
    path = scenario / "params/Mparam635.txt"
    if path.exists():
        raise ValueError("不覆盖既有635参数")
    raw = (scenario / "params/Mparam631.txt").read_bytes()
    lines = raw.splitlines()
    if len(lines) != 17 or not lines[0].startswith(b"Mparam631.txt-"):
        raise ValueError("631参考参数必须为表头及八对标签/数值")
    values = [float(lines[i]) for i in range(2, 17, 2)]
    if values != [0, 0, 0, 1, 0, 0, 5, .001]:
        raise ValueError("诊断只接受多路径增量5次固定参数")
    # The observed reader consumes two label lines before the first integer, then
    # label/value pairs; seven int extractions and one double extraction.
    path.write_bytes(raw.replace(b"Mparam631.txt-", b"Mparam635.txt-", 1))
    for mode in ("car", "taxi", "motor", "truck"):
        path = scenario / f"params/AsignParameter_{mode}.dat"
        lines = path.read_bytes().splitlines(keepends=True)
        if len(lines) != 5 or lines[3].strip() != b"7.2":
            raise ValueError("分车型派生参数初值不符")
        # Fourth algorithm flag selects the double at VA 0x1400113f0: 3.3.
        # Set it before both runs so model-generated inputs remain invariant.
        lines[3] = re.sub(rb"7\.2", b"3.3", lines[3], count=1)
        path.write_bytes(b"".join(lines))
    return {"binary_sha256": VEHICLE1_SHA256, "parameters": values, "derived_theta": 3.3,
            "changed_files": sorted(DERIVED_FILES), "schema_source": "shipped x64 reader; not an official supplied Mparam635",
            "limits": "同为5次增量但派生参数与默认程序不同，只在新配对基准内比较"}
