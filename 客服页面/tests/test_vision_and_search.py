"""图片识别模型修复与商品搜索中文子词兜底测试。

覆盖：
- vision.py：默认模型为 Qwen3-VL（Qwen2.5-VL 已在硅基流动下线）；
  主模型失败时自动降级备用模型；全部失败返回空字符串
- search_goods.py：无空格中文长关键词（如图片识别出的
  "灰色网面透气跑鞋"）整词无结果时按 2 字滑窗子词兜底命中
"""

import asyncio

import pytest

from agent.tools.ecom.search_goods import SearchGoodsTool


# ---------- vision：模型修复与降级 ----------

class TestVisionModelConfig:
    def test_default_model_is_qwen3_vl(self, monkeypatch):
        """未设环境变量时默认用 Qwen3-VL-8B（Qwen2.5-VL 已下线）。"""
        monkeypatch.delenv("MOCK_VISION_MODEL", raising=False)
        import importlib
        import providers.vision as vision
        importlib.reload(vision)
        assert vision.VISION_MODEL == "Qwen/Qwen3-VL-8B-Instruct"
        assert len(vision.VLM_FALLBACK_MODELS) >= 1

    def test_env_override_still_works(self, monkeypatch):
        """MOCK_VISION_MODEL 环境变量覆盖仍然生效（接口不变）。"""
        monkeypatch.setenv("MOCK_VISION_MODEL", "Custom/VLM")
        import importlib
        import providers.vision as vision
        importlib.reload(vision)
        assert vision.VISION_MODEL == "Custom/VLM"


class TestVisionFallback:
    def _run(self, monkeypatch, results):
        """按 results 队列模拟各模型调用结果，跑 recognize_product_image。"""
        import providers.vision as vision

        async def fake_recognize(model, image):
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        monkeypatch.setattr(vision, "_recognize_with", fake_recognize)
        return asyncio.run(vision.recognize_product_image("data:image/png;base64,x"))

    def test_primary_success(self, monkeypatch):
        """主模型成功 → 直接返回，不碰备用模型。"""
        import providers.vision as vision
        assert self._run(monkeypatch, ["白色运动鞋"]) == "白色运动鞋"

    def test_fallback_on_primary_error(self, monkeypatch):
        """主模型报错（如模型下线 400）→ 自动降级备用模型成功。"""
        assert self._run(
            monkeypatch, [RuntimeError("400 Model does not exist"), "灰色网面跑鞋"]
        ) == "灰色网面跑鞋"

    def test_all_models_fail_returns_empty(self, monkeypatch):
        """全部模型失败 → 返回空字符串（web 层据此提示用户）。"""
        assert self._run(
            monkeypatch, [RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom")]
        ) == ""

    def test_empty_result_falls_through(self, monkeypatch):
        """模型返回空内容 → 继续尝试下一个模型。"""
        assert self._run(monkeypatch, ["", "红色卫衣"]) == "红色卫衣"

    def test_empty_input_returns_empty(self, monkeypatch):
        """空图片输入 → 直接返回空，不调模型。"""
        import providers.vision as vision
        assert asyncio.run(vision.recognize_product_image("")) == ""


# ---------- search_goods：中文长词 2 字滑窗兜底 ----------

GOODS = [
    {"sku": "RUN-001", "name": "轻风慢跑鞋", "category": "运动鞋", "price": 299,
     "highlights": "一体织透气鞋面", "description": "透气网面慢跑鞋", "stock": 10,
     "scenes": ["运动"], "link": "https://shop.demo.cn/goods/RUN-001"},
    {"sku": "EAR-001", "name": "无线主动降噪耳机", "category": "数码配件", "price": 599,
     "highlights": "主动降噪", "description": "头戴式耳机", "stock": 5, "scenes": [],
     "link": "https://shop.demo.cn/goods/EAR-001"},
]


@pytest.fixture
def goods_tool(monkeypatch):
    # 固定数据后端为 json 并注入固定商品，避免 merchant_api 实时拉取干扰
    monkeypatch.setattr("agent.tools.ecom.search_goods.DATA_BACKEND", "json")
    tool = SearchGoodsTool()
    tool._goods = GOODS
    return tool


class TestSearchChineseFallback:
    def test_long_chinese_phrase_matches(self, goods_tool):
        """整词"灰色网面透气跑鞋"无空格 → 滑窗子词（透气/跑鞋/网面）兜底命中。"""
        result = asyncio.run(goods_tool.execute(keyword="灰色网面透气跑鞋"))
        assert "轻风慢跑鞋" in result
        assert "没有找到" not in result

    def test_full_sentence_ranks_by_relevance(self, goods_tool):
        """整句描述（含标点噪音词）按相关度排序：跑鞋应排在无关商品前。"""
        desc = "灰色网面透气跑鞋，适合日常运动穿着"
        result = asyncio.run(goods_tool.execute(keyword=desc))
        assert "按相关度排序" in result
        first_line = result.splitlines()[1]
        # 命中子词最多的跑鞋应排第一，而非价格最低的耳机
        assert "轻风慢跑鞋" in first_line

    def test_exact_word_still_prefers_exact_match(self, goods_tool):
        """正常短词（如"跑鞋"）行为不变：整词直接命中、按价格排序。"""
        result = asyncio.run(goods_tool.execute(keyword="跑鞋"))
        assert "轻风慢跑鞋" in result
        assert "按价格从低到高" in result

    def test_truly_no_match_still_empty(self, goods_tool):
        """完全无关的长词兜底后仍无结果 → 保持原"没有找到"提示。"""
        result = asyncio.run(goods_tool.execute(keyword="鲜榨橙汁冰淇淋"))
        assert "没有找到" in result

    def test_fallback_respects_max_price(self, goods_tool):
        """兜底路径同样遵守预算上限过滤。"""
        result = asyncio.run(
            goods_tool.execute(keyword="灰色网面透气跑鞋", max_price=100)
        )
        assert "没有找到" in result


# ---------- search_goods：推荐结果附带购买链接 ----------

class TestSearchGoodsLink:
    def test_result_contains_buy_link(self, goods_tool):
        """搜索结果每件商品附 markdown 购买链接（机器人回复可点击）。"""
        result = asyncio.run(goods_tool.execute(keyword="跑鞋"))
        assert "[查看商品](https://shop.demo.cn/goods/RUN-001)" in result

    def test_link_present_in_relevance_order_path(self, goods_tool):
        """子词兜底（相关度排序）路径同样输出链接。"""
        result = asyncio.run(goods_tool.execute(keyword="灰色网面透气跑鞋"))
        assert "[查看商品](https://shop.demo.cn/goods/RUN-001)" in result

    def test_new_goods_listing_contains_link(self, goods_tool):
        """「新品」列表路径同样输出链接。"""
        for g in GOODS:
            g["created_at"] = "2026-09-09 10:00:00"
        try:
            result = asyncio.run(goods_tool.execute(keyword="新品"))
            assert "https://shop.demo.cn/goods/" in result
        finally:  # 不留污染，其他测试类复用 GOODS
            for g in GOODS:
                g.pop("created_at", None)
