FROM python:3.13-slim
WORKDIR /app
COPY maxapi_server.py /app/maxapi_server.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
# 编排友好: 30s 后开始探测, 失败 3 次才标记 unhealthy (90s 容忍冷启动)
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz').status==200 else 1)" || exit 1
CMD ["python", "maxapi_server.py", "--host", "0.0.0.0", "--port", "8080"]
