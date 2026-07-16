# 重构方案：统一浏览器内核到 CloakBrowser

> 目标：把所有浏览器相关能力（cfbypass 过盾、browser 模式页面内 fetch、chrome 模式）
> 全部统一到 **CloakBrowser（隐身 Chromium）**，删除 camoufox / DrissionPage / 本地 chrome / playwright 依赖。
>
> 原则：**每个阶段独立可验证、可回滚**，不一次性大爆炸重构。

---

## 一、现状盘点（截至本方案）

### 已落地（上次被打断前已完成）
- ✅ `app/cfbypass/server.py` 已重写为 CloakBrowser（v2 端点 `/{password}/cookies`、`/{password}/turnstile`，有头 + Xvfb + `--no-sandbox`）
- ✅ `requirements.txt` 已加 `cloakbrowser`
- ✅ `Dockerfile` 已加 cloakbrowser 系统库 + `cloakbrowser.ensure_binary()` 下载步骤
- ✅ `app/cfbypass/cf_bypasser/` 已 vendoring 新版包（含 `core/bypasser.py`、`utils/`、`cache/`）

### 未落地（待本次重构）
- ❌ `executor.py` 的 `_send_browser`（camoufox，Firefox）完全没动
- ❌ `executor.py` 的 `_send_chrome`（本地 chrome / playwright）完全没动
- ❌ 第 0 步：cfbypass v2 在容器内过 nopecha **尚未验证**
- ❌ `requirements.txt` 仍含 `camoufox`、`DrissionPage`、`PyVirtualDisplay`
- ❌ `Dockerfile` 仍含 `python -m camoufox fetch`

---

## 二、目标与收益

| 项 | 现状 | 重构后 |
|----|------|--------|
| 浏览器内核 | camoufox(Firefox) + 本地 chrome + DrissionPage + CloakBrowser 四套 | **仅 CloakBrowser 一套** |
| browser 模式过 Managed Challenge | camoufox 可过，但偶发 pageerror 驱动崩溃 | CloakBrowser 隐身 Chromium，过盾更稳 |
| cfbypass 拿的 cf_clearance | 与指纹绑定，注入 httpx 对 Managed Challenge 失效 | 同（这是 CF 机制，非 bug），但 browser 模式走页面内 fetch 自洽 |
| 镜像体积 | 含 Firefox(~150MB) + Chrome 系统库 | 删 Firefox，瘦身 |
| cookie 缓存 | cfbypass 服务内一份 | 统一一份，browser 模式也复用 |

---

## 三、架构决策：browser 模式的 CloakBrowser 放哪？

两个服务同容器（40000 主 + 10000 cfbypass），Xvfb 共享。

**推荐方案 A（采用）：browser 模式也走 cfbypass 服务**
- 在 cfbypass 服务新增 `POST /{password}/exec` 端点：接收 `url/method/headers/params/body/cookies`，在 CloakBrowser 页面内 `fetch`，返回 `{status, body}`。
- executor 的 `_send_browser` 改为 **HTTP 调用 cfbypass `/exec`**（和现在 http 模式调 `/cookies` 同一套路）。
- 收益：单一内核、单一进程、共享 cookie 缓存、**executor 零浏览器依赖**（可彻底删 camoufox/playwright）。

**备选方案 B（不采用）：executor 进程内 import CloakBypasser**
- 改动小，但主服务也要 launch 浏览器，与 cfbypass 服务重复，缓存不共享，且主服务也要有 Xvfb 环境。

> 结论：采用方案 A。executor 侧 browser 模式从"进程内同步 camoufox"变为"HTTP 调 cfbypass 服务"，async 只在 cfbypass 服务内部，executor 保持同步、无 async 桥接负担。

---

## 四、分阶段实施

### 阶段 0：验证 cfbypass v2 在容器内过 nopecha（前提，必须先做）
- **改动**：无（server.py 已写好）
- **动作**：
  1. 构建 `:test` 镜像（`cloakbrowser.ensure_binary()` 已下载隐身 Chromium）
  2. 跑 nopecha 任务（HTTP 模式 + auto/on），观察 cfbypass 服务日志
- **验证目标**：
  - CloakBrowser 能在容器内 `launch_context_async`（有头 + Xvfb + `--no-sandbox`）正常启动
  - 能过 CF 盾、返回 cookie 字典
  - ⚠️ **注意**：nopecha 是 Managed Challenge，其 `cf_clearance` 注入 httpx 后仍可能无效（指纹绑定，这是预期，非 bug）。阶段 0 只验证"内核可用"，不要求 http 模式成功过 nopecha。
- **回滚**：无代码改动，重构建旧镜像即可。

### 阶段 1：browser 模式从 camoufox 迁移到 CloakBrowser
- **改动**：
  1. `app/cfbypass/server.py` 新增 `POST /{password}/exec` 端点：
     - 入参：`{url, method, headers, params, body, cookies}`
     - 复用 `CloakBypasser._run_in_browser` 骨架 + 自定义 extractor：
       - `add_cookies`（任务 cookie + 请求头 Cookie 合并）
       - `page.route` 拦截跨站资源（同 `_route` 逻辑）
       - `goto(origin)` → `solve_cloudflare_challenge`（等 CF 放行）
       - 页面内 `fetch(full_url, {method, headers, body})` → 返回 `{status, text}`
  2. `executor.py` `_send_browser` 改为 HTTP 调 cfbypass `/exec`（替代进程内 camoufox）
  3. 删 camoufox 依赖：`requirements.txt` 去 `camoufox`；`Dockerfile` 去 `python -m camoufox fetch`；`executor.py` 去 `HAS_CAMOUFOX` 分支
- **验证**：
  - nopecha（Managed Challenge）：browser 模式应过盾拿到 200
  - 普通 CF 站：browser 模式正常
- **回滚**：`git revert` 阶段 1 提交。

### 阶段 2：chrome 模式合并/废弃
- **改动**：
  1. `executor.py` `_send_chrome` 直接复用 `_send_browser`（CloakBrowser 路径）
  2. 删 `HAS_PLAYWRIGHT`、`_get_chrome_path`、`CHROME_PATH` 逻辑
- **验证**：原 chrome 模式任务改用 browser 模式跑通。
- **回滚**：`git revert`。

### 阶段 3：清理收口
- **改动**：
  1. `requirements.txt` 删 `DrissionPage`、`PyVirtualDisplay`（Xvfb 仍由系统装，但 Python 包可去）、`playwright`（若仅 camoufox 用）
  2. `Dockerfile` 精简 Firefox 相关系统库（如确定不再需要）
  3. `README.md` 更新"浏览器内执行"章节（camoufox → CloakBrowser）
  4. `main.py` / `models.py` 中 `executor_type` 枚举：`browser` / `chrome` 合并说明
- **验证**：镜像体积对比（应明显小于旧版）；全量回归 4 种模式（http auto/on/off + browser）。
- **回滚**：`git revert`。

---

## 五、风险与缓解

| 风险 | 缓解 |
|------|------|
| CloakBrowser 有头模式在容器里 launch 失败 | 阶段 0 先验证；失败则排查 Xvfb / `--no-sandbox` / 系统库缺失 |
| nopecha 在 http 模式仍"拿不到可用 clearance" | 这是 CF 指纹绑定机制，**预期行为**；正确解法是 browser 模式（阶段 1），不要误判为 bug |
| executor 调 cfbypass `/exec` 超时/崩溃 | `/exec` 内部用 `asyncio.wait_for` + `cleanup_browser` 兜底（复用 bypasser 既有保护）；executor 侧加超时与错误回退 |
| 删依赖后其他代码引用报错 | 每阶段 `py_compile` + 构建验证；逐个删，不批量 |
| Managed Challenge 页面内 fetch 也失败 | CloakBypasser 已有 `solve_cloudflare_challenge` + Turnstile 点选，复用即可 |

---

## 六、执行顺序与确认点

```
阶段0 验证内核 ──(通过)──> 阶段1 browser迁移 ──(通过)──> 阶段2 chrome合并 ──(通过)──> 阶段3 清理
                                │                                          │
                                └──────────── 任一步失败则 git revert ──────┘
```

**下一步**：先执行阶段 0（构建 `:test` 镜像验证 nopecha）。确认内核可用后，我再开始阶段 1 的具体代码改动。
