import json
import os
import re
from datetime import datetime
from urllib.parse import urlencode, urlparse

import httpx

# 优先使用 curl_cffi：它能模拟 Chrome 的 TLS 指纹（JA3/JA4），
# 让 Cloudflare 等防护把请求当作真实浏览器，避免 403 拦截。
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

from app.condition import evaluate, evaluate_text, resolve_path
from app.db import SessionLocal
from app.models import BypassCache, RunLog, Setting, Task
from app.notify import notify

# cf_clearance 缓存有效期（秒）。有效期内不调用 NAS bypass，超期才刷新。
# 配合"请求被拦则强制刷新"机制：即便缓存期内提前失效，也只会多调一次 bypass。
BYPASS_CACHE_TTL = 3600 * 12

# camoufox 反检测浏览器是否可用（仅在 browser 模式时按需 import）
HAS_CAMOUFOX = False
try:
    from camoufox.sync_api import Camoufox  # noqa: F401
    HAS_CAMOUFOX = True
except ImportError:
    HAS_CAMOUFOX = False

# Playwright（驱动本地 Chrome / cloakbrowser 等，避免 camoufox 内置驱动的 pageerror bug）
HAS_PLAYWRIGHT = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


def _merge_cookie(existing: str, add: dict) -> str:
    """把 add 里的 cookie 合并进 existing（字符串形式），同名字段覆盖。"""
    parts = {}
    if existing:
        for item in existing.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                parts[k.strip()] = v.strip()
    parts.update(add)
    return "; ".join(f"{k}={v}" for k, v in parts.items())


def _is_blocked(status_code: int, raw_bytes: bytes) -> bool:
    """判断响应是否被 Cloudflare 拦截（普通 JS Challenge / 验证页）。"""
    if status_code in (403, 503, 429):
        text = _decode_bytes(raw_bytes).lower()
        if any(k in text for k in (
            "just a moment", "attention required", "cf-chl", "challenge",
            "cf-mitigated", "verify you are human", "enable javascript",
        )):
            return True
        # 403 且无 JSON 业务体，多半是 CF 拦了
        try:
            json.loads(_decode_bytes(raw_bytes))
            return False
        except (UnicodeDecodeError, json.JSONDecodeError):
            return True
    return False


def _get_clearance(url: str, force: bool = False) -> dict | None:
    """获取某域名的 cf_clearance：优先用缓存，否则调 NAS bypass 服务。

    返回 {"cf_clearance": str, "user_agent": str, "from_cache": bool} 或 None。
    """
    domain = urlparse(url).netloc
    db = SessionLocal()
    try:
        if not force:
            cached = db.query(BypassCache).filter_by(domain=domain).first()
            if cached:
                age = (datetime.now() - cached.updated_at).total_seconds()
                if age < BYPASS_CACHE_TTL:
                    return {
                        "cf_clearance": cached.cf_clearance,
                        "user_agent": cached.user_agent,
                        "from_cache": True,
                    }

        setting = db.query(Setting).first()
        bypass_url = (setting.bypass_url or "").strip() if setting else ""
        # 统一用 host 基础 + 环境变量密码拼接，避免裸用基础地址导致缺 /cookies 路径
        cfb_password = os.environ.get("CFB_PASSWORD", "mnqswhai")
        if not bypass_url:
            # 合并模式：未配置外部 bypass 时，默认自调容器内 CFBypass 端点
            bypass_url = f"http://127.0.0.1:10000/{cfb_password}/cookies"
        else:
            bypass_url = f"{_bypass_host()}/{cfb_password}/cookies"

        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
        try:
            r = httpx.get(
                bypass_url,
                params={"url": f"https://{domain}/", "user_agent": ua},
                timeout=90,
            )
            data = r.json()
        except Exception:
            return None

        cf_cookies = data.get("cookies", {})
        cl = cf_cookies.get("cf_clearance") if isinstance(cf_cookies, dict) else None
        if not cl:
            return None
        ua_used = data.get("user_agent", ua)

        row = db.query(BypassCache).filter_by(domain=domain).first()
        if row:
            row.cf_clearance = cl
            row.user_agent = ua_used
            row.updated_at = datetime.now()
        else:
            db.add(BypassCache(
                domain=domain, cf_clearance=cl,
                user_agent=ua_used, updated_at=datetime.now(),
            ))
        db.commit()
        return {"cf_clearance": cl, "user_agent": ua_used, "from_cache": False}
    finally:
        db.close()


def _bypass_host() -> str:
    """返回 CFBypass 端点 host 基础（scheme://host:port，不含密码与路径）。

    用户只需在全局设置填 host:port（如 http://192.168.6.100:10001），
    密码统一用环境变量 CFB_PASSWORD（默认 mnqswhai）拼接，
    避免只填基础地址导致缺密码段 / 缺 /cookies 路径而 404。
    """
    db = SessionLocal()
    try:
        setting = db.query(Setting).first()
        bypass_url = (setting.bypass_url or "").strip() if setting else ""
    finally:
        db.close()
    if not bypass_url:
        return "http://127.0.0.1:10000"
    # 只取 scheme://host:port，丢弃用户可能误填的路径/密码段
    m = re.match(r"^(https?://[^/]+)", bypass_url)
    return m.group(1) if m else bypass_url.rstrip("/")


def _get_turnstile(url: str) -> dict | None:
    """通过 CFBypass 的 /turnstile 端点获取 cf_clearance + turnstile_token。

    用于需要提交 Turnstile 验证码的站点（如登录表单）。
    返回 {"cf_clearance": str, "user_agent": str, "turnstile_token": str} 或 None。
    """
    domain = urlparse(url).netloc
    cfb_password = os.environ.get("CFB_PASSWORD", "mnqswhai")
    turnstile_url = f"{_bypass_host()}/{cfb_password}/turnstile"
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
    try:
        r = httpx.get(
            turnstile_url,
            params={"url": f"https://{domain}/", "user_agent": ua},
            timeout=120,
        )
        data = r.json()
    except Exception:
        return None

    cf_cookies = data.get("cookies", {})
    cl = cf_cookies.get("cf_clearance") if isinstance(cf_cookies, dict) else None
    token = data.get("turnstile_token") or ""
    if isinstance(cf_cookies, dict):
        token = token or (cf_cookies.get("turnstile_token") or "")
    if not cl:
        return None
    return {
        "cf_clearance": cl,
        "user_agent": data.get("user_agent", ua),
        "turnstile_token": token,
    }


def _inject_turnstile(params, headers, body, token: str):
    """把 params / headers / body 中的 {{turnstile_token}} 占位符替换为 token。"""
    def _rep(v):
        if isinstance(v, str):
            return v.replace("{{turnstile_token}}", token)
        return v
    new_params = {k: _rep(v) for k, v in (params or {}).items()}
    new_headers = {k: _rep(v) for k, v in (headers or {}).items()}
    if isinstance(body, dict):
        new_body = {k: _rep(v) for k, v in body.items()}
    elif isinstance(body, str):
        new_body = body.replace("{{turnstile_token}}", token)
    else:
        new_body = body
    return new_params, new_headers, new_body


def _wait_cf(page, max_wait: int = 40) -> bool:
    """等待 Cloudflare 验证通过（如有）。返回 True 表示已放行。"""
    import time
    time.sleep(3)
    for _ in range(max_wait // 3):
        try:
            title = page.title()
            if "Just a moment" not in title and "Attention Required" not in title:
                return True
            try:
                checkbox = page.locator('#challenge-stage label, [id*="challenge"]')
                if checkbox.count() > 0:
                    checkbox.first.click()
                    time.sleep(3)
            except Exception:
                pass
            time.sleep(3)
        except Exception:
            time.sleep(3)
    return False


def _send_browser(task, params, headers, body, cookies, stage=None) -> tuple[int, bytes]:
    """用 camoufox 反检测浏览器在页面上下文内执行 API（应对 Managed Challenge）。

    等价于在 F12 Console 里敲 fetch：浏览器先过 CF（无感放行），再在页面内发请求，
    绕开 cf_clearance 与浏览器指纹绑定的限制。

    stage: 可选的进度回调，用于把执行阶段实时写入日志。
    """
    if not HAS_CAMOUFOX:
        return 0, "camoufox 未安装，无法执行 browser 模式任务".encode("utf-8")

    from camoufox.sync_api import Camoufox

    full_url = task.url
    if params:
        full_url += ("&" if "?" in full_url else "?") + urlencode(params)
    domain = urlparse(task.url).netloc
    origin = f"https://{domain}/"

    # 合并 cookie：任务 cookies + 请求头里的 Cookie
    all_cookies = dict(cookies)
    hdr_cookie = headers.get("Cookie") or headers.get("cookie")
    if hdr_cookie:
        for item in hdr_cookie.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                all_cookies[k.strip()] = v.strip()
    # 浏览器内 fetch 不需要 Cookie 头（已通过 add_cookies 注入上下文）
    headers.pop("Cookie", None)
    headers.pop("cookie", None)

    body_str = body if isinstance(body, str) else json.dumps(body)

    def _stage(msg):
        if stage:
            try:
                stage(msg)
            except Exception:
                pass

    # 只放行：同站资源 + 目标 API + Cloudflare 挑战脚本；拦截其余跨站资源。
    # 站点自身的第三方脚本（analytics/广告）常抛未捕获错误，会触发 camoufox 内置
    # playwright 的 pageerror bug 导致驱动崩溃；拦截它们可从源头避免。
    def _route(route):
        url = route.request.url
        low = url.lower()
        if (url.startswith(f"https://{domain}")
                or url.startswith(f"http://{domain}")
                or full_url in url or url.startswith(full_url)
                or "cloudflare" in low):
            return route.continue_()
        return route.abort()

    out: tuple[int, bytes] = (0, b"")
    err: str | None = None
    try:
        _stage("启动 camoufox 反检测浏览器...")
        with Camoufox(headless=True) as browser:
            try:
                context = browser.new_context()
                if all_cookies:
                    context.add_cookies([
                        {"name": k, "value": v, "domain": domain, "path": "/"}
                        for k, v in all_cookies.items()
                    ])
                page = context.new_page()
                page.set_viewport_size({"width": 1920, "height": 1080})
                page.route("**/*", _route)

                _stage(f"打开站点 {origin} 并等待 Cloudflare 放行...")
                page.goto(origin, wait_until="domcontentloaded", timeout=30000)
                if not _wait_cf(page):
                    err = "Cloudflare 验证超时，浏览器模式任务被拦截"
                else:
                    _stage("在页面上下文内发起 API 请求...")
                    try:
                        result = page.evaluate(
                            """async (args) => {
                                const r = await fetch(args.url, {
                                    method: args.method,
                                    headers: args.headers,
                                    body: args.body
                                });
                                const t = await r.text();
                                return {status: r.status, text: t};
                            }""",
                            {"url": full_url, "method": task.method,
                             "headers": headers, "body": body_str if task.method not in ("GET", "HEAD") else None},
                        )
                        status = result.get("status", 200) if isinstance(result, dict) else 200
                        text = result.get("text", "") if isinstance(result, dict) else str(result)
                        out = (status, text.encode("utf-8"))
                    except Exception as ev:
                        err = f"页面内请求执行失败: {ev}"
            except Exception as ex:
                if err is None:
                    err = f"浏览器执行异常: {ex}"
    except Exception as close_ex:
        # with __exit__ 关闭时若浏览器已崩溃会抛 Browser.close 错误，忽略它，
        # 优先保留上面已捕获的真实错误/结果。
        if err is None and out == (0, b""):
            err = f"浏览器执行异常: {close_ex}"

    if err is not None:
        return 0, err.encode("utf-8")
    return out[0], out[1]


def _get_chrome_path() -> str | None:
    """解析 chrome 模式使用的浏览器可执行文件路径。

    优先级：环境变量 CHROME_PATH > 全局设置 chrome_path。
    路径不存在则返回 None。
    """
    env = os.environ.get("CHROME_PATH", "").strip()
    if env and os.path.exists(env):
        return env
    db = SessionLocal()
    try:
        s = db.query(Setting).first()
        p = (s.chrome_path or "").strip() if s else ""
        if p and os.path.exists(p):
            return p
    finally:
        db.close()
    return None


def _send_chrome(task, params, headers, body, cookies, stage=None) -> tuple[int, bytes]:
    """用本地 Chrome / cloakbrowser（Playwright 驱动）在页面上下文内执行 API。

    与 camoufox 模式等价：浏览器先过 CF，再在页面内 fetch。区别是用普通 Chrome
    二进制（通过 executable_path 指定），避免 camoufox 内置 playwright 驱动的
    pageerror bug。适合本地测试或已自备反检测 Chrome 的场景。
    """
    if not HAS_PLAYWRIGHT:
        return 0, "playwright 未安装，无法执行 chrome 模式任务".encode("utf-8")

    chrome_path = _get_chrome_path()
    if not chrome_path:
        return 0, (
            "未配置 chrome 路径：请设置环境变量 CHROME_PATH "
            "或在全局设置中填写 chrome_path"
        ).encode("utf-8")

    full_url = task.url
    if params:
        full_url += ("&" if "?" in full_url else "?") + urlencode(params)
    domain = urlparse(task.url).netloc
    origin = f"https://{domain}/"

    # 合并 cookie：任务 cookies + 请求头里的 Cookie
    all_cookies = dict(cookies)
    hdr_cookie = headers.get("Cookie") or headers.get("cookie")
    if hdr_cookie:
        for item in hdr_cookie.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                all_cookies[k.strip()] = v.strip()
    headers.pop("Cookie", None)
    headers.pop("cookie", None)

    body_str = body if isinstance(body, str) else json.dumps(body)

    def _stage(msg):
        if stage:
            try:
                stage(msg)
            except Exception:
                pass

    # 只放行：同站资源 + 目标 API + Cloudflare 挑战脚本；拦截其余跨站资源。
    def _route(route):
        url = route.request.url
        low = url.lower()
        if (url.startswith(f"https://{domain}")
                or url.startswith(f"http://{domain}")
                or full_url in url or url.startswith(full_url)
                or "cloudflare" in low):
            return route.continue_()
        return route.abort()

    out: tuple[int, bytes] = (0, b"")
    err: str | None = None
    try:
        _stage(f"启动 Chrome（{chrome_path}）...")
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    executable_path=chrome_path,
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context()
                if all_cookies:
                    context.add_cookies([
                        {"name": k, "value": v, "domain": domain, "path": "/"}
                        for k, v in all_cookies.items()
                    ])
                page = context.new_page()
                page.set_viewport_size({"width": 1920, "height": 1080})
                page.route("**/*", _route)

                _stage(f"打开站点 {origin} 并等待 Cloudflare 放行...")
                page.goto(origin, wait_until="domcontentloaded", timeout=30000)
                if not _wait_cf(page):
                    err = "Cloudflare 验证超时，Chrome 模式任务被拦截"
                else:
                    _stage("在页面上下文内发起 API 请求...")
                    try:
                        result = page.evaluate(
                            """async (args) => {
                                const r = await fetch(args.url, {
                                    method: args.method,
                                    headers: args.headers,
                                    body: args.body
                                });
                                const t = await r.text();
                                return {status: r.status, text: t};
                            }""",
                            {"url": full_url, "method": task.method,
                             "headers": headers, "body": body_str if task.method not in ("GET", "HEAD") else None},
                        )
                        status = result.get("status", 200) if isinstance(result, dict) else 200
                        text = result.get("text", "") if isinstance(result, dict) else str(result)
                        out = (status, text.encode("utf-8"))
                    except Exception as ev:
                        err = f"页面内请求执行失败: {ev}"
                browser.close()
            except Exception as ex:
                if err is None:
                    err = f"Chrome 执行异常: {ex}"
    except Exception as ex:
        if err is None and out == (0, b""):
            err = f"Chrome 执行异常: {ex}"

    if err is not None:
        return 0, err.encode("utf-8")
    return out[0], out[1]


def _send_http(task, params, headers, body, stage=None) -> tuple[int, bytes]:
    """HTTP 模式发送请求，必要时自动调用 cf_bypass 获取 cf_clearance 重试。

    cf_bypass 三档：
      auto -> 仅当被 CF 拦截时才尝试（默认，最省资源）
      on   -> 强制注入 cf_clearance（即便未被拦，先确保有 clearance）
      off  -> 完全不调用 bypass
    """
    def _stage(m):
        if stage:
            try:
                stage(m)
            except Exception:
                pass

    mode = (task.cf_bypass or "auto").lower()

    # Turnstile 任务：先获取 token + clearance 并注入，再直接发送（不走 auto 重试逻辑）
    if getattr(task, "cf_turnstile", False) and mode != "off":
        _stage("该任务需要 Turnstile，调用 bypass /turnstile 端点")
        ts = _get_turnstile(task.url)
        if ts:
            retry_headers = dict(headers)
            retry_headers["Cookie"] = _merge_cookie(
                retry_headers.get("Cookie", ""), {"cf_clearance": ts["cf_clearance"]}
            )
            if ts.get("user_agent"):
                retry_headers["user-agent"] = ts["user_agent"]
            token = ts.get("turnstile_token") or ""
            if token:
                params, retry_headers, body = _inject_turnstile(params, retry_headers, body, token)
                _stage("已注入 cf_clearance 与 turnstile_token，直接发送")
            else:
                _stage("已注入 cf_clearance（站点无 Turnstile，未返回 token），直接发送")
            return _send(task, params, retry_headers, body)
        else:
            _stage("未能获取 Turnstile（bypass 服务不可用），退回普通模式")

    if mode == "off":
        _stage("HTTP 模式：直接发起请求（CF Bypass 关闭）")
        return _send(task, params, headers, body)

    _stage("HTTP 模式：发起请求")
    # 先正常发一次
    status_code, raw_bytes = _send(task, params, headers, body)
    if mode == "auto" and not _is_blocked(status_code, raw_bytes):
        return status_code, raw_bytes

    _stage("请求被 Cloudflare 拦截或需 clearance，调用 bypass 服务获取 cf_clearance")
    # 需要 clearance：取缓存或调 bypass
    cl = _get_clearance(task.url)
    if not cl:
        # 拿不到 clearance，返回原始响应（由判定逻辑决定成败）
        _stage("未能获取 cf_clearance（bypass 服务不可用或未配置），返回原始响应")
        return status_code, raw_bytes

    retry_headers = dict(headers)
    retry_headers["Cookie"] = _merge_cookie(
        retry_headers.get("Cookie", ""), {"cf_clearance": cl["cf_clearance"]}
    )
    if cl.get("user_agent"):
        retry_headers["user-agent"] = cl["user_agent"]

    _stage("注入 cf_clearance 后重试")
    status_code, raw_bytes = _send(task, params, retry_headers, body)

    # 若用的是缓存且仍被拦，说明缓存提前失效 -> 强制刷新 bypass 再试一次
    if cl.get("from_cache") and _is_blocked(status_code, raw_bytes):
        _stage("缓存的 clearance 已失效，强制刷新 bypass 重试")
        cl2 = _get_clearance(task.url, force=True)
        if cl2:
            retry_headers["Cookie"] = _merge_cookie(
                retry_headers.get("Cookie", ""), {"cf_clearance": cl2["cf_clearance"]}
            )
            if cl2.get("user_agent"):
                retry_headers["user-agent"] = cl2["user_agent"]
            status_code, raw_bytes = _send(task, params, retry_headers, body)

    return status_code, raw_bytes


def _decode_bytes(raw_bytes: bytes) -> str:
    """把原始字节按优先级解码为文本，避免任何编码异常。"""
    for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _send(task, params, headers, body):
    """发送请求，返回 (status_code, raw_bytes)。优先 curl_cffi，回退 httpx。"""
    if HAS_CFFI:
        resp = cffi_requests.request(
            task.method,
            task.url,
            params=params,
            headers=headers,
            json=body if task.body_type == "json" else None,
            data=body if task.body_type in ("form", "raw") else None,
            impersonate="chrome",
            timeout=30,
            allow_redirects=True,
        )
        return resp.status_code, resp.content

    # 回退：httpx（TLS 指纹可被 Cloudflare 识别，可能被 403）
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.request(
            task.method,
            task.url,
            params=params,
            headers=headers,
            json=body if task.body_type == "json" else None,
            data=body if task.body_type == "form" else None,
            content=body if task.body_type == "raw" else None,
        )
    return resp.status_code, resp.content


def execute_task(task_id: int, manual: bool = False, log_id: int = 0) -> dict:
    """执行单个签到任务：发请求 -> 判定 -> 提取字段 -> 记日志 -> 通知。

    Args:
        task_id: 任务 ID
        manual: 是否手动触发
        log_id: 若 > 0，复用已存在的 RunLog 记录（由调用方预创建），不再新建
    """
    db = SessionLocal()
    log = None
    try:
        task = db.get(Task, task_id)
        if not task:
            return {"ok": False, "error": "任务不存在"}

        headers = json.loads(task.headers or "{}")
        cookies = json.loads(task.cookies or "{}")
        params = json.loads(task.params or "{}")

        body = task.body or ""
        if task.body_type == "json" and body:
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                body = body

        # Cookie 合并进 header（若已存在 Cookie 头则覆盖）
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())

        # httpx 不支持 zstd 自动解压，移除后由服务端回退到 gzip/deflate/br
        ae = headers.get("accept-encoding", "")
        if "zstd" in ae.lower():
            headers["accept-encoding"] = (
                ae.lower().replace("zstd", "").replace(",,", ",").strip(", ").strip()
            )

        # 创建/复用 running 日志，便于实时查看进度
        if log_id > 0:
            log = db.get(RunLog, log_id)
            if not log:
                # 预创建的日志不存在（异常情况），回退到新建
                log_id = 0
        if not log or log_id == 0:
            log = RunLog(
                task_id=task.id, task_name=task.name, success=False,
                status_code=0, formatted="⏳ 任务执行中...", raw_response="", error="",
                process_log="",
            )
            db.add(log)
            db.commit()

        def stage(msg: str):
            """记录执行过程步骤：持久写入 process_log，同步更新 formatted 供实时轮询。"""
            try:
                cur = db.get(RunLog, log.id)
                if cur:
                    line = f"▶ {msg}"
                    # 持久化：追加到 process_log
                    cur.process_log = (cur.process_log or "").rstrip() + "\n" + line
                    # 实时：同步到 formatted 供轮询（执行期间 formatted 包含过程）
                    cur.formatted = (cur.formatted or "").rstrip() + "\n" + line
                    db.commit()
            except Exception:
                pass

        try:
            if task.executor_type == "chrome":
                stage(f"Chrome 模式：{task.method} {task.url}")
                status_code, raw_bytes = _send_chrome(task, params, headers, body, cookies, stage)
            elif task.executor_type == "browser":
                stage(f"浏览器模式：{task.method} {task.url}")
                status_code, raw_bytes = _send_browser(task, params, headers, body, cookies, stage)
            else:
                status_code, raw_bytes = _send_http(task, params, headers, body, stage)
            raw_text = _decode_bytes(raw_bytes)
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                data = None
        except Exception as ex:  # 网络/请求级错误
            if log:
                log.success = False
                log.status_code = 0
                log.formatted = f"请求失败: {ex}"
                log.error = str(ex)
                log.finished_at = datetime.now()
                db.commit()
            notify(task.name, False, f"请求失败: {ex}")
            return {"ok": False, "error": str(ex)}

        # 判定成功
        if task.response_type == "json":
            success = evaluate(
                json.loads(task.conditions or "[]"),
                task.logic,
                data if data is not None else {},
            )
        else:
            success = evaluate_text(
                json.loads(task.conditions or "[]"), task.logic, raw_text
            )

        # 生成展示文本（按字段映射）
        lines = []
        for f in json.loads(task.fields or "[]"):
            label = f.get("label") or f.get("path") or ""
            val = resolve_path(data, f.get("path", "")) if data is not None else None
            lines.append(f"{label}: {val if val is not None else '(无)'}")
        formatted = "\n".join(lines) if lines else (raw_text[:500] or "(空响应)")

        if log:
            log.success = success
            log.status_code = status_code
            # formatted 最终只存结果摘要（供日志列表/弹窗最终状态使用，不含 ▶ 过程行）
            # ▶ 过程日志仅在执行期间通过轮询可见，执行完毕后 formatted 被替换为纯结果
            log.formatted = formatted
            log.raw_response = raw_text
            log.error = ""
            log.finished_at = datetime.now()
            db.commit()
        # 返回值也带上完整日志（含过程），供弹窗展示
        _final_formatted = log.formatted if log else formatted
        notify(task.name, success, _final_formatted)
        return {
            "ok": True,
            "success": success,
            "status_code": status_code,
            "formatted": _final_formatted,
            "raw": raw_text,
        }
    finally:
        db.close()
