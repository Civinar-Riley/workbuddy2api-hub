"""Statistics timezone: optional fixed UTC+8 ("Beijing time") for every
human-facing time the gateway shows or buckets.

Off by default. With the switch off every helper here is a straight pass-through
to `time.localtime` / `time.mktime` / `time.strftime`, so the process behaves
byte-for-byte like upstream and an existing install's day/hour buckets, log
timestamps, limit-reset clocks and export stamps all keep their current meaning.

Switched on, the same helpers compute against a fixed +08:00 offset instead of
the host timezone. That is deliberately *not* `TZ=Asia/Shanghai` + `tzset()`:
China has had no daylight saving since 1991, so the offset is a constant, and a
constant offset needs no tz database. That matters on the platforms this gateway
runs on - Windows has no `time.tzset()` at all, and musl/OpenWrt images often
ship without zoneinfo - where an IANA name silently degrades to UTC and would
make "Beijing time" quietly wrong.

Two rules keep the switch a single decision point:

  * Callers never compare `time.tzname` or branch on the setting themselves.
    They call the helpers here, which read the flag on every call.
  * The flag is set once at startup from settings.json and re-applied when the
    panel saves it (`configure`), never cached in a module constant, so flipping
    the switch takes effect on the next formatted row without a restart.

The day-bucket memo in wb_proxy keys its invalidation off `time.tzname`; because
this module never calls `tzset()`, that tuple never changes, so `configure` must
also clear the memo explicitly (see wb_proxy.configure_timezone).
"""

import calendar
import time

# Fixed offset of the statistics timezone, in seconds. China Standard Time is
# UTC+8 all year round (no DST since 1991).
BEIJING_OFFSET = 8 * 3600

_ENABLED = False


def enabled():
    """Whether statistics use the fixed +08:00 timezone instead of the host's."""
    return _ENABLED


def set_enabled(value):
    """Flip the switch. Returns the stored boolean.

    Idempotent and cheap: every helper reads the flag per call, so no cached
    value has to be invalidated here beyond the day-bucket memo the caller
    owns.
    """
    global _ENABLED
    _ENABLED = bool(value)
    return _ENABLED


def localtime(seconds=None):
    """`time.struct_time` in the statistics timezone.

    With the switch off this is exactly `time.localtime(seconds)` (including
    the default-argument behaviour when `seconds` is None). With it on, the
    epoch is shifted by +08:00 and read back through `time.gmtime`, which is
    the same wall clock Beijing shows and, unlike `mktime`, has no DST or
    host-timezone dependency.
    """
    if seconds is None:
        # 显式取 time.time() 再传给 localtime：`time.localtime()` 不带参数时
        # 直接读 C 层时钟，绕开被替换过的 time.time（测试冻结时钟就靠这一层）。
        seconds = time.time()
    if not _ENABLED:
        return time.localtime(seconds)
    return time.gmtime(seconds + BEIJING_OFFSET)


def mktime(struct):
    """Inverse of :func:`localtime`: a 9-tuple wall clock -> epoch seconds.

    With the switch off this is `time.mktime`, so a tuple the caller built from
    `time.localtime` round-trips exactly as before (including the platform's
    own out-of-range normalisation, e.g. day 0 -> last day of the previous
    month). With it on, `calendar.timegm` interprets the tuple as UTC and the
    +08:00 offset is subtracted back out - no host timezone, no DST.

    `calendar.timegm` also normalises out-of-range fields the same way
    `mktime` does, so callers that rely on "day - 1" or "month + 1" keep
    working unchanged.
    """
    if not _ENABLED:
        return time.mktime(struct)
    return calendar.timegm(tuple(struct)) - BEIJING_OFFSET


def strftime(fmt, struct=None):
    """Format a struct_time (default: now) in the statistics timezone."""
    if struct is None:
        struct = localtime()
    return time.strftime(fmt, struct)


def day_key(seconds):
    """The calendar day `seconds` falls on, "YYYY-MM-DD", in the statistics tz."""
    return strftime("%Y-%m-%d", localtime(seconds))


def midnight(seconds=None, days_back=0):
    """Epoch of the statistics-timezone midnight `days_back` days before `seconds`.

    Same construction as the pre-existing `_local_midnight`: read the wall
    clock, zero the clock, step the day back, and let `mktime` normalise an
    out-of-range day (so stepping past the 1st still lands on a real midnight).
    """
    lt = localtime(seconds)
    return mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - days_back,
                   0, 0, 0, 0, 0, -1))


def hour_start(seconds):
    """Epoch of the statistics-timezone whole hour `seconds` falls in."""
    lt = localtime(seconds)
    return mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour,
                   0, 0, 0, 0, -1))


def month_start(seconds=None):
    """Epoch of the statistics-timezone first day of `seconds`'s month, 00:00."""
    lt = localtime(seconds)
    return mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def iso_seconds(seconds):
    """'YYYY-MM-DD HH:MM:SS' in the statistics timezone."""
    return strftime("%Y-%m-%d %H:%M:%S", localtime(seconds))
