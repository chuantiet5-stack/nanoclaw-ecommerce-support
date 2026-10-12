"""商品详情工具：按 SKU 查询规格、库存、链接（公共能力，免登录）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import find_good


class GetGoodsDetailTool(Tool):
    """按 SKU 查询商品详情，含尺码库存、颜色、购买链接。"""

    @property
    def name(self) -> str:
        return "get_goods_detail"

    @property
    def description(self) -> str:
        return (
            "按 SKU 查询单个商品的实时详细信息（公共能力，顾客无需登录即可使用）。"
            "适用场景：顾客询问某件商品有无某尺码/颜色、库存、价格、购买链接，"
            "或之前看过的商品现在还有没有货。"
            "注意：商品状态和库存随时变化，每次询问都必须调用本工具实时查询，"
            "不可凭对话历史中的旧信息回答。"
            "输入：sku（商品唯一编号，如 RUN-001）。"
            "输出：实时价格、规格（尺码及各尺码库存 / 颜色）、库存、简介、购买链接；"
            "商品已下架时会明确告知。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sku": {
                    "type": "string",
                    "description": "商品SKU编号，如 RUN-001",
                },
            },
            "required": ["sku"],
        }

    async def execute(self, **kwargs) -> str:
        sku = str(kwargs.get("sku") or "").strip().upper()
        if not sku:
            return "请提供商品 SKU（可在商品列表中查看），例如：RUN-001。"

        goods = find_good(sku)
        if goods is None:
            return f"未找到 SKU 为 {sku} 的商品，请核对后重试。"

        # 已下架商品：明确告知，不展示库存详情误导顾客
        if goods.get("status") and goods["status"] != "在售":
            return (
                f"{goods['name']}（SKU: {sku}）目前已下架，暂不可购买。"
                "可以看看同类在售商品，告诉我您的需求，我来帮您推荐。"
            )

        lines = [
            f"**{goods['name']}**（SKU: {goods['sku']}）",
            f"- 价格：¥{goods['price']}",
        ]
        if goods.get("highlights"):
            lines.append(f"- 卖点：{goods['highlights']}")
        if goods.get("description"):
            lines.append(f"- 简介：{goods['description']}")

        sizes = goods.get("sizes") or {}
        if sizes:
            in_stock = [f"{s}码(有货×{n})" for s, n in sizes.items() if n > 0]
            out_stock = [f"{s}码(无货)" for s, n in sizes.items() if n == 0]
            if in_stock:
                lines.append(f"- 可选尺码：{'、'.join(in_stock)}")
                if out_stock:
                    lines.append(f"- 缺码尺码：{'、'.join(out_stock)}")
            else:
                lines.append("- 可选尺码：全部尺码暂时无货")
        else:
            lines.append("- 规格：该商品无尺码规格")

        colors = goods.get("colors") or []
        if colors:
            lines.append(f"- 可选颜色：{'、'.join(colors)}")

        lines.append(f"- 总库存：{goods['stock']} 件")
        if goods.get("available_stock") is not None and goods["available_stock"] != goods["stock"]:
            lines.append(f"- 可售库存：{goods['available_stock']} 件（其余已被订单锁定）")
        lines.append(f"- 购买链接：{goods.get('link', '')}")
        return "\n".join(lines)
