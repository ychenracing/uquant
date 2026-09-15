"""Restore exact originals from committed parts; no network or implicit overwrite."""
import argparse
import base64
import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def restore(root, out, selected=None):
    index = json.loads((root / 'index.json').read_text())
    for entry in index['files']:
        if selected and entry['key'] != selected:
            continue
        if not entry.get('remote_readback_verified'):
            raise ValueError('Not remotely verified: ' + entry['key'])
        raw = (root / entry['manifest']).read_bytes()
        assert digest(raw) == entry['manifest_sha256']
        item = json.loads(raw)
        target = out / item['key']
        assert target.resolve().is_relative_to(out.resolve())
        if item['storage'] == 'existing_blob_reference':
            data = (root.parent / item['key']).read_bytes()
        else:
            blocks = []
            offset = 0
            for i, part in enumerate(item['parts']):
                assert part['index'] == i and part['offset'] == offset
                relative = part['path'].split('raw-preservation/', 1)[1]
                encoded = (root / relative).read_bytes()
                assert len(encoded) == part['encoded_bytes']
                block = base64.b64decode(encoded.strip(), validate=True)
                assert len(block) == part['bytes'] and digest(block) == part['sha256']
                blocks.append(block)
                offset += len(block)
            data = b''.join(blocks)
        assert len(data) == item['bytes'] and digest(data) == item['sha256']
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
        print(item['key'], len(data), digest(data))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).parent)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case')
    args = parser.parse_args()
    restore(args.root, args.output, args.case)
