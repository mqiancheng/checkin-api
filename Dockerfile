# ---- 阶段1：构建前端 ----
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
# 构建产物输出到 /app/static（见 vite.config.js 的 outDir）
RUN npm run build

# ---- 阶段2：后端运行 ----
# 构建参数：是否启用 browser/chrome 模式（默认关闭，大幅缩小镜像）
ARG WITH_BROWSER=0
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./

# 始终安装 curl_cffi（HTTP 模式的核心依赖）
RUN pip install --no-cache-dir fastapi uvicorn[standard] sqlalchemy httpx curl_cffi apscheduler pydantic

# 仅当 WITH_BROWSER=1 时才安装浏览器相关依赖
COPY requirements.txt ./requirements.txt
RUN if [ "$WITH_BROWSER" = "1" ]; then \
        # camoufox（反检测 Firefox）所需的系统库
        apt-get update && apt-get install -y --no-install-recommends \
            libgtk-3-0 libx11-6 libxext6 libxshmfence1 libglib2.0-0 \
            libdbus-glib-1-2 libasound2 libxrender1 libxcb1 libxcomposite1 \
            libxcursor1 libxdamage1 libxi6 libxtst6 libnss3 libcups2 \
            fonts-liberation libgbm1 libxkbcommon0 \
        && rm -rf /var/lib/apt/lists/* \
        && pip install --no-cache-dir camoufox \
        && python -m camoufox fetch || true; \
    fi
COPY app/ ./app/
# 把构建好的前端静态文件拷进后端 static 目录，运行时由 FastAPI 托管
COPY --from=frontend /app/static ./app/static

ENV DB_PATH=/data/app.db
VOLUME ["/data"]
EXPOSE 40000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 40000"]
