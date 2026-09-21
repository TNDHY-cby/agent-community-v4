# -*- coding: utf-8 -*-
# 【废弃标注 2026-09-20】本地推理兜底通道已下线：决策基线改为"全部沿用 API"（见 开发框架与路线_20260920.md）。本模块仅保留供历史引用，不再进入启用路径；config.json 中 ai_mode=remote + ai_provider=openai 时不会调用本模块。
"""平台内 AI —— 本地推理兜底通道（配置驱动 + 按需自启）。

背景：外端 harness / 内 AI Provider 依赖的付费 API 无额度时，平台若只等外部回写
会长时间空转。本模块探测本机可用的 OpenAI 兼容推理端点（llama.cpp / Ollama 等），
必要时按配置拉起本地推理服务；外部接管缺席时由本机模型直接产出，
保证平台离线自洽、可持续自动化测试。

配置（不入开源仓，属运行时数据）：agent_community/data/local_ai.json
{
  "endpoints": [
    {"base": "http://127.0.0.1:8082/v1", "backend": "llama.cpp",
     "autostart": {"exe": "...llama-server.exe", "model": "...gguf",
                   "port": 8082, "args": ["-c", "4096", "-t", "6", "--no-webui"]}}
  ]
}
未提供配置文件时，仅探测内置默认端点（不自动拉起任何进程）。

设计要点：
- 探测结果带 TTL 缓存；自启有节流，避免重复拉起；
- chat() 纯本地产出，失败抛异常，由调用方决定降级；
- quick_wait_limit()：本地可用时缩短"外部回写等待"，外部缺席即快速转本地。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

# 内置默认端点（按优先级探测；无配置文件时不含自启能力）
DEFAULT_ENDPOINTS: list[dict] = [
    {"base": "http://127.0.0.1:8082/v1", "backend": "llama.cpp"},
    {"base": "http://127.0.0.1:11434/v1", "backend": "ollama"},
    {"base": "http://127.0.0.1:1234/v1", "backend": "lmstudio"},
]

CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "local_ai.json"
_DETECT_TTL_S = 60.0
_LAUNCH_THROTTLE_S = 120.0
QUICK_WAIT_S = float(os.environ.get("AC_LOCAL_AI_QUICK_WAIT", "12"))
BOOT_WAIT_S = float(os.environ.get("AC_LOCAL_AI_BOOT_WAIT", "90"))
AUTOSTART_ENABLED = os.environ.get("AC_LOCAL_AI_AUTOSTART", "1") not in ("0", "false", "off")

_cache: dict = {"at": 0.0, "target": None, "launch_at": 0.0}
_procs: list = []


def endpoints() -> list[dict]:
    """端点列表：优先读运行时配置，缺失则用内置默认。"""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        eps = cfg.get("endpoints")
        if isinstance(eps, list) and eps:
            merged = []
            for e in eps:
                if isinstance(e, dict) and e.get("base"):
                    merged.append(e)
            for d in DEFAULT_ENDPOINTS:            # 配置未覆盖的默认端点仍保留
                if not any(m["base"] == d["base"] for m in merged):
                    merged.append(d)
            return merged
    except Exception:
        pass
    return list(DEFAULT_ENDPOINTS)


def _http_json(url: str, payload: dict | None = None, timeout: float = 10.0) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _probe(ep: dict, timeout: float = 3.0) -> dict | None:
    """探测单个端点，可用则返回 {base, model, backend}。"""
    try:
        info = _http_json(ep["base"] + "/models", timeout=timeout)
        models = info.get("models") or info.get("data") or []
        if not models:
            return None
        first = models[0]
        name = first.get("model") or first.get("name") or first.get("id")
        if not name:
            return None
        return {"base": ep["base"], "model": name, "backend": ep.get("backend", "?")}
    except Exception:
        return None


def _autostart(ep: dict) -> bool:
    """按配置拉起本机推理服务并等待就绪。返回是否拉起成功（不代表已就绪）。"""
    cfg = ep.get("autostart") or {}
    exe, model = cfg.get("exe"), cfg.get("model")
    if not AUTOSTART_ENABLED or not exe or not model:
        return False
    if not Path(exe).exists() or not Path(model).exists():
        print(f"[local-ai] 自启配置无效: exe={exe} model={model}", flush=True)
        return False
    port = int(cfg.get("port") or 8082)
    args = [str(x) for x in (cfg.get("args") or ["-c", "4096", "--no-webui"])]
    cmd = [exe, "-m", model, "--port", str(port), "--host", "127.0.0.1"] + args
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x00000008 | 0x00000200)  # DETACHED|NEW_GROUP
        _procs.append(p)
        print(f"[local-ai] 已拉起本机推理服务 pid={p.pid} port={port}", flush=True)
        return True
    except Exception as e:
        print(f"[local-ai] 拉起失败: {str(e)[:110]}", flush=True)
        return False


def detect(force: bool = False) -> dict | None:
    """探测可用端点；均不可用时按配置尝试自启一次并等待就绪（带缓存与节流）。"""
    now = time.time()
    if not force and now - _cache["at"] < _DETECT_TTL_S:
        return _cache["target"]

    eps = endpoints()
    for ep in eps:
        t = _probe(ep)
        if t:
            _cache.update(at=now, target=t)
            return t

    # 全部不可用 → 尝试自启（节流，避免重复拉起）
    launched = False
    if now - _cache["launch_at"] > _LAUNCH_THROTTLE_S:
        for ep in eps:
            if (ep.get("autostart") or {}).get("exe") and _autostart(ep):
                launched = True
                _cache["launch_at"] = now
                break

    if launched:
        deadline = time.time() + BOOT_WAIT_S
        while time.time() < deadline:
            time.sleep(2.0)
            for ep in eps:
                t = _probe(ep, timeout=3.0)
                if t:
                    print(f"[local-ai] 本机推理服务就绪: {t['backend']} model={t['model']}",
                          flush=True)
                    _cache.update(at=time.time(), target=t)
                    return t
        print(f"[local-ai] 自启后 {BOOT_WAIT_S:g}s 内未就绪，转外部接管通道", flush=True)

    _cache.update(at=now, target=None)
    return None


def available() -> bool:
    return detect() is not None


def quick_wait_limit() -> float | None:
    """本地可用时返回外部回写的短等待上限；不可用返回 None（沿用默认策略）。"""
    return QUICK_WAIT_S if available() else None


async def chat(system_prompt: str, user_message: str, *, max_tokens: int = 900,
               temperature: float = 0.4, timeout: float = 240.0) -> str:
    """本地模型产出（必要时先确保服务就绪）。失败抛异常，由调用方降级。"""
    target = await asyncio.to_thread(detect)
    if not target:
        raise RuntimeError("本机无可用推理端点")
    payload = {
        "model": target["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    resp = await asyncio.to_thread(
        _http_json, target["base"] + "/chat/completions", payload, timeout)
    choices = resp.get("choices") or []
    if not choices:
        raise RuntimeError("本地模型返回空 choices")
    msg = choices[0].get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text:
        raise RuntimeError("本地模型返回空内容")
    return text


def extract_json(text: str) -> dict | None:
    """从模型输出中提取首个 JSON 对象（兼容 ```json 包裹与前后杂文）。"""
    if not text:
        return None
    s = text.strip()
    if "```" in s:
        for seg in s.split("```"):
            seg = seg.strip()
            if seg.lower().startswith("json"):
                seg = seg[4:].strip()
            if seg.startswith("{"):
                try:
                    return json.loads(seg)
                except Exception:
                    pass
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except Exception:
                        break
        start = s.find("{", start + 1)
    return None


def is_placeholder(text: str) -> bool:
    """判定是否为纯规则占位/空转文本（非真实产出）。"""
    if not text or len(text.strip()) < 20:
        return True
    return "纯规则降级" in text or "未取得模型产出" in text


if __name__ == "__main__":
    import sys

    t = detect(force=True)
    print("target:", json.dumps(t, ensure_ascii=False))
    if "--chat" in sys.argv and t:
        print(asyncio.run(chat("你是测试助手，直接给出结论。",
                               "用一句话说明二手交易平台的核心功能。"))[:200])
