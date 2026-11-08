"""`start-workbuddy-all.bat` must use the gateway health probe.

`Get-NetTCPConnection -State Listen` only proves that *something* owns the
port. A wedged gateway or a foreign service passes that check and the workbench
then opens against a dead endpoint. Pin the launcher to the existing
`--probe-only` contract (0 = healthy, 3 = free, 1 = foreign/wedged) and keep
the launcher in the portable release.

Run with: python tests/_test_workbuddy_all_launcher.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PASS = 0
FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ok   %s" % label)
    else:
        FAIL += 1
        print("  FAIL %s%s" % (label, (" -> " + detail) if detail else ""))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


launcher = read("start-workbuddy-all.bat")
lower = launcher.lower()
portable = [line.strip() for line in read("release", "portable.txt").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
readme = read("README.md")

check("launcher is ASCII-only", all(ord(ch) < 128 for ch in launcher))
check("launcher uses --probe-only before starting",
      lower.count("--probe-only") >= 2, "expected pre-start and readiness probes")
check("launcher no longer treats a bare listener as healthy",
      "get-nettcpconnection" not in lower)
check("launcher handles the free-port exit code",
      "if errorlevel 3 goto port_free" in lower)
check("launcher rejects foreign or wedged holders",
      ":probe_failed" in lower and "not a healthy" in lower)
check("launcher ships in the portable release",
      "start-workbuddy-all.bat" in portable)
check("README documents the launcher",
      "start-workbuddy-all.bat" in readme and "probe-only" in readme)

print()
print("SUMMARY: PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
