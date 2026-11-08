# wb-tls-bridge

Optional local Go/uTLS bridge for the WorkBuddy upstream connection pool.

The bridge accepts a local HTTP `CONNECT host:port`, establishes the TCP route
(directly or through `X-WB-Upstream-Proxy`), performs the TLS handshake with a
Node/OpenSSL-compatible ClientHello, and then forwards plaintext HTTP/1.1 in
both directions. Python still owns HTTP request construction, keep-alive reuse,
and SSE reads. The bridge validates the upstream certificate.

The fingerprints, cipher order, extension order, groups, and signature
algorithms follow `internal/upstream/tlsfingerprint.go` from
[misakano7545/workbuddy2api-panel](https://github.com/misakano7545/workbuddy2api-panel).
The reference captures describe 18 ciphers, 10 extensions, no ALPN, and the
groups `X25519 / P-256 / P-384`.

## Build

Go 1.24 or newer is required.

```sh
cd tools/wb-tls-bridge
go mod tidy
go build -trimpath -ldflags="-s -w" -o wb-tls-bridge .
```

Recommended repository-local names:

```text
bin/wb-tls-bridge-windows-amd64.exe
bin/wb-tls-bridge-linux-amd64
bin/wb-tls-bridge-darwin-arm64
```

The Python side also discovers `bin/wb-tls-bridge[.exe]` and
`tools/wb-tls-bridge/wb-tls-bridge[.exe]`.

## Run

The gateway starts the bridge and sends a random authentication secret over its stdin pipe.

The process prints one machine-readable line to stdout:

```text
LISTEN 127.0.0.1:54321
```

`--parent-watch` exits when stdin reaches EOF, so a parent process can keep the
bridge alive by holding the stdin pipe.

## Python configuration

- `WB_TLS_FINGERPRINT=node`: enable the optional bridge.
- `WB_TLS_FINGERPRINT=python` (default; also `0`, `off`, `false`, `no`, `native`,
  `stdlib`): use the Python/OpenSSL profile and disable the bridge.
- `WB_TLS_BRIDGE=host:port`: use an already-running bridge on loopback, with
  its secret supplied as `WB_TLS_BRIDGE_SECRET`.
- `WB_TLS_BRIDGE=auto` or unset: launch a discovered bundled executable and
  cache its loopback address for the process lifetime.
- `WB_TLS_BRIDGE=0` (also `off`, `false`, `no`, `none`): use Python/OpenSSL.

Only HTTPS requests to non-loopback hosts use the bridge. The local
`X-WB-Upstream-Proxy` header is consumed by the bridge and is not forwarded to
the origin server. TLS certificates are verified against Go's system trust
store. For a private CA, build the bridge with the desired root installed in
that store.

Bind the listener to loopback. The proxy header can contain credentials, so do
not expose the bridge to an untrusted network.

## License

MIT License

Copyright (c) 2026 Sliverkiss
Copyright (c) 2026 linguo2625469
Copyright (c) 2026 misakano7545

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
