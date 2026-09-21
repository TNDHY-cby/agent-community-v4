"""
v4 MVP 记忆系统 —— TaskMemory + CapabilityLedger

职责：
1. TaskMemory：历史任务向量检索 + JSON 持久化
2. CapabilityLedger：Agent 能力信誉统计，用于 Orchestrator 匹配
"""

from __future__ import annotations
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .protocol import HistoricalTask, MemoryStats, CapabilityLedger

# ── 默认持久化目录 ──────────────────────────────

DEFAULT_MEMORY_DIR = Path(__file__).parent.parent / "data" / "memory"


# ═══════════════════════════════════════════════════════════════
# TaskMemory
# ═══════════════════════════════════════════════════════════════

class TaskMemory:
    """历史任务记忆库：支持向量检索（余弦相似度）与 JSON 持久化"""

    def __init__(self, data_dir: Path = DEFAULT_MEMORY_DIR, max_records: int = 500):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.max_records = max_records
        self.tasks: list[HistoricalTask] = []
        self._embedding_map: dict[str, list[float]] = {}  # task_id → embedding
        self._load()

    # ─ 持久化 ───────────────────────────────────

    def _load(self):
        tasks_file = self.data_dir / "task_memory.json"
        if tasks_file.exists():
            try:
                raw = json.loads(tasks_file.read_text(encoding="utf-8"))
                for d in raw:
                    t = HistoricalTask(**d)
                    self.tasks.append(t)
                    if t.embedding:
                        self._embedding_map[t.task_id] = t.embedding
            except Exception as e:
                print(f"[memory] 加载 task_memory.json 失败: {e}")

    def _save(self):
        tasks_file = self.data_dir / "task_memory.json"
        try:
            raw = [t.model_dump() for t in self.tasks[-self.max_records:]]
            tasks_file.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"[memory] 保存 task_memory.json 失败: {e}")

    # ─ 向量操作 ─────────────────────────────────

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """余弦相似度。两向量都为零向量时返回 0。"""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _capability_embedding(capabilities: list[str], vocab: list[str]) -> list[float]:
        """将能力标签列表编码为 one-hot 向量（基于固定 vocab）"""
        if not vocab:
            return []
        vec = [0.0] * len(vocab)
        for cap in capabilities:
            if cap in vocab:
                vec[vocab.index(cap)] = 1.0
        return vec

    # ─ 查询 ─────────────────────────────────────

    def search(
        self,
        capabilities: list[str],
        top_k: int = 5,
        min_similarity: float = 0.1,
    ) -> list[HistoricalTask]:
        """按能力标签检索最相似的历史任务。使用能力向量余弦相似度。"""
        if not self.tasks:
            return []

        # 动态构建 vocab（本次查询用到的能力 + 历史中全局 top 能力）
        all_caps = list(capabilities)
        for t in self.tasks:
            all_caps.extend(t.capabilities_used)
        unique_caps = list(dict.fromkeys(all_caps))  # 去重保序
        query_vec = self._capability_embedding(capabilities, unique_caps)

        scored = []
        for t in self.tasks:
            task_vec = self._capability_embedding(t.capabilities_used, unique_caps)
            sim = self._cosine_similarity(query_vec, task_vec)
            if sim >= min_similarity:
                scored.append((sim, t))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:top_k]]

    def search_by_text(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.1,
    ) -> list[HistoricalTask]:
        """按文本关键词检索（兜底：在 title + description 中做子串匹配）"""
        if not self.tasks:
            return []

        query_lower = query.lower()
        scored = []
        for t in self.tasks:
            text = (t.title + " " + t.description).lower()
            # 简单的 Jaccard-like 分数：匹配词数 / 查询词数
            query_words = set(query_lower.split())
            match_count = sum(1 for w in query_words if w in text)
            score = match_count / len(query_words) if query_words else 0.0
            if score >= min_similarity:
                scored.append((score, t))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:top_k]]

    # ─ 写入 ─────────────────────────────────────

    def add(self, task: HistoricalTask) -> None:
        """添加一条历史记录并持久化"""
        # 去重
        for i, t in enumerate(self.tasks):
            if t.task_id == task.task_id:
                self.tasks[i] = task
                self._save()
                return
        self.tasks.append(task)
        if len(self.tasks) > self.max_records * 2:
            self.tasks = self.tasks[-self.max_records:]
        self._save()

    def get(self, task_id: str) -> Optional[HistoricalTask]:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def stats(self) -> MemoryStats:
        """生成记忆统计"""
        if not self.tasks:
            return MemoryStats()

        cap_counter: dict[str, int] = defaultdict(int)
        total_score = 0.0
        for t in self.tasks:
            for c in t.capabilities_used:
                cap_counter[c] += 1
            total_score += t.quality_score

        recent = self.tasks[-50:]
        recent_success = sum(
            1 for t in recent
            if all(v == "pass" for v in t.review_verdicts.values())
        ) / max(len(recent), 1)

        top_caps = sorted(cap_counter.items(), key=lambda x: x[1], reverse=True)[:10]

        return MemoryStats(
            total_tasks=len(self.tasks),
            total_capabilities=len(cap_counter),
            avg_quality_score=round(total_score / len(self.tasks), 3),
            top_capabilities=top_caps,
            recent_success_rate=round(recent_success, 3),
            last_updated=datetime.now().isoformat(),
        )


# ═══════════════════════════════════════════════════════════════
# CapabilityLedger
# ═══════════════════════════════════════════════════════════════

class CapabilityLedgerManager:
    """Agent 能力信誉管理器，用于 Orchestrator 匹配时加权"""

    def __init__(self, data_dir: Path = DEFAULT_MEMORY_DIR):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ledgers: dict[tuple[str, str], CapabilityLedger] = {}
        # ↑ key = (agent_id, capability)
        self._load()

    def _load(self):
        ledger_file = self.data_dir / "capability_ledger.json"
        if ledger_file.exists():
            try:
                raw = json.loads(ledger_file.read_text(encoding="utf-8"))
                for d in raw:
                    ledger = CapabilityLedger(**d)
                    self.ledgers[(ledger.agent_id, ledger.capability)] = ledger
            except Exception as e:
                print(f"[memory] 加载 capability_ledger.json 失败: {e}")

    def _save(self):
        ledger_file = self.data_dir / "capability_ledger.json"
        try:
            raw = [
                l.model_dump()
                for key, l in sorted(self.ledgers.items())
            ]
            ledger_file.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"[memory] 保存 capability_ledger.json 失败: {e}")

    # ─ 查询 ─────────────────────────────────────

    def get_reputation(self, agent_id: str, capability: str) -> float:
        """获取某个 Agent 在特定能力上的信誉分"""
        ledger = self.ledgers.get((agent_id, capability))
        return ledger.reputation if ledger else 0.5  # 新 Agent 默认 0.5

    def get_agent_reputations(self, agent_id: str) -> dict[str, float]:
        """获取某个 Agent 所有能力的信誉分"""
        result = {}
        for (aid, cap), ledger in self.ledgers.items():
            if aid == agent_id:
                result[cap] = ledger.reputation
        return result

    def get_best_agents(
        self,
        capability: str,
        min_reputation: float = 0.3,
        top_k: int = 5,
    ) -> list[tuple[str, float]]:
        """获取某能力最强的 Agent（按信誉分降序）"""
        candidates = []
        for (aid, cap), ledger in self.ledgers.items():
            if cap == capability and ledger.reputation >= min_reputation:
                candidates.append((aid, ledger.reputation))
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:top_k]

    # ─ 更新 ─────────────────────────────────────

    def record(
        self,
        agent_id: str,
        capability: str,
        success: bool,
        score: float = 0.0,
        duration_ms: int = 0,
    ):
        """记录一次执行结果，更新信誉分"""
        key = (agent_id, capability)
        if key not in self.ledgers:
            self.ledgers[key] = CapabilityLedger(
                agent_id=agent_id,
                capability=capability,
            )

        ledger = self.ledgers[key]
        ledger.total_tasks += 1
        if success:
            ledger.success_count += 1

        # 加权平均评分
        old_weight = ledger.total_tasks - 1
        ledger.avg_score = (
            (ledger.avg_score * old_weight + score) / ledger.total_tasks
        )

        # 加权平均耗时
        if duration_ms > 0:
            old_dur_weight = ledger.total_tasks - 1
            ledger.avg_duration_ms = int(
                (ledger.avg_duration_ms * old_dur_weight + duration_ms)
                / ledger.total_tasks
            )

        ledger.last_used_at = datetime.now().isoformat()
        ledger.updated_at = datetime.now().isoformat()

        # 信誉分计算：成功率 0.7 + 评分 0.3，beta 惩罚低样本量
        beta = min(1.0, ledger.total_tasks / 10)  # 10 次后完全信任
        success_rate = ledger.success_count / ledger.total_tasks
        normalized_score = min(ledger.avg_score, 1.0)
        raw_reputation = success_rate * 0.7 + normalized_score * 0.3
        ledger.reputation = raw_reputation * beta + 0.5 * (1 - beta)

        self._save()

    def all_ledgers(self) -> list[CapabilityLedger]:
        return sorted(self.ledgers.values(), key=lambda l: l.reputation, reverse=True)


# ── 全局单例 ─────────────────────────────────

task_memory = TaskMemory()
capability_ledger = CapabilityLedgerManager()
