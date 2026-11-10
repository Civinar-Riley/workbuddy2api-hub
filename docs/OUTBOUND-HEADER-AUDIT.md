# 出站请求头逐路径审计

审计日期：2026-11-10

基线：`v2.0.8` / `7ffba72`

审计范围：Chat、计费、登录/刷新、产品目录、事件上报、媒体、代理与
Go/uTLS 桥的安全边界。

## 1. 结论先行

1. 本仓库的 Chat 请求头走独立的“有序头”路径，头名与顺序不做
   `.title()`；这条路径已有原始 socket 捕获测试。
2. 计费、登录、刷新、`/v3/config`、`/v2/report`、媒体仍走既有的
   `.title()` 默认路径。本次只补原始捕获，不凭参考项目猜改线上字节。
3. 参考实现的路径头顺序只是线索，不是本仓库验证过的官方抓包夹具。
   在没有本地官方逐字节证据时，保持现有行为。
4. `/v2/chat/busy` 在当前仓库未找到实际发送路径，不能为它凭空加模板。
5. 桥本地认证头只发给回环桥的 `CONNECT`，不会进入隧道后的源站请求；
   `Proxy-Authorization` 只发给代理，不进入 HTTPS 隧道后的源站请求。
6. Python `http.client` 本身只实现 HTTP/1.1，不会协商 h2；没有参考实现
   的“误复用半死 h2 流”问题需要在本传输栈里额外禁用。

## 2. 参考快照与证据等级

参考快照来自以下两个文件的本地副本：

- `internal/upstream/headorder.go`：路径头顺序表；
- `internal/upstream/transport.go`：连接层参数与 h2 处理。

参考提交：`65e644eb9372abe0eb0f85ed887343b6b117bf4d`。

该参考实现声称其顺序抄自官方夹具，但本仓库没有拿到同一批本地原始夹具，
因此本文把它标为“参考线索”，不能据此单独宣布与官方客户端逐字节一致。

本仓库的原始字节证据来自：

- `tests/_test_upstream_pool.py` 的 `[17]`：手工有序 Chat 头逐项顺序；
- `tests/_test_upstream_pool.py` 的 `[19]`：用真实构造器捕获
  Chat、billing、登录、刷新、config、report、media；
- `tests/_test_tls_fingerprint.py`：真实本地 CONNECT 桥复用与隧道头隔离；
- `tests/_test_media_headers.py`：媒体头集合及“不碰 billing”回归；
- `tests/_test_upstream_timeouts.py`：响应头、长流空闲和半死连接；
- `tests/_test_vscode_identity.py`：压缩头与 gzip 请求体一致性。

## 3. 汇总表

| 方法 / 路径 | 当前实际构造 | 参考快照 | 本地证据 | 本次是否改头 |
|---|---|---|---|---|
| `POST /v2/chat/completions` | `Account.headers("chat")` + `req.wb_exact_headers` | 43 头，按路径固定表重排 | 真实构造器与原始 socket | 否 |
| 计费类 `/billing/*`、`/v2/billing/*` | `Account.headers("billing")`，默认 `.title()` | 参考快照无独立计费夹具 | 真实构造器与原始 socket | 否 |
| `POST /v2/plugin/auth/state` | 登录轻量 Web 头，默认 `.title()` | 无顺序表 | 真实 `_start_login()` 捕获 | 否 |
| `POST /v2/plugin/auth/token/refresh` | 刷新头，默认 `.title()` | 无顺序表 | 真实 `refresh()` 捕获 | 否 |
| `GET /v2/plugin/auth/token` | 登录轮询轻量 Web 头 | 无顺序表 | 代码路径审计 | 否 |
| `GET /v3/config` | `remote_config_headers()`，默认 `.title()` | 6 头顺序表 | 真实构造器与原始 socket | 否 |
| `POST /v2/report` | 默认 Chat 头；`BILL_BASE` 时用 billing 头 | 12 头固定表 | 真实 `report_events()` 捕获 | 否 |
| 媒体 `/v2/images/*`、`/v2/videos/*` | `Account.headers(purpose="media")` | 无顺序表 | 真实 `_get_json()` 捕获 | 否 |
| `POST /v2/chat/busy` | 当前仓库无发送点 | 8 头固定表 | 未找到调用点 | 否 |
| Anthropic `/v1/messages` | 转换为 Chat 后走 Chat 路径 | 不适用 | 转换路径审计 | 否 |
| OpenAI Responses | 转换为 Chat 后走 Chat 路径 | 不适用 | 转换路径审计 | 否 |

## 4. 逐路径明细

### 4.1 `POST /v2/chat/completions`

当前实现：

- 构造器：`wb_accounts.Account.headers(purpose="chat")`。
- 发送点：`wb_proxy.open_upstream()` 将 `list(headers.items())` 挂到
  `req.wb_exact_headers`。
- 传输层：`wb_upstream_pool._request_headers(..., ordered_headers)` 不对
  这些头名做 `.title()`。

国际版桌面身份的代表性业务头顺序：

```text
Content-Type
Accept
X-Requested-With
Origin
Referer
X-CodeBuddy-Request
Accept-Language
X-Agent-Intent
X-Agent-Purpose
X-Agent-Type
X-IDE-Name
X-IDE-Type
X-IDE-Version
X-Private-Data
X-Product
X-Domain
User-Agent
X-User-Id
Authorization
X-Conversation-ID
X-Conversation-Request-ID
X-Root-Request-ID
X-Conversation-Message-ID
X-Request-ID
X-Trace-ID
traceparent
b3
X-B3-TraceId
X-B3-SpanId
X-B3-Sampled
```

本地原始捕获中的框架头位置：

```text
Accept-Encoding
Content-Length
Host
<上述业务头，保持构造顺序>
Connection
```

CLI、VSCode、桌面兼容模式会改变业务头集合；不能把某一种身份的表当成
所有身份的唯一表。

参考差异：

- 参考快照的 Chat 表为 43 个头，包含 `x-stainless-*`、
  `acp-connection-id`、`baggage`、`X-B3-ParentSpanId` 等；
- 参考表把 `Content-Length`、`Accept-Encoding`、`Host`、`Connection`
  固定在业务头之后；
- 本仓库当前由 `http.client` 自动补框架头，观测顺序是
  `Accept-Encoding`、`Content-Length`、`Host`。

本次处理：不改。原因是身份模式不同，且没有本仓库可直接核对的官方
原始夹具；现有有序头路径本身已有逐项字节测试。把参考表直接套到桌面
身份会同时改变集合与顺序，风险大于收益。

### 4.2 计费路径

覆盖：`/billing/*`、`/v2/billing/*`、签到、额度、套餐等。

当前业务头集合：

```text
Content-Type
Accept
X-Requested-With
User-Agent
Origin
Referer
Authorization
X-User-Id
X-Domain
X-CodeBuddy-Request
Accept-Language
X-Request-ID
X-Machine-ID
X-Session-ID
X-No-Enterprise-Id
```

可选追加：

```text
X-API-Key
X-Enterprise-Id
X-Tenant-Id
X-Product
X-Device-Token
```

当前发送点不挂 `wb_exact_headers`，因此头名仍经过 `.title()`，例如
`X-Machine-ID` 与 `X-Request-ID` 的缩写大小写不会按 Chat 的有序路径保留。
这是既有行为。

原始捕获确认：

- billing 集合与顺序稳定；
- 不会泄漏 Chat 的 `X-Conversation-ID`、`X-IDE-Type`、`traceparent`、`b3`。

参考快照没有独立 billing 夹具。本次不改。

### 4.3 登录与刷新

#### `POST /v2/plugin/auth/state`

当前业务头：

```text
Content-Type
Accept
X-Requested-With
User-Agent
Origin
Referer
```

不带 `Authorization`。原始捕获验证了上述集合。

#### `POST /v2/plugin/auth/token/refresh`

当前业务头：

```text
Content-Type
Accept
X-Requested-With
User-Agent
Origin
Referer
X-Refresh-Token
X-Auth-Refresh-Source
X-User-Id
X-Domain
X-CodeBuddy-Request
Accept-Language
```

可选追加 `X-Enterprise-Id`。刷新请求不带 `Authorization`；凭据在
`X-Refresh-Token`。原始捕获验证了请求头与响应后的 token 更新。

#### `GET /v2/plugin/auth/token`

登录轮询使用与 `auth/state` 相同的轻量 Web 头，不加桌面 Chat 的
会话、IDE、B3 头。

参考快照没有登录/刷新路径顺序表。本次不改。

### 4.4 `GET /v3/config`

当前业务头：

```text
Accept
User-Agent
Origin
Referer
Authorization
X-User-Id
```

静态 API Key 账号额外带 `X-API-Key`。

参考快照的顺序表：

```text
Accept
Connection
User-Agent
X-Request-ID
X-Product
Host
```

差异：

- 当前有 `Origin`、`Referer`、`Authorization`、`X-User-Id`；
- 参考表有 `X-Request-ID`、`X-Product`；
- 参考的 `Connection` / `Host` 位置也与 Python 框架头不同。

本仓库现有代码注释写明该端点的最小头集合是实际验证过的行为，但仓库
没有保存对应的官方原始抓包。本次只做捕获，不改集合或顺序。

### 4.5 `POST /v2/report`

当前实现：

- `base == BILL_BASE`：使用 billing 头；
- 其他情况，包括默认 Chat base：使用完整 Chat 头；
- 不挂 `wb_exact_headers`，因此线上仍走 `.title()` 默认路径。

参考快照的 `/v2/report` 顺序表只有：

```text
Accept
Content-Type
X-Request-ID
X-Product
User-Agent
Authorization
X-User-Id
X-Domain
Content-Length
Accept-Encoding
Host
Connection
```

当前默认路径明显更宽：会带 `Origin`、`Referer`、`X-Requested-With`、
`Accept-Language`、IDE/Agent 等 Chat 身份头。原始捕获已钉住现状。

本次不改。理由：

- 没有本仓库可核对的官方 `/v2/report` 夹具；
- 该路径服务成长任务事件，错误地缩头可能直接丢事件；
- 参考表属于另一个实现，不能单独作为修改证据。

### 4.6 媒体路径

覆盖 `/v2/images/*`、`/v2/videos/*`、上传预签名等。

代表性业务头顺序：

```text
Content-Type
Accept
X-Requested-With
X-Agent-Intent
X-Agent-Purpose
X-Agent-Type
X-IDE-Name
X-IDE-Type
X-IDE-Version
X-Private-Data
X-Product
X-Domain
User-Agent
X-User-Id
Authorization
X-Conversation-ID
X-Conversation-Request-ID
X-Root-Request-ID
X-Conversation-Message-ID
X-Request-ID
X-Trace-ID
traceparent
b3
X-B3-TraceId
X-B3-SpanId
X-B3-Sampled
X-No-Enterprise-Id
```

原始捕获确认：

- 有会话三件套；
- 不带 `Origin` / `Referer`；
- billing 与 media 的差异没有被互相污染。

参考快照没有媒体夹具。本次不改。

### 4.7 `POST /v2/chat/busy`

参考快照有 8 头顺序表，但当前仓库没有找到该端点的发送点。不能在没有
调用方、没有本地夹具的情况下为它新增模板或测试替身。

本次处理：记录为未覆盖路径。

### 4.8 Anthropic 与 Responses 兼容入口

`/v1/messages`、Responses API 等入口完成协议转换后，最终仍走
`open_upstream()` 的 Chat 上游和 Chat 有序头。没有第二套独立的源站
头构造器，因此不需要重复维护一份“Anthropic 出站头”。

### 4.9 压缩

CLI 与 VSCode 身份的 Chat 请求体使用 gzip，并同时设置
`Content-Encoding: gzip`。桌面普通身份不强制压缩。

- gzip 由 `wb_identity.gzip_body()` 产生；
- 发送点为 `wb_proxy.open_upstream()` 与每日 Chat；
- `tests/_test_vscode_identity.py` 校验头与可解压 body 一致；
- Go/uTLS 桥只接管 TLS，不决定是否压缩，也不会二次压缩。

本次未改压缩逻辑。

### 4.10 代理与桥的本地头边界

HTTPS 经 HTTP 代理：

- `Proxy-Authorization` 只放在发往代理的 `CONNECT` 请求里；
- 隧道建立后的源站请求不再带它。

HTTPS 经本地 Go/uTLS 桥：

- `X-WB-Bridge-Auth` 与 `X-WB-Upstream-Proxy` 只放在发往回环桥的
  `CONNECT` 请求里；
- 隧道建立后的 POST 不包含这三个本地/代理头。

真实本地桥测试已在同一条 keep-alive 连接上完成两次 POST，并逐项断言：

```text
隧道内 POST 不含 X-WB-Bridge-Auth
隧道内 POST 不含 X-WB-Upstream-Proxy
隧道内 POST 不含 Proxy-Authorization
```

## 5. 连接层对照

参考快照：

- TCP 建连超时 10s；
- TCP keepalive 15s；
- TLS 握手上限 10s；
- 空闲连接 30s；
- 响应头超时 60s；
- 显式禁用 h2。

本仓库：

- `http.client` 只走 HTTP/1.1，不需要额外“禁用 h2”；
- 连接池有复用探活、空闲 TTL、流式读取后归还、半截 body 丢弃；
- 响应头超时与流空闲超时由调用点传入的 timeout 和
  `_apply_stream_idle_timeout()` 管理；
- 桥退出会清退旧代次空闲连接，活跃连接不归还旧池。

连接层参数本次不改。参考值可以作为后续独立实验的输入，但在没有本仓库
的慢首包、长 SSE、移动网络与 OpenWrt 真机数据前，直接改成参考值会改变
既有超时语义。

## 6. 后续有证据时再做

以下项目必须拿到新的本地原始夹具或真实上游实验后才能动：

1. Chat 框架头 `Accept-Encoding` / `Content-Length` / `Host` 的相对位置；
2. `/v2/report` 是否应缩成参考实现的 12 头；
3. `/v3/config` 是否应改用参考实现的 `X-Request-ID` / `X-Product`
   组合；
4. `/v2/chat/busy` 的真实调用场景与头集合；
5. 空闲连接 30s、响应头 60s 与现有 Python 超时策略的 A/B 对比。

在这些证据出现前，本审计的明确结论是：**只记录、只保护现状，不盲目
复刻参考顺序。**
