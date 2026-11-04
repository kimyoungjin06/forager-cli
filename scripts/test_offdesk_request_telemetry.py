"""Actual HTTP forwarding, timing, privacy and cancellation regressions."""
import contextlib
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import offdesk_native_worker as worker
from offdesk_request_telemetry import RequestTelemetry


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.seen = []
        self.ready = threading.Event()
        test = self

        class Backend(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.do_POST()

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                test.seen.append((self.command, self.path, body, self.headers.get('Authorization')))
                with contextlib.suppress(OSError):
                    if body == b'wait':
                        test.ready.set()
                        time.sleep(.7)
                        self.send_response(200)
                        self.send_header('Content-Length', '2')
                        self.end_headers()
                        self.wfile.write(b'{}')
                    elif body == b'chunkrace':
                        self.send_response(200)
                        self.send_header('Transfer-Encoding', 'chunked')
                        self.send_header('X-Test-Cancel-Race', 'upstream')
                        self.end_headers()
                        self.wfile.write(b'1\r\nx\r\n')
                        self.wfile.flush()
                        test.ready.set()
                        time.sleep(.5)
                        self.wfile.write(b'0\r\n\r\n')
                    elif body in (b'stream', b'holdstream'):
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.wfile.write(b'data: {"choices":[{"delta":{"reasoning_content":"RESPONSE_SECRET"}}]}\n\n')
                        self.wfile.flush()
                        test.ready.set()
                        time.sleep(.3 if body == b'stream' else 1.5)
                        self.wfile.write(b'data: [DONE]\n\n')
                        self.close_connection = True
                    elif body == b'truncate':
                        self.send_response(200)
                        self.send_header('Content-Length', '100')
                        self.end_headers()
                        self.wfile.write(b'short')
                        self.close_connection = True
                    elif body == b'burst':
                        raw = b'data: {"type":"response.reasoning_text.delta","delta":"RESPONSE_SECRET"}\n\n' * 12000 + b'data: [DONE]\n\n'
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Content-Length', str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                    else:
                        self.send_response(200)
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        self.backend.daemon_threads = True
        self.thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.thread.start()
        self.upstream = f'http://127.0.0.1:{self.backend.server_port}/v1'

    def tearDown(self):
        self.backend.shutdown()
        self.backend.server_close()
        self.thread.join(timeout=1)
        self.temp.cleanup()

    def client(self, probe):
        return http.client.HTTPConnection('127.0.0.1', probe.server.server_port, timeout=2)

    def summary(self, name='probe'):
        return json.loads((self.root/name/'summary.json').read_text())

    def policy(self):
        return {'schema': 'offdesk_ollama_responses_generation.v1',
                'model': 'fixture-model', 'max_output_tokens': 2048}

    def test_generation_policy_requires_explicit_model_and_positive_finite_limit(self):
        invalid = [False, {}, {'schema': 'unknown'},
                   *[dict(self.policy(), max_output_tokens=value) for value in
                     (None, False, True, 0, -1, -2, 32769, 2048.0, '2048')],
                   *[dict(self.policy(), model=value) for value in (None, '', 'wrong\n', ' x', 'x' * 257)],
                   dict(self.policy(), extra='ignored')]
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                RequestTelemetry(self.upstream, self.root/'probe', time.time()+5, generation_policy=policy)
        self.assertFalse((self.root/'probe').exists())
        self.assertEqual(self.seen, [])

    def test_generation_policy_sets_cap_without_widening_or_changing_other_json(self):
        policy = self.policy()
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5, generation_policy=policy) as probe:
            policy['max_output_tokens'] = 32000
            for path, previous, expected in [('/v1/responses', None, 2048), ('/responses', 128, 128),
                                             ('/v1/responses', 4096, 2048), ('/v1/responses', 2048, 2048)]:
                packet = {'model': 'fixture-model', 'input': 'PROMPT_SECRET 한글', 'stream': True,
                          'tools': [{'type': 'function', 'name': 'exec_command', 'parameters': {}}],
                          'reasoning': {'summary': 'auto'}, 'parallel_tool_calls': True}
                if previous is not None:
                    packet['max_output_tokens'] = previous
                original = dict(packet)
                client = self.client(probe)
                client.request('POST', path, body=json.dumps(packet).encode(),
                               headers={'Content-Type': 'application/json; charset=utf-8', 'Authorization': 'AUTH_SECRET'})
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                forwarded = json.loads(response.read())
                client.close()
                self.assertEqual(forwarded.pop('max_output_tokens'), expected)
                original.pop('max_output_tokens', None)
                self.assertEqual(forwarded, original)
            self.assertTrue(all(row[3] == 'AUTH_SECRET' for row in self.seen))
        summary = self.summary()
        self.assertEqual(summary['generation_policy'], self.policy())
        self.assertEqual([row['max_output_tokens_sent'] for row in summary['requests']], [2048,128,2048,2048])
        evidence = ''.join(f.read_text() for f in (self.root/'probe').iterdir())
        for secret in ('PROMPT_SECRET', 'AUTH_SECRET', '한글'):
            self.assertNotIn(secret, evidence)

    def test_generation_policy_blocks_alternate_inference_paths_before_upstream(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5, generation_policy=self.policy()) as probe:
            for path in ('/api/generate', '/api/chat', '/v1/chat/completions', '/chat/completions',
                         '/v1/responses/compact', '/responses/compact'):
                client = self.client(probe)
                client.request('POST', path, body=b'{}', headers={'Content-Type': 'application/json'})
                response = client.getresponse(); self.assertEqual(response.status, 403); response.read(); client.close()
            client = self.client(probe); client.request('GET', '/v1/responses')
            response = client.getresponse(); self.assertEqual(response.status, 403); response.read(); client.close()
            self.assertEqual(self.seen, [])
            client = self.client(probe); client.request('POST', '/api/show', body=b'METADATA_SECRET')
            response = client.getresponse(); self.assertEqual(response.read(), b'METADATA_SECRET'); client.close()
        self.assertEqual([row[:3] for row in self.seen], [('POST', '/api/show', b'METADATA_SECRET')])
        controls = [json.loads(line) for line in (self.root/'probe/control-events.jsonl').read_text().splitlines()]
        self.assertEqual(sum(row['event'] == 'generation_policy_hold' for row in controls), 7)

    def test_generation_policy_rejects_ambiguous_or_invalid_request_without_logging_body(self):
        invalid = [b'not JSON PROMPT_SECRET', b'[]', b'null', b'{}', b'\xff',
                   b'{"model":"fixture-model","model":"fixture-model"}',
                   b'{"model":"other"}', b'{"model":"fixture-model","input":{"x":1,"x":2}}',
                   b'{"model":"fixture-model","input":NaN}',
                   b'{"model":"fixture-model","input":1e400}',
                   *[json.dumps({'model':'fixture-model','max_output_tokens':v}).encode()
                     for v in (None,False,True,0,-1,-2,1.5,'2048')],
                   *[json.dumps({'model':'fixture-model',key:2048}).encode()
                     for key in ('max_tokens','max_completion_tokens','options')]]
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5, generation_policy=self.policy()) as probe:
            for body in invalid:
                client = self.client(probe)
                client.request('POST','/v1/responses',body=body,headers={'Content-Type':'application/json'})
                response = client.getresponse(); self.assertEqual(response.status,400,body); response.read(); client.close()
        self.assertEqual(self.seen, [])
        evidence = ''.join(f.read_text() for f in (self.root/'probe').iterdir())
        self.assertNotIn('PROMPT_SECRET',evidence)

    def test_generation_policy_rejects_encoded_requests(self):
        with RequestTelemetry(self.upstream,self.root/'probe',time.time()+5,generation_policy=self.policy()) as probe:
            for headers in ({}, {'Content-Type':'text/plain'},
                            {'Content-Type':'application/json','Content-Encoding':'gzip'}):
                client=self.client(probe);client.request('POST','/v1/responses',body=b'{}',headers=headers)
                response=client.getresponse();self.assertEqual(response.status,415);response.read();client.close()
        self.assertEqual(self.seen,[])

    def test_generation_policy_rechecks_encoded_body_bound(self):
        packet={'model':'fixture-model','input':'한'*(1500000)}
        raw=json.dumps(packet,ensure_ascii=False).encode()
        self.assertLess(len(raw),8*1024*1024)
        with RequestTelemetry(self.upstream,self.root/'probe',time.time()+10,generation_policy=self.policy()) as probe:
            client=self.client(probe);client.request('POST','/v1/responses',body=raw,headers={'Content-Type':'application/json'})
            response=client.getresponse();self.assertEqual(response.status,413);response.read();client.close()
        self.assertEqual(self.seen,[])

    def test_payload_and_authorization_forward_without_content_logging(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5) as probe:
            client = self.client(probe)
            client.request('POST', '/v1/chat/completions', body=b'PROMPT_SECRET', headers={'Authorization':'AUTH_SECRET'})
            response = client.getresponse()
            self.assertEqual(response.read(), b'PROMPT_SECRET')
            client.close()
        self.assertEqual(self.seen[0], ('POST','/v1/chat/completions',b'PROMPT_SECRET','AUTH_SECRET'))
        evidence = ''.join(f.read_text() for f in (self.root/'probe').iterdir())
        for secret in ('PROMPT_SECRET','AUTH_SECRET'):
            self.assertNotIn(secret, evidence)
        request = self.summary()['requests'][0]
        self.assertEqual(request['outcome'], 'http_completed')
        self.assertEqual(request['response_bytes'], len(b'PROMPT_SECRET'))
        self.assertIsNotNone(request['first_byte_at'])

    def test_stream_first_byte_and_reasoning_are_observed_before_completion(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5) as probe:
            client = self.client(probe)
            client.request('POST','/v1/chat/completions',body=b'stream')
            response = client.getresponse()
            first = response.read1(4096)
            self.assertIn(b'RESPONSE_SECRET', first)
            self.assertTrue(self.ready.is_set())
            self.assertNotIn(b'[DONE]', first)
            self.assertIn(b'[DONE]', response.read())
            client.close()
        rows = [json.loads(line) for line in (self.root/'probe/events.jsonl').read_text().splitlines()]
        self.assertIn('reasoning',[r.get('kind') for r in rows])
        self.assertTrue(self.summary()['requests'][0]['terminal_frame_observed'])
        self.assertNotIn('RESPONSE_SECRET',(self.root/'probe/events.jsonl').read_text())

    def test_client_cancellation_during_header_wait_is_not_backend_ack(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5) as probe:
            client = self.client(probe)
            client.request('POST','/v1/chat/completions',body=b'wait')
            self.assertTrue(self.ready.wait(1))
            until = time.monotonic()+1
            while time.monotonic()<until:
                with probe.lock:
                    if probe.active and next(iter(probe.active.values()))['row']['phase']=='awaiting_headers':
                        break
                time.sleep(.005)
            probe.observe_stop('no_tool_progress')
            client.close()
            until = time.monotonic()+1
            while probe.active and time.monotonic()<until:
                time.sleep(.01)
        summary = self.summary()
        self.assertEqual(summary['native_stop']['active_requests'][0]['phase'],'awaiting_headers')
        request = summary['requests'][0]
        self.assertEqual(request['outcome'],'client_disconnected')
        self.assertIsNone(request['first_byte_at'])
        self.assertIsNone(request['backend_cancellation_acknowledged'])

    def test_shutdown_cancels_pending_upstream_and_bounds_event_log(self):
        with RequestTelemetry(self.upstream,self.root/'probe',time.time()+5,max_events=2) as probe:
            client = self.client(probe)
            client.request('POST','/v1/chat/completions',body=b'wait')
            self.assertTrue(self.ready.wait(1))
            probe.observe_stop('deadline_reached')
        client.close()
        summary = self.summary()
        self.assertEqual(summary['events_written'],2)
        self.assertGreater(summary['events_dropped'],0)
        self.assertEqual(summary['unfinished_local_requests'],[])
        self.assertEqual(summary['requests'][0]['outcome'],'relay_shutdown')

    def test_dense_real_http_stream_keeps_separate_control_journal(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+20, max_events=8) as probe:
            client = http.client.HTTPConnection('127.0.0.1', probe.server.server_port, timeout=12)
            client.request('POST', '/v1/responses', body=b'burst', headers={'Authorization': 'AUTH_SECRET'})
            actual = client.getresponse().read()
            client.close()
            until = time.monotonic()+1
            while probe.active and time.monotonic()<until:
                time.sleep(.005)
            probe.observe_stop(None)
        expected = b'data: {"type":"response.reasoning_text.delta","delta":"RESPONSE_SECRET"}\n\n' * 12000 + b'data: [DONE]\n\n'
        self.assertEqual(actual, expected)
        summary = self.summary()
        self.assertEqual(summary['events_written'], 8)
        self.assertGreater(summary['events_dropped'], 11000)
        row = summary['requests'][0]
        self.assertEqual(row['response_bytes'], len(expected))
        self.assertEqual(row['frames'], 12001)
        self.assertEqual(row['frame_kind_counts'], {'reasoning': 12000, 'terminal': 1})
        self.assertEqual(row['last_frame_kind'], 'terminal')
        controls = [json.loads(line) for line in (self.root/'probe/control-events.jsonl').read_text().splitlines()]
        names = [row['event'] for row in controls]
        for name in ('request_started', 'response_headers', 'response_first_byte', 'request_finished', 'native_stop', 'relay_stopped'):
            self.assertIn(name, names)
        self.assertNotIn('stream_frame', names)
        retention = summary['event_retention']
        self.assertEqual(retention['control_events_dropped'], 0)
        self.assertEqual(retention['control_events_written'], len(controls))
        self.assertEqual(retention['control_sha256'], hashlib.sha256((self.root/'probe/control-events.jsonl').read_bytes()).hexdigest())
        self.assertEqual(retention['native_stop_sha256'], hashlib.sha256((self.root/'probe/native-stop.json').read_bytes()).hexdigest())
        for path in (self.root/'probe').iterdir():
            text = path.read_text()
            self.assertNotIn('RESPONSE_SECRET', text)
            self.assertNotIn('AUTH_SECRET', text)

    def test_control_budget_is_finite_but_first_stop_snapshot_is_independent(self):
        with mock.patch('offdesk_request_telemetry.CONTROL_EVENT_LIMIT', 4):
            with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5, max_events=2) as probe:
                for _ in range(25):
                    probe.record('relay_route_hold')
                probe.observe_stop('no_tool_progress')
                snapshot = (self.root/'probe/native-stop.json').read_bytes()
                probe.observe_stop('later_reason_must_not_replace_stop')
                self.assertEqual((self.root/'probe/native-stop.json').read_bytes(), snapshot)
            summary = self.summary()
        self.assertEqual(summary['events_written'], 2)
        self.assertEqual(summary['event_retention']['control_events_written'], 4)
        self.assertGreater(summary['event_retention']['control_events_dropped'], 0)
        self.assertEqual(len((self.root/'probe/control-events.jsonl').read_text().splitlines()), 4)
        retained = json.loads(snapshot)
        self.assertEqual(retained['native_stop']['reason'], 'no_tool_progress')
        self.assertEqual(retained['native_stop'], summary['native_stop'])
        self.assertGreater(retained['accounting_before_stop_event']['control_events_dropped'], 0)

    def test_stop_frame_snapshot_does_not_mutate_when_later_frames_arrive(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5, max_events=2) as probe:
            for _ in range(10):
                probe.native_event({'type':'item.updated','item':{'type':'reasoning','id':'flood'}})
            now = time.time()
            row = {'request_id':1, 'method':'POST', 'path':'/v1/responses', 'started_at':now,
                   'headers_at':now, 'first_byte_at':now, 'last_byte_at':now, 'status':200,
                   'response_bytes':100, 'frames':0, 'terminal_frame_observed':False,
                   'phase':'reading_response', 'error_class':None}
            probe.active[1] = {'row':row}
            probe.last_compaction_at = now-1
            probe.frame(row, b'{"type":"response.reasoning_text.delta","delta":"RESPONSE_SECRET"}')
            probe.observe_stop('no_tool_progress')
            raw = (self.root/'probe/native-stop.json').read_bytes()
            probe.frame(row, b'[DONE]')
            self.assertEqual(probe.native_stop['active_requests'][0]['frame_kind_counts'], {'reasoning':1})
            self.assertEqual(probe.native_stop['post_compaction_inference_requests'][0]['frame_kind_counts'], {'reasoning':1})
            self.assertFalse(probe.native_stop['active_requests'][0]['terminal_frame_observed'])
            self.assertEqual(row['frame_kind_counts'], {'reasoning':1,'terminal':1})
            self.assertEqual((self.root/'probe/native-stop.json').read_bytes(), raw)
            probe.active.clear()
        self.assertEqual(self.summary()['native_stop']['active_requests'][0]['last_frame_kind'], 'reasoning')

    def test_native_lifecycle_and_notices_survive_detail_flood_without_content(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5, max_events=2) as probe:
            for _ in range(40):
                probe.native_event({'type':'item.updated','item':{'type':'reasoning','id':'flood'}})
            probe.native_event({'type':'item.completed','item':{'type':'error','id':'m','message':'metadata fallback NOTICE_SECRET'}})
            for kind in ('item.started','item.completed'):
                probe.native_event({'type':kind,'item':{'type':'command_execution','id':'command','command':'SECRET_COMMAND'}})
            probe.observe_stop('no_tool_progress')
        rows = [json.loads(line) for line in (self.root/'probe/control-events.jsonl').read_text().splitlines()]
        native = [r for r in rows if r['event']=='native_event']
        self.assertEqual([r['native_type'] for r in native], ['item.completed','item.started','item.completed'])
        self.assertTrue(self.summary()['native_stop']['metadata_fallback_observed'])
        self.assertIsNotNone(self.summary()['native_stop']['progress_timing']['last_tool_completed_at'])
        for p in (self.root/'probe').iterdir():
            self.assertNotIn('NOTICE_SECRET', p.read_text())
            self.assertNotIn('SECRET_COMMAND', p.read_text())

    def test_healthy_silent_stream_is_not_cancelled_at_socket_poll_timeout(self):
        with RequestTelemetry(self.upstream,self.root/'probe',time.time()+5) as probe:
            client = self.client(probe)
            client.request('POST','/v1/chat/completions',body=b'stream')
            response = client.getresponse();self.assertIn(b'RESPONSE_SECRET',response.read1(4096))
            with probe.lock:
                next(iter(probe.active.values()))['client'].settimeout(.03)
            self.assertIn(b'[DONE]',response.read());client.close()
        self.assertEqual(self.summary()['requests'][0]['outcome'],'http_completed')
        self.assertNotIn('local_cancel_requested',(self.root/'probe/events.jsonl').read_text())

    def test_disconnect_cancels_body_wait_even_after_http_connection_releases_socket(self):
        with RequestTelemetry(self.upstream,self.root/'probe',time.time()+5) as probe:
            client = self.client(probe)
            client.request('POST','/v1/chat/completions',body=b'holdstream')
            response = client.getresponse();response.read1(4096);response.close();client.close()
            until = time.monotonic()+1
            while probe.active and time.monotonic()<until:time.sleep(.01)
        self.assertEqual(self.summary()['unfinished_local_requests'],[])
        self.assertEqual(self.summary()['requests'][0]['outcome'],'client_disconnected')

    def test_cancel_does_not_close_response_under_chunk_reader(self):
        entered, resume = threading.Event(), threading.Event()
        original = http.client.HTTPResponse._read_next_chunk_size

        def paused_chunk_size(response):
            size = original(response)
            if response.getheader('X-Test-Cancel-Race') == 'upstream' and not entered.is_set():
                entered.set()
                if not resume.wait(2):
                    raise TimeoutError('Local cancellation fixture did not release reader')
            return size

        self.addCleanup(resume.set)
        with mock.patch.object(http.client.HTTPResponse, '_read_next_chunk_size', paused_chunk_size):
            with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5) as probe:
                client = self.client(probe)
                self.addCleanup(client.close)
                client.request('POST', '/v1/responses', body=b'chunkrace')
                response = client.getresponse()
                self.assertTrue(entered.wait(1))
                probe.observe_stop('no_tool_progress')
                response.close()
                client.close()
                until = time.monotonic()+1
                while time.monotonic() < until:
                    with probe.lock:
                        state = next(iter(probe.active.values()), None)
                        cancelled = state is not None and state['cancel'] is not None
                    if cancelled:
                        break
                    time.sleep(.005)
                self.assertTrue(cancelled)
                resume.set()
                until = time.monotonic()+1
                while probe.active and time.monotonic() < until:
                    time.sleep(.005)
            summary = self.summary()
        self.assertEqual(summary['unfinished_local_requests'], [])
        row = summary['requests'][0]
        self.assertEqual(row['outcome'], 'client_disconnected')
        self.assertNotEqual(row['error_class'], 'AttributeError')
        self.assertIsNone(row['backend_cancellation_acknowledged'])
        self.assertIsNone(summary['native_stop']['active_requests'][0]['error_class'])

    def test_model_mutation_and_query_routes_are_not_forwarded(self):
        with RequestTelemetry(self.upstream,self.root/'probe',time.time()+5) as probe:
            for path in ('/api/pull','/api/tags?token=URL_SECRET'):
                client = self.client(probe)
                client.request('POST',path,body=b'{}')
                self.assertEqual(client.getresponse().status,403)
                client.close()
        self.assertEqual(self.seen,[])
        self.assertNotIn('URL_SECRET',(self.root/'probe/events.jsonl').read_text())

    def test_unrecognized_native_fields_do_not_log_arbitrary_content(self):
        with RequestTelemetry(self.upstream,self.root/'probe',time.time()+5) as probe:
            probe.native_event({'type':'EVENT_SECRET','item':{'type':'TYPE_SECRET','id':'ID_SECRET','text':'TEXT_SECRET'}})
        evidence = (self.root/'probe/events.jsonl').read_text()
        for secret in ('EVENT_SECRET','TYPE_SECRET','ID_SECRET','TEXT_SECRET'):
            self.assertNotIn(secret,evidence)

    def test_truncated_upstream_body_is_not_completed(self):
        with RequestTelemetry(self.upstream,self.root/'probe',time.time()+5) as probe:
            client = self.client(probe)
            client.request('POST','/v1/chat/completions',body=b'truncate')
            response = client.getresponse()
            with self.assertRaises(http.client.IncompleteRead):
                response.read()
            client.close()
        self.assertEqual(self.summary()['requests'][0]['outcome'],'upstream_error')

    def test_native_idle_termination_keeps_active_request_evidence(self):
        root = self.root/'native';root.mkdir()
        code="import os,json,sys,urllib.request;sys.stdin.read();urllib.request.urlopen(urllib.request.Request(os.environ['CODEX_OSS_BASE_URL']+'/chat/completions',data=b'wait')).read();print(json.dumps({'type':'turn.completed'}))"
        original_popen = subprocess.Popen
        input_readers = []

        def ready_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            try:
                process.stdin.write(b'prompt');process.stdin.close()
                self.assertTrue(self.ready.wait(3))
                # As with the existing lifecycle fixtures, measure the idle
                # interval after interpreter startup and actual request arrival.
                # Input is already delivered. Keep a real pipe for the
                # supervisor's nonblocking input path without resending it.
                input_reader, input_writer = os.pipe()
                input_readers.append(input_reader)
                process.stdin = os.fdopen(input_writer, 'wb')
                return process
            except BaseException:
                worker.stop_group(process)
                raise

        try:
            with mock.patch.object(worker.subprocess,'Popen',side_effect=ready_process):
                result = worker.supervise([sys.executable,'-I','-S','-u','-c',code],'prompt',root,root/'.night/run',
                                          time.time()+5,worker.ProgressGuard(),dict(os.environ,CODEX_OSS_BASE_URL=self.upstream),
                                          idle_seconds=.35,request_telemetry=True)
        finally:
            for descriptor in input_readers:
                os.close(descriptor)
        self.assertEqual(result['stop_reason'],'no_tool_progress')
        summary = json.loads(Path(result['request_telemetry']['summary']).read_text())
        self.assertTrue(Path(result['request_telemetry']['control_events']).is_file())
        self.assertTrue(Path(result['request_telemetry']['native_stop_snapshot']).is_file())
        self.assertEqual(summary['native_stop']['active_requests'][0]['phase'],'awaiting_headers')
        self.assertEqual(summary['unfinished_local_requests'],[])
        packet = json.loads((root/'.night/run/council-request.json').read_text())
        self.assertEqual(packet['result_sha256'],worker.sha(root/'.night/run/result.json'))

    def test_post_compaction_header_wait_is_distinct_from_metadata_and_replays(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5) as probe:
            tool = {'type':'item.completed','item':{'id':'one','type':'command_execution'}}
            notice = {'type':'item.completed','item':{'id':'compact','type':'error','message':'compaction NOTICE_SECRET'}}
            probe.native_event(tool)
            probe.native_event(notice)
            saved_tool, saved_notice = probe.last_tool_at, probe.last_compaction_at
            probe.native_event(tool)
            probe.native_event(notice)
            self.assertEqual((probe.last_tool_at, probe.last_compaction_at), (saved_tool, saved_notice))
            metadata = self.client(probe)
            metadata.request('POST', '/api/show', body=b'{}')
            metadata.getresponse().read()
            metadata.close()
            client = self.client(probe)
            client.request('POST', '/v1/responses', body=b'wait')
            self.assertTrue(self.ready.wait(2))
            until = time.monotonic()+1
            while time.monotonic()<until:
                with probe.lock:
                    if any(v['row']['phase']=='awaiting_headers' for v in probe.active.values()):
                        break
                time.sleep(.005)
            probe.observe_stop('no_tool_progress')
            client.close()
        stop = self.summary()['native_stop']
        requests = stop['post_compaction_inference_requests']
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['phase'], 'awaiting_headers')
        self.assertIsNone(requests[0]['last_byte_at'])
        self.assertIsNone(requests[0]['seconds_since_last_byte'])
        self.assertEqual(stop['progress_timing']['last_tool_completed_at'], saved_tool)
        self.assertEqual(stop['progress_timing']['last_compaction_notice_at'], saved_notice)
        self.assertTrue(stop['local_observation_only'])
        self.assertIsNone(stop['backend_cancellation_acknowledged'])
        self.assertFalse(stop['gpu_load_observed'])
        self.assertNotIn('NOTICE_SECRET', (self.root/'probe/events.jsonl').read_text())

    def test_post_compaction_body_wait_keeps_byte_timing_without_claiming_tool_progress(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5) as probe:
            probe.native_event({'type':'item.completed','item':{'id':'compact','type':'error','message':'compaction'}})
            client = self.client(probe)
            client.request('POST', '/v1/responses', body=b'holdstream')
            response = client.getresponse()
            self.assertIn(b'RESPONSE_SECRET', response.read1(4096))
            probe.observe_stop('no_tool_progress')
            client.close()
        stop = self.summary()['native_stop']
        row = stop['post_compaction_inference_requests'][0]
        self.assertIsNotNone(row['last_byte_at'])
        self.assertGreaterEqual(row['seconds_since_last_byte'], 0)
        self.assertIsNone(stop['progress_timing']['last_tool_completed_at'])
        self.assertIsNone(stop['progress_timing']['seconds_since_tool_completion'])
        self.assertFalse(row['terminal_frame_observed'])
        self.assertNotIn('RESPONSE_SECRET', (self.root/'probe/events.jsonl').read_text())

    def test_completed_post_compaction_request_survives_no_active_request_stop(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5) as probe:
            probe.native_event({'type':'item.completed','item':{'id':'meta','type':'error','message':'metadata fallback'}})
            probe.native_event({'type':'item.completed','item':{'id':'compact','type':'error','message':'compaction'}})
            client = self.client(probe)
            client.request('POST', '/v1/responses', body=b'stream')
            self.assertIn(b'[DONE]', client.getresponse().read())
            client.close()
            until = time.monotonic()+1
            while probe.active and time.monotonic()<until:
                time.sleep(.005)
            probe.observe_stop('no_tool_progress')
        stop = self.summary()['native_stop']
        self.assertEqual(stop['active_requests'], [])
        self.assertTrue(stop['post_compaction_inference_requests'][0]['terminal_frame_observed'])
        self.assertTrue(stop['metadata_fallback_observed'])
        self.assertTrue(stop['local_observation_only'])

    def test_anonymous_compaction_notices_are_not_assumed_to_be_replays(self):
        with RequestTelemetry(self.upstream, self.root/'probe', time.time()+5) as probe:
            event = {'type':'item.completed','item':{'type':'error','message':'compaction'}}
            with mock.patch('offdesk_request_telemetry.time.time', return_value=10):
                probe.native_event(event)
            with mock.patch('offdesk_request_telemetry.time.time', return_value=20):
                probe.native_event(event)
            self.assertEqual(probe.last_compaction_at, 20)
        self.assertEqual(self.summary()['unfinished_local_requests'], [])


if __name__ == '__main__':
    unittest.main()
