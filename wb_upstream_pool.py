# -*- coding: utf-8 -*-
"""上游连接复用池：按 (scheme, host, port, 代理串, 凭据摘要) 复用 http.client 的
keep-alive 连接。

池键的最后一维是请求自带的账号凭据摘要（Authorization / X-Refresh-Token /
X-User-Id 的 sha256 前 16 位）：同一个出口上的两个账号绝不共用一条 TCP/TLS
连接。上游在一条连接上先后看到两个 Authorization 就是一条跨账号关联线索，
项目口径是「每账号一台设备」（见 wb_fingerprint.py），连接层不能例外。同一
账号的 chat / 计费 / 任务请求仍落在同一个键上，复用收益不变；没有凭据的请求
（登录前、匿名探活）仍共用一个键，与改动前一致。

为什么要自建池：CPython 的 urllib.request.AbstractHTTPHandler.do_open 会写死
headers["Connection"] = "close"（它担心 addinfourl 读不干净持久连接），所以在
urllib 这条路上连接**不可能**复用——每个出站请求都要重新走一次 TCP+TLS 握手。
真机（OpenWrt / Celeron N2840）裸握手实测：TCP connect 55.5ms、TCP+TLS 118.0ms
（4 次取最小），是请求路径上最大的一笔开销，比全部本地 CPU 项之和还大；多轮对话
每轮都要付一遍。这里绕开 do_open，直接拿 http.client 的连接来复用，其余语义
（代理 CONNECT、Proxy-Authorization、Host / Content-Length / Accept-Encoding 头、
HTTPError / URLError 的映射、重定向跟随）逐条与 urllib 对齐，差别只有
Connection: keep-alive 这一处，见下面各函数的注释。

什么时候**不**走池（抛 PoolBypass，由 wb_accounts.urlopen 回退到 urllib 原路径，
行为与改动前完全一致）：
  - URL scheme 不是 http(s)；
  - 代理串不是 HTTP 代理（socks5:// 等，urllib 的 ProxyHandler 本来也不支持）；
  - 上游回了 3xx：重定向跟随（含相对 Location、换主机、POST 降级 GET）是
    urllib 一整条 HTTPRedirectHandler 的职责，不在这里重写，交回原路径处理。

总开关：WB_UPSTREAM_KEEPALIVE=0 关闭（默认开）。
容量/回收：WB_UPSTREAM_POOL_SIZE（默认每键 2 条）、WB_UPSTREAM_POOL_IDLE（空闲
秒数，默认 90）。超龄回收在每次取用/归还时对**所有**池键各扫一遍（见
_KeepAlivePool._sweep_locked）：只回收被碰到过的那个键的话，一个之后再没人用过
的键（比如某个代理出口的账号被禁用）会把连接一直留到进程退出。
"""
import atexit
import base64
import hashlib
import http.client
import io
import os
import platform
import secrets
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

#: 关闭开关认这些值；其余（含未设置）都视为开启。
_KEEPALIVE_OFF = ("0", "off", "false", "no")

#: 错误响应体的读取上限。上游的错误体都是几百字节的小 JSON，调用方最多读 600 字节；
#: 这个上限只是防止一个异常大的 body 把内存吃掉。超限的部分不读、连接直接丢弃。
_ERROR_BODY_MAX = 1024 * 1024

OFFICIAL_TLS13_CIPHERS = (
    "TLS_AES_128_GCM_SHA256:"
    "TLS_AES_256_GCM_SHA384:"
    "TLS_CHACHA20_POLY1305_SHA256"
)
OFFICIAL_TLS12_CIPHERS = (
    "ECDHE-RSA-AES128-GCM-SHA256:"
    "ECDHE-ECDSA-AES128-GCM-SHA256:"
    "ECDHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-AES256-GCM-SHA384:"
    "ECDHE-RSA-AES128-SHA256:"
    "ECDHE-ECDSA-CHACHA20-POLY1305:"
    "ECDHE-RSA-CHACHA20-POLY1305:"
    "ECDHE-ECDSA-AES128-SHA:"
    "ECDHE-RSA-AES128-SHA:"
    "ECDHE-ECDSA-AES256-SHA:"
    "ECDHE-RSA-AES256-SHA:"
    "AES128-GCM-SHA256:"
    "AES256-GCM-SHA384:"
    "AES128-SHA:"
    "AES256-SHA"
)
OFFICIAL_TLS_GROUPS = "X25519:P-256:P-384"
OFFICIAL_TLS_SIGALGS = (
    "ECDSA+SHA256:RSA-PSS+SHA256:RSA+SHA256:"
    "ECDSA+SHA384:RSA-PSS+SHA384:RSA+SHA384:"
    "RSA-PSS+SHA512:RSA+SHA512:RSA+SHA1"
)

_TLS_FINGERPRINT_OFF = ("0", "off", "false", "no", "python", "native", "stdlib")
_TLS_BRIDGE_OFF = ("0", "off", "false", "no", "none")
_TLS_BRIDGE_START_TIMEOUT = 5.0
_TLS_BRIDGE_START_WAIT_TIMEOUT = _TLS_BRIDGE_START_TIMEOUT * 3 + 2.0
_TLS_BRIDGE_CACHE = None
_TLS_BRIDGE_PROCESS = None
_TLS_BRIDGE_SECRET = ""
_TLS_BRIDGE_LOCK = threading.RLock()
_TLS_BRIDGE_START_EVENT = threading.Event()
_TLS_BRIDGE_START_EVENT.set()

# Process-local TLS routing state. The bridge is attempted once per configuration
# and process lifetime: a failed start (or a bridge that exits later) makes all
# subsequent auto requests use Python TLS until the gateway is restarted.
_TLS_STATE_CONFIG_KEY = None
_TLS_AUTO_ATTEMPTED = False
_TLS_BRIDGE_DISABLED_REASON = ""
_TLS_BRIDGE_STATE = "not_started"
_TLS_LAST_ROUTE = ""
_TLS_LAST_ROUTE_REASON = ""
_TLS_LAST_FALLBACK_REASON = ""
_TLS_LAST_FALLBACK_AT = ""
_TLS_BRIDGE_GENERATION = 0
_TLS_LOG = None


def set_tls_logger(logger):
    """Install the gateway's redacting logger without creating an import cycle."""
    global _TLS_LOG
    _TLS_LOG = logger


def _tls_log(message, level="INFO"):
    logger = _TLS_LOG
    if logger is None:
        return
    try:
        logger(message, level=level, tag="tls")
    except Exception:
        try:
            logger(message)
        except Exception:
            pass


def tls_fingerprint_mode():
    """Return ``auto`` (default), ``node`` (bridge required) or ``python``."""
    raw = os.environ.get("WB_TLS_FINGERPRINT", "auto").strip().lower()
    if not raw:
        return "auto"
    if raw in _TLS_FINGERPRINT_OFF:
        return "python"
    if raw in ("auto", "node"):
        return raw
    raise ValueError("WB_TLS_FINGERPRINT must be auto, python or node")


def _bridge_setting():
    return os.environ.get("WB_TLS_BRIDGE", "").strip()


def _bridge_explicitly_off():
    return _bridge_setting().lower() in _TLS_BRIDGE_OFF


def _bridge_secret_setting():
    return os.environ.get("WB_TLS_BRIDGE_SECRET", "")


def _tls_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _tls_state_config_key():
    """Everything that makes a previous bridge decision inapplicable."""
    secret = _bridge_secret_setting()
    try:
        ident, _build = _resolve_ssl_factory()
    except Exception:
        ident = None
    try:
        candidates = tuple(_tls_bridge_candidates())
    except Exception:
        candidates = ()
    return (
        tls_fingerprint_mode(),
        _bridge_setting().lower(),
        hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else "",
        os.environ.get("WB_CA_BUNDLE", ""),
        os.environ.get("SSL_CERT_FILE", ""),
        platform.system().lower(),
        platform.machine().lower(),
        ident,
        id(_PooledHTTPSConnection),
        candidates,
    )


def _reset_tls_state_locked():
    global _TLS_AUTO_ATTEMPTED, _TLS_BRIDGE_CACHE, _TLS_BRIDGE_DISABLED_REASON
    global _TLS_BRIDGE_GENERATION, _TLS_BRIDGE_PROCESS, _TLS_BRIDGE_SECRET
    global _TLS_BRIDGE_STATE, _TLS_LAST_FALLBACK_AT, _TLS_LAST_FALLBACK_REASON
    global _TLS_LAST_ROUTE, _TLS_LAST_ROUTE_REASON
    if _TLS_BRIDGE_PROCESS is not None:
        _stop_tls_bridge_process(_TLS_BRIDGE_PROCESS)
    _TLS_BRIDGE_PROCESS = None
    _TLS_BRIDGE_CACHE = None
    _TLS_BRIDGE_SECRET = ""
    _TLS_AUTO_ATTEMPTED = False
    _TLS_BRIDGE_DISABLED_REASON = ""
    _TLS_LAST_ROUTE = ""
    _TLS_LAST_ROUTE_REASON = ""
    _TLS_LAST_FALLBACK_REASON = ""
    _TLS_LAST_FALLBACK_AT = ""
    _TLS_BRIDGE_GENERATION += 1
    _TLS_BRIDGE_STATE = "not_started"
    _TLS_BRIDGE_START_EVENT.set()


def _sync_tls_state():
    """Invalidate cached decisions when the TLS environment changes."""
    global _TLS_STATE_CONFIG_KEY, _TLS_BRIDGE_STATE
    key = _tls_state_config_key()
    with _TLS_BRIDGE_LOCK:
        if key != _TLS_STATE_CONFIG_KEY:
            _reset_tls_state_locked()
            _TLS_STATE_CONFIG_KEY = key
            mode = key[0]
            if mode == "python" or _bridge_explicitly_off():
                _TLS_BRIDGE_STATE = "off"
            elif _bridge_setting().lower() not in ("", "auto"):
                _TLS_BRIDGE_STATE = "external"
    return key


def _remember_route(route, reason, fallback=False):
    global _TLS_LAST_ROUTE, _TLS_LAST_ROUTE_REASON
    global _TLS_LAST_FALLBACK_AT, _TLS_LAST_FALLBACK_REASON
    with _TLS_BRIDGE_LOCK:
        _TLS_LAST_ROUTE = route
        _TLS_LAST_ROUTE_REASON = reason
        if fallback:
            _TLS_LAST_FALLBACK_REASON = reason
            _TLS_LAST_FALLBACK_AT = _tls_now()


def _mark_bridge_unavailable(reason):
    """Invalidate this bridge generation and close all of its idle sockets."""
    global _TLS_AUTO_ATTEMPTED, _TLS_BRIDGE_CACHE, _TLS_BRIDGE_DISABLED_REASON
    global _TLS_BRIDGE_GENERATION, _TLS_BRIDGE_PROCESS, _TLS_BRIDGE_SECRET
    global _TLS_BRIDGE_STATE, _TLS_LAST_FALLBACK_AT, _TLS_LAST_FALLBACK_REASON
    with _TLS_BRIDGE_LOCK:
        if (_TLS_BRIDGE_STATE == "disabled"
                and _TLS_BRIDGE_DISABLED_REASON == reason):
            _TLS_BRIDGE_START_EVENT.set()
            return _TLS_BRIDGE_GENERATION
        old_generation = _TLS_BRIDGE_GENERATION
        if _TLS_BRIDGE_PROCESS is not None:
            _stop_tls_bridge_process(_TLS_BRIDGE_PROCESS)
        _TLS_BRIDGE_PROCESS = None
        _TLS_BRIDGE_CACHE = None
        _TLS_BRIDGE_SECRET = ""
        _TLS_AUTO_ATTEMPTED = True
        _TLS_BRIDGE_DISABLED_REASON = reason
        _TLS_LAST_FALLBACK_REASON = reason
        _TLS_LAST_FALLBACK_AT = _tls_now()
        _TLS_BRIDGE_STATE = "disabled"
        _TLS_BRIDGE_GENERATION += 1
        _TLS_BRIDGE_START_EVENT.set()
    _drop_stale_bridge_connections()
    _tls_log(
        "TLS: bridge unavailable (%s); later auto-mode requests will use "
        "Python/OpenSSL" % reason,
        "WARN")
    return old_generation


def _refresh_bridge_process_locked():
    """Mark a started bridge unavailable when its child process has exited."""
    global _TLS_BRIDGE_CACHE, _TLS_BRIDGE_DISABLED_REASON
    global _TLS_BRIDGE_GENERATION, _TLS_BRIDGE_PROCESS, _TLS_BRIDGE_SECRET
    global _TLS_BRIDGE_STATE, _TLS_LAST_FALLBACK_AT, _TLS_LAST_FALLBACK_REASON
    if _TLS_BRIDGE_PROCESS is None:
        return False
    try:
        exited = _TLS_BRIDGE_PROCESS.poll() is not None
    except Exception:
        exited = True
    if not exited:
        return False
    _stop_tls_bridge_process(_TLS_BRIDGE_PROCESS)
    _TLS_BRIDGE_PROCESS = None
    _TLS_BRIDGE_CACHE = None
    _TLS_BRIDGE_SECRET = ""
    _TLS_BRIDGE_DISABLED_REASON = "runtime_exited"
    _TLS_LAST_FALLBACK_REASON = "runtime_exited"
    _TLS_LAST_FALLBACK_AT = _tls_now()
    _TLS_BRIDGE_STATE = "disabled"
    _TLS_BRIDGE_GENERATION += 1
    _tls_log(
        "TLS: bridge process exited; later auto-mode requests use "
        "Python/OpenSSL", "WARN")
    return True


def _is_bridge_transport(transport):
    return (isinstance(transport, tuple) and len(transport) >= 2
            and transport[0] == "bridge")


def _transport_is_current(transport):
    if not _is_bridge_transport(transport):
        return True
    with _TLS_BRIDGE_LOCK:
        return (transport[1] == _TLS_BRIDGE_GENERATION
                and _TLS_BRIDGE_STATE != "disabled")


def _drop_stale_bridge_connections():
    with _TLS_BRIDGE_LOCK:
        generation = _TLS_BRIDGE_GENERATION
        disabled = _TLS_BRIDGE_STATE == "disabled"
    _POOL.close_where(
        lambda key: (_is_bridge_transport(key[-1])
                     and (disabled
                          or key[-1][1] != generation)))


class TlsBridgeUnavailable(RuntimeError):
    def __init__(self, reason, message=None):
        self.reason = reason
        RuntimeError.__init__(self, message or (
            "TLS bridge unavailable: %s" % reason))


class TlsBridgeConnectError(Exception):
    """The local bridge CONNECT failed before any upstream HTTP was sent."""
    def __init__(self, reason="bridge_connect_failed", bridge_unavailable=True):
        self.reason = reason
        self.bridge_unavailable = bool(bridge_unavailable)
        Exception.__init__(self, "TLS bridge connect failed: %s" % reason)



def _apply_node_tls_profile(ctx):
    """Keep the supported stdlib TLS defaults when no bridge is active."""
    return ctx


def _parse_tls_bridge_address(raw):
    """Parse host:port, http://host:port, or LISTEN host:port."""
    text = str(raw or "").strip()
    if text.lower().startswith("listen "):
        text = text[7:].strip()
    try:
        if "://" in text:
            parts = urllib.parse.urlsplit(text)
            if parts.scheme.lower() != "http":
                return ""
        else:
            parts = urllib.parse.urlsplit("//" + text)
        if not parts.hostname or parts.port is None:
            return ""
        return (parts.hostname, int(parts.port))
    except (TypeError, ValueError):
        return ""


def _tls_bridge_arch():
    machine = (platform.machine() or "").strip().lower()
    return {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86": "386",
        "i386": "386",
        "i686": "386",
        "armv7l": "arm",
    }.get(machine, machine or "amd64")


def _tls_bridge_candidates():
    root = os.path.dirname(os.path.abspath(__file__))
    system = (platform.system() or "").strip().lower()
    if not system:
        system = "windows" if os.name == "nt" else "linux"
    suffix = ".exe" if os.name == "nt" else ""
    return (
        os.path.join(root, "bin", "wb-tls-bridge-%s-%s%s" % (
            system, _tls_bridge_arch(), suffix)),
        os.path.join(root, "bin", "wb-tls-bridge" + suffix),
        os.path.join(root, "tools", "wb-tls-bridge", "wb-tls-bridge" + suffix),
    )


def _stop_tls_bridge_process(proc):
    try:
        proc.kill()
    except Exception:
        pass
    for stream in (getattr(proc, "stdin", None), getattr(proc, "stdout", None)):
        try:
            stream.close()
        except Exception:
            pass
    try:
        proc.wait(timeout=3)
    except Exception:
        pass


def _shutdown_tls_bridge():
    global _TLS_BRIDGE_PROCESS, _TLS_BRIDGE_CACHE, _TLS_BRIDGE_SECRET
    global _TLS_BRIDGE_STATE
    with _TLS_BRIDGE_LOCK:
        if _TLS_BRIDGE_PROCESS is not None:
            _stop_tls_bridge_process(_TLS_BRIDGE_PROCESS)
        _TLS_BRIDGE_PROCESS = None
        _TLS_BRIDGE_CACHE = None
        _TLS_BRIDGE_SECRET = ""
        if _TLS_BRIDGE_STATE in ("ready", "external"):
            _TLS_BRIDGE_STATE = "off"


atexit.register(_shutdown_tls_bridge)


def _spawn_auto_tls_bridge(bin_path):
    """Spawn one bridge child and return (process, secret, address)."""
    proc = subprocess.Popen(
        [bin_path, "--listen", "127.0.0.1:0", "--parent-watch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    try:
        secret = secrets.token_hex(32)
        proc.stdin.write(secret + "\n")
        proc.stdin.flush()
        result = []
        reader = threading.Thread(
            target=lambda: result.append(proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(_TLS_BRIDGE_START_TIMEOUT)
        if reader.is_alive():
            raise TlsBridgeUnavailable("start_timeout")
        line = result[0] if result else ""
        address = _parse_tls_bridge_address(line)
        if not address:
            if proc.poll() is not None:
                raise TlsBridgeUnavailable("start_exited")
            raise TlsBridgeUnavailable("start_failed")
        return proc, secret, address
    except Exception:
        _stop_tls_bridge_process(proc)
        raise


def _ensure_auto_tls_bridge(bin_path):
    """Compatibility wrapper: start or return the cached bundled bridge."""
    global _TLS_BRIDGE_CACHE, _TLS_BRIDGE_PROCESS, _TLS_BRIDGE_SECRET
    global _TLS_AUTO_ATTEMPTED, _TLS_BRIDGE_GENERATION, _TLS_BRIDGE_STATE
    with _TLS_BRIDGE_LOCK:
        if _TLS_BRIDGE_CACHE and _TLS_BRIDGE_PROCESS is not None:
            if _TLS_BRIDGE_PROCESS.poll() is None:
                return _TLS_BRIDGE_CACHE
            _refresh_bridge_process_locked()
            raise TlsBridgeUnavailable(
                _TLS_BRIDGE_DISABLED_REASON or "runtime_exited")
        if _TLS_BRIDGE_STATE == "disabled":
            raise TlsBridgeUnavailable(
                _TLS_BRIDGE_DISABLED_REASON or "unavailable")
        if _TLS_AUTO_ATTEMPTED:
            raise TlsBridgeUnavailable("start_in_progress")
        _TLS_AUTO_ATTEMPTED = True
        _TLS_BRIDGE_STATE = "starting"
        _TLS_BRIDGE_START_EVENT.clear()
        proc, secret, address = _spawn_auto_tls_bridge(bin_path)
        _TLS_BRIDGE_PROCESS = proc
        _TLS_BRIDGE_SECRET = secret
        _TLS_BRIDGE_CACHE = address
        _TLS_BRIDGE_GENERATION += 1
        _TLS_BRIDGE_STATE = "ready"
        _TLS_BRIDGE_START_EVENT.set()
        return address


def _bridge_binary_candidates():
    return [
        path for path in _tls_bridge_candidates()
        if os.path.isfile(path) and os.access(path, os.X_OK)
    ]


def _start_bundled_tls_bridge(mode):
    """Start the first usable bundled bridge; never called for external mode."""
    global _TLS_AUTO_ATTEMPTED, _TLS_BRIDGE_CACHE, _TLS_BRIDGE_PROCESS
    global _TLS_BRIDGE_SECRET, _TLS_BRIDGE_GENERATION, _TLS_BRIDGE_STATE
    ident, _build = _resolve_ssl_factory()
    if ident not in _INITIAL_SSL_FACTORIES:
        raise TlsBridgeUnavailable("unsupported_ssl_factory")
    if _PooledHTTPSConnection is not _INITIAL_POOLED_HTTPS_CONN:
        raise TlsBridgeUnavailable("unsupported_connection_class")
    with _TLS_BRIDGE_LOCK:
        if _TLS_BRIDGE_CACHE and _TLS_BRIDGE_PROCESS is not None:
            if _TLS_BRIDGE_PROCESS.poll() is None:
                return _TLS_BRIDGE_CACHE
            _refresh_bridge_process_locked()
            raise TlsBridgeUnavailable(
                _TLS_BRIDGE_DISABLED_REASON or "runtime_exited")
        if _TLS_BRIDGE_STATE == "disabled":
            raise TlsBridgeUnavailable(
                _TLS_BRIDGE_DISABLED_REASON or "unavailable")
        if _TLS_AUTO_ATTEMPTED:
            raise TlsBridgeUnavailable("start_in_progress")
        _TLS_AUTO_ATTEMPTED = True
        _TLS_BRIDGE_STATE = "starting"
        _TLS_BRIDGE_START_EVENT.clear()

    candidates = _bridge_binary_candidates()
    if not candidates:
        raise TlsBridgeUnavailable("binary_missing")
    last = None
    for path in candidates:
        try:
            proc, secret, address = _spawn_auto_tls_bridge(path)
        except TlsBridgeUnavailable as exc:
            last = exc
            continue
        except Exception as exc:
            last = exc
            continue
        with _TLS_BRIDGE_LOCK:
            _TLS_BRIDGE_PROCESS = proc
            _TLS_BRIDGE_SECRET = secret
            _TLS_BRIDGE_CACHE = address
            _TLS_BRIDGE_GENERATION += 1
            _TLS_BRIDGE_STATE = "ready"
            _TLS_BRIDGE_START_EVENT.set()
        _tls_log("TLS: Go/uTLS bridge ready (generation %d)" %
                 _TLS_BRIDGE_GENERATION)
        return address
    if isinstance(last, TlsBridgeUnavailable):
        raise last
    raise TlsBridgeUnavailable("start_failed") from last


def _external_tls_bridge():
    raw = _bridge_setting()
    if not raw or raw.lower() == "auto" or raw.lower() in _TLS_BRIDGE_OFF:
        return None
    address = _parse_tls_bridge_address(raw)
    if not address or address[0] not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("TLS bridge must use a loopback address")
    secret = _bridge_secret_setting()
    if not secret:
        raise ValueError(
            "WB_TLS_BRIDGE_SECRET is required for an external bridge")
    return address, secret


def _tls_route_decision(mode, reason, bridge_address=None,
                        bridge_secret="", external=False):
    return {
        "mode": mode,
        "route": "bridge" if bridge_address else "python",
        "reason": reason,
        "bridge_address": bridge_address,
        "bridge_secret": bridge_secret,
        "external": bool(external),
        "generation": _TLS_BRIDGE_GENERATION,
        "transport": (
            ("bridge", _TLS_BRIDGE_GENERATION,
             bridge_address or (),
             hashlib.sha256(bridge_secret.encode("utf-8")).hexdigest()
             if bridge_secret else "",
             mode)
            if bridge_address else
            ("python", mode)
        ),
    }


def _python_route(mode, reason, fallback=False):
    decision = _tls_route_decision(mode, reason)
    _remember_route("python", reason, fallback=fallback)
    return decision


def _bridge_route(mode, address, secret, external=False):
    decision = _tls_route_decision(
        mode, "external" if external else "ready",
        bridge_address=address, bridge_secret=secret, external=external)
    _remember_route("bridge", decision["reason"])
    return decision


def _fallback_after_bridge_connect(decision, exc, scheme, host, proxy_info):
    """Choose a safe route after the bridge failed before request bytes."""
    if decision.get("mode") != "auto":
        raise TlsBridgeUnavailable(
            getattr(exc, "reason", "connect_failed"),
            "TLS bridge CONNECT failed before the request was sent: %s"
            % getattr(exc, "reason", "bridge_connect_failed")) from exc
    if not getattr(exc, "bridge_unavailable", True):
        # Upstream/proxy/TLS failures happen behind a healthy local bridge.
        # Keep the bridge available for other destinations and only fall back
        # this not-yet-sent request to Python.
        return _python_route(
            decision.get("mode") or "auto",
            getattr(exc, "reason", "upstream_failure"),
            fallback=True)
    reason = ("external_unavailable" if decision.get("external")
              else getattr(exc, "reason", "connect_failed"))
    _mark_bridge_unavailable(reason)
    next_route = _resolve_tls_route(scheme, host, proxy_info)
    if next_route["route"] == "bridge":
        raise TlsBridgeUnavailable(
            reason,
            "TLS bridge failed and a second bridge route was selected")
    return next_route


def _resolve_tls_route(scheme, host, proxy_info=None):
    """Resolve one new upstream connection to ``bridge`` or ``python``."""
    global _TLS_BRIDGE_STATE
    mode = tls_fingerprint_mode()
    if mode == "python":
        return _python_route(mode, "explicit_python")
    if _bridge_explicitly_off():
        return _python_route(mode, "explicit_bridge_off")
    if not enabled():
        if mode == "node":
            raise TlsBridgeUnavailable(
                "keepalive_disabled",
                "TLS bridge requires WB_UPSTREAM_KEEPALIVE to be enabled")
        return _python_route(mode, "keepalive_disabled", fallback=True)
    if scheme != "https" or str(host).lower() in (
            "127.0.0.1", "localhost", "::1"):
        return _python_route(
            mode, "non_https" if scheme != "https" else "loopback_target")
    ca_bundle = ca_bundle_path()
    if ca_bundle:
        if mode == "node":
            raise TlsBridgeUnavailable(
                "custom_ca",
                "TLS bridge does not support WB_CA_BUNDLE; "
                "use Python TLS for custom CAs")
        return _python_route(mode, "custom_ca", fallback=True)

    _sync_tls_state()
    try:
        external = _external_tls_bridge()
    except ValueError as exc:
        if mode == "auto":
            _mark_bridge_unavailable("external_invalid")
            return _python_route(
                mode, "external_invalid", fallback=True)
        raise exc
    if external is not None:
        address, secret = external
        with _TLS_BRIDGE_LOCK:
            if _TLS_BRIDGE_STATE == "disabled":
                return _python_route(
                    mode, _TLS_BRIDGE_DISABLED_REASON or "unavailable",
                    fallback=mode == "auto")
            _TLS_BRIDGE_STATE = "external"
        return _bridge_route(mode, address, secret, external=True)

    if mode == "node":
        deadline = time.monotonic() + _TLS_BRIDGE_START_WAIT_TIMEOUT
        while True:
            try:
                address = _start_bundled_tls_bridge(mode)
                break
            except TlsBridgeUnavailable as exc:
                if exc.reason != "start_in_progress":
                    _mark_bridge_unavailable(exc.reason)
                    raise
                with _TLS_BRIDGE_LOCK:
                    state = _TLS_BRIDGE_STATE
                    event = _TLS_BRIDGE_START_EVENT
                if state != "starting":
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TlsBridgeUnavailable(
                        "start_in_progress",
                        "TLS bridge startup is still in progress") from None
                event.wait(remaining)
        return _bridge_route(mode, address, _TLS_BRIDGE_SECRET)

    with _TLS_BRIDGE_LOCK:
        if _TLS_BRIDGE_STATE == "disabled":
            return _python_route(
                mode, _TLS_BRIDGE_DISABLED_REASON or "unavailable",
                fallback=True)
        if _TLS_BRIDGE_CACHE and _TLS_BRIDGE_PROCESS is not None:
            if _TLS_BRIDGE_PROCESS.poll() is None:
                return _bridge_route(
                    mode, _TLS_BRIDGE_CACHE, _TLS_BRIDGE_SECRET)
            _refresh_bridge_process_locked()
            return _python_route(
                mode, _TLS_BRIDGE_DISABLED_REASON or "runtime_exited",
                fallback=True)
        if _TLS_AUTO_ATTEMPTED:
            return _python_route(
                mode, "start_in_progress", fallback=False)
    try:
        address = _start_bundled_tls_bridge(mode)
    except TlsBridgeUnavailable as exc:
        if exc.reason == "start_in_progress":
            return _python_route(mode, exc.reason)
        _mark_bridge_unavailable(exc.reason)
        return _python_route(mode, exc.reason, fallback=True)
    return _bridge_route(mode, address, _TLS_BRIDGE_SECRET)


def tls_bridge_addr():
    """Return the optional local Go/uTLS bridge address as (host, port).

    Kept as a diagnostic/compatibility helper. Normal requests resolve one
    route through ``_resolve_tls_route`` so the pool key and the connection use
    the same bridge generation.
    """
    mode = tls_fingerprint_mode()
    if mode == "python" or _bridge_explicitly_off():
        return ""
    decision = _resolve_tls_route("https", "www.workbuddy.ai")
    if decision["route"] == "bridge":
        return decision["bridge_address"]
    if mode == "node":
        raise TlsBridgeUnavailable(decision["reason"])
    return ""


def tls_bridge_required():
    """True only for explicit ``node`` with the bridge not explicitly off."""
    return tls_fingerprint_mode() == "node" and not _bridge_explicitly_off()


def record_python_fallback(reason):
    """Record a Python fallback selected outside the connection pool."""
    _remember_route("python", reason, fallback=True)


def tls_status():
    """Public, secret-free TLS routing status for health/settings/UI."""
    _sync_tls_state()
    refreshed = False
    with _TLS_BRIDGE_LOCK:
        refreshed = _refresh_bridge_process_locked()
    if refreshed:
        _drop_stale_bridge_connections()
    with _TLS_BRIDGE_LOCK:
        mode = tls_fingerprint_mode()
        setting = _bridge_setting().lower()
        if mode == "python" or setting in _TLS_BRIDGE_OFF:
            bridge_state = "off"
        elif _TLS_BRIDGE_STATE == "disabled":
            bridge_state = "disabled"
        elif _TLS_BRIDGE_STATE == "starting":
            bridge_state = "starting"
        elif _TLS_BRIDGE_PROCESS is not None:
            try:
                bridge_state = (
                    "ready" if _TLS_BRIDGE_PROCESS.poll() is None
                    else "disabled")
            except Exception:
                bridge_state = "disabled"
        elif setting and setting != "auto":
            bridge_state = "external"
        elif _TLS_AUTO_ATTEMPTED:
            bridge_state = "disabled"
        else:
            bridge_state = "not_started"
        return {
            "requested_mode": mode,
            "bridge_state": bridge_state,
            "last_route": _TLS_LAST_ROUTE,
            "last_route_reason": _TLS_LAST_ROUTE_REASON,
            "last_fallback_reason": _TLS_LAST_FALLBACK_REASON,
            "last_fallback_at": _TLS_LAST_FALLBACK_AT,
            "auto_retry_available": (
                mode == "auto" and not _TLS_AUTO_ATTEMPTED
                and setting in ("", "auto")),
            "bridge_generation": _TLS_BRIDGE_GENERATION,
            "keepalive_enabled": enabled(),
        }


def reset_tls_state():
    """Reset process-local routing state (tests and explicit diagnostics)."""
    with _TLS_BRIDGE_LOCK:
        _reset_tls_state_locked()
        _TLS_STATE_CONFIG_KEY = None


def enabled():
    """连接池是否启用（默认启用，WB_UPSTREAM_KEEPALIVE=0 关闭）。

    每次调用都读环境变量而不是导入时读一次：现场排障改 env 重启即生效，测试也
    不用 reload 模块。热路径上就是一次 dict 查询，可以忽略。
    """
    raw = os.environ.get("WB_UPSTREAM_KEEPALIVE")
    if raw is None:
        return True
    return str(raw).strip().lower() not in _KEEPALIVE_OFF


def _pool_size():
    """每个池键最多保留的空闲连接数（默认 2）。

    2 条足以覆盖「一条在流式读、一条刚还回来」的常见重叠；再多只会让 OpenWrt
    这种小内存设备为僵尸连接多留 socket 与内核缓冲，收益很小。
    """
    try:
        return max(1, int(os.environ.get("WB_UPSTREAM_POOL_SIZE") or "2"))
    except (TypeError, ValueError):
        return 2


def _idle_ttl():
    """空闲连接的最长保留秒数（默认 90）。

    上游 CDN/网关的空闲超时通常在 60~90 秒；超过它的连接就算留着，下一次复用
    也大概率撞上探活失败。90 秒能覆盖多轮对话的常见间隔（用户两次提问之间），
    再久的连接不如让它在取用时被关掉。
    """
    try:
        return max(5.0, float(os.environ.get("WB_UPSTREAM_POOL_IDLE") or "90"))
    except (TypeError, ValueError):
        return 90.0


_SSL_CONTEXT = None
_SSL_CONTEXT_KEY = None
_SSL_CONTEXT_LOCK = threading.Lock()
_INITIAL_SSL_FACTORIES = (
    getattr(http.client, "_create_https_context", None),
    getattr(ssl, "_create_default_https_context", None),
    ssl.create_default_context,
)


def ca_bundle_path():
    """Optional PEM bundle for a private or self-signed TLS chain.

    WB_CA_BUNDLE wins over SSL_CERT_FILE. Both are read on every call, so a
    file that appears after startup is picked up without a restart. A path that
    was explicitly set but does not exist is a configuration error, not a
    silent fallback: otherwise every request keeps failing with the same
    opaque certificate message and the user never learns which file is wrong.
    """
    for name in ("WB_CA_BUNDLE", "SSL_CERT_FILE"):
        raw = os.environ.get(name)
        if not raw or not str(raw).strip():
            continue
        path = os.path.abspath(os.path.expanduser(str(raw).strip()))
        if os.path.isfile(path):
            return path
        if name == "WB_CA_BUNDLE":
            raise RuntimeError("%s points to a missing file: %s" % (name, path))
        # OpenSSL silently ignores a missing SSL_CERT_FILE; keep that behaviour
        # instead of turning an OS-level leftover into a startup failure.
    return ""


# The pooled path builds its own context, but urllib is still used for
# non-HTTP proxies and redirects. Exporting the bundle through the variable
# OpenSSL already understands keeps those fallbacks on the same trust set.
if os.environ.get("WB_CA_BUNDLE"):
    _ca = ca_bundle_path()
    if _ca:
        os.environ["SSL_CERT_FILE"] = _ca


def _resolve_ssl_factory():
    """(标识, 构造器)：http.client 在 context=None 时实际会走的那条路。

    3.13 起是 http.client._create_https_context(check_hostname)，之前直接是
    ssl._create_default_https_context()。取这条接缝而不是直接调
    ssl.create_default_context()，是为了让「谁都能换掉默认 context」继续有效
    ——测试就是靠它把自签证书塞进 https 主路径的。

    标识是底层那个函数对象本身：它变了就说明默认 context 被换过，缓存要作废。
    """
    factory = getattr(http.client, "_create_https_context", None)
    if factory is not None:
        # check_hostname=None 与 HTTPSConnection 自己的取法一致（不覆盖
        # _create_default_https_context 给出的默认值）。
        return factory, (lambda: factory(None))
    factory = getattr(ssl, "_create_default_https_context", None)
    if factory is not None:
        return factory, factory
    return ssl.create_default_context, ssl.create_default_context


def shared_ssl_context():
    """进程级共享的出站 TLS context（懒建，只建一次）。

    http.client 在 context=None 时**每建一条连接就调一次**
    ssl._create_default_https_context()。真机实测 create_default_context()
    单次 20.4ms（要解析 121 张 CA 证书），而 45 个账号各走自己的代理就是 45 个
    池键，连接来回过期重连时这笔开销按次付。更要紧的是 OpenSSL 的客户端会话
    缓存挂在 SSLContext 上：每换一个新 context 就等于放弃 TLS session
    resumption，每次握手都退化成完整握手。

    一份进程级 context 同时省掉这两笔。校验设置完全由 stdlib 那条工厂决定
    （默认 check_hostname=True、verify_mode=CERT_REQUIRED、走系统 CA 库），
    **不放松任何校验**；工厂被换掉时缓存自动作废，所以那条接缝仍然有效。

    懒建而不是导入时建：测试与只跑面板的进程根本不出站，不该为它们白付 20ms。
    """
    global _SSL_CONTEXT, _SSL_CONTEXT_KEY
    ident, build = _resolve_ssl_factory()
    key = (ident, ca_bundle_path(), tls_fingerprint_mode())
    if _SSL_CONTEXT is None or _SSL_CONTEXT_KEY != key:
        with _SSL_CONTEXT_LOCK:
            ident, build = _resolve_ssl_factory()
            key = (ident, ca_bundle_path(), tls_fingerprint_mode())
            if _SSL_CONTEXT is None or _SSL_CONTEXT_KEY != key:
                if key[1]:
                    # Keep the system roots and add the private bundle on top:
                    # a gateway that talks to both a normal CDN and a local
                    # TLS-inspecting proxy must not lose either trust set.
                    _SSL_CONTEXT = ssl.create_default_context()
                    _SSL_CONTEXT.load_verify_locations(cafile=key[1])
                else:
                    _SSL_CONTEXT = build()
                if key[2] == "node" and ident in _INITIAL_SSL_FACTORIES:
                    _apply_node_tls_profile(_SSL_CONTEXT)
                _SSL_CONTEXT_KEY = key
    return _SSL_CONTEXT


class PoolBypass(Exception):
    """这个请求不走连接池，调用方回退到 urllib 原路径。

    只用于「还没发出去」的情形（URL/代理不受支持）和「收到 3xx 需要跟随」的情形。
    调用方（wb_accounts.urlopen）捕获后原样走原来的 opener，行为与改动前一致。
    """
    def __init__(self, message="", reason="unsupported"):
        Exception.__init__(self, message)
        self.reason = reason


class _StaleConnection(Exception):
    """复用连接被对端关掉导致的失败：还没读到任何响应字节，可以安全重试一次。

    original 是原始异常（重试也失败时上抛它，保持错误形状不变）。
    """

    def __init__(self, original):
        Exception.__init__(self, str(original))
        self.original = original


class _TransportFailure(Exception):
    """传输阶段失败：连接状态不可信，调用方负责丢弃；original 是要上抛的异常。"""

    def __init__(self, original):
        Exception.__init__(self, str(original))
        self.original = original


def _stale_connection_error(exc):
    """这条失败是不是「对端已经把关掉的连接交还给我们」的典型形状。

    只认连接级错误：写失败（EPIPE/ECONNRESET）或状态行都没读到就 EOF
    （RemoteDisconnected，ConnectionResetError 的子类）。超时不算——那是对端
    还活着但没按时回答，重试语义由上层原有的逻辑决定，不在这里插手。
    """
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    if isinstance(exc, ssl.SSLEOFError):
        return True
    # 部分 OpenSSL 把「未收 close_notify 就 EOF」报成普通 SSLError，
    # 消息里带 UNEXPECTED_EOF；形状与 SSLEOFError 相同，按同类处理。
    if isinstance(exc, ssl.SSLError) and "UNEXPECTED_EOF" in str(exc).upper():
        return True
    return False


def _connection_looks_alive(conn):
    """空闲连接是否还干净、可用。

    探活是必需的：空闲连接被 CDN/网关按自己的空闲超时单方面关掉是常态，而 TCP
    要到下一次写才可能发现（写进本地缓冲会“成功”，读响应时才看到 RST）。把
    「发现」提前到发送之前，请求就不会被发上一条已死的连接，也就轮不到重试。

    判据：把 socket 切成非阻塞试读一个字节——
      - BlockingIOError / SSLWantReadError：对端什么都没发，干净的空闲连接；
      - 读到 b""（FIN / close_notify）、抛连接错误、或读到数据（协议已污染）：
        都不能复用，判死。读到的那一个字节反正属于要丢弃的连接，无所谓。
    不用 select：交付运行时是精简过的 CPython，import 面越小越好；这里只需要
    「有数据没有」这一个事实，非阻塞试读就是最短路径。TLS 缓冲里还压着没读走的
    数据同样不干净，先查 pending()。
    """
    sock = getattr(conn, "sock", None)
    if sock is None:
        return False
    pending = getattr(sock, "pending", None)
    if pending is not None:
        try:
            if pending() > 0:
                return False
        except Exception:
            return False
    try:
        sock.settimeout(0)
        sock.recv(1)
    except (BlockingIOError, ssl.SSLWantReadError):
        return True
    except Exception:
        return False
    return False


class _PooledResponse(http.client.HTTPResponse):
    """带「用完归还」语义的 HTTPResponse。

    HTTPResponse 的自然结束——Content-Length 读满、chunked 读到终块、HEAD 无 body——
    都会走 _close_conn()。我们在那里把连接还给池：这个时点比 close() 更早也更可靠，
    调用方只要把 body 读完（`with resp:`、`for line in resp:`、resp.read()），即使
    忘了显式 close，连接也会归还，不会白白漏掉。中途 close()（客户端中断、
    协议错误）走同一条路，但因为 body 没读干净，判定会拒绝复用、直接关掉。

    归还与否只认「读没读到自然结尾」这一个事实，绝不凭「isclosed()」——
    close() 也会把 fp 置空，但那不代表 body 读干净了。
    """
    _pool = None          # 以下三个由 _exchange() 在 getresponse() 之后挂上
    _pool_key = None
    _pool_conn = None
    _released = False
    _chunked_done = False

    def _read_and_discard_trailer(self):
        # http.client 读到 0 长度终块后会先读 trailer、紧接着调 _close_conn()；
        # 在 _close_conn() 之前把「chunked 到自然结尾」这个事实记下来，
        # _release() 才有据可依。trailer 读失败（异常）时不置位——那时连接已经
        # 脏了，宁可丢弃。（不能挂在 _get_chunk_left 的返回处：它内部先调
        # _close_conn() 再返回 None，等到返回时已经晚了。）
        http.client.HTTPResponse._read_and_discard_trailer(self)
        self._chunked_done = True

    def _body_complete(self):
        """body 是否读到了自然结尾。"""
        if not self.isclosed():
            return False
        if self.chunked:
            return self._chunked_done
        # 非 chunked：length 只有恰好读到 0 才会归零；中途 close() 时仍 > 0。
        return self.length == 0

    def _release(self):
        """决定这条连接的归宿：还回池，或关掉。幂等，可多次调用。"""
        if self._released:
            return
        self._released = True
        pool, key, conn = self._pool, self._pool_key, self._pool_conn
        if pool is None or conn is None:
            return
        if (_transport_is_current(key[-1])
                and self._body_complete() and not self.will_close
                and conn.sock is not None):
            pool.release(key, conn)
        else:
            # Connection: close（will_close）、半截 body、连接已断——一律丢弃：
            # 复用一条脏连接会让下一条请求读到上一条的残留 body。
            pool.discard(conn)

    def _close_conn(self):
        http.client.HTTPResponse._close_conn(self)
        self._release()

    def close(self):
        http.client.HTTPResponse.close(self)
        self._release()


class _PooledHTTPConnection(http.client.HTTPConnection):
    response_class = _PooledResponse


class _PooledHTTPSConnection(http.client.HTTPSConnection):
    response_class = _PooledResponse
    _wb_proxy_url = ""
    _wb_tls_route = None

    def connect(self):
        target_host = self._tunnel_host or self.host
        target_port = (self._tunnel_port or 443) if self._tunnel_host else self.port
        route = self._wb_tls_route
        if (route and route.get("route") == "bridge"
                and target_host not in ("127.0.0.1", "localhost", "::1")):
            self._connect_via_tls_bridge(route, target_host, target_port)
            return
        http.client.HTTPSConnection.connect(self)

    def _connect_via_tls_bridge(self, route, target_host, target_port):
        bridge_host, bridge_port = route["bridge_address"]
        sock = None
        try:
            try:
                sock = socket.create_connection(
                    (bridge_host, bridge_port), self.timeout)
            except Exception as exc:
                raise TlsBridgeConnectError(
                    "bridge_local_unavailable", True) from exc
            target_authority = "[%s]:%d" % (target_host, int(target_port)) if ":" in target_host else "%s:%d" % (target_host, int(target_port))
            if "\r" in target_authority or "\n" in target_authority:
                raise TlsBridgeConnectError("invalid_connect_target", False)
            lines = ["CONNECT %s HTTP/1.1" % target_authority,
                     "Host: %s" % target_authority]
            secret = route.get("bridge_secret") or ""
            if not secret or "\r" in secret or "\n" in secret:
                raise TlsBridgeConnectError(
                    "bridge_auth_unavailable", True)
            lines.append("X-WB-Bridge-Auth: " + secret)
            if self._wb_proxy_url:
                if "\r" in self._wb_proxy_url or "\n" in self._wb_proxy_url:
                    raise TlsBridgeConnectError(
                        "upstream_proxy_invalid", False)
                lines.append("X-WB-Upstream-Proxy: %s" % self._wb_proxy_url)
            lines.append("")
            lines.append("")
            try:
                sock.sendall("\r\n".join(lines).encode("latin-1"))
            except Exception as exc:
                raise TlsBridgeConnectError(
                    "bridge_local_io", True) from exc
            buf = b""
            while b"\r\n\r\n" not in buf:
                try:
                    chunk = sock.recv(1)
                except Exception as exc:
                    raise TlsBridgeConnectError(
                        "bridge_local_io", True) from exc
                if not chunk:
                    raise TlsBridgeConnectError("bridge_local_io", True)
                buf += chunk
                if len(buf) > 16384:
                    raise TlsBridgeConnectError(
                        "bridge_local_protocol_error", True)
            status_line = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            parts = status_line.split(None, 2)
            code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
            if code != 200:
                if code == 502:
                    # The bridge reached its listener, but the upstream TCP,
                    # proxy CONNECT or TLS handshake failed. Certificates stay
                    # verified in Go; this is not evidence that the bridge
                    # process itself is broken.
                    raise TlsBridgeConnectError(
                        "upstream_tls_or_connect_failed", False)
                if code in (401, 403):
                    raise TlsBridgeConnectError("bridge_auth_failed", True)
                if code == 400:
                    raise TlsBridgeConnectError(
                        "invalid_connect_target", False)
                if code == 405:
                    raise TlsBridgeConnectError(
                        "bridge_protocol_error", True)
                raise TlsBridgeConnectError("bridge_rejected", True)
        except TlsBridgeConnectError:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            raise
        except Exception:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            raise TlsBridgeConnectError("bridge_local_io", True)
        self.sock = sock


_INITIAL_POOLED_HTTPS_CONN = _PooledHTTPSConnection


class _KeepAlivePool(object):
    """按键存放空闲连接。锁只保护 _idle 字典；一条连接同一时刻只属于一个使用者。"""

    def __init__(self):
        self._lock = threading.Lock()
        # key -> [(conn, 最近一次归还的 monotonic 时刻), ...]，头部最旧、尾部最新。
        self._idle = {}
        # 诊断用计数（测试与真机验证都读它）：created 是真正新建的连接数，
        # reused 是命中池的次数——「每请求 SYN 消失」在进程内的直接证据。
        self.stats = {"created": 0, "reused": 0, "evicted": 0,
                      "dead": 0, "discarded": 0}

    def acquire(self, key, timeout, factory):
        """取一条可用连接：池里的活连接（返回 reused=True），或 factory() 新建。"""
        conn = None
        now = time.monotonic()
        ttl = _idle_ttl()
        with self._lock:
            self._sweep_locked(now)
            entries = self._idle.get(key)
            while entries:
                cand, ts = entries.pop()      # LIFO：最近用过的优先复用
                if now - ts <= ttl:
                    conn = cand
                    break
                # 空闲超龄：上游大概率已经按自己的空闲超时关掉了它，直接关。
                # （全局扫描刚跑过，这条只在同一次取用里被 release 又塞回来时
                # 才可能命中；留着重在防御，成本是一次比较。）
                self.stats["evicted"] += 1
                self._close(cand)
            if entries is not None and not entries:
                self._idle.pop(key, None)
        if conn is not None:
            if not _connection_looks_alive(conn):
                self.stats["dead"] += 1
                self._close(conn)
                conn = None
            else:
                # 复用前把读超时重设回本次请求的超时：上一位使用者可能是流式请求，
                # _apply_stream_idle_timeout 把 socket 超时改成了 idle 值（默认
                # 300s）；不重置的话这次请求连响应头也会按 300s 等，线程被拖住。
                try:
                    conn.sock.settimeout(timeout)
                except Exception:
                    self._close(conn)
                    conn = None
        if conn is None:
            conn = factory()
            self.stats["created"] += 1
            return conn, False
        self.stats["reused"] += 1
        return conn, True

    def release(self, key, conn):
        """把读完的连接放回池；已满就关掉最旧的一条腾位置。"""
        cap = _pool_size()
        with self._lock:
            self._sweep_locked(time.monotonic())
            entries = self._idle.setdefault(key, [])
            while len(entries) >= cap:
                oldest, _ts = entries.pop(0)
                self.stats["evicted"] += 1
                self._close(oldest)
            entries.append((conn, time.monotonic()))

    def _sweep_locked(self, now):
        """把所有池键里超龄的空闲连接关掉（调用方必须持有 self._lock）。

        每次取用/归还都扫一遍全部键，而不是只扫本次碰到的那个：否则一条连接
        所属的键之后再没人用过时（账号被禁用、代理被换掉），它会一直占着
        socket 直到进程退出。池键数就是「目标 × 代理出口」的组合数，量级是
        个位数，全扫一遍是几次 dict 遍历，热路径上可以忽略。
        """
        ttl = _idle_ttl()
        for key in list(self._idle):
            alive = []
            for conn, ts in self._idle[key]:
                if now - ts <= ttl:
                    alive.append((conn, ts))
                else:
                    self.stats["evicted"] += 1
                    self._close(conn)
            if alive:
                self._idle[key] = alive
            else:
                self._idle.pop(key, None)

    def discard(self, conn):
        """这条连接不可信（或对端要关）：从池的角度彻底放弃它。"""
        self.stats["discarded"] += 1
        self._close(conn)

    def note_created(self):
        """记一次「新建连接」。acquire() 的未命中路径已经记过；走 factory() 直接
        新建的重试路径要显式调用，否则「每请求一次 SYN」的计数会漏掉重试那一条。"""
        with self._lock:
            self.stats["created"] += 1

    def _close(self, conn):
        try:
            conn.close()
        except Exception:
            pass

    # ---- 测试与诊断 ----

    def idle_counts(self):
        """每个键当前的空闲连接数（测试断言容量与回收用）。"""
        with self._lock:
            return {k: len(v) for k, v in self._idle.items() if v}

    def close_all(self):
        """关掉所有空闲连接（测试清理用）。"""
        with self._lock:
            conns = [c for entries in self._idle.values() for c, _ in entries]
            self._idle.clear()
        for conn in conns:
            self._close(conn)

    def close_where(self, predicate):
        """Close idle connections whose pool key matches ``predicate``."""
        with self._lock:
            keys = [key for key in self._idle if predicate(key)]
            conns = [
                conn
                for key in keys
                for conn, _ts in self._idle.pop(key, [])
            ]
        for conn in conns:
            self._close(conn)


_POOL = _KeepAlivePool()


def stats():
    """池的累计计数快照。"""
    return dict(_POOL.stats)


def idle_counts():
    """每个池键的空闲连接数快照。"""
    return _POOL.idle_counts()


def close_all():
    """关掉池里所有空闲连接。"""
    _POOL.close_all()


# ---------------------------------------------------------------------------
# 请求编排：把 urllib.Request 翻译成 http.client 的一次交换
# ---------------------------------------------------------------------------

def _target_parts(url):
    """URL -> (scheme, host, port, selector)。selector 与 urllib 的 req.selector 一致。"""
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https") or not parts.hostname:
            raise PoolBypass(
                "unsupported url for the keep-alive pool: %r" % (url,),
                "unsupported_url")
    port = parts.port or (443 if scheme == "https" else 80)
    selector = parts.path or "/"
    if parts.query:
        selector += "?" + parts.query
    return scheme, parts.hostname, port, selector


def _split_proxy(proxy):
    """拆分代理串 -> dict(scheme, user, password, host, port)，非 HTTP 代理返回 None。

    语义与 urllib.request._parse_proxy 对齐（支持 http://user:pass@host:port 与
    authority 形式 host:port），但只认 http 代理：socks5:// 之类的串返回 None，
    调用方回退到 urllib 原路径（那条路本来也不支持它们），不在这里发明新行为。
    """
    raw = str(proxy or "").strip()
    if not raw:
        return None
    if "://" in raw:
        scheme, _, rest = raw.partition("://")
        if scheme.strip().lower() != "http":
            return None
        authority = rest.split("/", 1)[0]
    else:
        authority = raw                      # authority 形式：host:port
    user = password = None
    if "@" in authority:
        userinfo, _, authority = authority.rpartition("@")
        user, _, password = userinfo.partition(":")
        user = urllib.parse.unquote(user)
        password = urllib.parse.unquote(password) if password else password
    hostport = urllib.parse.unquote(authority)
    if hostport.startswith("["):             # IPv6: [::1]:8080 / [::1]
        host, sep, rest = hostport[1:].partition("]")
        port = rest.lstrip(":") if sep else ""
    elif ":" in hostport:
        host, _, port = hostport.rpartition(":")
    else:
        host, port = hostport, ""
    if not host:
        return None
    if port and not port.isdigit():
        # 非数字端口：urllib 的 HTTPConnection 会抛 InvalidURL。这里当作「池处理
        # 不了」，回退原路径，错误形状与改动前完全一致。
        return None
    return {"scheme": "http", "user": user or "", "password": password or "",
            "host": host, "port": port}


def _proxy_auth_header(proxy_info):
    """HTTP 代理的 Basic 认证头值（没有凭据返回空串）。

    与 urllib ProxyHandler.proxy_open 一致：user 与 password 都非空才发认证头，
    且 userinfo 先做 URL 解码。
    """
    if not proxy_info or not proxy_info.get("user") or not proxy_info.get("password"):
        return ""
    creds = "%s:%s" % (proxy_info["user"], proxy_info["password"])
    return "Basic " + base64.b64encode(creds.encode()).decode("ascii")


def _proxy_url(proxy_info):
    """Restore an HTTP proxy URL, including percent-encoded credentials."""
    if not proxy_info:
        return ""
    host = str(proxy_info.get("host") or "")
    if not host:
        return ""
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host
    port = str(proxy_info.get("port") or "")
    authority = host + (":" + port if port else "")
    user = str(proxy_info.get("user") or "")
    password = str(proxy_info.get("password") or "")
    if user or password:
        authority = "%s:%s@%s" % (
            urllib.parse.quote(user, safe=""),
            urllib.parse.quote(password, safe=""),
            authority,
        )
    return "http://" + authority


def _proxy_bypassed(host):
    """目标主机是否在系统代理旁路清单里（判定与 urllib ProxyHandler 完全一致）。

    urllib 的 proxy_open 在套用代理前会先问 proxy_bypass(host)：Windows 看注册表
    的 ProxyOverride（默认常含 <local> / 127.*），Linux 看 no_proxy 环境变量。
    池这边必须做同样的判断，否则同一份配置下「改动前直连、改动后走代理」——
    出站路径变了，而这不是本 PR 要改的东西。旁路命中时按直连处理，连接也归到
    直连那个池键，与不配代理的账号共用。
    """
    try:
        return bool(urllib.request.proxy_bypass(host))
    except Exception:
        return False


def _new_connection(scheme, host, port, proxy_info, timeout, route=None):
    """新建一条连接对象（不连接，惰性连接交给 http.client 自己）。

    - 直连：HTTPS 目标用 HTTPSConnection（context 走进程级共享的
      shared_ssl_context()，校验设置与 http.client 的默认 _create_https_context
      逐项一致，只是不再每次建连重建一份）；
    - 经 HTTP 代理：连的是代理的地址；HTTPS 目标走 CONNECT 隧道（set_tunnel），
      HTTP 目标直接把绝对 URL 当请求目标发给代理。
    """
    if proxy_info:
        # urllib 的默认端口由连接类决定（HTTPSConnection=443 / HTTPConnection=80），
        # 代理串没写端口时保持一致。
        proxy_port = int(proxy_info["port"]) if proxy_info["port"] else (
            443 if scheme == "https" else 80)
        if scheme == "https":
            context = (None if route and route.get("route") == "bridge"
                       else shared_ssl_context())
            conn = _PooledHTTPSConnection(proxy_info["host"], proxy_port, timeout=timeout,
                                          context=context)
            conn._wb_proxy_url = _proxy_url(proxy_info)
            conn._wb_tls_route = route
            tunnel_headers = {}
            auth = _proxy_auth_header(proxy_info)
            if auth:
                # Proxy-Authorization 只给代理看，绝不能流到源站（urllib do_open 同款处理）
                tunnel_headers["Proxy-Authorization"] = auth
            conn.set_tunnel(host, port, headers=tunnel_headers)
            return conn
        return _PooledHTTPConnection(proxy_info["host"], proxy_port, timeout=timeout)
    if scheme == "https":
        context = (None if route and route.get("route") == "bridge"
                   else shared_ssl_context())
        conn = _PooledHTTPSConnection(host, port, timeout=timeout,
                                      context=context)
        conn._wb_tls_route = route
        return conn
    return _PooledHTTPConnection(host, port, timeout=timeout)


def _host_header_value(url):
    """Host 头的值：URL 里的 netloc（含显式端口），与 urllib do_request_ 的取值一致。"""
    netloc = urllib.parse.urlsplit(url).netloc
    if "@" in netloc:
        netloc = netloc.rpartition("@")[2]
    return netloc


#: 池键里用来区分「同一出口上的不同账号/凭据」的请求头，按优先级取第一个有值的。
#: 取凭据的哈希而不是 uid：调用点不必改动，凡是带凭据的请求都自动隔离；只把
#: sha256 前 16 位放进键里，token 本身不进日志、也不在池里多存一份。
_IDENTITY_HEADER_FIELDS = ("authorization", "x-refresh-token", "x-user-id")


def _header_lookup(pairs):
    """把 (name, value) 序列收成「小写头名 -> 第一个值」的字典。"""
    lookup = {}
    for name, value in pairs:
        key = str(name).strip().lower()
        if key not in lookup and value:
            lookup[key] = value
    return lookup


def _identity_discriminator(req, ordered_headers=None):
    """这条请求属于哪个账号/凭据：池键的最后一维（没有凭据时返回空串）。

    两个账号落在同一个 (scheme, host, port, proxy) 上时，此前会复用同一条
    TCP/TLS 连接，上游能在一条连接上先后看到两个 Authorization / X-User-Id。
    把凭据摘要并进池键后各用各的连接；同一账号的不同请求仍共用一个键。
    """
    pairs = ordered_headers
    if pairs is None:
        pairs = list(req.headers.items())
    lookup = _header_lookup(pairs)
    for field in _IDENTITY_HEADER_FIELDS:
        value = lookup.get(field)
        if value:
            digest = hashlib.sha256(
                ("%s\0%s" % (field, value)).encode("utf-8")).hexdigest()[:16]
            return "%s:%s" % (field, digest)
    return ""


def _exact_header_pairs(headers):
    """把调用方给的有序头归一成 [(name, value), ...]；None 表示走默认路径。"""
    if headers is None:
        return None
    if hasattr(headers, "items"):
        return list(headers.items())
    return list(headers)


#: Canonical wire spelling for the official client header families. The legacy
#: default path intentionally still uses .title(); this table is available to
#: callers that opt into normalization without changing old request bytes.
_OFFICIAL_HEADER_SPELLING = {
    "accept": "Accept",
    "accept-encoding": "Accept-Encoding",
    "accept-language": "Accept-Language",
    "authorization": "Authorization",
    "b3": "b3",
    "connection": "Connection",
    "content-encoding": "Content-Encoding",
    "content-length": "Content-Length",
    "content-type": "Content-Type",
    "host": "Host",
    "origin": "Origin",
    "proxy-authorization": "Proxy-Authorization",
    "referer": "Referer",
    "traceparent": "traceparent",
    "user-agent": "User-Agent",
    "x-agent-intent": "X-Agent-Intent",
    "x-agent-purpose": "X-Agent-Purpose",
    "x-agent-runtime": "X-Agent-Runtime",
    "x-agent-type": "X-Agent-Type",
    "x-api-key": "X-API-Key",
    "x-auth-method": "X-Auth-Method",
    "x-b3-parentspanid": "X-B3-ParentSpanId",
    "x-b3-sampled": "X-B3-Sampled",
    "x-b3-spanid": "X-B3-SpanId",
    "x-b3-traceid": "X-B3-TraceId",
    "x-codebuddy-request": "X-CodeBuddy-Request",
    "x-conversation-id": "X-Conversation-ID",
    "x-conversation-message-id": "X-Conversation-Message-ID",
    "x-conversation-request-id": "X-Conversation-Request-ID",
    "x-device-token": "X-Device-Token",
    "x-domain": "X-Domain",
    "x-enterprise-id": "X-Enterprise-Id",
    "x-env-id": "X-Env-ID",
    "x-ide-name": "X-IDE-Name",
    "x-ide-type": "X-IDE-Type",
    "x-ide-version": "X-IDE-Version",
    "x-id-source": "X-Id-Source",
    "x-machine-id": "X-Machine-ID",
    "x-model-id": "X-Model-ID",
    "x-no-enterprise-id": "X-No-Enterprise-Id",
    "x-private-data": "X-Private-Data",
    "x-product": "X-Product",
    "x-product-version": "X-Product-Version",
    "x-refresh-token": "X-Refresh-Token",
    "x-request-id": "X-Request-ID",
    "x-request-trace-id": "X-Request-Trace-Id",
    "x-requested-with": "X-Requested-With",
    "x-root-request-id": "X-Root-Request-ID",
    "x-session-id": "X-Session-ID",
    "x-stainless-arch": "x-stainless-arch",
    "x-stainless-lang": "x-stainless-lang",
    "x-stainless-os": "x-stainless-os",
    "x-stainless-package-version": "x-stainless-package-version",
    "x-stainless-retry-count": "x-stainless-retry-count",
    "x-stainless-runtime": "x-stainless-runtime",
    "x-stainless-runtime-version": "x-stainless-runtime-version",
    "x-stainless-timeout": "x-stainless-timeout",
    "x-tenant-id": "X-Tenant-Id",
    "x-trace-id": "X-Trace-ID",
    "x-user-id": "X-User-Id",
    "x-userinfo": "X-Userinfo",
    "monitor_promptpreparestarttime": "monitor_promptPrepareStartTime",
    "monitor_httpsendtime": "monitor_httpSendTime",
}


def normalize_header_spelling(headers):
    """Return a dict with the official case for known WorkBuddy headers."""
    pairs = headers.items() if hasattr(headers, "items") else headers
    normalized = {}
    for name, value in pairs:
        original = str(name).strip()
        normalized[_OFFICIAL_HEADER_SPELLING.get(
            original.lower(), original)] = value
    return normalized


def _request_headers(req, scheme, proxy_info, ordered_headers=None):
    """拼这一条请求的线上头。

    默认路径（ordered_headers 为 None）按 urllib AbstractHTTPHandler.do_open 的
    规则拼：unredirected 侧先（自动 Content-type、Host、默认 User-agent——
    Content-Length 由 http.client 自动补在最前，恰好就是 urllib 里它的位置），
    再是调用方给的头，最后 Connection。头名统一 .title()，线上字节与改动前
    逐字节相同（除了 Connection 的值）。

    保真路径（ordered_headers 非 None）只给显式要求「头名原样」的调用点用
    （chat 路径，见 wb_proxy.open_upstream 与 wb_accounts._daily_chat_upstream）：
    调用方给的是有序 (name, value)，头名**不做 .title()**，http.client 按原样
    写到线上。为什么需要单独一条路：urllib.request.Request.add_header 会先把
    头名 capitalize（X-Request-ID → X-request-id），所以只从 Request.headers
    取值永远拿不回原名；保真调用点把有序头挂在 req.wb_exact_headers 上（见
    urlopen 的 headers 参数），池这边直接用。billing / 登录 / 签到等路径不传，
    行为完全不变。
    """
    headers = dict(req.unredirected_hdrs)
    data = req.data
    ordered_names = None
    if ordered_headers is not None:
        ordered_names = {str(name).strip().lower() for name, _value in ordered_headers}
    if data is not None:
        # do_request_ 的默认：带 body 而没有 Content-type 时补表单类型
        if not req.has_header("Content-type") and not (
                ordered_names is not None and "content-type" in ordered_names):
            headers["Content-type"] = "application/x-www-form-urlencoded"
        # Content-Length 不在这里补：http.client 的 _send_request 会按 body 自动补，
        # 位置与 urllib 的 unredirected 一侧相同，线上的字节完全一样。
    if not req.has_header("Host") and not (
            ordered_names is not None and "host" in ordered_names):
        headers["Host"] = _host_header_value(req.full_url)
    if not req.has_header("User-agent") and not (
            ordered_names is not None and "user-agent" in ordered_names):
        # 进程默认 opener 的兜底 UA；所有调用点都显式给了 UA，这里只保证不出现差异。
        headers["User-agent"] = "Python-urllib/%s" % urllib.request.__version__
    if ordered_headers is None:
        headers.update(req.headers)
    else:
        for name, value in ordered_headers:
            headers[name] = value
    if proxy_info and scheme == "http":
        # HTTP 目标经代理：认证头随请求发给代理（HTTPS 目标的认证头在 CONNECT 里）
        auth = _proxy_auth_header(proxy_info)
        if auth:
            headers["Proxy-authorization"] = auth
    headers["Connection"] = "keep-alive"
    if ordered_headers is not None:
        return headers
    return {name.title(): value for name, value in headers.items()}


def _exchange(conn, req, selector, scheme, proxy_info, key, reused,
              ordered_headers=None):
    """在 conn 上完成一次请求/响应交换，返回挂好归还语义的响应对象。

    失败时抛：
      - _StaleConnection：复用连接被对端关掉（还没读到任何响应字节，可重试一次）
      - _TransportFailure：其他传输失败，original 是要上抛给调用方的异常
      - 其他异常：连接不可信，调用方丢弃后原样上抛
    """
    method = req.get_method()
    headers = _request_headers(req, scheme, proxy_info, ordered_headers)
    try:
        conn.request(method, selector, req.data, headers,
                     encode_chunked=req.has_header("Transfer-encoding"))
    except OSError as err:
        # 与 urllib do_open 一致：发送阶段（含惰性 connect）的 OSError 包成 URLError
        if reused and _stale_connection_error(err):
            raise _StaleConnection(err) from None
        raise _TransportFailure(urllib.error.URLError(err)) from None
    try:
        resp = conn.getresponse()
    except ConnectionError as err:
        # RemoteDisconnected（状态行都没读到）等；http.client 已自行关掉连接
        if reused and _stale_connection_error(err):
            raise _StaleConnection(err) from None
        raise _TransportFailure(err) from None
    # 到这一步连接的归宿归响应对象管：读完归还、中途关闭丢弃
    resp._pool = _POOL
    resp._pool_key = key
    resp._pool_conn = conn
    resp.url = req.get_full_url()
    resp.msg = resp.reason
    return resp


def _read_error_body(resp):
    """把错误响应体读完（有界），让连接可以安全复用；读失败就返回已拿到的部分。"""
    try:
        return resp.read(_ERROR_BODY_MAX + 1)[:_ERROR_BODY_MAX]
    except Exception:
        return b""


def _fold_bypassed(host, proxy_raw):
    """把「配了代理、但命中系统旁路清单」折叠成直连，返回 (proxy_info, proxy_raw)。

    非 HTTP 代理（socks 等）抛 PoolBypass —— 那条路不走池，交回 urllib。
    判定与 urllib ProxyHandler 完全一致（见 _proxy_bypassed）。
    """
    proxy_info = _split_proxy(proxy_raw)
    if proxy_raw and proxy_info is None:
        raise PoolBypass(
            "proxy %r is not an http proxy" % (proxy_raw,),
            "proxy_unsupported")
    if proxy_info and _proxy_bypassed(host):
        return None, ""
    return proxy_info, proxy_raw

def egress_key(url, proxy=""):
    """出站路径的稳定标识：同一物理出口的账号落在同一个键上。

    与连接池键（见 urlopen）同一口径——(scheme, host, port, 代理串)，且把
    「配了代理但命中系统旁路」折叠成直连。失败归账按这个键做，一组共享出口的
    账号就自动共用一个计数器，而不是各自挨一次熔断（issue #271）。
    非 HTTP 代理仍以原串入键：它虽然不走池，出口身份还是这个串。
    """
    scheme, host, port, _selector = _target_parts(url)
    proxy_raw = str(proxy or "").strip()
    try:
        _proxy_info, proxy_raw = _fold_bypassed(host, proxy_raw)
    except PoolBypass:
        pass
    return (scheme, host, port, proxy_raw)

def urlopen(req, timeout=30, proxy="", headers=None):
    """连接池版 urlopen。默认签名与 wb_accounts.urlopen 一致，多了 headers。

    headers：可选的有序 (name, value) 序列（或 dict）。给了就按原样发这些头名
    （不做 .title()），用于 chat 这类必须与官方客户端逐字节对齐的路径；不给时
    沿用 req.headers 与 .title() 的默认路径，billing / 登录 / 签到等行为不变。
    保真路径也可以由调用方把有序头挂在 req.wb_exact_headers 上（wb_proxy 的
    chat 路径就是这么做，省得改 wb_accounts.urlopen 的签名与所有测试替身）。

    池键 = (scheme, host, port, 代理串, 凭据摘要)：不同账号绝不共用同一条
    TCP/TLS 连接；同一账号仍按出口复用。凭据摘要由 _identity_discriminator
    从 Authorization / X-Refresh-Token / X-User-Id 现取（sha256 前 16 位）。

    失败语义与 urllib 对齐：HTTP >= 400 抛 HTTPError（body 已读好，挂在 BytesIO
    上，exc.read()/read(n)/fp 的行为不变）；发送阶段的 OSError 抛 URLError；
    其余 http.client 异常原样上抛。

    复用连接被对端关掉时重试一次（仅此一种失败、仅此一次，且用新连接重试）：
    安全边界是「还没发出请求体 / 还没读到任何响应字节」——发送阶段或状态行阶段
    的连接级失败，此时对端不可能已经开始处理一个完整请求，重试不会造成重复计费
    或重复工具调用；一旦读到响应（哪怕一个字节）或请求已完整发出后对端仍活着，
    失败就不再重试，交给上层原有的重试语义。
    """
    scheme, host, port, selector = _target_parts(req.full_url)
    proxy_raw = str(proxy or "").strip()
    # 系统旁路清单命中时按直连处理（键也回到直连的池）——与 urllib 一致。
    proxy_info, proxy_raw = _fold_bypassed(host, proxy_raw)
    if proxy_info and scheme == "http":
        # 经代理的 HTTP 请求：请求目标是绝对 URL（urllib set_proxy 同款）
        selector = req.full_url
    ordered_headers = _exact_header_pairs(
        headers if headers is not None else getattr(req, "wb_exact_headers", None))
    # 池键按（目标, 代理串, 凭据）分：不同出口的账号绝不共用一条连接，即使代理
    # 地址相同、只是串写法不同（宁可多一条连接，也不赌两个出口是同一个）；同一
    # 出口上的不同账号也绝不共用（凭据摘要不同），见模块 docstring。
    route = _resolve_tls_route(scheme, host, proxy_info)
    route_fallback_used = False

    def pool_key(decision):
        return (scheme, host, port, proxy_raw,
                _identity_discriminator(req, ordered_headers),
                decision["transport"])

    def make_connection(decision):
        conn = _new_connection(
            scheme, host, port, proxy_info, timeout, route=decision)
        if decision["route"] != "bridge":
            return conn
        # http.client connects lazily on the first request. For a bridge route
        # that is too late: a failed local CONNECT must be visible before any
        # request bytes can reach the upstream. Doing it in the factory keeps
        # the fallback boundary explicit and lets auto choose Python safely.
        try:
            conn.connect()
        except TlsBridgeConnectError:
            try:
                conn.close()
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                conn.close()
            except Exception:
                pass
            raise TlsBridgeConnectError("bridge_connect_failed") from exc
        return conn

    def acquire_for(decision):
        return _POOL.acquire(
            pool_key(decision), timeout,
            lambda: make_connection(decision))

    def bridge_failure(decision, exc):
        """Turn a pre-request bridge failure into a route or a clear error."""
        nonlocal route, route_fallback_used
        if route_fallback_used:
            raise TlsBridgeUnavailable(
                getattr(exc, "reason", "connect_failed"),
                "TLS bridge CONNECT failed after a Python fallback was "
                "already selected") from exc
        route_fallback_used = True
        return _fallback_after_bridge_connect(
            decision, exc, scheme, host, proxy_info)

    try:
        conn, reused = acquire_for(route)
    except TlsBridgeConnectError as exc:
        route = bridge_failure(route, exc)
        conn, reused = acquire_for(route)

    # 只对可重放的 body（bytes/None）做安全重试：file-like 的 body 重发会从当前位置
    # 续读，第二条请求会残缺。本仓库的调用点全是 bytes，这个判断只是兜底。
    replayable = req.data is None or isinstance(req.data, (bytes, bytearray, memoryview))
    retried = False
    while True:
        key = pool_key(route)
        try:
            resp = _exchange(conn, req, selector, scheme, proxy_info, key, reused,
                             ordered_headers)
        except _StaleConnection as stale:
            _POOL.discard(conn)
            if reused and replayable and not retried:
                # 探活漏掉的竞态：连接在探活之后、发送之前被对端关掉。换一条
                # 全新连接重试一次（不重试池里的第二条：它同样可能已死，而且
                # 再用池连接就把重试的安全前提破坏掉了）。
                retried = True
                reused = False
                try:
                    conn = make_connection(route)
                except TlsBridgeConnectError as exc:
                    route = bridge_failure(route, exc)
                    conn, reused = acquire_for(route)
                else:
                    _POOL.note_created()
                continue
            raise stale.original
        except _TransportFailure as failure:
            _POOL.discard(conn)
            raise failure.original
        except PoolBypass:
            # 连接已经由响应对象归还/关闭，这里不能再碰它
            raise
        except Exception:
            # 连接状态未知（协议错、超时……）：不归还，直接关掉
            _POOL.discard(conn)
            raise
        status = resp.status
        if 200 <= status < 300:
            return resp
        # 非 2xx：先把 body 读完（有界），连接的去向也随之定下（读完且对端
        # keep-alive 就归还，Connection: close 或半截就丢弃）
        body = _read_error_body(resp)
        resp.close()
        if 300 <= status < 400:
            # 重定向跟随（相对 Location、换主机、POST 降级 GET）交给 urllib 原路径，
            # 语义一字不改。代价是这一条请求会被发两次——3xx 在这条链路上不出现
            # （都是 JSON API），值得。
            raise PoolBypass(
                "upstream answered %d; replayed via urllib" % status,
                "redirect")
        raise urllib.error.HTTPError(req.full_url, status, resp.reason,
                                     resp.headers, io.BytesIO(body))
