"""Byte-preserved public records; no historical Git LFS archives required."""
import hashlib
import json
from pathlib import Path

DIRECTORY = Path(__file__).with_name('fixtures')/'sealed_planning'


def load_sealed(stem):
    manifest = json.loads((DIRECTORY/'manifest.json').read_text('utf-8'))
    records = []
    for name, origin in sorted(manifest['files'].items()):
        if not name.startswith(stem+'__'):
            continue
        raw = (DIRECTORY/name).read_bytes()
        if len(raw) != origin['bytes'] or hashlib.sha256(raw).hexdigest() != origin['sha256']:
            raise ValueError('Sealed planning fixture bytes changed: '+name)
        records.append(json.loads(raw))
    if not records:
        raise ValueError('Missing sealed planning fixture: '+stem)
    return records
