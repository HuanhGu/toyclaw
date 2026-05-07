"""Session memory compaction for long-term storage."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from toyclaw.provider import LLMResponse, OpenAIProvider

from openai import OpenAI

client = OpenAI(
    base_url="https://api.moonshot.cn/v1",
    api_key="sk-sz3Mgj7b0B7hhqD13kNIcxb7DOvwD4AoufeMYbVfiwYbdEiq"
)

'''
provider_mem = OpenAIProvider(api_key="sk-sz3Mgj7b0B7hhqD13kNIcxb7DOvwD4AoufeMYbVfiwYbdEiq",
                          api_base= "https://api.moonshot.cn/v1", 
                          default_model="moonshot-v1-32k")
'''
class MemoryManager:
    """Compact old session JSONL files into long-term memory JSONL archives."""
    
    def __init__(
        self,
        # provider: OpenAIProvider,
        workspace: Path,
        trigger_count: int = 20,
        compact_batch_size: int = 10,
        keep_messages_per_session: int = 20,
        max_message_chars: int = 300,
    ):
        self._sessions_dir = workspace / "sessions"
        self._memory_dir = workspace / "memory"
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        self.trigger_count = trigger_count
        self.compact_batch_size = compact_batch_size
        self.keep_messages_per_session = keep_messages_per_session
        self.max_message_chars = max_message_chars

        self.provider = client


    # ====================  记忆压缩  ======================
    def maybe_compact(self, *, exclude_files: set[str] | None = None) -> int:
        """Compact old sessions when file count reaches threshold."""
        """压缩 所有session/*jsonl 文件 : 当有[trigger_count]个 .jsonl文件, 则压缩其中的[compact_batch_size]个文件  """

        exclude_files = exclude_files or set()
        session_files = sorted(self._sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        # if len(session_files) < self.trigger_count:  
            # return 0
        
        candidates = [p for p in session_files if p.name not in exclude_files]  # 当前文件不必压缩
        to_compact = candidates[: self.compact_batch_size]
        if not to_compact:
            return 0

        archive_name = f"memory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        archive_path = self._memory_dir / archive_name
        compacted = 0

        with open(archive_path, "w", encoding="utf-8") as out:
            header = {
                "_type": "metadata",
                "archive_file": archive_name,
                "archived_at": datetime.now().isoformat(),
                "source": "session_compaction",
            }
            out.write(json.dumps(header, ensure_ascii=False) + "\n")
            for session_path in to_compact:
                payload = self._compact_session_file(session_path)   # 每个session文件的记忆压缩部分
                if payload is None:
                    continue
                out.write(json.dumps(payload, ensure_ascii=False) + "\n")
                compacted += 1

        if compacted == 0:
            archive_path.unlink(missing_ok=True)
            return 0

        for session_path in to_compact:
            session_path.unlink(missing_ok=True)
        return compacted


    def _compact_session_file(self, path: Path) -> dict[str, Any] | None:
        """压缩每个会话.jsonl文件(滑动窗口) : 截取所有文件中, 最新对话信息self.keep_messages_per_session条
        每条信息, 只保留[: self.max_message_chars]个字符
        """

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return None

        created_at = None
        updated_at = None
        key = path.stem
        messages: list[dict[str, Any]] = []

        # 修改1：直接把session内容喂给大模型，让大模型帮忙压缩。输出格式：json
        # 问题1：为什么原来输出的memory格式长那样，看一下
        # 修改：LLM总结文档内容，生成摘要 trimmed_messages = '''调用LLM : messages 是json格式，请你分析，并生成摘要。要求输出 list[dict[str, Any]]'''
        completion = client.chat.completions.create(
            model="moonshot-v1-32k",
            messages = [
                {"role":"system", "content":"Summarize the content of documents I sent you and generate a summary."},
                { "role":"user","content":"\\n".join(lines),}
            ]
        ) 

        trimmed_messages = completion.choices[0].message.content
        messages = [{"content":trimmed_messages}]
        
        return {
            "_type": "memory_compact",
            "key": key,
            "created_at": created_at,
            "updated_at": updated_at,
            "archived_at": datetime.now().isoformat(),
            "message_count": len(messages), #len(lines) 是处理的行数,
            "messages": messages,
        }

        """
        # 旧：session文档内容拆分成行，保留每行信息前[:max_char]个字符
        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue

            if data.get("_type") == "metadata":
                key = str(data.get("key") or key)
                created_at = data.get("created_at")
                updated_at = data.get("updated_at")
                continue

            compact_msg = self._compact_message(data)  # 压缩每一行会话信息，直接截断前[:max_char]个字符
            if compact_msg is not None:
                messages.append(compact_msg)

        if not messages:
            return None

        trimmed_messages = messages[-self.keep_messages_per_session :]  #只保留最新对话
        return {
            "_type": "memory_compact",
            "key": key,
            "created_at": created_at,
            "updated_at": updated_at,
            "archived_at": datetime.now().isoformat(),
            "message_count": len(messages),
            "messages": trimmed_messages,
        }
        """


    
    def _compact_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """ 压缩每条jsonl信息 : 直接截断,保留[: self.max_message_chars]个字符 """

        role = msg.get("role")
        if role not in {"user", "assistant", "tool"}:
            return None

        content = msg.get("content")
        if isinstance(content, str):
            text = content.strip()
        else:
            text = json.dumps(content, ensure_ascii=False) if content is not None else ""

        if len(text) > self.max_message_chars:
            text = text[: self.max_message_chars] + " ... (trimmed)"

        item = {"role": role, "content": text}
        if "timestamp" in msg:
            item["timestamp"] = msg["timestamp"]
        if role == "tool" and "name" in msg:
            item["name"] = msg["name"]
        return item





    # ====================  记忆检索  ======================
    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return compact memory hits ranked by simple keyword overlap."""
        """记忆检索 : 关键词检索策略 """
    
        q = query.strip().lower()
        if not q:
            return []

        terms = [t for t in q.split() if len(t) >= 2]       # 用户问题解析：空格拆分话，得到关键词
        if not terms:
            return []

        hits: list[tuple[int, dict[str, Any]]] = []
        archives = sorted(self._memory_dir.glob("memory_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

        for archive in archives:
            for record in self._read_archive_records(archive): #json加载
                text = self._flatten_record_text(record).lower()  # json_value整合
                # BM检索：terms关键词匹配
                score_bm = sum(1 for t in terms if t in text) 
                # 模糊检索：调用AI 
                # terms 长什么样子？terms时query按空格分割后的list
                # q长什么样子？用户输入查询的完整信息 ‘给我讲过笑话：一个冷笑话 一个热笑话 笑掉大牙的那种’
                # text长什么样子？AI回答的flat文本: 'cli_direct_260507_161602 我是toyclaw，一个由月之暗面科技有限公司开发的人工智能助手。我在这里帮助你解答问题、处理文件和执行各种任务。'
                completion = client.chat.completions.create(
                    model="moonshot-v1-32k",
                    messages = [
                        {"role":"system", "content":"Summarize the content of documents I sent you and generate a summary."},
                        { "role":"user","content":f"Summarize the content of {text} and check if it contains the answer to the question {q}. \
                                                    Based on the match, output a score (output only an integer value, ranging from 0 to 10).\
                                                    Note:If they are completely unrelated, the output is 0.",}
                    ]
                ) 
                # 总结{text}的内容，检查里面是否有问题{q}的答案。根据匹配度输出一个打分值（仅输出一个整数值，范围限制在0~10）。
                score_llm = int(completion.choices[0].message.content)

                score = 0.3 * score_bm + 0.7 * score_llm
                if score <= 0:
                    continue
                hits.append((score, record))
        
        hits.sort(key=lambda x: x[0], reverse=True)  #检索结果排序，取前limit个结果
        return [item for _, item in hits[:limit]]


    def _read_archive_records(self, path: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return out

        for line in lines:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            if data.get("_type") == "memory_compact":
                out.append(data)
        return out

    @staticmethod
    def _flatten_record_text(record: dict[str, Any]) -> str:
        parts = [str(record.get("key", ""))]
        for msg in record.get("messages", []):
            parts.append(str(msg.get("content", "")))
        return " ".join(parts)


    

    # ====================  构建memory块，以便调用agent时注入  ======================
    def format_search_context(self, query: str, limit: int = 3) -> str:
        """Build a concise memory context block for prompt injection."""
        """将memory封装成块, 以便调用agent时注入"""
        
        records = self.search(query, limit=limit)  # ！长期记忆 '检索' 机制
        if not records:
            return ""

        lines = ["Relevant archived memory:"]
        for idx, r in enumerate(records, start=1):
            key = r.get("key", "unknown")
            updated = r.get("updated_at", "")
            msg_count = r.get("message_count", 0)
            messages = r.get("messages", [])[-3:]
            snippet = " | ".join(
                f"{m.get('role', '?')}: {str(m.get('content', '')).replace(chr(10), ' ')[:120]}"
                for m in messages
            )
            lines.append(f"{idx}. key={key} updated={updated} total={msg_count} :: {snippet}")
        return "\n".join(lines)