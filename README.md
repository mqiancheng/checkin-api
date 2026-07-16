# 签到助手 (Checkin API WebUI)

一个通用的「单条 API 签到/登录」自动化平台。你只需粘贴从 Fiddler / 浏览器开发者工具
复制出的原始请求（RAW），系统自动拆分成 headers / cookies / params / body，并支持：

- ⏰ 自定义执行时间 + 随机延时（防封、防同一秒打爆）
- ✅ 自定义「成功判定条件」（路径 + 运算符 + 期望值，支持 AND/OR 任意组合）
- 📋 自定义「日志展示字段」（从返回 JSON 按路径提取，显示成你想要的文案）
- 🔍 每次执行**无论成败都保存完整 JSON**，方便调试
- 📣 企业微信机器人通知（全局开关）
- 🐳 单容器 Docker 部署，前端构建进镜像，运行时零下载

## 目录结构

```
checkin-api/
├─ app/                 # 后端 (FastAPI + SQLAlchemy + APScheduler)
│  ├─ main.py           # 入口 + API 路由 + 托管前端静态文件
│  ├─ models.py         # 数据模型 (Task / RunLog / Setting)
│  ├─ raw_parser.py     # RAW / curl 解析
│  ├─ condition.py      # 条件求值引擎（核心）
│  ├─ executor.py       # 任务执行：发请求→判定→提取→记日志→通知
│  ├─ scheduler.py      # APScheduler 调度
│  ├─ notify.py         # 企业微信通知
│  └─ static/           # 前端构建产物（由 Docker/本地构建生成）
├─ frontend/            # 前端 (Vue 3 + Vite + Element Plus)
├─ Dockerfile
├─ docker-compose.yml
└─ requirements.txt
```

## 本地开发

后端（开发时运行在 8000，供前端 dev server 代理调用）：
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# API 文档: http://localhost:8000/docs
```

前端（另开终端，开发服务器端口 40000）：
```bash
cd frontend
npm install
npm run dev        # http://localhost:40000 （/api 已代理到后端 8000）
```

> 生产/ Docker 下只需暴露一个端口 **40000**：后端同时托管 UI 与 API。

## 部署（拉取预构建镜像）

镜像由 GitHub Actions 手动构建并推送到 `ghcr.io/mqiancheng/checkin-api:latest`，直接拉取运行即可，无需本地构建。

```bash
docker compose up -d
# 访问 http://<服务器IP>:40000   （UI 与 API 同端口）
```

- `docker-compose.yml` 已设 `pull_policy: always`，每次 `up` 都会拉取最新镜像。
- 更新镜像：先在 GitHub `Actions` → `Build Docker Image` 手动触发构建，再 `docker compose up -d` 拉取新版。
- 如需本地从源码构建：`docker compose up -d --build`（需把 `image:` 改回 `build: .`）。

数据保存在挂载卷 `./data/app.db`（SQLite），重启不丢。

## 使用流程

1. 仪表盘 → 新建任务
2. 「请求 (RAW 解析)」页：粘贴原始请求 → 点「解析 RAW」自动填充
3. 「调度」页：设置每天执行时间 + 随机延时
4. 「成功判定」页：添加条件，如 `status = success` 且 `statusCode = 200`
5. 「日志展示」页：添加要看到的返回字段，如 `message → 消息`、`data.sign_count → 签到积分`
6. 保存 → 可在仪表盘「立即执行」测试，结果在「运行日志」查看（含完整 JSON）

## 条件运算符

`eq`(等于) / `ne`(不等于) / `contains`(包含) / `exists`(存在) /
`gt` / `ge` / `lt` / `le`(数值比较) / `in`(在列表中)

值类型可选 `auto`(自动识别数字/字符串) / `str` / `num` / `bool`。

## 应对 Cloudflare（两种执行方式）

任务可配置两种执行方式（编辑任务 → 「执行方式」下拉）：

| 场景 | 执行方式 | CF Bypass |
|------|----------|-----------|
| 普通站点 | HTTP（默认） | auto（默认，被拦才调） |
| 普通 JS 盾（freecloud 类） | HTTP | on / auto |
| Managed Challenge（vikacg 类，clearance 绑浏览器指纹） | **浏览器内执行** | 不需要 |

### 浏览器内执行（camoufox）
- 用 camoufox 反检测 Firefox 在页面上下文内执行 API，等价于在 F12 Console 里敲 `fetch`；浏览器自己过 CF，无需手动处理 `cf_clearance`。
- RAW 中的 Cookie / Headers 会自动注入浏览器上下文。
- 镜像已包含 Firefox 系统依赖，构建时会下载 camoufox 内置 Firefox（约 +150MB）。

### CF Bypass（普通 JS 盾站点）
在「全局设置」填写 NAS 上 CloudflareBypassForScraping 服务的地址（如 `http://192.168.6.100:10000/<你的CFB密码>/cookies`）。
- `auto`（默认）：仅当请求被 CF 拦截时才调用，按域名缓存 `cf_clearance`（默认 12h），缓存失效自动刷新重试。
- `on`：强制注入 clearance。
- `off`：完全不调用。

> 注意：`cf_clearance` 按域名独立签发，**不能跨站通用**；且 Managed Challenge 站点的 clearance 与浏览器指纹绑定，程序无法复用——这类站点必须用「浏览器内执行」，CF Bypass 救不了。

## 数据库升级
旧库升级时 `init_db` 会自动给 `tasks` / `settings` 表 ALTER 补加新列，无需手动迁移。

## 手动构建 Docker 镜像（GitHub Actions）
仓库内置 `.github/workflows/build.yml`，**仅手动触发**（`Actions` 页 → `Build Docker Image` → `Run workflow`），不会因普通 push 自动构建。
镜像推送到 `ghcr.io/<仓库名>:<tag>`（默认 tag 为 `latest`）。
如需推 Docker Hub，修改 workflow 中的 `registry` 与 `tags` 并配置对应 `secrets`。
