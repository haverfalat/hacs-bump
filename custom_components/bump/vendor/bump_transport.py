"""TLS HTTP/2 transport for Bump's bidirectional gRPC streams."""
from __future__ import annotations

from collections import deque
import socket
import ssl
import struct
import time
from urllib.parse import unquote, urlsplit

import h2.config
import h2.connection
import h2.events


class StreamError(RuntimeError):
    def __init__(self, method: str, status: int, message: str):
        self.status = status
        self.method = method
        super().__init__(f"{method}: gRPC status {status}: {message}")


class GrpcStream:
    """Own one HTTP/2 connection and RPC. recv() returns a complete protobuf.

    None means the receive deadline elapsed; EOFError means a successful server
    close. Cancelling locally never claims a successful gRPC trailer was seen.
    """
    MAX_MESSAGE = 16 * 1024 * 1024

    def __init__(self, endpoint: str, method: str, headers: list[tuple[str, str]],
                 payloads: tuple[bytes, ...] = (), half_close: bool = False):
        target = urlsplit(endpoint)
        if target.scheme != "https" or not target.hostname or target.path not in ("", "/"):
            raise ValueError("Expected an HTTPS gRPC authority without a path")
        self.method = method
        self.socket = None
        self.buffer = bytearray()
        self.queue = deque()
        self.metadata = {}
        self.ended = False
        self.status = None
        self.last_received = time.monotonic()
        self.last_ping = self.last_received
        self.connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=True, header_encoding="utf-8"))
        context = ssl.create_default_context()
        context.set_alpn_protocols(["h2"])
        port = target.port or 443
        raw = socket.create_connection((target.hostname, port), timeout=15)
        try:
            self.socket = context.wrap_socket(raw, server_hostname=target.hostname)
            if self.socket.selected_alpn_protocol() != "h2":
                raise RuntimeError("Server did not negotiate HTTP/2")
            self.socket.settimeout(1)
            self.connection.initiate_connection()
            self.stream_id = self.connection.get_next_available_stream_id()
            authority = target.netloc
            pseudo = [(":method", "POST"), (":scheme", "https"), (":authority", authority),
                      (":path", "/" + method.lstrip("/"))]
            self.connection.send_headers(self.stream_id, pseudo + headers,
                                         end_stream=half_close and not payloads)
            self._flush()
            for payload in payloads:
                self.send(payload)
            if half_close and payloads:
                self.connection.end_stream(self.stream_id)
                self._flush()
        except BaseException:
            if self.socket is not None:
                self.socket.close()
            else:
                raw.close()
            raise

    def _flush(self) -> None:
        pending = self.connection.data_to_send()
        if pending:
            self.socket.sendall(pending)

    def send(self, payload: bytes) -> None:
        """Send one UpEvent, preserving any concurrent incoming DownEvents."""
        if self.ended or self.socket is None:
            raise RuntimeError("RPC is closed")
        framed = b"\0" + struct.pack(">I", len(payload)) + payload
        offset = 0
        deadline = time.monotonic() + 15
        while offset < len(framed):
            size = min(self.connection.local_flow_control_window(self.stream_id),
                       self.connection.max_outbound_frame_size, len(framed) - offset)
            if size:
                self.connection.send_data(self.stream_id, framed[offset:offset + size])
                offset += size
                self._flush()
            elif time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for HTTP/2 send window")
            else:
                self._receive()

    def _receive(self) -> None:
        try:
            raw = self.socket.recv(65536)
        except socket.timeout:
            now = time.monotonic()
            if now - self.last_received > 30 and now - self.last_ping > 30:
                self.connection.ping(struct.pack(">Q", time.monotonic_ns()))
                self.last_ping = now
                self._flush()
            if now - self.last_received > 90:
                raise TimeoutError("HTTP/2 peer has been unresponsive for 90 seconds")
            return
        if not raw:
            raise ConnectionError("Connection closed without a gRPC trailer")
        self.last_received = time.monotonic()
        for event in self.connection.receive_data(raw):
            if isinstance(event, (h2.events.ResponseReceived, h2.events.TrailersReceived)):
                if event.stream_id != self.stream_id:
                    continue
                self.metadata.update(event.headers)
                if ":status" in self.metadata and self.metadata[":status"] != "200":
                    raise RuntimeError("Unexpected HTTP " + self.metadata[":status"])
                if "grpc-status" in self.metadata:
                    self.status = int(self.metadata["grpc-status"])
                    if self.status:
                        raise StreamError(self.method, self.status,
                                          unquote(self.metadata.get("grpc-message", "")))
            elif isinstance(event, h2.events.DataReceived):
                self.connection.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if event.stream_id != self.stream_id:
                    continue
                self.buffer.extend(event.data)
                while len(self.buffer) >= 5:
                    compressed, length = struct.unpack_from(">BI", self.buffer)
                    if compressed:
                        raise ValueError("Compressed gRPC message was not requested")
                    if length > self.MAX_MESSAGE:
                        raise ValueError("gRPC message exceeds the receive limit")
                    if len(self.buffer) < 5 + length:
                        break
                    self.queue.append(bytes(self.buffer[5:5 + length]))
                    del self.buffer[:5 + length]
            elif isinstance(event, h2.events.StreamEnded) and event.stream_id == self.stream_id:
                self.ended = True
                if self.buffer:
                    raise ValueError("Server closed in the middle of a gRPC message")
                if self.status is None:
                    raise RuntimeError("Server closed without grpc-status")
            elif isinstance(event, h2.events.StreamReset) and event.stream_id == self.stream_id:
                raise ConnectionError(f"HTTP/2 stream reset: {event.error_code}")
            elif isinstance(event, h2.events.ConnectionTerminated):
                # A graceful GOAWAY allows an already accepted stream to finish.
                if event.error_code or event.last_stream_id < self.stream_id:
                    raise ConnectionError(f"HTTP/2 connection terminated: {event.error_code}")
        self._flush()  # Includes window updates, SETTINGS ACKs, and PING ACKs.

    def recv(self, timeout: float = 15) -> bytes | None:
        deadline = time.monotonic() + timeout
        while not self.queue:
            if self.ended:
                raise EOFError("RPC ended with grpc-status 0")
            if time.monotonic() >= deadline:
                return None
            self._receive()
        return self.queue.popleft()

    def close(self) -> None:
        if self.socket is not None:
            try:
                if not self.ended:
                    self.connection.reset_stream(self.stream_id, error_code=8)
                    self._flush()
            except (OSError, h2.exceptions.H2Error):
                pass
            finally:
                self.socket.close()
                self.socket = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
