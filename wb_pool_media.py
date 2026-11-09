# -*- coding: utf-8 -*-
"""媒体号池：图片 / 视频生成的派发模块。

与 ``wb_pool_text`` 成对存在，共用同一个 ``wb_accounts.AccountPool`` 实例，
号池隔离默认关闭，此时媒体请求与文本请求共用完整账号池；只有显式打开
``pool_separation_enabled`` 后才启用独立派发边界：

* 只从 ``poolScope == "media"`` 的账号里选号；
* 拿到现成账号（面板钉住、任务恢复、上次用过）时同样要过 ``require()``，
  文本号池的账号一律拒绝，抛 ``wb_pool_scope.ScopeViolation``。

媒体号池为空时**不会**回落到文本号池：这正是要切断的那条路。调用方会拿到
``None``，由上层翻译成「媒体号池里没有可用账号，请把画图账号设为媒体号池」
这种可执行的提示。
"""
import wb_pool_scope as scope_mod
from wb_pool_text import PoolBoundaryBlocked, _call_compat, _scoped_legacy_pick


class MediaPool(object):
    """媒体号池的门面：选号 + 准入检查。"""

    def __init__(self, pool, log=None):
        self.pool = pool
        self.log = log or (lambda msg: None)

    # ---------------------------------------------------------------- 选号
    def pick(self, realm=None, exclude=None, model=None, page=None, allowed=None):
        """从媒体号池里挑一个账号跑这次图片 / 视频请求。"""
        if self.pool is None:
            return None
        if not scope_mod.separation_enabled():
            return _call_compat(self.pool.pick, realm=realm, exclude=exclude,
                                model=model, page=page, allowed=allowed)
        if not scope_mod.entry_allows(scope_mod.SCOPE_MEDIA):
            # 文本入口：本进程不服务图片 / 视频，直接没有候选。
            return None
        try:
            return self.pool.pick(realm=realm, exclude=exclude, model=model,
                                  page=page, allowed=allowed,
                                  scope=scope_mod.SCOPE_MEDIA)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            return _scoped_legacy_pick(
                self.pool, scope_mod.SCOPE_MEDIA, realm=realm, exclude=exclude,
                model=model, page=page, allowed=allowed)

    def count_ready(self, realm=None, model=None, allowed=None, refresh=True):
        if self.pool is None:
            return 0
        if scope_mod.separation_enabled() and \
                not scope_mod.entry_allows(scope_mod.SCOPE_MEDIA):
            return 0
        if not scope_mod.separation_enabled():
            return _call_compat(self.pool.count_ready, realm, model=model,
                                allowed=allowed, refresh=refresh)
        try:
            return self.pool.count_ready(
                realm, model=model, allowed=allowed, refresh=refresh,
                scope=scope_mod.SCOPE_MEDIA)
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
                    account, scope_mod.SCOPE_MEDIA))
                and getattr(account, "realm", None) == realm
                and getattr(account, "enabled", True)
                and getattr(account, "access_token", "")
                and (allowed is None or account.uid in allowed)
                and account.ready(model=model, refresh=refresh))

    def empty_detail(self, realm=None):
        """媒体号池为空时的报错正文：直接告诉使用者下一步怎么做。"""
        if self.pool is None:
            return "账号池未加载。"
        if scope_mod.separation_enabled() and \
                not scope_mod.entry_allows(scope_mod.SCOPE_MEDIA):
            return "这个入口只服务文本模型，不提供图片 / 视频生成。"
        text_accounts = 0
        lock = getattr(self.pool, "_lock", None)
        if lock is None:
            accounts = list(getattr(self.pool, "accounts", ()) or ())
        else:
            with lock:
                accounts = list(self.pool.accounts)
        for account in accounts:
            if realm and account.realm != realm:
                continue
            if not scope_mod.separation_enabled() or \
                    scope_mod.account_scope(account) == scope_mod.SCOPE_TEXT:
                text_accounts += 1
        if scope_mod.separation_enabled() and text_accounts > 0:
            return ("媒体号池里没有账号：当前%s的账号 %d 个属于文本号池。"
                    "图片 / 视频只能用媒体号池的号，请在看板把用于画图的账号设为「媒体号池」。"
                    % ("该出口" if realm else "全部出口", text_accounts))
        if scope_mod.separation_enabled():
            return "媒体号池里没有账号：请先导入用于图片 / 视频生成的账号，并在看板设为「媒体号池」。"
        return "没有可用账号。"

    # ---------------------------------------------------------------- 准入
    def require(self, account):
        """确认这个账号可以跑图片 / 视频，不可以就抛 ScopeViolation。"""
        if account is None:
            return None
        if not scope_mod.entry_allows(scope_mod.SCOPE_MEDIA):
            raise PoolBoundaryBlocked(
                "这个入口只服务文本模型，不提供图片 / 视频生成；"
                "画图与生视频请走媒体号池的入口。",
                scope=scope_mod.boundary() or "", wanted=scope_mod.SCOPE_MEDIA)
        if scope_mod.separation_enabled() and \
                scope_mod.account_scope(account) == scope_mod.SCOPE_TEXT:
            raise scope_mod.media_violation(account)
        return account

    def allows(self, account):
        if account is None:
            return False
        if not scope_mod.separation_enabled():
            return True
        if not scope_mod.entry_allows(scope_mod.SCOPE_MEDIA):
            return False
        return scope_mod.account_scope(account) == scope_mod.SCOPE_MEDIA

    def accounts(self):
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
        """按 uid 取媒体号池里的账号；属于文本号池则返回 None。"""
        if self.pool is None:
            return None
        getter = getattr(self.pool, "get", None)
        if getter is not None:
            account = getter(uid)
        else:
            account = next((a for a in getattr(self.pool, "accounts", ())
                            if a.uid == uid), None)
        return account if self.allows(account) else None
