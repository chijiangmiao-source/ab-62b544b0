FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /srv

# 应用仅依赖 Python 标准库；复制源码、测试与检验脚本
COPY app/ ./app/
COPY tests/ ./tests/
COPY scripts/ ./scripts/
RUN chmod +x scripts/verify.sh scripts/http_smoke.py

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=5 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','8080')+'/healthz', timeout=2).status == 200 else 1)"

CMD ["python3", "-m", "app.server"]
