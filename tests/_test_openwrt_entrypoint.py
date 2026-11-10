"""OpenWrt packaging must default to auto, with Python as the safe fallback.

`python3 wb_proxy.py` runs the file as `__main__`; measured idle RSS is
47-54 MB higher than `python3 -m wb_proxy` on every platform sampled so far
(Windows 11 / aarch64, Python 3.12-3.14, issue #702). The package's init
script is the one launcher a router user cannot reasonably fix by hand, so
pin the entry mode and the PYTHONPATH wiring here.

`procd_set_param env` replaces the whole env list instead of appending to it,
so PYTHONPATH has to share a single line with the existing variables.

The optional Go/uTLS bundle covers exactly linux/amd64, linux/arm64 and
linux/arm(GOARM=7). A missing binary or any other CPU must stay on
Python/OpenSSL, so the tests also pin the UCI default, fallback notice and
packaging flags.

Run with: python tests/_test_openwrt_entrypoint.py
"""
import io
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INIT = os.path.join(ROOT, "wrt", "openwrt", "workbuddy2api",
                    "files", "etc", "init.d", "workbuddy2api")
CONFIG = os.path.join(ROOT, "wrt", "openwrt", "workbuddy2api",
                      "files", "etc", "config", "workbuddy2api")
MAKEFILE = os.path.join(ROOT, "wrt", "openwrt", "workbuddy2api", "Makefile")
BUILD_IPK = os.path.join(ROOT, "wrt", "openwrt", "build-ipk.sh")
BUILD_APK = os.path.join(ROOT, "wrt", "openwrt", "build-apk.sh")
README = os.path.join(ROOT, "wrt", "README.md")
BRIDGE = os.path.join(ROOT, "tools", "wb-tls-bridge")
BRIDGE_LICENSE_NAMES = (
    "brotli.txt",
    "compress.txt",
    "utls.txt",
    "x-crypto.txt",
    "x-sys.txt",
)


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def posix_sh():
    found = shutil.which("sh")
    if found:
        return found
    if os.name == "nt":
        for path in (r"C:\Program Files\Git\bin\sh.exe",
                     r"C:\Program Files\Git\usr\bin\sh.exe"):
            if os.path.isfile(path):
                return path
    return None


SH_DRIVER = textwrap.dedent(r"""
    set -eu
    INIT=$1
    UCI_LOG=$2
    PROCD_LOG=$3
    LOGGER_LOG=$4
    CFG_TLS_MODE=$5
    CFG_TLS_BRIDGE=$6
    CFG_MACHINE=$7

    config_load() { :; }
    config_get() {
        _target=$1
        _section=$2
        _option=$3
        _default=$4
        case "$_option" in
            tls_mode) _value=$CFG_TLS_MODE ;;
            tls_bridge) _value=$CFG_TLS_BRIDGE ;;
            host) _value=127.0.0.1 ;;
            port) _value=8788 ;;
            accounts_dir) _value=/tmp/workbuddy2api-test-accounts ;;
            usage_dir) _value=/tmp/workbuddy2api-test-usage ;;
            stats_ttl) _value=900 ;;
            *) _value=$_default ;;
        esac
        eval "$_target=\$_value"
    }
    uci() { printf '%s\n' "$*" >> "$UCI_LOG"; }
    logger() { printf '%s\n' "$*" >> "$LOGGER_LOG"; }
    uname() { printf '%s\n' "$CFG_MACHINE"; }
    mkdir() { :; }
    chmod() { :; }
    procd_open_instance() { :; }
    procd_close_instance() { :; }
    procd_set_param() {
        printf 'CALL'
        for _arg in "$@"; do printf '|%s' "$_arg"; done
        printf '\n'
    } >> "$PROCD_LOG"
    procd_append_param() {
        printf 'APPEND'
        for _arg in "$@"; do printf '|%s' "$_arg"; done
        printf '\n'
    } >> "$PROCD_LOG"

    . "$INIT"
    start_service
""")


def simulate_start(tls_mode="", tls_bridge="", machine="x86_64"):
    sh = posix_sh()
    if not sh:
        raise unittest.SkipTest("POSIX sh unavailable")
    with tempfile.TemporaryDirectory(prefix="wb-openwrt-sh-") as tmp:
        harness = os.path.join(tmp, "harness.sh")
        uci_log = os.path.join(tmp, "uci.log")
        procd_log = os.path.join(tmp, "procd.log")
        logger_log = os.path.join(tmp, "logger.log")
        with io.open(harness, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(SH_DRIVER)
        for path in (uci_log, procd_log, logger_log):
            with io.open(path, "w", encoding="utf-8"):
                pass

        def shell_path(path):
            return path.replace("\\", "/")

        argv = [
            sh,
            shell_path(harness),
            shell_path(INIT),
            shell_path(uci_log),
            shell_path(procd_log),
            shell_path(logger_log),
            tls_mode,
            tls_bridge,
            machine,
        ]
        proc = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=20)
        if proc.returncode:
            raise AssertionError(
                "OpenWrt init simulation failed (%d): %s\n%s"
                % (proc.returncode,
                   proc.stdout.decode("utf-8", "replace"),
                   proc.stderr.decode("utf-8", "replace")))
        return {
            "uci": read(uci_log).splitlines(),
            "procd": read(procd_log).splitlines(),
            "logger": read(logger_log).splitlines(),
        }


def env_from_procd(lines):
    for line in lines:
        parts = line.split("|")
        if len(parts) >= 2 and parts[0] == "CALL" and parts[1] == "env":
            env = {}
            for item in parts[2:]:
                name, _sep, value = item.partition("=")
                env[name] = value
            return env
    raise AssertionError("procd env call not found: %r" % (lines,))


class OpenWrtEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.text = read(INIT)

    def test_starts_as_module(self):
        self.assertIn("-m wb_proxy", self.text)

    def test_does_not_pass_the_script_path(self):
        self.assertNotIn('python3 "$PROG"', self.text)

    def test_pythonpath_shares_the_single_env_line(self):
        env_lines = [line for line in self.text.splitlines()
                     if "procd_set_param env" in line]
        self.assertEqual(len(env_lines), 1)
        self.assertIn('PYTHONPATH="$PROG_DIR"', env_lines[0])
        self.assertIn("WB_STATS_TTL", env_lines[0])
        self.assertIn('WB_TLS_FINGERPRINT="$tls_fingerprint"', env_lines[0])
        self.assertIn('WB_TLS_BRIDGE="$tls_bridge"', env_lines[0])

    def test_bridge_switch_only_changes_an_explicit_setting(self):
        self.assertIn("config_get tls_mode_setting main tls_mode ''", self.text)
        self.assertIn("config_get tls_bridge_setting main tls_bridge ''", self.text)
        self.assertIn("case \"$tls_mode_setting\" in", self.text)
        self.assertIn("1|on|true|yes|enabled|node", self.text)
        self.assertIn("tls_mode_setting=auto", self.text)
        self.assertIn("tls_fingerprint=auto", self.text)
        self.assertIn("tls_fingerprint=python", self.text)
        self.assertIn("tls_bridge=off", self.text)
        self.assertIn('uci -q set workbuddy2api.main.tls_mode="$tls_mode_setting"',
                      self.text)

    def test_legacy_tls_bridge_migrates_without_touching_data(self):
        self.assertIn("tls_mode_setting=node", self.text)
        self.assertIn("旧 tls_bridge 已启用", self.text)
        self.assertIn("tls_mode=python", self.text)
        self.assertNotIn("uci set workbuddy2api.main.tls_bridge", self.text)
        self.assertNotIn("uci commit workbuddy2api.main", self.text)

    def test_supported_cpu_names_select_the_exact_bundled_binaries(self):
        self.assertIn("x86_64|amd64) bridge_binary=\"$PROG_DIR/bin/wb-tls-bridge-linux-amd64\"", self.text)
        self.assertIn("aarch64|arm64) bridge_binary=\"$PROG_DIR/bin/wb-tls-bridge-linux-arm64\"", self.text)
        self.assertIn("armv7l) bridge_binary=\"$PROG_DIR/bin/wb-tls-bridge-linux-arm\"", self.text)

    def test_unsupported_cpu_falls_back_with_a_visible_notice(self):
        self.assertIn('[ -z "$bridge_binary" ] || [ ! -x "$bridge_binary" ]',
                      self.text)
        self.assertIn("logger -t workbuddy2api", self.text)
        self.assertIn("回退 Python/OpenSSL", self.text)

    def test_init_passes_posix_sh_syntax(self):
        sh = posix_sh()
        if not sh:
            self.skipTest("POSIX sh unavailable")
        proc = subprocess.run(
            [sh, "-n", INIT.replace("\\", "/")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(proc.returncode, 0,
                         proc.stderr.decode("utf-8", "replace"))

    def test_new_install_uses_auto_without_migration(self):
        result = simulate_start(tls_mode="auto")
        env = env_from_procd(result["procd"])
        self.assertEqual(env["WB_TLS_FINGERPRINT"], "auto")
        self.assertEqual(env["WB_TLS_BRIDGE"], "auto")
        self.assertEqual(result["uci"], [])

    def test_legacy_bridge_off_migrates_to_auto(self):
        result = simulate_start(tls_mode="", tls_bridge="0")
        env = env_from_procd(result["procd"])
        self.assertEqual(env["WB_TLS_FINGERPRINT"], "auto")
        self.assertEqual(env["WB_TLS_BRIDGE"], "auto")
        self.assertEqual(result["uci"], [
            "-q set workbuddy2api.main.tls_mode=auto",
            "-q commit workbuddy2api",
        ])

    def test_legacy_bridge_on_migrates_to_node(self):
        result = simulate_start(tls_mode="", tls_bridge="1")
        env = env_from_procd(result["procd"])
        self.assertEqual(env["WB_TLS_FINGERPRINT"], "node")
        self.assertEqual(env["WB_TLS_BRIDGE"], "auto")
        self.assertEqual(result["uci"], [
            "-q set workbuddy2api.main.tls_mode=node",
            "-q commit workbuddy2api",
        ])

    def test_existing_mode_is_not_overwritten(self):
        result = simulate_start(tls_mode="python", tls_bridge="1")
        env = env_from_procd(result["procd"])
        self.assertEqual(env["WB_TLS_FINGERPRINT"], "python")
        self.assertEqual(env["WB_TLS_BRIDGE"], "off")
        self.assertEqual(result["uci"], [])

    def test_unknown_mode_falls_back_to_auto_with_a_notice(self):
        result = simulate_start(tls_mode="mystery")
        env = env_from_procd(result["procd"])
        self.assertEqual(env["WB_TLS_FINGERPRINT"], "auto")
        self.assertEqual(env["WB_TLS_BRIDGE"], "auto")
        self.assertEqual(result["uci"], [])
        self.assertTrue(any("未知 tls_mode" in line
                            for line in result["logger"]), result["logger"])

    def test_unsupported_cpu_keeps_auto_and_logs_fallback(self):
        result = simulate_start(tls_mode="auto", machine="mips")
        env = env_from_procd(result["procd"])
        self.assertEqual(env["WB_TLS_FINGERPRINT"], "auto")
        self.assertEqual(env["WB_TLS_BRIDGE"], "auto")
        self.assertTrue(any("回退 Python/OpenSSL" in line
                            for line in result["logger"]), result["logger"])


class OpenWrtPackageTests(unittest.TestCase):
    def test_uci_default_is_auto(self):
        config = read(CONFIG)
        self.assertIn("option tls_mode 'auto'", config)
        self.assertIn("x86_64 / aarch64 / armv7l", config)
        self.assertIn("其他架构在 auto 下自动回退", config)

    def test_makefile_installs_bridge_only_when_files_exist(self):
        makefile = read(MAKEFILE)
        self.assertIn("PKGARCH:=all", makefile)
        self.assertIn(
            "BRIDGE_BINS:=$(wildcard $(CURDIR)/files/usr/lib/workbuddy2api/bin/wb-tls-bridge-linux-*)",
            makefile)
        self.assertIn('[ -z "$(BRIDGE_BINS)" ]', makefile)
        self.assertIn("$(INSTALL_BIN) $(BRIDGE_BINS)", makefile)

    def test_both_packers_have_an_opt_in_bridge_mode(self):
        for path in (BUILD_IPK, BUILD_APK):
            text = read(path)
            self.assertIn("BRIDGE_MODE=none", text)
            self.assertIn("--with-bridge", text)
            self.assertIn("--bridge-bin", text)
            self.assertIn("wb-tls-bridge-linux-amd64", text)
            self.assertIn("wb-tls-bridge-linux-arm64", text)
            self.assertIn("wb-tls-bridge-linux-arm", text)
            self.assertIn("CGO_ENABLED=0 GOOS=linux GOARCH=amd64", text)
            self.assertIn("CGO_ENABLED=0 GOOS=linux GOARCH=arm64", text)
            self.assertIn("CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7", text)

    def test_bridge_license_sources_are_complete(self):
        for name in BRIDGE_LICENSE_NAMES:
            path = os.path.join(BRIDGE, "licenses", name)
            self.assertTrue(os.path.getsize(path) > 0, path)
        bridge_readme = read(os.path.join(BRIDGE, "README.md"))
        self.assertIn("MIT License", bridge_readme)
        self.assertIn("Copyright (c) 2026 Sliverkiss", bridge_readme)

    def test_both_packers_require_and_install_the_license_bundle(self):
        for path in (BUILD_IPK, BUILD_APK):
            text = read(path)
            self.assertIn("check_bridge_licenses()", text)
            self.assertIn(
                'BRIDGE_LICENSE_NAMES="brotli.txt compress.txt utls.txt x-crypto.txt x-sys.txt"',
                text)
            self.assertIn("usr/share/licenses/workbuddy2api", text)
            self.assertIn("$_license", text)
            self.assertIn("wb-tls-bridge-README.md", text)

    def test_makefile_rejects_a_bridge_without_all_six_license_files(self):
        makefile = read(MAKEFILE)
        for name in BRIDGE_LICENSE_NAMES + ("wb-tls-bridge-README.md",):
            self.assertIn(name, makefile)
        self.assertIn("ifneq ($(words $(BRIDGE_LICENSES)),6)", makefile)
        self.assertIn("$(INSTALL_DATA) $(BRIDGE_LICENSES)", makefile)
        self.assertIn("$(1)/usr/share/licenses/workbuddy2api", makefile)

    def test_apk_stages_source_outside_the_worktree(self):
        text = read(BUILD_APK)
        self.assertIn('SDK_PKG="$SDK/package/workbuddy2api"', text)
        self.assertIn('DST="$SDK_PKG/files/usr/lib/workbuddy2api"', text)
        self.assertNotIn('DST="$REPO/openwrt/workbuddy2api/files/usr/lib/workbuddy2api"', text)

    def test_ipk_normalizes_cross_platform_file_modes(self):
        text = read(BUILD_IPK)
        self.assertIn("ti.mode = 0o755 if executable", text)
        self.assertIn('"/bin/wb-tls-bridge-linux-" in name', text)
        self.assertIn('0o600 if name == "./etc/config/workbuddy2api"', text)

    def test_readme_states_the_real_support_boundary(self):
        text = read(README)
        self.assertIn("| `x86_64` / `amd64` | `linux/amd64`", text)
        self.assertIn("| `aarch64` / `arm64` | `linux/arm64`", text)
        self.assertIn("| `armv7l` | `linux/arm`、`GOARM=7`", text)
        self.assertIn("不等于所有", text)
        self.assertIn("mips、riscv64、armv6", text)
        self.assertIn("Python/OpenSSL", text)
        self.assertIn("/usr/share/licenses/workbuddy2api/", text)
        self.assertIn("wb-tls-bridge-README.md", text)


if __name__ == "__main__":
    unittest.main()
