# -*- coding: utf-8 -*-
"""生成 NanoClaw 电商智能客服项目演讲 PPT（16:9）。"""

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

# ---------- 主题色 ----------
NAVY   = RGBColor(0x16, 0x29, 0x4D)   # 深海军蓝（主色）
BLUE   = RGBColor(0x25, 0x63, 0xEB)   # 品牌蓝
ORANGE = RGBColor(0xF9, 0x73, 0x16)   # 强调橙
GREEN  = RGBColor(0x10, 0xB9, 0x81)   # 通过绿
RED    = RGBColor(0xE1, 0x4B, 0x4B)   # 风险红
PURPLE = RGBColor(0x7C, 0x5C, 0xFC)
GRAY_BG= RGBColor(0xF4, 0xF6, 0xFA)
GRAY_L = RGBColor(0xE5, 0xE9, 0xF0)
DARK   = RGBColor(0x1F, 0x29, 0x37)
MID    = RGBColor(0x5B, 0x66, 0x75)
WHITE  = RGBColor(0xFF, 0xFF, 0xFF)

FONT = "微软雅黑"

prs = Presentation()
prs.slide_width  = Inches(13.333)
prs.slide_height = Inches(7.5)
SW, SH = prs.slide_width, prs.slide_height
BLANK = prs.slide_layouts[6]


# ---------- 工具函数 ----------
def set_font(run, size=18, bold=False, color=DARK, name=FONT, italic=False):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = name
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn('a:ea'))
    if ea is None:
        ea = rPr.makeelement(qn('a:ea'), {})
        rPr.append(ea)
    ea.set('typeface', name)


def add_slide():
    return prs.slides.add_slide(BLANK)


def fill_bg(slide, color=WHITE):
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SW, SH)
    bg.fill.solid()
    bg.fill.fore_color.rgb = color
    bg.line.fill.background()
    bg.shadow.inherit = False
    return bg


def rect(slide, l, t, w, h, fill=None, line=None, line_w=1.0, shape=MSO_SHAPE.RECTANGLE,
         radius=None):
    sp = slide.shapes.add_shape(shape, l, t, w, h)
    if fill is None:
        sp.fill.background()
    else:
        sp.fill.solid()
        sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line
        sp.line.width = Pt(line_w)
    sp.shadow.inherit = False
    return sp


def txt(slide, l, t, w, h, lines, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
        wrap=True, space_after=4, line_spacing=1.05):
    """lines: str 或 [(text, size, bold, color), ...] 的列表（每项一段）。"""
    tb = slide.shapes.add_textbox(l, t, w, h)
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = Pt(2); tf.margin_right = Pt(2)
    tf.margin_top = Pt(1); tf.margin_bottom = Pt(1)
    if isinstance(lines, str):
        lines = [lines]
    for i, item in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        p.line_spacing = line_spacing
        if isinstance(item, str):
            item = [(item, 16, False, DARK)]
        elif isinstance(item, tuple):
            item = [item]
        for seg in item:
            text, size, bold, color = seg[0], seg[1], seg[2], seg[3]
            run = p.add_run()
            run.text = text
            set_font(run, size=size, bold=bold, color=color)
    return tb


def title_bar(slide, no, title, subtitle=None):
    """内容页统一标题栏。"""
    rect(slide, 0, 0, SW, Inches(1.15), fill=NAVY)
    rect(slide, 0, Inches(1.15), SW, Pt(3), fill=ORANGE)
    rect(slide, Inches(0.55), Inches(0.28), Inches(0.14), Inches(0.62), fill=ORANGE)
    txt(slide, Inches(0.85), Inches(0.18), Inches(10.2), Inches(0.8),
        [(title, 27, True, WHITE)], anchor=MSO_ANCHOR.MIDDLE)
    if subtitle:
        txt(slide, Inches(0.88), Inches(0.74), Inches(10.2), Inches(0.35),
            [(subtitle, 12, False, RGBColor(0xB9, 0xC6, 0xDE))])
    # 页码圆
    c = rect(slide, Inches(12.35), Inches(0.3), Inches(0.55), Inches(0.55),
             fill=ORANGE, shape=MSO_SHAPE.OVAL)
    txt(slide, Inches(12.35), Inches(0.3), Inches(0.55), Inches(0.55),
        [(f"{no:02d}", 15, True, WHITE)], align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE)


def bullet(slide, l, t, w, h, head, body, head_color=BLUE, gap=0.0):
    """色块小标题 + 说明。"""
    rect(slide, l, t, Inches(0.12), Inches(0.32), fill=head_color)
    txt(slide, l + Inches(0.22), t - Inches(0.04), w - Inches(0.22), Inches(0.4),
        [(head, 16, True, NAVY)])
    if body:
        txt(slide, l + Inches(0.22 + gap), t + Inches(0.36), w - Inches(0.22), h,
            [(body, 13, False, MID)], line_spacing=1.12)


def chip(slide, l, t, w, text, fill=BLUE, h=0.34, size=12, color=WHITE):
    rect(slide, l, t, w, Inches(h), fill=fill, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(slide, l, t, w, Inches(h), [(text, size, True, color)],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


# ============================================================
# P1 封面
# ============================================================
s = add_slide()
fill_bg(s, NAVY)
# 装饰圆
rect(s, Inches(10.4), Inches(-1.6), Inches(4.6), Inches(4.6),
     fill=RGBColor(0x1E, 0x3A, 0x66), shape=MSO_SHAPE.OVAL)
rect(s, Inches(11.6), Inches(4.9), Inches(3.4), Inches(3.4),
     fill=RGBColor(0x1E, 0x3A, 0x66), shape=MSO_SHAPE.OVAL)
rect(s, Inches(0.9), Inches(1.5), Inches(0.18), Inches(2.5), fill=ORANGE)
txt(s, Inches(1.35), Inches(1.35), Inches(10.5), Inches(1.6),
    [("NanoClaw 电商智能客服系统", 44, True, WHITE)])
txt(s, Inches(1.4), Inches(2.95), Inches(10.5), Inches(0.7),
    [("基于 LLM Function Calling 的多渠道智能客服 Agent —— 智能客服「小纳」", 20, False,
      RGBColor(0xBF, 0xD0, 0xEA))])
for i, (t_, c_) in enumerate([
        ("商品咨询推荐", BLUE), ("订单物流跟踪", GREEN),
        ("售后自动审核", ORANGE), ("人机协同接管", PURPLE)]):
    chip(s, Inches(1.4 + i * 2.35), Inches(4.15), Inches(2.1), t_, fill=c_, h=0.45, size=14)
rect(s, Inches(1.4), Inches(5.35), Inches(6.2), Pt(1.5),
     fill=RGBColor(0x3A, 0x52, 0x80))
txt(s, Inches(1.4), Inches(5.6), Inches(10), Inches(1.0),
    [("四人小组 · 课程项目汇报", 18, True, WHITE),
     ("Python + asyncio + FastAPI + WebSocket + OpenAI 兼容 API", 13, False,
      RGBColor(0x9F, 0xB4, 0xD4))])

# ============================================================
# P2 目录
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 1, "目录 CONTENTS")
items = [
    ("01", "项目背景与目标", "为什么做、要做成什么"),
    ("02", "系统总体架构", "多渠道 + 总线 + 网关 + Agent 分层"),
    ("03", "Agent 核心机制", "工具调用循环 / 双模式 / 防爆熔断"),
    ("04", "业务功能闭环", "售前 · 售中 · 售后 · 人工兜底"),
    ("05", "项目两大亮点", "自动退款决策 · 人机协同"),
    ("06", "工程化与部署", "认证 · 数据库 · 测试 · Docker"),
    ("07", "四人小组分工", "模块负责人与协作方式"),
]
for i, (no, h, d) in enumerate(items):
    col, row = i % 2, i // 2
    l = Inches(0.85 + col * 6.25)
    t = Inches(1.65 + row * 1.28)
    card = rect(s, l, t, Inches(5.85), Inches(1.05), fill=GRAY_BG,
                shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    rect(s, l, t, Inches(0.12), Inches(1.05),
         fill=ORANGE if i == 6 else BLUE)
    txt(s, l + Inches(0.32), t + Inches(0.12), Inches(1.2), Inches(0.8),
        [(no, 30, True, RGBColor(0xC3, 0xCE, 0xDE))], anchor=MSO_ANCHOR.MIDDLE)
    txt(s, l + Inches(1.55), t + Inches(0.13), Inches(4.1), Inches(0.85),
        [(h, 18, True, NAVY), (d, 12, False, MID)],
        anchor=MSO_ANCHOR.MIDDLE)

# ============================================================
# P3 背景与目标
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 2, "项目背景与目标", "传统电商客服的痛点 → 我们的解法")
# 左：痛点
rect(s, Inches(0.55), Inches(1.5), Inches(5.9), Inches(0.55), fill=RED,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(0.55), Inches(1.5), Inches(5.9), Inches(0.55),
    [("传统客服痛点", 18, True, WHITE)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
pains = [
    "人力成本高：重复咨询（物流/尺码/售后）占用大量坐席",
    "服务不连续：夜间与高峰期排队、响应慢",
    "多平台重复建设：网页、QQ、飞书各写一套客服逻辑",
    "售后审核繁琐：小额退款也要人工逐单审批",
    "缺乏记忆：顾客换渠道、隔天再来，客服不认识他",
]
txt(s, Inches(0.75), Inches(2.25), Inches(5.6), Inches(4),
    [[("✕  ", 14, True, RED), (p, 14, False, DARK)] for p in pains],
    line_spacing=1.25, space_after=12)
# 右：目标
rect(s, Inches(6.85), Inches(1.5), Inches(5.9), Inches(0.55), fill=GREEN,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(6.85), Inches(1.5), Inches(5.9), Inches(0.55),
    [("我们的目标：全流程可对话式客服 Agent", 18, True, WHITE)],
    align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
goals = [
    ("全业务闭环", "商品咨询→推荐下单→订单物流→退换售后→转人工"),
    ("多渠道统一接入", "CLI / Web / 飞书 / QQ 共用同一套 Agent 与记忆"),
    ("自动化 + 兜底", "小额退款秒级自动审核，复杂问题无缝转人工"),
    ("零依赖可演示", "本地模拟数据 + 离线规则模式，断网也能完整演示"),
    ("个性化记忆", "尺码/预算/偏好按账号长期保存，跨渠道跟随"),
]
yy = 2.25
for h, d in goals:
    txt(s, Inches(7.05), Inches(yy), Inches(5.6), Inches(0.75),
        [[("✓ ", 14, True, GREEN), (h + "：", 14, True, NAVY), (d, 13, False, MID)]])
    yy += 0.82

# ============================================================
# P4 系统总体架构（分层图）
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 3, "系统总体架构", "分层设计：渠道接入 → 消息总线 → 网关 → Agent → 能力/数据")

layers = [
    ("接入层  Channel", "CLI 终端    |    Web 用户端 / 商城页 / 客服端 / 管理端    |    飞书    |    QQ",
     RGBColor(0x3B, 0x82, 0xF6), 1.45),
    ("消息总线  MessageBus", "inbound / outbound 两条 asyncio 异步队列，渠道与 Agent 完全解耦",
     RGBColor(0x64, 0x74, 0x8B), 2.35),
    ("网关层  Gateway", "消息路由 · 按 session_key 的 LRU Agent 缓存 · 信号量限流(20) · 会话锁串行 · 超时保护 · 人工接管管理",
     RGBColor(0x7C, 0x5C, 0xFC), 3.25),
    ("Agent 层", "AgentLoop（LLM↔工具循环） / OfflineAgent（关键词规则） · ContextBuilder 人设记忆 · Token 压缩 · 防爆熔断",
     ORANGE, 4.15),
    ("能力层  ToolRegistry", "13 个电商业务工具   |   通用工具：文件 / Shell / 联网搜索 / 图片识图 / 子Agent   |   MCP 外部工具   |   LLM Provider",
     GREEN, 5.05),
    ("数据层", "商品/订单/物流/工单 JSON 模拟数据 · MySQL+Redis（生产） · JSONL 会话持久化 · JWT 认证 · RAG 向量库(预留)",
     RGBColor(0x0E, 0x7A, 0xA5), 5.95),
]
for name, desc, color, top in layers:
    rect(s, Inches(0.55), Inches(top), Inches(2.55), Inches(0.78),
         fill=color, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(s, Inches(0.55), Inches(top), Inches(2.55), Inches(0.78),
        [(name, 15, True, WHITE)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    rect(s, Inches(3.25), Inches(top), Inches(9.5), Inches(0.78), fill=GRAY_BG,
         line=GRAY_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(s, Inches(3.45), Inches(top), Inches(9.1), Inches(0.78),
        [(desc, 12.5, False, DARK)], anchor=MSO_ANCHOR.MIDDLE)
for y in [2.23, 3.13, 4.03, 4.93, 5.83]:
    rect(s, Inches(1.75), Inches(y), Inches(0.18), Inches(0.13),
         fill=RGBColor(0x9A, 0xA6, 0xB8), shape=MSO_SHAPE.DOWN_ARROW)

# ============================================================
# P5 消息流转链路
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 4, "核心消息流转链路", "一条顾客消息的完整旅程")
steps = [
    ("①", "渠道收消息", "CLI/Web/飞书/QQ\n封装 InboundMessage\n投递到 inbound 队列", BLUE),
    ("②", "网关分发", "解析 session_key\nLRU 取/建 Agent\n会话锁 + 限流 + 超时", PURPLE),
    ("③", "组装上下文", "人设 identity.md\n+时间+长期记忆\n+历史对话+当前问题", ORANGE),
    ("④", "LLM 工具循环", "模型返回 tool_calls\n→ 工具执行 → 结果回灌\n→ 再问模型，直到作答", GREEN),
    ("⑤", "回复出队", "OutboundMessage\n进入 outbound 队列\n按原渠道推送给顾客", RGBColor(0x0E, 0x7A, 0xA5)),
]
x = 0.55
for no, h, d, c in steps:
    rect(s, Inches(x), Inches(2.1), Inches(2.18), Inches(2.9), fill=GRAY_BG,
         line=GRAY_L, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    rect(s, Inches(x + 0.74), Inches(1.75), Inches(0.7), Inches(0.7), fill=c,
         shape=MSO_SHAPE.OVAL)
    txt(s, Inches(x + 0.74), Inches(1.75), Inches(0.7), Inches(0.7),
        [(no, 22, True, WHITE)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    txt(s, Inches(x + 0.1), Inches(2.65), Inches(1.98), Inches(0.5),
        [(h, 17, True, NAVY)], align=PP_ALIGN.CENTER)
    txt(s, Inches(x + 0.12), Inches(3.2), Inches(1.94), Inches(1.6),
        [(line, 12, False, MID) for line in d.split("\n")],
        align=PP_ALIGN.CENTER, line_spacing=1.2)
    if x < 11:
        rect(s, Inches(x + 2.18), Inches(3.35), Inches(0.34), Inches(0.32),
             fill=RGBColor(0xB4, 0xBE, 0xCC), shape=MSO_SHAPE.RIGHT_ARROW)
    x += 2.52
rect(s, Inches(0.55), Inches(5.45), Inches(12.2), Inches(1.35),
     fill=RGBColor(0xFE, 0xF3, 0xE7), line=ORANGE, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(0.85), Inches(5.6), Inches(11.7), Inches(1.1),
    [[("并发与容错设计：", 15, True, ORANGE),
      ("同一会话内严格串行（保证消息顺序），不同会话间最多 20 路并发；", 13.5, False, DARK)],
     [("单条消息 300 秒超时保护，单会话异常被捕获隔离，不影响其他顾客；Agent 实例 LRU 缓存上限 500，防止长期运行内存泄漏。",
       13.5, False, DARK)]], line_spacing=1.25)

# ============================================================
# P6 多渠道接入
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 5, "多渠道统一接入", "一套 Agent 内核，四种触达方式")
cards = [
    ("CLI 终端", "▸ 本地启动即可演示\n▸ 预绑定演示账号 han\n▸ 支持 /clear /tools 命令", BLUE),
    ("Web 网页", "▸ FastAPI + WebSocket\n▸ 流式打字机效果、工具进度\n▸ 登录/游客双态，可发图片识图", GREEN),
    ("飞书", "▸ 配置 app_id 自动启用\n▸ 渠道免登录绑定账号\n▸ 与 Web 端共享同一份记忆", ORANGE),
    ("QQ", "▸ 基于 botpy 框架接入\n▸ 同样免登录绑定\n▸ 三端会话历史互通", PURPLE),
]
for i, (h, d, c) in enumerate(cards):
    l = Inches(0.55 + i * 3.13)
    rect(s, l, Inches(1.6), Inches(2.9), Inches(2.6), fill=WHITE, line=GRAY_L,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    rect(s, l, Inches(1.6), Inches(2.9), Inches(0.7), fill=c,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    rect(s, l, Inches(2.05), Inches(2.9), Inches(0.25), fill=c)
    txt(s, l, Inches(1.6), Inches(2.9), Inches(0.7),
        [(h, 18, True, WHITE)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    txt(s, l + Inches(0.22), Inches(2.5), Inches(2.5), Inches(1.6),
        [(line, 13, False, DARK) for line in d.split("\n")], line_spacing=1.35)
rect(s, Inches(0.55), Inches(4.55), Inches(12.2), Inches(2.3), fill=GRAY_BG,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
bullet(s, Inches(0.85), Inches(4.8), Inches(11.6), Inches(0.6),
       "Channel 抽象基类统一接口", "所有渠道只需实现 start() / send() / stop() 三个方法，新平台接入只写一个适配器，核心代码零修改。",
       head_color=BLUE)
bullet(s, Inches(0.85), Inches(5.65), Inches(11.6), Inches(0.6),
       "跨渠道会话合并", "QQ / 飞书用户经绑定表解析出账号后，复用 web:{用户名} 会话键 —— 同一个人在网页、QQ、飞书三端共享对话历史与偏好记忆。",
       head_color=GREEN)

# ============================================================
# P7 Agent 核心：工具调用循环
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 6, "Agent 大脑：LLM ↔ 工具调用循环", "agent/loop.py —— 系统的核心引擎")
# 左侧循环
flow = [
    ("用户消息 + 上下文", BLUE),
    ("调用大模型（携带全部工具定义）", PURPLE),
    ("模型要求调用工具？", ORANGE),
    ("防爆检测 → ToolRegistry 执行工具", GREEN),
    ("工具结果作为 tool 消息回灌，再次请求模型", RGBColor(0x0E, 0x7A, 0xA5)),
]
for i, (t_, c) in enumerate(flow):
    top = 1.55 + i * 0.92
    rect(s, Inches(0.7), Inches(top), Inches(5.6), Inches(0.68), fill=c,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(s, Inches(0.85), Inches(top), Inches(5.3), Inches(0.68),
        [(t_, 14.5, True, WHITE)], anchor=MSO_ANCHOR.MIDDLE)
    if i < len(flow) - 1:
        rect(s, Inches(3.35), Inches(top + 0.66), Inches(0.22), Inches(0.26),
             fill=RGBColor(0x9A, 0xA6, 0xB8), shape=MSO_SHAPE.DOWN_ARROW)
# 回流箭头说明
txt(s, Inches(0.7), Inches(6.25), Inches(5.6), Inches(0.6),
    [[("↩ 工具结果回灌后循环；模型不再调用工具 → ", 12.5, False, MID),
      ("输出最终答复", 12.5, True, GREEN)]])
# 右侧三大保护
rect(s, Inches(6.7), Inches(1.55), Inches(6.05), Inches(0.6), fill=NAVY,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(6.7), Inches(1.55), Inches(6.05), Inches(0.6),
    [("三重保护机制，保障 Agent 稳定可靠", 16, True, WHITE)],
    align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
guards = [
    ("迭代上限", "最多 32 轮工具调用，防止模型失控空转", "max_iterations"),
    ("防爆熔断", "同一工具+相同参数：10 次警告、20 次强制熔断；30 次滑动窗口", "_check_tool_loop"),
    ("全局限流", "LLM 请求统一经过令牌限流器，高并发下自动排队，不打爆 API", "llm_limiter"),
    ("Token 压缩", "对话超过 16000 token 预算时自动摘要压缩历史，长对话不爆上下文", "MemoryConsolidator"),
]
yy = 2.35
for h, d, code in guards:
    rect(s, Inches(6.7), Inches(yy), Inches(6.05), Inches(0.95), fill=GRAY_BG,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(s, Inches(6.95), Inches(yy + 0.1), Inches(3.3), Inches(0.8),
        [[(h, 15, True, ORANGE)], [(d, 12, False, MID)]], line_spacing=1.15)
    chip(s, Inches(10.45), Inches(yy + 0.28), Inches(2.1), code,
         fill=RGBColor(0xD7, 0xDF, 0xEC), h=0.38, size=11, color=NAVY)
    yy += 1.08

# ============================================================
# P8 双模式设计
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 7, "在线 / 离线双模式设计", "有无 API Key 都能完整运行 —— 答辩演示零门槛")
# 在线
rect(s, Inches(0.55), Inches(1.55), Inches(5.95), Inches(4.7), fill=WHITE,
     line=BLUE, line_w=2, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
rect(s, Inches(0.55), Inches(1.55), Inches(5.95), Inches(0.75), fill=BLUE,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
rect(s, Inches(0.55), Inches(2.05), Inches(5.95), Inches(0.25), fill=BLUE)
txt(s, Inches(0.55), Inches(1.55), Inches(5.95), Inches(0.75),
    [("在线模式 · AgentLoop", 19, True, WHITE)], align=PP_ALIGN.CENTER,
    anchor=MSO_ANCHOR.MIDDLE)
online = [
    "大模型理解自然语言，自主选择并组合工具",
    "支持多轮模糊表达：「那双鞋有 40 码吗」",
    "流式输出：工具进度 + 打字机效果实时推送",
    "写操作自我约束：复述 → 用户确认 → 执行",
    "图片识图：视觉模型识别商品图后搜同款",
]
txt(s, Inches(0.9), Inches(2.55), Inches(5.4), Inches(3.5),
    [[("● ", 13, True, BLUE), (x, 14, False, DARK)] for x in online],
    line_spacing=1.3, space_after=10)
# 离线
rect(s, Inches(6.8), Inches(1.55), Inches(5.95), Inches(4.7), fill=WHITE,
     line=ORANGE, line_w=2, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
rect(s, Inches(6.8), Inches(1.55), Inches(5.95), Inches(0.75), fill=ORANGE,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
rect(s, Inches(6.8), Inches(2.05), Inches(5.95), Inches(0.25), fill=ORANGE)
txt(s, Inches(6.8), Inches(1.55), Inches(5.95), Inches(0.75),
    [("离线演示模式 · OfflineAgent", 19, True, WHITE)], align=PP_ALIGN.CENTER,
    anchor=MSO_ANCHOR.MIDDLE)
offline = [
    "无 API Key 自动进入，零成本、断网可演",
    "13 类正则关键词意图路由（订单号/SKU 自动识别）",
    "写操作同样遵循「复述 → 确认 → 执行」",
    "连续 2 轮无法理解 → 自动兜底转人工",
    "与在线模式接口完全一致（run / tools / clear_history）",
]
txt(s, Inches(7.15), Inches(2.55), Inches(5.4), Inches(3.5),
    [[("● ", 13, True, ORANGE), (x, 14, False, DARK)] for x in offline],
    line_spacing=1.3, space_after=10)
rect(s, Inches(2.4), Inches(6.45), Inches(8.5), Inches(0.62), fill=NAVY,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(2.4), Inches(6.45), Inches(8.5), Inches(0.62),
    [("Gateway 以鸭子类型调用，渠道 / 总线 / 工具 / 会话四层零修改复用", 14, True, WHITE)],
    align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# ============================================================
# P9 13 个电商业务工具
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 8, "工具体系：13 个电商业务工具", "每个业务能力 = 一个 Tool 类，模型像调用函数一样完成业务")
groups = [
    ("售前导购", BLUE, [
        ("search_goods", "按关键词/预算搜索在售商品"),
        ("get_goods_detail", "查实时价格、库存、尺码"),
    ]),
    ("账户与偏好", PURPLE, [
        ("get_my_profile", "查询顾客档案与历史偏好"),
        ("remember_preference", "长期记住尺码/预算/风格"),
    ]),
    ("交易履约", GREEN, [
        ("place_order", "确认后一键下单"),
        ("query_order", "查订单列表/单笔订单"),
        ("update_address", "未签收订单修改地址"),
        ("query_logistics", "查询物流轨迹"),
    ]),
    ("售后服务", ORANGE, [
        ("check_return_eligibility", "退货资格自动判定"),
        ("approve_refund", "退款审核：自动秒退/转人工"),
        ("create_ticket", "创建售后工单"),
        ("query_ticket_progress", "查询工单处理进度"),
        ("escalate_to_human", "兜底转人工并建工单"),
    ]),
]
x = 0.55
for gname, gc, tools_ in groups:
    w = 2.9 if len(tools_) <= 2 else 3.15
    rect(s, Inches(x), Inches(1.55), Inches(w), Inches(0.62), fill=gc,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(s, Inches(x), Inches(1.55), Inches(w), Inches(0.62),
        [(gname, 16, True, WHITE)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    yy = 2.35
    for name, desc in tools_:
        rect(s, Inches(x), Inches(yy), Inches(w), Inches(0.95 if gname == "售后服务" else 1.05),
             fill=GRAY_BG, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
        txt(s, Inches(x + 0.15), Inches(yy + 0.08), Inches(w - 0.3), Inches(0.9),
            [[("⚙ ", 11.5, True, gc), (name, 12.5, True, NAVY)],
             [(desc, 11, False, MID)]], line_spacing=1.1)
        yy += 1.08 if gname == "售后服务" else 1.18
    x += w + 0.22
rect(s, Inches(0.55), Inches(6.55), Inches(12.2), Inches(0.55),
     fill=RGBColor(0xFE, 0xF3, 0xE7), shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(0.8), Inches(6.55), Inches(11.8), Inches(0.55),
    [[("身份注入：", 13, True, ORANGE),
      ("工具构造时注入登录账号 actor；未登录游客访问个人数据一律拒绝，订单/偏好按账号严格隔离。另有文件、联网搜索、子 Agent、MCP 等通用工具。",
       12.5, False, DARK)]], anchor=MSO_ANCHOR.MIDDLE)

# ============================================================
# P10 业务闭环
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 9, "全链路业务闭环", "售前 → 售中 → 售后 → 人工兜底，覆盖电商客服完整旅程")
phases = [
    ("售前导购", BLUE, [
        "免登录浏览、搜索商品",
        "发送商品图片 → 识图找同款",
        "结合偏好档案个性化推荐 2~3 款",
        "确认后调用 place_order 下单",
    ]),
    ("售中服务", GREEN, [
        "查询订单列表与状态",
        "实时物流轨迹跟踪",
        "未签收订单可修改收货地址",
        "写操作先复述、确认后执行",
    ]),
    ("售后处理", ORANGE, [
        "退货资格判定（7 天无理由等规则）",
        "顾客确认 → 退款审核分流",
        "小额自动秒退 / 大额转人工",
        "工单创建与进度随时查询",
    ]),
    ("人工兜底", PURPLE, [
        "「转人工」关键词秒级直连",
        "不满情绪 / 连续未解决自动升级",
        "进入人工接管，AI 暂停回答",
        "超时无对话自动转回 AI",
    ]),
]
for i, (h, c, items) in enumerate(phases):
    l = Inches(0.55 + i * 3.13)
    rect(s, l, Inches(1.6), Inches(2.9), Inches(0.7), fill=c,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(s, l, Inches(1.6), Inches(2.9), Inches(0.7),
        [(h, 18, True, WHITE)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    rect(s, l, Inches(2.35), Inches(2.9), Inches(3.5), fill=GRAY_BG,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(s, l + Inches(0.2), Inches(2.55), Inches(2.55), Inches(3.2),
        [[("✓ ", 12.5, True, c), (it, 12.5, False, DARK)] for it in items],
        line_spacing=1.25, space_after=12)
    if i < 3:
        rect(s, l + Inches(2.86), Inches(3.85), Inches(0.3), Inches(0.3),
             fill=RGBColor(0xB4, 0xBE, 0xCC), shape=MSO_SHAPE.RIGHT_ARROW)
rect(s, Inches(0.55), Inches(6.1), Inches(12.2), Inches(0.85), fill=NAVY,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(0.85), Inches(6.1), Inches(11.7), Inches(0.85),
    [[("贯穿全程的安全与体验准则：", 14, True, ORANGE),
      ("一切以工具实时返回为准、绝不编造库存与政策 ｜ 订单号/手机号/地址脱敏展示 ｜ 不越权承诺价格与补偿 ｜ 偏好按账号长期记忆",
       13, False, WHITE)]], anchor=MSO_ANCHOR.MIDDLE)

# ============================================================
# P11 亮点一：退款自动审核决策
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 10, "亮点① 退款自动审核决策机制", "approve_refund + refund_policy：按订单状态智能分流")
branches = [
    ("已付款 · 未发货", BLUE,
     ["金额 ≤ 50 元：机器人自动审核，直接退款", "大额订单：自动建工单 → 转人工"]),
    ("已发货 · 在途", RGBColor(0x0E, 0x7A, 0xA5),
     ["小额：自动拦截物流 + 退款，顾客无需寄回", "大额：转人工审核处理"]),
    ("已签收 · 已完成", GREEN,
     ["7 天内 + 无理由 + 账号正常 → 自动秒退",
      "超过 7 天无理由期 → 拒绝并说明政策依据",
      "质量问题 / 大额 / 高风险 → 转人工跟进"]),
]
for i, (h, c, items) in enumerate(branches):
    l = Inches(0.55 + i * 4.18)
    rect(s, l, Inches(1.6), Inches(3.9), Inches(0.75), fill=c,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(s, l, Inches(1.6), Inches(3.9), Inches(0.75),
        [(h, 17, True, WHITE)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    rect(s, l, Inches(2.4), Inches(3.9), Inches(2.25), fill=GRAY_BG,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    txt(s, l + Inches(0.25), Inches(2.6), Inches(3.45), Inches(2),
        [[("• ", 13, True, c), (it, 13.5, False, DARK)] for it in items],
        line_spacing=1.3, space_after=10)
rect(s, Inches(0.55), Inches(4.95), Inches(12.2), Inches(1.9), fill=WHITE,
     line=ORANGE, line_w=1.5, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(0.9), Inches(5.12), Inches(11.6), Inches(1.7),
    [[("闭环联动：", 15, True, ORANGE),
      ("每次决策都写入退款记录与审计日志（refund_records / refund_audit_log），可追溯；", 13.5, False, DARK)],
     [("转人工分支的回复含「已提交人工审核」，", 13.5, False, DARK),
      ("Gateway 检测到后自动进入人工接管模式，客服端弹窗 + 声音 + 红色闪烁提醒；", 13.5, True, RED)],
     [("所有资金类写操作，模型必须先向顾客复述订单号与金额、收到「确认」后才执行 —— 安全可审计。",
       13.5, False, DARK)]], line_spacing=1.3)

# ============================================================
# P12 亮点二：人机协同
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 11, "亮点② AI × 人工客服协同接管", "HumanTakeoverManager：机器人与真人无缝切换")
left_items = [
    ("秒级直连转人工", "「转人工/我要投诉」等关键词命中正则，跳过 LLM 直接建工单进入接管，响应零等待"),
    ("接管期 AI 静默", "人工接管期间顾客消息只存历史、不再由 AI 回答，避免人机抢答干扰"),
    ("AI 化身坐席助手", "AI 基于最近 8 轮对话后台生成推荐话术，仅显示在客服端，人工可一键采用"),
    ("自动回归 AI", "双方 300 秒无对话，自动结束人工服务并切回智能客服，附礼貌提示"),
]
yy = 1.6
for h, d in left_items:
    rect(s, Inches(0.55), Inches(yy), Inches(7.3), Inches(1.12), fill=GRAY_BG,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    rect(s, Inches(0.55), Inches(yy), Inches(0.12), Inches(1.12), fill=PURPLE)
    txt(s, Inches(0.85), Inches(yy + 0.12), Inches(6.85), Inches(0.95),
        [[(h, 15.5, True, NAVY)], [(d, 12.5, False, MID)]], line_spacing=1.15)
    yy += 1.26
# 右侧：客服端能力
rect(s, Inches(8.15), Inches(1.6), Inches(4.6), Inches(4.6), fill=NAVY,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(8.15), Inches(1.85), Inches(4.6), Inches(0.5),
    [("人工客服工作台能力", 17, True, WHITE)], align=PP_ALIGN.CENTER)
feats = ["会话列表 + 未读消息红点", "顾客最后消息/时间实时展示",
         "一键接管 / 退出接管", "AI 推荐话术一键采用发送",
         "接管请求弹窗 + 声音 + 闪烁标签", "活跃接管会话全局监控"]
txt(s, Inches(8.5), Inches(2.55), Inches(4), Inches(3.4),
    [[("✦ ", 13, True, ORANGE), (f, 13.5, False, WHITE)] for f in feats],
    line_spacing=1.4, space_after=10)
txt(s, Inches(0.55), Inches(6.5), Inches(12.2), Inches(0.5),
    [[("设计理念：", 13.5, True, ORANGE),
      ("AI 处理 80% 重复性咨询，人工聚焦复杂与情绪场景 —— 降本增效，同时不让顾客流失在机器人处。",
       13.5, False, DARK)]])

# ============================================================
# P13 Web 四端
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 12, "Web 端：面向三类角色的四个页面", "FastAPI 路由 + WebSocket 实时通信")
pages = [
    ("/  用户端", "index.html", BLUE,
     ["账号登录 / 游客浏览", "流式对话、工具进度提示", "发送图片，识图推荐同款", "转人工状态实时切换"]),
    ("/mall  商城页", "mall.html", GREEN,
     ["商品陈列与分类筛选", "商品卡片实时价格", "对接 /api/goods 数据", "跳转客服咨询"]),
    ("/agent  客服端", "agent.html", ORANGE,
     ["全部会话列表与未读", "接管/回复顾客", "查看 AI 推荐话术", "活跃人工接管监控"]),
    ("/admin  管理端", "admin.html", PURPLE,
     ["会话量/工单量数据看板", "自动退款统计与趋势", "VOC 顾客之声分析", "服务质量指标监控"]),
]
for i, (h, f, c, items) in enumerate(pages):
    col, row = i % 2, i // 2
    l = Inches(0.7 + col * 6.2)
    t = Inches(1.55 + row * 2.62)
    rect(s, l, t, Inches(5.85), Inches(2.35), fill=WHITE, line=GRAY_L,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    rect(s, l, t, Inches(0.18), Inches(2.35), fill=c)
    txt(s, l + Inches(0.4), t + Inches(0.15), Inches(4), Inches(0.5),
        [(h, 18, True, NAVY)])
    chip(s, l + Inches(3.9), t + Inches(0.18), Inches(1.75), f,
         fill=GRAY_BG, h=0.38, size=11, color=NAVY)
    txt(s, l + Inches(0.42), t + Inches(0.78), Inches(5.2), Inches(1.5),
        [[("• ", 12.5, True, c), (x, 12.5, False, DARK)] for x in items],
        line_spacing=1.22, space_after=4)

# ============================================================
# P14 数据层与安全
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 13, "数据层与安全认证", "演示自包含，生产可切换 —— 同一套接口")
blocks = [
    ("双数据后端", BLUE,
     ["DATA_BACKEND=json：本地 JSON 自包含演示",
      "DATA_BACKEND=mysql：SQLAlchemy 异步 + 连接池",
      "一键迁移脚本 JSON → MySQL，启动连通性预检"]),
    ("缓存与会话", GREEN,
     ["Redis 会话缓存（redis_manager）",
      "JSONL 按会话落盘，重启自动恢复历史",
      "跨渠道同账号共享会话与记忆"]),
    ("安全体系", RED,
     ["密码哈希存储、JWT 登录令牌签发校验",
      "admin 角色接口鉴权、CORS 配置",
      "游客数据隔离、Shell 工具环境变量门控"]),
    ("RAG 知识库", PURPLE,
     ["Chroma 向量库 + SentenceTransformers",
      "FAQ / 售后条款语义检索（惰性导入）",
      "依赖未装不报错，数据就绪即插即用"]),
]
for i, (h, c, items) in enumerate(blocks):
    col, row = i % 2, i // 2
    l = Inches(0.7 + col * 6.2)
    t = Inches(1.6 + row * 2.45)
    rect(s, l, t, Inches(5.85), Inches(2.2), fill=GRAY_BG,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    rect(s, l, t, Inches(5.85), Inches(0.6), fill=c,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    rect(s, l, t + Inches(0.35), Inches(5.85), Inches(0.25), fill=c)
    txt(s, l, t, Inches(5.85), Inches(0.6),
        [(h, 16.5, True, WHITE)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    txt(s, l + Inches(0.35), t + Inches(0.78), Inches(5.2), Inches(1.3),
        [[("✓ ", 12.5, True, c), (x, 12.5, False, DARK)] for x in items],
        line_spacing=1.25, space_after=5)

# ============================================================
# P15 工程化与部署
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 14, "工程化与生产部署", "不止能跑通 Demo：16 个测试文件 + 容器化部署")
eng = [
    ("异步高并发", "asyncio 全链路；信号量限流、会话锁、超时、错误隔离、LRU 缓存", BLUE),
    ("自动化测试", "pytest 共 16 个测试文件：网关路由、登录鉴权、退款策略、限流、视觉搜索、CORS、MySQL 兼容", GREEN),
    ("容器化部署", "Dockerfile + docker-compose（含 MySQL 服务）+ Nginx 反向代理配置", ORANGE),
    ("配置与可观测", "环境变量/配置文件分离（.env.example）、结构化日志、令牌限流、生产就绪指南文档", PURPLE),
]
for i, (h, d, c) in enumerate(eng):
    col, row = i % 2, i // 2
    l = Inches(0.7 + col * 6.2)
    t = Inches(1.65 + row * 1.75)
    rect(s, l, t, Inches(5.85), Inches(1.5), fill=WHITE, line=GRAY_L,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    rect(s, l, t, Inches(0.14), Inches(1.5), fill=c)
    txt(s, l + Inches(0.4), t + Inches(0.18), Inches(5.2), Inches(1.2),
        [[(h, 17, True, NAVY)], [(d, 13, False, MID)]], line_spacing=1.25)
rect(s, Inches(0.7), Inches(5.35), Inches(11.9), Inches(1.4), fill=NAVY,
     shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(1.05), Inches(5.55), Inches(11.2), Inches(1.05),
    [("技术栈一览", 16, True, ORANGE),
     ("Python 3 · asyncio · FastAPI · WebSocket · OpenAI 兼容 API（Function Calling / 流式 / 视觉）",
      13.5, False, WHITE),
     ("SQLAlchemy + MySQL · Redis · Chroma · pytest · Docker · Nginx · 飞书/QQ 机器人 SDK",
      13.5, False, WHITE)], line_spacing=1.35)

# ============================================================
# P16 四人小组分工
# ============================================================
s = add_slide()
fill_bg(s)
title_bar(s, 15, "四人小组分工", "按架构层划分模块，接口先行，并行开发")
rows = [
    ("成员 A\n（组长）", "架构与接入层", BLUE,
     "总体架构设计；main.py 组装启动；Gateway 网关（路由/缓存/并发控制）；MessageBus 消息总线；CLI / 飞书 / QQ 渠道接入",
     "main.py · gateway.py · bus/ · channels/"),
    ("成员 B", "Agent 内核", PURPLE,
     "AgentLoop 工具调用循环；防爆熔断与限流；Token 压缩；ContextBuilder 上下文；离线规则模式；LLM Provider 与视觉",
     "agent/loop.py · offline_agent.py · context.py · memory.py · providers/"),
    ("成员 C", "业务工具与协同", ORANGE,
     "13 个电商业务工具；退款政策决策树；订单/物流/工单数据逻辑；人工接管与 AI 推荐话术；Web 四个前端页面",
     "agent/tools/ecom/ · channels/web_ui/ · identity.md"),
    ("成员 D", "数据安全与工程化", GREEN,
     "JWT 认证与密码安全；MySQL/Redis 数据层与迁移脚本；RAG 知识库；pytest 测试；Docker/Nginx 部署与文档",
     "security/ · db/ · migrations/ · rag/ · tests/ · Dockerfile"),
]
# 表头
heads = [("成员", 0.7, 1.35), ("负责板块", 2.05, 2.3),
         ("主要工作", 4.35, 5.5), ("对应代码", 9.85, 2.9)]
for h, l, w in heads:
    rect(s, Inches(l), Inches(1.5), Inches(w), Inches(0.55), fill=NAVY)
    txt(s, Inches(l), Inches(1.5), Inches(w), Inches(0.55),
        [(h, 14, True, WHITE)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
yy = 2.05
for who, plate, c, work, code in rows:
    rh = 1.12
    rect(s, Inches(0.7), Inches(yy), Inches(12.05), Inches(rh), fill=GRAY_BG,
         line=GRAY_L)
    rect(s, Inches(0.7), Inches(yy), Inches(0.1), Inches(rh), fill=c)
    txt(s, Inches(0.75), Inches(yy), Inches(1.3), Inches(rh),
        [[(line, 14, True, c)] for line in who.split("\n")],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    txt(s, Inches(2.1), Inches(yy), Inches(2.2), Inches(rh),
        [(plate, 13.5, True, NAVY)], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    txt(s, Inches(4.5), Inches(yy + 0.08), Inches(5.25), Inches(rh - 0.1),
        [(work, 11, False, DARK)], anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.12)
    txt(s, Inches(9.95), Inches(yy + 0.08), Inches(2.7), Inches(rh - 0.1),
        [(code, 10.5, False, MID)], anchor=MSO_ANCHOR.MIDDLE, line_spacing=1.15)
    yy += rh + 0.06
rect(s, Inches(0.7), Inches(6.75), Inches(12.05), Inches(0.5),
     fill=RGBColor(0xFE, 0xF3, 0xE7), shape=MSO_SHAPE.ROUNDED_RECTANGLE)
txt(s, Inches(0.7), Inches(6.75), Inches(12.05), Inches(0.5),
    [[("协作方式：", 12.5, True, ORANGE),
      ("先约定 Tool 基类与 Channel 接口契约 → 四人并行开发 → 联调全链路 → 共同准备演示脚本与答辩", 12, False, DARK)]],
    align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# ============================================================
# P17 总结
# ============================================================
s = add_slide()
fill_bg(s, NAVY)
rect(s, Inches(10.6), Inches(4.6), Inches(4.2), Inches(4.2),
     fill=RGBColor(0x1E, 0x3A, 0x66), shape=MSO_SHAPE.OVAL)
txt(s, Inches(0.9), Inches(0.85), Inches(11), Inches(0.9),
    [("项目总结", 36, True, WHITE)])
rect(s, Inches(0.95), Inches(1.75), Inches(2.2), Pt(3), fill=ORANGE)
sums = [
    ("一套代码，双态运行", "在线 LLM 智能对话 + 离线规则演示，断网可答辩、零 token 成本"),
    ("一条完整业务闭环", "售前导购 → 下单支付 → 订单物流 → 退换售后 → 人工兜底"),
    ("两个技术亮点", "退款自动审核决策树；AI 与人工客服无缝协同接管 + 话术辅助"),
    ("一份工程化答卷", "多渠道统一、JWT 安全、MySQL/Redis、16 项测试、Docker 部署"),
]
yy = 2.2
for h, d in sums:
    rect(s, Inches(0.95), Inches(yy + 0.05), Inches(0.22), Inches(0.7), fill=ORANGE)
    txt(s, Inches(1.4), Inches(yy), Inches(10.8), Inches(0.9),
        [[(h, 19, True, WHITE)], [(d, 14, False, RGBColor(0xBF, 0xD0, 0xEA))]],
        line_spacing=1.2)
    yy += 1.05
txt(s, Inches(0.95), Inches(6.55), Inches(11), Inches(0.7),
    [("谢谢观看！欢迎提问与指导  ·  NanoClaw 智能客服「小纳」", 18, True, WHITE)])

# ---------- 保存 ----------
out = r"c:\Users\橙子才是唯一水果\Desktop\项目2\v4\NanoClaw电商智能客服_演讲PPT.pptx"
prs.save(out)
print("OK:", out, "slides =", len(prs.slides._sldIdLst))
