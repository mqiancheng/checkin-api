import json
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
        if not bypass_url:
            return None

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
                             "headers": headers, "body": body_str},
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


def execute_task(task_id: int, manual: bool = False) -> dict:
    """执行单个签到任务：发请求 -> 判定 -> 提取字段 -> 记日志 -> 通知。"""
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

        # 先建一条 running 日志，便于实时查看进度（解决"只有结束才看到日志"）
        log = RunLog(
            task_id=task.id, task_name=task.name, success=False,
            status_code=0, formatted="⏳ 任务执行中...", raw_response="", error="",
        )
        db.add(log)
        db.commit()

        def stage(msg: str):
            try:
                cur = db.get(RunLog, log.id)
                if cur:
                    cur.formatted = f"{cur.formatted}\n▶ {msg}"
                    db.commit()
            except Exception:
                pass

        try:
            if task.executor_type == "browser":
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
            log.formatted = formatted
            log.raw_response = raw_text
            log.error = ""
            db.commit()

        notify(task.name, success, formatted)
        return {
            "ok": True,
            "success": success,
            "status_code": status_code,
            "formatted": formatted,
            "raw": raw_text,
        }
    finally:
        db.close()
