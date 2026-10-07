import argparse
import json
import re
import shutil
from pathlib import Path
from analyze_results import candidates, links

ROOT = Path(__file__).resolve().parent


def prepare(name, parking):
    baseline = ROOT / "baseline"
    dest = ROOT / name
    if not dest.is_relative_to(ROOT) or dest.parent != ROOT or dest.exists():
        raise ValueError("Use a new direct child directory of the experiment root")
    selected = candidates(links(baseline / "基础路网"))[0]
    shutil.copytree(baseline, dest)
    path = dest / "基础路网/data/manageL.txt"
    original = path.read_bytes()
    frm, to = selected["from_node"], selected["to_node"]
    pattern = rb"(?m)^(" + str(frm).encode() + rb"[ \t]+" + str(to).encode() + rb"[ \t]+\d+[ \t]+)(\d+)([ \t]+\d+[ \t]*\r?$)"
    updated, count = re.subn(pattern, lambda m: m[1] + str(parking).encode() + m[3], original)
    if count != 1:
        raise ValueError(f"Expected exactly one changed management row, found {count}")
    path.write_bytes(updated)
    proposal = {"scenario": name, "selected_link": selected, "new_parking": parking,
                "changed_file": str(path), "changed_rows": count,
                "selection_rule": "Named urban class 2/3 links; parking 3/4; length 0.1-1.5km; flow>=500pcu/h; 1<VC<=4; speed<40; exclude ramps/numeric labels; rank flow*length/speed",
                "purpose": "Single-variable file-to-model response test, not calibrated real-world policy evaluation"}
    (dest / "proposal.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(proposal, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("parking", type=int, choices=[0, 1, 2])
    args = parser.parse_args()
    prepare(args.name, args.parking)
