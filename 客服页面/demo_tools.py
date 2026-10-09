"""
demo_tools.py — 电商业务工具直调演示脚本

不经 LLM，直接通过 ToolRegistry 依次调用全部业务工具，一键演示：
- 公共能力（商品搜索/详情，游客可用）
- 游客态拦截（未登录查订单/档案被拦）
- 登录态业务（订单/物流/退货/工单/改地址）
- 越权拦截（han 查 li 的订单）
- 边界行为（查询不存在的订单）
- 账号记忆读写（remember_preference / get_my_profile）
- 兜底转人工（escalate_to_human）

用法：
    python demo_tools.py
"""

import asyncio

from agent.tools.registry import ToolRegistry
from agent.tools.ecom.search_goods import SearchGoodsTool
from agent.tools.ecom.get_goods_detail import GetGoodsDetailTool
from agent.tools.ecom.get_my_profile import GetMyProfileTool
from agent.tools.ecom.remember_preference import RememberPreferenceTool
from agent.tools.ecom.query_order import QueryOrderTool
from agent.tools.ecom.update_address import UpdateAddressTool
from agent.tools.ecom.query_logistics import QueryLogisticsTool
from agent.tools.ecom.check_return_eligibility import CheckReturnEligibilityTool
from agent.tools.ecom.create_ticket import CreateTicketTool
from agent.tools.ecom.query_ticket_progress import QueryTicketProgressTool
from agent.tools.ecom.escalate_to_human import EscalateToHumanTool


def build_registry(actor: str | None, logged_in: bool) -> ToolRegistry:
    """按登录身份构建工具注册表（与 main.py build_agent 中一致）。"""
    tools = ToolRegistry()
    tools.register(SearchGoodsTool())
    tools.register(GetGoodsDetailTool())
    tools.register(GetMyProfileTool(actor=actor, logged_in=logged_in))
    tools.register(RememberPreferenceTool(actor=actor, logged_in=logged_in))
    tools.register(QueryOrderTool(actor=actor, logged_in=logged_in))
    tools.register(UpdateAddressTool(actor=actor, logged_in=logged_in))
    tools.register(QueryLogisticsTool(actor=actor, logged_in=logged_in))
    tools.register(CheckReturnEligibilityTool(actor=actor, logged_in=logged_in))
    tools.register(CreateTicketTool(actor=actor, logged_in=logged_in))
    tools.register(QueryTicketProgressTool(actor=actor, logged_in=logged_in))
    tools.register(EscalateToHumanTool(actor=actor, logged_in=logged_in))
    return tools


async def call(tools: ToolRegistry, title: str, name: str, args: dict) -> None:
    """调用单个工具并打印结果。"""
    print()
    print("=" * 64)
    print(f"▶ {title}")
    print(f"  调用: {name}({args})")
    print("-" * 64)
    result = await tools.execute(name, args)
    print(result)


async def main() -> None:
    print("=" * 64)
    print("NanoClaw 电商业务工具直调演示（不经 LLM）")
    print("=" * 64)

    # 游客身份（未登录）与登录身份 han
    guest = build_registry(None, logged_in=False)
    han = build_registry("han", logged_in=True)

    # ---------- 1. 公共能力（游客可用） ----------
    await call(
        guest,
        "场景1 · 游客搜索商品（公共能力免登录）",
        "search_goods",
        {"keyword": "跑鞋", "max_price": 300},
    )
    await call(
        guest,
        "场景1b · 游客查商品详情",
        "get_goods_detail",
        {"sku": "RUN-001"},
    )

    # ---------- 2. 游客态拦截 ----------
    await call(
        guest,
        "场景2 · 游客查订单被拦（登录提示）",
        "query_order",
        {"order_id": "SO20260901001"},
    )
    await call(
        guest,
        "场景2b · 游客查档案被拦（登录提示）",
        "get_my_profile",
        {},
    )

    # ---------- 3. 登录态业务（han） ----------
    await call(
        han,
        "场景3 · 登录后查订单详情（收件信息脱敏）",
        "query_order",
        {"order_id": "SO20260901001"},
    )
    await call(
        han,
        "场景3b · 登录后查订单列表",
        "query_order",
        {},
    )
    await call(
        han,
        "场景4 · 物流跟踪（含派送异常节点）",
        "query_logistics",
        {"order_id": "SO20260902003"},
    )
    await call(
        han,
        "场景5 · 退货资格判定（已签收 7 天内）",
        "check_return_eligibility",
        {"order_id": "SO20260901001", "reason": "尺码不合适"},
    )

    # ---------- 4. 越权拦截与边界 ----------
    await call(
        han,
        "场景6 · 越权拦截（han 查 li 的订单）",
        "query_order",
        {"order_id": "SO20260901007"},
    )
    await call(
        han,
        "场景7 · 边界行为（查询不存在的订单）",
        "query_order",
        {"order_id": "SO20990101999"},
    )

    # ---------- 5. 账号记忆读写 ----------
    await call(
        han,
        "场景8 · 偏好写入（remember_preference 写入 han 的账号记忆）",
        "remember_preference",
        {"content": "喜欢清晨跑步，偏好透气网面鞋款"},
    )
    await call(
        han,
        "场景9 · 偏好读取（get_my_profile 读账号档案与记忆）",
        "get_my_profile",
        {},
    )

    # ---------- 6. 工单与售后 ----------
    await call(
        han,
        "场景10 · 工单进度查询（按工单号）",
        "query_ticket_progress",
        {"ticket_id": "TK20260901001"},
    )
    await call(
        han,
        "场景11 · 创建售后工单（写操作，演示级直调）",
        "create_ticket",
        {
            "type": "售后",
            "order_id": "SO20260902003",
            "description": "耳机左耳出现杂音，申请检测换货",
        },
    )
    await call(
        han,
        "场景12 · 修改收货地址（未签收订单，写操作演示）",
        "update_address",
        {
            "order_id": "SO20260903004",
            "new_address": "北京市朝阳区望京街道阜通东大街6号方恒时代中心",
        },
    )

    # ---------- 7. 兜底转人工 ----------
    await call(
        han,
        "场景13 · 兜底转人工（escalate_to_human 创建人工工单）",
        "escalate_to_human",
        {
            "summary": "顾客反馈物流派送异常（门牌号不完整）已多次沟通未解决，要求人工跟进",
            "reason": "连续未解决",
        },
    )

    print()
    print("=" * 64)
    print("演示完成：13 个场景覆盖全部 11 个业务工具与权限拦截。")
    print("说明：场景 8/11/12/13 为写操作，运行后数据已更新（记忆/工单/地址），")
    print("     重复运行会追加新记录，属预期行为。")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
