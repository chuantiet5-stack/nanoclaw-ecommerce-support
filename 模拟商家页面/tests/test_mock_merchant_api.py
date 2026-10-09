# -*- coding: utf-8 -*-
"""模拟商家 API 集成测试（pytest）。

覆盖：鉴权 / 统一响应与错误结构 / 商品 / 库存 / 订单状态机 / 物流 /
售后 / 退款风控 R0-R8 / 工单 / 顾客脱敏 / 看板 / 故障模拟 / 商家后台页面。

运行：python -m pytest tests/ -v
（测试使用独立的临时 SQLite 数据库，不影响 data/merchant_mock.db 演示数据）
"""

import os
import sys
import tempfile
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MERCHANT_MOCK_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from mock_merchant.app import create_app  # noqa: E402

app = create_app()
c = TestClient(app)


def login(username: str, password: str = "123456") -> dict:
    r = c.post("/mock-api/v1/auth/token", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["data"]["access_token"]}


H_ADMIN = login("merchant_admin")   # 商家管理员
H_OP = login("operator")            # 商家运营
H_AS = login("after_sales")         # 售后客服
H_C1 = login("han")                 # 顾客 C001 韩梅梅
H_C2 = login("lei")                 # 顾客 C002 李雷


def restock(sku: str, qty: int = 50) -> None:
    """测试辅助：给商品补货（保证各测试用例库存互不干扰）。"""
    r = c.post("/mock-api/v1/inventory/adjust", headers=H_OP,
               json={"sku": sku, "change_qty": qty, "reason": "测试补货"})
    assert r.status_code == 200, r.text


def create_order(customer_id="C001", sku="SOCK-001", qty=1,
                 headers=H_OP, receiver="韩梅梅",
                 restock_qty: int | None = 21) -> dict:
    """创建一笔新订单（待付款，默认自动补足库存）并返回详情。"""
    if restock_qty is not None:
        restock(sku, restock_qty)
    r = c.post("/mock-api/v1/orders", headers=headers, json={
        "customer_id": customer_id, "items": [{"sku": sku, "qty": qty}],
        "receiver_name": receiver, "receiver_phone": "13812345678",
        "address": "北京市朝阳区",
    })
    assert r.status_code == 201, r.text
    return r.json()["data"]


def pay_and_ship(order_id: str, headers=H_OP) -> None:
    """支付并发货。"""
    r = c.post(f"/mock-api/v1/orders/{order_id}/pay", headers=headers)
    assert r.status_code == 200, r.text
    r = c.post(f"/mock-api/v1/orders/{order_id}/ship", headers=headers,
               json={"carrier": "顺丰"})
    assert r.status_code == 200, r.text


def sign_order(order_id: str, headers=H_OP) -> None:
    """推进物流并签收。"""
    r = c.post(f"/mock-api/v1/logistics/{order_id}/sign", headers=headers)
    assert r.status_code == 200, r.text


def find_seed_order(customer_id: str, status: str, max_days: int | None = None):
    """从种子订单中挑一笔指定状态的订单（可限制签收天数）。"""
    r = c.get("/mock-api/v1/orders", headers=H_OP,
              params={"customer_id": customer_id, "status": status, "page_size": 50})
    lst = r.json()["data"]["list"]
    if max_days is not None:
        lst = [o for o in lst if o["signed_at"] and
               (datetime.now() - datetime.strptime(o["signed_at"][:10], "%Y-%m-%d")).days <= max_days]
    return lst[0] if lst else None


# ======================================================================
# 鉴权
# ======================================================================

class TestAuth:
    def test_login_ok_structure(self):
        r = c.post("/mock-api/v1/auth/token",
                   json={"username": "operator", "password": "123456"})
        body = r.json()
        assert r.status_code == 200
        assert body["success"] is True
        assert "request_id" in body and "timestamp" in body
        d = body["data"]
        assert d["access_token"].startswith("mtk_")
        assert d["token_type"] == "Bearer"

    def test_login_wrong_password(self):
        r = c.post("/mock-api/v1/auth/token",
                   json={"username": "operator", "password": "bad"})
        assert r.status_code == 401
        body = r.json()
        assert body["success"] is False and body["data"] is None
        assert body["error_code"] == "AUTH_FAILED"

    def test_me(self):
        r = c.get("/mock-api/v1/auth/me", headers=H_AS)
        d = r.json()["data"]
        assert d["username"] == "after_sales"
        assert d["role"] == "after_sales"
        assert "orders:read" in d["scope"] or "aftersales:read" in d["scope"]

    def test_protected_without_token(self):
        r = c.get("/mock-api/v1/orders")
        assert r.status_code == 401
        assert r.json()["error_code"] == "UNAUTHORIZED"

    def test_invalid_token(self):
        r = c.get("/mock-api/v1/orders",
                  headers={"Authorization": "Bearer mtk_invalid"})
        assert r.status_code == 401

    def test_refresh_revokes_old_token(self):
        # 用独立 token 测试刷新，避免吊销模块级共享 token
        h = login("lei")
        r = c.post("/mock-api/v1/auth/refresh", headers=h)
        assert r.status_code == 200
        new_token = r.json()["data"]["access_token"]
        r = c.get("/mock-api/v1/auth/me", headers=h)
        assert r.status_code == 401  # 旧 token 已吊销
        r = c.get("/mock-api/v1/auth/me",
                  headers={"Authorization": "Bearer " + new_token})
        assert r.status_code == 200

    def test_customer_cannot_write_products(self):
        r = c.post("/mock-api/v1/products", headers=H_C1, json={
            "sku": "X-001", "name": "测试", "price": 1})
        assert r.status_code == 403
        assert r.json()["error_code"] == "FORBIDDEN"


# ======================================================================
# 统一错误结构
# ======================================================================

class TestErrorEnvelope:
    def test_404_route(self):
        r = c.get("/mock-api/v1/no-such-api", headers=H_OP)
        assert r.status_code == 404
        body = r.json()
        assert body["success"] is False and body["error_code"]

    def test_422_validation(self):
        r = c.post("/mock-api/v1/auth/token", json={"username": "operator"})
        assert r.status_code == 422
        assert r.json()["error_code"] == "VALIDATION_ERROR"

    def test_404_resource(self):
        r = c.get("/mock-api/v1/orders/NOT-EXIST", headers=H_OP)
        assert r.status_code == 404
        assert r.json()["error_code"] == "ORDER_NOT_FOUND"


# ======================================================================
# 商品 / 库存
# ======================================================================

class TestProducts:
    def test_list_and_search(self):
        r = c.get("/mock-api/v1/products", headers=H_OP, params={"page_size": 50})
        d = r.json()["data"]
        assert d["total"] >= 12
        p = d["list"][0]
        # V4 兼容字段
        for k in ("sku", "name", "price", "stock", "sizes", "scenes", "link"):
            assert k in p
        r = c.get("/mock-api/v1/products", headers=H_OP,
                  params={"keyword": "运动袜"})
        assert r.json()["data"]["total"] >= 1

    def test_create_and_duplicate(self):
        r = c.post("/mock-api/v1/products", headers=H_OP, json={
            "sku": "TEST-001", "name": "测试商品", "category": "测试",
            "price": 19.9, "stock": 5})
        assert r.status_code == 201
        assert r.json()["data"]["available_stock"] == 5
        r = c.post("/mock-api/v1/products", headers=H_OP, json={
            "sku": "TEST-001", "name": "重复", "price": 1})
        assert r.status_code == 409
        assert r.json()["error_code"] == "SKU_DUPLICATED"

    def test_update_and_status(self):
        c.post("/mock-api/v1/products", headers=H_OP, json={
            "sku": "TEST-002", "name": "上下架测试", "price": 9.9, "stock": 3})
        r = c.put("/mock-api/v1/products/TEST-002", headers=H_OP,
                  json={"price": 8.8})
        assert r.json()["data"]["price"] == 8.8
        r = c.patch("/mock-api/v1/products/TEST-002/status", headers=H_OP,
                    json={"status": "下架"})
        assert r.json()["data"]["status"] == "下架"
        # 下架商品不能下单
        r = c.post("/mock-api/v1/orders", headers=H_OP, json={
            "customer_id": "C001", "items": [{"sku": "TEST-002", "qty": 1}],
            "receiver_name": "韩梅梅", "receiver_phone": "13812345678",
            "address": "北京市朝阳区"})
        assert r.status_code == 409
        assert r.json()["error_code"] == "PRODUCT_OFF_SHELF"

    def test_delete_protection(self):
        # 被订单引用的商品不允许删除
        r = c.delete("/mock-api/v1/products/SOCK-001", headers=H_ADMIN)
        assert r.status_code == 409
        assert r.json()["error_code"] == "PRODUCT_IN_USE"
        # 非管理员不能删除
        r = c.delete("/mock-api/v1/products/TEST-001", headers=H_OP)
        assert r.status_code == 403


class TestInventory:
    def test_adjust_in_and_out(self):
        c.post("/mock-api/v1/products", headers=H_OP, json={
            "sku": "TEST-100", "name": "库存测试", "price": 5, "stock": 10})
        r = c.post("/mock-api/v1/inventory/adjust", headers=H_OP,
                   json={"sku": "TEST-100", "change_qty": 5, "reason": "补货"})
        assert r.json()["data"]["stock"] == 15
        r = c.post("/mock-api/v1/inventory/adjust", headers=H_OP,
                   json={"sku": "TEST-100", "change_qty": -15, "reason": "清仓"})
        assert r.status_code == 200
        assert r.json()["data"]["stock"] == 0
        # 出库超过库存 → 409
        r = c.post("/mock-api/v1/inventory/adjust", headers=H_OP,
                   json={"sku": "TEST-100", "change_qty": -1})
        assert r.status_code == 409
        assert r.json()["error_code"] == "INSUFFICIENT_STOCK"

    def test_reserve_and_release(self):
        restock("TEST-100", 10)  # 上一用例出库清零，先补货
        r = c.post("/mock-api/v1/inventory/reserve", headers=H_OP,
                   json={"sku": "TEST-100", "qty": 3, "order_id": "TEST-ORDER"})
        d = r.json()["data"]
        assert d["locked_stock"] == 3
        # 可售不足 → 409（超卖拦截）
        r = c.post("/mock-api/v1/inventory/reserve", headers=H_OP,
                   json={"sku": "TEST-100", "qty": 99})
        assert r.status_code == 409
        # 调整后低于锁定 → 409
        r = c.post("/mock-api/v1/inventory/adjust", headers=H_OP,
                   json={"sku": "TEST-100", "change_qty": -10})
        assert r.status_code == 409
        # 释放超量 → 409
        r = c.post("/mock-api/v1/inventory/release", headers=H_OP,
                   json={"sku": "TEST-100", "qty": 5})
        assert r.status_code == 409
        assert r.json()["error_code"] == "INVALID_RELEASE"
        # 正常释放
        r = c.post("/mock-api/v1/inventory/release", headers=H_OP,
                   json={"sku": "TEST-100", "qty": 3})
        assert r.json()["data"]["locked_stock"] == 0

    def test_low_stock_filter_and_logs(self):
        r = c.get("/mock-api/v1/inventory", headers=H_OP,
                  params={"low_stock_only": "true", "page_size": 50})
        for p in r.json()["data"]["list"]:
            assert p["low_stock_warning"] is True
        r = c.get("/mock-api/v1/inventory/TEST-100", headers=H_OP)
        logs = r.json()["data"]["recent_logs"]
        assert logs and {"action", "change_qty", "before_stock",
                         "after_stock"} <= set(logs[0])


# ======================================================================
# 订单生命周期 + 状态机
# ======================================================================

class TestOrderLifecycle:
    def test_create_order_validations(self):
        # 顾客不存在
        r = c.post("/mock-api/v1/orders", headers=H_OP, json={
            "customer_id": "C999", "items": [{"sku": "SOCK-001", "qty": 1}],
            "receiver_name": "x", "receiver_phone": "1", "address": "x"})
        assert r.status_code == 404
        assert r.json()["error_code"] == "CUSTOMER_NOT_FOUND"
        # 商品不存在
        r = c.post("/mock-api/v1/orders", headers=H_OP, json={
            "customer_id": "C001", "items": [{"sku": "NO-SKU", "qty": 1}],
            "receiver_name": "x", "receiver_phone": "1", "address": "x"})
        assert r.status_code == 404
        assert r.json()["error_code"] == "PRODUCT_NOT_FOUND"
        # 顾客不能替别人下单
        r = c.post("/mock-api/v1/orders", headers=H_C1, json={
            "customer_id": "C002", "items": [{"sku": "SOCK-001", "qty": 1}],
            "receiver_name": "x", "receiver_phone": "1", "address": "x"})
        assert r.status_code == 403

    def test_full_lifecycle(self):
        order = create_order(qty=2)
        oid = order["order_id"]
        assert order["status"] == "待付款"
        assert order["items"][0]["qty"] == 2

        # 非法跳转：待付款不能发货后直接完成
        r = c.post(f"/mock-api/v1/orders/{oid}/complete", headers=H_OP)
        assert r.status_code == 409
        assert r.json()["error_code"] == "INVALID_STATUS_TRANSITION"

        # 支付 → 待发货（自动锁库存）
        r = c.post(f"/mock-api/v1/orders/{oid}/pay", headers=H_OP)
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "待发货"

        # 重复支付 → 409
        r = c.post(f"/mock-api/v1/orders/{oid}/pay", headers=H_OP)
        assert r.status_code == 409

        # 发货 → 已发货 + 自动创建物流单
        r = c.post(f"/mock-api/v1/orders/{oid}/ship", headers=H_OP,
                   json={"carrier": "中通"})
        d = r.json()["data"]
        assert d["status"] == "已发货" and d["shipped_at"]
        assert d["logistics"]["carrier"] == "中通"
        assert d["logistics"]["traces"][0]["status"] == "待发货"

        # 非法快递公司
        r = c.post(f"/mock-api/v1/orders/{oid}/cancel", headers=H_OP,
                   json={"reason": "x"})
        assert r.status_code == 409  # 已发货不能取消

        # 签收 → 已完成
        r = c.post(f"/mock-api/v1/logistics/{oid}/advance", headers=H_OP, json={})
        assert r.json()["data"]["status"] == "已揽收"
        sign_order(oid)
        r = c.get(f"/mock-api/v1/orders/{oid}", headers=H_OP)
        assert r.json()["data"]["status"] == "已签收"
        r = c.post(f"/mock-api/v1/orders/{oid}/complete", headers=H_OP)
        assert r.json()["data"]["status"] == "已完成"
        # 已完成后不能重复完成
        r = c.post(f"/mock-api/v1/orders/{oid}/complete", headers=H_OP)
        assert r.status_code == 409

    def test_cancel_unpaid(self):
        order = create_order()
        r = c.post(f"/mock-api/v1/orders/{order['order_id']}/cancel",
                   headers=H_OP, json={"reason": "不想要了"})
        assert r.json()["data"]["status"] == "已取消"
        assert r.json()["data"]["cancel_reason"] == "不想要了"

    def test_customer_isolation(self):
        # 顾客只能看自己的订单
        order = find_seed_order("C001", "已签收")
        r = c.get(f"/mock-api/v1/orders/{order['order_id']}", headers=H_C2)
        assert r.status_code == 403
        assert r.json()["error_code"] == "ORDER_ACCESS_DENIED"
        r = c.get(f"/mock-api/v1/orders/{order['order_id']}", headers=H_C1)
        assert r.status_code == 200
        # 顾客列表只返回本人订单
        r = c.get("/mock-api/v1/orders", headers=H_C2)
        for o in r.json()["data"]["list"]:
            assert o["customer_id"] == "C002"

    def test_phone_masked(self):
        r = c.get("/mock-api/v1/orders", headers=H_OP, params={"page_size": 5})
        for o in r.json()["data"]["list"]:
            phone = o["receiver_phone"]
            if len(phone) == 11:
                assert phone[3:7] == "****"


# ======================================================================
# 物流
# ======================================================================

class TestLogistics:
    def test_trace_structure_and_flow(self):
        order = create_order(sku="TEST-100", qty=1)
        oid = order["order_id"]
        pay_and_ship(oid)
        r = c.get(f"/mock-api/v1/logistics/{oid}", headers=H_OP)
        d = r.json()["data"]
        assert {"order_id", "carrier", "tracking_no", "status",
                "current_location", "traces"} <= set(d)
        for t in d["traces"]:
            assert {"status", "location", "time"} <= set(t)
        # 逐步推进
        statuses = ["已揽收", "到达分拨中心", "运输中", "派送中"]
        for expect in statuses:
            r = c.post(f"/mock-api/v1/logistics/{oid}/advance", headers=H_OP, json={})
            assert r.json()["data"]["status"] == expect
        # 模拟派送异常
        r = c.post(f"/mock-api/v1/logistics/{oid}/advance", headers=H_OP,
                   json={"force_exception": True})
        assert r.json()["data"]["status"] == "派送异常"
        # 签收
        r = c.post(f"/mock-api/v1/logistics/{oid}/sign", headers=H_OP)
        assert r.json()["data"]["status"] == "已签收"
        # 已签收不能推进
        r = c.post(f"/mock-api/v1/logistics/{oid}/advance", headers=H_OP, json={})
        assert r.status_code == 409

    def test_logistics_not_found(self):
        r = c.get("/mock-api/v1/logistics/NOT-EXIST", headers=H_OP)
        assert r.status_code == 404
        assert r.json()["error_code"] == "LOGISTICS_NOT_FOUND"

    def test_customer_isolation(self):
        order = find_seed_order("C001", "已发货")
        r = c.get(f"/mock-api/v1/logistics/{order['order_id']}", headers=H_C2)
        assert r.status_code == 403


# ======================================================================
# 售后
# ======================================================================

class TestAfterSales:
    def _new_signed_order(self, customer_id="C001", sku="TEST-100",
                          restock_qty: int | None = 21):
        order = create_order(customer_id=customer_id, sku=sku, qty=1,
                             receiver="李雷" if customer_id == "C002" else "韩梅梅",
                             restock_qty=restock_qty)
        oid = order["order_id"]
        pay_and_ship(oid)
        sign_order(oid)
        return oid

    def test_apply_validations(self):
        # 订单归属不匹配
        o = find_seed_order("C001", "已签收")
        r = c.post("/mock-api/v1/after-sales", headers=H_OP, json={
            "order_id": o["order_id"], "customer_id": "C002",
            "type": "仅退款", "reason": "不想要了"})
        assert r.status_code == 403
        assert r.json()["error_code"] == "ORDER_OWNERSHIP_MISMATCH"
        # 非法类型
        r = c.post("/mock-api/v1/after-sales", headers=H_OP, json={
            "order_id": o["order_id"], "customer_id": "C001",
            "type": "仅退货", "reason": "x"})
        assert r.status_code == 400
        # 待付款订单不能申请售后
        order = create_order()
        r = c.post("/mock-api/v1/after-sales", headers=H_OP, json={
            "order_id": order["order_id"], "customer_id": "C001",
            "type": "仅退款", "reason": "不想要了"})
        assert r.status_code == 409

    def test_expired_rejected_quality_allowed(self):
        # 种子里 21 天前签收的 C001 订单：无理由 → 超期拒绝
        r = c.get("/mock-api/v1/orders", headers=H_OP,
                  params={"customer_id": "C001", "status": "已签收", "page_size": 50})
        expired = [o for o in r.json()["data"]["list"]
                   if (datetime.now() - datetime.strptime(
                       o["signed_at"][:10], "%Y-%m-%d")).days > 7]
        assert expired, "种子数据应包含超期已签收订单"
        o = expired[0]
        r = c.post("/mock-api/v1/after-sales", headers=H_OP, json={
            "order_id": o["order_id"], "customer_id": "C001",
            "type": "仅退款", "reason": "不想要了"})
        assert r.status_code == 409
        assert r.json()["error_code"] == "AFTER_SALE_EXPIRED"
        # 质量问题不受 7 天限制
        r = c.post("/mock-api/v1/after-sales", headers=H_OP, json={
            "order_id": o["order_id"], "customer_id": "C001",
            "type": "退货退款", "reason": "穿了一次就开胶，质量问题"})
        assert r.status_code == 201

    def test_duplicate_ongoing(self):
        oid = self._new_signed_order()
        r = c.post("/mock-api/v1/after-sales", headers=H_OP, json={
            "order_id": oid, "customer_id": "C001",
            "type": "退货退款", "reason": "尺码不合适"})
        assert r.status_code == 201
        # 再次申请：订单已进入「退款中」，状态机先拦截（重复申请已被状态检查覆盖）
        r = c.post("/mock-api/v1/after-sales", headers=H_OP, json={
            "order_id": oid, "customer_id": "C001",
            "type": "换货", "reason": "尺码不合适"})
        assert r.status_code == 409
        assert r.json()["error_code"] in ("AFTER_SALE_DUPLICATED",
                                          "INVALID_STATUS_TRANSITION")

    def test_return_refund_full_flow(self):
        """退货退款：待审核 → 审核通过(待寄回) → 商家收货 → 完成 → 退款打款 → 退货入库。"""
        restock("TEST-100", 30)
        before = c.get("/mock-api/v1/inventory/TEST-100", headers=H_OP)\
            .json()["data"]["stock"]
        oid = self._new_signed_order(restock_qty=None)  # 不自动补货，精确断言库存
        r = c.post("/mock-api/v1/after-sales", headers=H_AS, json={
            "order_id": oid, "customer_id": "C001",
            "type": "退货退款", "reason": "尺码不合适"})
        d = r.json()["data"]
        assert d["status"] == "待审核"
        asid = d["after_sale_id"]
        # 订单进入退款中
        r = c.get(f"/mock-api/v1/orders/{oid}", headers=H_OP)
        assert r.json()["data"]["status"] == "退款中"

        # 审核通过 → 待寄回
        r = c.post(f"/mock-api/v1/after-sales/{asid}/approve", headers=H_AS,
                   json={"remark": "同意"})
        assert r.json()["data"]["status"] == "待寄回"
        # 商家收货
        r = c.post(f"/mock-api/v1/after-sales/{asid}/receive", headers=H_AS)
        assert r.json()["data"]["status"] == "商家收货"
        # 完成 → 自动创建退款单（人工审核通过，等待打款）
        r = c.post(f"/mock-api/v1/after-sales/{asid}/complete", headers=H_AS)
        d = r.json()["data"]
        assert d["status"] == "已完成"
        refund_id = d["refund_id"]
        assert refund_id
        r = c.get(f"/mock-api/v1/refunds/{refund_id}", headers=H_AS)
        assert r.json()["data"]["status"] == "审核通过"
        # 打款 → 订单已退款 + 退货入库
        r = c.post(f"/mock-api/v1/refunds/{refund_id}/success", headers=H_AS)
        assert r.json()["data"]["status"] == "已退款"
        r = c.get(f"/mock-api/v1/orders/{oid}", headers=H_OP)
        assert r.json()["data"]["status"] == "已退款"
        after = c.get("/mock-api/v1/inventory/TEST-100", headers=H_OP)\
            .json()["data"]["stock"]
        # 发货出库 -1，退货入库 +1 → 库存回到下单前
        assert after == before

    def test_reject_rolls_back_order(self):
        oid = self._new_signed_order()
        r = c.post("/mock-api/v1/after-sales", headers=H_AS, json={
            "order_id": oid, "customer_id": "C001",
            "type": "退货退款", "reason": "尺码不合适"})
        asid = r.json()["data"]["after_sale_id"]
        r = c.post(f"/mock-api/v1/after-sales/{asid}/reject", headers=H_AS,
                   json={"remark": "不符合条件"})
        assert r.json()["data"]["status"] == "审核驳回"
        # 订单回滚到申请前状态（已签收）
        r = c.get(f"/mock-api/v1/orders/{oid}", headers=H_OP)
        assert r.json()["data"]["status"] == "已签收"


# ======================================================================
# 退款 + 风控 R0-R8
# ======================================================================

class TestRefundRisk:
    # 注意：方法顺序即执行顺序。R8（自动通过）会消耗顾客的自动通过次数（R2），
    # 因此所有依赖 manual 判定的用例（R4/R3/R5/R1）必须排在 R8 之前。
    def test_r4_expired_auto_reject(self):
        """超 7 天 + 无理由 → R4 自动驳回（直接发起退款场景）。"""
        r = c.get("/mock-api/v1/orders", headers=H_OP,
                  params={"customer_id": "C001", "status": "已完成",
                          "page_size": 50})
        expired = [o for o in r.json()["data"]["list"]
                   if o["signed_at"] and (datetime.now() - datetime.strptime(
                       o["signed_at"][:10], "%Y-%m-%d")).days > 7]
        assert expired, "种子数据应包含签收超 7 天的已完成订单"
        o = expired[0]
        r = c.post("/mock-api/v1/refunds", headers=H_AS, json={
            "order_id": o["order_id"], "customer_id": "C001", "reason": "不想要了"})
        d = r.json()["data"]
        assert d["rule_hit"] == "R4"
        assert d["status"] == "已驳回"

    def test_r3_quality_manual(self):
        """质量问题 → R3 转人工。"""
        order = create_order(sku="TEST-001", qty=1)
        oid = order["order_id"]
        pay_and_ship(oid)
        sign_order(oid)
        r = c.post("/mock-api/v1/refunds", headers=H_AS, json={
            "order_id": oid, "customer_id": "C001", "reason": "鞋头开胶，质量问题"})
        assert r.json()["data"]["rule_hit"] == "R3"

    def test_r5_large_amount_manual(self):
        """大额 → R5 转人工。"""
        order = create_order(sku="RUN-001", qty=1)  # 远超 50 元
        oid = order["order_id"]
        pay_and_ship(oid)
        sign_order(oid)
        r = c.post("/mock-api/v1/refunds", headers=H_AS, json={
            "order_id": oid, "customer_id": "C001", "reason": "尺码不合适"})
        d = r.json()["data"]
        assert d["rule_hit"] == "R5"
        assert d["status"] == "待审核"
        # 人工审核 → 通过 → 打款
        r = c.post(f"/mock-api/v1/refunds/{d['refund_id']}/approve", headers=H_AS)
        assert r.json()["data"]["status"] == "审核通过"
        r = c.post(f"/mock-api/v1/refunds/{d['refund_id']}/success", headers=H_AS)
        assert r.json()["data"]["status"] == "已退款"

    def test_r1_high_risk_customer(self):
        """C002 高风险（退款率 0.5+，风险分 80）→ R1 转人工。"""
        # 先人工打款种子中 C002 的待审核退款，使已退款率达到 0.75（风险分 80）
        r = c.get("/mock-api/v1/refunds", headers=H_AS,
                  params={"customer_id": "C002", "status": "待审核"})
        pending = r.json()["data"]["list"]
        if pending:
            rid = pending[0]["refund_id"]
            c.post(f"/mock-api/v1/refunds/{rid}/approve", headers=H_AS)
            c.post(f"/mock-api/v1/refunds/{rid}/success", headers=H_AS)
        order = create_order(customer_id="C002", sku="TEST-001", qty=1,
                             receiver="李雷")
        oid = order["order_id"]
        pay_and_ship(oid)
        sign_order(oid)
        r = c.post("/mock-api/v1/refunds", headers=H_AS, json={
            "order_id": oid, "customer_id": "C002", "reason": "尺码不合适"})
        d = r.json()["data"]
        assert d["rule_hit"] == "R1"
        assert d["status"] == "待审核"

    def test_r8_small_amount_auto_approve(self):
        """小额 + 无理由 + 签收 7 天内 + 账号正常 → R8 自动通过。"""
        order = create_order(sku="TEST-001", qty=1)  # 19.9 元
        oid = order["order_id"]
        pay_and_ship(oid)
        sign_order(oid)
        r = c.post("/mock-api/v1/refunds", headers=H_AS, json={
            "order_id": oid, "customer_id": "C001", "reason": "尺码不合适"})
        d = r.json()["data"]
        assert d["rule_hit"] == "R8"
        assert d["review_type"] == "auto"
        assert d["status"] == "审核通过"
        assert d["risk_decision"]["rule"] == "R8"
        # 订单进入退款中
        r = c.get(f"/mock-api/v1/orders/{oid}", headers=H_OP)
        assert r.json()["data"]["status"] == "退款中"
        # 打款
        r = c.post(f"/mock-api/v1/refunds/{d['refund_id']}/success", headers=H_AS)
        assert r.json()["data"]["status"] == "已退款"

    def test_refund_validations(self):
        order = create_order()
        # 未支付不能退款
        r = c.post("/mock-api/v1/refunds", headers=H_AS, json={
            "order_id": order["order_id"], "customer_id": "C001",
            "reason": "不想要了"})
        assert r.status_code == 409
        # 归属不匹配
        o = find_seed_order("C001", "已签收")
        r = c.post("/mock-api/v1/refunds", headers=H_AS, json={
            "order_id": o["order_id"], "customer_id": "C002", "reason": "x"})
        assert r.status_code == 403
        # 金额超限
        order2 = create_order(sku="TEST-001", qty=1)
        pay_and_ship(order2["order_id"])
        sign_order(order2["order_id"])
        r = c.post("/mock-api/v1/refunds", headers=H_AS, json={
            "order_id": order2["order_id"], "customer_id": "C001",
            "amount": 99999, "reason": "尺码不合适"})
        assert r.status_code == 400
        assert r.json()["error_code"] == "INVALID_AMOUNT"

    def test_reject_rolls_back(self):
        """人工驳回退款 → 订单回滚、关联售后关闭。"""
        order = create_order(customer_id="C002", sku="TEST-100", qty=1,
                             receiver="李雷")
        oid = order["order_id"]
        pay_and_ship(oid)
        sign_order(oid)
        # R1 转人工
        r = c.post("/mock-api/v1/refunds", headers=H_AS, json={
            "order_id": oid, "customer_id": "C002", "reason": "不想要了"})
        refund_id = r.json()["data"]["refund_id"]
        assert r.json()["data"]["status"] == "待审核"
        r = c.post(f"/mock-api/v1/refunds/{refund_id}/reject", headers=H_AS,
                   json={"remark": "不符合条件"})
        assert r.json()["data"]["status"] == "已驳回"
        # 订单回滚到已签收
        r = c.get(f"/mock-api/v1/orders/{oid}", headers=H_OP)
        assert r.json()["data"]["status"] == "已签收"

    def test_cancel_paid_auto_refund(self):
        """已支付订单取消 → 自动原路退款（即时退款通道）。"""
        order = create_order(sku="TEST-001", qty=1)
        oid = order["order_id"]
        r = c.post(f"/mock-api/v1/orders/{oid}/pay", headers=H_OP)
        assert r.status_code == 200
        r = c.post(f"/mock-api/v1/orders/{oid}/cancel", headers=H_OP,
                   json={"reason": "顾客取消"})
        d = r.json()["data"]
        assert d["status"] == "已取消"
        # 已支付取消 → 自动原路退款
        r = c.get("/mock-api/v1/refunds", headers=H_OP,
                  params={"order_id": oid})
        lst = r.json()["data"]["list"]
        assert lst and lst[0]["status"] == "已退款"


# ======================================================================
# 工单
# ======================================================================

class TestTickets:
    def test_create_type_validation(self):
        r = c.post("/mock-api/v1/tickets", headers=H_OP, json={
            "customer_id": "C001", "type": "无效类型", "description": "x"})
        assert r.status_code == 400

    def test_full_flow(self):
        r = c.post("/mock-api/v1/tickets", headers=H_C1, json={
            "customer_id": "C001", "type": "售后",
            "description": "袜子洗后变形，要求处理", "priority": 3})
        d = r.json()["data"]
        assert d["status"] == "待处理"
        assert d["priority_label"] == "加急"
        tid = d["ticket_id"]
        # 分配
        r = c.post(f"/mock-api/v1/tickets/{tid}/assign", headers=H_AS,
                   json={"assignee": "赵售后"})
        assert r.json()["data"]["status"] == "处理中"
        assert r.json()["data"]["assigned_to"] == "赵售后"
        # 回复
        r = c.post(f"/mock-api/v1/tickets/{tid}/reply", headers=H_AS,
                   json={"content": "已登记，24 小时内处理"})
        records = r.json()["data"]["records"]
        assert any("已登记" in rec["note"] for rec in records)
        # 关闭
        r = c.post(f"/mock-api/v1/tickets/{tid}/close", headers=H_AS,
                   json={"resolution": "已换新处理"})
        assert r.json()["data"]["status"] == "已关闭"
        # 已关闭不能回复
        r = c.post(f"/mock-api/v1/tickets/{tid}/reply", headers=H_AS,
                   json={"content": "x"})
        assert r.status_code == 409

    def test_customer_isolation(self):
        r = c.get("/mock-api/v1/tickets", headers=H_C2, params={"page_size": 50})
        for t in r.json()["data"]["list"]:
            assert t["customer_id"] == "C002"
        # C002 访问 C001 的工单 → 403
        r = c.get("/mock-api/v1/tickets", headers=H_C1, params={"page_size": 50})
        tid = r.json()["data"]["list"][0]["ticket_id"]
        r = c.get(f"/mock-api/v1/tickets/{tid}", headers=H_C2)
        assert r.status_code == 403


# ======================================================================
# 顾客（脱敏与隔离）
# ======================================================================

class TestCustomers:
    def test_list_staff_only_and_masked(self):
        r = c.get("/mock-api/v1/customers", headers=H_C1)
        assert r.status_code == 403
        r = c.get("/mock-api/v1/customers", headers=H_OP)
        lst = r.json()["data"]["list"]
        assert len(lst) >= 2
        for cu in lst:
            assert cu["phone"][3:7] == "****"
            assert "*" in cu["name"] or len(cu["name"]) <= 3
            assert "password" not in cu and "password_hash" not in cu

    def test_customer_sees_own_plain_data(self):
        r = c.get("/mock-api/v1/customers/C001", headers=H_C1)
        d = r.json()["data"]
        assert d["phone"] == "13812345678"  # 本人可见明文
        assert d["name"] == "韩梅梅"
        # 查别人 → 403
        r = c.get("/mock-api/v1/customers/C002", headers=H_C1)
        assert r.status_code == 403

    def test_customer_nested_resources(self):
        r = c.get("/mock-api/v1/customers/C001/orders", headers=H_OP)
        assert r.json()["data"]["total"] >= 1
        r = c.get("/mock-api/v1/customers/C001/after-sales", headers=H_OP)
        assert "list" in r.json()["data"]
        r = c.get("/mock-api/v1/customers/C001/tickets", headers=H_OP)
        assert "list" in r.json()["data"]


# ======================================================================
# 看板
# ======================================================================

class TestDashboard:
    def test_summary_fields(self):
        r = c.get("/mock-api/v1/dashboard/summary", headers=H_OP)
        d = r.json()["data"]
        for k in ("today_order_count", "today_sales_amount",
                  "pending_shipment_count", "after_sale_order_count",
                  "pending_ticket_count", "total_refund_amount",
                  "low_stock_product_count", "in_stock_product_count"):
            assert k in d

    def test_sales_trend(self):
        r = c.get("/mock-api/v1/dashboard/sales", headers=H_OP)
        d = r.json()["data"]
        assert len(d["trend"]) == 7
        assert "top_products" in d

    def test_customer_service_metrics(self):
        r = c.get("/mock-api/v1/dashboard/customer-service", headers=H_OP)
        d = r.json()["data"]
        assert d["ai_resolution_rate"] == 82.5
        assert d["satisfaction"] == 96.4

    def test_customer_cannot_read_dashboard(self):
        r = c.get("/mock-api/v1/dashboard/summary", headers=H_C1)
        assert r.status_code == 403


# ======================================================================
# 故障模拟
# ======================================================================

class TestFaults:
    def test_admin_only(self):
        r = c.put("/mock-api/v1/admin/faults", headers=H_OP,
                  json={"enabled": True})
        assert r.status_code == 403

    def test_force_401_and_reset(self):
        r = c.put("/mock-api/v1/admin/faults", headers=H_ADMIN,
                  json={"enabled": True, "force_status": 401})
        assert r.status_code == 200
        # 白名单外的接口被强制 401
        r = c.get("/mock-api/v1/products", headers=H_OP)
        assert r.status_code == 401
        assert r.json()["error_code"] == "SIMULATED_401"
        # 白名单内（登录、健康检查）不受影响
        r = c.post("/mock-api/v1/auth/token",
                   json={"username": "operator", "password": "123456"})
        assert r.status_code == 200
        r = c.get("/mock-api/v1/health")
        assert r.status_code == 200
        # 重置恢复
        r = c.post("/mock-api/v1/admin/faults/reset", headers=H_ADMIN)
        assert r.status_code == 200
        r = c.get("/mock-api/v1/products", headers=H_OP)
        assert r.status_code == 200

    def test_service_unavailable(self):
        c.put("/mock-api/v1/admin/faults", headers=H_ADMIN,
              json={"enabled": True, "service_unavailable": True})
        r = c.get("/mock-api/v1/products", headers=H_OP)
        assert r.status_code == 503
        assert r.json()["error_code"] == "SERVICE_UNAVAILABLE"
        c.post("/mock-api/v1/admin/faults/reset", headers=H_ADMIN)

    def test_inventory_shortage_flag(self):
        c.put("/mock-api/v1/admin/faults", headers=H_ADMIN,
              json={"enabled": True, "inventory_shortage": True})
        # 直接创建订单（不用辅助函数，避免补货接口被故障开关拦截）
        r = c.post("/mock-api/v1/orders", headers=H_OP, json={
            "customer_id": "C001", "items": [{"sku": "SOCK-001", "qty": 1}],
            "receiver_name": "韩梅梅", "receiver_phone": "13812345678",
            "address": "北京市朝阳区"})
        assert r.status_code == 201
        r = c.post(f"/mock-api/v1/orders/{r.json()['data']['order_id']}/pay",
                   headers=H_OP)
        assert r.status_code == 409
        assert r.json()["error_code"] == "INSUFFICIENT_STOCK"
        c.post("/mock-api/v1/admin/faults/reset", headers=H_ADMIN)


# ======================================================================
# 商家后台页面 / 系统
# ======================================================================

class TestPages:
    def test_merchant_page(self):
        r = c.get("/merchant")
        assert r.status_code == 200
        assert "模拟商家后台" in r.text
        assert "mock-api/v1" in r.text  # 页面真实调用 API

    def test_health(self):
        r = c.get("/mock-api/v1/health")
        body = r.json()
        assert body["success"] is True
        assert body["data"]["status"] == "ok"

    def test_op_logs(self):
        r = c.get("/mock-api/v1/admin/op-logs", headers=H_ADMIN,
                  params={"page_size": 5})
        d = r.json()["data"]
        assert d["total"] >= 1
        log = d["list"][0]
        assert {"actor", "action", "created_at"} <= set(log)
