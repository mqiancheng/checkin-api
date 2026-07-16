"""CFBypass 端点（v2：基于 CloakBrowser 隐身 Chromium）。

对外提供与旧版一致的接口：
  GET /{password}/cookies?url=...       -> {"cookies": {...}, "user_agent": ...}
  GET /{password}/turnstile?url=...     -> 同上，并尽力返回 turnstile_token

executor 侧无需改动：仍按 /{password}/cookies 取 cf_clearance 注入 httpx 请求。
"""
import os
import sys
import ipaddress
import logging
from typing import Dict, Optional
from urllib.parse import urlparse

# 让 cf_bypasser（本目录下的子包）可作为顶层包导入，无论以何种方式启动（uvicorn / 直接 import）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cloakbrowser as cb
from fastapi import FastAPI, HTTPException, Depends, Query
from pydantic import BaseModel

from cf_bypasser.core.bypasser import CloakBypasser, FAKE_SHADOW_ARG
from cf_bypasser.utils.misc import get_browser_init_lock
from cf_bypasser.utils.constants import DEFAULT_TIMEOUT_MS

PASSWORD = os.getenv("PASSWORD", "gua12345")
MAX_BROWSERS = int(os.getenv("CF_MAX_CONCURRENT_BROWSERS", "2"))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger("cfbypass")


class _CloakBypasser(CloakBypasser):
    """子类：容器内以 root 运行，需 --no-sandbox；保持有头模式复用 Xvfb 显示。

    有头模式对 Managed Challenge（如 nopecha 的 Just a moment...）过盾更稳，
    与上游 CloudflareBypassForScraping 的 docker-entrypoint（Xvfb + 有头）一致。
    """

    async def setup_browser(self, proxy=None, lang="en", user_agent=None, headless=False):
        self.cookie_cache.clear_expired()

        proxy_config = None
        if proxy:
            proxy_config = self.parse_proxy(proxy)
            if proxy_config:
                self.log_message(f"Using proxy: {proxy_config['server']}")
            else:
                # never silently fall back to direct: that leaks the real IP
                raise ValueError(f"Invalid proxy, refusing to continue direct: {proxy}")

        launch_kwargs = dict(
            headless=False,  # 有头：复用 start.sh 启动的 Xvfb 显示
            args=[FAKE_SHADOW_ARG, "--no-sandbox"],  # root 容器必须禁用沙箱
            geoip=bool(proxy_config),
            locale=lang if lang else None,
        )
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config
        if user_agent:
            launch_kwargs["user_agent"] = user_agent

        context = None
        try:
            # browserforge fingerprint generation isn't thread-safe; serialize launches
            async with get_browser_init_lock():
                context = await cb.launch_context_async(**launch_kwargs)
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT_MS)
            page.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
            return context, page
        except BaseException:
            # a partial launch must never orphan a browser process
            await self.cleanup_browser(context)
            raise


bypasser = _CloakBypasser(max_retries=5, log=True)

app = FastAPI(title="Cloudflare Bypasser (CloakBrowser)", version="2.0.0")


async def verify_password(password: str):
    if password != PASSWORD:
        logger.warning(f"密码验证失败: {password}")
        raise HTTPException(status_code=403, detail="Invalid password")
    return password


class CookieResponse(BaseModel):
    cookies: Dict[str, str]
    user_agent: str
    turnstile_token: str = ""


@app.get("/{password}/cookies", response_model=CookieResponse)
async def get_cookies(
    password: str = Depends(verify_password),
    url: Optional[str] = Query(None, description="Target URL to get cookies for"),
    retries: int = Query(5, ge=1, le=10),
):
    if not url or not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or unsafe URL")
    try:
        data = await bypasser.get_or_generate_cookies(url, None)
    except Exception as e:
        logger.error(f"获取 cookies 失败: {e}")
        raise HTTPException(status_code=500, detail="Failed to bypass Cloudflare protection")
    if not data:
        raise HTTPException(status_code=500, detail="Failed to bypass Cloudflare protection")
    cf_cookies = [n for n in data["cookies"] if n.startswith(("cf_", "__cf"))]
    logger.info(f"成功获取 {len(data['cookies'])} 个 cookie: {cf_cookies}")
    return CookieResponse(cookies=data["cookies"], user_agent=data["user_agent"])


@app.get("/{password}/turnstile", response_model=CookieResponse)
async def get_turnstile(
    password: str = Depends(verify_password),
    url: Optional[str] = Query(None, description="Target URL to get cookies+token for"),
    retries: int = Query(5, ge=1, le=10),
):
    if not url or not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid or unsafe URL")
    try:
        data = await bypasser.get_or_generate_cookies(url, None)
    except Exception as e:
        logger.error(f"获取 turnstile 失败: {e}")
        raise HTTPException(status_code=500, detail="Failed to bypass Cloudflare protection")
    if not data:
        raise HTTPException(status_code=500, detail="Failed to bypass Cloudflare protection")
    # 尽力从 cookie 中提取 turnstile token（若站点将其写入 cookie）
    token = ""
    for name, val in data["cookies"].items():
        if "turnstile" in name.lower():
            token = val
            break
    return CookieResponse(
        cookies=data["cookies"],
        user_agent=data["user_agent"],
        turnstile_token=token,
    )


from urllib.parse import urlparse as _urlparse


class ExecRequest(BaseModel):
    """browser 模式（/exec）：由 CloakBrowser 在页面上下文内执行 API 请求。"""
    url: str
    method: str = "GET"
    headers: Dict[str, str] = {}
    body: Optional[str] = None
    cookies: Dict[str, str] = {}


class ExecResponse(BaseModel):
    status: int = 0
    text: str = ""
    error: str = ""
    process_log: str = ""


async def _exec_in_page(req: ExecRequest) -> dict:
    """用 CloakBrowser 在页面上下文内执行 fetch，绕开 cf_clearance 指纹绑定。

    流程：注入 cookies -> 打开站点首页过 CF（自动点 Turnstile）-> 页面内 fetch API。
    """
    parsed = _urlparse(req.url)
    domain = parsed.netloc.split(":")[0]

    context = None
    log_lines: list[str] = []
    try:
        log_lines.append("启动 CloakBrowser（有头模式，复用 Xvfb 显示）...")
        context, page = await bypasser.setup_browser()

        if req.cookies:
            cookie_list = [
                {"name": k, "value": v, "domain": domain, "path": "/"}
                for k, v in req.cookies.items()
            ]
            await context.add_cookies(cookie_list)

        # 直接打开目标 URL 过 CF（而非首页）：browser 模式的目标就是受保护的页/API，
        # 这样无论首页是否受保护都能正确过盾；对 POST API 也适用（goto GET 版本过盾后 fetch POST）。
        log_lines.append(f"打开 {req.url} 并等待 Cloudflare 放行...")
        result = await bypasser.solve_cloudflare_challenge(req.url, page)
        if not result.success:
            log_lines.append("Cloudflare 验证超时，browser 模式任务被拦截")
            return {
                "status": 0, "text": "",
                "error": "Cloudflare 验证超时，browser 模式任务被拦截",
                "process_log": "\n".join(log_lines),
            }

        log_lines.append("在页面上下文内发起 API 请求...")
        resp = await page.evaluate(
            """async (args) => {
                const r = await fetch(args.url, {
                    method: args.method,
                    headers: args.headers,
                    body: args.body
                });
                const t = await r.text();
                return {status: r.status, text: t};
            }""",
            {
                "url": req.url,
                "method": req.method,
                "headers": req.headers,
                "body": req.body if req.method not in ("GET", "HEAD") else None,
            },
        )
        status = resp.get("status", 200) if isinstance(resp, dict) else 200
        text = resp.get("text", "") if isinstance(resp, dict) else str(resp)
        log_lines.append(f"页面内请求完成: status={status}, 响应长度={len(text)}")
        return {
            "status": status, "text": text, "error": "",
            "process_log": "\n".join(log_lines),
        }
    except Exception as e:
        logger.error(f"/exec 异常: {e}")
        return {
            "status": 0, "text": "",
            "error": f"browser 模式执行异常: {e}",
            "process_log": "\n".join(log_lines),
        }
    finally:
        await bypasser.cleanup_browser(context)


def _is_safe_url(url: str) -> bool:
    """SSRF 防护：仅拦截 IP 字面量（私网/回环/链路本地等）和已知危险主机名。

    不依赖实时 DNS 解析（cf_bypasser 的 is_safe_url 在 Python 进程无法解析域名时会
    一律拒绝，导致 /exec 永远 400）。浏览器子进程自身会做 DNS 解析，这里只防最危险的
    内网/IP 直连场景。
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme in ("file", "ftp", "gopher"):
            return False
        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            return False

        # 已知危险主机名（云元数据、内网域名等）
        dangerous = ("localhost", "metadata", "metadata.google.internal",
                     "metadata.internal", "169.254.169.254")
        if hostname in dangerous or hostname.endswith((".local", ".internal", ".svc", ".svc.cluster.local")):
            return False

        # IP 字面量：拦截私网/回环/链路本地等
        try:
            ip = ipaddress.ip_address(hostname)
            return not (ip.is_loopback or ip.is_private or ip.is_link_local
                        or ip.is_multicast or ip.is_reserved or ip.is_unspecified)
        except ValueError:
            # 非 IP 字面量（普通域名）：放行（DNS 解析交给浏览器）
            return True
    except Exception:
        return False


@app.post("/{password}/exec", response_model=ExecResponse)
async def exec_in_page(
    req: ExecRequest,
    password: str = Depends(verify_password),
):
    if not _is_safe_url(req.url):
        raise HTTPException(status_code=400, detail="Invalid or unsafe URL")
    data = await _exec_in_page(req)
    if data.get("error"):
        logger.warning(f"/exec 执行返回错误: {data['error']}")
    return ExecResponse(**data)
