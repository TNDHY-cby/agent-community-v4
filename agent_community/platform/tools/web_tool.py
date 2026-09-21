"""网页抓取工具。"""

from __future__ import annotations

from ..tool_registry import BaseTool, ToolResult, ToolSchema


class WebFetchTool(BaseTool):
    """抓取网页正文内容。仅 GET 请求，超时 15 秒。"""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="web_fetch",
            description=(
                "抓取指定网页的正文内容（以 Markdown 格式返回）。用于获取网页上的信息、"
                "读取在线文档、查阅 API 文档等。仅支持 HTTP GET 请求，超时 15 秒。"
                "不支持需要登录、交互或 JavaScript 动态渲染的页面。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的网页 URL，必须以 http:// 或 https:// 开头",
                    },
                },
                "required": ["url"],
            },
        )

    async def execute(self, **params) -> ToolResult:
        url = params.get("url", "")
        if not url:
            return ToolResult(tool_name="web_fetch", success=False, content="缺少参数: url")
        if not url.startswith(("http://", "https://")):
            return ToolResult(tool_name="web_fetch", success=False, content=f"URL 必须以 http:// 或 https:// 开头: {url}")

        try:
            import httpx

            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                r = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    },
                    follow_redirects=True,
                )

            if r.status_code != 200:
                return ToolResult(
                    tool_name="web_fetch",
                    success=False,
                    content=f"HTTP {r.status_code}: {r.reason_phrase}",
                )

            content_type = r.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ToolResult(
                    tool_name="web_fetch",
                    success=True,
                    content=f"非文本内容 ({content_type})，大小: {len(r.content)} 字节。"
                            f"内容预览: {r.text[:500]}",
                )

            # 简易 HTML → Markdown/纯文本提取
            text = _html_to_text(r.text)

            # 截断过长内容
            if len(text) > 8000:
                text = text[:8000] + f"\n\n... (内容过长，已截断，共 {len(r.text)} 字符)"

            return ToolResult(
                tool_name="web_fetch",
                success=True,
                content=f"URL: {url}\n---\n{text}",
            )
        except ImportError:
            return ToolResult(tool_name="web_fetch", success=False, content="缺少 httpx 库，无法执行网络请求")
        except Exception as e:
            return ToolResult(tool_name="web_fetch", success=False, content=f"抓取失败: {type(e).__name__}: {e}")


def _html_to_text(html: str) -> str:
    """简易 HTML → 纯文本提取。不依赖 BeautifulSoup，用正则做基本清理。"""
    import re

    # 移除 script/style/head
    for tag in ("script", "style", "head", "noscript", "iframe"):
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # 移除 HTML 注释
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

    # 将块级元素替换为换行
    for tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "header", "footer"):
        html = re.sub(rf"<{tag}[^>]*>", "\n", html, flags=re.IGNORECASE)

    # 移除所有剩余 HTML 标签
    html = re.sub(r"<[^>]+>", "", html)

    # 解码 HTML 实体
    html = html.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&quot;", '"').replace("&#39;", "'").replace("&mdash;", "—").replace("&ndash;", "–")

    # 压缩空白行
    lines = [line.strip() for line in html.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)
