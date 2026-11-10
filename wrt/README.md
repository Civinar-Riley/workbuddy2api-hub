# wrt/ — OpenWrt 打包配方

这里放 WorkBuddy2API-Hub 的 OpenWrt 安装包配方。配方本身来自
[#190](https://github.com/ardeyouxipianyi/workbuddy2api-hub/issues/190) 引用的
`aodianjun/workbuddy2api-hub/wrt/`，逐项审计后并入本仓库，**只保留打包与运行
需要的东西**（审计结论见文末）。

## 内容

```
wrt/
├── README.md                      # 本文件
└── openwrt/
    ├── build-ipk.sh               # 打包 .ipk（opkg 系，OpenWrt <= 24.10），不需要 SDK
    ├── build-apk.sh               # 打包 .apk（apk-tools 3，OpenWrt 25.x），需要 OpenWrt SDK
    └── workbuddy2api/
        ├── Makefile               # 包定义（PKG_VERSION / PKG_RELEASE / 依赖 / postinst ...）
        └── files/                 # init.d、uci 配置、面板缓存预热脚本
```

默认两种包的内容一致：`wb_*.py` + `dashboard.html`（纯 Python 标准库）+
`/etc/init.d/workbuddy2api` + `/etc/config/workbuddy2api` +
`/usr/bin/workbuddy2api-warm`，不包含任何 CPU 架构相关文件，行为与此前相同。
显式加 `--with-bridge` 或 `--bridge-bin` 后，包内才额外出现三个 Go/uTLS
二进制：

```
/usr/lib/workbuddy2api/bin/wb-tls-bridge-linux-amd64
/usr/lib/workbuddy2api/bin/wb-tls-bridge-linux-arm64
/usr/lib/workbuddy2api/bin/wb-tls-bridge-linux-arm
```

含桥包同时安装以下许可证到 `/usr/share/licenses/workbuddy2api/`：

```
brotli.txt
compress.txt
utls.txt
x-crypto.txt
x-sys.txt
wb-tls-bridge-README.md    # 原项目 MIT 许可及版权声明
```

无桥包不创建该许可证目录。打包脚本在存在桥二进制但缺少上述任一文件时会直接
失败，避免生成许可证不完整的产物。

| 产物 | 适用系统 | 安装方式 |
| --- | --- | --- |
| `workbuddy2api_<版本>-<发布号>_all.ipk` | opkg 系（OpenWrt 24.10 及更早） | `opkg install <文件>` |
| `workbuddy2api-<版本>-r<发布号>.apk` | apk-tools 3（OpenWrt 25.x） | `apk add --allow-untrusted <文件>`（未签名时） |

文件名不带 `bridge` 标记；带桥构建会覆盖同名的无桥产物，CI 一次只应选择
其中一种构建。两种格式的产物分别写到 `wrt/ipk/` 与 `wrt/apk/`。

依赖（两套系统同名）：`python3-light`、`python3-urllib`、`python3-uuid`、
`python3-openssl`、`python3-sqlite3`、`python3-codecs`。OpenWrt 为了省空间把
ssl、sqlite3 和完整编码表拆成了独立包，缺任何一个都会在启动或首次解析域名时
报 `ModuleNotFoundError`。

## 可选 Go/uTLS 桥

桥从 Go 1.24 起可构建。打包脚本固定交叉编译下面三个目标，使用
`CGO_ENABLED=0` 生成静态二进制：

| 路由器 `uname -m` | Go 目标 | 随包文件 |
| --- | --- | --- |
| `x86_64` / `amd64` | `linux/amd64` | `wb-tls-bridge-linux-amd64` |
| `aarch64` / `arm64` | `linux/arm64` | `wb-tls-bridge-linux-arm64` |
| `armv7l` | `linux/arm`、`GOARM=7` | `wb-tls-bridge-linux-arm` |

**兼容边界**：`PKGARCH:=all` 只表示基础 Python 包仍可按通用包安装，不等于所有
CPU 都能使用 Go/uTLS。上述三种之外的架构（例如 mips、riscv64、armv6）没有
随包桥；即使打开 UCI 开关，init 也会写 syslog 告警并继续使用
Python/OpenSSL。不要对外宣称该可选构建“全硬件支持”。

新安装的 UCI 默认是 `auto`：

```sh
option tls_mode 'auto'
```

`auto` 会尝试启动与 CPU 匹配的随包桥；二进制缺失、架构不支持、启动失败
或桥运行中退出时，网关自动一次性回退 Python/OpenSSL，服务不需要重新配置。
只有需要强制要求桥时才使用 `node`：

```sh
uci set workbuddy2api.main.tls_mode='node'
uci commit workbuddy2api
/etc/init.d/workbuddy2api restart
logread -e workbuddy2api     # 看是否告警回退
```

如果确实要恢复旧版 Python/OpenSSL 行为：

```sh
uci set workbuddy2api.main.tls_mode='python'
uci commit workbuddy2api
/etc/init.d/workbuddy2api restart
```

旧配置没有 `tls_mode` 时，init 会把未启用旧 `tls_bridge` 的安装迁移为
`tls_mode=auto`；旧 `tls_bridge=1/on/true/yes/enabled/node` 迁移为
`tls_mode=node`。旧版无法区分“默认 0”和“曾主动关闭”，所以升级后想彻底
关闭桥的用户需要显式设置 `tls_mode=python`。迁移只写 TLS 键，不覆盖账号、
API Key、代理槽、usage 或其他设置。桥只监听回环地址，由网关进程拉起并通过
stdin 传认证密钥，不额外开放网络端口。

## 与 release 工作流的关系

`.github/workflows/release.yml` 在 `v*` tag 上跑：先校验 tag 与源码版本一致、
等同一提交的测试矩阵通过，然后依次构建便携 ZIP、`.ipk`、`.apk`，生成
`SHA256SUMS`，最后创建/更新 **Draft Release**（永不自动 publish）。
本目录的两个脚本就是它的第 4 步，工作流不重复实现打包逻辑。release 工作流会
显式传入预构建的三种 Linux 桥；本地不带桥构建时，运行时 `auto` 仍会在发现
缺桥后回退 Python/OpenSSL。

脚本不再从网络拉源码：`--app-src` 不给时默认用当前仓库检出，CI 直接把
`$GITHUB_WORKSPACE` 传进来。

## 本地构建

```sh
# 默认：包内不带桥，不需要 Go；运行时 auto 会在发现缺桥后回退 Python/OpenSSL
# .ipk：任意 POSIX sh + python3 即可，不需要 SDK（脚本内嵌打包器并自检）
sh wrt/openwrt/build-ipk.sh --app-src "$PWD"      # 产物在 wrt/ipk/

# .apk：需要 OpenWrt 25.x 的 x86_64 SDK（glibc 宿主机）
sh wrt/openwrt/build-apk.sh <SDK 目录> "$PWD"     # 产物在 wrt/apk/

# 可选：让脚本自行交叉编译并装入桥（两种包分别执行一次）
sh wrt/openwrt/build-ipk.sh --with-bridge --app-src "$PWD"
sh wrt/openwrt/build-apk.sh <SDK 目录> "$PWD" --with-bridge
```

CI 更推荐只编译一次桥，再把同一个目录交给两个打包脚本：

```sh
mkdir -p wrt/bridge
(
  cd tools/wb-tls-bridge
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -mod=readonly -trimpath -buildvcs=false -ldflags='-s -w' \
    -o ../../wrt/bridge/wb-tls-bridge-linux-amd64 .
  CGO_ENABLED=0 GOOS=linux GOARCH=arm64 \
    go build -mod=readonly -trimpath -buildvcs=false -ldflags='-s -w' \
    -o ../../wrt/bridge/wb-tls-bridge-linux-arm64 .
  CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 \
    go build -mod=readonly -trimpath -buildvcs=false -ldflags='-s -w' \
    -o ../../wrt/bridge/wb-tls-bridge-linux-arm .
)
sh wrt/openwrt/build-ipk.sh --bridge-bin "$PWD/wrt/bridge" --app-src "$PWD"
sh wrt/openwrt/build-apk.sh <SDK 目录> "$PWD" --bridge-bin "$PWD/wrt/bridge"
```

桥构建需要 Go 1.24+；`--bridge-bin` 要求目录按上表提供三个固定文件名，缺一个
都会拒绝打包，避免生成“看起来带桥、实际某架构不可用”的包。

## 升级版本

只改一处：`wrt/openwrt/workbuddy2api/Makefile` 的 `PKG_VERSION`（需要时
`PKG_RELEASE`）。两个脚本都会把源码 `wb_proxy.py` 的 `server_version` 与
`PKG_VERSION` 比对，`release.yml` 的 version job 还会把 tag 一起比对，
三者任一不一致就拒绝出包——宁可不发，也不发一个版本号对不上的包。

## 数据目录与持久化

- 程序文件在 `/usr/lib/workbuddy2api`，升级时整体替换；
- 账号凭据与用量在 `/etc/workbuddy2api/`（`postinst` 会把它加进
  `/etc/sysupgrade.conf`，系统升级不丢）；
- 配置在 `/etc/config/workbuddy2api`（`conffiles`，权限 600）；
- 首次安装不自动启用服务：`uci set workbuddy2api.main.panel_password='...'`、
  `uci commit`，再 `enable` + `start`。

`postinst` 只装一条 cron：`workbuddy2api-warm` 每 2 分钟预热面板的统计缓存
（`uci main.warm=0` 可关），服务每次启动后也会在后台先预热一轮。init.d 把
`WB_STATS_TTL` 默认调到 900 秒，配合预热器让面板点开即出数；这两个默认值是
配套的，改一个要一起看。

## 审计结论（相对 #190 的参考配方）

保留：`.ipk`/`.apk` 两个打包脚本、包 Makefile、init.d、uci 配置、缓存预热脚本，
以及 `.ipk` 的可复现构建约定（GNU tar + `SOURCE_DATE_EPOCH`，同一份源码逐字节相同）。

去掉：

- **`files/usr/bin/workbuddy2api-update` 及其 cron / `auto_update` 选项**：那是
  fork 自带的 GitHub 自更新器，属于 #190 里的 fork 专属自动化，本 issue 明确
  不引入；OpenWrt 安装本来就走包管理器升级（#29 也把包管理器安装排除在自更新
  之外）。去掉后 `postrm` 只清 `workbuddy2api-warm`。
- **`wrt/ci/activate-workflows.py` 与 `wrt-sync-upstream.yml`**：fork 用来把
  工作流塞进 `.github/workflows/` 和每天 merge 上游的机制，与本仓库无关。
- **`PIN_SHA` / `PIN_VER` 钉死上游 commit**：配方进入上游仓库后，"从 GitHub 拉
  另一个 commit 的上游源码"没有意义，版本改为直接取自当前检出的 `wb_proxy.py`。
- `PKG_MAINTAINER` 由 `aodianjun` 改回仓库维护者。
