"""离线演示 Agent：无 LLM API key 时的关键词规则路由实现。

与 AgentLoop 保持接口兼容（run / tools / clear_history），
Gateway 以鸭子类型调用，核心链路（渠道/总线/网关/工具/会话）零修改复用。
定位是演示兜底而非 LLM 替代：仅做关键词级意图识别，
写操作遵循与在线模式一致的"复述 → 确认 → 执行"约定。
"""

import re

from agent.tools.ecom.store import ORDER_ID_RE, TICKET_ID_RE


class OfflineAgent:
    """关键词规则路由 Agent（离线演示模式）。"""

    def __init__(self, tools, session_manager, session_key: str) -> None:
        self.tools = tools
        self.session_manager = session_manager
        self.session_key = session_key
        self._history = session_manager.get_history(session_key)

        # 内部状态
        self._pending = None        # 待确认的写操作 {"tool": ..., "args": ..., "echo": ...}
        self._miss_count = 0        # 连续未识别轮数
        self._last_order_id = None  # 最近一次涉及的订单号
        self._last_sku = None       # 最近一次推荐的 SKU

    # ---------- 对外接口（与 AgentLoop 兼容） ----------

    async def run(self, user_message: str, persist_user_message: bool = True,
                  on_event=None) -> str:
        # persist_user_message / on_event 仅为对齐 AgentLoop 签名（离线模式无流式）
        self.session_manager.save_message(
            self.session_key, {"role": "user", "content": user_message}
        )
        try:
            reply = await self._route(user_message)
        except Exception as e:
            reply = f"抱歉，处理时出现了一点问题（{e}），请换个说法再试试。"
        self.session_manager.save_message(
            self.session_key, {"role": "assistant", "content": reply}
        )
        return reply

    def clear_history(self) -> None:
        self._history.clear()
        self._pending = None
        self._miss_count = 0
        self.session_manager.clear(self.session_key)

    # ---------- 工具调用 ----------

    async def _call(self, tool: str, args: dict) -> str:
        print(f"  🛠️  [离线] 调用工具: {tool}({args})")
        result = await self.tools.execute(tool, args)
        # 记住最近涉及的订单号，供后续上下文引用
        m = ORDER_ID_RE.search(result or "")
        if m:
            self._last_order_id = m.group(1)
        return result

    # ---------- 意图路由 ----------

    async def _route(self, text: str) -> str:
        # 0. 待确认的写操作优先处理
        if self._pending:
            if re.match(r"^\s*(确认|确定|好的|可以|嗯|同意)[!。.，,]?\s*$", text):
                return await self._execute_pending()
            if re.match(r"^\s*(取消|算了|不了|不用|不确认)\s*$", text):
                self._pending = None
                return "好的，已取消本次操作。还有其他可以帮您的吗？"

        # 1. 转人工 / 投诉升级 / 负面情绪（兜底）
        if re.search(r"转人工|人工客服|找真人|真人客服|投诉到底|我要投诉", text):
            return await self._escalate("顾客要求人工服务")
        if re.search(r"不行|没用|不满意|解决不了|什么破|太差|气死", text):
            return await self._escalate("顾客对处理结果不满意")

        # 2. 记住偏好
        if "记住" in text:
            content = re.sub(r"^[我]?", "", text.split("记住", 1)[1]).strip("，。!！:： ")
            if content:
                result = await self._call("remember_preference", {"content": content})
                return f"{result}\n\n（离线提示：本条偏好已长期保存，清空对话也不会丢失。）"
            return "好的，请告诉我需要记住的内容，例如：记住我平时穿 42 码的鞋。"

        # 3. 偏好回溯
        if re.search(r"多大码|什么码|我的偏好|我的档案|我的资料|记不记得我|还记得我", text):
            return await self._call("get_my_profile", {})

        # 4. 修改地址（写操作：复述 → 确认 → 执行）
        if re.search(r"改地址|修改地址|地址.{0,3}(改|换)", text):
            return await self._handle_update_address(text)

        # 5. 退货 / 售后
        if re.search(r"退货|退换|退掉|退款|换货", text):
            return await self._handle_return(text)

        # 6. 订单号直接查询
        m = ORDER_ID_RE.search(text)
        if m:
            return await self._call("query_order", {"order_id": m.group(1)})

        # 7. 工单进度
        if re.search(r"工单|进度|处理得怎么样", text):
            tk = TICKET_ID_RE.search(text)
            args = {"ticket_id": tk.group(1)} if tk else {}
            return await self._call("query_ticket_progress", args)

        # 8. 物流
        if re.search(r"物流|快递|包裹|到哪|哪了|什么时候到|发货了吗|发了没", text):
            args = {"order_id": m.group(1)} if m else {}
            return "好的，帮您查询物流：\n\n" + await self._call("query_logistics", args)

        # 9. 我的订单（列表）
        if re.search(r"我的订单|查订单|订单列表|所有订单|订单查询", text):
            return await self._call("query_order", {})

        # 10. 商品详情（SKU / 上一轮推荐 / 尺码询问）
        sku = re.search(r"\b([A-Z]{2,8}-\d{2,4})\b", text.upper())
        if sku:
            return await self._call("get_goods_detail", {"sku": sku.group(1)})
        if re.search(r"\d{2}\s*码|有货吗|有卖吗|还有吗|详情", text):
            if self._last_sku:
                return await self._call("get_goods_detail", {"sku": self._last_sku})
            return "请告诉我商品名称或 SKU（如 RUN-001），我帮您查一下详情～"

        # 11. 商品搜索 / 推荐
        if re.search(r"推荐|想买|买个|买一|礼物|送礼|有没有|看看|想看看|有什么|想购|选一", text):
            return await self._handle_search(text)

        # 12. 问候 / 帮助
        if re.search(r"你好|您好|hi|hello|在吗|早上好|晚上好", text, re.IGNORECASE):
            return self._greeting()
        if re.search(r"帮助|help|能做什么|你会什么|指令", text, re.IGNORECASE):
            return self._capabilities()

        # 13. 未识别：连续 2 轮触发兜底转人工
        self._miss_count += 1
        if self._miss_count >= 2:
            return await self._escalate("离线模式连续多轮无法理解顾客需求")
        return self._miss_hint()

    # ---------- 场景处理器 ----------

    async def _handle_search(self, text: str) -> str:
        # 预算提取
        price = None
        pm = re.search(r"(\d{2,5})\s*(?:元|块)?\s*(?:左右|以内|以下|内|预算)", text) \
             or re.search(r"预算\s*(\d{2,5})", text)
        if pm:
            price = int(pm.group(1))

        # 关键词识别（商品域同义词）
        keyword = None
        rules = [
            (r"跑步鞋|慢跑|跑步|跑鞋", "跑鞋"),
            (r"运动鞋", "运动鞋"),
            (r"板鞋|帆布鞋", "板鞋"),
            (r"皮鞋", "皮鞋"),
            (r"老爹鞋", "老爹鞋"),
            (r"洞洞鞋|凉鞋", "洞洞鞋"),
            (r"T恤|体恤", "T恤"),
            (r"卫衣", "卫衣"),
            (r"风衣|皮肤衣", "风衣"),
            (r"冲锋衣", "冲锋衣"),
            (r"运动裤|裤子", "运动裤"),
            (r"降噪耳机|耳机", "耳机"),
            (r"智能手表|运动手表|手表", "手表"),
            (r"腕表", "腕表"),
            (r"双肩包|背包", "双肩包"),
            (r"礼物|送礼|送男朋友|送女朋友|送人", "送礼"),
            (r"鞋", "鞋"),
        ]
        for pattern, kw in rules:
            if re.search(pattern, text):
                keyword = kw
                break

        args = {}
        if keyword:
            args["keyword"] = keyword
        if price:
            args["max_price"] = price
        if not args:
            return "好的，请告诉我您想找什么商品（如跑鞋、耳机、双肩包），以及预算～"

        result = await self._call("search_goods", args)
        # 记住首个推荐结果，供"有40码吗"这类追问使用
        sku = re.search(r"SKU:\s*([A-Z]+-\d+)", result or "")
        if sku:
            self._last_sku = sku.group(1)
        return result + "\n\n（离线提示：配置 LLM API key 后可获得更自然的智能推荐对话。）"

    async def _handle_update_address(self, text: str) -> str:
        m = ORDER_ID_RE.search(text)
        if not m:
            return "请提供订单号和新地址，格式：改地址 订单号 新地址\n例如：改地址 SO20260903004 上海市浦东新区张江路5号"

        # 提取订单号之后的内容作为新地址
        rest = text[m.end():]
        addr = re.sub(
            r"^[\s,，。.。:：的]*"
            r"(地址)?\s*(改为|改成|换为|换成|修改为|更改为|新地址[:：]?)?\s*",
            "", rest,
        ).strip()
        if not addr:
            return "请一并提供新的收货地址，格式：改地址 订单号 新地址"

        self._pending = {
            "tool": "update_address",
            "args": {"order_id": m.group(1), "new_address": addr},
            "echo": f"将订单 {m.group(1)} 的收货地址修改为「{addr}」",
        }
        return (
            "请确认以下操作：\n"
            f"- {self._pending['echo']}\n"
            "回复「确认」执行，回复「取消」放弃。"
        )

    async def _handle_return(self, text: str) -> str:
        m = ORDER_ID_RE.search(text)

        # 确认退货 → 创建售后工单
        if re.search(r"确认|同意|继续", text):
            order_id = m.group(1) if m else self._last_order_id
            if order_id:
                return await self._call("create_ticket", {
                    "type": "售后",
                    "order_id": order_id,
                    "description": "顾客确认申请退货（离线演示）",
                })
            return "请先提供订单号，我再为您创建退货工单～"

        # 退货资格判定
        args = {"order_id": m.group(1)} if m else {}
        return await self._call("check_return_eligibility", args)

    async def _execute_pending(self) -> str:
        pending, self._pending = self._pending, None
        result = await self._call(pending["tool"], pending["args"])
        return f"已执行：{pending['echo']}\n\n{result}"

    async def _escalate(self, reason: str) -> str:
        result = await self._call("escalate_to_human", {
            "summary": f"离线模式自动转人工：{reason}",
            "reason": reason,
        })
        return (
            "非常抱歉给您带来不便，已为您转接人工客服 🙏\n\n" + result +
            "\n\n（离线提示：人工客服接入后将继续跟进您的问题。）"
        )

    # ---------- 固定话术 ----------

    def _greeting(self) -> str:
        return (
            "您好，我是智能客服小纳 🛍️\n"
            "可以帮您：推荐商品、查订单物流、办理退换售后、记录偏好。\n"
            "未登录的话只能逛逛商品哦（演示账号：han / 123456）。"
        )

    def _capabilities(self) -> str:
        return (
            "离线模式下我支持这些指令：\n"
            "· 推荐 300 左右的跑鞋 —— 商品推荐（免登录）\n"
            "· RUN-001 有货吗 / 这双鞋有40码吗 —— 商品详情（免登录）\n"
            "· 我的订单 / 查订单 SO20260901001 —— 订单查询（需登录）\n"
            "· 我的包裹到哪了 —— 物流查询（需登录）\n"
            "· 我想退货 / 确认退货 —— 售后流程（需登录）\n"
            "· 改地址 SO20260903004 新地址 —— 修改收货地址（需确认）\n"
            "· 记住我喜欢黑色 / 我平时穿多大码 —— 偏好记忆（需登录）\n"
            "· 转人工 —— 兜底转人工（需登录）\n"
            "配置 LLM API key 后，我将支持自然语言智能对话。"
        )

    def _miss_hint(self) -> str:
        return (
            "抱歉，离线模式下我没太听懂您的意思 🙏 您可以试试：\n"
            "· 「推荐 300 左右的跑鞋」（商品推荐）\n"
            "· 「查订单 SO20260901001」（订单查询）\n"
            "· 「我的包裹到哪了」（物流查询）\n"
            "· 输入「帮助」查看完整指令列表"
        )
