# LcvSearch 后端 Dockerfile
# Django + Scrapy + Playwright
FROM python:3.12-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    # Playwright 依赖
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 安装 Python 依赖
COPY LcvSearch/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装 Playwright 浏览器
RUN playwright install chromium

# 复制项目代码
COPY LcvSearch/ .

# 创建必要目录
RUN mkdir -p cache/images jobs logs

# 暴露端口
EXPOSE 8000

# 健康检查（python:3.12-slim 不带 curl，用 python 标准库探测）
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/stats/', timeout=5)" || exit 1

# 启动命令
# 先 migrate，再启动 gunicorn（生产环境）
# 注意：CrawlManager/ScheduleManager 是进程内单例（任务互斥、定时调度都依赖它），
# 必须 --workers 1；多 worker 会导致状态互不可见、定时任务重复触发。
# 需要横向扩容时先把任务状态外置到 Redis/DB，再增加 worker。
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn LcvSearch.wsgi:application --bind 0.0.0.0:8000 --workers 1 --timeout 120"]
