FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies are installed before the source is copied so the layer is cached.
# The pinned set (aiogram 3.4.1 -> aiohttp 3.9.x, pydantic-core 2.14.x) ships
# prebuilt wheels for CPython 3.12 only; on 3.13+ pip falls back to building from
# source and the build fails.
COPY requirements.txt ./
RUN pip install --timeout 120 --retries 8 -r requirements.txt

COPY . .

CMD ["python", "main.py"]