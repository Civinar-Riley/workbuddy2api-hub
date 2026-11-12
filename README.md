# WorkBuddy2API-Hub — 国际版、国内版多账号网关中枢

> [!IMPORTANT]
> **官方群聊**：[https://t.me/wb2apihub](https://t.me/wb2apihub)
>
> **凭证安全**：本项目是本地部署网关，运行时需要在本地读取用户自行导入的账号凭证，才能调用官方接口；凭证保存在用户自己的 `accounts/` 目录。项目不会把凭证上传到本项目的第三方服务器，也没有隐藏的凭证收集或上传逻辑。请立即关闭公网暴露，联系官方吊销全部 OAuth / 设备授权，确认旧 token 返回 `401` 后再重新授权。

> [!WARNING]
> **WorkBuddy 上游正在收紧对非官方客户端的识别与拦截。**
>
> - **封禁**：2026-10-23 下午起，非官方客户端的识别与拦截明显收紧。
> - **维护者**：近期工作压力增大，无法保证及时更新与响应。
> - **可用性**：上游可能随时进一步收紧，导致本项目不可用。
> - **账号风险**：使用非官方客户端造成的风控、限流或封禁，由使用者自行承担。
>
> **Upstream enforcement** tightened detection and blocking of unofficial clients from the afternoon of 2026-10-23. The maintainer is under increased workload and cannot guarantee timely updates or replies. The upstream may tighten further at any time; rate limits, account restrictions or bans caused by using an unofficial client are the user's responsibility.

本项目仅用于协议学习、研究与个人自用。请优先使用官方客户端，不要使用主账号，不要共享账号，也不要用于商业转售。

This project is for protocol research and personal use only. Prefer the official client, do not use your primary account, and do not resell or share access.

<p align="center">
  <a href="https://github.com/ardeyouxipianyi/workbuddy2api-hub/releases"><img src="https://img.shields.io/badge/Release-v2.1.2-2496ED?style=flat-square" alt="Version 2.1.2"></a>
  <img src="https://img.shields.io/badge/Python-3.9+-blue.svg?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/API-OpenAI_Compatible-412991?style=flat-square" alt="OpenAI API">
  <img src="https://img.shields.io/badge/Dual_Realm-Intl_&_CN-0DBD8B?style=flat-square" alt="Dual Realm">
  <img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/Maintenance-Resumed-0DBD8B?style=flat-square" alt="Maintenance resumed">
</p>

## 核心特性

- **标准 API 兼容**：将 WorkBuddy（国际版/国内版）与 CodeArts 封装为标准 OpenAI 格式（Chat Completions / Responses），无缝支持 Codex、Claude Code、NextChat 等各类客户端。
- **开箱即用与多端部署**：便携版免装环境（Windows 自带精简运行时），支持 Docker、NAS（群晖/飞牛）一键部署及 OpenWrt 路由器固件。
- **多账号池与出口隔离**：支持浏览器 OAuth 快速导入、每账号独立出口代理槽绑定、设备指纹隔离、自动故障转移与配额轮转。
- **自动化福利与签到**：内置国内版全套成长任务、加油站签到与猫猫旅行，支持国际版每日活跃会话与 CodeArts 自动签到。
- **可视化管理看板**：提供 Web 仪表盘，支持实时请求日志、Token/积分透视、多 API Key 权限分发（模型/账号白名单）与额度护栏。
- **一键工作台启动**：`start-workbuddy-all.bat` 先用 `--probe-only` 检查端口上的实例是否健康，未运行才拉起网关并打开 WorkBuddy 工作台，避免重复启动或误连陌生服务。

### 社区 Rust 版本

[YTjungle666/workbuddy2api-hub](https://github.com/YTjungle666/workbuddy2api-hub) 是社区独立维护的 Rust 重写分支。网关后端与启动器均使用 Rust 实现，提供单文件静态二进制，不依赖 Python 运行时；功能以上游 `v2.0.1` 为对齐基线。它不是本仓库的官方发布物，问题反馈与版本更新请前往对应仓库。

> ⚡ **Vibe Coding 产物**：本项目为 100% Vibe Coding 协同产物——人类开发者提出架构与业务意图，AI 助手端到端完成逆向分析、链路调度、WAF 指纹脱敏与界面编写。

> 📌 **提交代码贡献前**：请先查看并**严格遵循**贡献手册 [`CONTRIBUTING.md`](CONTRIBUTING.md)——提交格式、测试门禁与端到端冒烟要求都在那里（AI / 智能体请特别注意其中的硬性规则节）。

---

## 💖 赞助商

> 想出现在这里？联系 [ardeyouxipianyi@gmail.com](mailto:ardeyouxipianyi@gmail.com)

<table>
<tr>
<td width="180" align="center" valign="middle">
  <a href="https://agentrouter.org/register?aff=unrV"><img src="https://raw.githubusercontent.com/88lin/picx-images-hosting/master/90C5FAD072EA247822CB88BB32512A41.webp" alt="Agent Router" width="150"></a>
</td>
<td valign="middle"><b><a href="https://agentrouter.org/register?aff=unrV">Agent Router</a></b>&nbsp;是免费公益大模型 API 平台，支持 Claude-opus-5.5、GPT-6 Astra、GPT-6.1 Sol、deepseek-v4-flash 等主流模型，国内直连。注册送 ＄175（每日签到得 ＄25，被邀得 ＄50），支持 GitHub / LinuxDo 登录。</td>
</tr>
</table>


---

## 一、快速启动

### 1. 本机运行

- **Windows**：双击 `start-wb-proxy.bat`（静默后台运行 + 独立日志窗口）；如需前台控制台窗口，双击 `start-wb-proxy-visible.bat`。
- **macOS / Linux**：双击 `start-wb-proxy.command` 或在终端执行 `./start-wb-proxy.sh [端口]`（默认 8788，需要 Python 3.9+）。
- **局域网共享模式**：运行 `start-wb-proxy-lan.bat`（Windows）或 `./start-wb-proxy-lan.sh`（macOS/Linux），首次启动将自动生成高强度 API Key 并在控制台打印。
- 启动后访问：
  - **Web 看板**：`http://127.0.0.1:8788/`（默认访问密码 `admin`，首次登录请在设置中修改）
  - **API 地址**：`http://127.0.0.1:8788/v1`

### 2. Docker 部署

预编译多架构镜像发布在 GHCR，无需本地构建：

**方式一：一键脚本（推荐）**

```bash
# 官方源（直连 GitHub 环境）：
curl -fsSL https://raw.githubusercontent.com/ardeyouxipianyi/workbuddy2api-hub/main/quick-deploy.sh | bash

# 国内网络 / NAS 加速：
curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/ardeyouxipianyi/workbuddy2api-hub/main/quick-deploy.sh | sudo bash
```

> 公网部署或配合反向代理：在管道前指定 `WB_DEPLOY_MODE=public bash`，并搭配 Nginx / Caddy / Cloudflare Tunnel 配置 TLS。

**方式二：单文件 Docker Compose**

```yaml
services:
  wb-proxy:
    image: ghcr.io/ardeyouxipianyi/workbuddy2api-hub:latest
    container_name: wb-proxy
    restart: unless-stopped
    ports:
      - "8788:8788"
    environment:
      - HOST=0.0.0.0
      - PORT=8788
      - TZ=Asia/Shanghai
    volumes:
      - ./accounts:/app/accounts    # 账号凭据与配置（持久化）
      - ./usage:/app/usage          # 用量流水日志（持久化）
```

更新镜像：`docker compose pull && docker compose up -d`。

Docker 镜像在构建阶段已经编译好当前架构的 Go/uTLS 桥，容器内不需要 Go。
未设置 `WB_TLS_FINGERPRINT` 时默认 `auto`；如需彻底关闭桥，可加
`- WB_TLS_FINGERPRINT=python`，卷中的账号、设置和用量不会因此改变。

### 3. 启动入口：优先用 `-m`

同一份代码、同一个账号池，`python3 wb_proxy.py` 与 `python3 -m wb_proxy` 的常驻内存差 **47-54 MB**
（Windows 11 / aarch64、Python 3.12-3.14 四套环境实测一致，见 #702）。成因仍在排查，
但只要换成 `-m` 就能拿到这段收益——512 MB 的设备上约 10%。

```bash
cd /path/to/workbuddy2api-hub        # 必须先进入 wb_proxy.py 所在目录
python3 -m wb_proxy --port 8788
```

`-m` 把**当前目录**放进 `sys.path`，而脚本方式放的是脚本所在目录，所以务必先 `cd`。
仓库内的资源路径都基于 `__file__` 解析，不受影响。Docker 镜像和 OpenWrt 包已改用 `-m`；
Windows / macOS 的 `start-wb-proxy.*` 启动脚本暂未改动。

### 4. TLS 出站桥（默认 `auto`）

从本版本起，未显式配置 TLS 模式的安装默认使用 `WB_TLS_FINGERPRINT=auto`：
网关会先尝试随包 Go/uTLS 桥；桥二进制缺失、架构不支持、启动失败、桥进程运行中
退出，或当前请求使用自定义 CA / 不兼容代理时，自动回退 Python/OpenSSL。
回退只发生在当前进程内，不会反复拉起失败的桥，也不会改变原来的代理出口和证书
校验。便捷版、Docker 和 OpenWrt 的受支持架构包都自带对应桥，用户不需要安装 Go。

需要强制指定时：

```sh
WB_TLS_FINGERPRINT=python   # 强制 Python/OpenSSL
WB_TLS_FINGERPRINT=node     # 强制桥；桥不可用时报明确错误
```

`node` 模式下不要设置 `WB_TLS_BRIDGE=off`，否则会按显式关闭桥处理而走 Python。
OpenWrt 使用 `uci set workbuddy2api.main.tls_mode='auto'`、`'node'` 或 `'python'`。
实际模式、最近路线和回退原因可在 `/health`、`/settings` 及面板网络设置中查看。
降级到不识别 `auto` 的旧版本前，请临时设置 `WB_TLS_FINGERPRINT=python`。
逐路径头顺序、代理/桥头隔离和未修改项见
[`docs/OUTBOUND-HEADER-AUDIT.md`](docs/OUTBOUND-HEADER-AUDIT.md)。

### 5. CodeArts 原生第三出口

网关内置进程内直连华为云 CodeArts，支持聊天 SSE 协议转换与自动签到，无需单独运行第三方桥接服务：
1. 打开看板 →「设置 → CodeArts 接入」，勾选「启用 CodeArts 出口」并保存；
2. 切换至看板顶部「CodeArts」视图，点击「+ 添加账号 (OAuth)」完成华为云授权；
3. 在「设置 → API Key」中新建 Key 并绑定出口为 `CodeArts`（或全局通用）即可调用。

CodeArts 的模型 ID 按上游目录**精确匹配**：`deepseek-v4.1-flash` 与
`deepseek-v4.1-flash-1017` 是两个独立模型，不会互相别名或静默回退。
`/v1/models?realm=codearts` 返回的是账号当前可用的真实目录 ID；调用时请直接使用
返回的完整 ID。

---

## 二、账号接入与 API Key 权限

### 1. 账号添加与代理出口绑定
- 打开看板 `http://127.0.0.1:8788/`，点击「+ 添加账号 (OAuth)」，在浏览器中完成官方登录授权即可自动入库。
- **独立代理出口槽（Proxy Slot）**：在看板中可按需配置并命名 HTTP/SOCKS5 出口代理（支持账号密码认证），支持将账号与特定代理槽位绑定，账号身份请求与出站调用均自动走该代理。

### 2. 多 API Key 权限管理
- 「设置」页支持创建多个独立 API Key：
  - **出口绑定**：可固定走国际版、国内版或 CodeArts；
  - **模型限制**：支持通配符白名单（如 `deepseek*`, `gpt-6-astra`），违规请求本机直接返回 400；
  - **账号白名单**：可精确限定该 Key 仅允许调度的账号范围；
  - **用量透视**：数据看板按 Key 独立统计调用次数、Token 消耗、缓存命中与积分。

---

## 三、客户端接入配置

- **Base URL**：`http://127.0.0.1:8788/v1`（局域网为 `http://<局域网IP>:8788/v1`）
- **API Key**：看板「设置」中添加并绑定的 Key
- **模型名称**：调用 `/v1/models` 中列出的任意官方对齐模型 ID（如 `deepseek-v4.1-flash`, `gpt-6-astra`, `hy3`）

**Codex CLI / Responses API**

```bash
export OPENAI_BASE_URL="http://127.0.0.1:8788/v1"
export OPENAI_API_KEY="你在看板设置中添加并绑定的API_Key"
```

**Claude Code / Anthropic Messages（实验性）**

网关支持实验性 Messages 协议适配，因长会话缓存命中率差异，默认关闭：
```bash
# 网关端设置 WB_ENABLE_ANTHROPIC_MESSAGES=1 开启后生效：
export ANTHROPIC_BASE_URL="http://127.0.0.1:8788"
export ANTHROPIC_API_KEY="你在看板设置中添加并绑定的API_Key"
```

---

## 四、常用环境变量

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PORT` | `8788` | 网关监听端口 |
| `API_KEY` | 自动生成 | 初始 API Key（看板配置多 Key 后优先由看板管理） |
| `WB_DEPLOY_MODE` | `lan` | 部署模式：`lan`（0.0.0.0）、`local`（127.0.0.1）、`public` |
| `WB_MAX_PAYLOAD_BYTES` | `16777216` | 单请求体上限（字节，默认 16MB，支持 1–256 MB） |
| `WB_CHAT_SLOT_WAIT` | `30` | 并发槽位的等待秒数：全局对话槽位与 Key 级 `concurrency_limit` 共用这个预算，等到才有名额，超时仍按 429 拒绝 |
| `WB_ENABLE_ANTHROPIC_MESSAGES` | `0` | 是否开启实验性 Anthropic Messages 协议转换 |
| `WB_BACKGROUND_MODEL_TARGET` | 留空 | 后台模型别名改写目标（如 DSH 客户端自动映射） |
| `WB_CA_BUNDLE` | 留空 | 自签根证书路径（供抓包调试或中间代理使用） |
| `WB_CN_CHAT_UPSTREAM` | 官方默认 | 国内版出站代理地址 |
| `WB_IDENTITY_OS` | 宿主系统 | 仅 CLI 身份：覆盖 `x-stainless-os`（如 `Linux` / `Windows`） |
| `WB_IDENTITY_ARCH` | 宿主架构 | 仅 CLI 身份：覆盖 `x-stainless-arch`（如 `x64` / `arm64`） |
| `WB_IDENTITY_NODE_VERSION` | 官方默认 | 仅 CLI 身份：覆盖 `x-stainless-runtime-version` |
| `WB_SANITIZE_LOOSE_BILLING` | `0` | 计费头的宽松兜底：严格解析认不出时，按「像计费头」（含 ≥2 个 `cc_*=` / `cch=` 键值、且不超过 400 字符）整段删除。默认关闭，保持 #853 之后的严格语义；客户端改字段名导致每轮前缀命中率归零时才需要打开 |
| `WB_MSGSTAT_SHARDS` | `0` | 单发 `/v1/messages` 请求收敛到几个账号上（0 = 关闭）。打开后同一 *(key, 模型, system, tools)* 组合的一次性调用固定复用 ≤N 个账号，避免被首条 user 文本打散到整池；上游 prompt 缓存按账号存放，换号即整段重付 |
| `WB_MSGSTAT_DAILY_SALT` | `1` | 上一条的亲和键带当天日期盐，每天各桶重新开一次；`0` 关闭（键跨天不变） |

遇到上游 `400 / 11-128` 时，先按 [`docs/TROUBLESHOOTING-11-128.md`](docs/TROUBLESHOOTING-11-128.md) 收集同一请求的
身份、最小请求和提示词对照结果；不要先轮换账号。

---

## 五、主要 API 端点

| 接口 | 方法 | 端点 | 说明 |
|---|---|---|---|
| 聊天补全 | `POST` | `/v1/chat/completions` | 标准 OpenAI 聊天接口（支持流式 SSE 与思维链回填） |
| 交互式响应 | `POST` | `/v1/responses` | OpenAI Responses 协议接口（Codex 专用） |
| 模型目录 | `GET` | `/v1/models` | 获取当前出口对齐的官方可用模型列表 |
| 生图 | `POST` | `/v1/images/generations` | 文生图 / 图生图（始终可用，按媒体号池自动选出口） |
| 生视频 | `POST` | `/v1/videos` | 异步生视频任务提交（本地并发排队与空闲调度） |
| 视频状态 | `GET` | `/v1/videos/<id>` | 轮询视频生成进度与结果 |
| 产物下载 | `GET` | `/v1/videos/<id>/content` | 下载视频 MP4 产物直链 |
| 健康检查 | `GET` | `/health` | 网关运行状态、可用账号数与就绪模型分布 |

---

## 六、测试验证

```bash
python tests/run_all.py            # 全部套件
python tests/run_all.py realm      # 只跑名字里含 realm 的
```

- 305 个套件：228 个 Python + 77 个 JS；JS 需要 PATH 上有 `node`，缺失时会跳过并提示。
- `tests/_mobile_check.py` 是独立的 Playwright 手机/桌面布局检查器（需自行安装 Playwright），按需手动运行，不在上面的套件集里。
- `tests/_visual_recent.py` 是同一类手动检查器：用合成 fixture 起一份 1480 宽的「请求列表」，验证失败徽章点开的详情弹窗、四个过滤下拉真的把参数发到 `/usage/recent`，并把列表 / 弹窗 / 筛选条截图写到系统临时目录的 `wb-visual-recent/`。
- CI（`.github/workflows/tests.yml`）跑同一条命令：Ubuntu 上 python 3.9 与 3.12（3.9 是本项目声称的最低版本），Windows 上 python 3.12；推送 `v*` tag 时额外断言 **tag == 源码版本**（`-ci` 演练 tag 豁免）。

---

## 七、版本更新记录 (Changelog)

本版更新与完整历史版本请查阅 **[docs/CHANGELOG.md](docs/CHANGELOG.md)**。
### v2.1.2

- **请求列表支持失败详情与筛选**（#883）：最近请求现在可按账号、模型、API Key 和结果筛选；失败状态可直接展开完整错误，不再只能看到截断的一行。筛选与分页在后端完成，跨区域扫描有上限，不会为了筛一页把全部日志读进内存。
- **CodeArts 请求积分不再恒为 0**（#866）：CodeArts 聊天 SSE 只返回 token，不返回逐请求 `credit`。网关现在在请求发出前读取账号累计已用积分，响应结束后按差值写入本次 usage 行；`chat/completions`、`responses`、`messages` 的流式与非流式共用同一结算点。采样失败、计数回退或上游已返回非零 `credit` 时不改写，且查询失败不会影响正常聊天。
- **统计时区开关（北京时间）**：设置 →「日志与系统」新增「统计时区」卡片（`settings.json` 键 `stats_tz_beijing`，`/settings/save` 只收 JSON 布尔，默认关闭）。关闭时全部时间按服务器所在时区显示与分桶，与上游一致；勾选后统一按北京时间（固定 UTC+8）计算日志时间戳、请求记录、撞线 / 重置时刻、今日活动、用量日 / 小时分桶、签到日期、额度重置日与导出文件名。落盘仍存 epoch，切换只影响展示与日历日计算，保存后立即生效。实现用固定偏移而不是 `TZ=Asia/Shanghai` + `tzset()`：中国自 1991 年起无夏令时，固定偏移不需要 tzdata，Windows 没有 `tzset`、musl / OpenWrt 镜像常缺 zoneinfo 也能正确工作。 / A statistics-timezone switch (Settings → Logs & system) now offers Beijing time: `stats_tz_beijing` in `settings.json`, a strict JSON boolean on `/settings/save`, off by default. With it off every time is shown and bucketed in the host timezone exactly as before; switched on, log timestamps, request rows, limit-hit and reset moments, today's activity, usage day/hour buckets, check-in dates, quota reset days and export filenames all use a fixed UTC+8. Epochs are still what gets stored, so the switch only changes display and calendar-day maths, and it takes effect on save. The implementation uses a fixed offset rather than `TZ=Asia/Shanghai` + `tzset()`: China has had no DST since 1991, so no tz database is needed, which keeps it correct on Windows (no `tzset`) and on musl / OpenWrt images that ship without zoneinfo.

- **请求列表：失败看得进去、筛得出结果**：结果列里的「失败 / 上游中断 / 客户端取消」改成可点按钮，点开是一层详情弹窗——HTTP 状态码、上游原话（落盘时已截断 200 字）、账号、API Key、模型、耗时、模式、token 与请求类型，可一键复制；行上不再为它多占一列。筛选条移到标题下方（表格之上），新增「结果（全部 / 成功 / 失败）/ 账号 / 模型 / Key」四个下拉，全部走服务端 `GET /usage/recent?account=&model=&key=&outcome=`：日志上百万行时不是只筛当前页，而是向后最多扫描 `WB_RECENT_FILTER_SCAN`（默认 20000）行；扫到上限时返回 `total_exact=false` 与 `scanned=N`，界面写明「已扫描 N 行，命中 M 条（可能还有更早的）」，不假装知道全量。「失败」是个桶：`failed` / `upstream_aborted` / `client_aborted` 都算没成功。账号 / 模型 / Key 三个下拉恒带搜索框（`data-cs-search="on"`），几十上百条也不用翻。 / **Request list: failures readable, results filterable**: the outcome cell's "failed / upstream aborted / client aborted" badges are buttons now, opening a detail dialog with the HTTP status, the upstream message as recorded (already truncated to 200 chars), account, API key, model, elapsed time, mode, token counts and request type, copyable in one click - with no extra column spent on the row. The filter bar moves above the table and gains outcome (all / succeeded / failed), account, model and key dropdowns, all served by `GET /usage/recent?account=&model=&key=&outcome=`: with millions of log lines the filter is not limited to the current page - it scans back at most `WB_RECENT_FILTER_SCAN` (default 20000) rows and reports `total_exact=false` plus `scanned=N` when it hits that cap, so the UI says "scanned N rows, M matched (older rows may exist)" instead of pretending to know the total. "Failed" is a bucket: `failed` / `upstream_aborted` / `client_aborted` all count as not-succeeded. The account / model / key dropdowns always carry a search box (`data-cs-search="on"`), so a few hundred entries do not have to be scrolled by hand.
- **账号页批量操作统一按「勾选否则当前筛选结果」定作用范围**：刷新积分 / 刷新凭证 / 测试 / 分配代理 / 切换所有出站身份 / 启用 / 停用原来各写一套口径（有的刷整池、有的只认分组），现在统一走 `acctScopeUids()`——有勾选只作用于勾选的账号，没有勾选则作用于当前筛选结果的全部账号（视图 + 号池 + 搜索 / 状态 / 分组 / 错误码，且不分页，避免「全部」只作用当前页）。批量挪号池仍要求先勾选：号池归属是一次明确的指派，没有勾选无从下手。`/accounts/refresh`、`/accounts/test`、`/accounts/product` 新增 `uids` 点名，空列表即什么都不做，不退化成全池；`/accounts/set-all` 原本就要求显式 UID 列表。按钮同时去掉「一键」前缀（刷新积分 / 刷新凭证 / 测试 / 分配代理 / 切换所有出站身份 / 启用 / 停用），「当前分组启用 / 停用」保留。 / The account page's bulk actions now share one scope rule: "Refresh credits", "Refresh tokens", "Test", "Assign proxy exits", "Switch all outbound identities", "Enable" and "Disable" each used to compute their own target set (some hit the whole pool, some only the group filter). They now all go through `acctScopeUids()`: the ticked accounts when there are any, otherwise the whole current filter result (view + pool + search / status / group / error code, and not just the current page, so "all" does not silently mean "this page"). Moving accounts between pools still requires a selection, because a pool assignment is an explicit choice. `/accounts/refresh`, `/accounts/test` and `/accounts/product` accept a `uids` list now; an empty list does nothing instead of falling back to the whole pool, and `/accounts/set-all` already required an explicit UID list. The "一键" prefix is dropped from those buttons while "Enable / Disable current group" keeps its name.
- **号池筛选常显并改为下拉框**：账号表的号池筛选不再跟随「文本 / 媒体号池隔离」开关隐藏——`poolScope` 是账号的归属标记，隔离关着时它只影响服务端派发，不影响这个字段可查可改。三个互斥按钮（全部 / 文本 / 媒体）换成一个下拉框，默认「全部号池」，选中即生效；账号行的号池徽章同样恒显示，「移入媒体池 / 移入文本池」不再要求先开隔离。移动端号池组排成一行（下拉 + 计数 + 两个按钮），不再各占一行把账号列表推下去。 / The pool filter no longer hides behind the pool-separation switch: `poolScope` is an account's ownership tag, and the switch only affects server-side dispatch, not whether the field can be seen or changed. The three mutually exclusive buttons become one dropdown (All / Text / Media, default All, applied immediately); the pool badge on each row is always shown, and "Move to media pool" / "Move to text pool" no longer require the switch. On phones the pool group sits on a single row (dropdown + counter + two buttons) instead of pushing the account list down.
- **模型别名开关**：`model_aliases` 之前只是一张无条件生效的映射表——填进去的别名立刻对所有客户端可见、所有请求都会被改写，运维想「先填好但暂不生效」没有手段。新增 `model_aliases_enabled`（缺省关闭，旧配置升级后行为不变），关闭时别名既不进 `/v1/models` 也不改写请求，原模型名照常可用；开启后别名与原名都可用，别名在模型清单里紧跟其目标模型列出。面板「设置 → 模型与定价」新增开关，「网关 → 模型清单」新增行内「对外名称」列（开关关闭时整列隐藏），支持直接编辑单个模型的对外名称。配套：别名请求的应答回显客户端发来的名字（`client_facing_model`），配了模型限制的 Key 用别名调用不再被网关自己的改写拦下，`/settings` 与 `/settings/save` 读写 `model_aliases` / `model_aliases_enabled`。 / **Model alias switch**: `model_aliases` used to be an unconditionally applied mapping table - any alias typed in was immediately visible to every client and rewritten on every request, with no way to stage a table without activating it. A new `model_aliases_enabled` key (off by default, so upgrades keep the old behaviour) gates it: while off, aliases are neither listed in `/v1/models` nor rewritten, and the original model names keep working; while on, aliases and original names both work and each alias is listed right next to its target. The panel gains a switch under Settings -> Models & pricing and an inline "External name" column in the gateway model catalog (the whole column hides while the switch is off), editable per model. Supporting changes: replies echo the name the client sent (`client_facing_model`), a key restricted to an alias is no longer rejected by the gateway's own rewrite, and `/settings` plus `/settings/save` read and write `model_aliases` / `model_aliases_enabled`.
- **账号页桌面布局重排**：账号表改为跟随容器宽度（`width:100%` + `min-width:1096px` 只作横向滚动兜底），上一版写死 `min-width` 让容器窄时右侧列被固定操作列盖住，「Token / 缓存」看着像被裁掉；各列按实测内容定宽，多余宽度全给账号列。勾选列、版本列、账号列三列常驻左侧吸附（偏移 0 / 32 / 100px），横向滚动时不会只剩一排认不出归属的数据。桌面昵称允许折行、完整显示（此前 `nowrap + ellipsis` 把长昵称裁成「changzhan…」）。任务开关 / 备注 / 分组收到账号行内同一排（`.acct-chip-row`）。 / **Desktop account table re-layout**: the table now follows its container (`width:100%` with `min-width:1096px` only as a horizontal-scroll fallback); the previous fixed `min-width` let the sticky actions column cover the right-hand columns on narrower containers, so "Token / Cache" looked cut off. Columns are sized to their measured content and the leftover width goes to the account column. The checkbox, version and account columns stay pinned to the left (offsets 0 / 32 / 100px) so a horizontally scrolled row never becomes an unidentifiable strip. Desktop nicknames wrap and show in full (they used to be clipped to "changzhan..." by `nowrap + ellipsis`). The task switch, note and group chips sit on one row inside the account cell (`.acct-chip-row`).
- **账号页移动端卡片分层**：第一行「勾选 + 昵称 + 版本 / 账户类型徽章」，第二行「号池 + 任务开关 + 备注 + 分组」chip 流；状态徽章与到期信息各归其行，长昵称按字符折行不超屏。启用 / 停用 / 删除三个按钮在手机上排成一行（各占 1/3），删除紧跟停用。批量操作组改成两列网格（6 个按钮 3 行 2 列），按钮文字不再被压成竖排。号池组（下拉 + 计数 + 两个「移入」）排成一行。 / **Mobile account card layering**: the first row is "checkbox + nickname + version / account-type badges" and the second row is a chip flow of "pool + task switch + note + group"; the state badge and expiry each get their own line, and long nicknames wrap by character instead of overflowing. Enable / Disable / Delete sit on one row on phones (one third each) with Delete right after Disable. The bulk-action group becomes a two-column grid (six buttons over three rows), so button labels are no longer squeezed into vertical text. The pool group (dropdown + counter + two "move" buttons) sits on a single row.
- **启用 / 停用 / 删除 / 导出统一作用域**：四个「xx 已选」独立按钮移除，能力并进「启用 / 停用 / 删除 / 导出」——有勾选时以勾选为准，没有勾选时以当前筛选结果为准；勾选时按钮上标出数量（如「启用 (2)」），确认框写明本次范围。表头全选框改为作用于当前筛选结果（跨页），三态也按筛选结果回填。 / **One scope for enable / disable / delete / export**: the four "xx selected" buttons are gone and their behaviour folded into Enable / Disable / Delete / Export - checked accounts when anything is selected, otherwise the current filter result; the buttons show the count when a selection exists (e.g. "Enable (2)") and the confirmation dialog names the scope. The header select-all now applies to the whole filter result (across pages) and its tri-state is filled from that same set.
- **表头文案缩短**：「今日活动」→「活动」、「总 token」→「Token」（全称保留在 `title` 里），省出的宽度给账号列。 / **Shorter header labels**: "Today's activity" -> "Activity" and "Total tokens" -> "Token" (the full wording stays in `title`), freeing width for the account column.

---

## 八、致谢与引用声明 (Credits & References)

在协议逆向、风控规避、任务链路与工程化设计过程中，本项目参考、吸纳并致谢以下优秀开源项目与技术成果（排名不分先后）：

### 核心逆向与协议参考
- **[Sliverkiss/workbuddy2api](https://github.com/Sliverkiss/workbuddy2api)**：成长任务逆向、设备指纹派生（`derive_id`）、调度器（`Scheduler`）与 `reasoning_content` 回填设计；
- **[L0NE-6/WorkBuddy-Daily](https://github.com/L0NE-6/WorkBuddy-Daily)**：国内版微信小程序专属事件指纹与动态专家深链任务模型；
- **[88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin)**：猫猫日常旅行与成长能量开盲盒（Buddy 盲盒）安全生命周期；
- **[linguo2625469/workbuddy2api-panel](https://github.com/linguo2625469/workbuddy2api-panel)**：国内版连登管家闭环逻辑与轻量对话活跃上报事件；
- **[misakano7545/workbuddy2api-panel](https://github.com/misakano7545/workbuddy2api-panel)**：Go/uTLS Node/OpenSSL ClientHello 指纹参数与桥接实现参考（18 Cipher、10 Extension、无 ALPN）；
- **[Zhengyuuuui/workbuddy2api](https://github.com/Zhengyuuuui/workbuddy2api)**：国际版 11140 账号封禁识别、冷却规避与故障排查清单；
- **[yuanyuanyuan430/workbuddy2api-hub](https://github.com/yuanyuanyuan430/workbuddy2api-hub)**：国际版 EdgeOne CDN 证书错配排查与活跃上报优雅降级方案；
- **[CangShui/workbuddy-cliproxy-fix](https://github.com/CangShui/workbuddy-cliproxy-fix)** & **[lovingfish/workbuddy-cliproxy](https://github.com/lovingfish/workbuddy-cliproxy)**：早期客户端代理修复与网关通信原型；
- **[mmqz/cpa-multi-plugins](https://github.com/mmqz/cpa-multi-plugins)**：多插件管理与出站调度结构参考；
- **[ardeyouxipianyi/workbuddy2api](https://github.com/ardeyouxipianyi/workbuddy2api)**：国内版早期分发包逆向分析与出站 User-Agent 规范；

### 工程化与生态基础
- **[aodianjun/workbuddy2api-hub](https://github.com/aodianjun/workbuddy2api-hub)**：OpenWrt 路由器打包配方（`.ipk` / `.apk`）与系统服务支持；
- **[YTjungle666/workbuddy2api-hub](https://github.com/YTjungle666/workbuddy2api-hub)**：社区维护的 Rust 后端与启动器重写分支，提供无 Python 运行时依赖的单文件静态二进制；
- **[astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone)**：Windows 便携版免安装精简独立 Python 运行时构建基石；
- **[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)**：DSH 客户端协议与提示词指纹清洗规避参考。

感谢所有向本项目提交 PR、反馈 Issue 与分享实测样本的社区贡献者！

---

## 九、免责声明 (Disclaimer)

本项目仅用于协议学习与个人开发测试，不提供任何商业服务或可用性担保。因使用非官方客户端产生的账号风控、限制或封禁由使用者自行承担，请优先使用官方客户端。

## ⭐ Star 趋势

<p align="center">
  <a href="https://star-history.com/#ardeyouxipianyi/workbuddy2api-hub&Date">
    <img src="https://api.star-history.com/svg?repos=ardeyouxipianyi/workbuddy2api-hub&type=Date" alt="Star History Chart" width="760">
  </a>
</p>
