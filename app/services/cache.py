"""缓存层：优先使用华为云 DCS for Redis，未配置时降级为进程内字典缓存。

缓存只用于加速"同一通知重复解析"与"问答结果"，不承担数据持久化职责，
因此降级不会影响业务正确性。
"""
from __future__ import annotations

import json
import logging
import time

from app.config import settings

logger = logging.getLogger("zhixun.cache")


class _MemoryCache:
    kind = "内存缓存（单进程）"

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if not item:
            return None
        expire_at, value = item
        if expire_at and expire_at < time.time():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str, ttl: int = 3600) -> None:
        self._data[key] = (time.time() + ttl if ttl else 0, value)

    def stats(self) -> dict:
        return {"kind": self.kind, "keys": len(self._data)}


class _RedisCache:
    kind = "Redis（华为云 DCS）"

    def __init__(self, url: str) -> None:
        import redis  # 延迟导入，避免未安装时影响启动

        self._client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
            max_connections=20,
            retry_on_timeout=True,
        )
        self._client.ping()

    def get(self, key: str) -> str | None:
        return self._client.get(key)

    def set(self, key: str, value: str, ttl: int = 3600) -> None:
        self._client.set(key, value, ex=ttl or None)

    def stats(self) -> dict:
        return {"kind": self.kind, "keys": self._client.dbsize()}


def _build():
    if settings.REDIS_URL:
        try:
            return _RedisCache(settings.REDIS_URL)
        except Exception as exc:
            logger.warning("Redis 连接失败，降级为内存缓存：%s", exc)
    return _MemoryCache()


class Cache:
    def __init__(self) -> None:
        self._impl = _build()

    def get_json(self, key: str):
        try:
            raw = self._impl.get(key)
        except Exception as exc:  # 云端 Redis 断连时按未命中处理，业务继续
            logger.warning("读缓存失败，按未命中处理：%s", exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def set_json(self, key: str, value, ttl: int = 3600) -> None:
        try:
            self._impl.set(key, json.dumps(value, ensure_ascii=False), ttl)
        except Exception as exc:  # 缓存失败绝不影响主流程
            logger.debug("写缓存失败：%s", exc)

    def stats(self) -> dict:
        try:
            return self._impl.stats()
        except Exception as exc:  # 状态接口不能因为缓存异常整体 500
            logger.warning("缓存状态读取失败：%s", exc)
            return {"kind": self.kind, "keys": -1}

    @property
    def kind(self) -> str:
        return self._impl.kind


cache = Cache()
