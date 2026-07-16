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
        # 密码优先取 PASSWORD（与 CFBypass server 同一变量），兼容旧 CFB_PASSWORD，默认 gua12345
        cfb_password = os.environ.get("PASSWORD") or os.environ.get("CFB_PASSWORD") or "gua12345"
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
        except Exception as e:
            _stage(f"调用 bypass 服务异常: {e}")
            return None

        _stage(f"bypass 服务返回: status={r.status_code}, cookies_keys={list(data.get('cookies', {}).keys())}, ua={str(data.get('user_agent', ''))[:60]}")
        cf_cookies = data.get("cookies", {})
        cl = cf_cookies.get("cf_clearance") if isinstance(cf_cookies, dict) else None
        if not cl:
            _stage("bypass 返回的 cookies 中无 cf_clearance（该站可能不下发此 cookie 或 bypass 未成功过盾）")
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
    密码统一用环境变量 CFB_PASSWORD（默认 test1234）拼接，
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
    cfb_password = os.environ.get("PASSWORD") or os.environ.get("CFB_PASSWORD") or "gua12345"
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





def _send_browser(task, params, headers, body, cookies, stage=None) -> tuple[int, bytes]:
    """通过 cfbypass 服务的 /exec 端点，由 CloakBrowser 在页面上下文内执行 API。

    等价于在 F12 Console 里敲 fetch：CloakBrowser 先过 CF（无感放行），再在页面内
    发请求，绕开 cf_clearance 与浏览器指纹绑定的限制。executor 侧只发 HTTP，
    不依赖任何本地浏览器库（camoufox/playwright/chrome 全删）。

    stage: 可选的进度回调，用于把执行阶段实时写入日志。
    """
    def _stage(msg):
        if stage:
            try:
                stage(msg)
            except Exception:
                pass

    full_url = task.url
    if params:
        full_url += ("&" if "?" in full_url else "?") + urlencode(params)

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

    cfb_password = os.environ.get("PASSWORD") or os.environ.get("CFB_PASSWORD") or "gua12345"
    exec_url = f"{_bypass_host()}/{cfb_password}/exec"

    _stage("browser 模式：提交 CloakBrowser 页面内执行任务到 cfbypass 服务...")
    try:
        r = httpx.post(
            exec_url,
            json={
                "url": full_url,
                "method": task.method,
                "headers": headers,
                "body": body_str if task.method not in ("GET", "HEAD") else None,
                "cookies": all_cookies,
            },
            timeout=180,
        )
        data = r.json()
    except Exception as e:
        return 0, f"browser 模式调用 cfbypass /exec 失败: {e}".encode("utf-8")

    # 把服务端执行过程回写到进度日志
    if data.get("process_log"):
        for line in data["process_log"].split("\n"):
            if line.strip():
                _stage(line)

    if data.get("error"):
        return 0, data["error"].encode("utf-8")
    return data.get("status", 0), (data.get("text") or "").encode("utf-8")





def _send_http(task, params, headers, body, stage=None) -> tuple[int, bytes]:
    """HTTP 模式发送请求，必要时自动调用 cf_bypass 获取 cf_clearance 重试。

    cf_bypass 三档：
      auto -> 仅当被 CF 拦截时才尝试（默认，最省资源，优先用缓存）
      on   -> 每次都强制调用 cfbypass 获取全新 cf_clearance（忽略缓存、不先发请求）
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

    if mode == "on":
        # 强制模式：每次都直接调 cfbypass 拿全新 cf_clearance，跳过缓存、不先发请求
        _stage("HTTP 模式：强制调用 bypass 服务获取全新 cf_clearance（忽略缓存）")
        cl = _get_clearance(task.url, force=True)
        if not cl:
            _stage("未能获取 cf_clearance（bypass 服务不可用或未配置），返回原始响应")
            return _send(task, params, headers, body)
        retry_headers = dict(headers)
        retry_headers["Cookie"] = _merge_cookie(
            retry_headers.get("Cookie", ""), {"cf_clearance": cl["cf_clearance"]}
        )
        if cl.get("user_agent"):
            retry_headers["user-agent"] = cl["user_agent"]
        _stage("注入全新 cf_clearance 后发送")
        return _send(task, params, retry_headers, body)

    # auto 模式：先正常发一次，被拦再调 bypass
    _stage("HTTP 模式：发起请求")
    status_code, raw_bytes = _send(task, params, headers, body)
    if not _is_blocked(status_code, raw_bytes):
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
            if task.executor_type in ("chrome", "browser", "camoufox"):
                stage(f"浏览器模式（CloakBrowser）：{task.method} {task.url}")
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
