"""OpenWrt packaging must keep Python as the default and make uTLS opt-in.

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
        self.assertIn("config_get tls_bridge_setting main tls_bridge '0'", self.text)
        self.assertIn("case \"$tls_bridge_setting\" in", self.text)
        self.assertIn("1|on|true|yes|enabled|node", self.text)
        self.assertIn("tls_fingerprint=python", self.text)
        self.assertIn("tls_bridge=off", self.text)

    def test_supported_cpu_names_select_the_exact_bundled_binaries(self):
        self.assertIn("x86_64|amd64) bridge_binary=\"$PROG_DIR/bin/wb-tls-bridge-linux-amd64\"", self.text)
        self.assertIn("aarch64|arm64) bridge_binary=\"$PROG_DIR/bin/wb-tls-bridge-linux-arm64\"", self.text)
        self.assertIn("armv7l) bridge_binary=\"$PROG_DIR/bin/wb-tls-bridge-linux-arm\"", self.text)

    def test_unsupported_cpu_falls_back_with_a_visible_notice(self):
        self.assertIn('[ -n "$bridge_binary" ] && [ -x "$bridge_binary" ]', self.text)
        self.assertIn("logger -t workbuddy2api", self.text)
        self.assertIn("继续使用 Python/OpenSSL", self.text)


class OpenWrtPackageTests(unittest.TestCase):
    def test_uci_default_keeps_the_bridge_disabled(self):
        config = read(CONFIG)
        self.assertIn("option tls_bridge '0'", config)
        self.assertIn("x86_64 / aarch64 / armv7l", config)
        self.assertIn("其他架构会记录告警并保持 Python", config)

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
