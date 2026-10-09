"""订单共享服务：状态流转日志、退款/售后联动、订单查询。

被 orders / logistics / after_sales / refunds 路由共同复用，
保证订单状态机在全模块中一致，不会出现互相冲突的状态名称。
"""

from mock_merchant import db, rules
from mock_merchant.response import AppError


def get_order(order_id: str) -> dict:
    row = db.query_one("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    if not row:
        raise AppError(f"订单 {order_id} 不存在", "ORDER_NOT_FOUND", 404)
    return row


def log_order(order_id: str, action: str, from_status: str, to_status: str,
              operator: str, remark: str = "") -> None:
    db.execute(
        """INSERT INTO order_logs (order_id, action, from_status, to_status, operator,
           remark, created_at) VALUES (?,?,?,?,?,?,?)""",
        (order_id, action, from_status, to_status, operator, remark, rules.now_str()),
    )


def change_status(order_id: str, to_status: str, action: str, operator: str,
                  remark: str = "", extra_set: str = "", extra_params: tuple = ()) -> dict:
    """更新订单状态并写操作日志（extra_set 可附加字段，如 paid_at = ?）。"""
    row = get_order(order_id)
    sets = ["status = ?", "updated_at = ?"]
    params = [to_status, rules.now_str()]
    if extra_set:
        sets.append(extra_set)
        params.extend(extra_params)
    params.append(order_id)
    db.execute(f"UPDATE orders SET {', '.join(sets)} WHERE order_id = ?", params)
    log_order(order_id, action, row["status"], to_status, operator, remark)
    return get_order(order_id)


def start_refund(order_id: str, operator: str, remark: str = "发起退款") -> dict:
    """订单进入「退款中」（保存原状态，便于驳回后回滚）。"""
    row = get_order(order_id)
    if row["status"] in ("已退款",):
        raise AppError("订单已退款，不能重复发起", "INVALID_STATUS_TRANSITION", 409)
    if row["status"] == "退款中":
        return row
    db.execute(
        "UPDATE orders SET prev_status = ?, status = '退款中', updated_at = ? WHERE order_id = ?",
        (row["status"], rules.now_str(), order_id),
    )
    log_order(order_id, "发起退款", row["status"], "退款中", operator, remark)
    return get_order(order_id)


def start_after_sale(order_id: str, operator: str, remark: str = "发起售后") -> dict:
    """订单进入「售后中」（保存原状态，便于关闭后回滚）。"""
    row = get_order(order_id)
    if row["status"] == "售后中":
        return row
    db.execute(
        "UPDATE orders SET prev_status = ?, status = '售后中', updated_at = ? WHERE order_id = ?",
        (row["status"], rules.now_str(), order_id),
    )
    log_order(order_id, "发起售后", row["status"], "售后中", operator, remark)
    return get_order(order_id)


def restore_order(order_id: str, operator: str, action: str, remark: str = "") -> dict:
    """回滚到进入退款/售后前的状态（退款被驳回 / 售后关闭时）。"""
    row = get_order(order_id)
    if row["status"] not in ("退款中", "售后中") or not row["prev_status"]:
        return row
    prev = row["prev_status"]
    db.execute(
        "UPDATE orders SET prev_status = '', status = ?, updated_at = ? WHERE order_id = ?",
        (prev, rules.now_str(), order_id),
    )
    log_order(order_id, action, row["status"], prev, operator, remark)
    return get_order(order_id)


def order_logs(order_id: str) -> list[dict]:
    rows = db.query(
        "SELECT * FROM order_logs WHERE order_id = ? ORDER BY id ASC", (order_id,)
    )
    return [
        {"action": r["action"], "from_status": r["from_status"],
         "to_status": r["to_status"], "operator": r["operator"],
         "remark": r["remark"], "created_at": r["created_at"]}
        for r in rows
    ]
