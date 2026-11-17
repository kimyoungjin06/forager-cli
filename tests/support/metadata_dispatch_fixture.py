"""Build only private synthetic metadata packets; never dispatch a task."""
import hashlib
import json
from pathlib import Path
import sys
import time

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import offdesk_resume_checkpoint as gate
import offdesk_metadata_packet as packet


def build(root, task='task', request='request', project='project', policy_sha256='f' * 64):
    root = Path(root)
    (root / '.runtime').mkdir(exist_ok=True)
    names = ['offdesk_metadata_dispatch.py', 'offdesk_metadata_consumer.py', 'offdesk_metadata_packet.py',
             'offdesk_resume_checkpoint.py', 'native_worker_bootstrap.py']
    for name in names:
        (root / '.runtime' / name).write_bytes((SCRIPTS / name).read_bytes())
    for name, original in [('consumer.py', names[1]), ('adapter.py', names[2]), ('validator.py', names[3])]:
        (root / name).write_bytes((SCRIPTS / original).read_bytes())
    rows = [{'work_id': 'W' + str(i + 1), 'publication_year': 1970 + i, 'local_idx': 321 + i,
             'p0_action': 3, 'vector_present': False} for i in range(4)]
    raw = b''.join(gate.encode(row) for row in rows)
    policy = gate.encode({'schema': 'incremental_embedding_usage_exclusion_v1', 'query_policy': 'deny',
                         'member_policy': 'deny', 'denied_work_ids': [r['work_id'] for r in rows],
                         'diagnostic_compute_allowed': True, 'live_applied': False})
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    context = {'purpose': packet.PURPOSE, 'source_vintage': 'synthetic-fixture-only',
               'source_artifact_sha256': 'a' * 64, 'source_contract_sha256': 'b' * 64,
               'selected_rows_sha256': sha(raw), 'policy_snapshot_sha256': sha(policy),
               'selection_start': 0, 'selection_rows': len(rows)}
    payload, provenance, manifest = packet.build_packet(raw, policy, context)
    (root / 'input.jsonl').write_bytes(payload)
    (root / 'row_provenance.jsonl').write_bytes(provenance)
    (root / 'manifest.json').write_bytes(gate.encode(manifest))
    (root / 'policy.json').write_bytes(policy)
    config = {'schema': 'forager.metadata_consumer.v2', 'batch_rows': 2,
              'limits': {'work_seconds': 10, 'shutdown_grace_seconds': 2, 'total_seconds': 120},
              'packet': {k: v for k, v in context.items() if k not in ('selection_start', 'selection_rows')}}
    config['packet']['manifest_sha256'] = sha(gate.encode(manifest))
    (root / 'configuration.json').write_bytes(gate.encode(config))
    from offdesk_metadata_dispatch import INPUTS
    info = root.stat()
    now = time.time()
    cfg = {'schema': 'offdesk_metadata_selection.v1', 'executor_kind': 'deterministic_local_no_model',
           'project_key': project, 'task_id': task, 'request_id': request,
           'root_device': info.st_dev, 'root_inode': info.st_ino,
           'runtime_files': {'.runtime/' + name: sha((root / '.runtime' / name).read_bytes()) for name in names},
           'input_files': {name: sha((root / name).read_bytes()) for name in sorted(INPUTS)},
           'starts_at': now - 1, 'stops_at': now + 119, 'total_seconds': 120,
           'child_seconds': 20, 'closeout_seconds': 20, 'rows': 4, 'batch_rows': 2,
           'read_policy_sha256': policy_sha256}
    (root / 'METADATA_SELECTION.json').write_bytes(gate.encode(cfg))
    return cfg, sha(gate.encode(cfg))


if __name__ == '__main__':
    _, pin = build(Path(sys.argv[1]), *sys.argv[2:])
    print(json.dumps({'selection_sha256': pin}))
