"""商品搜索工具：按关键词 / 品类 / 预算 / 用途搜索商品（公共能力，免登录）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import DATA_BACKEND, load_goods


class SearchGoodsTool(Tool):
    """搜索在售商品，返回 SKU、名称、价格、卖点、库存。"""

    def __init__(self) -> None:
        self._goods = None  # 惰性加载，避免构造时读盘

    def _all_goods(self):
        # merchant_api 为实时数据源：每次搜索都重新拉取，
        # 保证 SJ 商家后台刚上架的新商品能立即被搜到并推荐给顾客
        if DATA_BACKEND == "merchant_api":
            return load_goods()
        if self._goods is None:
            self._goods = load_goods()
        return self._goods

    @property
    def name(self) -> str:
        return "search_goods"

    @property
    def description(self) -> str:
        return (
            "搜索店铺在售商品（公共能力，顾客无需登录即可使用）。"
            "适用场景：顾客想买/被推荐商品、询问某类商品、按预算选品、为特定用途或送礼选品、"
            "询问店里有什么新品、或询问之前看过的商品还有没有。"
            "注意：商品上架/下架/库存随时变化，每次顾客询问商品相关问题时都必须"
            "调用本工具实时查询，不可凭对话历史中的旧信息回答。"
            "输入：keyword（核心商品词，如'跑鞋'、'电子表'，去掉'新品'等修饰词）、"
            "category（品类，可选）、max_price（预算上限，可选）、purpose（用途描述，可选）。"
            "输出：实时商品列表（SKU、名称、价格、卖点、库存、购买链接）；传'新品'可看最近上架商品。"
            "推荐时应结合顾客预算与偏好，给出对比理由而非单纯罗列；"
            "向顾客推荐商品时，回复中应附上商品的购买链接（markdown 格式），方便顾客直接点击查看。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "商品关键词，如'跑鞋'、'耳机'、'送礼'",
                },
                "category": {
                    "type": "string",
                    "description": "品类过滤，可选，如'运动鞋'、'数码配件'",
                },
                "max_price": {
                    "type": "number",
                    "description": "预算上限（元），可选",
                },
                "purpose": {
                    "type": "string",
                    "description": "用途描述，可选，如'送男朋友'、'夜跑'",
                },
            },
            "required": ["keyword"],
        }

    async def execute(self, **kwargs) -> str:
        keyword = str(kwargs.get("keyword") or "").strip().lower()
        category = str(kwargs.get("category") or "").strip().lower()
        purpose = str(kwargs.get("purpose") or "").strip().lower()
        max_price = kwargs.get("max_price")

        if not keyword and not category and not purpose:
            return "请告诉我您想找什么类型的商品，例如：跑鞋、耳机、双肩包。"

        # 关键词/用途共同参与匹配；复合词（如"电子表 新品"）拆成子词，
        # 任一子词命中即可，避免整串匹配导致搜不到
        terms = []
        for t in (keyword, purpose):
            if t:
                terms.extend(p for p in t.split() if p)

        # "新品/新款/上新"类泛词：直接返回最近上架的商品
        NEW_WORDS = ("新品", "新款", "上新", "新上架", "最近上")
        if any(any(w in t for w in NEW_WORDS) for t in terms):
            recent = sorted(
                self._all_goods(),
                key=lambda g: str(g.get("created_at", "")),
                reverse=True,
            )
            recent = recent[:5]
            if recent and not [t for t in terms if not any(w in t for w in NEW_WORDS)]:
                lines = ["为您找到最近的上新商品（按上架时间倒序，展示前 5 件）："]
                for i, g in enumerate(recent, 1):
                    hl = f" — {g['highlights']}" if g.get("highlights") else ""
                    link = f" | [查看商品]({g['link']})" if g.get("link") else ""
                    lines.append(
                        f"{i}. {g['name']}（SKU: {g['sku']}）¥{g['price']}"
                        f"{hl} | 库存{g['stock']}"
                        f" | 上架于 {g.get('created_at', '未知')}{link}"
                    )
                lines.append("如需了解某件商品的详情，请提供 SKU 或名称。")
                return "\n".join(lines)

        matched = []
        fallback_matched = False  # 是否经中文子词兜底匹配（决定排序方式）
        for g in self._all_goods():
            if category and category not in g.get("category", "").lower():
                continue
            if max_price is not None:
                try:
                    if g["price"] > float(max_price):
                        continue
                except (TypeError, ValueError):
                    pass
            searchable = " ".join(
                [g.get("name", ""), g.get("category", ""),
                 g.get("highlights", ""), g.get("description", ""),
                 " ".join(g.get("scenes", []))]
            ).lower()
            if terms and not any(t in searchable for t in terms):
                continue
            matched.append(g)

        # 中文长词兜底：整词无结果时，把无空格的长关键词（如图片识别出的
        # "灰色网面透气跑鞋，适合日常运动"）按标点拆短语、再按 2 字滑窗拆子词
        # （透气/跑鞋/网面…）重试；按命中子词数评分排序（命中越多越相关），
        # 避免"运动"等泛词把无关商品顶到前面
        if not matched and terms:
            import re as _re

            fallback_matched = True
            sub_terms = set()
            for t in terms:
                # 先按标点/空白拆成短语，避免跨标点生成噪音子词（如"鞋，搭"）
                for phrase in _re.split(r"[，。！？；、：,.!?:;\s]+", t):
                    if len(phrase) >= 3:
                        sub_terms.update(
                            phrase[i:i + 2] for i in range(len(phrase) - 1)
                        )
            if sub_terms:
                scored = []
                for g in self._all_goods():
                    if category and category not in g.get("category", "").lower():
                        continue
                    if max_price is not None:
                        try:
                            if g["price"] > float(max_price):
                                continue
                        except (TypeError, ValueError):
                            pass
                    searchable = " ".join(
                        [g.get("name", ""), g.get("category", ""),
                         g.get("highlights", ""), g.get("description", ""),
                         " ".join(g.get("scenes", []))]
                    ).lower()
                    hits = sum(1 for s in sub_terms if s in searchable)
                    if hits:
                        scored.append((hits, g))
                # 命中子词数降序（相关性优先），同分按价格升序
                scored.sort(key=lambda x: (-x[0], x[1]["price"]))
                matched = [g for _, g in scored]

        if not matched:
            return (
                "抱歉，没有找到符合条件的商品。"
                "可以调整一下关键词或预算再试试，比如：跑鞋、卫衣、耳机。"
            )

        # 兜底路径已按相关性（命中子词数）排序，保持该顺序；
        # 常规路径按价格升序展示
        if not fallback_matched:
            matched.sort(key=lambda g: g["price"])
            lines = [f"为您找到 {len(matched)} 件相关商品（按价格从低到高，展示前 5 件）："]
        else:
            lines = [f"为您找到 {len(matched)} 件相关商品（按相关度排序，展示前 5 件）："]
        for i, g in enumerate(matched[:5], 1):
            hl = f" — {g['highlights']}" if g.get("highlights") else ""
            link = f" | [查看商品]({g['link']})" if g.get("link") else ""
            lines.append(
                f"{i}. {g['name']}（SKU: {g['sku']}）¥{g['price']}"
                f"{hl} | 库存{g['stock']}{link}"
            )
        if len(matched) > 5:
            lines.append(f"...等共 {len(matched)} 件，可告诉我更具体的需求缩小范围。")
        lines.append("如需查看某件商品的尺码、颜色等详情，请提供 SKU 或名称。")
        return "\n".join(lines)
