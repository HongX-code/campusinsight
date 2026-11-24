"""大模型调用适配层：统一 OpenAI 兼容协议，可对接华为云 ModelArts Studio（MaaS）。

关键设计：模型可替换。业务层只依赖 LLMClient.chat()，不关心底层是哪家模型；
未配置密钥时 available 为 False，业务层自动降级到本地规则引擎。

可靠性约定：
    · 连接复用：单进程共用一个 httpx.Client，避免每次请求重新建连；
    · 失败冷却：连续失败后短暂熔断，冷却期内直接抛错交给规则引擎，
      不让每一份通知都卡满超时时间；
    · 错误分类：把鉴权 / 限流 / 超时 / 网络 / 服务端错误区分开，
      前端能直接看到「为什么降级」，而不是一句笼统的「调用失败」。
"""
from __future__ import annotations

import json
import logging
import threading
import time

import httpx

from app.config import settings

logger = logging.getLogger("zhixun.llm")

# 连续失败后的冷却阶梯（秒）。一次网络抖动不应长时间关闭模型能力，
# 只有连续失败才逐级拉长冷却。
_COOLDOWN_STEPS = (10, 30, 120)


class LLMError(RuntimeError):
    """模型不可用或返回异常；调用方据此降级到规则引擎。"""


def _classify(status: int | None, exc: Exception | None, timeout: float) -> tuple[str, str]:
    """把底层异常翻译成「类别 + 可读提示」，供前端与复核说明直接展示。"""
    if status == 401 or status == 403:
        return "auth", "鉴权失败：请检查 API Key 是否有效、是否已开通该模型权限"
    if status == 404:
        return "endpoint", "接口或模型不存在：请检查 LLM_BASE_URL 与 LLM_MODEL"
    if status == 429:
        return "rate_limit", "调用频率或配额受限（HTTP 429），建议稍后重试或切换模型"
    if status is not None and status >= 500:
        return "server", f"模型服务端异常（HTTP {status}）"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", f"请求超时（>{timeout:.0f}s），已降级为规则引擎"
    if isinstance(exc, httpx.ConnectError):
        return "network", "无法连接模型服务：请检查网络、代理与 base_url"
    if isinstance(exc, (httpx.HTTPError, OSError)):
        return "network", f"网络异常：{type(exc).__name__}"
    return "unknown", f"模型调用异常：{type(exc).__name__}"


class LLMClient:
    def __init__(self) -> None:
        self.base_url = settings.LLM_BASE_URL.rstrip("/")
        self.api_key = settings.LLM_API_KEY
        self.model = settings.LLM_MODEL
        self.provider = settings.LLM_PROVIDER
        self.timeout = settings.LLM_TIMEOUT

        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        self._fail_count = 0
        self._cooldown_until = 0.0
        self._last_error = ""
        self._last_error_kind = ""
        self._last_error_hint = ""
        self._last_latency_ms = 0
        # 连通性探测单独记账：探测不应改变业务调用的熔断状态
        self._last_probe_ok: bool | None = None
        self._last_probe_ms = 0
        self._last_probe_error = ""

    @property
    def available(self) -> bool:
        """配置层面是否可用（有密钥且不是规则引擎）。"""
        return bool(self.api_key) and self.provider not in ("", "none", "rule")

    @property
    def cooldown_remaining(self) -> int:
        return max(0, int(round(self._cooldown_until - time.time())))

    @property
    def ready(self) -> bool:
        """可立即尝试调用：既完成配置，又不在冷却期。"""
        return self.available and self.cooldown_remaining <= 0

    @property
    def last_error(self) -> str:
        return self._last_error

    def describe(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "available": self.available,
            "ready": self.ready,
            "cooldown_remaining": self.cooldown_remaining,
            "fail_count": self._fail_count,
            "last_error": self._last_error,
            "last_error_kind": self._last_error_kind,
            "last_error_hint": self._last_error_hint,
            "last_latency_ms": self._last_latency_ms,
            "last_probe_ok": self._last_probe_ok,
            "last_probe_ms": self._last_probe_ms,
            "last_probe_error": self._last_probe_error,
        }

    # ---------------- 连接与状态 ----------------
    def _get_client(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(
                    timeout=httpx.Timeout(self.timeout),
                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                )
            return self._client

    def _record_success(self, started: float) -> None:
        with self._lock:
            self._fail_count = 0
            self._cooldown_until = 0.0
            self._last_error = ""
            self._last_error_kind = ""
            self._last_error_hint = ""
            self._last_latency_ms = int((time.perf_counter() - started) * 1000)

    def _trip(self, kind: str, hint: str, detail: str) -> None:
        with self._lock:
            self._fail_count += 1
            step = _COOLDOWN_STEPS[min(self._fail_count, len(_COOLDOWN_STEPS)) - 1]
            self._cooldown_until = time.time() + step
            self._last_error = detail[:200]
            self._last_error_kind = kind
            self._last_error_hint = hint

    def _record_protocol_error(self, detail: str) -> None:
        """模型返回了非法 JSON：网络是通的，但输出契约被破坏。

        这类失败如果不记账，界面会一直显示「大模型已就绪」，而实际每次
        都在悄悄降级；连续失败也不会触发冷却，会反复等待超时并重复计费。
        """
        self._trip("protocol", "模型输出不是合法 JSON，已降级为规则引擎", detail)

    # ---------------- 核心调用 ----------------
    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 1400,
        timeout: float | None = None,
    ) -> str:
        if not self.available:
            raise LLMError("未配置大模型密钥，已降级为规则引擎")

        remaining = self.cooldown_remaining
        if remaining > 0:
            raise LLMError(
                f"模型冷却中（剩余 {remaining}s）：{self._last_error_hint or self._last_error}"
            )

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        timeout_s = float(timeout or self.timeout)
        kind, hint, detail = "unknown", "", ""
        tripped = False

        for attempt in range(2):
            started = time.perf_counter()
            try:
                resp = self._get_client().post(url, json=payload, headers=headers, timeout=timeout_s)
                if resp.status_code == 400 and "response_format" in payload:
                    # 部分兼容接口不识别 json_object，去掉后重试一次（不计入失败次数）
                    payload.pop("response_format", None)
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                if not content:
                    raise LLMError("模型返回内容为空")
                self._record_success(started)
                return content
            except httpx.HTTPStatusError as exc:
                kind, hint = _classify(exc.response.status_code, exc, timeout_s)
                detail = f"HTTP {exc.response.status_code}: {exc.response.text[:160]}"
                # 鉴权 / 模型不存在 / 请求格式错误重试也不会好，直接退出
                if exc.response.status_code in (400, 401, 403, 404):
                    self._trip(kind, hint, detail)
                    tripped = True
                    break
            except LLMError as exc:
                kind, hint, detail = "empty", "模型返回内容为空", str(exc)
            except Exception as exc:
                kind, hint = _classify(None, exc, timeout_s)
                detail = f"{type(exc).__name__}: {exc}"

            if attempt == 0:
                time.sleep(0.6)

        if not tripped:
            self._trip(kind, hint, detail)

        logger.warning("大模型调用失败：%s %s", self._last_error_kind, self._last_error)
        raise LLMError(f"{hint}：{self._last_error}" if self._last_error else hint)

    def chat_json(self, messages: list[dict], **kwargs) -> dict:
        raw = self.chat(messages, json_mode=True, **kwargs)
        try:
            return parse_json_loose(raw)
        except LLMError as exc:
            self._record_protocol_error(str(exc))
            raise

    def probe(self) -> dict:
        """手动连通性探测：只读检查，不改变业务熔断状态，不返回任何密钥。

        「测试连接」是诊断动作，不该有副作用：成功时若清空 fail_count，
        会把真实的连续故障提前解除；失败时若触发冷却，又会让本来正常的
        业务解析白白降级。这里先快照熔断状态，探测结束后完整恢复。
        """
        started = time.perf_counter()
        if not self.available:
            return {
                "ok": False,
                "engine": "rule",
                "latency_ms": 0,
                "model": self.model,
                "reason": "未配置大模型密钥，内置规则引擎可用（离线兜底）",
                "error_kind": "not_configured",
            }
        with self._lock:
            saved = (self._fail_count, self._cooldown_until, self._last_error,
                     self._last_error_kind, self._last_error_hint, self._last_latency_ms)
        ok, reason, kind = False, "", "unknown"
        try:
            self.chat([{"role": "user", "content": "ping"}], temperature=0, max_tokens=8,
                      timeout=min(self.timeout, 12))
            ok, reason = True, "模型连通正常"
        except Exception as exc:
            reason, kind = str(exc)[:200], (self._last_error_kind or "unknown")
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)
            with self._lock:
                (self._fail_count, self._cooldown_until, self._last_error,
                 self._last_error_kind, self._last_error_hint, self._last_latency_ms) = saved
                self._last_probe_ok, self._last_probe_ms = ok, elapsed
                self._last_probe_error = "" if ok else reason
        out = {
            "ok": ok,
            "engine": "llm" if ok else "rule",
            "latency_ms": elapsed,
            "model": self.model,
            "provider": self.provider,
            "reason": reason,
        }
        if not ok:
            out.update(error_kind=kind, cooldown_remaining=self.cooldown_remaining)
        return out


def _strip_fence(raw: str) -> str:
    """去掉 ```json ... ``` 代码围栏。"""
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("```")]
    return "\n".join(lines).strip()


def parse_json_loose(raw: str) -> dict:
    """容错解析：模型常把 JSON 包在代码块里，或在前后附加解释文字。"""
    raw = _strip_fence((raw or "").strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError("模型输出不是合法 JSON")


llm = LLMClient()