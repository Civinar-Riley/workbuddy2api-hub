# -*- coding: utf-8 -*-
"""号池分区：文本号池 / 媒体号池。

分区**默认关闭**：所有账号都能同时服务文本与媒体请求，这与历史单池行为
一致。只有操作者显式打开 ``pool_separation_enabled``（设置页开关或
``WB_POOL_SEPARATION=1``）后，才会按账号的 ``poolScope`` 做派发边界：

* **文本号池**（``text``）：只服务文本模型（chat / responses / codearts 等），
  **不参与**图片与视频生成。
* **媒体号池**（``media``）：只服务图片与视频生成，**永远不能**发起文本模型
  请求。

为什么要拆：文本模型与媒体模型的账号在上游是两套风控口径。把同一个号同时
拿去跑画图和对话时，一旦画图侧被判定异常，连坐会把文本模型一起带走；反过来
一个被文本侧限流的号也会把画图任务卡死。分区之后，两类流量各自有独立的可用性
和熔断，一侧出事不传染另一侧。

账号落在哪个号池由凭证档里的 ``poolScope`` 字段决定（见
``wb_accounts.Account``）。没有这个字段的老账号按 **文本号池** 记录；只要
分区没打开，这个字段不限制任何请求。

本模块只提供口径（常量、归一化、判定、异常），不持有状态；选号的实现在
``wb_pool_text`` / ``wb_pool_media`` 两个模块里，共用的账号存储、凭据刷新、
出口代理与护栏仍由 ``wb_accounts.AccountPool`` 单实例承担——两个号池是**派发
边界**，不是两份账号文件，否则同一个 token 会被两套刷新器同时刷新。
"""
import os

_separation_resolver = None

#: 文本号池：只跑文本模型，不参与图片 / 视频生成。
SCOPE_TEXT = "text"

#: 媒体号池：只跑图片 / 视频生成，禁止文本模型请求。
SCOPE_MEDIA = "media"

#: 两个号池的合法取值。顺序即面板上的展示顺序。
SCOPES = (SCOPE_TEXT, SCOPE_MEDIA)

#: 老账号没有 ``poolScope`` 时的归属。选文本是刻意的：媒体号池必须由人显式
#: 加入，升级动作本身不该把一个正在对话的号变成画图号。
DEFAULT_SCOPE = SCOPE_TEXT

#: 环境变量覆盖值。未设置时使用设置页的 pool_separation_enabled；两者都没有
#: 时默认关闭，回到「一个池子跑全部流量」。
_SEPARATION_OFF = ("0", "off", "false", "no")


def configure_separation_resolver(resolver):
    """Install the settings-file lookup used when the env var is absent."""
    global _separation_resolver
    _separation_resolver = resolver


def separation_enabled():
    """号池分区是否生效（默认关闭）。

    优先级：进程入口 ``WB_POOL_SCOPE`` > 环境变量 ``WB_POOL_SEPARATION`` >
    ``settings.json.pool_separation_enabled`` > 默认关闭。

    存在两个号池的**独立接入端**（``WB_TEXT_POOL_URL`` / ``WB_MEDIA_POOL_URL``，
    见 wb_pool_scope.boundary）时，分区强制开启且不可关闭：两个入口背后各有
    一套账号，关掉分区等于让其中一个入口的账号跑到另一个号池去，正是要防的事。
    """
    if boundary() is not None:
        return True
    raw = os.environ.get("WB_POOL_SEPARATION")
    if raw is not None:
        return str(raw).strip().lower() not in _SEPARATION_OFF
    if _separation_resolver is not None:
        try:
            return bool(_separation_resolver())
        except Exception:
            # A broken settings file must not silently turn isolation on.
            return False
    return False


def boundary():
    """本进程绑定的号池入口（``text`` / ``media``），未绑定时返回 None。

    部署方式：同一个 wb_proxy.py 起两个实例，各用一个端口和一份账号目录——

    * 文本实例：``WB_POOL_SCOPE=text  ACCOUNTS_DIR=<accounts_text>``
    * 媒体实例：``WB_POOL_SCOPE=media  ACCOUNTS_DIR=<accounts_media>``

    这比「一个池子里靠字段过滤」更硬：媒体实例的进程里**根本没有**文本账号，
    它拿到文本模型请求时无从选号；文本实例同理碰不到媒体账号。字段过滤仍然
    保留，作为同进程内的第二道闸。
    """
    raw = os.environ.get("WB_POOL_SCOPE")
    if raw is None:
        return None
    key = str(raw).strip().lower()
    if key in ("", "all", "*"):
        return None
    return normalize_scope(key)


def entry_allows(account_scope):
    """这道入口是否允许 ``account_scope`` 这个号池的账号干活。

    未绑定入口（单实例部署）时恒为 True，退化到字段过滤那套。
    """
    bound = boundary()
    if bound is None:
        return True
    return normalize_scope(account_scope) == bound

_SCOPE_ALIASES = {
    "text": SCOPE_TEXT,
    "txt": SCOPE_TEXT,
    "chat": SCOPE_TEXT,
    "llm": SCOPE_TEXT,
    "文本": SCOPE_TEXT,
    "对话": SCOPE_TEXT,
    "media": SCOPE_MEDIA,
    "image": SCOPE_MEDIA,
    "video": SCOPE_MEDIA,
    "img": SCOPE_MEDIA,
    "媒体": SCOPE_MEDIA,
    "图片": SCOPE_MEDIA,
    "视频": SCOPE_MEDIA,
}

LABELS = {
    SCOPE_TEXT: "文本号池",
    SCOPE_MEDIA: "媒体号池",
}


class ScopeViolation(RuntimeError):
    """账号被派到了它不属于的号池。

    文本号池的账号来做画图、或媒体号池的账号来跑对话，都抛这个。带 ``code``
    与 ``status``，方便 HTTP 层直接翻译成 403 / 503，而不是把内部异常冒成 500。
    """

    def __init__(self, message, uid="", scope="", wanted=""):
        super().__init__(message)
        self.uid = uid or ""
        self.scope = scope or ""
        self.wanted = wanted or ""
        self.code = "POOL_SCOPE"

    @property
    def status(self):
        return 403

    def to_body(self):
        return {
            "error": {
                "message": str(self),
                "type": "pool_scope_violation",
                "code": self.code,
            }
        }


def normalize_scope(value, default=DEFAULT_SCOPE):
    """把面板 / 配置里出现的各种写法收敛成 ``text`` 或 ``media``。

    认不出的值一律回 ``default``（默认文本号池）：坏数据不该让账号加载失败，
    更不该让一个来路不明的值把账号放进媒体号池。
    """
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _SCOPE_ALIASES:
            return _SCOPE_ALIASES[key]
        if key in SCOPES:
            return key
    return default


def is_valid_scope(value):
    """面板入参校验：只有明确写了 text / media（含别名）才算合法。"""
    if not isinstance(value, str):
        return False
    key = value.strip().lower()
    return key in _SCOPE_ALIASES or key in SCOPES


def account_scope(account):
    """账号所属号池。读属性而不是字典，两个号池的判定都收敛到这一处。"""
    if account is None:
        return DEFAULT_SCOPE
    return normalize_scope(getattr(account, "pool_scope", DEFAULT_SCOPE))


def in_scope(account, scope):
    """``account`` 是否属于 ``scope``。``scope`` 为 None 表示不限（内部统计用）。"""
    if scope is None:
        return True
    return account_scope(account) == normalize_scope(scope)


def label_for(scope):
    return LABELS.get(normalize_scope(scope), LABELS[DEFAULT_SCOPE])


def describe_uid(account, scope):
    """报错正文里的账号称呼：``昵称(uid 前 8 位)``。"""
    uid = str(getattr(account, "uid", "") or "")
    nickname = str(getattr(account, "nickname", "") or "").strip()
    shown = uid[:8] if uid else "?"
    if nickname:
        return "%s(%s)" % (nickname, shown)
    return shown


def text_violation(account):
    """媒体号池的账号被要求跑文本模型时的标准异常。"""
    return ScopeViolation(
        "账号 %s 属于媒体号池，已禁止发起文本模型请求（防止文本账号被风控连坐）。"
        "请改用文本号池的账号，或先在看板把这个账号改回文本号池。"
        % describe_uid(account, SCOPE_TEXT),
        uid=str(getattr(account, "uid", "") or ""),
        scope=SCOPE_MEDIA,
        wanted=SCOPE_TEXT,
    )


def media_violation(account):
    """文本号池的账号被要求跑图片 / 视频生成时的标准异常。"""
    return ScopeViolation(
        "账号 %s 属于文本号池，不参与图片 / 视频生成。"
        "请把用于画图 / 生视频的账号在看板里设为媒体号池。"
        % describe_uid(account, SCOPE_MEDIA),
        uid=str(getattr(account, "uid", "") or ""),
        scope=SCOPE_TEXT,
        wanted=SCOPE_MEDIA,
    )
