"""向上架中的 SJ 服务追加 5 个低价（<50 元）演示商品。

用法（SJ 服务运行中执行，幂等可重复）：
    python add_low_price_goods.py

说明：
- 通过商家后台 HTTP API 创建（merchant_admin 登录），不重启服务、
  不清空数据库，不影响库中已有商品（含运营手工添加的数据）；
- 商品数据与 mock_merchant/seed.py 中的定义完全一致，保证
  未来 --reseed 重建后仍能通过本脚本补齐。
"""

import sys

import requests

BASE = "http://127.0.0.1:8090/mock-api/v1"

# 与 seed.py 新增低价商品行保持一致
# (sku, name, category, price, original_price, highlights, description,
#  sizes, colors, scenes, stock, safety_stock)
LOW_PRICE_GOODS = [
    ("CAP-001", "透气棒球帽", "运动配件", 39, 59, "透气网眼，遮阳防晒",
     "轻量网眼棒球帽，可调节帽围，跑步骑行遮阳防晒。",
     {"均码": 60}, ["黑色", "白色"], ["运动", "日常"], 200, 30),
    ("BOTTLE-001", "便携运动水壶", "运动配件", 35, 49, "一键弹盖，450ml轻量便携",
     "Tritan材质运动水壶，一键弹盖单手饮水，带提环易携带。",
     {"450ml": 150}, ["透明蓝", "活力橙"], ["运动", "户外"], 260, 40),
    ("TOWEL-001", "速干运动毛巾", "运动配件", 19, 29, "超细纤维，吸汗速干",
     "超细纤维毛巾，吸水速干，轻便易携，运动健身必备。",
     {"小号": 200, "中号": 150}, ["灰色", "蓝色"], ["运动", "日常"], 350, 50),
    ("ROPE-001", "健身跳绳", "运动配件", 29, 39, "轴承顺滑，可调节长度",
     "钢珠轴承跳绳，顺滑不绕绳，长度自由调节，燃脂神器。",
     {"2.8米": 180}, ["黑色", "红色"], ["运动", "健身"], 280, 40),
    ("BAND-001", "运动吸汗护腕", "运动配件", 25, 35, "加厚毛巾底，吸汗防滑",
     "棉质加厚护腕一对装，吸汗透气，羽毛球网球跑步通用。",
     {"均码": 240}, ["白色", "黑色"], ["运动"], 300, 40),
]


def login() -> str:
    resp = requests.post(f"{BASE}/auth/token",
                         json={"username": "merchant_admin", "password": "123456"},
                         timeout=10)
    resp.raise_for_status()
    return resp.json()["data"]["access_token"]


def main() -> int:
    token = login()
    headers = {"Authorization": f"Bearer {token}"}
    created, skipped = [], []
    for (sku, name, category, price, original_price, highlights, description,
         sizes, colors, scenes, stock, safety_stock) in LOW_PRICE_GOODS:
        # 幂等：已存在则跳过
        exist = requests.get(f"{BASE}/products/{sku}", headers=headers, timeout=10)
        if exist.status_code == 200:
            skipped.append(sku)
            continue
        resp = requests.post(f"{BASE}/products", headers=headers, json={
            "sku": sku, "name": name, "category": category, "price": price,
            "original_price": original_price, "highlights": highlights,
            "description": description, "sizes": sizes, "colors": colors,
            "scenes": scenes, "stock": stock, "safety_stock": safety_stock,
        }, timeout=10)
        if resp.status_code == 201:
            created.append(f"{sku}({name} ¥{price})")
        else:
            print(f"[FAIL] {sku}: HTTP {resp.status_code} {resp.text}")
            return 1
    print(f"新增 {len(created)} 个低价商品：{', '.join(created) or '无'}")
    if skipped:
        print(f"已存在跳过：{', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
