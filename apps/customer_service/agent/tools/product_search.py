"""
电商商品页搜索工具模块

数据源与策略（按顺序回退）：
1. 主源：直接请求 cn.bing.com（国内可直连），解析搜索结果后用正则白名单
   只保留主流电商平台商品页，知乎、百科、资讯等一律过滤。
2. 次源：
   - 必应括号 site: 定向（京东专用）：查询形如「墨镜 (site:jd.com)」。
     括号形式的 site: 在必应中国有效（不带括号会被忽略）。京东品类页
     （jd.com/chanpin/xxx.html）的搜索摘要带有热销商品名/型号/评价数等
     快照信息（如「帕森墨镜 PZ9913 已有100000人评价」），适合市场行情类
     问题；但价格/销量信息来自必应快照，零散且可能滞后。
   - 360 搜索（www.so.com）+ site: 定向：360 对京东/天猫/淘宝/拼多多
     收录较好，真实链接藏在结果的 data-mdurl 属性中，适合具体商品查询。
3. 兜底：苏宁易购站内搜索（SSR 直出商品链接，无反爬）。

支持 platform 参数直达指定平台（jd / tmall / taobao / pdd / suning），
默认 auto 按上述顺序回退。

背景：当前网络环境下 DuckDuckGo 完全不可达（web_search 一直超时），
cn.bing.com 可直连，商品类联网查询走本工具。
"""

import base64
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from agent.tools.base import Tool


class ProductSearchTool(Tool):
    """
    电商商品页搜索工具

    三级数据源回退（必应 → 360 site: 定向 → 苏宁站内），商品页白名单过滤，
    支持 platform 参数指定平台：
    - jd      京东（360 site:item.jd.com）
    - tmall   天猫（360 site:detail.tmall.com）
    - taobao  淘宝（360 site:item.taobao.com）
    - pdd     拼多多（360 site:mobile.yangkeduo.com）
    - suning  苏宁（站内搜索）
    - auto    自动回退（默认）
    """

    MAX_OUTPUT_LENGTH = 8000

    # 单个数据源请求超时（秒）
    TIMEOUT = 15

    # 必应每页抓取的搜索结果条数（多抓一些，过滤后仍有剩余）
    FETCH_COUNT = 20

    # 常见浏览器 User-Agent
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # 商品页 URL 白名单（正则）：只保留这些电商域名且带商品 ID 的链接
    # （京东 ID 在路径 /xxx.html；天猫/淘宝 ID 在 query id=；拼多多 goods_id=；
    #   苏宁为 /店铺/商品.html；国美/唯品会/华为商城按域名匹配）
    # 京东品类页（chanpin）/频道页（hprm）单独放行：页面本身是热销商品列表，
    # 必应快照摘要带有商品名/型号/评价数等汇总信息
    PRODUCT_URL_RE = re.compile(
        r"^https?://("
        r"item\.jd\.com/\d+\.html"                              # 京东商品详情页
        r"|item\.m\.jd\.com/(?:product/\d+\.html|ware/view\.action\?[^#]*wareId=\d+)"  # 京东移动端
        r"|www\.jd\.com/chanpin/\d+\.html"                      # 京东品类页（热销榜快照）
        r"|www\.jd\.com/hprm/[0-9a-f]+\.html"                   # 京东频道页
        r"|detail\.tmall\.com/item\.htm\?[^#]*\bid=\d+"         # 天猫商品页
        r"|item\.taobao\.com/item\.htm\?[^#]*\bid=\d+"          # 淘宝商品页
        r"|mobile\.yangkeduo\.com/goods\.html\?[^#]*goods_id=\d+"  # 拼多多商品页
        r"|product\.suning\.com/\d+/\d+\.html"                  # 苏宁商品页
        r"|item\.gome\.com\.cn/"                                # 国美商品页
        r"|detail\.vip\.com/"                                   # 唯品会商品页
        r"|item\.vmall\.com/"                                   # 华为商城商品页
        r")",
        re.IGNORECASE,
    )

    # platform 参数 → 360 site: 定向域名
    PLATFORM_SITES = {
        "jd": "item.jd.com",
        "tmall": "detail.tmall.com",
        "taobao": "item.taobao.com",
        "pdd": "mobile.yangkeduo.com",
    }

    # auto 模式下 360 并行定向的平台顺序
    AUTO_SITES = ["item.jd.com", "detail.tmall.com", "item.taobao.com"]

    @property
    def name(self) -> str:
        return "web_product_search"

    @property
    def description(self) -> str:
        return (
            "联网搜索电商商品页，返回商品页链接（含标题/摘要），自动过滤知乎、"
            "百科等干扰内容。当需要查询商品的市场价、外部在售渠道、型号对比时使用。"
            "platform 可选：jd=京东 / tmall=天猫 / taobao=淘宝 / pdd=拼多多 / "
            "suning=苏宁 / auto=自动（默认）。普通网页/资讯搜索请用 web_search。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "商品搜索关键词，建议带上品牌/型号，如「索尼 WH-1000XM5 耳机」",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回几条商品结果",
                    "default": 5,
                },
                "platform": {
                    "type": "string",
                    "enum": ["auto", "jd", "tmall", "taobao", "pdd", "suning"],
                    "description": "限定电商平台，默认 auto（自动选择有结果的平台）",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """
        执行商品页搜索。

        Args:
            query: 搜索关键词
            max_results: 最多返回几条商品结果，默认 5
            platform: 限定平台（jd/tmall/taobao/pdd/suning），默认 auto

        Returns:
            格式化的商品页搜索结果字符串
        """
        query = kwargs.get("query")
        max_results = kwargs.get("max_results", 5)
        platform = kwargs.get("platform", "auto")

        if not query:
            return "错误：搜索关键词不能为空"

        products: list[dict] = []
        source = ""

        if platform == "suning":
            # 直达苏宁站内
            try:
                products = await self._search_suning(query)
                if products:
                    source = "苏宁站内搜索"
            except Exception as e:
                print(f"警告: 苏宁商品搜索失败 - {e}")
        elif platform == "jd":
            # 京东：必应 (site:jd.com) 品类页快照（热销/评价数）→ 360 商品详情页
            try:
                products = await self._search_bing_site(query, "jd.com")
                if products:
                    source = "必应搜索 (site:jd.com)"
            except Exception as e:
                print(f"警告: 必应 site: 定向搜索失败 - {e}")
            if not products:
                try:
                    products = await self._search_360_site(
                        query, self.PLATFORM_SITES["jd"]
                    )
                    if products:
                        source = f"360搜索 site:{self.PLATFORM_SITES['jd']}"
                except Exception as e:
                    print(f"警告: 360 定向搜索失败 - {e}")
            if not products:
                try:
                    products = await self._search_suning(query)
                    if products:
                        source = "苏宁站内搜索"
                except Exception as e:
                    print(f"警告: 苏宁商品搜索失败 - {e}")
        elif platform in self.PLATFORM_SITES:
            # 直达指定平台：360 site: 定向搜索
            try:
                products = await self._search_360_site(
                    query, self.PLATFORM_SITES[platform]
                )
                if products:
                    source = f"360搜索 site:{self.PLATFORM_SITES[platform]}"
            except Exception as e:
                print(f"警告: 360 定向搜索失败 - {e}")
        else:
            # auto：必应普通搜索 → [必应 (site:jd.com) + 360 多平台] 并行合并 → 苏宁
            try:
                products = await self._search_bing(query)
                if products:
                    source = "必应搜索 cn.bing.com"
            except Exception as e:
                print(f"警告: 必应商品搜索失败 - {e}")

            if not products:
                try:
                    import asyncio

                    bing_site, multi_360 = await asyncio.gather(
                        self._search_bing_site(query, "jd.com"),
                        self._search_360_multi(query, self.AUTO_SITES),
                        return_exceptions=True,
                    )
                    merged, seen = [], set()
                    # 360 具体商品页在前，必应品类页快照在后（补充行情信息）
                    for batch in (multi_360, bing_site):
                        if isinstance(batch, Exception) or not batch:
                            continue
                        for item in batch:
                            if item["url"] not in seen:
                                seen.add(item["url"])
                                merged.append(item)
                    if merged:
                        products = merged
                        source = "360 site: 定向 + 必应 (site:jd.com)"
                except Exception as e:
                    print(f"警告: 定向搜索失败 - {e}")

            if not products:
                try:
                    products = await self._search_suning(query)
                    if products:
                        source = "苏宁站内搜索"
                except Exception as e:
                    print(f"警告: 苏宁商品搜索失败 - {e}")

        if not products:
            return (
                "未找到电商商品页结果。建议换更具体的品牌/型号关键词重试，"
                "或改用 web_search 做普通网页搜索。"
            )

        # ---- 格式化输出 ----
        output_parts = []
        for index, item in enumerate(products[:max_results], 1):
            # 品类页标注：提示这是热销列表页而非具体商品
            is_category = "chanpin" in item["url"] or "hprm" in item["url"]
            tag = "【京东品类页·热销快照】" if is_category else ""
            output_parts.append(
                f"### {index}. {tag}{item['title']}\n"
                f"链接: {item['url']}\n"
                f"{item['snippet']}\n"
            )
        output = "".join(output_parts)
        output += f"（来源：{source}，已按商品页白名单过滤非商品内容）"

        # 品类页快照说明：摘要里的价格/销量/评价数来自必应抓取的页面快照，
        # 并非实时数据，提醒 LLM 谨慎引用
        if any("chanpin" in p["url"] or "hprm" in p["url"] for p in products[:max_results]):
            output += (
                "\n注：标注「品类页·热销快照」的结果，其摘要中的价格/销量/评价数"
                "来自必应搜索快照，可能滞后或不完整，仅供参考，建议以商品页实际信息为准。"
            )

        if len(output) > self.MAX_OUTPUT_LENGTH:
            output = output[: self.MAX_OUTPUT_LENGTH]
        return output

    # ------------------------------------------------------------------
    # 数据源 1：必应搜索（主源）
    # ------------------------------------------------------------------

    async def _search_bing(self, query: str) -> list[dict]:
        """
        请求 cn.bing.com 并按商品页白名单过滤结果。

        Returns:
            [{"title": ..., "url": ..., "snippet": ...}, ...] 仅有商品页
        """
        import httpx

        search_url = "https://cn.bing.com/search?" + urlencode({
            "q": query,
            "count": self.FETCH_COUNT,
            "mkt": "zh-CN",
        })

        async with httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        ) as client:
            response = await client.get(search_url)

        if response.status_code != 200:
            return []

        products, seen = [], set()
        for item in self._parse_bing_results(response.text):
            url = item["url"]
            if self.PRODUCT_URL_RE.match(url) and url not in seen:
                seen.add(url)
                products.append(item)
        return products

    def _parse_bing_results(self, html: str) -> list[dict]:
        """
        解析必应搜索结果页 HTML。

        结果块以 <li class="b_algo"> 标记，每块内取 <h2> 中首个 <a> 的
        href 与标题，以及首个 <p> 作为摘要。

        Args:
            html: 必应搜索结果页 HTML

        Returns:
            [{"title": ..., "url": ..., "snippet": ...}, ...] 按排名顺序
        """
        results = []
        # 按结果块切分（每块以 <li ... class="...b_algo..." 开始）
        chunks = re.split(r'<li[^>]+class="[^"]*b_algo', html)
        for chunk in chunks[1:]:
            h2 = re.search(r"<h2[^>]*>(.*?)</h2>", chunk, re.DOTALL)
            if not h2:
                continue
            anchor = re.search(
                r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', h2.group(1), re.DOTALL
            )
            if not anchor:
                continue

            raw_href = unescape(anchor.group(1))
            url = self._decode_bing_url(raw_href)
            title = re.sub(r"<[^>]+>", "", anchor.group(2)).strip()

            snippet = ""
            p = re.search(r"<p[^>]*>(.*?)</p>", chunk, re.DOTALL)
            if p:
                snippet = re.sub(r"<[^>]+>", "", p.group(1)).strip()

            if title and url:
                results.append({"title": title, "url": url, "snippet": snippet})
        return results

    def _decode_bing_url(self, href: str) -> str:
        """
        还原 Bing 跳转链接中的真实 URL。

        必应部分结果链接形如：
            https://www.bing.com/ck/a?!&&p=...&u=a1aHR0cHM6Ly9pdGVt...
        其中 u 参数去掉前缀 a1 后为 URL-safe Base64 编码的真实地址。

        Args:
            href: 结果页上的原始链接

        Returns:
            str: 真实 URL；解码失败时原样返回
        """
        if "bing.com/ck/" not in href:
            return href
        try:
            qs = parse_qs(urlparse(unquote(href)).query)
            u = qs.get("u", [""])[0]
            if u.startswith("a1"):
                u = u[2:]
            u += "=" * (-len(u) % 4)  # 补齐 Base64 padding
            decoded = base64.urlsafe_b64decode(u).decode("utf-8", errors="ignore")
            return decoded if decoded.startswith("http") else href
        except Exception:
            return href

    # ------------------------------------------------------------------
    # 数据源 2：必应括号 site: 定向（京东品类页快照）
    # ------------------------------------------------------------------

    async def _search_bing_site(self, query: str, site: str) -> list[dict]:
        """
        必应 + 括号 site: 定向搜索，如「墨镜 (site:jd.com)」。

        括号形式的 site: 在必应中国有效（不带括号会被非浏览器请求忽略）。
        京东品类页（jd.com/chanpin/）的摘要带有热销商品名/型号/评价数等
        快照信息，适合市场行情类问题；信息来自必应快照，零散且可能滞后。

        Args:
            query: 搜索关键词
            site: 平台域名，如 "jd.com"

        Returns:
            [{"title": ..., "url": ..., "snippet": ...}, ...] 白名单过滤后
        """
        import httpx

        search_url = (
            "https://cn.bing.com/search?q="
            + quote(f"{query} (site:{site})", safe=":")
        )
        async with httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        ) as client:
            response = await client.get(search_url)

        if response.status_code != 200:
            return []

        products, seen = [], set()
        for item in self._parse_bing_results(response.text):
            url = self._decode_bing_url(item["url"])
            # 双重校验：白名单 + 域名匹配（防 site: 失效时跨平台串结果）
            if site in url and self.PRODUCT_URL_RE.match(url) and url not in seen:
                seen.add(url)
                products.append({**item, "url": url})
        return products

    # ------------------------------------------------------------------
    # 数据源 3：360 搜索 site: 定向（京东/天猫/淘宝/拼多多）
    # ------------------------------------------------------------------

    async def _search_360_multi(
        self, query: str, sites: list[str]
    ) -> list[dict]:
        """
        多平台 site: 定向并行搜索并合并结果。

        Args:
            query: 搜索关键词
            sites: 站点域名列表，如 ["item.jd.com", "detail.tmall.com"]

        Returns:
            合并去重后的商品结果（按平台顺序拼接）
        """
        import asyncio

        batches = await asyncio.gather(
            *[self._search_360_site(query, s) for s in sites],
            return_exceptions=True,
        )
        merged, seen = [], set()
        for batch in batches:
            if isinstance(batch, Exception):
                continue
            for item in batch:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    merged.append(item)
        return merged

    async def _search_360_site(self, query: str, site: str) -> list[dict]:
        """
        360 搜索 site: 定向搜索单个平台。

        360 对国内电商收录好且 site: 操作符有效；结果真实 URL 藏在
        锚标签 data-mdurl 属性中（href 为 so.com 跳转链）。

        Args:
            query: 搜索关键词
            site: 平台商品页域名，如 "item.jd.com"

        Returns:
            [{"title": ..., "url": ..., "snippet": ...}, ...]
        """
        search_url = (
            "https://www.so.com/s?q="
            + quote(f"site:{site} {query}", safe=": ")
        )
        html = await self._impersonated_get(
            search_url, referer="https://www.so.com/"
        )
        if not html:
            return []

        products, seen = [], set()
        for item in self._parse_360_results(html):
            url = self._clean_product_url(item["url"])
            if self.PRODUCT_URL_RE.match(url) and url not in seen:
                seen.add(url)
                products.append({**item, "url": url})
        return products

    def _parse_360_results(self, html: str) -> list[dict]:
        """
        解析 360 搜索结果页 HTML。

        结果块以 <li class="res-list"> 标记；标题锚标签的 data-mdurl
        属性为真实 URL（href 是跳转链），摘要取 res-desc 段落。

        Args:
            html: 360 搜索结果页 HTML

        Returns:
            [{"title": ..., "url": ..., "snippet": ...}, ...]
        """
        results = []
        for block in re.split(r'<li[^>]+class="[^"]*res-list', html)[1:]:
            anchor = re.search(
                r'<a[^>]*data-mdurl="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL
            )
            if not anchor:
                continue
            url = unescape(anchor.group(1))
            title = re.sub(r"<[^>]+>", "", anchor.group(2)).strip()

            snippet = ""
            p = re.search(
                r'<p[^>]+class="[^"]*res-desc[^"]*"[^>]*>(.*?)</p>',
                block, re.DOTALL,
            )
            if p:
                snippet = re.sub(
                    r"<[^>]+>", "", p.group(1).replace("&nbsp;", " ")
                ).strip()

            if title and url:
                results.append({"title": title, "url": url, "snippet": snippet})
        return results

    @staticmethod
    def _clean_product_url(url: str) -> str:
        """
        清理商品 URL 中的跟踪参数。

        京东商品页 ID 在路径中，?cu=true 等跟踪参数可去掉；
        天猫/淘宝商品 ID 在 query 参数（id=xxx）中，必须保留。

        Args:
            url: 原始商品 URL

        Returns:
            str: 清理后的 URL
        """
        # 京东：item.jd.com/123456.html?xxx → 去掉 query
        m = re.match(r"(https?://item\.jd\.com/\d+\.html)", url, re.IGNORECASE)
        if m:
            return m.group(1)
        return url

    # ------------------------------------------------------------------
    # 数据源 4（兜底）：苏宁站内搜索
    # ------------------------------------------------------------------

    async def _search_suning(self, query: str) -> list[dict]:
        """
        请求苏宁易购站内搜索页（SSR 直出商品链接）。

        京东/天猫搜索有反爬或需登录，苏宁搜索页服务端渲染、可直连解析，
        作为必应与 360 均无商品页时的兜底数据源。

        Returns:
            [{"title": ..., "url": ..., "snippet": ...}, ...]
        """
        search_url = "https://search.suning.com/" + quote(query, safe="") + "/"
        html = await self._impersonated_get(search_url)
        if not html:
            return []

        products, seen = [], set()
        # 商品块：<li class="... item ..." id="{vendor}-{sku}">
        for block in re.split(r'<li[^>]+class="[^"]*\bitem\b', html)[1:]:
            link = re.search(
                r'href="(//product\.suning\.com/\d+/\d+\.html)"', block
            )
            if not link:
                continue
            url = "https:" + link.group(1)
            if url in seen:
                continue

            # 标题：优先取商品图 alt，其次标题区锚文本
            title = ""
            img = re.search(r'<img[^>]+alt="([^"]+)"', block)
            if img:
                title = img.group(1).strip()
            if not title:
                t = re.search(
                    r'class="title-selling-point"[^>]*>\s*<a[^>]*>(.*?)</a>',
                    block, re.DOTALL,
                )
                if t:
                    title = re.sub(r"<[^>]+>", "", t.group(1)).strip()
            if not title:
                continue

            # 卖点（卖点区 <em>）与店铺名，拼进摘要
            snippet_parts = []
            em = re.search(
                r'class="title-selling-point".*?<em[^>]*>([^<]+)</em>',
                block, re.DOTALL,
            )
            if em:
                snippet_parts.append(em.group(1).strip())
            shop = re.search(
                r"title=['\"]([^'\"]+)['\"][^>]*class=['\"]store-name", block
            )
            if shop:
                snippet_parts.append(shop.group(1))
            snippet = " | ".join(snippet_parts) if snippet_parts else "苏宁易购"

            seen.add(url)
            products.append({"title": title, "url": url, "snippet": snippet})
        return products

    # ------------------------------------------------------------------
    # 通用请求
    # ------------------------------------------------------------------

    async def _impersonated_get(self, url: str, referer: str | None = None) -> str:
        """
        以浏览器指纹发起 GET（优先 curl_cffi，未安装时退回 httpx）。

        Args:
            url: 目标地址
            referer: 可选 Referer 头

        Returns:
            str: 响应 HTML；请求失败返回空字符串
        """
        headers = {"User-Agent": self.USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}
        if referer:
            headers["Referer"] = referer
        try:
            from curl_cffi.requests import AsyncSession

            async with AsyncSession(impersonate="chrome", timeout=self.TIMEOUT) as s:
                resp = await s.get(url, headers=headers)
                return resp.text if resp.status_code == 200 else ""
        except ImportError:
            import httpx

            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True, headers=headers
            ) as client:
                resp = await client.get(url)
                return resp.text if resp.status_code == 200 else ""
