"""商家后台页面路由：/merchant 入口（单页应用，纯本地资源，无需网络）。"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

import config

router = APIRouter(tags=["商家后台"])

_PAGE_FILE = Path(__file__).resolve().parent / "templates" / "merchant.html"


@router.get(config.MERCHANT_PAGE_PATH, response_class=HTMLResponse)
def merchant_page():
    """模拟商家后台（登录 / 看板 / 商品 / 库存 / 订单 / 物流 / 售后 / 退款 /
    工单 / 顾客 / 操作日志 / API 调试控制台）。"""
    return HTMLResponse(_PAGE_FILE.read_text(encoding="utf-8"))
