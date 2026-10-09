"""Web 渠道实现模块，提供 WebSocket 接口与演示级账号登录。

除用户端对话（/）外，还承载客服工作台（/agent）与管理后台（/admin），
以及三端共用的演示级 REST API：
- GET  /api/sessions                 会话列表（客服端左栏 / 管理端统计）
- GET  /api/sessions/{key}/messages  会话完整历史（客服端聊天区）
- GET  /api/active-chats             在线用户连接（客服端直达消息用）
- POST /api/agent/send               人工客服向在线用户推送消息
- GET  /api/orders?username=         用户订单（客服端辅助面板）
- GET  /api/stats                    数据看板聚合
- GET  /api/quality                  会话质检评分
- GET  /api/voc                      用户热点问题 / 情感分析
- GET  /api/config                   系统配置（自动退款开关 / 风控阈值）
"""

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from bus import InboundMessage, MessageBus, OutboundMessage
from session.manager import SessionManager
from security.auth_service import authenticate, issue_token
from security.jwt import decode_access_token
from agent.tools.ecom import store as data_store
from providers.vision import recognize_product_image
from .base import Channel

# 演示账号数据文件（项目根目录 data/users.json）
USERS_FILE = Path(__file__).resolve().parents[1] / "data" / "users.json"

# 业务数据目录（订单 / 工单 / 退款记录）
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# 会话历史目录默认值（main.py 会显式传入）
DEFAULT_SESSIONS_DIR = Path(__file__).resolve().parents[1] / "workspace" / "sessions"

# 工具库根（供 web_ui 下的静态页面读取）
WEB_UI_DIR = Path(__file__).parent / "web_ui"


def _read_json(path: Path, default):
    """读取 JSON 文件，失败时返回默认值。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_session_file(path: Path) -> list[dict]:
    """读取单个会话 JSONL 文件，返回带 timestamp 的消息列表。"""
    messages = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return messages


def _parse_ts(value) -> datetime | None:
    """解析 ISO 时间戳字符串，失败返回 None。"""
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


class WebChannel(Channel):
    """Web 渠道，通过 WebSocket 接收和发送消息。

    登录机制（演示级）：
    - 客户端通过 WebSocket 发送 {"type": "login", "username": ..., "password": ...}
    - 校验 data/users.json，成功后该连接后续消息的 sender_id 切换为 username，
      Gateway 由此生成账号级 session_key（web:{username}），自动恢复该账号会话
    - 未登录时 sender_id 为 guest-{uuid}，仅能使用免登录的公共能力
    """

    def __init__(self, bus: MessageBus, host: str = "0.0.0.0", port: int = 8080,
                 sessions_dir: str | None = None):
        """初始化 Web 渠道。

        Args:
            bus: 消息总线实例
            host: 监听地址
            port: 监听端口
            sessions_dir: 会话历史目录（客服端/管理端 API 读取用）
        """
        super().__init__(name="web", bus=bus)
        self.host = host
        self.port = port
        self.sessions_dir = Path(sessions_dir) if sessions_dir else DEFAULT_SESSIONS_DIR
        self._session_manager = SessionManager(sessions_dir=str(self.sessions_dir))
        self._connections: dict[str, WebSocket] = {}  # client_id -> WebSocket
        self._logins: dict[str, str | None] = {}       # client_id -> 登录用户名（None 为游客）
        self._takeover = None                          # 人工接管管理器（main.py 注入）
        # 客服端已读标记：session_key -> ISO 时间戳（持久化文件，未读红点用）
        self._reads_file = self.sessions_dir / ".agent_reads.json"
        self._session_reads: dict[str, str] = self._load_reads()
        # 服务启动时间：从未标记过已读的会话，只统计启动后收到的顾客消息（避免首次满屏红点）
        self._start_time = datetime.now().isoformat()
        self._app: FastAPI | None = None
        self._server: uvicorn.Server | None = None

    def set_takeover(self, takeover) -> None:
        """注入人工接管管理器（Gateway 创建后由 main.py 调用）。"""
        self._takeover = takeover

    def _load_reads(self) -> dict[str, str]:
        """加载客服已读标记文件（不存在或损坏时返回空表）。"""
        try:
            data = json.loads(self._reads_file.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_reads(self) -> None:
        """保存客服已读标记到文件（失败静默，仅影响红点记忆）。"""
        try:
            self._reads_file.write_text(
                json.dumps(self._session_reads, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    async def start(self) -> None:
        """启动 FastAPI + WebSocket 服务。"""
        # 创建 FastAPI 应用
        self._app = FastAPI(title="NanoClaw Web")

        # 注册路由
        self._register_routes()

        display_host = "localhost" if self.host in ("0.0.0.0", "::", "") else self.host
        print(f"[WebChannel] 正在启动 Web 服务: http://{display_host}:{self.port}")

        # 使用 uvicorn.Server.serve() 启动（异步方式，不阻塞事件循环）
        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        await self._server.serve()

    def _register_routes(self) -> None:
        """注册 FastAPI 路由（三端页面 + REST API + WebSocket）。"""
        if self._app is None:
            return

        # ---------- API 鉴权中间件（生产落地 P0-1.2，env 门控）----------
        # REQUIRE_API_AUTH=true 时：/api/*（除 /api/auth/login）需要有效的
        # 管理员 JWT（Authorization: Bearer <token>）；缺 token/无效 → 401，
        # 非管理员角色 → 403。默认 false：全部开放（与原有行为一致，接口不变）。
        # 注意：注册顺序在 CORS 之前（本中间件为内层），保证 OPTIONS 预检
        # 由 CORS 中间件先行放行、鉴权失败响应也携带跨域头。
        REQUIRE_API_AUTH = os.getenv("REQUIRE_API_AUTH", "false").lower() == "true"
        AUTH_EXEMPT_PATHS = {"/api/auth/login"}

        @self._app.middleware("http")
        async def api_auth_middleware(request: Request, call_next):
            if (
                REQUIRE_API_AUTH
                and request.method != "OPTIONS"  # 放行 CORS 预检
                and request.url.path.startswith("/api")
                and request.url.path not in AUTH_EXEMPT_PATHS
            ):
                auth = request.headers.get("authorization", "")
                if not auth.startswith("Bearer "):
                    return JSONResponse(
                        {"detail": "未提供认证令牌"}, status_code=401
                    )
                payload = decode_access_token(auth.removeprefix("Bearer "))
                if payload is None:
                    return JSONResponse(
                        {"detail": "令牌无效或已过期"}, status_code=401
                    )
                if payload.get("role") != "admin":
                    return JSONResponse(
                        {"detail": "需要管理员权限"}, status_code=403
                    )
            return await call_next(request)

        if REQUIRE_API_AUTH:
            print("[WebChannel] 🔒 API 鉴权已启用（REQUIRE_API_AUTH=true，需管理员 JWT）")

        # ---------- CORS 中间件（生产落地 P0-1.2/3.4）----------
        # 允许的来源（生产环境通过 CORS_ORIGINS 环境变量填具体域名，不要用 *）。
        # 同源请求（三端页面自身 fetch /api/*）不触发 CORS，此配置不影响现有功能；
        # 仅约束跨域调用（如独立部署的前端站点）。
        ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:8080").split(",")
        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=ALLOWED_ORIGINS,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

        # ---------- 健康检查（生产落地 P0-3.4：Docker healthcheck / 负载均衡探活用）----------

        @self._app.get("/health")
        async def health_check():
            """健康检查端点：无鉴权、轻量，探活专用。"""
            return {"status": "ok", "timestamp": datetime.now().isoformat()}

        # ---------- REST 登录（生产落地 P0-1.2 步骤 4）----------
        # 客服端/管理端页面登录入口：校验成功签发 JWT（含 role），
        # 前端保存后所有 /api/* 请求携带 Authorization: Bearer <token>。
        # 任何模式（REQUIRE_API_AUTH 开关与否）均可调用，开关仅控制是否强制校验。

        @self._app.post("/api/auth/login")
        async def api_auth_login(request: Request):
            try:
                payload = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "message": "请求体非法"}, status_code=400
                )
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", ""))
            user = authenticate(username, password)
            if user is None:
                return JSONResponse(
                    {"ok": False, "message": "账号或密码错误"}, status_code=401
                )
            return JSONResponse({
                "ok": True,
                "token": issue_token(user),
                "username": user["username"],
                "name": user.get("name", ""),
                "role": user.get("role", "user"),
            })

        # ---------- 三端页面 ----------

        def _serve_page(filename: str):
            def handler():
                path = WEB_UI_DIR / filename
                if path.exists():
                    # no-cache：页面内容随代码迭代更新，禁止浏览器缓存旧版
                    return HTMLResponse(
                        content=path.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-cache"},
                    )
                return HTMLResponse(content=f"<h1>{filename} not found</h1>", status_code=404)
            return handler

        self._app.get("/")(_serve_page("index.html"))          # 用户端
        self._app.get("/agent")(_serve_page("agent.html"))     # 客服端工作台
        self._app.get("/admin")(_serve_page("admin.html"))     # 管理后台
        self._app.get("/mall")(_serve_page("mall.html"))       # 商品陈列页

        # ---------- 三端共用 API ----------

        def _all_session_messages() -> list[tuple[str, list[dict]]]:
            """扫描会话目录，返回 [(session_key, messages)]。"""
            result = []
            if not self.sessions_dir.is_dir():
                return result
            for f in sorted(self.sessions_dir.glob("*.jsonl")):
                key = f.stem.replace("_", ":")
                result.append((key, _load_session_file(f)))
            return result

        @self._app.get("/api/sessions")
        async def api_sessions():
            """会话列表（客服端左栏，含未读红点数）。"""
            items = []
            for key, msgs in _all_session_messages():
                if not msgs:
                    continue
                channel, _, username = key.partition(":")
                last = msgs[-1]
                last_user = next((m["content"] for m in reversed(msgs)
                                  if m.get("role") == "user"), "")
                # 未读数：顾客消息中晚于客服上次已读时间的条数
                # （从未标记过则从服务启动时间起算，避免历史消息满屏红点）
                last_read = _parse_ts(self._session_reads.get(key))
                if last_read is None:
                    last_read = _parse_ts(self._start_time)
                unread = 0
                for m in msgs:
                    if m.get("role") != "user":
                        continue
                    ts = _parse_ts(m.get("timestamp"))
                    if ts is not None and (last_read is None or ts > last_read):
                        unread += 1
                items.append({
                    "key": key,
                    "channel": channel,
                    "username": username or key,
                    "msgCount": len(msgs),
                    "lastMessage": str(last.get("content", ""))[:60],
                    "lastUserMessage": str(last_user)[:60],
                    "lastTime": last.get("timestamp", ""),
                    "unread": unread,
                })
            items.sort(key=lambda x: x["lastTime"], reverse=True)
            return JSONResponse(items)

        @self._app.get("/api/sessions/{session_key}/messages")
        async def api_session_messages(session_key: str):
            """单个会话完整历史（客服端聊天区）。"""
            path = self.sessions_dir / f"{session_key.replace(':', '_')}.jsonl"
            return JSONResponse(_load_session_file(path))

        @self._app.post("/api/sessions/{session_key}/read")
        async def api_mark_read(session_key: str):
            """客服端标记会话已读（清除红点，持久化）。"""
            self._session_reads[session_key] = datetime.now().isoformat()
            self._save_reads()
            return JSONResponse({"ok": True})

        @self._app.get("/api/active-chats")
        async def api_active_chats():
            """当前在线的用户连接（含游客，客服端在线状态/直达消息用）。"""
            return JSONResponse([
                # 游客（未登录）以 guest-{id前8位} 上报，与游客会话 key 命名一致
                {"client_id": cid, "username": name or f"guest-{cid[:8]}"}
                for cid, name in self._logins.items()
            ])

        @self._app.post("/api/agent/send")
        async def api_agent_send(request: Request):
            """人工客服消息直达在线用户（经总线出站 → WebChannel.send）。"""
            try:
                payload = await request.json()
            except Exception:
                return JSONResponse({"ok": False, "message": "请求体非法"}, status_code=400)

            client_id = str(payload.get("client_id", ""))
            content = str(payload.get("content", "")).strip()
            if not client_id or not content:
                return JSONResponse({"ok": False, "message": "缺少 client_id 或 content"}, status_code=400)
            if client_id not in self._connections:
                return JSONResponse({"ok": False, "message": "用户已断开连接"}, status_code=404)

            await self.bus.publish_outbound(OutboundMessage(
                channel="web",
                chat_id=client_id,
                content=f"👤【人工客服】{content}",
            ))

            # 人工客服消息写入会话历史（客服端刷新可见），并刷新人工接管活跃时间
            # 注意：用 assistant 角色 + 前缀存储（human 不是合法的 LLM 消息角色，
            # 会破坏 AI 恢复后的上下文），客服端按前缀识别人工消息
            sender_id = self._logins.get(client_id) or f"guest-{client_id[:8]}"
            session_key = f"web:{sender_id}"
            self._session_manager.save_message(
                session_key, {"role": "assistant", "content": f"👤【人工客服】{content}"}
            )
            if self._takeover is not None:
                self._takeover.touch(session_key)

            return JSONResponse({"ok": True})

        @self._app.get("/api/suggestion/{session_key}")
        async def api_suggestion(session_key: str):
            """人工接管期间 AI 为客服生成的推荐话术（客服端辅助面板轮询）。"""
            active = False
            suggestion = None
            if self._takeover is not None:
                active = self._takeover.is_active(session_key)
                suggestion = self._takeover.suggestions.get(session_key)
            return JSONResponse({"active": active, "suggestion": suggestion})

        @self._app.get("/api/takeovers")
        async def api_takeovers():
            """当前处于人工接管模式的会话列表（客服端转人工提醒/高亮用）。"""
            items = []
            if self._takeover is not None:
                for s in self._takeover.active_sessions():
                    channel, _, username = s["session_key"].partition(":")
                    items.append({
                        "sessionKey": s["session_key"],
                        "username": username or s["session_key"],
                        "channel": channel,
                        "idleSeconds": s["idle_seconds"],
                    })
            return JSONResponse(items)

        @self._app.get("/api/goods")
        async def api_goods():
            """在售商品列表（商品陈列页 /mall 用，经 store 走当前数据后端）。"""
            return JSONResponse(data_store.load_goods())

        @self._app.get("/api/orders")
        async def api_orders(username: str = ""):
            """用户订单（客服端辅助面板）。"""
            # 数据读取经 store（生产落地 P0-1.1：DATA_BACKEND=mysql 时自动读库）
            users = data_store.load_users()
            user = users.get(username)
            if user is None or username == "__default__":
                return JSONResponse([])
            orders = [o for o in data_store.load_orders()
                      if o.get("customer_id") == user.get("customer_id")]
            orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
            return JSONResponse(orders)

        @self._app.get("/api/stats")
        async def api_stats():
            """管理端数据看板聚合。"""
            sessions = _all_session_messages()

            # 逐会话统计
            message_count = 0
            channels: dict[str, int] = {}
            escalated = 0
            daily: dict[str, int] = {}
            for key, msgs in sessions:
                if not msgs:
                    continue
                channel, _, username = key.partition(":")
                channels[channel] = channels.get(channel, 0) + 1
                message_count += len(msgs)
                # 转人工/建工单的会话数（AI 解决率的分母）
                if any(m.get("role") == "assistant" and
                       re.search(r"工单|转人工|人工客服", str(m.get("content", "")))
                       for m in msgs):
                    escalated += 1
                for m in msgs:
                    day = str(m.get("timestamp", ""))[:10]
                    if day:
                        daily[day] = daily.get(day, 0) + 1

            # 近 7 日趋势（含空日期）
            today = datetime.now().date()
            trend = []
            for i in range(6, -1, -1):
                d = (today - timedelta(days=i)).isoformat()
                trend.append({"date": d, "count": daily.get(d, 0)})

            tickets = data_store.load_tickets()
            try:
                # 退款记录读取经 refund_policy（mysql 模式自动读库）
                from agent.tools.ecom import refund_policy as _rp
                refunds = _rp.load_refund_records()
            except Exception:
                refunds = _read_json(DATA_DIR / "refund_records.json", [])

            session_count = len([1 for _, m in sessions if m])
            return JSONResponse({
                "sessionCount": session_count,
                "messageCount": message_count,
                "aiResolveRate": round(1 - escalated / session_count, 2) if session_count else 1.0,
                "openTickets": sum(1 for t in tickets if t.get("status") != "已完成"),
                "orderCount": len(data_store.load_orders()),
                "autoRefundCount": sum(1 for r in refunds if r.get("decided_by") == "auto"),
                "channels": channels,
                "daily": trend,
                "recentTickets": sorted(
                    tickets, key=lambda t: t.get("created_at", ""), reverse=True)[:8],
            })

        @self._app.get("/api/quality")
        async def api_quality():
            """管理端质检中心：按规则给每个会话评分。"""
            items = []
            for key, msgs in _all_session_messages():
                if not msgs:
                    continue
                channel, _, username = key.partition(":")
                user_msgs = [m for m in msgs if m.get("role") == "user"]
                ai_msgs = [m for m in msgs if m.get("role") == "assistant"]
                if not user_msgs and not ai_msgs:
                    continue

                user_text = " ".join(str(m.get("content", "")) for m in user_msgs)
                ai_text = " ".join(str(m.get("content", "")) for m in ai_msgs)

                # 完整性：AI 回复含可执行信息（单号/金额/政策依据）
                completeness = min(100, 60 + 20 * bool(re.search(r"SO\d{6,}|TK\d{8,}", ai_text))
                                   + 20 * bool(re.search(r"¥|元|天|政策", ai_text)))
                # 响应质量：平均回复长度（太短说明敷衍）
                avg_len = (len(ai_text) / len(ai_msgs)) if ai_msgs else 0
                response = min(100, int(avg_len / 2))
                # 情绪处理：用户负面情绪后是否有安抚用语
                has_negative = re.search(r"投诉|差评|太慢|骗|垃圾|气人|失望", user_text)
                soothed = re.search(r"抱歉|抱歉|请放心|谅解|理解您", ai_text)
                emotion = 100 if not has_negative else (90 if soothed else 55)

                score = int(completeness * 0.4 + response * 0.3 + emotion * 0.3)
                verdict = "优秀" if score >= 85 else "良好" if score >= 70 else "待改进"
                items.append({
                    "username": username or key,
                    "rounds": len(user_msgs),
                    "completeness": completeness,
                    "response": response,
                    "emotion": emotion,
                    "score": score,
                    "verdict": verdict,
                })
            items.sort(key=lambda x: x["score"])
            return JSONResponse(items)

        @self._app.get("/api/voc")
        async def api_voc():
            """管理端 VOC 洞察：热点问题 / 情感分布 / 用户原声。"""
            topic_defs = {
                "物流查询": r"物流|快递|到哪|包裹|发货|配送|签收",
                "退换货": r"退货|退款|换货|退掉|退了",
                "商品咨询": r"推荐|尺码|颜色|材质|区别|哪款|适合",
                "价格优惠": r"便宜|优惠|券|折扣|降价|贵",
                "质量问题": r"质量|坏了|破损|开胶|掉色|故障",
                "订单查询": r"订单|我的订单|买了什么",
                "售后工单": r"工单|售后|投诉|客服",
                "账号相关": r"登录|密码|账号|地址|修改",
            }
            sentiments_def = {
                "正面": r"谢谢|感谢|好用|喜欢|不错|好评|满意|赞",
                "负面": r"投诉|差评|太慢|骗|垃圾|气人|失望|坏了|破损|退款",
            }
            topic_count = {k: 0 for k in topic_defs}
            sent_count = {"正面": 0, "负面": 0, "中性": 0}
            quotes = []

            for key, msgs in _all_session_messages():
                channel, _, username = key.partition(":")
                for m in msgs:
                    if m.get("role") != "user":
                        continue
                    text = str(m.get("content", "")).strip()
                    if len(text) < 4:
                        continue
                    for topic, pattern in topic_defs.items():
                        if re.search(pattern, text):
                            topic_count[topic] += 1
                    senti = next((s for s, p in sentiments_def.items() if re.search(p, text)), "中性")
                    sent_count[senti] += 1
                    if senti != "中性" and len(quotes) < 12:
                        quotes.append({"text": text[:60], "sentiment": senti,
                                       "username": username or key})

            topics = sorted(
                [{"topic": t, "count": c} for t, c in topic_count.items() if c > 0],
                key=lambda x: x["count"], reverse=True)
            return JSONResponse({
                "topics": topics,
                "sentiments": [{"label": s, "count": c} for s, c in sent_count.items()],
                "quotes": quotes,
            })

        @self._app.get("/api/config")
        async def api_config():
            """管理端系统配置：自动退款开关 / 风控阈值 / 知识库。"""
            try:
                from agent.tools.ecom import refund_policy
                auto_enabled = refund_policy.AUTO_APPROVE_ENABLED
                thresholds = [
                    {"name": "自动通过单笔上限", "desc": "超过此金额的退款转人工",
                     "value": f"¥{refund_policy.AUTO_APPROVE_MAX_AMOUNT}"},
                    {"name": "频次窗口", "desc": "自动通过次数统计窗口",
                     "value": f"{refund_policy.FREQ_WINDOW_DAYS} 天"},
                    {"name": "窗口内次数上限", "desc": "防薅羊毛频次限制",
                     "value": f"{refund_policy.FREQ_MAX_TIMES} 次"},
                    {"name": "历史退款率上限", "desc": "超过转人工",
                     "value": f"{refund_policy.MAX_REFUND_RATE * 100:.0f}%"},
                    {"name": "风险分阈值", "desc": "超过转人工",
                     "value": str(refund_policy.MAX_RISK_SCORE)},
                ]
            except Exception:
                auto_enabled = False
                thresholds = []

            goods = data_store.load_goods()
            categories = sorted({g.get("category", "其他") for g in goods})
            knowledge = [{"name": f"商品知识库 · {c}", "type": "商品"} for c in categories] + [
                {"name": "售后政策（7 天无理由）", "type": "政策"},
                {"name": "运费险规则", "type": "政策"},
                {"name": "质量问题判定标准", "type": "政策"},
            ]
            return JSONResponse({
                "autoRefundEnabled": auto_enabled,
                "thresholds": thresholds,
                "knowledge": knowledge,
            })

        # ---------- WebSocket ----------

        # WebSocket /ws
        @self._app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket 连接处理（含演示级登录）。"""
            # 接收连接
            await websocket.accept()

            # 生成 client_id 与游客标识
            client_id = str(uuid.uuid4())
            guest_id = f"guest-{client_id[:8]}"
            self._connections[client_id] = websocket
            self._logins[client_id] = None

            print(f"[WebChannel] 新连接: client_id={client_id}")

            try:
                # 循环接收消息
                while True:
                    data = await websocket.receive_text()

                    # 跳过空消息
                    if not data.strip():
                        continue

                    # ---- 登录/登出控制消息（不进入对话总线）----
                    if data.startswith("{"):
                        try:
                            payload = json.loads(data)
                        except json.JSONDecodeError:
                            payload = None
                        if isinstance(payload, dict) and payload.get("type") in ("login", "logout"):
                            reply = _handle_control(client_id, payload)
                            if reply is not None:
                                await websocket.send_text(
                                    json.dumps(reply, ensure_ascii=False)
                                )
                                # 登录成功且该会话处于人工接管中：
                                # 更新推送目标到新连接，并通知前端进入人工模式（刷新/重连场景）
                                if (
                                    reply.get("type") == "login_ok"
                                    and self._takeover is not None
                                ):
                                    username = self._logins.get(client_id)
                                    if username:
                                        sk = f"web:{username}"
                                        if self._takeover.is_active(sk):
                                            self._takeover.touch(sk, chat_id=client_id)
                                            await websocket.send_text(json.dumps(
                                                {"type": "human_takeover", "active": True},
                                                ensure_ascii=False,
                                            ))
                                continue

                        # ---- 图片消息：识别图片内容，拼成文本进入对话流程 ----
                        if isinstance(payload, dict) and payload.get("type") == "image_message":
                            image = str(payload.get("image", "")).strip()
                            text = str(payload.get("text", "")).strip()
                            description = await recognize_product_image(image)
                            if not description:
                                # 识别失败：直接发纯文本提示（前端按普通消息显示），不进入 Agent
                                await websocket.send_text(
                                    "抱歉，图片识别失败，请换一张清晰的图片再试。"
                                )
                                continue
                            question = text or "有没有类似的商品"
                            # 拼成可读文本（图片 base64 不进入会话历史，避免污染上下文）
                            data = (
                                f"顾客上传了一张商品图片，经识别图片内容为：{description}。"
                                f"顾客的问题是：{question}"
                            )

                    # ---- 普通对话消息：登录后以 username 作为 sender_id ----
                    sender_id = self._logins.get(client_id) or guest_id
                    print(
                        f"[WebChannel] 收到消息: client_id={client_id} "
                        f"user={sender_id} content={data[:50]}..."
                    )

                    # 收到即写入会话历史（不等网关处理）：
                    # AI 处理慢时客服端红点/未读数也能即时可见，
                    # persisted=True 告知网关不要重复保存
                    session_key = f"web:{sender_id}"
                    self._session_manager.save_message(
                        session_key, {"role": "user", "content": data}
                    )

                    # 构造入站消息（chat_id = client_id，用于找回连接发送回复）
                    message = InboundMessage(
                        channel="web",
                        sender_id=sender_id,
                        chat_id=client_id,
                        content=data,
                        raw={
                            "client_id": client_id,
                            "username": sender_id,
                        },
                        persisted=True,
                    )

                    # 发布到总线
                    await self.bus.publish_inbound(message)

            except Exception as e:
                # 连接断开
                print(f"[WebChannel] 连接断开: client_id={client_id} reason={e}")

            finally:
                # 清理连接与登录状态
                self._connections.pop(client_id, None)
                self._logins.pop(client_id, None)

        def _handle_control(client_id: str, payload: dict) -> dict | None:
            """处理登录/登出控制消息，返回回复给客户端的 JSON（dict）。

            Args:
                client_id: 连接标识
                payload: 客户端发来的控制消息

            Returns:
                dict | None: 回复内容；None 表示无法识别（按普通消息处理）
            """
            msg_type = payload.get("type")

            if msg_type == "login":
                username = str(payload.get("username", "")).strip()
                password = str(payload.get("password", ""))

                # ---- token 免密登录（商城页登录态带过来）----
                # 商城页 /mall 已通过 REST /api/auth/login 登录并持有服务端签发的
                # JWT；跳转到本对话页后凭 token 自动恢复登录，无需再次输入密码。
                # JWT 由本服务签名（decode_access_token 校验签名与有效期），
                # 与账号密码登录同等可信；原有账号密码登录分支保持不变。
                token = str(payload.get("token", "")).strip()
                if token and not password:
                    claims = decode_access_token(token)
                    if claims and claims.get("sub"):
                        token_username = str(claims["sub"]).strip()
                        self._logins[client_id] = token_username
                        print(f"[WebChannel] token 免密登录成功: {token_username}")
                        return {
                            "type": "login_ok",
                            "username": token_username,
                            "name": claims.get("name", ""),
                            "role": claims.get("role", "user"),
                            "token": token,
                        }
                    print("[WebChannel] token 免密登录失败: token 无效或已过期")
                    return {
                        "type": "login_fail",
                        "message": "登录凭证已过期，请重新登录",
                    }

                # 统一走 auth_service（bcrypt/明文兼容 + MySQL/JSON 双后端），
                # 成功签发 JWT 一并返回（前端保存后可用于 REST API 鉴权）
                user = authenticate(username, password)

                if user:
                    self._logins[client_id] = username
                    print(f"[WebChannel] 登录成功: {username}")
                    return {
                        "type": "login_ok",
                        "username": username,
                        "name": user.get("name", ""),
                        "role": user.get("role", "user"),
                        "token": issue_token(user),
                    }
                print(f"[WebChannel] 登录失败: {username}")
                return {
                    "type": "login_fail",
                    "message": "账号或密码错误（演示账号：han / 123456）",
                }

            if msg_type == "logout":
                self._logins[client_id] = None
                print(f"[WebChannel] 已登出: client_id={client_id}")
                return {"type": "logout_ok"}

            return None

    async def send(self, message: OutboundMessage) -> None:
        """发送消息到 WebSocket 客户端。

        Args:
            message: 出站消息实例
        """
        # 通过 chat_id 找回 WebSocket 连接
        ws = self._connections.get(message.chat_id)

        if ws is None:
            # 连接不存在（用户已断开）
            print(f"[WebChannel] 连接不存在: chat_id={message.chat_id}")
            return

        try:
            # 发送文本消息
            await ws.send_text(message.content)
            print(f"[WebChannel] 消息已发送: chat_id={message.chat_id}")

        except Exception as e:
            # 发送失败，清理连接
            print(f"[WebChannel] 发送失败: chat_id={message.chat_id} reason={e}")
            self._connections.pop(message.chat_id, None)

    async def stop(self) -> None:
        """停止 Web 服务。"""
        if self._server is not None:
            # 通知 server 停止
            self._server.should_exit = True
            self._server = None

        # 关闭所有 WebSocket 连接
        for client_id, ws in list(self._connections.items()):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()

        print("[WebChannel] 服务已停止")