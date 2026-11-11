# 预览镜像：仅用于看板预览部署，运行已构建前端与只读演示数据。
# 生产启动方式见 README.md（./start.sh 与 deploy/ 下方案）。
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "fastapi==0.162.0" "uvicorn==0.54.0" "requests>=2.32,<3" \
    "beautifulsoup4>=4.12,<5" "urllib3" "pandas>=2.2,<3"
COPY backend ./backend
COPY frontend/dist ./frontend/dist
COPY runtime/market.sqlite3 ./runtime/market.sqlite3
ENV SECTOR_DISABLE_COLLECTOR=1 SECTOR_DISABLE_NEWS=1
EXPOSE 8788
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8788}"]
