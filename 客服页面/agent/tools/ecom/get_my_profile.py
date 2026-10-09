"""账号档案工具：读取当前登录顾客的档案与偏好记忆（需登录）。"""

from agent.tools.base import Tool
from agent.tools.ecom.store import (
    check_login, get_user, load_memory, mask_name,
)


class GetMyProfileTool(Tool):
    """读取当前登录顾客的档案（姓名脱敏、等级、积分）与账号偏好记忆。"""

    def __init__(self, actor: str | None = None, logged_in: bool = False) -> None:
        self._actor = actor
        self._logged_in = logged_in

    @property
    def name(self) -> str:
        return "get_my_profile"

    @property
    def description(self) -> str:
        return (
            "读取当前登录顾客的档案与偏好记忆（需登录；未登录时返回登录提示）。"
            "适用场景：顾客询问自身偏好（如'我平时穿多大码'）、"
            "个性化推荐前先了解顾客已有偏好。"
            "无需参数，身份由系统注入。"
            "输出：姓名（脱敏）、会员等级、积分、偏好记忆列表。"
        )

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        err = check_login(self._logged_in)
        if err:
            return err

        user = get_user(self._actor)
        if user is None:
            return "账号信息异常，请重新登录。"

        lines = [
            f"顾客档案：{mask_name(user.get('name', ''))}（{user.get('level', '普通会员')}，"
            f"积分 {user.get('points', 0)}）",
        ]
        memories = load_memory(self._actor)
        if memories:
            lines.append("偏好记忆：")
            lines.extend(f"- {m}" for m in memories)
        else:
            lines.append("偏好记忆：暂无。顾客告知偏好时可以帮TA记住（如'记住我穿42码'）。")
        return "\n".join(lines)
