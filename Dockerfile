# 外端Agent生产合作社（External Agent Community）· 容器镜像
#
# 为什么有它（老板 2026-10-14 的"可移植"判据）：
#    **在陌生 Linux 容器里只改环境变量即可运行** —— 这是最硬的验收方式。
# 一切环境差异都走环境变量（见 .env.example / PORTABILITY.md）；镜像里**不含任何本机坐标**。
FROM python:3.11-slim

# 运行期最小依赖：curl 便于健康检查排障（可选）
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY agent_community/ ./agent_community/
COPY tests/ ./tests/
COPY start.py pytest.ini ./

# 可移植性：不写死端口；数据落 /data（请挂卷）
ENV AC_PORT=18920 \
    AC_HOST=0.0.0.0 \
    AC_DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

VOLUME ["/data"]
EXPOSE 18920

# 健康检查（判据与 README 一致：只有 /api/status 返回 running 才算健康）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json,os,urllib.request; p=os.environ.get('AC_PORT','18920'); \
print(json.load(urllib.request.urlopen('http://127.0.0.1:'+p+'/api/status'))['status'])" | grep -q running

# 起服务：只读命令行开关，端口/地址/数据目录都来自环境变量
CMD ["python", "-m", "agent_community.platform.server"]
