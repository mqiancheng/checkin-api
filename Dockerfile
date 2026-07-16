# ---- 阶段1：构建前端 ----
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
# 构建产物输出到 /app/static（见 vite.config.js 的 outDir）
RUN npm run build

# ---- 阶段2：后端运行 ----
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
# 系统库：CloakBrowser 隐身 Chromium 运行所需（含 Xvfb 虚拟显示，供有头模式使用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 libx11-6 libxext6 libxshmfence1 libglib2.0-0 libdbus-glib-1-2 \
    libasound2 libxrender1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxi6 libxtst6 libnss3 libcups2 fonts-liberation libgbm1 libxkbcommon0 \
    libx11-xcb1 libxrandr2 libxss1 libatk-bridge2.0-0 x11-apps \
    libappindicator3-1 libu2f-udev libvulkan1 libdrm2 xdg-utils xvfb \
    libcurl4 gnupg ca-certificates \
    libnspr4 libatk1.0-0 libpango-1.0-0 libcairo2 libatspi2.0-0 libxfixes3 \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
# 下载 CloakBrowser 隐身 Chromium（CFBypass v2 端点需要；无网络时构建不阻断，运行时首次启动会自动拉取）
RUN python -c "import cloakbrowser; cloakbrowser.ensure_binary()" || true
# 下载 camoufox 内置 Firefox（camoufox 反检测模式需要；无网络时构建不阻断）
RUN python -m camoufox fetch || true
COPY app/ ./app/
# 把构建好的前端静态文件拷进后端 static 目录，运行时由 FastAPI 托管
COPY --from=frontend /app/static ./app/static
COPY start.sh /start.sh
RUN chmod +x /start.sh

# 容器内 CFBypass 端点固定 10000；checkin-api 主服务固定 40000
ENV DB_PATH=/data/app.db
ENV SERVER_PORT=10000
ENV HEADLESS=false
VOLUME ["/data"]
EXPOSE 40000 10000
CMD ["/start.sh"]
