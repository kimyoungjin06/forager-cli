"""Independent implementation qualification through an approved ordinary launcher."""
import hashlib
import json
from pathlib import Path
import re,shlex,sys,time
sys.path.insert(0, str(Path(__file__).resolve().parent))
import offdesk_native_worker as worker_module
import urllib.request,urllib.parse
import offdesk_qualification_packet as packet_module
from offdesk_qualification_packet import load_packet


LITERAL_SHELL_TOKEN = re.compile(r'''(?P<word>(?:'[^']*'|"(?:[^"\\$`]|\\[^\r\n])*"|\\[^\r\n]|[^\s'"\\;&|<>()$`*?\[\]{}~#])+)|(?P<and>&&)|(?P<space>[ \t]+)''')
SELECTED_SHELLS = ('bash', 'sh', '/bin/bash', '/usr/bin/bash', '/bin/sh', '/usr/bin/sh')
SELECTED_PYTHONS = ('python3', 'python3.11', '/usr/bin/python3', '/usr/bin/python3.11')
MAX_INSPECTION_LINES = worker_module.MAX_INSPECTION_LINES
MAX_INSPECTION_BYTES = worker_module.MAX_INSPECTION_BYTES


def checked_command_argv(raw, root=None):
    """Recognize literal argv and one exact-root cd, without shell evaluation."""
    def tokens(text):
        result, position = [], 0
        while position < len(text):
            match = LITERAL_SHELL_TOKEN.match(text, position)
            if match is None:
                raise ValueError('Only literal arguments and an exact selected-root cd are recognized')
            position = match.end()
            if match.lastgroup == 'word':
                result.append((shlex.split(match.group())[0], False))
            elif match.lastgroup == 'and':
                result.append(('&&', True))
        return result

    parts = tokens(raw)
    if parts and Path(parts[0][0]).name in ('bash', 'sh'):
        if (parts[0][0] not in SELECTED_SHELLS or len(parts) != 3
                or any(control for _, control in parts) or parts[1][0] not in ('-lc', '-c')):
            raise ValueError('Unexpected shell wrapper')
        parts = tokens(parts[2][0])
    if parts and parts[0] == ('cd', False):
        if (root is None or not Path(root).is_absolute() or len(parts) < 4
                or parts[1] != (str(root), False) or parts[2] != ('&&', True)):
            raise ValueError('cd must select the exact execution root before one command')
        parts = parts[3:]
    if not parts or any(control for _, control in parts):
        raise ValueError('One simple command required')
    argv = [word for word, _ in parts]
    if Path(argv[0]).name in ('bash', 'sh', 'cd'):
        raise ValueError('Nested shell or directory change is not a simple command')
    if Path(argv[0]).name in ('python3', 'python3.11'):
        if argv[0] not in SELECTED_PYTHONS:
            raise ValueError('Unselected Python executable')
        argv[0] = '/usr/bin/python3'
    return argv


def inspection_commands(raw, root=None):
    """Recognize a conservative subset of display commands, never shell eval."""
    if '$' in raw or '`' in raw or '\n' in raw:
        return False
    try:
        outer = shlex.split(raw)
    except ValueError:
        return False
    if outer and Path(outer[0]).name in ('bash', 'sh'):
        if len(outer) != 3 or outer[1] not in ('-lc', '-c'):
            return False
        raw = outer[2]
    lexer = shlex.shlex(raw, posix=True, punctuation_chars=';&|<>()')
    lexer.whitespace_split = True
    lexer.commenters = ''
    segments, current = [], []
    try:
        tokens = list(lexer)
    except ValueError:
        return False
    if root is not None and tokens[:3] == ['cd', str(root), '&&']:
        tokens = tokens[3:]
    position = 0
    while position < len(tokens):
        # A display command's stderr sink does not invoke or modify a checker.
        if tokens[position:position + 3] == ['2', '>', '/dev/null']:
            position += 3
            continue
        token = tokens[position]
        position += 1
        if token in (';', '&&', '|'):
            if not current:
                return False
            segments.append(current)
            current = []
        elif token and all(c in ';&|<>()' for c in token):
            return False
        else:
            current.append(token)
    if not current:
        return False
    segments.append(current)
    flags = {
        'cat': {'-n', '-b', '-A', '-v', '--'},
        'head': {'-n', '-c', '--'}, 'tail': {'-n', '-c', '--'},
        'wc': {'-l', '-w', '-c', '-m', '--'},
        'grep': {'-n', '-i', '-F', '-E', '-l', '--'},
        'rg': {'-n', '-i', '-F', '-l', '--line-number', '--fixed-strings', '--'},
        'echo': {'-n', '-e', '-E'},
    }
    for argv in segments:
        if Path(argv[0]).name in ('python3', 'python3.11'):
            argv[0] = '/usr/bin/python3'
        name = Path(argv[0]).name
        if name == 'sed':
            if (len(argv) < 4 or argv[1] != '-n'
                    or not re.fullmatch(r'\d+(?:,\d+)?p', argv[2])
                    or any(a.startswith('-') for a in argv[3:])):
                return False
        elif name in flags:
            if (name in ('grep', 'rg') and len(argv) < 2) or (name != 'echo' and any(a.startswith('-') and a not in flags[name]
                    and not (name in ('head', 'tail') and re.fullmatch(r'-\d+', a))
                    for a in argv[1:])):
                return False
        elif (argv[:4] != ['/usr/bin/python3', '-I', '-S', '.runtime/offdesk_native_worker.py']
              or argv[4:5] not in (['excerpt'], ['inspect'])):
            return False
    return segments


def inspection_command(raw, root=None):
    return bool(inspection_commands(raw, root))


def inspection_targets(segments):
    """Extract literal file operands/selectors from the recognized read subset."""
    targets = []
    for argv in segments:
        name = Path(argv[0]).name
        if name == 'echo':
            continue
        if name == 'sed':
            selector = ','.join(str(int(n)) for n in argv[2][:-1].split(',')) + 'p'
            targets.extend((path, [name, selector]) for path in argv[3:])
            continue
        if (argv[:4] == ['/usr/bin/python3', '-I', '-S', '.runtime/offdesk_native_worker.py']
                and argv[4:5] in (['excerpt'], ['inspect'])):
            options = {}
            tail = argv[5:]
            if len(tail) % 2:
                return None
            for option, value in zip(tail[::2], tail[1::2]):
                if option not in ('--root', '--path', '--start', '--lines') or option in options:
                    return None
                options[option] = value
            if options.get('--root') != '.' or '--path' not in options:
                return None
            try:
                start, lines = int(options.get('--start', '1')), int(options.get('--lines', '60'))
            except ValueError:
                return None
            if start < 1 or lines < 1:
                return None
            targets.append((options['--path'], [argv[4], start, lines]))
            continue
        files, selector, pattern = [], [name], name not in ('grep', 'rg')
        options_done, position = False, 1
        while position < len(argv):
            word = argv[position]
            position += 1
            if not options_done and word == '--':
                options_done = True
            elif not options_done and name in ('head', 'tail') and word in ('-n', '-c'):
                if position == len(argv) or not re.fullmatch(r'[+-]?\d+', argv[position]):
                    return None
                selector.extend([word, str(int(argv[position]))])
                position += 1
            elif not options_done and word.startswith('-'):
                selector.append(word)
            elif not pattern:
                selector.append(word)
                pattern = True
            else:
                files.append(word)
        if not pattern:
            return None
        targets.extend((path, selector) for path in files)
    return targets


def selected_read_path(path, root=None):
    candidate = Path(path)
    if '..' in candidate.parts:
        raise ValueError('Parent traversal is not a selected read')
    if candidate.is_absolute():
        if root is None or not Path(root).is_absolute():
            raise ValueError('Absolute read requires an exact selected root')
        candidate = candidate.relative_to(root)
    if not candidate.parts:
        raise ValueError('File operand required')
    return str(candidate)


def selected_read_paths(paths):
    if (not isinstance(paths, list) or not 1 <= len(paths) <= 16
            or any(not isinstance(p, str) or not 1 <= len(p) <= 500
                   or any(c in p for c in '\0\r\n*?[]') or p.endswith('/') or Path(p).is_absolute()
                   or '..' in Path(p).parts or not Path(p).parts for p in paths)):
        raise ValueError('Explicit selected relative read paths required')
    return {selected_read_path(p) for p in paths}


def inspection_output(item, root=None):
    """Measure a recognized read's visible completion, never hidden output."""
    text = item.get('aggregated_output')
    observation = {'native_item_id': item['id'], 'lines': None, 'utf8_bytes': None,
                   'source_lines': None, 'stop_reason': None}
    if not isinstance(text, str):
        observation['stop_reason'] = 'inspection_output_unverifiable'
        return observation
    try:
        observation.update(lines=len(text.splitlines()), utf8_bytes=len(text.encode()))
    except UnicodeError:
        observation['stop_reason'] = 'inspection_output_unverifiable'
        return observation
    segments = inspection_commands(item['command'], root) or []
    if any(argv[:4] == ['/usr/bin/python3', '-I', '-S', '.runtime/offdesk_native_worker.py']
           and argv[4:5] in (['excerpt'], ['inspect'])
           for argv in segments):
        # The helper's JSON string escapes newlines; count the decoded excerpt too.
        try:
            receipt = json.loads(text)
            source_lines = len(receipt['text'].splitlines())
            if type(receipt['lines_emitted']) is not int or receipt['lines_emitted'] != source_lines:
                raise ValueError('Excerpt line count disagrees with text')
            observation['source_lines'] = source_lines
            if any(argv[4:5] == ['inspect'] for argv in segments):
                argv = checked_command_argv(item['command'], root)
                if (len(segments) != 1 or argv[:5] != ['/usr/bin/python3', '-I', '-S',
                        '.runtime/offdesk_native_worker.py', 'inspect']
                        or receipt.get('schema') != worker_module.INSPECTION_SCHEMA):
                    raise ValueError('Bounded inspection requires one exact helper command')
                if receipt.get('status') == 'refused':
                    reason = receipt.get('reason')
                    if item.get('exit_code') != 0 or receipt != worker_module.inspection_refusal(reason):
                        raise ValueError('Invalid bounded inspection refusal')
                    observation['policy_refusal'] = reason
                elif receipt.get('status') != 'ok':
                    raise ValueError('Unknown bounded inspection status')
        except (ValueError, KeyError, TypeError, AttributeError):
            observation['stop_reason'] = 'inspection_output_unverifiable'
    if (observation['lines'] > MAX_INSPECTION_LINES
            or (observation['source_lines'] or 0) > MAX_INSPECTION_LINES
            or observation['utf8_bytes'] > MAX_INSPECTION_BYTES):
        observation['stop_reason'] = 'inspection_output_limit_exceeded'
    return observation


class ImplementationGuard(worker_module.ProgressGuard):
    """Bound observed inspection and preserve progress on a compaction notice."""
    def __init__(self, max_calls, source_observer, progress_observer, record,
                 max_inspections=4, read_paths=None):
        super().__init__(max_calls=max_calls, context_warnings=1)
        if type(max_inspections) is not int or not 1 <= max_inspections <= 4:
            raise ValueError('Implementation inspection budget must be 1 through 4')
        self.source_observer = source_observer
        self.progress_observer = progress_observer
        self.record = record
        self.initial_source = source_observer()
        self.inspection_items = set()
        self.inspection_outputs = {}
        self.read_paths = None if read_paths is None else selected_read_paths(read_paths)
        self.read_signatures = set()
        self.inspection_policy = []
        self.edit_observed = False
        self.max_inspections = max_inspections

    def feed(self, event, source_generation=None):
        valid = worker_module.valid_native_event(event)
        item = event.get('item', {}) if valid else {}
        fresh = (valid and event.get('type') in ('item.started', 'item.completed')
                 and item.get('type') == 'command_execution'
                 and item.get('id') not in self.charged_items)
        compaction = (valid and event.get('type') == 'item.completed'
                      and item.get('type') == 'error'
                      and 'compaction' in item.get('message', '').lower())
        warnings_before = self.warnings
        reason = super().feed(event, source_generation)
        if compaction and self.warnings > warnings_before:
            self.record('COMPACTION_PROGRESS_OBSERVATION.json', {
                'observation': self.progress_observer(), 'observed_at': time.time(),
                'claims_verified': False, 'automatic_resume_authorized': False})
        if fresh:
            self.edit_observed |= self.source_observer() != self.initial_source
            if not self.edit_observed and inspection_command(item['command'], getattr(self, 'check_root', None)):
                self.inspection_items.add(item['id'])
                if self.read_paths is not None:
                    targets = inspection_targets(inspection_commands(item['command'], self.check_root))
                    policy_reason, signatures = None, set()
                    if targets is None:
                        policy_reason = 'inspection_scope_unverifiable'
                    else:
                        for path, selector in targets:
                            try:
                                path = selected_read_path(path, self.check_root)
                            except ValueError:
                                policy_reason = 'inspection_path_not_selected'
                            if path not in self.read_paths:
                                policy_reason = 'inspection_path_not_selected'
                            signature = hashlib.sha256(json.dumps([path, selector]).encode()).hexdigest()
                            if signature in self.read_signatures or signature in signatures:
                                policy_reason = policy_reason or 'duplicate_pre_edit_inspection'
                            signatures.add(signature)
                    self.read_signatures.update(signatures)
                    self.inspection_policy.append({'native_item_id': item['id'],
                        'read_signature_sha256': sorted(signatures), 'stop_reason': policy_reason})
                    reason = reason or policy_reason
                if len(self.inspection_items) > self.max_inspections:
                    reason = reason or 'inspection_budget_reached_without_edit'
        if (valid and event.get('type') == 'item.completed'
                and item.get('type') == 'command_execution'
                and item['id'] not in self.inspection_outputs
                and inspection_command(item['command'], self.check_root)):
            observation = inspection_output(item, self.check_root)
            self.inspection_outputs[item['id']] = observation
            if observation.get('policy_refusal') and not observation['stop_reason']:
                self.nonprogress_items.add(item['id'])
            reason = reason or observation['stop_reason']
        self.record('IMPLEMENTATION_GUARD_STATE.json', {
            'inspection_starts_without_edit': len(self.inspection_items),
            'max_inspections': self.max_inspections, 'edit_observed': self.edit_observed,
            'charged_command_starts': self.calls, 'stop_reason': reason,
            'policy_refusals': len(self.nonprogress_items),
            'max_observed_inspection_lines': MAX_INSPECTION_LINES,
            'max_observed_inspection_utf8_bytes': MAX_INSPECTION_BYTES,
            'inspection_outputs': list(self.inspection_outputs.values()),
            'selected_pre_edit_read_paths': None if self.read_paths is None else sorted(self.read_paths),
            'pre_edit_read_policy': list(self.inspection_policy),
            'output_scope': 'recognized reads; visible completion output only, including after edits',
            'post_execution_observation': True, 'admission_fencing': False})
        return reason


def implementation_instructions(context, progress, context_sha, read_paths=None):
    path = read_paths[0] if read_paths else 'SELECTED_SOURCE_PATH'
    inspect = shlex.join(['python3', '-I', '-S', '.runtime/offdesk_native_worker.py',
                         'inspect', '--root', '.', '--path', path, '--start', '1', '--lines', '40'])
    return (
        'Edit first after recovery using the inline source and requirements. '
        'If one essential detail is missing, prefer one focused read, then edit. '
        'Do not restart discovery or dump files. At most four bounded read commands '
        'before the first edit; every read output, including after edits, must fit '
        f'{MAX_INSPECTION_LINES} lines and {MAX_INSPECTION_BYTES} UTF-8 bytes. '
        f'For a necessary selected-source read use exactly: {inspect}. '
        'This helper refuses oversized ranges before reading and bounds its serialized output. '
        'A status=refused response contains no source text; follow next_action without a file dump. '
        'Refused reads still consume command and inspection budgets, but are not tool progress. '
        'Do not reread the inline checker; after editing, save progress and run the selected check '
        'before further inspection. '
        'The guard checks visible completed output, not hidden or truncated bytes, '
        'and is not pre-execution admission. Inspections and progress saves consume '
        'the shared command budget. Save actual progress after edits and before '
        'checks, then once finally; at most two intermediate saves and one final '
        'save. Run each selected check once; stop on failure without rerun. '
        'A compaction notice stops and preserves progress; it does not grant another context or replay.\n'
        f'Latest progress path: {progress}. Task context: {context}. '
        f'Expected context sha256: {context_sha}.\n')


def plain_progress_instructions(context, context_sha):
    command = shlex.join(['python3', '-I', '-S', '.runtime/offdesk_native_worker.py',
                         'progress', '--root', '.', '--context', context,
                         '--expected-context-sha256', context_sha])
    return (
        f"Save progress using {command} --assessment 'Actual assessment' "
        "--next-action 'Concrete next action'. Use repeated --checked-command "
        "'One actual action' and --changed-file 'One actual changed source path' "
        "options; omit them when empty. Assessment and next action must each "
        "fit 1-1500 characters, arrays at most 16 strings of at most 500 "
        "characters, and the saved packet at most 8192 UTF-8 bytes. "
        "Only plain string options are selected; no stdin or legacy JSON "
        "inputs, shell substitutions or invented successful checks. "
        "Each progress save must be a separate single command. Never join it "
        "to an edit heredoc or another action; finish the edit, then issue a new "
        "command-execution call using the save form above. The writer "
        "preserves binding; model claims remain unverified.\n")


def verify_selected_model(run,deadline,context=1):
    s=run.selection;p=urllib.parse.urlsplit(s['base_url'])
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/','/v1'):
        raise ValueError('Invalid selected local metadata endpoint')
    with urllib.request.urlopen(urllib.parse.urlunsplit((p.scheme,p.netloc,'/api/tags','','')),timeout=max(.01,min(10,deadline-time.time()))) as response:raw=response.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:raise ValueError('Model metadata exceeds bound')
    matches=[row for row in json.loads(raw)['models'] if row.get('name')==s['model']]
    if len(matches)!=1 or matches[0].get('digest')!=s['model_digest']:raise ValueError('Selected model identity changed')
    run.record(f'MODEL_IDENTITY_CONTEXT_{context}.json',{'model':s['model'],'digest':s['model_digest'],'read_only':True,'observed_at':time.time(),'metadata_compatibility_qualified':False})


def completed_commands(output):
    return [event['item'] for line in (output/'stdout.log').read_text().splitlines()
        if (event:=json.loads(line)).get('type')=='item.completed' and event.get('item',{}).get('type')=='command_execution']


def helper_receipt(item,action,root=None):
    if item.get('exit_code')!=0:raise ValueError('Native helper command failed')
    argv=checked_command_argv(item['command'],root)
    if argv[:5]!=['/usr/bin/python3','-I','-S','.runtime/offdesk_native_worker.py',action]:raise ValueError('Actual checked helper invocation required')
    receipt=json.loads(item.get('aggregated_output',''))
    if not isinstance(receipt,dict):raise ValueError('One helper receipt object required')
    return receipt


def thread_id(output):
    ids=[event.get('thread_id') for line in (output/'stdout.log').read_text().splitlines() if (event:=json.loads(line)).get('type')=='thread.started']
    if len(ids)!=1 or not isinstance(ids[0],str) or not ids[0]:raise ValueError('One native thread identity required')
    return ids[0]


def selected_instructions(phase,worker):
    worker.validate_verification_commands(phase['checks'])
    action=phase['implementation_next_action']
    if not isinstance(action,str) or not 1<=len(action)<=1500:raise ValueError('Bounded selected implementation next action required')
    return action,('Selected post-edit check argv: '+json.dumps(phase['checks'])+'\n'
        +'Checkpoint next_action must be exactly: '+json.dumps(action)+'\n'
        +'Use repeated string progress options. Do not invent already-executed checks or edits.\n')


def implementation_stage(phase):
    stage = phase.get('implementation_stage', 'stream_relay.v1')
    if stage not in ('stream_relay.v1', packet_module.HELPER_STAGE):
        raise ValueError('Unsupported independent implementation stage')
    if stage == packet_module.HELPER_STAGE and phase['write_paths'] != [packet_module.HELPER_TARGET]:
        raise ValueError('Helper stage permits only the exact new helper file')
    return stage


def selected_stage_packet(phase, packet):
    stage = implementation_stage(phase)
    if phase['write_paths'] != packet['write_paths']:
        raise ValueError('Delivery scope differs from selected implementation scope')
    helper = packet.get('schema') == packet_module.HELPER_PACKET_SCHEMA
    if (stage == packet_module.HELPER_STAGE) != helper:
        raise ValueError('Explicit implementation stage and delivery packet must agree')
    if helper:
        if phase['checks'] != [packet_module.helper_check_argv(packet['checker_path'])]:
            raise ValueError('One exact model helper check and distinct host report are required')
    elif not any(packet['checker_path'] in argv for argv in phase['checks']):
        raise ValueError('Packet checker must be one of the selected post-edit checks')
    return stage


def helper_check_receipt(root, relative, packet, worker):
    report, digest = worker.read_task_json(root, root / relative, 262144)
    target_sha = packet_module.selected_file(root, packet_module.HELPER_TARGET)[1]
    expected_ids = [case[0] + '/' + style for case in packet['cases']
                    for style in ('whole', 'bytewise', 'three-byte')] + ['independent-instances']
    rows = report.get('cases')
    if (report.get('schema') != 'forager_stream_helper_checks.v1' or report.get('status') != 'passed'
            or report.get('source_root') != str(root.resolve()) or report.get('target_sha256') != target_sha
            or any(type(report.get(key)) is not int or report[key] != count
                   for key, count in [('tests_run', 97), ('passed', 97), ('failed', 0), ('errors', 0)])
            or report.get('qualification_complete') is not False or not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows)
            or [row.get('id') for row in rows] != expected_ids
            or any(row.get('passed') is not True for row in rows)):
        raise ValueError('Complete bound helper check report required, not exit status alone')
    return {'path': relative, 'sha256': digest, 'target_sha256': target_sha, 'tests_run': 97,
            'qualification_complete': False, 'reported_retention_requires_code_review': True}


def failed_helper_check_evidence(root, packet, item, worker):
    relative = '.night/helper-model-check.json'
    evidence = {'path': relative, 'sha256': None, 'reported_counts': None,
                'status': 'unverified', 'independent_host_verified': False}
    try:
        report, digest = worker.read_task_json(root, root / relative, 262144)
        evidence['sha256'] = digest
        target_sha = packet_module.selected_file(root, packet_module.HELPER_TARGET)[1]
        expected = [case[0] + '/' + style for case in packet['cases']
                    for style in ('whole', 'bytewise', 'three-byte')] + ['independent-instances']
        rows = report.get('cases')
        if (report.get('schema') != 'forager_stream_helper_checks.v1'
                or report.get('status') != 'failed'
                or report.get('source_root') != str(root.resolve())
                or report.get('target_sha256') != target_sha
                or report.get('qualification_complete') is not False
                or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
                or [row.get('id') for row in rows] != expected
                or any(type(row.get('passed')) is not bool for row in rows)
                or any(type(report.get(key)) is not int for key in ('tests_run', 'passed', 'failed', 'errors'))
                or report['tests_run'] != 97 or report['errors'] != 0
                or not 0 < report['failed'] <= 97
                or report['passed'] != sum(row['passed'] for row in rows)
                or report['failed'] != sum(not row['passed'] for row in rows)
                or report['passed'] + report['failed'] != 97):
            raise ValueError('Failed helper report is not bound to the selected source and full case inventory')
        counts = {key: report[key] for key in ('status', 'tests_run', 'passed', 'failed', 'errors')}
        summary = json.loads(item.get('aggregated_output', ''))
        if (summary != counts or any(type(summary.get(key)) is not int
                for key in ('tests_run', 'passed', 'failed', 'errors'))):
            raise ValueError('Native checker summary and retained report disagree')
        evidence.update(status='source_and_summary_bound', reported_counts=counts,
                        target_sha256=target_sha)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        evidence['error_class'] = type(exc).__name__
    return evidence


def native_command_failure(phase, commands, root, packet, worker):
    """Describe observed failed commands without accepting work or rerunning checks."""
    selected = {tuple(argv) for argv in phase['checks']}
    observed = []
    for item in commands:
        try:
            argv = checked_command_argv(item['command'], root)
        except ValueError:
            argv = []
        observed.append((item, argv, tuple(argv) in selected))
    failed = [(item, argv, chosen) for item, argv, chosen in observed
              if type(item.get('exit_code')) is not int or item['exit_code'] != 0]
    if not failed:
        return None
    execution_failure = any(not chosen or type(item.get('exit_code')) is not int
                            for item, _, chosen in failed)
    checks = []
    for item, argv, chosen in observed:
        if not chosen:
            continue
        evidence = {'status': 'unverified', 'reported_counts': None,
                    'independent_host_verified': False}
        occurrences = sum(other_argv == argv for _, other_argv, selected_check in observed if selected_check)
        if occurrences != 1:
            evidence['reason'] = 'multiple_selected_invocations'
        elif (type(item.get('exit_code')) is int and item['exit_code'] != 0
                and implementation_stage(phase) == packet_module.HELPER_STAGE):
            evidence = failed_helper_check_evidence(root, packet, item, worker)
        checks.append({'argv': argv, 'native_item_id': item['id'],
                       'exit_code': item.get('exit_code'), 'evidence': evidence})
    return {'schema': 'offdesk_native_command_failure.v1', 'phase': phase['id'],
            'category': 'execution_failed' if execution_failure else 'verification_failed',
            'failed_commands': [{'native_item_id': item['id'], 'exit_code': item.get('exit_code'),
                'selected_check': chosen, 'exit_status_verified': type(item.get('exit_code')) is int,
                'command_sha256': hashlib.sha256(item['command'].encode()).hexdigest()}
                for item, _, chosen in failed],
            'selected_checks': checks, 'implementation_audit': 'not_completed',
            'host_verification': 'not_run', 'canonical_acceptance': False,
            'automatic_retry_authorized': False}


def native_environment(root, selection, worker):
    runtime = root / '.runtime'
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError('Native homes require the regular selected runtime directory')
    for name in ('home', 'codex-home'):
        path = runtime / name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('Native home must be a regular directory inside the selected runtime')
        path.mkdir(mode=0o700, exist_ok=True)
    env = worker.worker_env(root)
    env.update(CODEX_OSS_BASE_URL=selection['base_url'].rstrip('/') + '/v1',
               CODEX_HOME=str(runtime / 'codex-home'), HOME=str(runtime / 'home'))
    return env


def implementation_audit(before,after,phase,commands,recovery,final_sha,root=None):
    stage = implementation_stage(phase)
    changes=sorted(name for name in set(before)|set(after) if before.get(name)!=after.get(name))
    if stage == packet_module.HELPER_STAGE:
        target = packet_module.HELPER_TARGET
        if target in before or changes != [target]:
            raise ValueError('Actual helper implementation must create only the previously absent helper')
    elif not changes or 'scripts/offdesk_request_telemetry.py' not in changes or any(name not in phase['write_paths'] for name in changes):raise ValueError('Actual independent edits must change the selected telemetry source only')
    if any(after.get(name,{}).get('kind')!='file' for name in changes):raise ValueError('Selected code edits must remain regular files')
    if not commands or helper_receipt(commands[0],'recovery-context',root)!=recovery:raise ValueError('Pinned recovery must be the first replacement command')
    if any(item.get('exit_code')!=0 for item in commands):raise ValueError('Failed native command prevents this qualification pass')
    selected={tuple(argv):[] for argv in phase['checks']};progress=[]
    for item in commands[1:]:
        raw=item['command']
        try:argv=checked_command_argv(raw,root)
        except ValueError:argv=[]
        if tuple(argv) in selected:
            selected[tuple(argv)].append(item)
        elif argv[:5]==['/usr/bin/python3','-I','-S','.runtime/offdesk_native_worker.py','progress']:
            if '--request-stdin' in argv or '--request-json' in argv:
                raise ValueError('Progress inputs must use repeated plain string options')
            progress.append(helper_receipt(item,'progress',root))
        elif any(Path(arg).name in raw for check in phase['checks'] for arg in check[1:]
                 if arg.endswith('.py')) and not inspection_command(raw,root):
            raise ValueError('Selected checks may not be nested in another shell command')
        elif '.runtime/offdesk_native_worker.py' in raw and not inspection_command(raw,root):
            raise ValueError('Helper invocations must use one simple selected command')
    if any(len(rows)!=1 for rows in selected.values()):raise ValueError('Each selected post-edit check must execute exactly once')
    if (not 1<=len(progress)<=3 or progress[-1].get('sha256')!=final_sha
            or sum(row.get('sha256')==final_sha for row in progress)!=1
            or any(row.get('claims_verified') is not False for row in progress)):
        raise ValueError('One matching final progress receipt and at most two intermediate saves required')
    return {'passed':True,'changed_paths':changes,'explicit_selected_check_invocations':[{'argv':list(argv),'native_item_id':rows[0]['id'],'exit_code':0} for argv,rows in selected.items()],'native_failed_commands':0,'final_progress_receipt_verified':True,'intermediate_progress_saves':len(progress)-1,'post_execution_only':True,'opaque_subprocess_visibility':'not universal','canonical_acceptance':False}


def host_report(run,phase,audit,transition,verified,worker):
    if verified.get('verification_state')!='passed' or len(verified.get('verification_checks',[]))!=len(phase['checks']) or any(row.get('exit_code')!=0 or row.get('timed_out') for row in verified['verification_checks']):raise ValueError('Independent host checks required')
    path=run.root/phase['summary_path']
    helper = implementation_stage(phase) == packet_module.HELPER_STAGE
    text=(('# Independent native helper-stage observation\n\n' if helper else '# Independent native stream-framing qualification\n\n') +
        'Host report from native edit/recovery receipts and post-edit verification.\n'
        'Review pending; no source integration or canonical acceptance.\n\n'
        'Actual changed paths: '+json.dumps(audit['changed_paths'])+'\n'
        'Context hash: '+transition['context_sha256']+'\n'
        'Pinned progress hash: '+transition['progress_sha256']+'\n'
        'Two distinct native contexts and exact first-command recovery verified.\n'
        'Each selected native check had one explicit successful invocation.\n'
        'All selected commands passed independent host verification.\n'
        'Opaque subprocess visibility and pre-admission fencing are not established.\n'
        'This technical experiment does not accept C5.1a or its unreviewed predecessors.\n\n'
        + ('Pure helper stage only. All 97 model and host checks are separately bound.\n'
           'Reported memory counters still require independent code review.\n'
           'Later model-authored relay integration requires all twelve HTTP cases and regressions.\n\n'
           if helper else '') +
        'Check receipts:\n'+json.dumps([{k:row[k] for k in ('argv','exit_code')} for row in verified['verification_checks']],indent=2)+'\n')
    import os
    with worker.night_parent(run.root,path) as (parent,name):
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        with os.fdopen(fd,'w') as stream:stream.write(text);stream.flush();os.fsync(stream.fileno())
    run.record('HOST_REPORT_RECEIPT.json',{'path':str(path.relative_to(run.root)),'sha256':worker.sha(path),'host_generated':True,'canonical_acceptance':False})


def supervise_counted(run,context,worker,argv,prompt,root,output,deadline,guard,env,**options):
    try:
        return worker.supervise(argv,prompt,root,output,deadline,guard,env,**options)
    finally:
        run.observe_native_calls(context,guard.calls)


def selected_generation_policy(phase, model, helper):
    if 'generation_policy' not in phase:
        return None
    from offdesk_request_telemetry import validate_generation_policy
    if not helper or phase['generation_policy'] is None:
        raise ValueError('Generation policy requires an explicitly selected helper stage')
    policy = validate_generation_policy(phase['generation_policy'])
    if policy['model'] != model:
        raise ValueError('Generation policy must bind the selected native model')
    return policy


def selected_reasoning_policy(phase, model, helper):
    if 'reasoning_policy' not in phase:
        return None
    policy = phase['reasoning_policy']
    if (not helper or not isinstance(policy, dict)
            or set(policy) != {'schema', 'model', 'effort'}
            or policy.get('schema') != 'offdesk_native_reasoning_effort.v1'
            or not isinstance(policy.get('model'), str) or policy['model'] != model
            or policy.get('effort') not in ('none', 'low', 'medium', 'xhigh')):
        raise ValueError('Reasoning policy must bind the selected helper model and explicit supported effort')
    return dict(policy)


def qualify(run,phase,deadline,batch,worker):
    selected_read_paths(phase['read_paths'])
    run.bindings();packet,delivery=load_packet(run.root,phase['delivery_packet'])
    stage = selected_stage_packet(phase, packet)
    helper = stage == packet_module.HELPER_STAGE
    generation_policy = selected_generation_policy(phase, run.selection['model'], helper)
    reasoning_policy = selected_reasoning_policy(phase, run.selection['model'], helper)
    reasoning_options = {} if reasoning_policy is None else {'reasoning_effort': reasoning_policy['effort']}
    if reasoning_policy is not None:
        run.record('NATIVE_REASONING_POLICY.json', {'policy': reasoning_policy,
            'scope': 'both native contexts; independent of output-token limit',
            'wire_or_backend_effect_qualified': False, 'launch_authorized_by_this_record': False})
    generation_options = {} if generation_policy is None else {'generation_policy': generation_policy}
    if generation_policy is not None:
        run.record('NATIVE_GENERATION_POLICY.json', {'policy': generation_policy,
            'scope': 'each Responses request in both native contexts; not a shift token budget',
            'server_enforcement_qualified': False, 'launch_authorized_by_this_record': False})
    if helper:
        run.record('NATIVE_TOOL_PROFILE.json', {'profile': 'helper_commands.v1',
            'web_search': 'disabled', 'view_image': False, 'request_user_input': False,
            'strict_config': True, 'strict_command_only_qualified': False,
            'qualification_scope': 'invocation_declaration; verify the selected CLI request separately',
            'launch_authorized_by_this_record': False})
    if helper and any((run.root / relative).exists() or (run.root / relative).is_symlink()
                      for relative in ('.night/helper-model-check.json', '.night/helper-host-check.json')):
        raise ValueError('Existing helper check reports require review, not replay')
    env = native_environment(run.root, run.selection, worker)
    run.record('INLINE_DELIVERY_RECEIPT.json',{'packet_sha256':phase['delivery_packet']['sha256'],'inline_utf8_bytes':len(delivery.encode()),'contexts_selected':[1,2],'transport':'direct_prompt_input','model_visibility_confirmed':False,'implementation_patch_supplied':False})
    action,instructions=selected_instructions(phase,worker);verify_selected_model(run,deadline)
    worker.verify_outer_boundary(run.root,Path(run.selection['boundary_canary']))
    preflight=worker.preflight(run.root,run.output/'native-preflight.json',phase['preflight_checks'],min(60,deadline-time.time()));worker.validate_preflight(run.root,preflight,time.time())
    before=batch.inventory(run.root)
    context,progress,binding=batch.prepare_task_context(run.root,run.output,phase,{key:run.selection[key] for key in ('run_id','task_id','request_id')})
    context_sha=worker.sha(context);initial_progress_sha=worker.sha(progress);observer=lambda:batch.observe_task_progress(run.root,progress,binding)
    first_prompt=('Checkpoint only: the verified task inputs below are already in this prompt. '
        'Read them here, save one concise actual assessment and the exact selected next action, then finish. '
        'Do not edit source, run checks, or cat the index/contract/checker in this context. '
        'The task-context path below binds progress; its contents need no separate read. '
        'Use plain string progress options; no external requests, delegates, reports or server changes.\n'
        +delivery+'\nTask index: '+str(context.relative_to(run.root))+'\n'
        +plain_progress_instructions(str(context.relative_to(run.root)),context_sha)+instructions)
    out1=run.output/'context-1';guard1=worker.ProgressGuard(max_calls=8)
    first=supervise_counted(run,'context-1',worker,worker.native_argv(str(run.root/run.selection['native_codex']),run.root,run.selection['model'],out1/'final.txt',True,helper,**reasoning_options),first_prompt,run.root,out1,min(deadline,time.time()+180),guard1,env,command_seconds=60,progress_observer=observer,request_telemetry=True,**generation_options);run.record('CONTEXT_1_RESULT.json',first)
    if first.get('stop_reason') or not first.get('turn_completed'):first['verification_state']='failed';return first
    if batch.inventory(run.root)!=before:raise ValueError('Checkpoint context must not edit source')
    observation=observer()
    if observation.get('status')!='bound_model_report' or observation['sha256']==initial_progress_sha or observation['content']['assessment']=='Not yet assessed' or observation['content']['next_action']!=action:raise ValueError('Actual model checkpoint must preserve selected implementation action')
    progress_sha=observation['sha256'];receipts=[]
    for item in completed_commands(out1):
        try:receipts.append(helper_receipt(item,'progress',run.root))
        except (ValueError,KeyError):pass
    if not any(row.get('sha256')==progress_sha and row.get('claims_verified') is False for row in receipts):raise ValueError('First model progress writer receipt required')
    recovery=worker.recovery_context(run.root,context,context_sha,progress_sha);run.record('PINNED_RECOVERY.json',recovery);run.bindings();worker.validate_preflight(run.root,preflight,time.time());verify_selected_model(run,deadline,context=2)
    recovery_command=shlex.join(['python3','-I','-S','.runtime/offdesk_native_worker.py','recovery-context','--root','.','--context',str(context.relative_to(run.root)),'--expected-context-sha256',context_sha,'--expected-progress-sha256',progress_sha])
    _,second_delivery=load_packet(run.root,phase['delivery_packet'])
    second_prompt=('First command must be exactly: '+recovery_command+'\n'
        'If recovery fails, stop. Otherwise implement independently. Use only '
        'command_execution and quoted literal heredocs; no native apply_patch, '
        'delegates, external requests, dependencies, Council/paid calls or server changes.\n'
        +implementation_instructions(str(context.relative_to(run.root)),str(progress.relative_to(run.root)),context_sha,phase['read_paths'])
        +'Selected pre-edit read paths only: '+json.dumps(phase['read_paths'])+'. '
        'Do not reread the inline contract/checker. A repeated read before editing stops this attempt.\n'
        +'Selected checks: '+json.dumps(phase['checks'])+'\n'
        +plain_progress_instructions(str(context.relative_to(run.root)),context_sha)
        +'Keep other source, tests and frozen runtime unchanged; the host owns the report.\n'
        +second_delivery)
    out2=run.output/'context-2';guard2=ImplementationGuard(phase['max_tool_calls']-guard1.calls,lambda:batch.inventory(run.root),observer,run.record,read_paths=phase['read_paths'])
    second=supervise_counted(run,'context-2',worker,worker.native_argv(str(run.root/run.selection['native_codex']),run.root,run.selection['model'],out2/'final.txt',True,helper,**reasoning_options),second_prompt,run.root,out2,deadline,guard2,env,command_seconds=60,progress_observer=observer,request_telemetry=True,**generation_options);run.record('SECOND_CONTEXT_NATIVE_RESULT.json',dict(second));run.bindings()
    for key in ('tool_calls','started_tool_calls','completed_tool_calls','in_flight_tool_calls'):second[key]+=first[key]
    for key in ('nonprogress_tool_calls', 'progress_tool_calls'):
        if key in second and key in first:
            second[key] += first[key]
    run.record('CONTEXT_2_RESULT.json',second)
    if second.get('stop_reason') or not second.get('turn_completed'):second['verification_state']='failed';return second
    commands = completed_commands(out2)
    failure = native_command_failure(phase, commands, run.root, packet, worker)
    if failure is not None:
        name = phase['id'] + '-native-command-failure.json'
        failure['artifact_path'] = str((run.output / name).relative_to(run.root))
        failure['native_log'] = {'path': str((out2 / 'stdout.log').relative_to(run.root)),
                                 'sha256': worker.sha(out2 / 'stdout.log')}
        run.record(name, failure)
        return {**second, 'verification_state': 'failed', 'qualification_failure': failure,
                'native_verification_checks': failure['selected_checks'], 'verification_checks': []}
    final_saved,final_sha=worker.read_task_json(run.root,progress,worker.MAX_PROGRESS_BYTES);worker.validate_progress(final_saved,binding)
    events=[json.loads(line) for line in (out2/'stdout.log').read_text().splitlines()]
    if any(event.get('item',{}).get('type') not in (None,'command_execution','reasoning','agent_message','error') for event in events):raise ValueError('Only command-execution tools are selected for this bounded trial')
    audit=implementation_audit(before,batch.inventory(run.root),phase,commands,recovery,final_sha,run.root)
    if sorted(final_saved['changed_files'])!=audit['changed_paths']:raise ValueError('Saved changed-file paths must match actual source edits')
    run.record('HOST_IMPLEMENTATION_AUDIT.json',audit)
    first_id,second_id=thread_id(out1),thread_id(out2)
    if first_id==second_id:raise ValueError('Distinct native contexts required')
    transition={'kind':'native_context_replacement','independent_implementation_observed':True,'first_thread_id':first_id,'second_thread_id':second_id,'context_sha256':context_sha,'progress_sha256':progress_sha,'recovery_first_command_verified':True,'canonical_acceptance':False};run.record('CONTEXT_TRANSITION.json',transition)
    model_receipt = helper_check_receipt(run.root, '.night/helper-model-check.json', packet, worker) if helper else None
    if helper:
        run.record('HELPER_MODEL_CHECK_RECEIPT.json', model_receipt)
    host_checks = [packet_module.helper_check_argv(packet['checker_path'], host=True)] if helper else phase['checks']
    checked_source = batch.inventory(run.root) if helper else None
    verified=worker.verify_result(second,out2,run.root,host_checks,deadline)
    if helper:
        run.bindings()
        if batch.inventory(run.root) != checked_source:
            raise ValueError('Independent host helper checks changed source')
        if worker.sha(run.root / model_receipt['path']) != model_receipt['sha256']:
            raise ValueError('Model helper check report changed during independent host verification')
    if helper and not verified.get('stop_reason'):
        host_receipt = helper_check_receipt(run.root, '.night/helper-host-check.json', packet, worker)
        run.record('HELPER_CHECK_RECEIPTS.json', {'implementation_stage': stage,
            'model': model_receipt, 'host': host_receipt, 'qualification_complete': False,
            'later_full_relay_integration_required': True})
    if not verified.get('stop_reason'):host_report(run,phase,audit,transition,verified,worker)
    return verified


def verify_driver_files(root, selection):
    for driver_path in (Path(__file__).resolve(), Path(packet_module.__file__).resolve()):
        if not driver_path.is_relative_to(root.resolve()):
            raise ValueError('Independent driver and packet module must be frozen inside the selected root')
        relative = str(driver_path.relative_to(root.resolve()))
        if selection.get('native_runtime_hashes', {}).get(relative) != worker_module.sha(driver_path):
            raise ValueError('Exact independent driver and packet module hashes must be bound by the new selection')


def main():
    script,selection_path=map(Path,sys.argv[1:]);selection=json.loads(selection_path.read_text())
    if selection.get('operator_runtime_approved') is not True:raise ValueError('This new independent task selection is not approved')
    verify_driver_files(Path(selection['root']), selection)
    sys.path.insert(0,str(script.parent));import offdesk_four_phase_run as batch;import offdesk_native_worker as worker
    root=Path(selection['root']);run=batch.FourPhaseRun(root,selection,root/selection['output_relative']);run.backend=lambda phase,deadline:qualify(run,phase,deadline,batch,worker);result=run.run();print(json.dumps(result,indent=2));return 0 if result['status']=='completed' else 1
if __name__=='__main__':sys.exit(main())
