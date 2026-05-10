"""根據使用者設定建立 LLM client（支援 Groq / Gemini / Ollama / OpenRouter）。"""
import asyncio
import logging
import time
from typing import Any, Optional

from config import GROQ_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
from settings import get_settings


# Gemini 2.5 系列使用 thinking_budget，3.x 系列使用 thinking_level
_GEMINI_3X_PREFIXES = ("gemini-3-", "gemini-3.", "gemini-3.1")


def _is_gemini_3x(model: str) -> bool:
    return any(model.startswith(p) for p in _GEMINI_3X_PREFIXES)


def build_llm(temperature: float = 0.0) -> Any:
    """依當前設定回傳一個 LangChain chat model 實例。"""
    s = get_settings()
    provider = s["provider"]
    model = s["model"]

    if provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(api_key=GROQ_API_KEY, model=model, temperature=temperature)

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        gem_thinking = s.get("gemini_thinking", "off")
        kwargs: dict[str, Any] = {
            "model": model,
            "google_api_key": GEMINI_API_KEY,
            "temperature": temperature,
            "max_output_tokens": 8192,  # 防止 gemma 等模型無限生成
        }
        # 只有 gemini-2.5 和 gemini-3.x 系列支援思考模式，其他模型（gemma, gemini-2.0）靜默忽略
        supports_thinking = model.startswith("gemini-2.5-") or _is_gemini_3x(model)
        if gem_thinking != "off" and supports_thinking:
            if _is_gemini_3x(model):
                kwargs["thinking_level"] = gem_thinking if gem_thinking != "auto" else "medium"
            else:
                budget_map = {"auto": -1, "low": 1024, "medium": 4096, "high": 16384}
                kwargs["thinking_budget"] = budget_map.get(gem_thinking, -1)
        return ChatGoogleGenerativeAI(**kwargs)

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=OPENAI_API_KEY,
            temperature=temperature,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        # Anthropic 預設沒設 max_tokens 會吃 LangChain 的 1024 default、Claude 4 系列容易截斷；
        # 這裡明確給 8192、跟 Gemini 那邊邏輯一致
        return ChatAnthropic(
            model=model,
            api_key=ANTHROPIC_API_KEY,
            temperature=temperature,
            max_tokens=8192,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        thinking = s.get("ollama_thinking", "off")
        num_ctx = s.get("ollama_num_ctx", 16384)
        kwargs = {
            "model": model,
            "base_url": s.get("ollama_base_url") or "http://localhost:11434",
            "temperature": temperature,
            "num_ctx": num_ctx,
        }
        # auto → 不傳，讓模型自行決定；on/off → 明確開關（對 qwen3 等思考型模型有效）
        if thinking == "on":
            kwargs["reasoning"] = True
        elif thinking == "off":
            kwargs["reasoning"] = False
        return ChatOllama(**kwargs)

    raise ValueError(f"unknown provider: {provider}")


async def invoke_with_streaming(
    llm: Any,
    messages: list,
    *,
    label: str = "LLM",
    timeout: float = 300.0,
    logger: Optional[logging.Logger] = None,
    return_usage: bool = False,
) -> Any:
    """以串流方式呼叫 LLM，附帶進度記錄與硬性超時。

    - 用串流避免 Ollama thinking 模式整包 buffer 造成「看起來卡住」
    - 每 15 秒打一行 log 顯示目前進度（reasoning / content 累積字數）
    - 超過 timeout 秒直接拋 asyncio.TimeoutError
    - 回傳最終 content 字串（reasoning_content 僅消耗，不回傳）

    return_usage=True 時改回 dict {content, usage_metadata, model}，
    供需要算 token / 成本的 caller（subagent_runner）使用。
    向後相容：預設 False 仍回純字串。
    """
    log = logger or logging.getLogger(__name__)
    start = time.time()
    last_log = start
    content_parts: list[str] = []
    reasoning_len = 0
    chunk_count = 0
    final_chunk = None
    # streaming 中 usage 可能 attach 在最後一個 chunk、也可能跨 chunk 增量；累計保險
    acc_um = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    try:
        input_chars = sum(len(str(getattr(m, "content", "") or "")) for m in messages)
        log.info(f"[{label}] 🤖 LLM 開始處理（input {input_chars:,} 字）…")
    except Exception:
        log.info(f"[{label}] 🤖 LLM 開始處理…")

    async def _stream():
        nonlocal last_log, reasoning_len, chunk_count, final_chunk
        async for chunk in llm.astream(messages):
            chunk_count += 1
            final_chunk = chunk
            # 累計 usage_metadata（langchain 0.2+ 標準）— 多數 provider 只在 final chunk 給，累加安全
            um = getattr(chunk, "usage_metadata", None)
            if isinstance(um, dict) and um:
                for k in ("input_tokens", "output_tokens", "total_tokens"):
                    v = um.get(k)
                    if v:
                        acc_um[k] = acc_um.get(k, 0) + int(v)
            c = getattr(chunk, "content", None)
            if c:
                if isinstance(c, list):
                    for block in c:
                        if isinstance(block, dict):
                            t = block.get("text") or ""
                            if t:
                                content_parts.append(t)
                        elif isinstance(block, str):
                            content_parts.append(block)
                else:
                    content_parts.append(str(c))
            extra = getattr(chunk, "additional_kwargs", None) or {}
            rc = extra.get("reasoning_content") or extra.get("reasoning") or ""
            if rc:
                reasoning_len += len(rc) if isinstance(rc, str) else 0
            now = time.time()
            if now - last_log >= 15.0:
                elapsed = now - start
                total = sum(len(p) for p in content_parts)
                if reasoning_len:
                    log.info(
                        f"[{label}] 🧠 思考中… {elapsed:.0f}s（reasoning {reasoning_len} 字, content {total} 字）"
                    )
                else:
                    log.info(
                        f"[{label}] ✍️ 產生中… {elapsed:.0f}s（content {total} 字）"
                    )
                last_log = now

    try:
        await asyncio.wait_for(_stream(), timeout=timeout)
    except asyncio.TimeoutError:
        total = sum(len(p) for p in content_parts)
        log.error(
            f"[{label}] LLM 串流逾時（>{timeout:.0f}s），已收集 reasoning {reasoning_len} 字 / content {total} 字"
        )
        raise

    elapsed = time.time() - start
    total = sum(len(p) for p in content_parts)
    content = "".join(content_parts)

    # Usage：優先用 stream loop 累計的；空時 fallback 到 final_chunk 各種 metadata 位置
    # 1) langchain 0.2+ standard usage_metadata
    # 2) response_metadata.token_usage (OpenAI / Groq 風格 prompt_tokens/completion_tokens)
    # 3) additional_kwargs.usage (個別 provider)
    usage = dict(acc_um) if acc_um.get("total_tokens", 0) > 0 else {}
    model_name = ""
    if final_chunk is not None:
        if not usage:
            rm = getattr(final_chunk, "response_metadata", None) or {}
            tk = rm.get("token_usage") or rm.get("usage") or {}
            if tk:
                usage = {
                    "input_tokens": int(tk.get("prompt_tokens") or tk.get("input_tokens") or 0),
                    "output_tokens": int(tk.get("completion_tokens") or tk.get("output_tokens") or 0),
                    "total_tokens": int(tk.get("total_tokens") or 0),
                }
        if not usage:
            ak = getattr(final_chunk, "additional_kwargs", None) or {}
            u = ak.get("usage") or {}
            if u:
                usage = {
                    "input_tokens": int(u.get("prompt_tokens") or u.get("input_tokens") or 0),
                    "output_tokens": int(u.get("completion_tokens") or u.get("output_tokens") or 0),
                    "total_tokens": int(u.get("total_tokens") or 0),
                }
        # 仍空時把 acc_um 也丟回去（即使全 0、結構完整方便前端判斷 'stream 沒給 usage'）
        if not usage:
            usage = dict(acc_um)
        # provider model name（給成本對照表用）
        rm = getattr(final_chunk, "response_metadata", None) or {}
        model_name = rm.get("model_name") or rm.get("model") or ""
    # streaming chunk 沒 attach model 名稱時(常見:Groq / 部分 Gemini)，用設定頁的 model 當 fallback
    if not model_name:
        try:
            from settings import get_settings
            model_name = (get_settings().get("model") or "").strip()
        except Exception:
            pass

    if reasoning_len:
        log.info(
            f"[{label}] ✅ LLM 完成（{elapsed:.0f}s, reasoning {reasoning_len} 字, content {total} 字, "
            f"tokens {usage.get('total_tokens', '?')}）"
        )
    else:
        log.info(
            f"[{label}] ✅ LLM 完成（{elapsed:.0f}s, content {total} 字, "
            f"tokens {usage.get('total_tokens', '?')}）"
        )

    if return_usage:
        return {"content": content, "usage_metadata": usage, "model": model_name}
    return content
