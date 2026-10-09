"""渠道免登录绑定 + 跨渠道共用会话测试。

覆盖：
- store.resolve_channel_actor：绑定表命中 / 未绑定回退默认演示账号 /
  绑定指向不存在用户回退默认 / 无默认账号时游客
- main.resolve_actor：qq / feishu 免登录；web 登录 / 游客、cli 行为不变
- Gateway._resolve_session_key：qq / feishu 已知身份 → web:{username}，
  web 游客 / cli / 无默认账号回退原样
- 跨渠道会话共用：QQ 入站消息落盘到 web_{username}.jsonl（与 Web 端同一文件）
"""

import json

import pytest

from agent.tools.ecom import store as data_store
from agent.tools.ecom.store import (
    get_channel_binding,
    load_channel_bindings,
    resolve_channel_actor,
    set_channel_binding,
)
from bus.queue import InboundMessage, MessageBus
from gateway import Gateway
from session.manager import SessionManager


USERS_DATA = {
    "default_demo_user": "han",
    "users": [
        {"username": "han", "customer_id": "C001", "name": "韩梅梅", "role": "user"},
        {"username": "li", "customer_id": "C002", "name": "李雷", "role": "user"},
    ],
}


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    """把 data 目录隔离到临时目录（users.json + channel_bindings.json）。"""
    monkeypatch.setattr(data_store, "DATA_DIR", tmp_path)
    (tmp_path / "users.json").write_text(
        json.dumps(USERS_DATA, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "channel_bindings.json").write_text(
        json.dumps({"bindings": {}}, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def make_inbound(channel: str, sender_id: str, content: str = "你好") -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        chat_id=sender_id,
        content=content,
        raw={},
    )


# ---------- store.resolve_channel_actor ----------

def test_resolve_channel_actor_bound(isolated_data):
    """绑定表命中且用户存在 → 该账号（登录态）。"""
    set_channel_binding("qq:OPEN123", "li")
    assert resolve_channel_actor("qq", "OPEN123") == ("li", True)
    set_channel_binding("feishu:ou_abc", "han")
    assert resolve_channel_actor("feishu", "ou_abc") == ("han", True)


def test_resolve_channel_actor_unbound_fallback_default(isolated_data):
    """未绑定 → 回退默认演示账号（免登录）。"""
    assert resolve_channel_actor("qq", "STRANGER") == ("han", True)
    assert resolve_channel_actor("feishu", "ou_stranger") == ("han", True)


def test_resolve_channel_actor_invalid_binding_fallback(isolated_data):
    """绑定指向不存在的用户 → 回退默认演示账号。"""
    set_channel_binding("qq:GHOST", "nobody")
    assert resolve_channel_actor("qq", "GHOST") == ("han", True)


def test_resolve_channel_actor_no_default(isolated_data):
    """无默认演示账号 → 游客。"""
    (isolated_data / "users.json").write_text(
        json.dumps({"users": USERS_DATA["users"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert resolve_channel_actor("qq", "OPEN123") == (None, False)


def test_binding_roundtrip(isolated_data):
    """绑定写入后可读回，且不破坏其他绑定。"""
    set_channel_binding("qq:A", "han")
    set_channel_binding("feishu:B", "li")
    bindings = load_channel_bindings()
    assert bindings == {"qq:A": "han", "feishu:B": "li"}
    assert get_channel_binding("qq:A") == "han"
    assert get_channel_binding("qq:MISSING") is None


# ---------- main.resolve_actor ----------

def test_resolve_actor_qq_feishu_login_free(isolated_data):
    """qq / feishu 渠道免登录（未绑定回退默认账号）。"""
    from main import resolve_actor

    assert resolve_actor("qq:ANYONE") == ("han", True)
    assert resolve_actor("feishu:ou_ANYONE") == ("han", True)
    set_channel_binding("qq:OPEN123", "li")
    assert resolve_actor("qq:OPEN123") == ("li", True)


def test_resolve_actor_existing_behavior_unchanged(isolated_data):
    """web 登录 / web 游客 / cli 原有行为不变。"""
    from main import resolve_actor

    assert resolve_actor("web:han") == ("han", True)
    assert resolve_actor("web:guest-abc12345") == (None, False)
    assert resolve_actor("cli:local") == ("han", True)


# ---------- Gateway._resolve_session_key ----------

def make_gateway() -> Gateway:
    return Gateway(MessageBus(), [], lambda key: None)


def test_gateway_session_key_qq_feishu_shared_with_web(isolated_data):
    """qq / feishu 免登录身份 → 会话键复用 web:{username}，与 Web 端共用历史。"""
    gateway = make_gateway()
    assert gateway._resolve_session_key(make_inbound("qq", "ANYONE")) == "web:han"
    assert gateway._resolve_session_key(make_inbound("feishu", "ou_ANYONE")) == "web:han"
    set_channel_binding("qq:OPEN123", "li")
    assert gateway._resolve_session_key(make_inbound("qq", "OPEN123")) == "web:li"


def test_gateway_session_key_others_unchanged(isolated_data):
    """web 游客 / cli / 未知渠道会话键保持原样。"""
    gateway = make_gateway()
    assert gateway._resolve_session_key(make_inbound("web", "guest-abc")) == "web:guest-abc"
    assert gateway._resolve_session_key(make_inbound("web", "han")) == "web:han"
    assert gateway._resolve_session_key(make_inbound("cli", "local")) == "cli:local"
    assert gateway._resolve_session_key(make_inbound("test", "t1")) == "test:t1"


def test_gateway_session_key_no_default_fallback(isolated_data):
    """无默认账号且未绑定 → 保持原渠道会话键（游客行为不变）。"""
    (isolated_data / "users.json").write_text(
        json.dumps({"users": USERS_DATA["users"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    gateway = make_gateway()
    assert gateway._resolve_session_key(make_inbound("qq", "ANYONE")) == "qq:ANYONE"


# ---------- 跨渠道会话共用（落盘验证） ----------

def test_qq_message_persists_to_web_session_file(isolated_data, tmp_path):
    """QQ 入站消息的会话键落盘到 web_han.jsonl —— 与 Web 端同一份历史文件。"""
    sessions_dir = tmp_path / "sessions"
    manager = SessionManager(sessions_dir=str(sessions_dir))

    gateway = make_gateway()
    # Web 端 han 先聊一句
    web_key = gateway._resolve_session_key(make_inbound("web", "han"))
    manager.save_message(web_key, {"role": "user", "content": "web 上查下订单"})

    # QQ 端同一个人（绑定到 han）接着聊
    set_channel_binding("qq:OPEN123", "han")
    qq_key = gateway._resolve_session_key(make_inbound("qq", "OPEN123"))
    assert qq_key == web_key == "web:han"
    manager.save_message(qq_key, {"role": "user", "content": "QQ 上追问物流"})

    # 两端消息写入同一文件，历史互通
    session_file = sessions_dir / "web_han.jsonl"
    assert session_file.exists()
    history = manager.get_history("web:han")
    contents = [m["content"] for m in history]
    assert "web 上查下订单" in contents
    assert "QQ 上追问物流" in contents
