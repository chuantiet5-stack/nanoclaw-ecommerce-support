# -*- coding: utf-8 -*-
"""模拟商家 API 客户端（交付给 V4 客服系统使用）。

设计目标：
- V4 只需把配置改为 `MERCHANT_API_BASE_URL=http://localhost:8090` 即可无缝切换到
  本模拟商家服务（服务监听 8090，API 前缀 /mock-api/v1 已在客户端内部处理）。
- 内部封装 base_url、token 获取/刷新、超时、重试与统一错误处理。
- 服务不可用时抛出 `MerchantApiError`（中文说明），绝不静默伪造成功。

用法一（推荐，V4 配置文件）：

    # .env / config
    MERCHANT_API_BASE_URL=http://localhost:8090
    MERCHANT_API_USERNAME=operator        # 可选：自动登录获取 token
    MERCHANT_API_PASSWORD=123456
    MERCHANT_API_TOKEN=                   # 也可直接填静态 token

    from merchant_api_client import client
    data = client.get_order("SO20260908001")

用法二（代码直连）：

    from merchant_api_client import MerchantApiClient
    c = MerchantApiClient("http://localhost:8090", username="operator", password="123456")
    print(c.get_products(keyword="袜子"))

命令行自检（服务需已启动）：

    python merchant_api_client.py --check
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

__all__ = ["MerchantApiError", "MerchantApiClient", "client", "get_default_client"]

# 演示账号（与服务端种子数据一致）
DEMO_ACCOUNTS = {
    "merchant_admin": "123456",  # 商家管理员
    "operator": "123456",        # 商家运营
    "after_sales": "123456",     # 商家售后客服
    "han": "123456",             # 顾客 C001 韩梅梅
    "lei": "123456",             # 顾客 C002 李雷
}


class MerchantApiError(Exception):
    """调用模拟商家 API 失败（中文提示，便于直接展示给用户）。"""

    def __init__(self, message: str, error_code: str = "CLIENT_ERROR",
                 status_code: int | None = None, request_id: str = ""):
        self.error_code = error_code
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(message)


# HTTP 状态码 → 中文提示（用户可理解的兜底文案）
_STATUS_HINTS = {
    401: "登录已失效，请重新登录",
    403: "无权执行该操作（或不是本人数据）",
    404: "数据不存在或接口路径错误",
    409: "当前状态不允许执行该操作",
    422: "请求参数有误",
    429: "请求过于频繁，请稍后再试",
    503: "商家服务暂时不可用，请稍后再试",
}


class MerchantApiClient:
    """模拟商家 API 统一客户端（requests 优先，缺失时回退标准库 urllib）。"""

    API_PREFIX = "/mock-api/v1"

    def __init__(
        self,
        base_url: str = "",
        token: str = "",
        username: str = "",
        password: str = "",
        timeout: float = 10.0,
        retries: int = 2,
    ) -> None:
        # base_url 形如 http://localhost:8090（末尾 / 与 /mock-api/v1 前缀均可自动兼容）
        base_url = (base_url or os.environ.get("MERCHANT_API_BASE_URL")
                    or "http://localhost:8090").rstrip("/")
        if base_url.endswith(self.API_PREFIX):
            base_url = base_url[: -len(self.API_PREFIX)]
        self.base_url = base_url
        self.api_root = base_url + self.API_PREFIX
        self.timeout = timeout
        self.retries = max(0, retries)

        # token：显式传入 > 环境变量；没配则用账号密码自动登录
        self._token = token or os.environ.get("MERCHANT_API_TOKEN", "")
        self._username = username or os.environ.get("MERCHANT_API_USERNAME", "")
        self._password = password or os.environ.get("MERCHANT_API_PASSWORD", "")
        if not self._token and not self._username:
            self._username, self._password = "operator", "123456"

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------
    def _http(self, method: str, path: str, body: dict | None = None,
              params: dict | None = None, auth_header: bool = True) -> tuple[int, dict]:
        """发起一次 HTTP 请求，返回 (status, json)。失败抛 MerchantApiError。"""
        url = self.api_root + path
        if params:
            clean = {k: v for k, v in params.items() if v not in ("", None)}
            if clean:
                from urllib.parse import urlencode
                url += "?" + urlencode(clean)

        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth_header and self._token:
            headers["Authorization"] = "Bearer " + self._token

        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return e.code, {"success": False, "message": raw or str(e),
                                "error_code": f"HTTP_{e.code}"}
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            raise MerchantApiError(
                f"无法连接模拟商家服务（{self.base_url}）：{reason}。"
                "请确认服务已启动：cd sj && python main.py", "SERVICE_UNREACHABLE") from e
        except TimeoutError:
            raise MerchantApiError(
                f"调用模拟商家服务超时（{self.timeout}s）：{method} {path}",
                "TIMEOUT") from None

    def request(self, method: str, path: str, body: dict | None = None,
                params: dict | None = None) -> Any:
        """统一入口：带鉴权、401 自动重登、GET 幂等重试、统一错误转换。"""
        attempts = 1 + (self.retries if method.upper() in ("GET", "HEAD") else 0)
        last_error: MerchantApiError | None = None
        for i in range(attempts):
            try:
                status, payload = self._http(method, path, body, params)
            except MerchantApiError as e:
                last_error = e
                if i < attempts - 1:
                    time.sleep(0.6 * (i + 1))
                    continue
                raise

            # 401 → 尝试用账号密码重新登录一次后重试
            if status == 401 and self._username and not path.startswith("/auth/"):
                try:
                    self.login(self._username, self._password)
                    status, payload = self._http(method, path, body, params)
                except MerchantApiError:
                    pass

            if not payload.get("success"):
                code = payload.get("error_code") or f"HTTP_{status}"
                msg = payload.get("message") or _STATUS_HINTS.get(
                    status, f"请求失败（HTTP {status}）")
                raise MerchantApiError(msg, code, status,
                                        payload.get("request_id", ""))
            return payload.get("data")
        raise last_error or MerchantApiError("请求失败", "CLIENT_ERROR")

    # ------------------------------------------------------------------
    # 鉴权
    # ------------------------------------------------------------------
    def login(self, username: str = "", password: str = "") -> dict:
        """登录获取 Bearer Token（成功后自动记住，后续请求自动携带）。"""
        username = username or self._username or "operator"
        password = password or self._password or DEMO_ACCOUNTS.get(username, "123456")
        status, payload = self._http("POST", "/auth/token",
                                     {"username": username, "password": password},
                                     auth_header=False)
        if not payload.get("success"):
            raise MerchantApiError(payload.get("message") or "账号或密码错误",
                                   payload.get("error_code", "AUTH_FAILED"), status)
        data = payload["data"]
        self._token = data["access_token"]
        self._username, self._password = username, password
        return data

    def ensure_token(self) -> str:
        """确保持有可用 token（无 token 时自动登录一次）。"""
        if not self._token:
            self.login()
        return self._token

    def me(self) -> dict:
        """当前登录身份（角色/权限/过期时间）。"""
        return self.request("GET", "/auth/me")

    def refresh_token(self) -> dict:
        """刷新 token（旧 token 吊销，返回新 token）。"""
        data = self.request("POST", "/auth/refresh")
        self._token = data["access_token"]
        return data

    @property
    def token(self) -> str:
        return self._token

    # ------------------------------------------------------------------
    # 商品 / 库存
    # ------------------------------------------------------------------
    def get_products(self, keyword: str = "", category: str = "", status: str = "",
                     page: int = 1, page_size: int = 20) -> dict:
        """商品列表（支持关键词/分类/状态筛选，分页）。"""
        return self.request("GET", "/products", params=locals_dict(locals()))

    def get_product(self, sku: str) -> dict:
        """商品详情（含库存/尺码/卖点/场景等 V4 兼容字段）。"""
        return self.request("GET", f"/products/{sku}")

    def get_inventory(self, sku: str) -> dict:
        """单个商品库存详情（含最近 10 条调整流水）。"""
        return self.request("GET", f"/inventory/{sku}")

    def search_inventory(self, keyword: str = "", low_stock_only: bool = False,
                         page: int = 1, page_size: int = 20) -> dict:
        """库存列表（可仅看低于安全库存的商品）。"""
        return self.request("GET", "/inventory", params={
            "keyword": keyword, "low_stock_only": str(bool(low_stock_only)).lower(),
            "page": page, "page_size": page_size})

    # ------------------------------------------------------------------
    # 订单 / 物流
    # ------------------------------------------------------------------
    def search_orders(self, customer_id: str = "", status: str = "", keyword: str = "",
                      page: int = 1, page_size: int = 20) -> dict:
        """订单列表（顾客 Token 自动只返回本人订单）。"""
        return self.request("GET", "/orders", params=locals_dict(locals()))

    def get_order(self, order_id: str) -> dict:
        """订单详情（含商品明细、脱敏手机号、操作日志与物流信息）。"""
        return self.request("GET", f"/orders/{order_id}")

    def create_order(self, customer_id: str, items: list[dict],
                     receiver_name: str, receiver_phone: str, address: str,
                     discount: float = 0, freight: float = 0) -> dict:
        """创建订单（待付款）。items 形如 [{"sku": "SOCK-001", "qty": 2}]。"""
        return self.request("POST", "/orders", {
            "customer_id": customer_id, "items": items,
            "receiver_name": receiver_name, "receiver_phone": receiver_phone,
            "address": address, "discount": discount, "freight": freight})

    def pay_order(self, order_id: str) -> dict:
        """支付订单：待付款 → 已付款（库存充足自动进入待发货）。"""
        return self.request("POST", f"/orders/{order_id}/pay")

    def cancel_order(self, order_id: str, reason: str = "顾客主动取消") -> dict:
        """取消订单（已支付的自动原路退款）。"""
        return self.request("POST", f"/orders/{order_id}/cancel", {"reason": reason})

    def ship_order(self, order_id: str, carrier: str = "顺丰") -> dict:
        """商家发货（自动扣减库存并创建物流单）。"""
        return self.request("POST", f"/orders/{order_id}/ship", {"carrier": carrier})

    def confirm_order(self, order_id: str) -> dict:
        """确认收货：已发货/运输中 → 已签收。"""
        return self.request("POST", f"/orders/{order_id}/confirm")

    def complete_order(self, order_id: str) -> dict:
        """交易完成：已签收 → 已完成。"""
        return self.request("POST", f"/orders/{order_id}/complete")

    def get_logistics(self, order_id: str) -> dict:
        """查询物流轨迹（traces: status/location/time，与 V4 兼容）。"""
        return self.request("GET", f"/logistics/{order_id}")

    def advance_logistics(self, order_id: str, force_exception: bool = False) -> dict:
        """推进物流到下一节点（演示用）。"""
        return self.request("POST", f"/logistics/{order_id}/advance",
                            {"force_exception": force_exception})

    # ------------------------------------------------------------------
    # 售后 / 退款
    # ------------------------------------------------------------------
    def create_after_sale(self, order_id: str, customer_id: str, type_: str,
                          reason: str, description: str = "",
                          amount: float = 0) -> dict:
        """申请售后。type_: 仅退款/退货退款/换货/维修/投诉。

        服务端自动校验：订单归属、订单状态、售后期限（签收 7 天内，
        质量问题除外）、重复申请。
        """
        return self.request("POST", "/after-sales", {
            "order_id": order_id, "customer_id": customer_id, "type": type_,
            "reason": reason, "description": description, "amount": amount})

    def get_after_sale(self, after_sale_id: str) -> dict:
        """售后单详情（含审核日志、关联退款与订单状态）。"""
        return self.request("GET", f"/after-sales/{after_sale_id}")

    def search_after_sales(self, customer_id: str = "", status: str = "",
                           type_: str = "", order_id: str = "",
                           page: int = 1, page_size: int = 20) -> dict:
        """售后单列表。"""
        params = {"customer_id": customer_id, "status": status, "type": type_,
                  "order_id": order_id, "page": page, "page_size": page_size}
        return self.request("GET", "/after-sales", params=params)

    def create_refund(self, order_id: str, customer_id: str, reason: str = "",
                      amount: float = 0, after_sale_id: str = "") -> dict:
        """发起退款（自动执行 R0-R8 风控：自动通过/自动驳回/转人工）。"""
        return self.request("POST", "/refunds", {
            "order_id": order_id, "customer_id": customer_id, "reason": reason,
            "amount": amount, "after_sale_id": after_sale_id})

    def get_refund(self, refund_id: str) -> dict:
        """退款单详情（含风控结果 rule_hit/risk_result、审核与打款日志）。"""
        return self.request("GET", f"/refunds/{refund_id}")

    def search_refunds(self, customer_id: str = "", order_id: str = "",
                       status: str = "", page: int = 1, page_size: int = 20) -> dict:
        """退款单列表。"""
        return self.request("GET", "/refunds", params={
            "customer_id": customer_id, "order_id": order_id, "status": status,
            "page": page, "page_size": page_size})

    # ------------------------------------------------------------------
    # 工单 / 顾客
    # ------------------------------------------------------------------
    def create_ticket(self, customer_id: str, type_: str, description: str,
                      order_id: str = "", priority: int = 2,
                      summary: str = "") -> dict:
        """创建工单。type_: 咨询/售后/投诉/建议/其他；priority: 1低/2普通/3加急。"""
        return self.request("POST", "/tickets", {
            "customer_id": customer_id, "type": type_, "description": description,
            "order_id": order_id, "priority": priority, "summary": summary})

    def get_ticket(self, ticket_id: str) -> dict:
        """工单详情（含处理记录）。"""
        return self.request("GET", f"/tickets/{ticket_id}")

    def search_tickets(self, customer_id: str = "", status: str = "",
                       type_: str = "", priority: int = 0,
                       page: int = 1, page_size: int = 20) -> dict:
        """工单列表。"""
        return self.request("GET", "/tickets", params={
            "customer_id": customer_id, "status": status, "type": type_,
            "priority": priority, "page": page, "page_size": page_size})

    def reply_ticket(self, ticket_id: str, content: str) -> dict:
        """客服回复工单（追加处理记录）。"""
        return self.request("POST", f"/tickets/{ticket_id}/reply",
                            {"content": content})

    def close_ticket(self, ticket_id: str, resolution: str = "") -> dict:
        """关闭工单。"""
        return self.request("POST", f"/tickets/{ticket_id}/close",
                            {"resolution": resolution})

    def get_customer_profile(self, customer_id: str) -> dict:
        """顾客详情（脱敏：手机号/地址/姓名；顾客 Token 仅可查自己）。"""
        return self.request("GET", f"/customers/{customer_id}")

    def get_customer_orders(self, customer_id: str, page: int = 1,
                            page_size: int = 20) -> dict:
        """顾客的订单列表。"""
        return self.request("GET", f"/customers/{customer_id}/orders",
                            params={"page": page, "page_size": page_size})

    def get_customer_after_sales(self, customer_id: str, page: int = 1,
                                 page_size: int = 20) -> dict:
        """顾客的售后记录。"""
        return self.request("GET", f"/customers/{customer_id}/after-sales",
                            params={"page": page, "page_size": page_size})

    def get_customer_tickets(self, customer_id: str, page: int = 1,
                             page_size: int = 20) -> dict:
        """顾客的工单记录。"""
        return self.request("GET", f"/customers/{customer_id}/tickets",
                            params={"page": page, "page_size": page_size})

    # ------------------------------------------------------------------
    # 看板 / 系统
    # ------------------------------------------------------------------
    def dashboard_summary(self) -> dict:
        """经营总览（今日订单/销售额/待发货/售后/工单/退款/库存预警）。"""
        return self.request("GET", "/dashboard/summary")

    def health(self) -> dict:
        """健康检查（不需要登录）。"""
        status, payload = self._http("GET", "/health", auth_header=False)
        return payload.get("data") or payload


def locals_dict(ns: dict) -> dict:
    """把方法参数字典整理成干净的 query 参数（去掉 type_ → type 等别名）。"""
    out = {}
    for k, v in ns.items():
        if k == "type_":
            k = "type"
        if k not in ("self",) and v not in ("", None):
            out[k] = v
    return out


# ----------------------------------------------------------------------
# 模块级默认客户端（V4 引用最省事的方式）
# ----------------------------------------------------------------------
_default_client: MerchantApiClient | None = None


def get_default_client() -> MerchantApiClient:
    """获取进程级共享客户端（读取环境变量配置，惰性初始化）。"""
    global _default_client
    if _default_client is None:
        _default_client = MerchantApiClient()
    return _default_client


class _ModuleClientProxy:
    """让 `client.get_order(...)` 直接可用的轻量代理。"""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_default_client(), name)

    def __repr__(self) -> str:  # pragma: no cover
        c = get_default_client()
        return f"<MerchantApiClientProxy {c.api_root}>"


client = _ModuleClientProxy()


# ----------------------------------------------------------------------
# 命令行自检
# ----------------------------------------------------------------------
def _selfcheck() -> int:
    c = MerchantApiClient()
    print(f"[1/5] 服务连通性  {c.base_url} ... ", end="")
    try:
        h = c.health()
        print(f"OK（{h.get('service')} v{h.get('version')}）")
    except MerchantApiError as e:
        print("失败\n  " + str(e))
        return 1

    print("[2/5] 登录（operator）... ", end="")
    try:
        c.login("operator", "123456")
        print(f"OK（token={c.token[:16]}...）")
    except MerchantApiError as e:
        print("失败\n  " + str(e))
        return 1

    print("[3/5] 商品列表 ... ", end="")
    try:
        d = c.get_products(page_size=3)
        print(f"OK（共 {d['total']} 个商品，示例 {d['list'][0]['sku']}）")
    except MerchantApiError as e:
        print("失败\n  " + str(e))
        return 1

    print("[4/5] 订单列表 ... ", end="")
    try:
        d = c.search_orders(page_size=3)
        print(f"OK（共 {d['total']} 个订单，示例 {d['list'][0]['order_id']}）")
    except MerchantApiError as e:
        print("失败\n  " + str(e))
        return 1

    print("[5/5] 看板总览 ... ", end="")
    try:
        d = c.dashboard_summary()
        print(f"OK（今日订单 {d['today_order_count']}，销售额 ¥{d['today_sales_amount']}）")
    except MerchantApiError as e:
        print("失败\n  " + str(e))
        return 1

    print("\n全部通过：模拟商家服务与客户端工作正常 ✔")
    return 0


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        raise SystemExit(_selfcheck())
    print(__doc__)
