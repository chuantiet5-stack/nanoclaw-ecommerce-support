"""业务公共规则：状态机、脱敏、ID 生成、时间工具。

状态命名与 V4 电商工具体系（agent/tools/ecom/）严格一致，保证字段兼容。
"""

import re
import secrets
import uuid
from datetime import datetime, timedelta

import config

# ---------- 时间 ----------

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def days_since(time_str: str) -> int:
    """计算距今天数（只取日期部分；解析失败按 999 天处理，与 V4 store.py 一致）。"""
    try:
        d = datetime.strptime(str(time_str)[:10], "%Y-%m-%d").date()
        return (datetime.now().date() - d).days
    except Exception:
        return 999


# ---------- ID 生成 ----------

def request_id() -> str:
    return "req_" + uuid.uuid4().hex[:12]


def _seq_no(prefix: str, table: str, column: str) -> str:
    """按日期 + 三位序号生成业务单号，如 SO20260908001。"""
    today = datetime.now().strftime("%Y%m%d")
    like = f"{prefix}{today}%"
    from mock_merchant import db
    row = db.query_one(
        f"SELECT COUNT(*) AS n FROM {table} WHERE {column} LIKE ?", (like,)
    )
    seq = (row["n"] if row else 0) + 1
    # 防冲突：循环检查
    base = f"{prefix}{today}"
    while True:
        candidate = f"{base}{seq:03d}"
        exists = db.query_one(
            f"SELECT 1 AS x FROM {table} WHERE {column} = ?", (candidate,)
        )
        if not exists:
            return candidate
        seq += 1


def new_order_id() -> str:
    return _seq_no("SO", "orders", "order_id")


def new_after_sale_id() -> str:
    return _seq_no("AS", "after_sales", "after_sale_id")


def new_refund_id() -> str:
    return _seq_no("RF", "refunds", "refund_id")


def new_ticket_id() -> str:
    return _seq_no("TK", "tickets", "ticket_id")


def new_token() -> str:
    return "mtk_" + secrets.token_hex(24)


def new_tracking_no(carrier: str) -> str:
    code = {"顺丰": "SF", "中通": "ZTO", "圆通": "YTO", "韵达": "YD", "申通": "STO"}.get(
        carrier, "EXP"
    )
    return code + datetime.now().strftime("%Y%m%d") + secrets.token_hex(4).upper()


# ---------- 脱敏（与 V4 agent/tools/ecom/store.py 保持一致） ----------

def mask_name(name: str) -> str:
    if not name:
        return ""
    if len(name) <= 1:
        return name
    return name[0] + "**"


def mask_phone(phone: str) -> str:
    phone = str(phone)
    if len(phone) < 7:
        return phone
    return phone[:3] + "****" + phone[-4:]


def mask_address(address: str) -> str:
    if not address:
        return ""
    if len(address) <= 10:
        return address[:3] + "****"
    return address[:9] + "****" + address[-2:]


# ---------- 订单状态机 ----------

ORDER_STATUSES = [
    "待付款", "已付款", "待发货", "已发货", "运输中",
    "已签收", "已完成", "已取消", "退款中", "已退款", "售后中",
]

# 端点驱动的状态跳转表：{动作: {当前状态: 目标状态}}
ORDER_ACTIONS = {
    "pay": {"待付款": "已付款"},
    "cancel": {"待付款": "已取消", "已付款": "已取消", "待发货": "已取消"},
    "ship": {"已付款": "已发货", "待发货": "已发货"},
    "confirm": {"已发货": "已签收", "运输中": "已签收"},
    "complete": {"已签收": "已完成"},
}

# 允许发起售后/退款的订单状态
AFTER_SALE_ALLOWED_ORDER_STATUS = [
    "已付款", "待发货", "已发货", "运输中", "已签收", "已完成",
]

# 售后类型 → 是否资金类（影响订单状态：退款中 / 售后中）
REFUND_LIKE_TYPES = ("仅退款", "退货退款")


class OrderStatusError(Exception):
    """非法状态跳转。"""

    def __init__(self, action: str, current: str):
        self.action = action
        self.current = current
        super().__init__(f"订单当前状态为「{current}」，不允许执行「{action}」操作")


def next_order_status(action: str, current: str) -> str:
    """校验并返回动作后的目标状态；非法跳转抛 OrderStatusError。"""
    table = ORDER_ACTIONS.get(action, {})
    if current not in table:
        raise OrderStatusError(action, current)
    return table[current]


# ---------- 售后状态机 ----------

AFTER_SALE_TYPES = ["仅退款", "退货退款", "换货", "维修", "投诉"]

AFTER_SALE_STATUSES = [
    "待审核", "审核通过", "审核驳回", "待寄回", "已寄回",
    "商家收货", "退款中", "已完成", "已关闭",
]

# 售后动作跳转表
AFTER_SALE_ACTIONS = {
    "approve": {"待审核": None},   # 目标状态依类型而定，由业务层决定
    "reject": {"待审核": "审核驳回"},
    "receive": {"待寄回": "商家收货", "已寄回": "商家收货"},
    "complete": {"商家收货": "已完成", "审核通过": "已完成", "退款中": "已完成"},
}

# 进行中的售后（同一订单同时只允许一个）
ONGOING_AFTER_SALE_STATUS = [
    "待审核", "审核通过", "待寄回", "已寄回", "商家收货", "退款中",
]


class AfterSaleStatusError(Exception):
    def __init__(self, action: str, current: str):
        self.action = action
        self.current = current
        super().__init__(f"售后单当前状态为「{current}」，不允许执行「{action}」操作")


def next_after_sale_status(action: str, current: str) -> str:
    table = AFTER_SALE_ACTIONS.get(action, {})
    if current not in table:
        raise AfterSaleStatusError(action, current)
    target = table[current]
    if target is None:
        raise ValueError(f"动作 {action} 的目标状态需由业务层按类型决定")
    return target


# ---------- 退款状态机 ----------

REFUND_STATUSES = ["待审核", "审核通过", "已驳回", "已退款", "已关闭"]

REFUND_ACTIONS = {
    "approve": {"待审核": "审核通过"},
    "reject": {"待审核": "已驳回"},
    "success": {"审核通过": "已退款"},
}

# 退款决策（与 V4 refund_policy 一致）：auto_approve / auto_reject / manual
REFUND_REVIEW_TYPE = {"auto_approve": "auto", "auto_reject": "auto", "manual": "manual"}


class RefundStatusError(Exception):
    def __init__(self, action: str, current: str):
        self.action = action
        self.current = current
        super().__init__(f"退款单当前状态为「{current}」，不允许执行「{action}」操作")


def next_refund_status(action: str, current: str) -> str:
    table = REFUND_ACTIONS.get(action, {})
    if current not in table:
        raise RefundStatusError(action, current)
    return table[current]


# ---------- 工单 ----------

TICKET_TYPES = ["咨询", "售后", "投诉", "建议", "其他"]
TICKET_STATUSES = ["待处理", "处理中", "已解决", "已关闭"]

TICKET_PRIORITY_LABELS = {1: "低", 2: "普通", 3: "加急"}


def ticket_priority_label(priority: int) -> str:
    return TICKET_PRIORITY_LABELS.get(int(priority), "普通")


# ---------- 物流 ----------

CARRIERS = ["顺丰", "中通", "圆通", "韵达", "申通"]

LOGISTICS_NODES = ["待发货", "已揽收", "到达分拨中心", "运输中", "派送中", "已签收"]

# 各节点的示例地点（演示用，随机挑一个）
NODE_LOCATIONS = {
    "已揽收": ["北京市朝阳区营业点", "北京市海淀区营业点", "北京市丰台区营业点"],
    "到达分拨中心": ["北京华北分拨中心", "廊坊转运中心", "天津分拨中心"],
    "运输中": ["京沪线运输途中", "京广线运输途中", "G2高速运输途中"],
    "派送中": ["上海市浦东新区网点", "南京市玄武区网点", "杭州市西湖区网点"],
    "已签收": ["本人签收", "代收点签收", "家人代签"],
    "派送异常": ["派送员联系不上收件人", "地址不详无法派送", "收件人要求改日派送"],
}


def next_logistics_node(current: str) -> str | None:
    """返回物流的下一个节点；已到终点返回 None。"""
    if current not in LOGISTICS_NODES:
        return "已揽收"
    idx = LOGISTICS_NODES.index(current)
    if idx >= len(LOGISTICS_NODES) - 1:
        return None
    return LOGISTICS_NODES[idx + 1]


# ---------- 校验 ----------

ORDER_ID_RE = re.compile(r"^(SO\d{9,})$")


def valid_order_id(order_id: str) -> bool:
    return bool(ORDER_ID_RE.match(order_id or ""))


def calc_return_deadline(signed_at: str) -> str:
    """签收后 N 天的退货截止日期。"""
    try:
        d = datetime.strptime(str(signed_at)[:10], "%Y-%m-%d").date()
        return (d + timedelta(days=config.RETURN_WINDOW_DAYS)).strftime("%Y-%m-%d")
    except Exception:
        return ""
