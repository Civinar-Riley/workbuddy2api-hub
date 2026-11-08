# -*- coding: utf-8 -*-
"""文本号池：只服务文本模型调用的派发模块。

与 ``wb_pool_media`` 成对存在，两者共用同一个 ``wb_accounts.AccountPool``
实例（账号文件、凭据刷新、出口代理、日额度护栏都只有一份），但**派发边界**
各自独立：

* 本模块只会从 ``poolScope == "text"`` 的账号里选号；
* 任何拿到手的账号都要再过一遍 ``require()``，媒体号池的账号在这里被挡回去，
  并抛 ``wb_pool_scope.ScopeViolation``（403），从而保证「媒体号池的账号不能
  发起文本模型请求」不是靠调用方自觉，而是这一层硬拒。

为什么要再挡一次：选号之外还有几条路径会**拿现成的账号**去发文本请求——面板
钉住的账号、API Key 绑定的账号、会话亲和已经绑定的账号、签到 / 每日活跃这类
定时任务、以及重试链里沿用的上一轮账号。这些路径都不过 ``pick()``，只靠选号
过滤等于给媒体账号留了一条后门。
"""
import wb_pool_scope as scope_mod


def _call_compat(method, *args, **kwargs):
    """Call a pool seam that may predate the optional scope/allowed arguments.

    AccountPool is not the only implementation of this seam: tests and
    embedders substitute smaller pools. Drop only the newer optional kwargs
    when a legacy signature rejects them; a TypeError raised inside the real
    method is re-raised unchanged after every fallback fails.
    """
    reduced = dict(kwargs)
    while True:
        try:
            return method(*args, **reduced)
        except TypeError as exc:
            if not reduced:
                raise
            # CPython 3.9/3.12 use slightly different wording, but both name
            # the rejected keyword. A TypeError from inside the method is not
            # a signature mismatch and must never trigger a retry.
            message = str(exc)
            if "unexpected keyword argument" not in message:
                raise
            rejected = [name for name in ("scope", "allowed", "refresh")
                        if name in reduced and name in message]
            if not rejected:
                raise
            reduced.pop(rejected[0], None)


def _scoped_legacy_pick(pool, scope, realm=None, exclude=None, model=None,
                        page=None, allowed=None):
    """Pick from a legacy pool that does not understand ``scope``.

    A stand-in pool in the suite exposes ``accounts`` as a plain list. Filter
    that snapshot before asking it to pick: passing ``scope`` is impossible,
    and falling back to an unfiltered pick would let a media account serve
    text traffic. Pools without ``accounts`` keep their old signature.
    """
    accounts = getattr(pool, "accounts", None)
    if not isinstance(accounts, (list, tuple)):
        return _call_compat(
            pool.pick, realm=realm, exclude=exclude, model=model,
            page=page, allowed=allowed)
    # A legacy stand-in may predate the poolScope field entirely. In that
    # case there is no field to filter on, so preserve its old behavior;
    # otherwise a fake pool would silently stop serving every media request.
    has_scope = any(hasattr(a, "pool_scope") for a in accounts)
    wanted = ([a for a in accounts if scope_mod.in_scope(a, scope)]
              if has_scope else list(accounts))
    allowed = set(allowed) if allowed is not None else None
    if allowed is not None:
        wanted = [a for a in wanted if a.uid in allowed]
    excluded = set(exclude or ())
    for account in wanted:
        if account.uid in excluded:
            continue
        if not getattr(account, "realm", None) == realm:
            continue
        ready = getattr(account, "ready", None)
        if ready is not None and not ready(model=model):
            continue
        return account
    return None


class PoolBoundaryBlocked(Exception):
    """这道入口绑定的号池不接受这个账号（见 wb_pool_scope.boundary）。

    与 ``ScopeViolation`` 的区别：那条是账号字段判定的 403，这条是部署层面
    「本进程根本不该跑这个号池的活」，HTTP 层翻译成 503（本入口无此项服务），
    并引导客户端换用另一个入口。
    """

    def __init__(self, message, scope="", wanted=""):
        super().__init__(message)
        self.scope = scope or ""
        self.wanted = wanted or ""
        self.code = "POOL_BOUNDARY"

    @property
    def status(self):
        return 503


class TextPool(object):
    """文本号池的门面：选号 + 准入检查。"""

    def __init__(self, pool, log=None):
        self.pool = pool
        self.log = log or (lambda msg: None)

    # ---------------------------------------------------------------- 选号
    def pick(self, realm=None, exclude=None, model=None, page=None, allowed=None):
        """从文本号池里挑一个账号，没有可用账号时返回 None。

        ``allowed`` 是 API Key 的账号白名单，``exclude`` 是本轮已经试过的账号，
        两者都由调用方传进来，这里只叠加号池这一维。
        """
        if self.pool is None:
            return None
        if not scope_mod.separation_enabled():
            return _call_compat(self.pool.pick, realm=realm, exclude=exclude,
                                model=model, page=page, allowed=allowed)
        if not scope_mod.entry_allows(scope_mod.SCOPE_TEXT):
            # 媒体入口：本进程不服务任何文本模型请求，直接没有候选。
            return None
        try:
            return self.pool.pick(realm=realm, exclude=exclude, model=model,
                                  page=page, allowed=allowed,
                                  scope=scope_mod.SCOPE_TEXT)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            return _scoped_legacy_pick(
                self.pool, scope_mod.SCOPE_TEXT, realm=realm, exclude=exclude,
                model=model, page=page, allowed=allowed)

    def pick_for_session(self, realm=None, session_key=None, exclude=None,
                         model=None, page=None, allowed=None):
        """一轮对话的选号：优先沿用这条会话上一轮用的账号。"""
        if self.pool is None:
            return None
        if not scope_mod.separation_enabled():
            return _call_compat(
                self.pool.pick_for_session,
                realm=realm, session_key=session_key, exclude=exclude,
                model=model, page=page, allowed=allowed)
        if not scope_mod.entry_allows(scope_mod.SCOPE_TEXT):
            return None
        try:
            return self.pool.pick_for_session(
                realm=realm, session_key=session_key, exclude=exclude,
                model=model, page=page, allowed=allowed,
                scope=scope_mod.SCOPE_TEXT)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            return _scoped_legacy_pick(
                self.pool, scope_mod.SCOPE_TEXT, realm=realm, exclude=exclude,
                model=model, page=page, allowed=allowed)

    def count_ready(self, realm=None, model=None, allowed=None, refresh=True):
        """文本号池里此刻能用的账号数（面板 / 总并发都用这个口径）。"""
        if self.pool is None:
            return 0
        if not scope_mod.separation_enabled():
            return _call_compat(self.pool.count_ready, realm, model=model,
                                allowed=allowed, refresh=refresh)
        if not scope_mod.entry_allows(scope_mod.SCOPE_TEXT):
            return 0
        try:
            return self.pool.count_ready(
                realm, model=model, allowed=allowed, refresh=refresh,
                scope=scope_mod.SCOPE_TEXT)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            accounts = getattr(self.pool, "accounts", None)
            if not isinstance(accounts, (list, tuple)):
                return _call_compat(self.pool.count_ready, realm, model=model,
                                    allowed=allowed, refresh=refresh)
            has_scope = any(hasattr(a, "pool_scope") for a in accounts)
            return sum(
                1 for account in accounts
                if (not has_scope or scope_mod.in_scope(
                    account, scope_mod.SCOPE_TEXT))
                and getattr(account, "realm", None) == realm
                and getattr(account, "enabled", True)
                and getattr(account, "access_token", "")
                and (allowed is None or account.uid in allowed)
                and account.ready(model=model, refresh=refresh))

    # ---------------------------------------------------------------- 准入
    def require(self, account):
        """确认这个账号可以跑文本模型，不可以就抛 ScopeViolation。

        返回账号本身，方便 ``account = TEXTS.require(account)`` 这样串联。
        """
        if account is None:
            return None
        if not scope_mod.entry_allows(scope_mod.SCOPE_TEXT):
            raise PoolBoundaryBlocked(
                "这个入口只服务图片 / 视频生成，不发起文本模型请求；"
                "文本模型请走文本号池的入口。",
                scope=scope_mod.boundary() or "", wanted=scope_mod.SCOPE_TEXT)
        if scope_mod.separation_enabled() and \
                scope_mod.account_scope(account) == scope_mod.SCOPE_MEDIA:
            raise scope_mod.text_violation(account)
        return account

    def allows(self, account):
        """不抛异常的版本，给「过滤候选列表」用。"""
        if account is None:
            return False
        if not scope_mod.separation_enabled():
            return True
        if not scope_mod.entry_allows(scope_mod.SCOPE_TEXT):
            return False
        return scope_mod.account_scope(account) != scope_mod.SCOPE_MEDIA

    def accounts(self):
        """文本号池的账号列表（快照）。定时任务按号遍历时用它过滤。"""
        if self.pool is None:
            return []
        lock = getattr(self.pool, "_lock", None)
        if lock is None:
            values = list(getattr(self.pool, "accounts", ()) or ())
        else:
            with lock:
                values = list(self.pool.accounts)
        return [a for a in values if self.allows(a)]

    def get(self, uid):
        """按 uid 取文本号池里的账号；属于媒体号池则返回 None。"""
        if self.pool is None:
            return None
        getter = getattr(self.pool, "get", None)
        if getter is not None:
            account = getter(uid)
        else:
            account = next((a for a in getattr(self.pool, "accounts", ())
                            if a.uid == uid), None)
        return account if self.allows(account) else None

    def public(self, realm=None):
        """面板账号列表（只含文本号池）。"""
        if self.pool is None:
            return []
        lister = getattr(self.pool, "list_public", None)
        if lister is None:
            return []
        return [a for a in lister(realm) if self.allows_public(a)]

    @staticmethod
    def allows_public(row):
        """对 ``Account.public()`` 出来的 dict 做同样的判定。"""
        if not scope_mod.separation_enabled():
            return True
        if not scope_mod.entry_allows(scope_mod.SCOPE_TEXT):
            return False
        return scope_mod.normalize_scope(row.get("poolScope")) != scope_mod.SCOPE_MEDIA
