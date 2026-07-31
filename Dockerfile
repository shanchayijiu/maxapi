FROM python:3.13-slim
WORKDIR /app
COPY maxapi_server.py /app/maxapi_server.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "maxapi_server.py", "--host", "0.0.0.0", "--port", "8080"]
