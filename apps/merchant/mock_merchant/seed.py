"""演示种子数据。

首次启动时自动写入 SQLite，覆盖演示所需的全部场景：
正常/待付款/待发货/已发货/已签收/已完成/已取消订单、可退货订单（7 天内）、
超出售后期订单、非本人订单（C002 名下）、库存不足商品、退款审核中、已退款、
高风险退款顾客（C002 退款率 0.75）、待处理与已关闭工单等。

日期全部相对「今天」动态生成，保证任何时候启动演示数据都在有效期内。
"""

import hashlib
from datetime import datetime, timedelta

from mock_merchant import db, rules

TODAY = datetime.now()


def _d(days_ago: int, hm: str = "10:00:00") -> str:
    """N 天前的日期字符串。"""
    return (TODAY - timedelta(days=days_ago)).strftime("%Y-%m-%d") + " " + hm


def _ymd(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).strftime("%Y%m%d")


def hash_password(password: str) -> str:
    return hashlib.sha256(("merchant_mock_salt::" + password).encode("utf-8")).hexdigest()


# ---------- 商品 ----------

def _seed_products() -> None:
    products = [
        # sku, 名称, 分类, 价格, 原价, 卖点, 描述, 尺码, 颜色, 场景, 库存, 安全库存, 销量, 状态
        ("RUN-001", "轻风慢跑鞋", "运动鞋", 299, 399, "一体织透气鞋面，单只仅重230g",
         "轻量缓震慢跑鞋，适合日常5-10公里慢跑，透气网面夏天不闷脚。",
         {"39": 8, "40": 5, "41": 12, "42": 9, "43": 10, "44": 4}, ["黑色", "白色"], ["运动", "日常"], 48, 10, 326, "在售"),
        ("RUN-002", "云弹缓震跑鞋", "运动鞋", 499, 599, "高弹缓震中底，久跑不累脚",
         "中底回弹率68%，长距离训练防护膝盖，适合进阶跑者。",
         {"39": 6, "40": 0, "41": 9, "42": 11, "43": 7, "44": 5}, ["黑色", "灰蓝"], ["运动"], 38, 10, 158, "在售"),
        ("RUN-003", "竞速碳板跑鞋", "运动鞋", 899, 1099, "全掌碳板推进，马拉松竞速之选",
         "全掌异构碳板+超临界发泡中底，马拉松PB神器。",
         {"40": 1, "41": 1, "42": 1}, ["荧光绿", "黑红"], ["运动"], 3, 10, 87, "在售"),
        ("RUN-004", "恋风女款跑鞋", "运动鞋", 379, 459, "粉色渐变配色，专为女生设计",
         "窄楦设计贴合女性脚型，轻量缓震兼顾颜值与性能。",
         {"36": 7, "37": 10, "38": 12, "39": 6}, ["樱花粉", "云白"], ["运动", "日常"], 35, 10, 210, "在售"),
        ("RUN-005", "经典复刻跑鞋", "运动鞋", 199, 259, "复古鞋型，日常百搭",
         "复刻80年代经典鞋型，牛筋大底耐磨，通勤穿搭皆宜。",
         {"39": 15, "40": 18, "41": 12, "42": 10, "43": 14, "44": 9}, ["米白", "藏青"], ["日常"], 78, 10, 445, "在售"),
        ("RUN-006", "限定联名跑鞋", "运动鞋", 699, 899, "限量发售，联名定制款",
         "艺术家联名限定配色，附赠定制鞋盒与徽章，限量 500 双。",
         {"40": 0, "41": 0, "42": 0, "43": 0}, ["联名橙"], ["运动", "收藏"], 0, 10, 60, "在售"),
        ("TSHIRT-001", "速干运动T恤", "运动上衣", 89, 129, "冰感速干，跑步不贴身",
         "聚酯纤维速干面料，透气网眼拼接，运动通勤两相宜。",
         {"S": 30, "M": 45, "L": 35, "XL": 10}, ["黑色", "白色", "藏青"], ["运动", "日常"], 120, 20, 680, "在售"),
        ("JACKET-001", "轻量皮肤风衣", "运动外套", 199, 269, "收纳仅手掌大小，防风防泼水",
         "20D尼龙面料，重量180g，自带收纳袋，徒步跑步皆宜。",
         {"S": 12, "M": 20, "L": 16, "XL": 8}, ["荧光黄", "黑色"], ["运动", "户外"], 56, 10, 233, "在售"),
        ("HOODIE-001", "加绒连帽卫衣", "运动外套", 239, 299, "内里加绒，保暖不臃肿",
         "抓绒内里搭配透气面料，秋冬训练保暖首选，情侣款。",
         {"S": 14, "M": 22, "L": 18, "XL": 10}, ["燕麦色", "黑色", "雾灰"], ["日常", "运动"], 64, 10, 178, "在售"),
        ("EAR-001", "无线主动降噪耳机", "数码配件", 599, 799, "42dB深度降噪，30小时续航",
         "混合主动降噪，LDAC高解析音质，IPX4防水，运动通勤两用。",
         {}, ["曜石黑", "月光银"], ["运动", "通勤", "数码"], 25, 10, 96, "在售"),
        ("WATCH-002", "智能运动手表", "数码配件", 1299, 1599, "双频GPS，两周超长续航",
         "血氧心率监测，100+运动模式，5ATM防水，蓝宝石镜面。",
         {}, ["耀岩黑"], ["运动", "数码"], 18, 5, 52, "在售"),
        ("SOCK-001", "专业运动袜", "运动配件", 29, 39, "毛巾底加厚，防磨防滑",
         "透气网眼+毛巾底设计，跑步防磨脚，3双装。",
         {"S": 100, "M": 120, "L": 80}, ["白色", "黑色"], ["运动", "日常"], 300, 50, 1200, "在售"),
        # 小额商品（单价 < 50 元）：用于演示小额免拦截自动退款流程
        ("CAP-001", "透气棒球帽", "运动配件", 39, 59, "透气网眼，遮阳防晒",
         "轻量网眼棒球帽，可调节帽围，跑步骑行遮阳防晒。",
         {"均码": 60}, ["黑色", "白色"], ["运动", "日常"], 200, 30, 310, "在售"),
        ("BOTTLE-001", "便携运动水壶", "运动配件", 35, 49, "一键弹盖，450ml轻量便携",
         "Tritan材质运动水壶，一键弹盖单手饮水，带提环易携带。",
         {"450ml": 150}, ["透明蓝", "活力橙"], ["运动", "户外"], 260, 40, 520, "在售"),
        ("TOWEL-001", "速干运动毛巾", "运动配件", 19, 29, "超细纤维，吸汗速干",
         "超细纤维毛巾，吸水速干，轻便易携，运动健身必备。",
         {"小号": 200, "中号": 150}, ["灰色", "蓝色"], ["运动", "日常"], 350, 50, 860, "在售"),
        ("ROPE-001", "健身跳绳", "运动配件", 29, 39, "轴承顺滑，可调节长度",
         "钢珠轴承跳绳，顺滑不绕绳，长度自由调节，燃脂神器。",
         {"2.8米": 180}, ["黑色", "红色"], ["运动", "健身"], 280, 40, 640, "在售"),
        ("BAND-001", "运动吸汗护腕", "运动配件", 25, 35, "加厚毛巾底，吸汗防滑",
         "棉质加厚护腕一对装，吸汗透气，羽毛球网球跑步通用。",
         {"均码": 240}, ["白色", "黑色"], ["运动"], 300, 40, 430, "在售"),
    ]
    now = rules.now_str()
    for sku, name, cat, price, orig, hl, desc, sizes, colors, scenes, stock, safety, sales, status in products:
        db.execute(
            """INSERT INTO products (sku, name, category, price, original_price, highlights,
               description, image_url, sizes, colors, scenes, link, stock, locked_stock,
               safety_stock, sales, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sku, name, cat, price, orig, hl, desc, "", db.dumps(sizes), db.dumps(colors),
             db.dumps(scenes), f"https://shop.demo.cn/goods/{sku}", stock, 0, safety,
             sales, status, _d(90), now),
        )
    # 几条历史库存调整记录
    for sku, qty, reason in [("RUN-001", 20, "补货入库"), ("RUN-006", -60, "限量款售罄"),
                             ("RUN-003", -45, "促销活动售出")]:
        row = db.query_one("SELECT stock FROM products WHERE sku = ?", (sku,))
        db.execute(
            """INSERT INTO inventory_logs (sku, action, change_qty, before_stock, after_stock,
               reason, operator, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (sku, "adjust", qty, (row["stock"] or 0) - qty, row["stock"], reason, "陈运营", _d(30)),
        )


# ---------- 账号 ----------

def _seed_accounts() -> None:
    now = rules.now_str()
    staffs = [
        ("E001", "merchant_admin", "123456", "王掌柜", "merchant_admin"),
        ("E002", "operator", "123456", "陈运营", "operator"),
        ("E003", "after_sales", "123456", "赵售后", "after_sales"),
    ]
    for staff_id, username, pwd, name, role in staffs:
        db.execute(
            """INSERT INTO staffs (staff_id, username, password_hash, name, role, merchant_id,
               status, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (staff_id, username, hash_password(pwd), name, role, "M001", "在职", now),
        )
    customers = [
        ("C001", "han", "123456", "韩梅梅", "13812345678",
         "北京市朝阳区建国路88号3单元502", "黄金会员", 2680),
        ("C002", "lei", "123456", "李雷", "13998765432",
         "上海市浦东新区张江路500号畅星大厦8层", "普通会员", 320),
    ]
    for cid, username, pwd, name, phone, addr, level, points in customers:
        db.execute(
            """INSERT INTO customers (customer_id, username, password_hash, name, phone,
               address, level, points, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (cid, username, hash_password(pwd), name, phone, addr, level, points, _d(120)),
        )


# ---------- 订单 ----------

def _seed_orders() -> None:
    # (订单号日期偏移, 序号, 顾客, 状态, prev_status, items, 收货人, 电话, 地址,
    #  created偏移, paid偏移, shipped偏移, signed偏移, completed偏移, cancel_reason)
    o = lambda days, seq: f"SO{_ymd(days)}{seq:03d}"
    orders = [
        # C001 韩梅梅
        dict(order_id=o(9, 1), customer_id="C001", status="已签收", prev_status="",
             items=[("RUN-001", "轻风慢跑鞋", 1, 299), ("TSHIRT-001", "速干运动T恤", 2, 89)],
             receiver="韩梅梅", phone="13812345678",
             address="北京市朝阳区建国路88号3单元502",
             created=9, paid=9, shipped=8, signed=7, completed=None, cancel="",
             note="可退货订单（签收 7 天内，无理由退货窗口内）"),
        dict(order_id=o(26, 2), customer_id="C001", status="已完成", prev_status="",
             items=[("JACKET-001", "轻量皮肤风衣", 1, 199)],
             receiver="韩梅梅", phone="13812345678",
             address="北京市朝阳区建国路88号3单元502",
             created=26, paid=26, shipped=25, signed=24, completed=20, cancel="",
             note="超出售后期订单（签收超 7 天）"),
        dict(order_id=o(6, 3), customer_id="C001", status="已发货", prev_status="",
             items=[("EAR-001", "无线主动降噪耳机", 1, 599)],
             receiver="韩梅梅", phone="13812345678",
             address="北京市朝阳区建国路88号3单元502",
             created=6, paid=6, shipped=5, signed=None, completed=None, cancel="",
             note="在途订单（物流运输中）"),
        dict(order_id=o(5, 4), customer_id="C001", status="待发货", prev_status="已付款",
             items=[("HOODIE-001", "加绒连帽卫衣", 2, 239)],
             receiver="韩梅梅", phone="13812345678",
             address="北京市朝阳区望京街道阜通东大街6号方恒时代中心",
             created=5, paid=5, shipped=None, signed=None, completed=None, cancel="",
             note="已付款待发货"),
        dict(order_id=o(1, 5), customer_id="C001", status="待付款", prev_status="",
             items=[("WATCH-002", "智能运动手表", 1, 1299)],
             receiver="韩梅梅", phone="13812345678",
             address="北京市朝阳区建国路88号3单元502",
             created=1, paid=None, shipped=None, signed=None, completed=None, cancel="",
             note="待付款订单"),
        dict(order_id=o(21, 6), customer_id="C001", status="已签收", prev_status="",
             items=[("RUN-005", "经典复刻跑鞋", 1, 199)],
             receiver="韩梅梅", phone="13812345678",
             address="北京市朝阳区建国路88号3单元502",
             created=21, paid=21, shipped=20, signed=19, completed=None, cancel="",
             note="超期订单（退款申请已被自动驳回 R4）"),
        dict(order_id=o(4, 7), customer_id="C001", status="已退款", prev_status="已签收",
             items=[("SOCK-001", "专业运动袜", 1, 29)],
             receiver="韩梅梅", phone="13812345678",
             address="北京市朝阳区建国路88号3单元502",
             created=4, paid=4, shipped=3, signed=2, completed=None, cancel="",
             note="小额自动退款已完成（R8 自动通过）"),
        dict(order_id=o(29, 8), customer_id="C001", status="已取消", prev_status="待付款",
             items=[("RUN-004", "恋风女款跑鞋", 1, 379)],
             receiver="韩梅梅", phone="13812345678",
             address="北京市朝阳区建国路88号3单元502",
             created=29, paid=None, shipped=None, signed=None, completed=None,
             cancel="顾客主动取消（未支付）", note="已取消订单"),
        # C002 李雷（高风险顾客：退款率 0.5+，风险分 80）
        dict(order_id=o(16, 9), customer_id="C002", status="已退款", prev_status="已签收",
             items=[("RUN-003", "竞速碳板跑鞋", 1, 899)],
             receiver="李雷", phone="13998765432",
             address="上海市浦东新区张江路500号畅星大厦8层",
             created=16, paid=16, shipped=15, signed=14, completed=None, cancel="",
             note="已退款订单（质量问题，人工审核通过）"),
        dict(order_id=o(11, 10), customer_id="C002", status="已退款", prev_status="已签收",
             items=[("TSHIRT-001", "速干运动T恤", 2, 89)],
             receiver="李雷", phone="13998765432",
             address="上海市浦东新区张江路500号畅星大厦8层",
             created=11, paid=11, shipped=10, signed=9, completed=None, cancel="",
             note="已退款订单（质量问题，人工审核通过）"),
        dict(order_id=o(9, 11), customer_id="C002", status="退款中", prev_status="已签收",
             items=[("JACKET-001", "轻量皮肤风衣", 1, 199)],
             receiver="李雷", phone="13998765432",
             address="上海市浦东新区张江路500号畅星大厦8层",
             created=9, paid=9, shipped=8, signed=7, completed=None, cancel="",
             note="退款审核中（高风险账号，转人工 R1）"),
        dict(order_id=o(6, 12), customer_id="C002", status="售后中", prev_status="已签收",
             items=[("RUN-002", "云弹缓震跑鞋", 1, 499)],
             receiver="李雷", phone="13998765432",
             address="上海市浦东新区张江路500号畅星大厦8层",
             created=6, paid=6, shipped=5, signed=4, completed=None, cancel="",
             note="换货售后进行中（待寄回）"),
    ]
    for od in orders:
        items = [
            {"sku": sku, "name": name, "qty": qty, "price": price,
             "amount": round(qty * price, 2)}
            for sku, name, qty, price in od["items"]
        ]
        goods_amount = round(sum(i["amount"] for i in items), 2)
        total = goods_amount  # 演示数据：无优惠无运费
        db.execute(
            """INSERT INTO orders (order_id, customer_id, status, prev_status, items,
               goods_amount, discount, freight, total, receiver_name, receiver_phone,
               address, created_at, paid_at, shipped_at, signed_at, completed_at,
               cancel_reason, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (od["order_id"], od["customer_id"], od["status"], od["prev_status"],
             db.dumps(items), goods_amount, 0, 0, total, od["receiver"], od["phone"],
             od["address"], _d(od["created"], "10:23:00"),
             _d(od["paid"], "10:24:00") if od["paid"] is not None else None,
             _d(od["shipped"], "09:00:00") if od["shipped"] is not None else None,
             _d(od["signed"], "14:10:00") if od["signed"] is not None else None,
             _d(od["completed"], "11:00:00") if od["completed"] is not None else None,
             od["cancel"], _d(od["created"], "10:23:00")),
        )
        # 订单操作日志（按实际发生的时间节点补齐）
        logs = [("创建订单", "", "待付款", od["created"], "顾客提交订单")]
        if od["paid"] is not None:
            logs.append(("支付订单", "待付款", "已付款", od["paid"], "支付成功"))
        if od["shipped"] is not None:
            logs.append(("商家发货", "已付款", "已发货", od["shipped"], "商家发货并创建物流单"))
        if od["signed"] is not None:
            logs.append(("确认收货", "已发货", "已签收", od["signed"], "顾客确认收货"))
        if od["completed"] is not None:
            logs.append(("交易完成", "已签收", "已完成", od["completed"], "订单交易完成"))
        if od["cancel"]:
            logs.append(("取消订单", "待付款", "已取消", od["created"], od["cancel"]))
        for action, frm, to, day, remark in logs:
            db.execute(
                """INSERT INTO order_logs (order_id, action, from_status, to_status,
                   operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
                (od["order_id"], action, frm, to, od["customer_id"], remark,
                 _d(day, "10:30:00")),
            )


# ---------- 物流 ----------

def _seed_logistics() -> None:
    plans = [
        # (订单序号, 快递公司, 最终节点, [(节点, 距今天数)...])；揽收时间与发货时间对齐
        (3, "顺丰", "运输中", [("已揽收", 5), ("到达分拨中心", 5), ("运输中", 4)]),
        (1, "中通", "已签收",
         [("已揽收", 8), ("到达分拨中心", 8), ("运输中", 8), ("派送中", 7), ("已签收", 7)]),
        (2, "圆通", "已签收",
         [("已揽收", 25), ("到达分拨中心", 25), ("运输中", 24), ("派送中", 24), ("已签收", 24)]),
        (9, "顺丰", "已签收",
         [("已揽收", 15), ("到达分拨中心", 15), ("运输中", 14), ("派送中", 14), ("已签收", 14)]),
        (10, "中通", "已签收",
         [("已揽收", 10), ("到达分拨中心", 10), ("运输中", 9), ("派送中", 9), ("已签收", 9)]),
        (11, "申通", "已签收",
         [("已揽收", 8), ("到达分拨中心", 8), ("运输中", 8), ("派送中", 7), ("已签收", 7)]),
        (12, "韵达", "已签收",
         [("已揽收", 5), ("到达分拨中心", 5), ("运输中", 4), ("派送中", 4), ("已签收", 4)]),
    ]
    loc_map = {
        "已揽收": "北京市朝阳区营业点",
        "到达分拨中心": "北京华北分拨中心",
        "运输中": "京沪线运输途中",
        "派送中": "上海市浦东新区网点",
        "已签收": "本人签收",
    }
    for seq, carrier, final, nodes in plans:
        order_row = db.query_one(
            "SELECT order_id FROM orders WHERE order_id LIKE ?", (f"SO%{seq:03d}",)
        )
        if not order_row:
            continue
        order_id = order_row["order_id"]
        traces = [
            {"status": "待发货", "location": "商家仓库", "time": _d(nodes[0][1], "09:00:00")}
        ]
        for status, day in nodes:
            traces.append(
                {"status": status, "location": loc_map[status], "time": _d(day, "12:00:00")}
            )
        db.execute(
            """INSERT INTO logistics (order_id, carrier, tracking_no, status,
               current_location, traces, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (order_id, carrier, rules.new_tracking_no(carrier), final,
             loc_map[final], db.dumps(traces), traces[1]["time"], traces[-1]["time"]),
        )


# ---------- 售后 / 退款 ----------

def _seed_after_sales_and_refunds() -> None:
    # 售后单：AS+日期+序号
    def as_id(days: int, seq: int) -> str:
        return f"AS{_ymd(days)}{seq:03d}"

    def rf_id(days: int, seq: int) -> str:
        return f"RF{_ymd(days)}{seq:03d}"

    def order_by_seq(seq: int) -> str:
        row = db.query_one("SELECT order_id FROM orders WHERE order_id LIKE ?", (f"SO%{seq:03d}",))
        return row["order_id"] if row else ""

    now = rules.now_str()
    # (售后ID, 订单seq, 顾客, 类型, 状态, 原因, 金额, 关联退款ID, 创建偏移)
    after_sales = [
        (as_id(0, 1), 11, "C002", "仅退款", "退款中", "尺码不合适，想退款", 199, rf_id(0, 1), 0),
        (as_id(9, 2), 10, "C002", "退货退款", "已完成", "洗后严重掉色，质量问题", 178, rf_id(9, 2), 9),
        (as_id(14, 3), 9, "C002", "仅退款", "已完成", "鞋底开胶，质量问题", 899, rf_id(14, 3), 14),
        (as_id(18, 4), 2, "C001", "退货退款", "审核驳回", "穿过一次想退", 199, "", 18),
        (as_id(1, 5), 3, "C001", "投诉", "待审核", "物流太慢，投诉快递服务", 0, "", 1),
        (as_id(4, 6), 12, "C002", "换货", "待寄回", "尺码偏大，想换小一码", 0, "", 4),
    ]
    for asid, seq, cid, typ, status, reason, amount, refund_id, day in after_sales:
        db.execute(
            """INSERT INTO after_sales (after_sale_id, order_id, customer_id, type, status,
               reason, description, amount, refund_id, admin_remark, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (asid, order_by_seq(seq), cid, typ, status, reason, reason, amount,
             refund_id, "演示种子数据", _d(day), _d(day)),
        )
        db.execute(
            """INSERT INTO after_sale_logs (after_sale_id, action, from_status, to_status,
               operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
            (asid, "创建售后单", "", "待审核", cid, reason, _d(day)),
        )
        if status == "退款中":
            db.execute(
                """INSERT INTO after_sale_logs (after_sale_id, action, from_status,
                   to_status, operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
                (asid, "审核通过", "待审核", "退款中", "赵售后", "同意退款，等待打款", _d(day, "09:00:00")),
            )
        elif status == "已完成":
            db.execute(
                """INSERT INTO after_sale_logs (after_sale_id, action, from_status,
                   to_status, operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
                (asid, "审核通过", "待审核", "待寄回", "赵售后", "同意退货退款", _d(day, "09:00:00")),
            )
            db.execute(
                """INSERT INTO after_sale_logs (after_sale_id, action, from_status,
                   to_status, operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
                (asid, "完成售后", "商家收货", "已完成", "赵售后", "退货已收货，退款已打款", _d(day, "09:30:00")),
            )
        elif status == "审核驳回":
            db.execute(
                """INSERT INTO after_sale_logs (after_sale_id, action, from_status,
                   to_status, operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
                (asid, "审核驳回", "待审核", "审核驳回", "赵售后", "已超过7天无理由退货期", _d(day, "10:00:00")),
            )
        elif status == "待寄回":
            db.execute(
                """INSERT INTO after_sale_logs (after_sale_id, action, from_status,
                   to_status, operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
                (asid, "审核通过", "待审核", "待寄回", "赵售后", "同意换货，请寄回商品", _d(day, "09:00:00")),
            )

    # 退款单：RF+日期+序号
    # (退款ID, 订单seq, 顾客, 金额, 原因, 类型, 结果, 风控, 规则, 状态, 创建偏移, 退款完成偏移, 售后ID)
    refunds = [
        (rf_id(0, 1), 11, "C002", 199, "尺码不合适，想退款", "manual", "转人工审核",
         "高风险账号（历史退款率 0.5）", "R1", "待审核", 0, None, as_id(0, 1)),
        (rf_id(9, 2), 10, "C002", 178, "洗后严重掉色，质量问题", "manual", "人工审核通过",
         "质量问题转人工", "R3", "已退款", 9, 8, as_id(9, 2)),
        (rf_id(14, 3), 9, "C002", 899, "鞋底开胶，质量问题", "manual", "人工审核通过",
         "质量问题转人工", "R3", "已退款", 14, 13, as_id(14, 3)),
        (rf_id(18, 4), 6, "C001", 199, "不想要了", "auto", "自动拒绝",
         "超过无理由退货期", "R4", "已驳回", 18, None, ""),
        (rf_id(1, 5), 7, "C001", 29, "尺码不合适", "auto", "自动通过",
         "小额+无理由+账号正常", "R8", "已退款", 1, 1, ""),
    ]
    for rid, seq, cid, amount, reason, rtype, result, risk, rule, status, day, done_day, asid in refunds:
        db.execute(
            """INSERT INTO refunds (refund_id, order_id, customer_id, after_sale_id, amount,
               reason, review_type, review_result, risk_result, rule_hit, status,
               refunded_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, order_by_seq(seq), cid, asid, amount, reason, rtype, result, risk,
             rule, status, _d(done_day, "15:00:00") if done_day is not None else None,
             _d(day), _d(done_day) if done_day is not None else _d(day)),
        )
        db.execute(
            """INSERT INTO refund_logs (refund_id, action, from_status, to_status, operator,
               remark, created_at) VALUES (?,?,?,?,?,?,?)""",
            (rid, "创建退款单", "", "待审核", cid, reason, _d(day)),
        )
        if status == "已退款":
            db.execute(
                """INSERT INTO refund_logs (refund_id, action, from_status, to_status,
                   operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
                (rid, "审核通过", "待审核", "审核通过", "赵售后", result, _d(day, "09:10:00")),
            )
            db.execute(
                """INSERT INTO refund_logs (refund_id, action, from_status, to_status,
                   operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
                (rid, "退款成功", "审核通过", "已退款", "system", "退款已原路退回",
                 _d(done_day, "15:00:00")),
            )
        elif status == "已驳回":
            db.execute(
                """INSERT INTO refund_logs (refund_id, action, from_status, to_status,
                   operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
                (rid, "自动驳回", "待审核", "已驳回", "auto", risk, _d(day, "09:05:00")),
            )


# ---------- 工单 ----------

def _seed_tickets() -> None:
    def tk_id(days: int, seq: int) -> str:
        return f"TK{_ymd(days)}{seq:03d}"

    def order_by_seq(seq: int) -> str:
        row = db.query_one("SELECT order_id FROM orders WHERE order_id LIKE ?", (f"SO%{seq:03d}",))
        return row["order_id"] if row else ""

    tickets = [
        (tk_id(1, 1), "C001", 3, "咨询", "待处理", 2, "普通", "物流进度咨询",
         "耳机订单显示已发货，大概什么时候能到？", ""),
        (tk_id(0, 2), "C002", 11, "投诉", "处理中", 3, "加急", "退款进度投诉",
         "退款申请提交 3 天了还没审核通过，要求尽快处理！", "赵售后"),
        (tk_id(0, 3), "C001", 1, "售后", "待处理", 2, "普通", "跑鞋退货咨询",
         "跑鞋想申请退货，还在7天内，怎么操作？", ""),
        (tk_id(15, 4), "C002", 9, "投诉", "已关闭", 3, "加急", "商品质量投诉",
         "碳板跑鞋穿了两次就开胶，强烈要求退款！", "赵售后"),
        (tk_id(10, 5), "C001", None, "建议", "已解决", 1, "低", "APP 体验建议",
         "希望 APP 支持商品收藏夹功能，方便回购。", "陈运营"),
    ]
    for tid, cid, seq, typ, status, pri, plabel, summary, desc, assigned in tickets:
        records = [{"time": _d(1), "note": "工单已创建，等待受理"}]
        if assigned:
            records.append({"time": _d(1, "09:30:00"), "note": f"已分配给 {assigned}"})
        if status == "已关闭":
            records.append({"time": _d(14), "note": "问题已解决，工单关闭"})
        if status == "已解决":
            records.append({"time": _d(9), "note": "建议已记录并转产品团队，工单解决"})
        db.execute(
            """INSERT INTO tickets (ticket_id, customer_id, order_id, type, status, priority,
               priority_label, summary, description, assigned_to, records, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, cid, order_by_seq(seq) if seq else "", typ, status, pri, plabel,
             summary, desc, assigned, db.dumps(records), _d(1), _d(1, "09:30:00")),
        )


# ---------- 操作日志 / 故障配置 ----------

def _seed_op_logs() -> None:
    logs = [
        ("merchant_admin", "merchant_admin", "登录", "auth", "商家管理员登录商家后台", 1),
        ("陈运营", "operator", "库存调整", "inventory:RUN-001", "补货入库 +20", 30),
        ("陈运营", "operator", "商品上架", "products:RUN-006", "限定联名跑鞋上架", 45),
        ("赵售后", "after_sales", "售后审核", "after_sales", "退货退款人工审核通过", 9),
        ("赵售后", "after_sales", "退款打款", "refunds", "质量问题退款已原路退回", 8),
        ("赵售后", "after_sales", "工单分配", "tickets", "加急投诉工单分配处理", 0),
    ]
    for actor, role, action, resource, detail, day in logs:
        db.execute(
            """INSERT INTO op_logs (actor, role, action, resource, detail, request_id,
               created_at) VALUES (?,?,?,?,?,?,?)""",
            (actor, role, action, resource, detail, "req_seed_" + str(day), _d(day, "09:20:00")),
        )


def _seed_faults() -> None:
    import config as cfg

    defaults = {
        "enabled": "true" if cfg.MOCK_MERCHANT_ENABLE_FAILURE_SIMULATION else "false",
        "latency_ms": str(cfg.MOCK_MERCHANT_LATENCY_MS),
        "error_rate": str(cfg.MOCK_MERCHANT_ERROR_RATE),
        "force_status": "",
        "force_error_code": "",
        "force_next": "-1",
        "service_unavailable": "false",
        "inventory_shortage": "false",
        "duplicate_request": "false",
        "refund_risk_block": "false",
        "logistics_query_fail": "false",
    }
    for key, value in defaults.items():
        db.execute(
            "INSERT OR REPLACE INTO fault_config (key, value) VALUES (?,?)", (key, value)
        )


def run() -> None:
    """写入全部种子数据（仅在空库时被调用）。"""
    _seed_accounts()
    _seed_products()
    _seed_orders()
    _seed_logistics()
    _seed_after_sales_and_refunds()
    _seed_tickets()
    _seed_op_logs()
    _seed_faults()
