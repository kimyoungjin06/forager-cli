"""Bound existing source and independent test expectations for inline delivery."""
import ast
import hashlib
import itertools
import json
from pathlib import Path
import textwrap


MAX_PACKET_BYTES = 16000
MAX_INLINE_BYTES = 12000
HELPER_STAGE = 'stream_helper.v1'
HELPER_TARGET = 'scripts/offdesk_stream_frames.py'
HELPER_PACKET_SCHEMA = 'forager_inline_helper_packet.v1'


def selected_file(root, relative, limit=262144):
    if not isinstance(relative, str):
        raise ValueError('Packet inputs must be selected relative file names')
    path = Path(relative)
    if (path.is_absolute() or '..' in path.parts
            or not path.parts or any((root / Path(*path.parts[:i])).is_symlink()
                                     for i in range(1, len(path.parts) + 1))):
        raise ValueError('Packet inputs must be selected regular relative files')
    selected = root / path
    if not selected.resolve().is_relative_to(root.resolve()):
        raise ValueError('Packet input escapes selected root')
    if not selected.is_file() or selected.stat().st_nlink != 1 or selected.stat().st_size > limit:
        raise ValueError('Packet input exceeds regular-file or size bound')
    raw = selected.read_bytes()
    if len(raw) > limit:
        raise ValueError('Packet input exceeds byte bound')
    return raw, hashlib.sha256(raw).hexdigest()


def packet_bytes(packet):
    return (json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def helper_target_absent(root):
    parent = root / 'scripts'
    target = root / HELPER_TARGET
    if (parent.is_symlink() or not parent.is_dir() or target.exists() or target.is_symlink()
            or not parent.resolve().is_relative_to(root.resolve())):
        raise ValueError('Helper stage requires an absent target under the regular scripts directory')


def compact_bytes(raw):
    # This encodes literal fixture bytes, not a stream parser or candidate patch.
    parts, literal = [], bytearray()
    for value, group in itertools.groupby(raw):
        count = sum(1 for _ in group)
        if count >= 16:
            if literal:
                parts.append(literal.hex())
                literal.clear()
            parts.append([count, bytes([value]).hex()])
        else:
            literal.extend(bytes([value]) * count)
    if literal:
        parts.append(literal.hex())
    return parts


def helper_cases(vectors):
    if (vectors.get('schema') != 'forager_stream_helper_vectors.v1'
            or vectors.get('implementation_supplied') is not False
            or type(vectors.get('expected_tests')) is not int or vectors['expected_tests'] != 97
            or not isinstance(vectors.get('cases'), list) or len(vectors['cases']) != 32):
        raise ValueError('All 32 helper vectors and 97 checks are required')
    cases, identifiers = [], set()

    def raw_hex(value):
        if not isinstance(value, str):
            raise ValueError('Literal hex fixture bytes required')
        raw = bytes.fromhex(value)
        if raw.hex() != value:
            raise ValueError('Normalized literal hex fixture bytes required')
        return compact_bytes(raw)

    for case in vectors['cases']:
        if not isinstance(case, dict):
            raise ValueError('Structured literal helper case required')
        name = case.get('id')
        if (not isinstance(name, str) or not name or len(name) > 100 or name in identifiers
                or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name)
                or case.get('mode') not in ('sse', 'ndjson')
                or type(case.get('max_bytes')) is not int or not 1 <= case['max_bytes'] <= 65536
                or type(case.get('expected_drops')) is not int or case['expected_drops'] < 0):
            raise ValueError('Unique bounded helper case identity and limits required')
        identifiers.add(name)
        outputs = []
        for field in ('expected_frames_hex', 'expected_before_finish_hex'):
            values = case.get(field)
            if not isinstance(values, list) or len(values) > 64:
                raise ValueError('Bounded exact helper frame lists required')
            outputs.append([raw_hex(value) for value in values])
        if case['expected_frames_hex'][:len(case['expected_before_finish_hex'])] != case['expected_before_finish_hex']:
            raise ValueError('Before-EOF frames must be the prefix of all frames')
        cases.append([name, case['mode'], case['max_bytes'], raw_hex(case['payload_hex']),
                      *outputs, case['expected_drops']])
    return cases


def build_helper_packet(root, contract_path, checker_path, vectors_path):
    helper_target_absent(root)
    contract_raw, contract_sha = selected_file(root, contract_path)
    _, checker_sha = selected_file(root, checker_path)
    vectors_raw, vectors_sha = selected_file(root, vectors_path)
    if (Path(checker_path).parent != Path(vectors_path).parent
            or Path(checker_path).name != 'check_stream_helper.py'
            or Path(vectors_path).name != 'HELPER_VECTORS.json'):
        raise ValueError('Helper checker and its default vector file must be selected together')
    contract = json.loads(contract_raw)
    if (contract.get('schema') != 'forager_stream_helper_task_proposal.v1'
            or contract.get('launch_authorized') is not False
            or contract.get('qualification_complete') is not False
            or contract.get('write_paths') != [HELPER_TARGET]
            or not isinstance(contract.get('api'), dict)):
        raise ValueError('Explicit pure-helper proposal and exact new-file scope required')
    requirements = []
    for field in ('scope', 'local_inspection_policy'):
        rows = contract.get(field)
        if (not isinstance(rows, list) or not 1 <= len(rows) <= 16
                or any(not isinstance(row, str) or not 1 <= len(row) <= 1500 for row in rows)):
            raise ValueError('Bounded helper requirements required')
        requirements.extend(rows)
    checks = contract.get('checks', {})
    if (checks.get('case_count') != 32 or checks.get('expected_tests') != 97
            or checks.get('post_edit_argv') != helper_check_argv(checker_path)
            or checks.get('host_report_path') != '.night/helper-host-check.json'):
        raise ValueError('Exact model and separate host helper checks required')
    packet = {'schema': HELPER_PACKET_SCHEMA, 'implementation_stage': HELPER_STAGE,
              'bindings': {contract_path: contract_sha, checker_path: checker_sha, vectors_path: vectors_sha},
              'contract_path': contract_path, 'checker_path': checker_path, 'vectors_path': vectors_path,
              'absent_paths': [HELPER_TARGET], 'write_paths': [HELPER_TARGET],
              'api': contract['api'], 'requirements': requirements,
              'cases': helper_cases(json.loads(vectors_raw)),
              'implementation_patch_supplied': False, 'execution_authority': False}
    if len(packet_bytes(packet)) > MAX_PACKET_BYTES:
        raise ValueError('Helper delivery packet exceeds selected byte bound')
    render_helper_packet(packet)
    return packet


def helper_check_argv(checker_path, host=False):
    return ['/usr/bin/python3', '-I', '-S', checker_path, '--source-root', '.', '--json-report',
            '.night/helper-host-check.json' if host else '.night/helper-model-check.json']


def render_helper_packet(packet):
    chunks = ['VERIFIED TASK INPUT (pure helper requirements and literal fixtures; no decoder patch).',
              'Already delivered inline: do not cat the index/contract/checker to rediscover these inputs.',
              'Create only ' + HELPER_TARGET + '. It must be absent before the implementation context.',
              'Bindings: ' + json.dumps(packet['bindings'], sort_keys=True),
              'API: ' + json.dumps(packet['api'], sort_keys=True), 'Requirements:']
    chunks += [str(i) + '. ' + text for i, text in enumerate(packet['requirements'], 1)]
    chunks += ['All 32 literal cases follow. No preview or truncation is used.',
               'Each row: [id, mode, max_bytes, input, all_frames, frames_before_finish, drops].',
               'Bytes are lists of lowercase hex literal segments or [repeat_count, single_byte_hex].',
               'Concatenate segments; [] means empty bytes. Frame lists contain such byte lists.',
               'Every case runs with whole, one-byte and three-byte chunks, including empty feed calls.',
               'A 97th check interleaves independent SSE and NDJSON instances.',
               'finish releases pending storage and is idempotent; feed after finish raises ValueError.']
    chunks += [json.dumps(case, separators=(',', ':')) for case in packet['cases']]
    chunks += ['Run the one selected model check once, then save actual final progress.',
               'The host separately repeats the checker without overwriting your check report.',
               'A helper pass stays pending review. Later relay integration still needs all twelve HTTP cases.',
               'No telemetry edits, transport changes, task acceptance or launch authority is granted here.']
    text = '\n'.join(chunks) + '\n'
    if len(text.encode()) > MAX_INLINE_BYTES:
        raise ValueError('Inline helper input exceeds selected prompt byte bound')
    return text


def build_packet(root, source_path, contract_path, checker_path, ranges):
    source, source_sha = selected_file(root, source_path)
    contract_raw, contract_sha = selected_file(root, contract_path)
    checker_raw, checker_sha = selected_file(root, checker_path)
    contract = json.loads(contract_raw)
    if (contract.get('operator_implementation_patch_supplied') is not False
            or source_path not in contract.get('write_paths', [])
            or not isinstance(contract.get('requirements'), list)
            or not 1 <= len(contract['requirements']) <= 16
            or any(not isinstance(s, str) or not 1 <= len(s) <= 1500 for s in contract['requirements'])):
        raise ValueError('Selected requirements and independent implementation scope required')
    lines = source.decode().splitlines(keepends=True)
    excerpts = []
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= 4:
        raise ValueError('One through four selected source ranges required')
    for start, end in ranges:
        if (type(start) is not int or type(end) is not int
                or not 1 <= start <= end <= len(lines) or end - start >= 60):
            raise ValueError('Selected source ranges must contain at most 60 existing lines')
        excerpts.append({'start': start, 'end': end, 'text': ''.join(lines[start - 1:end])})
    checker_text = checker_raw.decode()
    methods = [n for n in ast.walk(ast.parse(checker_text))
               if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')]
    cases = []
    for method in sorted(methods, key=lambda n: n.name):
        expected = [n for n in ast.walk(method) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == 'expected']
        if len(expected) != 1 or len(expected[0].args) < 4:
            raise ValueError('One explicit expected frame/terminal assertion per case required')
        args = expected[0].args
        name, frames, terminal = (ast.literal_eval(args[i]) for i in (0, 2, 3))
        if not isinstance(name, str) or type(frames) is not int or frames < 0 or type(terminal) is not bool:
            raise ValueError('Literal independent check expectations required')
        cases.append({'id': method.name, 'name': name, 'frames': frames, 'terminal': terminal,
                      'fixture': ast.get_source_segment(checker_text, method)})
    if len(cases) != 12:
        raise ValueError('All twelve selected independent stream cases required')
    constants = []
    for node in ast.parse(checker_text).body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id in ('CONTENT', 'TERMINAL') for n in node.targets):
            constants.append(ast.get_source_segment(checker_text, node))
    packet = {'schema': 'forager_inline_implementation_packet.v1',
              'bindings': {source_path: source_sha, contract_path: contract_sha, checker_path: checker_sha},
              'source_path': source_path, 'contract_path': contract_path, 'checker_path': checker_path,
              'write_paths': contract['write_paths'], 'requirements': contract['requirements'],
              'excerpts': excerpts, 'case_constants': constants, 'cases': cases,
              'implementation_patch_supplied': False, 'execution_authority': False}
    if len(packet_bytes(packet)) > MAX_PACKET_BYTES:
        raise ValueError('Delivery packet exceeds selected byte bound; select focused excerpts')
    render_packet(packet)
    return packet


def render_packet(packet):
    # This is prompt input, not a tool result subject to tool_output_token_limit.
    chunks = ['VERIFIED TASK INPUT (requirements and original source, not an implementation patch).',
              'Already delivered inline: do not cat the index/contract/checker to rediscover these inputs.',
              'Additional focused reads remain within the existing inspection budget.',
              'Bindings: ' + json.dumps(packet['bindings'], sort_keys=True),
              'Selected source writes: ' + ', '.join(packet['write_paths']), 'Requirements:']
    chunks += [str(i) + '. ' + text for i, text in enumerate(packet['requirements'], 1)]
    for excerpt in packet['excerpts']:
        chunks += [f"Original {packet['source_path']}:{excerpt['start']}-{excerpt['end']} (common indentation removed):",
                   textwrap.dedent(excerpt['text']).rstrip()]
    chunks += ['Independent checker fixture constants:'] + packet['case_constants']
    for case in packet['cases']:
        chunks += [f"Case {case['id']}:",
                   textwrap.dedent(case['fixture'])]
    chunks += ['All cases require exact original wire bytes/counts, HTTP completion and no body/auth canaries in metadata.',
               'Discard incomplete SSE data at EOF; NDJSON final-line observation stays supported.',
               'Keep forwarding, timeout, cancellation and authority behavior unchanged.',
               'Implement independently, then run the two selected checks once each. Packet alone grants no launch or acceptance.']
    text = '\n'.join(chunks) + '\n'
    if len(text.encode()) > MAX_INLINE_BYTES:
        raise ValueError('Inline task input exceeds selected prompt byte bound')
    return text


def load_packet(root, selection):
    raw, digest = selected_file(root, selection['path'], MAX_PACKET_BYTES)
    if digest != selection['sha256']:
        raise ValueError('Selected delivery packet hash changed')
    packet = json.loads(raw)
    if packet.get('schema') == HELPER_PACKET_SCHEMA:
        rebuilt = build_helper_packet(root, packet['contract_path'], packet['checker_path'], packet['vectors_path'])
        if packet != rebuilt:
            raise ValueError('Helper delivery packet differs from bound requirements/checker/vectors')
        return packet, render_helper_packet(packet)
    if packet.get('schema') != 'forager_inline_implementation_packet.v1':
        raise ValueError('Selected delivery packet schema required')
    # Rebuild from current immutable inputs rather than trusting claimed hashes or expectations.
    rebuilt = build_packet(root, packet['source_path'], packet['contract_path'], packet['checker_path'],
                           [(x['start'], x['end']) for x in packet['excerpts']])
    if packet != rebuilt:
        raise ValueError('Delivery packet differs from bound source/requirements/checker')
    return packet, render_packet(packet)
