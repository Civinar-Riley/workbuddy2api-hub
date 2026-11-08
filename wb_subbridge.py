#!/usr/bin/env python3
"""WorkBuddy subscription bridge v5.

Converts Clash/Mihomo subscriptions, V2Ray/SS/SSR/Trojan URI lists, or direct
http/socks5 proxy URLs into per-node mihomo HTTP listeners.

Key behaviour:
  --list             parse sources and print the node catalogue (no ports opened)
  --render           render config for the saved selection (selection.json)
  --render --nodes   render config for the given selection and persist it

Port allocation is sticky via portmap.json so an existing slot URL keeps
working across re-renders.
"""
import argparse
import base64
import hashlib
import json
import os
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request

yaml = None


def load_yaml():
    """Import PyYAML only when a YAML source is actually parsed or rendered."""
    global yaml
    if yaml is None:
        try:
            import yaml as module
        except Exception as exc:
            raise ValueError(
                "PyYAML is required for Clash subscriptions and config rendering: %s"
                % exc)
        yaml = module
    return yaml


BASE_DIR = os.environ.get("WB_SUBBRIDGE_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "subbridge-data")
SUBS_PATH = os.path.join(BASE_DIR, "subs.json")
SELECTION_PATH = os.path.join(BASE_DIR, "selection.json")
PORTMAP_PATH = os.path.join(BASE_DIR, "portmap.json")
LISTEN_HOST = (os.environ.get("WB_SUBBRIDGE_HOST")
               or os.environ.get("WB_SUBBRIDGE_LISTEN") or "172.17.0.1")
CONTROLLER = os.environ.get("WB_SUBBRIDGE_CONTROLLER_ADDR") or "172.17.0.1:19090"
SECRET = os.environ.get("WB_SUBBRIDGE_SECRET") or ""
PORT_START = int(os.environ.get("WB_SUBBRIDGE_PORT_START") or "17901")
PORT_COUNT = int(os.environ.get("WB_SUBBRIDGE_PORT_COUNT") or "64")
UA = "clash-verge/v1.7.7"

SKIP_NAMES = ("剩余流量", "距离下次重置", "套餐到期", "官网", "更新软件", "更新订阅", "流量")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def load_subs():
    data = load_json(SUBS_PATH, None)
    items = data.get("subs") if isinstance(data, dict) else data
    if isinstance(items, list):
        return [str(x).strip() for x in items if str(x).strip()]
    return []


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    context = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=30, context=context) as resp:
        return resp.read()


def looks_clash(raw):
    return "proxies:" in raw[:4096].decode("utf-8", "ignore")


def looks_base64(raw):
    text = raw.decode("utf-8", "ignore")
    if "://" in text[:2048]:
        return False
    compact = "".join(text.split())
    if not compact or len(compact) < 64:
        return False
    try:
        decoded = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=True)
    except Exception:
        return False
    return b"://" in decoded[:2048]


def decode_base64_body(raw):
    compact = "".join(raw.decode("utf-8", "ignore").split())
    return base64.b64decode(compact + "=" * (-len(compact) % 4))


def v2ray_to_proxy(uri):
    u = urllib.parse.urlparse(uri)
    scheme = (u.scheme or "").lower()
    q = dict(urllib.parse.parse_qsl(u.query))
    if scheme == "vmess":
        payload = json.loads(base64.b64decode(u.netloc + "=" * (-len(u.netloc) % 4)).decode("utf-8"))
        proxy = {"name": str(payload.get("ps") or "vmess"), "type": "vmess",
                 "server": str(payload.get("add") or ""), "port": int(payload.get("port") or 0),
                 "uuid": str(payload.get("id") or ""), "alterId": int(payload.get("aid") or 0),
                 "cipher": str(payload.get("scy") or "auto"), "tls": str(payload.get("tls") or "") == "tls"}
        net = str(payload.get("net") or "tcp")
        if net == "ws":
            proxy["network"] = "ws"
            proxy["ws-opts"] = {"path": str(payload.get("path") or "/")}
        if net == "grpc":
            proxy["network"] = "grpc"
            proxy["grpc-opts"] = {"grpc-service-name": str(payload.get("path") or "")}
        sni = str(payload.get("sni") or payload.get("host") or "")
        if sni:
            proxy["servername"] = sni
        return proxy
    if scheme == "vless":
        proxy = {"name": urllib.parse.unquote(u.fragment) or "vless", "type": "vless",
                 "server": u.hostname or "", "port": u.port or 0, "uuid": u.username or ""}
        net = q.get("type", "tcp")
        if net == "ws":
            proxy["network"] = "ws"
            proxy["ws-opts"] = {"path": urllib.parse.unquote(q.get("path", "/")),
                                "headers": {"Host": urllib.parse.unquote(q.get("host", ""))}}
        if net == "grpc":
            proxy["network"] = "grpc"
            proxy["grpc-opts"] = {"grpc-service-name": urllib.parse.unquote(q.get("serviceName", ""))}
        security = str(q.get("security") or "").lower()
        if security in ("tls", "reality"):
            proxy["tls"] = True
        if security == "reality" and q.get("pbk"):
            proxy["reality-opts"] = {"public-key": q["pbk"]}
            if q.get("sid"):
                proxy["reality-opts"]["short-id"] = q["sid"]
        if q.get("fp"):
            proxy["client-fingerprint"] = q["fp"]
        if q.get("flow"):
            proxy["flow"] = q["flow"]
        if q.get("sni"):
            proxy["servername"] = q["sni"]
        return proxy
    if scheme == "trojan":
        return {"name": urllib.parse.unquote(u.fragment) or "trojan", "type": "trojan",
                "server": u.hostname or "", "port": u.port or 0,
                "password": urllib.parse.unquote(u.username or "")}
    if scheme == "ss":
        userinfo = urllib.parse.unquote(u.username or "")
        if ":" in userinfo:
            cipher, password = userinfo.split(":", 1)
        else:
            padded = userinfo + "=" * (-len(userinfo) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", "ignore")
            cipher, password = decoded.split(":", 1)
        return {"name": urllib.parse.unquote(u.fragment) or "ss", "type": "ss",
                "server": u.hostname or "", "port": u.port or 0,
                "cipher": cipher, "password": password}
    if scheme == "ssr":
        main, _, tail = uri[6:].partition("/?")
        parts = main.split(":")
        if len(parts) < 6:
            return None
        host, port, proto, cipher, obfs, pwd_b64 = parts[:6]
        params = dict(urllib.parse.parse_qsl(tail))
        pad = lambda s: s + "=" * (-len(s) % 4)
        name = base64.b64decode(pad(params.get("remarks", ""))).decode("utf-8", "ignore") or "ssr"
        return {"name": name, "type": "ssr", "server": host, "port": int(port or 0),
                "cipher": cipher,
                "password": base64.b64decode(pad(pwd_b64)).decode("utf-8", "ignore"),
                "protocol": proto, "obfs": obfs,
                "protocol-param": base64.b64decode(pad(params.get("protoparam", ""))).decode("utf-8", "ignore"),
                "obfs-param": base64.b64decode(pad(params.get("obfsparam", ""))).decode("utf-8", "ignore")}
    return None


def v2rayn_json_to_proxies(raw):
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return []
    entries = data if isinstance(data, list) else [data]
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        config = entry
        if isinstance(entry.get("config"), str):
            try:
                config = json.loads(entry["config"])
            except Exception:
                continue
        for ob in (config.get("outbounds") or []):
            proto = ob.get("protocol")
            if proto not in ("vmess", "vless", "trojan", "shadowsocks"):
                continue
            settings = ob.get("settings") or {}
            servers = settings.get("vnext") or settings.get("servers") or []
            if not servers:
                continue
            server = servers[0]
            stream = ob.get("streamSettings") or {}
            net = stream.get("network", "tcp")
            proxy = {"name": str(entry.get("remarks") or server.get("address") or proto),
                     "type": "ss" if proto == "shadowsocks" else proto,
                     "server": str(server.get("address") or ""), "port": int(server.get("port") or 0)}
            users = server.get("users") or [{}]
            if proto == "vmess":
                proxy["uuid"] = str(users[0].get("id", ""))
                proxy["alterId"] = int(users[0].get("alterId", 0))
                proxy["cipher"] = str(users[0].get("security", "auto"))
            elif proto == "vless":
                proxy["uuid"] = str(users[0].get("id", ""))
            elif proto == "trojan":
                proxy["password"] = str(users[0].get("password", server.get("password", "")))
            else:
                proxy["cipher"] = str(server.get("method", "aes-256-gcm"))
                proxy["password"] = str(server.get("password", ""))
            if net == "ws":
                ws = (stream.get("wsSettings") or {})
                proxy["network"] = "ws"
                proxy["ws-opts"] = {"path": str(ws.get("path", "/")),
                                    "headers": {"Host": str((ws.get("headers") or {}).get("Host", ""))}}
            if net == "grpc":
                grpc = (stream.get("grpcSettings") or {})
                proxy["network"] = "grpc"
                proxy["grpc-opts"] = {"grpc-service-name": str(grpc.get("serviceName", ""))}
            if (stream.get("security") or "") == "tls":
                proxy["tls"] = True
                tls = stream.get("tlsSettings") or {}
                if tls.get("serverName"):
                    proxy["servername"] = str(tls["serverName"])
            out.append(proxy)
    return out


def is_direct_proxy_source(source):
    """Whether a source line is one proxy URL rather than a subscription."""
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme.lower() not in ("http", "https", "socks5", "socks5h"):
        return False
    if not parsed.hostname:
        return False
    path = (parsed.path or "").rstrip("/")
    query = parsed.query or ""
    # A bare proxy endpoint has no subscription-looking path or query. This
    # keeps http(s) subscription URLs on the download path.
    if parsed.scheme.lower() in ("http", "https"):
        return not path and not query
    return True


def direct_proxy_to_mihomo(url, name):
    """Translate one authenticated proxy URL into a mihomo proxies entry."""
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("http", "https"):
        ptype = "http"
    elif scheme in ("socks5", "socks5h"):
        ptype = "socks5"
    else:
        raise ValueError("unsupported direct proxy scheme: %s" % scheme)
    if not parsed.hostname:
        raise ValueError("direct proxy is missing a host")
    entry = {"name": name, "type": ptype, "server": parsed.hostname,
             "port": int(parsed.port or 0)}
    if not entry["port"]:
        raise ValueError("direct proxy is missing a port")
    if parsed.username is not None:
        entry["username"] = urllib.parse.unquote(parsed.username)
    if parsed.password is not None:
        entry["password"] = urllib.parse.unquote(parsed.password)
    if scheme == "https":
        entry["tls"] = True
    if scheme == "socks5h":
        entry["udp"] = False
    return entry


def collect_nodes():
    """Return (real_proxies, direct_urls); no port limiting here."""
    proxies = []
    direct = []
    for source in load_subs():
        if is_direct_proxy_source(source):
            if source not in direct:
                direct.append(source)
            continue
        try:
            raw = download(source)
        except Exception as exc:
            print("skip source %s: %s" % (source[:80], exc), file=sys.stderr)
            continue
        if looks_clash(raw):
            config = load_yaml().safe_load(raw)
            items = config.get("proxies") if isinstance(config, dict) else []
            items = items if isinstance(items, list) else []
            proxies.extend(p for p in items if isinstance(p, dict))
            continue
        if looks_base64(raw):
            raw = decode_base64_body(raw)
        text = raw.decode("utf-8", "ignore")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if any(line.lower().startswith(("vmess://", "vless://", "trojan://", "ss://", "ssr://")) for line in lines):
            for line in lines:
                proxy = v2ray_to_proxy(line)
                if proxy:
                    proxies.append(proxy)
            continue
        proxies.extend(v2rayn_json_to_proxies(raw))
    return proxies, direct


def is_real_node(proxy):
    name = str(proxy.get("name") or "")
    if any(mark in name for mark in SKIP_NAMES):
        return False
    server = str(proxy.get("server") or "").strip()
    if not server or server in ("127.0.0.1", "localhost"):
        return False
    return bool(proxy.get("type")) and int(proxy.get("port") or 0) > 0


def dedupe(proxies):
    out, seen = [], set()
    for p in proxies:
        if not isinstance(p, dict) or not is_real_node(p):
            continue
        key = (str(p.get("name") or ""), p.get("type"), p.get("server"), p.get("port"))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def selectable_nodes(proxies, direct):
    """Build stable unique selectors while keeping display names unchanged."""
    nodes = []
    for proxy in dedupe(proxies):
        identity = json.dumps(proxy, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        selector = "p:" + hashlib.sha256(identity).hexdigest()[:16]
        nodes.append({
            "id": selector,
            "name": str(proxy.get("name") or ""),
            "kind": "proxy",
            "proxy": proxy,
            "type": str(proxy.get("type") or ""),
            "server": str(proxy.get("server") or ""),
            "port": int(proxy.get("port") or 0),
        })
    seen_direct = set()
    for url in direct:
        if url in seen_direct:
            continue
        seen_direct.add(url)
        selector = "d:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        scheme = urllib.parse.urlparse(url).scheme.lower()
        nodes.append({
            "id": selector,
            "name": url,
            "kind": "direct",
            "url": url,
            "type": "direct-" + scheme,
            "server": "",
            "port": 0,
        })
    return nodes


def catalogue():
    proxies, direct = collect_nodes()
    return [
        {key: entry[key] for key in ("id", "name", "type", "server", "port")}
        for entry in selectable_nodes(proxies, direct)
    ]


def build_config(selection, proxies, direct):
    entries = selectable_nodes(proxies, direct)
    name_counts = {}
    for entry in entries:
        name_counts[entry["name"]] = name_counts.get(entry["name"], 0) + 1
    name_seq = {}

    def display_name(entry):
        name = entry["name"]
        if name_counts.get(name, 0) <= 1:
            return name
        name_seq[name] = name_seq.get(name, 0) + 1
        return "%s #%d" % (name, name_seq[name])

    by_id = {entry["id"]: entry for entry in entries}
    by_name = {}
    for entry in entries:
        by_name.setdefault(entry["name"], entry)
    portmap = load_json(PORTMAP_PATH, {})
    used = set(int(v) for v in portmap.values() if isinstance(v, int))
    scan = PORT_START
    def alloc(key):
        nonlocal scan
        if key in portmap:
            return int(portmap[key])
        while scan in used:
            scan += 1
        if scan >= PORT_START + PORT_COUNT:
            raise ValueError("subbridge port pool exhausted")
        port = scan
        used.add(port)
        portmap[key] = port
        return port
    listeners, chosen, selected_meta = [], [], []
    seen_selection = set()
    for key in selection:
        key = str(key)
        entry = by_id.get(key) or by_name.get(key)
        if entry is None:
            print("skip unknown node: %s" % key[:120], file=sys.stderr)
            continue
        selector = entry["id"]
        if selector in seen_selection:
            continue
        seen_selection.add(selector)
        if entry["kind"] == "proxy":
            port = alloc(selector)
            proxy = dict(entry["proxy"])
            proxy["name"] = selector
            selected_meta.append({"id": selector, "name": display_name(entry)})
            listeners.append({"name": "wb-slot-%d" % (port - PORT_START), "type": "http",
                              "port": port, "listen": LISTEN_HOST, "proxy": selector})
            chosen.append(proxy)
        else:
            url = entry["url"]
            port = alloc(selector)
            proxy = direct_proxy_to_mihomo(url, selector)
            selected_meta.append({"id": selector, "name": display_name(entry)})
            listeners.append({"name": "wb-direct-%d" % (port - PORT_START), "type": "http",
                              "port": port, "listen": LISTEN_HOST, "proxy": selector})
            chosen.append(proxy)
    portmap = {key: value for key, value in portmap.items() if key in seen_selection}
    save_json(PORTMAP_PATH, portmap)
    save_json(SELECTION_PATH, {"nodes": selection, "saved_at": time.time()})
    config = {"mode": "global", "log-level": "info", "ipv6": True}
    if CONTROLLER:
        config["external-controller"] = CONTROLLER
    if SECRET:
        config["secret"] = SECRET
    config["proxies"] = chosen
    config["listeners"] = listeners
    config["_wb_selected"] = selected_meta
    return config


def render(out_path, selection):
    proxies, direct = collect_nodes()
    if not proxies and not direct:
        raise ValueError("no usable sources: add a subscription or direct proxy first")
    if selection is None:
        saved = load_json(SELECTION_PATH, {})
        selection = saved.get("nodes") if isinstance(saved, dict) else None
    if not selection:
        raise ValueError("no nodes selected yet: pick nodes in the panel first")
    config = build_config(selection, proxies, direct)
    selected_meta = config.pop("_wb_selected", [])
    payload = load_yaml().safe_dump(config, allow_unicode=True, sort_keys=False)
    fd, tmp = tempfile.mkstemp(prefix=".config.yaml.", dir=os.path.dirname(out_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, out_path)
        # A systemd .path unit restarts mihomo when config.yaml changes; a
        # controller PUT cannot rebind listener ports.
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
    config["_wb_selected"] = selected_meta
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(BASE_DIR, "config.yaml"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--probe-controller", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--nodes", default=None, help="JSON array of node names / direct URLs")
    parser.add_argument("--set-subs", default=None, help="JSON array of source URLs")
    args = parser.parse_args()
    if args.set_subs is not None:
        try:
            value = json.loads(args.set_subs)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": "invalid subs json: %s" % exc}, ensure_ascii=False))
            raise SystemExit(0)
        if not isinstance(value, list):
            print(json.dumps({"ok": False, "error": "subs must be a JSON array"}, ensure_ascii=False))
            raise SystemExit(0)
        save_json(SUBS_PATH, {"subs": [str(x).strip() for x in value if str(x).strip()],
                              "saved_at": time.time()})
        if not (args.render or args.list):
            print(json.dumps({"ok": True, "saved": True, "count": len(value),
                              "subs": load_subs()}, ensure_ascii=False))
            return
    if args.probe_controller:
        host, _, port = CONTROLLER.partition(":")
        try:
            req = urllib.request.Request(
                "http://%s:%s/version" % (host, port),
                headers={"Authorization": "Bearer " + SECRET} if SECRET else {})
            with urllib.request.urlopen(req, timeout=5) as resp:
                info = json.loads(resp.read().decode("utf-8"))
            print(json.dumps({"ok": True, "version": info.get("version", ""),
                              "subs": load_subs()}, ensure_ascii=False))
            return
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)[:300]}, ensure_ascii=False))
            raise SystemExit(0)
    if args.list:
        try:
            nodes = catalogue()
            print(json.dumps({"ok": True, "nodes": nodes, "subs": load_subs()}, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)[:300]}, ensure_ascii=False))
        return
    if not args.render:
        parser.error("choose --list, --render, or --probe-controller")
    try:
        selection = None
        if args.nodes is not None:
            try:
                selection = json.loads(args.nodes)
            except Exception as exc:
                raise ValueError("invalid nodes json: %s" % exc)
            if not isinstance(selection, list):
                raise ValueError("nodes must be a JSON array")
            selection = [str(x).strip() for x in selection if str(x).strip()]
        config = render(args.out, selection)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:300]}, ensure_ascii=False))
        raise SystemExit(0)
    if args.json:
        selected_names = {
            item["id"]: item["name"] for item in config.get("_wb_selected", [])
        }
        listeners = [{
            "port": i["port"],
            "proxy": str(i.get("proxy", "")),
            "name": selected_names.get(str(i.get("proxy", "")), ""),
        } for i in config["listeners"]]
        print(json.dumps({"ok": True, "listeners": listeners, "subs": load_subs()}, ensure_ascii=False))
    else:
        print("rendered %d listeners" % len(config["listeners"]))


if __name__ == "__main__":
    main()
