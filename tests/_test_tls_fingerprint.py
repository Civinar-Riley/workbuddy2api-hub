"""TLS fingerprint profiles and the optional local CONNECT bridge."""

import hashlib
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import wb_upstream_pool as pool  # noqa: E402
import wb_accounts  # noqa: E402


TLS12_CIPHERS = [
    49199, 49195, 49200, 49196, 49191,
    52393, 52392, 49161, 49171, 49162,
    49172, 156, 157, 47, 53,
]
ALL_CIPHERS = [4865, 4866, 4867] + TLS12_CIPHERS


OFFICIAL_JA3 = (
    "771,"
    "4865-4866-4867-49199-49195-49200-49196-49191-52393-52392-49161-49171-49162-49172-156-157-47-53,"
    "0-23-65281-10-11-35-13-51-45-43,"
    "29-23-24,"
    "0"
)
OFFICIAL_JA3_MD5 = "71dc8c533dd919ae9f4963224a4ba8fd"
OFFICIAL_JA4 = "t13d0a1200_5d04281c6031_b76c5ee2507d"


class EnvironmentPatch(object):
    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for name, value in self.values.items():
            self.saved[name] = os.environ.get(name)
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        return self

    def __exit__(self, exc_type, exc, traceback):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError("socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def capture_client_hello(context, server_name="www.workbuddy.ai"):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    captured = {}

    def server():
        try:
            conn, _address = listener.accept()
            conn.settimeout(5)
            try:
                header = recv_exact(conn, 5)
                length = struct.unpack("!H", header[3:5])[0]
                captured["raw"] = header + recv_exact(conn, length)
            finally:
                conn.close()
        except Exception as err:
            captured["error"] = err
        finally:
            listener.close()

    thread = threading.Thread(target=server)
    thread.start()
    client = socket.create_connection(listener.getsockname(), timeout=5)
    client.settimeout(5)
    try:
        context.wrap_socket(client, server_hostname=server_name)
    except (EOFError, OSError, ssl.SSLError):
        pass
    finally:
        try:
            client.close()
        except Exception:
            pass
    thread.join(5)
    if "error" in captured:
        raise captured["error"]
    if "raw" not in captured:
        raise AssertionError("ClientHello was not captured")
    return captured["raw"]


def parse_client_hello(raw):
    handshake = b""
    offset = 0
    while offset + 5 <= len(raw):
        content_type = raw[offset]
        length = struct.unpack("!H", raw[offset + 3:offset + 5])[0]
        payload = raw[offset + 5:offset + 5 + length]
        if content_type == 22:
            handshake += payload
        offset += 5 + length
    if len(handshake) < 4 or handshake[0] != 1:
        raise AssertionError("first TLS handshake message is not ClientHello")
    body_length = struct.unpack("!I", b"\x00" + handshake[1:4])[0]
    body = handshake[4:4 + body_length]
    if len(body) != body_length:
        raise AssertionError("truncated ClientHello")

    offset = 0
    legacy_version = struct.unpack("!H", body[offset:offset + 2])[0]
    offset += 2 + 32
    session_id_length = body[offset]
    offset += 1 + session_id_length
    cipher_length = struct.unpack("!H", body[offset:offset + 2])[0]
    offset += 2
    ciphers = [
        struct.unpack("!H", body[pos:pos + 2])[0]
        for pos in range(offset, offset + cipher_length, 2)
    ]
    offset += cipher_length
    compression_length = body[offset]
    offset += 1 + compression_length
    extension_total = struct.unpack("!H", body[offset:offset + 2])[0]
    offset += 2
    extension_end = offset + extension_total

    extensions = []
    extension_data = {}
    while offset + 4 <= extension_end:
        ext_type = struct.unpack("!H", body[offset:offset + 2])[0]
        ext_length = struct.unpack("!H", body[offset + 2:offset + 4])[0]
        offset += 4
        data = body[offset:offset + ext_length]
        extensions.append(ext_type)
        extension_data[ext_type] = data
        offset += ext_length

    groups = []
    group_data = extension_data.get(10, b"")
    if len(group_data) >= 2:
        group_length = struct.unpack("!H", group_data[:2])[0]
        groups = [
            struct.unpack("!H", group_data[pos:pos + 2])[0]
            for pos in range(2, 2 + group_length, 2)
        ]

    sigalgs = []
    sig_data = extension_data.get(13, b"")
    if len(sig_data) >= 2:
        sig_length = struct.unpack("!H", sig_data[:2])[0]
        sigalgs = [
            struct.unpack("!H", sig_data[pos:pos + 2])[0]
            for pos in range(2, 2 + sig_length, 2)
        ]
    ecpf_data = extension_data.get(11, b"")
    ecpf = list(ecpf_data[1:1 + ecpf_data[0]]) if len(ecpf_data) >= 1 else []
    return {
        "legacy_version": legacy_version,
        "session_id_length": session_id_length,
        "ciphers": ciphers,
        "extensions": extensions,
        "groups": groups,
        "ecpf": ecpf,
        "sigalgs": sigalgs,
        "alpn": extension_data.get(16),
    }


class LocalConnectBridge(object):
    def __init__(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.server.settimeout(0.2)
        self.port = self.server.getsockname()[1]
        self.lock = threading.Lock()
        self.requests = []
        self.connections = 0
        self.closed = False
        self.thread = threading.Thread(target=self._accept_loop)
        self.thread.daemon = True
        self.thread.start()

    def address(self):
        return "127.0.0.1:%d" % self.port

    def _accept_loop(self):
        while not self.closed:
            try:
                conn, _address = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                self.connections += 1
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn):
        conn.settimeout(5)
        stream = conn.makefile("rb")
        try:
            while not self.closed:
                line = stream.readline()
                if not line:
                    return
                line = line.rstrip(b"\r\n")
                if not line:
                    continue
                parts = line.decode("latin-1").split()
                if len(parts) < 2:
                    return
                method, target = parts[0], parts[1]
                header_lines = []
                headers = {}
                while True:
                    header = stream.readline()
                    if header in (b"\r\n", b"\n", b""):
                        break
                    name, _sep, value = header.decode("latin-1").partition(":")
                    name, value = name.strip(), value.strip()
                    header_lines.append((name, value))
                    headers[name.lower()] = value
                length = int(headers.get("content-length") or 0)
                if length:
                    stream.read(length)
                with self.lock:
                    self.requests.append({
                        "method": method,
                        "target": target,
                        "headers": headers,
                        "header_lines": header_lines,
                    })
                if method == "CONNECT":
                    conn.sendall(
                        b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    continue
                body = b'{"ok":true}'
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode("ascii") +
                    b"\r\nConnection: keep-alive\r\n\r\n" + body)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass
            conn.close()

    def close(self):
        self.closed = True
        try:
            self.server.close()
        except Exception:
            pass
        self.thread.join(2)


class TLSFingerprintTests(unittest.TestCase):
    def tearDown(self):
        pool.close_all()

    def test_mode_switches_between_node_and_python(self):
        with EnvironmentPatch(WB_TLS_FINGERPRINT=None):
            self.assertEqual(pool.tls_fingerprint_mode(), "python")
        for value in ("0", "off", "false", "no", "python", "native", "stdlib"):
            with EnvironmentPatch(WB_TLS_FINGERPRINT=value):
                self.assertEqual(pool.tls_fingerprint_mode(), "python")
        for value in ("node",):
            with EnvironmentPatch(WB_TLS_FINGERPRINT=value):
                self.assertEqual(pool.tls_fingerprint_mode(), "node")
        with EnvironmentPatch(WB_TLS_FINGERPRINT=""):
            self.assertEqual(pool.tls_fingerprint_mode(), "python")
        for value in ("go", "something-else"):
            with EnvironmentPatch(WB_TLS_FINGERPRINT=value):
                with self.assertRaises(ValueError):
                    pool.tls_fingerprint_mode()

    def test_python_mode_disables_bridge(self):
        with EnvironmentPatch(
                WB_TLS_FINGERPRINT="python",
                WB_TLS_BRIDGE="127.0.0.1:41000"):
            self.assertEqual(pool.tls_bridge_addr(), "")

    def test_external_bridge_requires_loopback_and_secret(self):
        with EnvironmentPatch(WB_TLS_FINGERPRINT="node", WB_TLS_BRIDGE="192.0.2.1:1234", WB_TLS_BRIDGE_SECRET="x" * 64):
            with self.assertRaises(ValueError):
                pool.tls_bridge_addr()
        with EnvironmentPatch(WB_TLS_FINGERPRINT="node", WB_TLS_BRIDGE="127.0.0.1:1234", WB_TLS_BRIDGE_SECRET=None):
            with self.assertRaises(ValueError):
                pool.tls_bridge_addr()

    def test_bridge_does_not_silently_bypass_disabled_pool(self):
        with EnvironmentPatch(WB_TLS_FINGERPRINT="node", WB_UPSTREAM_KEEPALIVE="0"):
            with mock.patch("urllib.request.urlopen") as fallback:
                with self.assertRaisesRegex(RuntimeError, "KEEPALIVE"):
                    wb_accounts.urlopen(urllib.request.Request("https://example.test/"))
                fallback.assert_not_called()

    def test_bridge_refuses_unauthenticated_requests(self):
        candidates = [p for p in pool._tls_bridge_candidates() if os.path.isfile(p) and os.access(p, os.X_OK)]
        if not candidates:
            self.skipTest("bridge binary unavailable")
        proc = subprocess.Popen([candidates[0], "--listen", "127.0.0.1:0", "--parent-watch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            proc.stdin.write("x" * 64 + "\n")
            proc.stdin.flush()
            host, port = pool._parse_tls_bridge_address(proc.stdout.readline())
            with socket.create_connection((host, port), timeout=5) as client:
                client.sendall(b"CONNECT 127.0.0.1:1 HTTP/1.1\r\nHost: 127.0.0.1:1\r\n\r\n")
                self.assertIn(b" 403 ", client.recv(1024))
        finally:
            pool._stop_tls_bridge_process(proc)

    def test_node_fallback_keeps_stdlib_verification(self):
        with EnvironmentPatch(
                WB_TLS_FINGERPRINT="node",
                WB_TLS_BRIDGE="off",
                WB_CA_BUNDLE=None):
            context = pool.shared_ssl_context()
            fields = parse_client_hello(capture_client_hello(context))

        self.assertEqual(fields["legacy_version"], 0x0303)
        self.assertEqual(fields["session_id_length"], 32)
        self.assertTrue(fields["ciphers"])
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_official_header_spelling_helper(self):
        headers = [
            ("x-request-id", "request"),
            ("x-conversation-id", "conversation"),
            ("x-ide-type", "WorkBuddy"),
            ("traceparent", "trace"),
            ("x-stainless-lang", "js"),
            ("x-private-header", "value"),
        ]
        normalized = pool.normalize_header_spelling(headers)
        self.assertEqual(list(normalized), [
            "X-Request-ID",
            "X-Conversation-ID",
            "X-IDE-Type",
            "traceparent",
            "x-stainless-lang",
            "x-private-header",
        ])

    def test_https_uses_local_connect_bridge_and_reuses_it(self):
        bridge = LocalConnectBridge()
        proxy = "http://u:p@10.0.0.1:8080"
        before = pool.stats()
        try:
            with EnvironmentPatch(
                    WB_TLS_FINGERPRINT="node",
                    WB_TLS_BRIDGE=bridge.address(),
                    WB_TLS_BRIDGE_SECRET="x" * 64,
                    no_proxy="127.0.0.1,localhost",
                    NO_PROXY="127.0.0.1,localhost"):
                pool.close_all()
                for suffix in ("one", "two"):
                    req = urllib.request.Request(
                        "https://www.workbuddy.ai/v2/chat/completions",
                        data=b'{"message":"' + suffix.encode("ascii") + b'"}',
                        method="POST",
                        headers={"Authorization": "Bearer fixture"},
                    )
                    req.wb_exact_headers = [
                        ("Authorization", "Bearer fixture"),
                        ("Content-Type", "application/json"),
                        ("X-Request-ID", "request-" + suffix),
                    ]
                    resp = pool.urlopen(req, timeout=3, proxy=proxy)
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.read(), b'{"ok":true}')
                    resp.close()

            connects = [
                item for item in bridge.requests if item["method"] == "CONNECT"
            ]
            self.assertEqual(len(connects), 1)
            self.assertEqual(connects[0]["target"], "www.workbuddy.ai:443")
            self.assertEqual(
                connects[0]["headers"].get("x-wb-upstream-proxy"), proxy)
            self.assertEqual(bridge.connections, 1)
            self.assertEqual(len([
                item for item in bridge.requests if item["method"] == "POST"
            ]), 2)
            delta = {
                key: pool.stats()[key] - before[key]
                for key in before
            }
            self.assertEqual(delta["created"], 1)
            self.assertEqual(delta["reused"], 1)
        finally:
            pool.close_all()
            bridge.close()

    def test_bundled_utls_bridge_emits_exact_official_ja3_and_ja4(self):
        candidates = [
            p for p in pool._tls_bridge_candidates()
            if os.path.isfile(p) and os.access(p, os.X_OK)
        ]
        if not candidates:
            return
        proxy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        proxy.bind(("127.0.0.1", 0))
        proxy.listen(1)
        proxy.settimeout(5)
        proxy_port = proxy.getsockname()[1]
        captured = {}

        def mock_upstream_proxy():
            try:
                conn, _ = proxy.accept()
                conn.settimeout(5)
                try:
                    buf = b""
                    while b"\r\n\r\n" not in buf:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                    conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    header = recv_exact(conn, 5)
                    length = struct.unpack("!H", header[3:5])[0]
                    captured["raw"] = header + recv_exact(conn, length)
                finally:
                    conn.close()
            except Exception as err:
                captured["error"] = err
            finally:
                proxy.close()

        thread = threading.Thread(target=mock_upstream_proxy)
        thread.start()
        proc = subprocess.Popen(
            [candidates[0], "--listen", "127.0.0.1:0", "--parent-watch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            proc.stdin.write("x" * 64 + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            host, port = pool._parse_tls_bridge_address(line)
            cli = socket.create_connection((host, port), timeout=5)
            try:
                req = (
                    "CONNECT www.workbuddy.cn:443 HTTP/1.1\r\n"
                    "Host: www.workbuddy.cn:443\r\n"
                    "X-WB-Bridge-Auth: " + "x" * 64 + "\r\n"
                    "X-WB-Upstream-Proxy: http://127.0.0.1:%d\r\n\r\n"
                    % proxy_port
                )
                cli.sendall(req.encode("ascii"))
                cli.recv(1024)
            except OSError:
                pass
            finally:
                cli.close()
            thread.join(5)
        finally:
            pool._stop_tls_bridge_process(proc)

        if "error" in captured:
            raise captured["error"]
        raw = captured["raw"]
        fields = parse_client_hello(raw)
        ja3 = "%d,%s,%s,%s,%s" % (
            fields["legacy_version"],
            "-".join(str(x) for x in fields["ciphers"]),
            "-".join(str(x) for x in fields["extensions"]),
            "-".join(str(x) for x in fields["groups"]),
            "-".join(str(x) for x in fields["ecpf"]),
        )
        ja3_md5 = hashlib.md5(ja3.encode("ascii")).hexdigest()
        b_hash = hashlib.sha256(
            ",".join("%04x" % c for c in sorted(fields["ciphers"])).encode("ascii")
        ).hexdigest()[:12]
        c_exts = ",".join(
            "%04x" % e for e in sorted(e for e in fields["extensions"] if e not in (0, 16))
        )
        c_sigs = ",".join("%04x" % s for s in sorted(fields["sigalgs"]))
        c_hash = hashlib.sha256(
            ("%s_%s" % (c_exts, c_sigs)).encode("ascii")
        ).hexdigest()[:12]
        ja4 = "t13d%02x%02x00_%s_%s" % (
            len(fields["extensions"]),
            len(fields["ciphers"]),
            b_hash,
            c_hash,
        )
        self.assertEqual(ja3, OFFICIAL_JA3)
        self.assertEqual(ja3_md5, OFFICIAL_JA3_MD5)
        self.assertEqual(ja4, OFFICIAL_JA4)
        self.assertEqual(fields["session_id_length"], 32)
        self.assertEqual(len(raw), 255)


if __name__ == "__main__":
    unittest.main(verbosity=2)
