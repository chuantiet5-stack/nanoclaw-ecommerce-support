# -*- coding: utf-8 -*-
"""临时冒烟测试脚本（验证后写正式测试）。"""
import os
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, r"c:\Users\asus\Desktop\项目\sj")
os.environ["MERCHANT_MOCK_DB"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient  # noqa: E402

from mock_merchant.app import create_app  # noqa: E402

app = create_app()
c = TestClient(app)


def login(u, p):
    r = c.post("/mock-api/v1/auth/token", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["data"]["access_token"]}


H = login("operator", "123456")
H2 = login("after_sales", "123456")  # 售后角色：审批售后/退款

# ---- 订单全生命周期 ----
r = c.post("/mock-api/v1/orders", headers=H, json={
    "customer_id": "C001", "items": [{"sku": "SOCK-001", "qty": 2}],
    "receiver_name": "韩梅梅", "receiver_phone": "13812345678",
    "address": "北京市朝阳区"})
oid = r.json()["data"]["order_id"]
print("create:", r.status_code, oid, r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/orders/{oid}/pay", headers=H)
print("pay:", r.status_code, r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/orders/{oid}/ship", headers=H, json={"carrier": "中通"})
print("ship:", r.status_code, r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/orders/{oid}/complete", headers=H)
print("illegal complete:", r.status_code, r.json()["error_code"])
r = c.post(f"/mock-api/v1/logistics/{oid}/advance", headers=H, json={})
print("advance:", r.status_code, r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/logistics/{oid}/sign", headers=H)
print("sign:", r.status_code, r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/orders/{oid}/complete", headers=H)
print("complete:", r.status_code, r.json()["data"]["status"])

# ---- 售后：仅退款 → 风控自动通过 ----
r = c.get("/mock-api/v1/orders", headers=H,
          params={"customer_id": "C001", "status": "已签收"})
from datetime import datetime, timedelta
in_window = [o for o in r.json()["data"]["list"]
             if (datetime.now() - datetime.strptime(o["signed_at"][:10], "%Y-%m-%d")).days <= 7]
target = in_window[0]
print("target:", target["order_id"], target["total"], target["signed_at"])
r = c.post("/mock-api/v1/after-sales", headers=H, json={
    "order_id": target["order_id"], "customer_id": "C001",
    "type": "仅退款", "reason": "尺码不合适"})
print("create AS:", r.status_code, r.json()["data"]["status"],
      "| order:", r.json()["data"]["order_status"])
asid = r.json()["data"]["after_sale_id"]
r = c.post(f"/mock-api/v1/after-sales/{asid}/approve", headers=H2, json={})
refund = r.json()["data"].get("refund", {})
print("approve AS:", r.status_code, r.json()["data"]["status"],
      "| refund:", refund.get("status"), refund.get("rule_hit"))
rfid = r.json()["data"]["refund_id"]
r = c.post(f"/mock-api/v1/refunds/{rfid}/approve", headers=H2)
print("refund approve:", r.status_code, r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/refunds/{rfid}/success", headers=H2)
print("refund success:", r.status_code, r.json()["data"]["status"])
r = c.get(f"/mock-api/v1/orders/{target['order_id']}", headers=H)
print("order after refund:", r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/after-sales/{asid}/complete", headers=H2)
print("AS complete:", r.status_code, r.json()["data"]["status"])

# ---- C002 高风险退款 → 转人工 ----
r = c.get("/mock-api/v1/orders", headers=H2,
          params={"customer_id": "C002", "status": "已签收"})
lst = r.json()["data"]["list"]
if lst:
    o2 = lst[0]
    r = c.post("/mock-api/v1/refunds", headers=H2, json={
        "order_id": o2["order_id"], "customer_id": "C002", "reason": "不想要了"})
    print("C002 refund:", r.status_code, r.json()["data"]["status"],
          r.json()["data"]["rule_hit"], "|", r.json()["data"]["risk_result"])
else:
    print("(C002 无可用已签收订单)")

# ---- 顾客归属校验 ----
HH = login("han", "123456")
r = c.get("/mock-api/v1/orders", headers=HH)
own = r.json()["data"]["list"]
r = c.get(f"/mock-api/v1/orders/{own[0]['order_id']}", headers=HH)
print("own order ok:", r.status_code)
HL = login("lei", "123456")
r = c.get(f"/mock-api/v1/orders/{own[0]['order_id']}", headers=HL)
print("other order denied:", r.status_code, r.json()["error_code"])

# ---- 权限：顾客不能改商品 ----
r = c.patch("/mock-api/v1/products/RUN-001/status", headers=HH,
            json={"status": "下架"})
print("customer patch product denied:", r.status_code, r.json()["error_code"])

# ---- 库存：锁定/释放 + 不足 ----
r = c.post("/mock-api/v1/inventory/reserve", headers=H,
           json={"sku": "RUN-006", "qty": 1})
print("reserve OOS:", r.status_code, r.json()["error_code"])
r = c.post("/mock-api/v1/inventory/adjust", headers=H,
           json={"sku": "RUN-006", "change_qty": 5, "reason": "补货"})
print("adjust:", r.status_code, r.json()["data"]["stock"])
r = c.post("/mock-api/v1/inventory/release", headers=H,
           json={"sku": "RUN-001", "qty": 3})
print("release no-lock:", r.status_code, r.json()["error_code"])

# ---- 故障模拟：强制 401 ----
HA = login("merchant_admin", "123456")
r = c.put("/mock-api/v1/admin/faults", headers=HA,
          json={"enabled": True, "force_status": 401})
print("set fault:", r.status_code)
r = c.get("/mock-api/v1/products", headers=H)
print("forced 401:", r.status_code, r.json()["error_code"])
r = c.post("/mock-api/v1/admin/faults/reset", headers=HA)
print("reset faults:", r.status_code)
r = c.get("/mock-api/v1/products", headers=H)
print("after reset:", r.status_code)

# ---- 退货退款全流程（用本次新建并已完成的订单） ----
o3 = {"order_id": oid}
r = c.post("/mock-api/v1/after-sales", headers=H, json={
    "order_id": o3["order_id"], "customer_id": "C001",
    "type": "退货退款", "reason": "洗后掉色，质量问题"})
asid2 = r.json()["data"]["after_sale_id"]
print("AS2 create:", r.status_code, r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/after-sales/{asid2}/approve", headers=H2, json={})
print("AS2 approve:", r.status_code, r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/after-sales/{asid2}/receive", headers=H2)
print("AS2 receive:", r.status_code, r.json()["data"]["status"])
r = c.post(f"/mock-api/v1/after-sales/{asid2}/complete", headers=H2, json={})
print("AS2 complete:", r.status_code, r.json()["data"]["status"],
      "| refund:", r.json()["data"].get("refund", {}).get("status"))
rfid2 = r.json()["data"]["refund_id"]
r = c.post(f"/mock-api/v1/refunds/{rfid2}/success", headers=H2)
print("AS2 refund success:", r.status_code, r.json()["data"]["status"])
r = c.get(f"/mock-api/v1/orders/{o3['order_id']}", headers=H)
print("AS2 order:", r.json()["data"]["status"])
r = c.get("/mock-api/v1/inventory/SOCK-001", headers=H)
print("SOCK-001 stock after restock:", r.json()["data"]["stock"])

print("\n=== ALL SMOKE TESTS DONE ===")
