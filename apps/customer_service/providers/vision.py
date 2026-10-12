"""图片识别（视觉模型）。

顾客在前端对话框附加商品图片时，用硅基流动的视觉模型识别图片内容，
返回一句话商品描述（品类 + 颜色 + 材质/款式等关键特征），
供后续 `search_goods` 匹配店内类似商品使用。

模型默认为硅基流动 Qwen3-VL-8B（Qwen2.5-VL 系列已下线），
可通过环境变量 MOCK_VISION_MODEL 覆盖；
主模型失败时依次尝试备用模型（VLM_FALLBACK_MODELS），
任一成功即返回，全部失败返回空字符串。
"""

import os

from openai import AsyncOpenAI

# 主模型（可用环境变量 MOCK_VISION_MODEL 覆盖）
VISION_MODEL = os.getenv("MOCK_VISION_MODEL", "Qwen/Qwen3-VL-8B-Instruct")

# 备用模型链：主模型失败（限流/维护/下线）时依次降级尝试
VLM_FALLBACK_MODELS = [
    "Qwen/Qwen3-VL-30B-A3B-Instruct",
    "Qwen/Qwen3-VL-32B-Instruct",
]

# 图片识别提示词：要求只输出商品描述本身，避免污染后续搜索关键词
_PROMPT = (
    "请识别这张商品图片，用一句简短的中文描述这是什么商品，"
    "重点说明品类、颜色、材质或款式等关键特征，"
    "用于在店铺内搜索类似商品。只输出商品描述本身，不要任何前缀或解释。"
)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """惰性创建视觉模型客户端（复用 config.json 的 api_key / base_url）。"""
    global _client
    if _client is None:
        from config import load_config

        cfg = load_config()
        _client = AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            timeout=60,
            max_retries=1,
        )
    return _client


async def _recognize_with(model: str, image_data_url: str) -> str:
    """用指定模型识别一次；成功返回描述，失败抛出原始异常。"""
    client = _get_client()
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
        max_tokens=200,
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


async def recognize_product_image(image_data_url: str) -> str:
    """识别商品图片，返回简短中文商品描述。

    Args:
        image_data_url: 图片的 data URL（如 data:image/jpeg;base64,xxx）。

    Returns:
        str: 商品描述文本；识别失败时返回空字符串。
    """
    if not image_data_url:
        return ""
    # 主模型 + 备用模型依次尝试，任一成功即返回
    for model in [VISION_MODEL, *VLM_FALLBACK_MODELS]:
        try:
            content = await _recognize_with(model, image_data_url)
            if content:
                return content
            print(f"[Vision] 模型 {model} 返回空结果，尝试下一个")
        except Exception as e:
            print(f"[Vision] 模型 {model} 识别失败: {e}")
    print("[Vision] 全部视觉模型均失败，请检查模型名称与 API Key")
    return ""
