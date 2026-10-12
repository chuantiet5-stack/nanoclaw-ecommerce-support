"""电商工具共享数据访问层。

所有业务工具通过此模块读写 data/ 目录下的模拟数据，
统一提供脱敏、登录校验、归属校验等公共能力。
"""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

# 项目根目录下的 data/（agent/tools/ecom/store.py 向上三级）
DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# 数据后端开关（生产落地 P0-1.1）：
# - merchant_api（默认）：商品数据实时来自 SJ 模拟商家服务（HTTP，端口 8090），
#   SJ 上架/下架/改价即时对 V4 生效；SJ 未启动时商品查询自动回退 JSON
# - json：走本模块的 JSON 文件读写，行为与历史版本完全一致
# - mysql：数据函数委托到 store_mysql（MySQL 读写，形状兼容，业务工具无感知）
DATA_BACKEND = os.getenv("DATA_BACKEND", "merchant_api").lower()


def _mysql():
    """惰性导入 MySQL 委托层（仅 DATA_BACKEND=mysql 时加载）。"""
    from agent.tools.ecom import store_mysql
    return store_mysql


def _merchant_api():
    """惰性导入 SJ 模拟商家 HTTP 客户端（仅 DATA_BACKEND=merchant_api 时加载）。"""
    from agent.tools.ecom import merchant_api_client
    return merchant_api_client

# 未登录统一提示（由 Agent 转述给顾客）
NOT_LOGGED_IN = (
    "请先登录后再进行此操作。当前为游客状态，"
    "请点击页面右上角【登录】（演示账号：han / 123456）。"
)


def _load_json(filename: str, default):
    path = DATA_DIR / filename
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(filename: str, data) -> None:
    path = DATA_DIR / filename
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8"
    )


# ---------- 用户 ----------

def load_users() -> dict:
    """返回 {username: user_dict}，另附 __default__ 键存默认演示账号。"""
    if DATA_BACKEND == "mysql":
        return _mysql().load_users()
    data = _load_json("users.json", {"users": []})
    users = {u["username"]: u for u in data.get("users", [])}
    users["__default__"] = data.get("default_demo_user")
    return users


def get_user(actor):
    """按用户名取用户档案，不存在返回 None。"""
    if not actor:
        return None
    return load_users().get(actor)


# ---------- 渠道账号绑定（QQ / 飞书免登录，跨渠道共用会话）----------

# 绑定表文件（data/channel_bindings.json），格式：
# {"bindings": {"qq:OPENID": "han", "feishu:ou_xxx": "li"}}
# IM 渠道用户身份（openid）→ 站内账号；未绑定的渠道用户回退默认演示账号（免登录）
CHANNEL_BINDINGS_FILE = "channel_bindings.json"


def load_channel_bindings() -> dict:
    """返回 {channel_key: username} 绑定表（channel_key 形如 "qq:OPENID"）。"""
    data = _load_json(CHANNEL_BINDINGS_FILE, {"bindings": {}})
    bindings = data.get("bindings", {})
    if not isinstance(bindings, dict):
        return {}
    return {str(k): str(v) for k, v in bindings.items()}


def get_channel_binding(channel_key: str):
    """查询单个渠道身份绑定的用户名，未绑定返回 None。"""
    return load_channel_bindings().get(channel_key)


def set_channel_binding(channel_key: str, username: str) -> None:
    """写入/更新一条渠道身份绑定（立即持久化到 JSON 文件）。"""
    data = _load_json(CHANNEL_BINDINGS_FILE, {"bindings": {}})
    bindings = data.get("bindings") if isinstance(data.get("bindings"), dict) else {}
    bindings[channel_key] = username
    _save_json(CHANNEL_BINDINGS_FILE, {"bindings": bindings})


def resolve_channel_actor(channel: str, sender_id: str):
    """QQ / 飞书等 IM 渠道的免登录身份解析，返回 (actor, logged_in)。

    IM 渠道无法弹网页登录框，规则与 CLI 预绑定演示账号思路一致：
    1. 绑定表（data/channel_bindings.json）命中且用户存在 → 该账号（登录态）；
    2. 未绑定 / 绑定指向不存在的用户 → 默认演示账号（登录态，免登录）；
    3. 无默认演示账号 → 游客。
    """
    users = load_users()
    binding = get_channel_binding(f"{channel}:{sender_id}")
    if binding and binding in users:
        return binding, True
    default = users.get("__default__")
    if default and default in users:
        return default, True
    return None, False


# ---------- 商品 ----------

def load_goods() -> list:
    if DATA_BACKEND == "mysql":
        return _mysql().load_goods()
    if DATA_BACKEND == "merchant_api":
        return _load_goods_from_merchant_api()
    return _load_json("goods.json", [])


def _load_goods_from_merchant_api() -> list:
    """从 SJ 模拟商家服务实时拉取在售商品目录。

    SJ 商品字段（sku/name/price/highlights/sizes/colors/scenes/link 等）
    与 V4 goods 模型完全对齐，直接透传；SJ 上架新商品、改价、下架对 V4 即时生效。
    SJ 服务未启动时回退本地 JSON，保证 V4 可独立运行。
    """
    try:
        data = _merchant_api().client.get_products(status="在售", page_size=100)
        return data.get("list", [])
    except Exception as e:  # 读操作回退安全；写操作不经此路径
        print(f"[store] SJ 模拟商家服务不可用（{e}），商品数据回退本地 JSON")
        return _load_json("goods.json", [])


def find_good(sku: str):
    """按 SKU 查单个商品（含已下架的，供详情/状态判断）。

    返回商品 dict 或 None（商品不存在）。
    下架判断：merchant_api 模式下 SJ 单品接口不限状态，可区分「已下架」。
    """
    sku = (sku or "").strip().upper()
    if not sku:
        return None
    if DATA_BACKEND == "merchant_api":
        try:
            return _merchant_api().client.get_product(sku)
        except Exception:
            return None
    for g in load_goods():
        if g.get("sku", "").upper() == sku:
            return g
    return None


# ---------- 订单 ----------

def load_orders() -> list:
    if DATA_BACKEND == "mysql":
        return _mysql().load_orders()
    return _load_json("orders.json", [])


def save_orders(orders: list) -> None:
    if DATA_BACKEND == "mysql":
        _mysql().save_orders(orders)
        return
    _save_json("orders.json", orders)


def find_order(order_id: str):
    if DATA_BACKEND == "mysql":
        return _mysql().find_order(order_id)
    for order in load_orders():
        if order["order_id"] == order_id:
            return order
    return None


# ---------- 物流 ----------

def load_logistics() -> dict:
    if DATA_BACKEND == "mysql":
        return _mysql().load_logistics()
    return _load_json("logistics.json", {})


def save_logistics(logistics: dict) -> None:
    """写回物流轨迹表（拦截物流等售后场景追加点位用）。"""
    if DATA_BACKEND == "mysql":
        _mysql().save_logistics(logistics)
        return
    _save_json("logistics.json", logistics)


# ---------- 工单 ----------

def load_tickets() -> list:
    if DATA_BACKEND == "mysql":
        return _mysql().load_tickets()
    return _load_json("tickets.json", [])


def save_tickets(tickets: list) -> None:
    if DATA_BACKEND == "mysql":
        _mysql().save_tickets(tickets)
        return
    _save_json("tickets.json", tickets)


def new_ticket_id() -> str:
    """生成不与现有工单冲突的工单号。"""
    existing = {t["ticket_id"] for t in load_tickets()}
    base = datetime.now().strftime("%Y%m%d%H%M%S")
    ticket_id = f"TK{base}"
    suffix = 1
    while ticket_id in existing:
        suffix += 1
        ticket_id = f"TK{base}{suffix:02d}"
    return ticket_id


# ---------- 账号记忆 ----------

def memory_path(actor: str) -> Path:
    return DATA_DIR / "memory" / f"{actor}.md"


def load_memory(actor: str) -> list:
    if DATA_BACKEND == "mysql":
        return _mysql().load_memory(actor)
    path = memory_path(actor)
    if not path.exists():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("- "):
            lines.append(line[2:].strip())
    return lines


def append_memory(actor: str, content: str) -> None:
    if DATA_BACKEND == "mysql":
        _mysql().append_memory(actor, content)
        return
    path = memory_path(actor)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {content}（{today} 记录）\n")


# ---------- 脱敏 ----------

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


# ---------- 业务规则 ----------

RETURN_WINDOW_DAYS = 7  # 签收后 7 天内可无理由退货


def days_since(time_str: str) -> int:
    """计算距今天数（只取日期部分）。"""
    try:
        d = datetime.strptime(time_str[:10], "%Y-%m-%d").date()
        return (datetime.now().date() - d).days
    except Exception:
        return 999


def return_deadline(signed_at: str) -> str:
    d = datetime.strptime(signed_at[:10], "%Y-%m-%d").date()
    return (d + timedelta(days=RETURN_WINDOW_DAYS)).strftime("%Y-%m-%d")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 校验辅助 ----------

def check_login(logged_in: bool):
    """未登录时返回提示文本，已登录返回 None。"""
    if not logged_in:
        return NOT_LOGGED_IN
    return None


def check_order_owner(order: dict, customer_id: str):
    """订单存在但非本人时返回越权提示，本人返回 None。"""
    if order.get("customer_id") != customer_id:
        return "无权查询该订单。"
    return None


# ---------- 文本提取 ----------

ORDER_ID_RE = re.compile(r"(SO\d{6,})")
TICKET_ID_RE = re.compile(r"(TK\d{8,})")
