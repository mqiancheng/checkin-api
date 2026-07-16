#!/bin/bash
# 合并容器启动脚本：同时拉起 checkin-api 主服务(40000) 与 CFBypass 端点(10000)
set -e

# CFBypass 使用有头模式 + Xvfb 虚拟显示（与原项目配置一致，最稳）
Xvfb :99 -screen 0 1024x768x24 >/dev/null 2>&1 &
export DISPLAY=:99

# 主服务：签到助手 WebUI + API
uvicorn app.main:app --host 0.0.0.0 --port 40000 &

# CFBypass 端点：对外提供 /{password}/cookies、/{password}/turnstile
# 端口可由 CFB_PORT 环境变量覆盖（默认 10000），需与 docker-compose 端口映射、WebUI 自动识别保持一致
CFB_PORT=${CFB_PORT:-10000}
uvicorn app.cfbypass.server:app --host 0.0.0.0 --port $CFB_PORT &

wait
