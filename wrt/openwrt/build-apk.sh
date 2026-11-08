#!/bin/sh
# ============================================================================
# 一条命令构建 workbuddy2api 的 .apk 安装包
#
# 用法：
#   sh wrt/openwrt/build-apk.sh <SDK 目录> [应用源码目录]
#                               [--with-bridge | --bridge-bin <目录>]
#
#   <SDK 目录>      OpenWrt 25.12.x SDK（例：openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64）
#   [应用源码目录]  含 wb_*.py + dashboard.html 的目录（可选，默认本仓库检出）。
#
# 做的事：
#   1) 准备应用源码，校验版本号与 Makefile 的 PKG_VERSION 一致
#   2) 按需构建/复用三个 linux Go 桥二进制（不指定则完全不打包桥）
#   3) 只把 wb_*.py + dashboard.html（以及可选桥）暂存到 SDK 包目录
#   4) make package/workbuddy2api/compile（-j1，低配机器/容器上更稳）
#   5) 产物拷到 wrt/apk/workbuddy2api-<版本>-r<发布号>.apk 并打印 sha256
#
# 注意：构建机必须是 glibc 的 x86_64 Linux（SDK 自带宿主工具是 glibc 二进制，
# 不能在 musl 的 OpenWrt 上直接跑，也不能在 Windows 上跑）。CI 用 ubuntu-latest。
# ============================================================================
set -e

usage() {
	cat <<'EOF'
用法: sh wrt/openwrt/build-apk.sh <OpenWrt SDK 目录> [应用源码目录]
                                  [--with-bridge | --bridge-bin <目录>]

  --with-bridge    用 Go 交叉编译 linux/amd64、linux/arm64、linux/arm(GOARM=7)
                   并放进包内；需要 Go 1.24+ 与 tools/wb-tls-bridge。
  --bridge-bin <目录>
                   直接复用目录下三个约定文件名的桥二进制，不重新编译。
  -h, --help       显示本帮助

不指定桥选项时，产物与仅含 Python/OpenSSL 的旧包一致。
EOF
}

SDK=""
APP_SRC=""
BRIDGE_MODE=none
BRIDGE_INPUT=""
while [ $# -gt 0 ]; do
	case "$1" in
		--with-bridge)
			[ "$BRIDGE_MODE" = none ] || { echo "错误: --with-bridge 与 --bridge-bin 只能选一个" >&2; exit 1; }
			BRIDGE_MODE=build
			shift
			;;
		--bridge-bin)
			[ -n "$2" ] || { echo "错误: --bridge-bin 需要一个目录参数" >&2; exit 1; }
			[ "$BRIDGE_MODE" = none ] || { echo "错误: --with-bridge 与 --bridge-bin 只能选一个" >&2; exit 1; }
			BRIDGE_MODE=input
			BRIDGE_INPUT="$2"
			shift 2
			;;
		--bridge-bin=*)
			[ "$BRIDGE_MODE" = none ] || { echo "错误: --with-bridge 与 --bridge-bin 只能选一个" >&2; exit 1; }
			BRIDGE_MODE=input
			BRIDGE_INPUT="${1#--bridge-bin=}"
			shift
			;;
		-h|--help) usage; exit 0 ;;
		--*) echo "错误: 未知参数 '$1'" >&2; usage >&2; exit 1 ;;
		*)
			if [ -z "$SDK" ]; then
				SDK="$1"
			elif [ -z "$APP_SRC" ]; then
				APP_SRC="$1"
			else
				echo "错误: 多余的位置参数 '$1'" >&2; usage >&2; exit 1
			fi
			shift
			;;
	esac
done
[ -n "$SDK" ] || { usage >&2; exit 1; }
[ -f "$SDK/rules.mk" ] || { echo "错误: $SDK 不像 OpenWrt SDK 目录（找不到 rules.mk）"; exit 1; }

# REPO 是 wrt/ 目录（本脚本住在 wrt/openwrt/ 下），仓库根在它上一层。
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$REPO/.." && pwd)"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/build-apk.XXXXXX")
trap 'rm -rf "$WORK"' EXIT INT TERM
BRIDGE_SRC="$ROOT/tools/wb-tls-bridge"
BRIDGE_LICENSE_NAMES="brotli.txt compress.txt utls.txt x-crypto.txt x-sys.txt"

# mk_var <makefile> <VAR> -> 变量值（取第一处赋值，与 build-ipk.sh 同一约定）
mk_var() {
	sed -n "s/^[[:space:]]*$2:=[[:space:]]*//p" "$1" | sed -n 1p
}

check_bridge_licenses() {
	[ -s "$BRIDGE_SRC/README.md" ] || {
		echo "错误: 含桥包缺少原项目许可 $BRIDGE_SRC/README.md" >&2; exit 1; }
	for _license in $BRIDGE_LICENSE_NAMES; do
		[ -s "$BRIDGE_SRC/licenses/$_license" ] || {
			echo "错误: 含桥包缺少依赖许可证 licenses/$_license" >&2; exit 1; }
	done
}

# prepare_bridge_bins <输出目录>
#   三个文件名必须与 wb_upstream_pool._tls_bridge_candidates() 的命名一致。
#   CGO_ENABLED=0 让二进制静态链接，不绑定 OpenWrt 的 glibc/musl。
prepare_bridge_bins() {
	_out="$1"
	check_bridge_licenses
	mkdir -p "$_out"
	if [ "$BRIDGE_MODE" = build ]; then
		command -v go >/dev/null 2>&1 || {
			echo "错误: --with-bridge 需要 Go 1.24+（PATH 里找不到 go）" >&2; exit 1; }
		[ -f "$BRIDGE_SRC/go.mod" ] && [ -f "$BRIDGE_SRC/main.go" ] || {
			echo "错误: 找不到桥源码 $BRIDGE_SRC" >&2; exit 1; }
		echo "    交叉编译 Go/uTLS bridge（linux/amd64、linux/arm64、linux/arm GOARM=7）"
		(
			cd "$BRIDGE_SRC"
			CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
				go build -mod=readonly -trimpath -buildvcs=false -ldflags='-s -w' \
				-o "$_out/wb-tls-bridge-linux-amd64" .
			CGO_ENABLED=0 GOOS=linux GOARCH=arm64 \
				go build -mod=readonly -trimpath -buildvcs=false -ldflags='-s -w' \
				-o "$_out/wb-tls-bridge-linux-arm64" .
			CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 \
				go build -mod=readonly -trimpath -buildvcs=false -ldflags='-s -w' \
				-o "$_out/wb-tls-bridge-linux-arm" .
		)
	else
		[ -d "$BRIDGE_INPUT" ] || {
			echo "错误: --bridge-bin 目录不存在: $BRIDGE_INPUT" >&2; exit 1; }
		for _name in \
			wb-tls-bridge-linux-amd64 \
			wb-tls-bridge-linux-arm64 \
			wb-tls-bridge-linux-arm
		do
			[ -s "$BRIDGE_INPUT/$_name" ] || {
				echo "错误: --bridge-bin 缺少非空文件 $_name" >&2; exit 1; }
			cp "$BRIDGE_INPUT/$_name" "$_out/$_name"
		done
	fi
	for _name in \
		wb-tls-bridge-linux-amd64 \
		wb-tls-bridge-linux-arm64 \
		wb-tls-bridge-linux-arm
	do
		[ -s "$_out/$_name" ] || {
			echo "错误: 桥二进制构建失败: $_name" >&2; exit 1; }
		chmod 0755 "$_out/$_name"
	done
}

# ---- 1. 应用源码 ------------------------------------------------------------

if [ -z "$APP_SRC" ]; then
	APP_SRC="$ROOT"
	echo "==> 1/5 未指定应用源码目录，按仓库检出取: $APP_SRC"
fi

[ -f "$APP_SRC/wb_proxy.py" ] || {
	echo "错误: $APP_SRC 里没有 wb_proxy.py（应用源码目录应指向含 wb_*.py 的仓库根）" >&2; exit 1; }
[ -f "$APP_SRC/dashboard.html" ] || { echo "错误: $APP_SRC 里没有 dashboard.html" >&2; exit 1; }

DETECTED_VER="$(sed -n 's/.*server_version = "wb-proxy\/\([0-9.]*\)".*/\1/p' "$APP_SRC/wb_proxy.py" | head -n1)"
MAKE_VER="$(mk_var "$REPO/openwrt/workbuddy2api/Makefile" PKG_VERSION)"
PKG_REL="$(mk_var "$REPO/openwrt/workbuddy2api/Makefile" PKG_RELEASE)"
echo "    源码版本: ${DETECTED_VER:-未知}    Makefile: $MAKE_VER-r${PKG_REL:-?}"
[ -n "$DETECTED_VER" ] || { echo "错误: 从 $APP_SRC/wb_proxy.py 解析不出 server_version" >&2; exit 1; }
[ "$DETECTED_VER" = "$MAKE_VER" ] || {
	echo "错误: 源码版本（$DETECTED_VER）与 Makefile PKG_VERSION（${MAKE_VER:-未知}）不一致。" >&2
	echo "      升级时请把 wrt/openwrt/workbuddy2api/Makefile 的 PKG_VERSION 改成 $DETECTED_VER。" >&2
	exit 1
}

# ---- 2. 可选 Go/uTLS 桥 -----------------------------------------------------

BRIDGE_DIR=""
echo "==> 2/5 准备可选 Go/uTLS 桥"
if [ "$BRIDGE_MODE" = none ]; then
	echo "    未包含（默认 Python/OpenSSL）"
else
	BRIDGE_DIR="$WORK/bridge-bin"
	prepare_bridge_bins "$BRIDGE_DIR"
	echo "    已准备 linux/amd64、linux/arm64、linux/arm(GOARM=7)"
fi

# ---- 3. 暂存进 SDK 包目录 ---------------------------------------------------

echo "==> 3/5 暂存应用源码进 SDK 包目录（只带 wb_*.py + dashboard.html + 可选桥）"
[ -d "$SDK/package" ] || { echo "错误: SDK 缺少 package/ 目录: $SDK" >&2; exit 1; }
SDK_PKG="$SDK/package/workbuddy2api"
rm -rf "$SDK_PKG"
cp -R "$REPO/openwrt/workbuddy2api" "$SDK_PKG"
DST="$SDK_PKG/files/usr/lib/workbuddy2api"
rm -rf "$DST"
mkdir -p "$DST"
for f in "$APP_SRC"/wb_*.py; do
	cp "$f" "$DST/"
done
cp "$APP_SRC/dashboard.html" "$DST/"
if [ -n "$BRIDGE_DIR" ]; then
	mkdir -p "$DST/bin"
	for _name in \
		wb-tls-bridge-linux-amd64 \
		wb-tls-bridge-linux-arm64 \
		wb-tls-bridge-linux-arm
	do
		cp "$BRIDGE_DIR/$_name" "$DST/bin/$_name"
		chmod 0755 "$DST/bin/$_name"
	done
	LICENSE_DST="$SDK_PKG/files/usr/share/licenses/workbuddy2api"
	mkdir -p "$LICENSE_DST"
	for _license in $BRIDGE_LICENSE_NAMES; do
		cp "$BRIDGE_SRC/licenses/$_license" "$LICENSE_DST/$_license"
		chmod 0644 "$LICENSE_DST/$_license"
	done
	cp "$BRIDGE_SRC/README.md" "$LICENSE_DST/wb-tls-bridge-README.md"
	chmod 0644 "$LICENSE_DST/wb-tls-bridge-README.md"
fi
find "$DST" -type f | wc -l | xargs echo "    文件数:"

# ---- 4. 编译 ----------------------------------------------------------------

echo "==> 4/5 编译"
cd "$SDK"
[ -f .config ] || make defconfig
make -j1 package/workbuddy2api/compile V=s

# ---- 5. 收集产物 ------------------------------------------------------------

echo "==> 5/5 收集产物到 $REPO/apk/"
mkdir -p "$REPO/apk"
OUT_APK="$(find "$SDK/bin" -name 'workbuddy2api-*.apk' | head -n1)"
[ -n "$OUT_APK" ] || { echo "错误: 没找到 workbuddy2api 的 .apk 产物"; exit 1; }
# 维护者约定的资产名（release 工作流直接按这个名字上传，不再改名）
OUT_NAME="workbuddy2api-${DETECTED_VER}-r${PKG_REL}.apk"
cp "$OUT_APK" "$REPO/apk/$OUT_NAME"
echo "    $OUT_APK"
echo "    -> apk/$OUT_NAME"

if [ "$BRIDGE_MODE" = none ]; then
	echo "==> 完成（仅 Python/OpenSSL）"
else
	echo "==> 完成（含三个 Linux Go/uTLS 桥目标）"
fi
sha256sum "$REPO/apk/$OUT_NAME"
