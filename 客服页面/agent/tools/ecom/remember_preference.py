"""偏好记忆工具：将顾客偏好写入账号记忆文件（需登录，低风险写，可直接执行）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import check_login, append_memory


class RememberPreferenceTool(Tool):
    """把顾客偏好（尺码、预算、颜色、习惯等）追加写入该账号的记忆文件。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "remember_preference"

    @property
    def description(self) -> str:
        return (
            "将顾客的偏好写入其账号记忆（需登录；未登录时返回登录提示）。"
            "适用场景：顾客说'记住我……'（如'记住我平时穿42码的鞋'、'记住我喜欢黑色'），"
            "或在对话中识别到明确的尺码/预算/品类/颜色/收货习惯等偏好时主动记录并告知顾客。"
            "该记忆跨会话长期保留（清空对话不清除），用于后续个性化推荐。"
            "输入：content（偏好内容，一句话描述）。输出：写入确认。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "偏好内容，如'平时穿42码的鞋'、'偏好黑配色'",
                },
            },
            "required": ["content"],
        }

    async def execute(self, **kwargs) -> str:
        err = check_login(self._logged_in)
        if err:
            return err

        content = str(kwargs.get("content") or "").strip()
        if not content:
            return "请告诉我需要记住的偏好内容，例如：记住我平时穿 42 码的鞋。"

        append_memory(self._actor, content)
        return f"已记住：{content}。之后为您推荐商品时会结合这条偏好～"
