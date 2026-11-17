"""Opt-in loopback timing relay for one explicitly selected model endpoint.

Record protocol metadata only. An explicit generation policy may narrow the
Responses output limit; otherwise bodies are forwarded unchanged without logging.
local socket cancellation does not certify cancellation on the GPU server.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import select
import socket
import sys
import threading
import time
import urllib.parse

ROUTES = frozenset(("/", "/api/version", "/api/tags", "/api/show", "/api/ps", "/api/chat", "/api/generate",
                    "/v1/models", "/v1/chat/completions", "/v1/responses",
                    "/v1/responses/compact", "/responses/compact", "/models", "/chat/completions", "/responses"))
INFERENCE = frozenset(("/api/chat", "/api/generate", "/v1/chat/completions",
                       "/v1/responses", "/v1/responses/compact", "/responses/compact", "/chat/completions", "/responses"))
HOP_HEADERS = frozenset(("connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                         "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"))
CONTROL_EVENT_LIMIT = 2048
FRAME_KINDS = frozenset(("reasoning", "content", "tool", "terminal", "other"))
CONTROL_EVENTS = frozenset(("relay_started", "relay_stopped", "native_stop", "request_started",
    "response_headers", "response_first_byte", "request_finished", "local_cancel_requested",
    "relay_concurrency_hold", "relay_handler_error", "relay_route_hold", "relay_request_framing_hold",
    "client_upload_incomplete", "relay_request_budget_hold", "frame_inspection_dropped",
    "generation_policy_applied", "generation_policy_hold"))


def validate_generation_policy(policy):
    if (not isinstance(policy, dict) or set(policy) != {"schema", "model", "max_output_tokens"}
            or policy.get("schema") != "offdesk_ollama_responses_generation.v1"
            or not isinstance(policy.get("model"), str) or not 1 <= len(policy["model"]) <= 256
            or policy["model"] != policy["model"].strip()
            or any(ord(char) < 32 for char in policy["model"])
            or type(policy.get("max_output_tokens")) is not int
            or not 1 <= policy["max_output_tokens"] <= 32768):
        raise ValueError("Explicit model and positive bounded Responses output limit required")
    return dict(policy)


def generation_request(body, policy):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous generation request")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("Non-finite generation request")

    try:
        packet = json.loads(body.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant)
        if (not isinstance(packet, dict) or packet.get("model") != policy["model"]
                or any(key in packet for key in ("max_tokens", "max_completion_tokens", "options"))):
            raise ValueError("Unselected or ambiguous generation request")
        previous = packet.get("max_output_tokens", policy["max_output_tokens"])
        if type(previous) is not int or previous < 1:
            raise ValueError("Invalid existing generation output limit")
        effective = min(previous, policy["max_output_tokens"])
        packet["max_output_tokens"] = effective
        return json.dumps(packet, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode(), effective
    except (ValueError, RecursionError, OverflowError):
        raise ValueError("Generation request cannot satisfy the selected policy") from None


def is_control_event(event, fields):
    if event in CONTROL_EVENTS:
        return True
    return event == "native_event" and (
        fields.get("native_type") in ("thread.started", "turn.started", "turn.completed", "turn.failed", "error")
        or fields.get("notice") is not None
        or fields.get("native_type") in ("item.started", "item.completed"))


class RequestTelemetry:
    def __init__(self, upstream: str, output: Path, deadline: float, max_events: int = 10000,
                 generation_policy=None):
        parsed = urllib.parse.urlsplit(upstream)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/", "/v1")
                or not math.isfinite(deadline) or deadline <= time.time()
                or type(max_events) is not int or max_events < 2 or output.exists()):
            raise ValueError("Fresh telemetry output and explicit bounded endpoint required")
        self.parsed, self.output, self.deadline = parsed, output, deadline
        self.generation_policy = (None if generation_policy is None else validate_generation_policy(generation_policy))
        self.lock = threading.RLock()
        self.active, self.finished = {}, []
        self.events, self.dropped, self.sequence = 0, 0, 0
        self.event_sequence = self.control_events = self.control_dropped = 0
        self.max_events = max_events
        self.stopping = False
        self.server = self.thread = None
        self.native_stop = None
        self.last_tool_at = self.last_tool_monotonic = None
        self.last_compaction_at = self.last_compaction_monotonic = None
        self.seen_tools, self.seen_notices = set(), set()
        self.metadata_fallback_observed = False
        self.last_response_progress = None

    def response_progress(self):
        """Semantic generation activity, independent of capped detail logging."""
        with self.lock:
            return None if self.last_response_progress is None else dict(self.last_response_progress)

    def record(self, event: str, **fields):
        with self.lock:
            self.event_sequence += 1
            row = {"event": event, "observed_at": time.time(), "event_sequence": self.event_sequence, **fields}
            raw = json.dumps(row, allow_nan=False) + "\n"
            if is_control_event(event, fields):
                if self.control_events < CONTROL_EVENT_LIMIT:
                    with (self.output / "control-events.jsonl").open("a") as stream:
                        stream.write(raw)
                    self.control_events += 1
                else:
                    self.control_dropped += 1
            if self.events >= self.max_events:
                self.dropped += 1
                return
            with (self.output / "events.jsonl").open("a") as stream:
                stream.write(raw)
            self.events += 1

    def native_event(self, event):
        item = event.get("item", {})
        notice = None
        if item.get("type") == "error":
            text = item.get("message", "").lower()
            notice = ("compaction_notice" if "compaction" in text else
                      "model_metadata_fallback" if "metadata" in text and "fallback" in text else "other_error")
        native_type = event["type"] if event["type"] in (
            "thread.started", "turn.started", "turn.completed", "turn.failed", "error",
            "item.started", "item.updated", "item.completed") else "other"
        item_type = item.get("type") if item.get("type") in (
            "command_execution", "reasoning", "agent_message", "error") else "other"
        identifier = hashlib.sha256(str(item.get("id", "")).encode()).hexdigest()
        with self.lock:
            if native_type == "item.completed" and item_type == "command_execution" and identifier not in self.seen_tools:
                self.seen_tools.add(identifier)
                self.last_tool_at, self.last_tool_monotonic = time.time(), time.monotonic()
            notice_id = (identifier, hashlib.sha256(str(item.get("message", "")).encode()).hexdigest())
            if notice == "compaction_notice" and (not item.get("id") or notice_id not in self.seen_notices):
                if item.get("id"):
                    self.seen_notices.add(notice_id)
                self.last_compaction_at, self.last_compaction_monotonic = time.time(), time.monotonic()
            self.metadata_fallback_observed |= notice == "model_metadata_fallback"
            self.record("native_event", native_type=native_type, item_type=item_type, notice=notice,
                        item_id_sha256=identifier)

    def observe_stop(self, reason):
        with self.lock:
            if self.native_stop is not None:
                return
            now, tick = time.time(), time.monotonic()
            active = [copy.deepcopy(v["row"]) for v in self.active.values()]
            for row in active:
                row["seconds_since_last_byte"] = None if row["last_byte_at"] is None else max(0, now - row["last_byte_at"])
            post_compaction = ([] if self.last_compaction_at is None else
                [copy.deepcopy(row) for row in (*self.finished, *active)
                 if row["method"] == "POST" and row["path"] in INFERENCE and row["started_at"] >= self.last_compaction_at])
            self.native_stop = {"reason": reason, "observed_at": now, "active_requests": active,
                "progress_timing": {"last_tool_completed_at": self.last_tool_at,
                    "seconds_since_tool_completion": None if self.last_tool_monotonic is None else tick - self.last_tool_monotonic,
                    "last_compaction_notice_at": self.last_compaction_at,
                    "seconds_since_compaction_notice": None if self.last_compaction_monotonic is None else tick - self.last_compaction_monotonic},
                "post_compaction_inference_requests": post_compaction,
                "metadata_fallback_observed": self.metadata_fallback_observed,
                "response_progress": (None if self.last_response_progress is None else
                    {"kind": self.last_response_progress["kind"],
                     "observed_at": self.last_response_progress["observed_at"],
                     "seconds_since_progress": max(0, tick - self.last_response_progress["monotonic"])}),
                "local_observation_only": True, "backend_cancellation_acknowledged": None, "gpu_load_observed": False}
            snapshot = {"schema": "offdesk_native_stop_snapshot.v1", "native_stop": self.native_stop,
                "accounting_before_stop_event": {"events_written": self.events, "events_dropped": self.dropped,
                    "control_events_written": self.control_events, "control_events_dropped": self.control_dropped,
                    "event_sequence": self.event_sequence}}
            temporary = self.output / "native-stop.json.partial"
            with temporary.open("x") as stream:
                json.dump(snapshot, stream, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.output / "native-stop.json")
            if os.name == "posix":
                descriptor = os.open(self.output, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            self.record("native_stop", **self.native_stop)

    def cancel(self, state, reason):
        with self.lock:
            if (state["cancel"] is not None or
                    self.active.get(state["row"]["request_id"]) is not state or state["row"]["phase"] == "complete"):
                return
            state["cancel"] = reason
            self.record("local_cancel_requested", request_id=state["row"]["request_id"], reason=reason,
                        backend_cancellation_acknowledged=None)
            conn = state["upstream"]
            upstream_socket = state.get("socket") or conn.sock
            if upstream_socket is not None:
                with contextlib.suppress(OSError):
                    upstream_socket.shutdown(socket.SHUT_RDWR)
            # Interrupt I/O here; the forwarding thread owns response/connection
            # close so it cannot lose HTTPResponse.fp in the middle of read1.

    def poll_clients(self):
        with self.lock:
            states = list(self.active.values())
        for state in states:
            if time.time() >= self.deadline:
                self.cancel(state, "selected_deadline")
            else:
                try:
                    ready, _, _ = select.select([state["client"]], [], [], 0)
                    if not ready:
                        continue
                    data = state["client"].recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
                    if not data:
                        self.cancel(state, "client_disconnected")
                except BlockingIOError:
                    pass
                except (OSError, ValueError):
                    self.cancel(state, "client_disconnected")

    def __enter__(self):
        self.output.mkdir(parents=True, mode=0o700)
        probe = self

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

            def __init__(self):
                self.slots = threading.BoundedSemaphore(4)
                super().__init__(("127.0.0.1", 0), Handler)

            def process_request(self, request, address):
                request.settimeout(5)
                if not self.slots.acquire(blocking=False):
                    with contextlib.suppress(OSError):
                        request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    self.shutdown_request(request)
                    probe.record("relay_concurrency_hold")
                    return
                try:
                    super().process_request(request, address)
                except Exception:
                    self.slots.release()
                    raise

            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    self.slots.release()

            def service_actions(self):
                probe.poll_clients()

            def handle_error(self, request, client_address):
                probe.record("relay_handler_error", error_class=sys.exc_info()[0].__name__)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def reject(self, code):
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

            def do_GET(self):
                self.forward()

            def do_POST(self):
                self.forward()

            def forward(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path not in ROUTES or parsed.query or parsed.fragment or probe.stopping:
                    probe.record("relay_route_hold")
                    self.reject(403)
                    return
                if self.headers.get("Transfer-Encoding"):
                    probe.record("relay_request_framing_hold")
                    self.reject(411)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.reject(400)
                    return
                if not 0 <= length <= 8 * 1024 * 1024:
                    self.reject(413)
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    probe.record("client_upload_incomplete")
                    return
                effective_limit = None
                if probe.generation_policy is not None and parsed.path in INFERENCE:
                    if self.command != "POST" or parsed.path not in ("/v1/responses", "/responses"):
                        probe.record("generation_policy_hold", reason="unsupported_inference_route")
                        self.reject(403)
                        return
                    if (self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json"
                            or self.headers.get("Content-Encoding", "identity").lower() != "identity"):
                        probe.record("generation_policy_hold", reason="unsupported_request_encoding")
                        self.reject(415)
                        return
                    try:
                        body, effective_limit = generation_request(body, probe.generation_policy)
                    except ValueError:
                        probe.record("generation_policy_hold", reason="invalid_generation_request")
                        self.reject(400)
                        return
                    if len(body) > 8 * 1024 * 1024:
                        probe.record("generation_policy_hold", reason="encoded_request_exceeds_bound")
                        self.reject(413)
                        return
                cls = http.client.HTTPSConnection if probe.parsed.scheme == "https" else http.client.HTTPConnection
                conn = cls(probe.parsed.hostname, probe.parsed.port, timeout=min(5, max(.01, probe.deadline - time.time())))
                start = time.monotonic()
                with probe.lock:
                    probe.sequence += 1
                    row = {"request_id": probe.sequence, "method": self.command, "path": parsed.path,
                           "request_bytes": length, "started_at": time.time(), "headers_at": None,
                           "first_byte_at": None, "last_byte_at": None, "status": None,
                           "response_bytes": 0, "frames": None, "terminal_frame_observed": None,
                           "phase": "connecting", "error_class": None}
                    if effective_limit is not None:
                        row.update(upstream_request_bytes=len(body), max_output_tokens_sent=effective_limit)
                    if probe.sequence > 256:
                        probe.record("relay_request_budget_hold")
                        self.reject(503)
                        return
                    state = {"row": row, "upstream": conn, "socket": None, "client": self.connection, "cancel": None}
                    probe.active[row["request_id"]] = state
                probe.record("request_started", **row)
                if effective_limit is not None:
                    probe.record("generation_policy_applied", request_id=row["request_id"],
                                 max_output_tokens_sent=effective_limit, server_enforcement_qualified=False)
                headers_sent, chunked = False, False
                buffer = b""
                outcome = "upstream_error"
                try:
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
                    if state["cancel"] is not None:
                        raise ConnectionAbortedError()
                    conn.request(self.command, parsed.path, body=body or None, headers=headers)
                    if state["cancel"] is not None:
                        raise ConnectionAbortedError()
                    if conn.sock is None:
                        raise ConnectionAbortedError()
                    state["socket"] = conn.sock
                    conn.sock.settimeout(max(.01, probe.deadline - time.time()))
                    row["phase"] = "awaiting_headers"
                    response = conn.getresponse()
                    row.update(status=response.status, headers_at=time.time())
                    probe.record("response_headers", request_id=row["request_id"], status=response.status,
                                 elapsed_ms=(time.monotonic() - start) * 1000)
                    if 300 <= response.status < 400:
                        self.reject(502)
                        outcome = "upstream_redirect_held"
                        return
                    chunked = response.getheader("Content-Length") is None
                    self.send_response(response.status)
                    for k, v in response.getheaders():
                        if k.lower() not in HOP_HEADERS:
                            self.send_header(k, v)
                    self.send_header("Transfer-Encoding" if chunked else "Content-Length",
                                     "chunked" if chunked else response.getheader("Content-Length"))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    headers_sent = True
                    inspect = parsed.path in INFERENCE and response.getheader("Content-Encoding", "identity") == "identity"
                    row["phase"] = "reading_response"
                    if inspect:
                        row.update(frames=0, terminal_frame_observed=False)
                    while True:
                        chunk = response.read1(8192)
                        if not chunk:
                            if response.length not in (None, 0):
                                raise http.client.IncompleteRead(b"")
                            break
                        now = time.time()
                        if row["first_byte_at"] is None:
                            row["first_byte_at"] = now
                            probe.record("response_first_byte", request_id=row["request_id"],
                                         elapsed_ms=(time.monotonic() - start) * 1000)
                        row["last_byte_at"] = now
                        row["response_bytes"] += len(chunk)
                        if response.length == 0:
                            row["phase"] = "complete"
                        self.wfile.write((f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n") if chunked else chunk)
                        self.wfile.flush()
                        if inspect:
                            buffer += chunk
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                if len(line) <= 65536:
                                    probe.frame(row, line)
                            if len(buffer) > 65536:
                                buffer = b""
                                probe.record("frame_inspection_dropped", request_id=row["request_id"])
                    if inspect and buffer:
                        probe.frame(row, buffer)
                    if state["cancel"] is not None:
                        raise ConnectionAbortedError()
                    row["phase"] = "complete"
                    if chunked:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                    outcome = "http_completed"
                except (BrokenPipeError, ConnectionResetError) as error:
                    row["error_class"] = type(error).__name__
                    outcome = state["cancel"] or "transport_reset"
                except (OSError, http.client.HTTPException) as error:
                    row["error_class"] = type(error).__name__
                    outcome = state["cancel"] or "upstream_error"
                    if not headers_sent:
                        with contextlib.suppress(OSError):
                            self.reject(502)
                except Exception as error:
                    row["error_class"] = type(error).__name__
                    outcome = state["cancel"] or "relay_error"
                finally:
                    conn.close()
                    self.close_connection = True
                    row.update(outcome=outcome, ended_at=time.time(), elapsed_ms=(time.monotonic() - start) * 1000,
                               backend_cancellation_acknowledged=None)
                    with probe.lock:
                        probe.active.pop(row["request_id"], None)
                        probe.finished.append(dict(row))
                        probe.record("request_finished", **row)

        self.server = Server()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}" + ("/v1" if self.parsed.path == "/v1" else "")
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()
        self.record("relay_started", connection_reuse=False, backend_retargeted=False)
        return self

    def frame(self, row, raw):
        raw = raw.strip()
        progress_kind = None
        if raw.startswith(b"data:"):
            raw = raw[5:].strip()
        if raw == b"[DONE]":
            kind = "terminal"
        else:
            try:
                packet = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return
            if not isinstance(packet, dict):
                return
            choices = packet.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            delta = choice.get("delta", packet.get("message", {}))
            delta = delta if isinstance(delta, dict) else {}
            kind = ("terminal" if packet.get("done") is True or packet.get("type") == "response.completed"
                    or choice.get("finish_reason") is not None else
                    "reasoning" if delta.get("reasoning_content") or delta.get("thinking") or packet.get("thinking")
                    or packet.get("type") in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta") else
                    "tool" if delta.get("tool_calls") or packet.get("type") == "response.function_call_arguments.delta" else
                    "content" if delta.get("content") or packet.get("response")
                    or packet.get("type") == "response.output_text.delta" else "other")
            text = lambda value: isinstance(value, str) and bool(value.strip())
            if kind != "terminal":
                if any(text(value) for value in (delta.get("reasoning_content"), delta.get("thinking"), packet.get("thinking"))):
                    progress_kind = "reasoning"
                elif any(text(value) for value in (delta.get("content"), packet.get("response"))):
                    progress_kind = "content"
                elif text(packet.get("delta")) and isinstance(packet.get("type"), str):
                    progress_kind = {"response.reasoning_summary_text.delta": "reasoning",
                        "response.reasoning_text.delta": "reasoning", "response.output_text.delta": "content",
                        "response.function_call_arguments.delta": "tool"}.get(packet.get("type"))
                if progress_kind is None and isinstance(delta.get("tool_calls"), list):
                    for call in delta["tool_calls"]:
                        function = call.get("function") if isinstance(call, dict) else None
                        if isinstance(function, dict) and any(text(function.get(key)) for key in ("name", "arguments")):
                            progress_kind = "tool"
                            break
        with self.lock:
            row["frames"] += 1
            row["terminal_frame_observed"] |= kind == "terminal"
            counts = row.setdefault("frame_kind_counts", {})
            counts[kind] = counts.get(kind, 0) + 1
            row.update(last_frame_kind=kind, last_frame_at=time.time())
            if (progress_kind is not None and row.get("method") == "POST" and row.get("path") in INFERENCE
                    and type(row.get("status")) is int and 200 <= row["status"] < 300):
                self.last_response_progress = {"monotonic": time.monotonic(), "observed_at": time.time(),
                                               "kind": progress_kind}
            self.record("stream_frame", request_id=row["request_id"], kind=kind)

    def __exit__(self, *args):
        with self.lock:
            self.stopping = True
            states = list(self.active.values())
        for state in states:
            self.cancel(state, "relay_shutdown")
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        until = time.monotonic() + 1
        while self.active and time.monotonic() < until:
            time.sleep(.01)
        with self.lock:
            self.record("relay_stopped", finished_requests=len(self.finished), unfinished_requests=len(self.active))
            control_path = self.output / "control-events.jsonl"
            snapshot_path = self.output / "native-stop.json"
            summary = {"schema": "offdesk_request_timing.v1", "native_stop": self.native_stop,
                       "requests": self.finished, "unfinished_local_requests": [v["row"] for v in self.active.values()],
                       "events_written": self.events, "events_dropped": self.dropped,
                       "backend_cancellation_acknowledged": None, "gpu_load_observed": False,
                       "connection_reuse": False, "prompt_or_response_content_logged": False,
                       "generation_policy": self.generation_policy,
                       "generation_limit_scope": "per_request; server enforcement not qualified",
                       "event_retention": {"schema": "offdesk_event_retention.v1",
                           "detail_event_limit": self.max_events, "control_event_limit": CONTROL_EVENT_LIMIT,
                           "control_events_written": self.control_events, "control_events_dropped": self.control_dropped,
                           "control_path": "control-events.jsonl", "control_sha256": hashlib.sha256(control_path.read_bytes()).hexdigest(),
                           "native_stop_path": "native-stop.json" if snapshot_path.exists() else None,
                           "native_stop_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest() if snapshot_path.exists() else None}}
            (self.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
