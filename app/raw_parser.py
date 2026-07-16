import json
import re
from urllib.parse import urlparse, parse_qsl

METHOD_RE = re.compile(
    r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(\S+)(?:\s+HTTP/[\d.]+)?$",
    re.IGNORECASE,
)


def parse_raw(text: str) -> dict:
    """解析 Fiddler / Chrome 开发者工具 复制出的原始 HTTP 文本。

    支持三种格式：
    1. Fiddler 原始请求（POST https://... HTTP/2 开头）
    2. Chrome 开发者工具 Headers 面板复制（含 Request URL / Request Method）
    3. curl 命令
    """
    if not text:
        return {}
    if text.lstrip().lower().startswith("curl "):
        return parse_curl(text)
    # Chrome 开发者工具 Headers 格式检测
    if re.search(r"Request\s+URL", text) or "Request Method" in text:
        return parse_chrome_headers(text)

    text = text.replace("\r\n", "\n")
    lines = []
    for line in text.split("\n"):
        # 过滤掉以 // 开头的注释行（如 Fiddler 注入的 //User-Agent:...）
        if line.strip().startswith("//"):
            continue
        lines.append(line)

    method, url = "POST", ""
    if lines and METHOD_RE.match(lines[0].strip()):
        m = METHOD_RE.match(lines[0].strip())
        method = m.group(1).upper()
        url = m.group(2)

    # 拆分 header 与 body（空行分隔）
    header_lines, body = [], ""
    if "" in lines:
        idx = lines.index("")
        header_lines = lines[1:idx]
        body = "\n".join(lines[idx + 1:])
    else:
        header_lines = lines[1:]

    headers, cookies = {}, {}
    for h in header_lines:
        if ":" not in h:
            continue
        k, v = h.split(":", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        if k.lower() == "cookie":
            for part in v.split(";"):
                part = part.strip()
                if not part or "=" not in part:
                    continue
                ck, cv = part.split("=", 1)
                ck = ck.strip()
                if not ck:
                    continue
                cookies[ck] = cv.strip()
        else:
            headers[k] = v

    # 从 URL 中抽取 query 参数
    params = {}
    parsed = urlparse(url)
    if parsed.query:
        for k, v in parse_qsl(parsed.query):
            params[k] = v
        url = url.split("?", 1)[0]

    # 推断 body 类型
    body_type = "raw"
    ct = ""
    for k, v in headers.items():
        if k.lower() == "content-type":
            ct = v.lower()
            break
    if "application/json" in ct:
        body_type = "json"
    elif "application/x-www-form-urlencoded" in ct:
        body_type = "form"

    return {
        "method": method,
        "url": url,
        "headers": headers,
        "cookies": cookies,
        "params": params,
        "body": body,
        "body_type": body_type,
    }


def parse_chrome_headers(text: str) -> dict:
    """解析 Chrome 开发者工具 Headers 面板复制的文本。

    格式为每行一个 key，下一行是其 value，例如：
        Request URL
        https://...
        accept
        application/json
        cookie
        a=1; b=2
    """
    lines = [l.rstrip() for l in text.replace("\r\n", "\n").split("\n") if l.strip() != ""]

    # 按 (key, value) 两两配对
    pairs: dict[str, str] = {}
    request_url = None
    request_method = "POST"
    i = 0
    while i < len(lines) - 1:
        key = lines[i].strip()
        val = lines[i + 1].strip()
        if key == "Request URL":
            request_url = val
        elif key == "Request Method":
            request_method = val
        else:
            pairs[key] = val
        i += 2

    # 响应头（需跳过，只保留请求头）
    skip = {
        "status code", "remote address", "referrer policy",
        "access-control-allow-headers", "access-control-allow-methods",
        "access-control-allow-origin", "access-control-expose-headers",
        "alt-svc", "cf-cache-status", "cf-ray", "content-encoding",
        "country", "date", "ip", "nel", "report-to", "server",
        "strict-transport-security", "vary", "x-cache", "x-powered-by",
        "set-cookie", "content-length",  # 响应/自动计算，不手动设
    }

    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    for k, v in pairs.items():
        kl = k.lower()
        if kl in skip:
            continue
        if k.startswith(":"):  # HTTP/2 伪头（:authority/:method 等），httpx 从 URL 自动处理
            continue
        if kl == "cookie":
            for part in v.split(";"):
                part = part.strip()
                if "=" in part:
                    ck, cv = part.split("=", 1)
                    if ck.strip():
                        cookies[ck.strip()] = cv.strip()
            continue
        headers[k] = v

    url = request_url
    params = {}
    if url:
        parsed = urlparse(url)
        if parsed.query:
            for k, v in parse_qsl(parsed.query):
                params[k] = v
            url = url.split("?", 1)[0]

    body_type = "raw"
    ct = ""
    for k, v in headers.items():
        if k.lower() == "content-type":
            ct = v.lower()
            break
    if "application/json" in ct:
        body_type = "json"
    elif "application/x-www-form-urlencoded" in ct:
        body_type = "form"

    return {
        "method": request_method,
        "url": url,
        "headers": headers,
        "cookies": cookies,
        "params": params,
        "body": "",
        "body_type": body_type,
    }


def parse_curl(text: str) -> dict:
    """极简 curl 解析：支持 -X 方法、-H 头、--data/--data-raw body、URL。"""
    method, url, body = "GET", "", ""
    headers, cookies = {}, {}

    # URL（第一个非 - 开头的 token，或紧接 curl 的）
    url_match = re.search(r"curl\s+['\"]?(\S+?)['\"]?\s", text)
    if url_match:
        url = url_match.group(1)

    for m in re.finditer(r"-X\s+(\w+)", text):
        method = m.group(1).upper()
    for m in re.finditer(r"-H\s+['\"]([^'\"]+)['\"]", text):
        kv = m.group(1)
        if ":" in kv:
            k, v = kv.split(":", 1)
            k, v = k.strip(), v.strip()
            if k.lower() == "cookie":
                for part in v.split(";"):
                    if "=" in part:
                        ck, cv = part.split("=", 1)
                        cookies[ck.strip()] = cv.strip()
            else:
                headers[k] = v
    dm = re.search(r"--data(?:-raw|-binary)?\s+['\"]([^'\"]+)['\"]", text)
    if dm:
        body = dm.group(1)
        method = method if method != "GET" else "POST"
        if "application/json" in headers.get("Content-Type", "").lower():
            body_type = "json"
        else:
            body_type = "form"
    else:
        body_type = "raw"

    params = {}
    parsed = urlparse(url)
    if parsed.query:
        for k, v in parse_qsl(parsed.query):
            params[k] = v
        url = url.split("?", 1)[0]

    return {
        "method": method,
        "url": url,
        "headers": headers,
        "cookies": cookies,
        "params": params,
        "body": body,
        "body_type": body_type,
    }
