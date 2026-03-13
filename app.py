#!/usr/bin/env python3
"""AI 辯論 - Web 控制面板（含主持人機制，支援多場同時辯論）"""

import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file, send_from_directory, stream_with_context
from opencc import OpenCC
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

app = Flask(__name__)

API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LLM_API_KEY")
if not API_KEY:
    raise RuntimeError("請設定 OPENROUTER_API_KEY 或 LLM_API_KEY")

BASE_URL = os.environ.get("LLM_BASE_URL", "").strip() or "https://openrouter.ai/api/v1"
DEFAULT_FREE_MODEL = os.environ.get("OPENROUTER_DEFAULT_MODEL", "").strip() or "openrouter/hunter-alpha"
APP_NAME = os.environ.get("OPENROUTER_APP_NAME", "").strip() or "ai-debate"
APP_URL = os.environ.get("OPENROUTER_APP_URL", "").strip()
FALLBACK_FREE_MODELS = [
    DEFAULT_FREE_MODEL,
    "google/gemma-3-4b-it:free",
    "arcee-ai/trinity-mini:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

client_headers = {
    "User-Agent": "ai-debate/1.0",
    "X-Title": APP_NAME,
}
if APP_URL:
    client_headers["HTTP-Referer"] = APP_URL

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, default_headers=client_headers)
opencc_tw = OpenCC("s2twp")



# ── Session 管理 ──────────────────────────────────────

class SessionState:
    """每場辯論的獨立狀態"""

    def __init__(self):
        self.session_id = str(uuid.uuid4())[:8]
        self.debate_state = {
            "running": False,
            "stop_requested": False,
            "current_round": 0,
            "total_rounds": 0,
            "waiting_for_human": False,
            "waiting_speaker": "",
        }
        self.event_queues: list[queue.Queue] = []
        self.eq_lock = threading.Lock()
        self.human_input_queue: queue.Queue = queue.Queue(maxsize=1)
        self.human_time_limit = 120
        self.state_lock = threading.Lock()


_sessions: dict[str, SessionState] = {}
_sessions_lock = threading.Lock()


def get_session(session_id: str) -> SessionState | None:
    with _sessions_lock:
        return _sessions.get(session_id)


def create_session() -> SessionState:
    s = SessionState()
    with _sessions_lock:
        _sessions[s.session_id] = s
    return s


def create_session_if_available() -> SessionState | None:
    with _sessions_lock:
        running = sum(1 for sess in _sessions.values() if sess.debate_state["running"])
        if running >= MAX_CONCURRENT_DEBATES:
            return None
        sess = SessionState()
        sess.debate_state["running"] = True
        _sessions[sess.session_id] = sess
        return sess


def broadcast(session_id: str, event_type: str, data: dict):
    sess = get_session(session_id)
    if not sess:
        return
    msg = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with sess.eq_lock:
        dead = []
        for i, q in enumerate(sess.event_queues):
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(i)
        for i in reversed(dead):
            sess.event_queues.pop(i)


# ── LLM 呼叫（含共用重試邏輯）──────────────────────────

MAX_RETRIES = 3
RETRY_DELAYS = [10, 30, 60]  # 秒
FIXED_MAX_TOKENS = 650
MIN_HUMAN_TIME_LIMIT = 30
MAX_HUMAN_TIME_LIMIT = 300
MAX_AI_PARTICIPANTS = 4
MAX_CONCURRENT_DEBATES = 3
TARGET_DEBATE_RESPONSE_CHARS = 300
SAFETY_DEBATE_RESPONSE_CHARS = 900
TAIWAN_TRADITIONAL_CHINESE_RULES = (
    "語言硬性規則：你只能使用台灣繁體中文（zh-TW）作答。"
    "禁止使用簡體中文、禁止混用中國用語。"
    "若必須保留英文，僅限模型名稱、程式碼、URL、專有名詞或引用內容，"
    "其餘敘述一律改寫成自然的台灣繁體中文。"
    f"每次回覆請優先精簡在 {TARGET_DEBATE_RESPONSE_CHARS} 字左右。"
    "若論點還沒講完，可以適度延伸，但一定要把完整句子與結論講完，禁止留下半句或未完成收尾。"
    "語氣不要太學術或太官腔。"
    "要有明確立場、敢直接反駁對手，可以帶一點嘴砲、吐槽與火藥味，"
    "但核心仍要有論點、有邏輯，不准只剩情緒發言。"
)

RETRYABLE_KEYWORDS = ("503", "overload", "timeout", "502", "429")


def _is_retryable(err_str: str) -> bool:
    return any(k in err_str for k in RETRYABLE_KEYWORDS)


def _should_fallback_model(err_str: str) -> bool:
    lower = err_str.lower()
    return (
        _is_retryable(lower)
        or "no endpoints available" in lower
        or "connection error" in lower
        or "rate-limit" in lower
        or "rate limited" in lower
    )


def _candidate_models(model: str) -> list[str]:
    seen = set()
    candidates = []
    for item in [model, *FALLBACK_FREE_MODELS]:
        if not item or item in seen:
            continue
        seen.add(item)
        candidates.append(item)
    return candidates


def _create_chat_completion(model: str, messages: list[dict], **kwargs):
    last_error = None
    for candidate in _candidate_models(model):
        try:
            resp = client.chat.completions.create(
                model=candidate,
                messages=messages,
                **kwargs,
            )
            return candidate, resp
        except Exception as e:
            last_error = e
            if not _should_fallback_model(str(e)):
                raise
    if last_error:
        raise last_error
    raise RuntimeError("沒有可用的模型")


def _build_messages(system: str, messages: list[dict]) -> list[dict]:
    merged = list(messages)
    parts = [TAIWAN_TRADITIONAL_CHINESE_RULES]
    if system.strip():
        parts.append(f"請先遵守以下角色設定與規則：\n{system.strip()}")
    system_prefix = "\n\n".join(parts).strip() + "\n\n"
    if merged and merged[0].get("role") == "user":
        first = dict(merged[0])
        first["content"] = system_prefix + str(first.get("content", ""))
        merged[0] = first
        return merged
    return [{"role": "user", "content": system_prefix}] + merged


def _to_taiwan_traditional(text: str) -> str:
    return opencc_tw.convert(text or "")


def _truncate_debate_output(text: str, max_chars: int = SAFETY_DEBATE_RESPONSE_CHARS) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if len(cleaned) <= max_chars:
        return cleaned
    truncated = cleaned[:max_chars].rstrip("，、；：,.!?！？ \n")
    last_break = max(truncated.rfind(sep) for sep in ("。", "！", "？", "\n"))
    if last_break >= max_chars // 2:
        truncated = truncated[: last_break + 1].rstrip()
    return truncated


def running_session_count() -> int:
    with _sessions_lock:
        return sum(1 for sess in _sessions.values() if sess.debate_state["running"])


def _with_retry(session_id: str, model: str, fn, error_prefix: str = "API") -> str:
    """共用重試包裝：fn() 應回傳 str 或拋例外"""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            if _is_retryable(str(e)) and attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                time.sleep(delay)
                continue
            return f"[{error_prefix} 錯誤: {e}]"
    return f"[{error_prefix} 錯誤: 重試次數已用盡]"


def call_api(session_id: str, model: str, system: str, messages: list[dict], max_tokens: int) -> str:
    def _call():
        request_messages = _build_messages(system, messages)
        actual_model, resp = _create_chat_completion(
            model=model,
            messages=request_messages,
            max_tokens=max_tokens,
            temperature=0.8,
            stream=True,
        )
        chunks = []
        for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                chunks.append(chunk.choices[0].delta.content)
        full = "".join(chunks)
        full = re.sub(r"<think>.*?</think>\s*", "", full, flags=re.DOTALL)
        return _truncate_debate_output(_to_taiwan_traditional(full).strip())

    return _with_retry(session_id, model, _call, "API")


def _is_free_price(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _is_text_model(model: dict) -> bool:
    arch = model.get("architecture") or {}
    input_modalities = set(arch.get("input_modalities") or [])
    output_modalities = set(arch.get("output_modalities") or [])
    return "text" in input_modalities and "text" in output_modalities


def get_free_models() -> list[str]:
    """從 OpenRouter 取得免費文字模型清單。"""
    fallback = list(FALLBACK_FREE_MODELS)
    url = f"{BASE_URL.rstrip('/')}/models"
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            **client_headers,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])

    models = []
    for item in data:
        pricing = item.get("pricing") or {}
        if not (_is_free_price(pricing.get("prompt")) and _is_free_price(pricing.get("completion"))):
            continue
        if not _is_text_model(item):
            continue
        model_id = item.get("id", "").strip()
        if model_id == "openrouter/free":
            continue
        if model_id and model_id not in models:
            models.append(model_id)

    if not models:
        return fallback
    prioritized = []
    for model in FALLBACK_FREE_MODELS:
        if model in models:
            prioritized.append(model)
            models.remove(model)
    return prioritized + sorted(models)


VIDEO_DIR = OUTPUT_DIR / "videos"
VIDEO_DIR.mkdir(exist_ok=True)
_video_counter = 0
_video_counter_lock = threading.Lock()


def call_video(scene_description: str) -> dict:
    """使用 grok-imagine-1.0-video 生成場景影片，立即下載到本地"""
    global _video_counter
    try:
        resp = client.chat.completions.create(
            model="grok-imagine-1.0-video",
            messages=[{"role": "user", "content": scene_description}],
            stream=True,
        )
        chunks = []
        for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                chunks.append(chunk.choices[0].delta.content)
        full = "".join(chunks)
        # 提取 mp4 URL
        remote_url = ""
        url_match = re.search(r'https?://[^\s"<>\']+\.mp4[^\s"<>\']*', full)
        if url_match:
            remote_url = url_match.group(0)
        else:
            src_match = re.search(r'src=["\']([^"\']+)["\']', full)
            if src_match:
                remote_url = src_match.group(1)
        if not remote_url:
            return {"url": "", "raw": full}
        # 嘗試下載到本地（URL 可能有時效性）
        import requests as req
        with _video_counter_lock:
            _video_counter += 1
            idx = _video_counter
        local_name = f"clip_{idx:04d}.mp4"
        local_path = VIDEO_DIR / local_name
        try:
            dl = req.get(remote_url, timeout=60)
            if dl.status_code == 200 and len(dl.content) > 1000:
                local_path.write_bytes(dl.content)
                return {"url": f"/api/video/{local_name}", "raw": full}
        except Exception:
            pass
        # 下載失敗就直接回傳遠端 URL（讓瀏覽器試試）
        return {"url": remote_url, "raw": full}
    except Exception as e:
        return {"url": "", "raw": f"[影片生成錯誤: {e}]"}


def call_human(session_id: str, speaker: str, prompt: str) -> str:
    sess = get_session(session_id)
    if not sess:
        return "[session 不存在]"
    with sess.state_lock:
        sess.debate_state["waiting_for_human"] = True
        sess.debate_state["waiting_speaker"] = speaker
    broadcast(session_id, "waiting_human", {"speaker": speaker, "context": prompt})
    try:
        response = sess.human_input_queue.get(timeout=sess.human_time_limit)
        return response
    except queue.Empty:
        return "[人類未在時限內回應，跳過此輪]"
    finally:
        with sess.state_lock:
            sess.debate_state["waiting_for_human"] = False
            sess.debate_state["waiting_speaker"] = ""


def call_participant(session_id: str, participant: dict, prompt: str, max_tokens: int) -> str:
    """統一的參與者呼叫入口"""
    via = participant["via"]
    if via == "human":
        return call_human(session_id, participant["name"], prompt)
    messages = [{"role": "user", "content": prompt}]
    return call_api(session_id, participant["model"], participant["system"], messages, max_tokens)


def extract_cue_order(mod_response: str, participants: list[dict]) -> list[dict]:
    """根據主持人點名順序重新排列參與者"""
    positions = []
    for p in participants:
        name = p["name"]
        # 同時支援全形（）與半形()括號
        short_name = re.split(r"[（(]", name)[0].strip()
        pos = mod_response.find(name)
        if pos == -1:
            pos = mod_response.find(short_name)
        positions.append((p, pos))

    mentioned = [(p, pos) for p, pos in positions if pos != -1]
    unmentioned = [p for p, pos in positions if pos == -1]

    if not mentioned:
        return participants

    mentioned.sort(key=lambda x: x[1])
    return [p for p, _ in mentioned] + unmentioned


def build_context(history: list[dict], latest_n: int = 4) -> str:
    recent = history[-latest_n:] if len(history) > latest_n else history
    lines = []
    for msg in recent:
        lines.append(f"【{msg['speaker']}】\n{msg['content']}\n")
    return "\n".join(lines)


def merge_videos(session_id: str, urls: list[str], name: str) -> Path | None:
    """合併影片片段（支援本地路徑和遠端 URL）"""
    import requests as req
    local_files = []
    for i, url in enumerate(urls):
        if url.startswith("/api/video/"):
            filename = url.split("/")[-1]
            path = VIDEO_DIR / filename
            if not path.exists():
                path = OUTPUT_DIR / filename
            if path.exists():
                local_files.append(path)
        elif url.startswith("http"):
            try:
                broadcast(session_id, "status", {"message": f"下載影片 {i+1}/{len(urls)}..."})
                dl = req.get(url, timeout=60)
                if dl.status_code == 200 and len(dl.content) > 1000:
                    path = VIDEO_DIR / f"merge_{i:04d}.mp4"
                    path.write_bytes(dl.content)
                    local_files.append(path)
            except Exception:
                pass

    if not local_files:
        return None

    concat_file = VIDEO_DIR / f"{name}_concat.txt"
    with open(concat_file, "w") as f:
        for p in local_files:
            # 跳脫單引號避免 ffmpeg concat 解析錯誤
            safe = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    output_path = OUTPUT_DIR / f"{name}_merged.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0",
             "-i", str(concat_file), "-c", "copy", "-y", str(output_path)],
            check=True, capture_output=True, timeout=300,
        )
        return output_path
    except (FileNotFoundError, subprocess.CalledProcessError):
        try:
            subprocess.run(
                ["ffmpeg", "-f", "concat", "-safe", "0",
                 "-i", str(concat_file),
                 "-c:v", "libx264", "-c:a", "aac", "-y", str(output_path)],
                check=True, capture_output=True, timeout=600,
            )
            return output_path
        except Exception as e:
            broadcast(session_id, "status", {"message": f"合併影片失敗: {e}"})
            return None
    finally:
        concat_file.unlink(missing_ok=True)


# ── 主持人 System Prompt ─────────────────────────────

MODERATOR_SYSTEM = """你是這場辯論的主持人。你的職責：

1. **開場**：介紹辯論主題、參與者、辯論規則，然後拋出第一個核心問題
2. **引導節奏**：每輪開始時提出該輪要聚焦的子議題或問題方向
3. **維持秩序**：如果有人離題、重複論點、或無意義爭吵，立即拉回主線
4. **深挖觀點**：對有價值但不夠深入的論點追問
5. **平衡發言**：確保每位參與者的觀點都被充分討論
6. **階段總結**：在適當時機總結目前共識和分歧
7. **收尾**：在最後一輪產出完整的結論報告

語言：繁體中文
風格：不失中立，但節奏要俐落、敢點破漏洞，必要時可用輕微吐槽或酸句增加張力
每次發言盡量精簡在 300 字左右；若論點尚未收完，可以稍微延伸，但一定要完整收尾（結論報告同理）"""


# ── 辯論共用邏輯 ─────────────────────────────────────

class DebateContext:
    """封裝單場辯論所需的共用狀態與工具方法"""

    def __init__(self, config: dict):
        self.session_id = config["session_id"]
        self.topic = config["topic"]
        self.rounds = config["rounds"]
        self.max_tokens = config["max_tokens"]
        self.participants = config["participants"]
        self.moderator = config.get("moderator")
        self.generate_video = config.get("generate_video", False)

        self.log_file = OUTPUT_DIR / f"debate_{datetime.now():%Y%m%d_%H%M%S}.md"
        self.history: list[dict] = []
        self._all_video_urls: list[str] = []
        self._video_lock = threading.Lock()
        self._video_threads: list[threading.Thread] = []

    @property
    def sess(self) -> SessionState | None:
        return get_session(self.session_id)

    @property
    def stop_requested(self) -> bool:
        sess = self.sess
        return sess.debate_state["stop_requested"] if sess else True

    def write_log(self, content: str):
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(content)

    def queue_video(self, speaker: str, content: str, rnd: int):
        """在背景為說話者生成影片（不阻塞辯論）"""
        if not self.generate_video or self.stop_requested:
            return
        topic = self.topic
        session_id = self.session_id

        def _gen():
            summary = content[:200].replace('\n', ' ')
            prompts = [
                f"Cinematic close-up: {speaker} passionately debating about '{topic[:80]}'. "
                f"Key point: {summary}. Professional debate studio, dramatic lighting.",
            ]
            if len(content) > 100:
                s2 = content[100:350].replace('\n', ' ')
                prompts.append(
                    f"Wide shot debate panel: {speaker} making argument. Topic: '{topic[:80]}'. "
                    f"Context: {s2[:200]}. Futuristic holographic display, dynamic camera.",
                )
            for p in prompts:
                if self.stop_requested:
                    break
                r = call_video(p)
                if r["url"]:
                    with self._video_lock:
                        self._all_video_urls.append(r["url"])
                    broadcast(session_id, "video", {"url": r["url"], "round": rnd, "speaker": speaker})
                    self.write_log(f"**[場景影片 - {speaker}]** {r['url']}\n\n")

        t = threading.Thread(target=_gen, daemon=True)
        t.start()
        self._video_threads.append(t)

    def speak(self, participant: dict, prompt: str, rnd: int,
              max_tok: int = 0, phase: str = "") -> str:
        """讓一位參與者發言並記錄"""
        broadcast(self.session_id, "speaking", {
            "speaker": participant["name"],
            "round": rnd,
            "status": "moderating" if participant is self.moderator else "thinking",
        })
        response = call_participant(self.session_id, participant, prompt, max_tok or self.max_tokens)
        self.history.append({"speaker": participant["name"], "content": response})
        self.write_log(f"### {participant['name']}\n\n{response}\n\n")
        broadcast(self.session_id, "message", {
            "speaker": participant["name"],
            "content": response,
            "round": rnd,
            "phase": phase,
        })
        self.queue_video(participant["name"], response, rnd)
        time.sleep(1)
        return response

    def wait_and_merge_videos(self):
        """等待所有影片生成完成，並合併"""
        if not self.generate_video or not self._video_threads:
            return
        broadcast(self.session_id, "status", {"message": f"等待 {len(self._video_threads)} 個影片生成完成..."})
        for t in self._video_threads:
            t.join(timeout=300)
        with self._video_lock:
            urls = list(self._all_video_urls)
        if not urls:
            return
        broadcast(self.session_id, "status", {"message": f"正在合併 {len(urls)} 段影片..."})
        merged = merge_videos(self.session_id, urls, self.log_file.stem)
        if merged:
            broadcast(self.session_id, "video_merged", {"filename": merged.name, "count": len(urls)})
            self.write_log(f"\n**[完整影片]** /api/video/{merged.name}\n")
        else:
            broadcast(self.session_id, "status", {"message": "影片合併失敗"})

    def finish(self):
        """辯論結束共用收尾"""
        if not self.stop_requested:
            self.wait_and_merge_videos()
        else:
            # 停止時不等影片，只等短暫時間
            for t in self._video_threads:
                t.join(timeout=5)
        broadcast(self.session_id, "done", {"message": "辯論結束", "log_file": str(self.log_file)})
        sess = self.sess
        if sess:
            with sess.state_lock:
                sess.debate_state["running"] = False


def run_debate(config: dict):
    """在背景執行辯論（主持人模式）"""
    session_id = config["session_id"]
    ctx = DebateContext(config)
    sess = ctx.sess

    with sess.state_lock:
        sess.debate_state["stop_requested"] = False
        sess.debate_state["total_rounds"] = ctx.rounds
        sess.debate_state["current_round"] = 0

    moderator = ctx.moderator
    participants = ctx.participants

    try:
        _run_debate_inner(ctx, session_id, moderator, participants)
    except Exception as e:
        broadcast(session_id, "status", {"message": f"辯論發生錯誤：{e}"})
    finally:
        ctx.finish()


def _run_debate_inner(ctx, session_id, moderator, participants):
    # ── header ──
    ctx.write_log(f"# AI 辯論：{ctx.topic[:50]}\n\n")
    ctx.write_log(f"**開始時間：** {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    ctx.write_log(f"**主持人：** {moderator['name']}\n")
    ctx.write_log(f"**參與者：** {', '.join(p['name'] for p in participants)}\n")
    ctx.write_log(f"**輪數：** {ctx.rounds}\n\n---\n\n")

    broadcast(session_id, "status", {"message": f"辯論開始！主持人：{moderator['name']}", "log_file": str(ctx.log_file)})

    # ── 開場 ──
    participant_names = "、".join(p["name"] for p in participants)
    opening_prompt = (
        f"辯論主題：{ctx.topic}\n\n"
        f"參與者：{participant_names}\n\n"
        f"請做開場白：介紹主題的背景與重要性，介紹參與者，說明辯論規則，"
        f"然後提出第一個核心問題，請所有參與者回應。"
    )
    ctx.write_log("## 開場\n\n")
    broadcast(session_id, "round", {"round": 0, "total": ctx.rounds, "label": "開場"})
    ctx.speak(moderator, opening_prompt, rnd=0, phase="opening")

    # 每位參與者回應開場問題
    for participant in participants:
        if ctx.stop_requested:
            break
        history_ctx = build_context(ctx.history)
        prompt = f"主持人的開場與提問如下：\n\n{history_ctx}\n\n請回應主持人的問題，提出你的核心觀點。"
        ctx.speak(participant, prompt, rnd=0, phase="opening")

    ctx.write_log("---\n\n")

    # ── 主迴圈 ──
    for round_num in range(1, ctx.rounds + 1):
        if ctx.stop_requested:
            broadcast(session_id, "status", {"message": "辯論已被手動停止"})
            break

        ctx.sess.debate_state["current_round"] = round_num
        ctx.write_log(f"## 第 {round_num} 輪\n\n")
        broadcast(session_id, "round", {"round": round_num, "total": ctx.rounds})

        # 主持人開場：回顧 + 提出本輪議題
        history_ctx = build_context(ctx.history, latest_n=len(participants) + 2)
        mod_prompt = (
            f"這是第 {round_num}/{ctx.rounds} 輪辯論。\n\n"
            f"近期討論：\n{history_ctx}\n\n"
            f"請做以下事情：\n"
            f"1. 簡要回顧上一輪的重點（如有離題或無意義爭論請指出並糾正）\n"
            f"2. 提出本輪要聚焦的子議題或追問方向\n"
            f"3. 明確指定本輪的發言順序，點名參與者回答（例如：「請 A 先回應，接著 B，最後 C」）"
        )
        ctx.speak(moderator, mod_prompt, rnd=round_num, phase="round_intro")

        # 根據主持人點名順序決定發言順序
        mod_response = ctx.history[-1]["content"]
        round_order = extract_cue_order(mod_response, participants)

        # 各參與者依主持人 cue 的順序回應
        for participant in round_order:
            if ctx.stop_requested:
                break
            history_ctx = build_context(ctx.history, latest_n=len(participants) + 2)
            prompt = (
                f"這是第 {round_num} 輪辯論。\n\n"
                f"近期討論（含主持人引導）：\n{history_ctx}\n\n"
                f"請回應主持人提出的問題，並針對其他參與者的觀點進行回應。"
            )
            ctx.speak(participant, prompt, rnd=round_num)
            if ctx.stop_requested:
                break

        if ctx.stop_requested:
            break

        # 主持人小結（每 3 輪且非最後一輪）
        if round_num % 3 == 0 and round_num < ctx.rounds:
            history_ctx = build_context(ctx.history, latest_n=len(participants) * 3 + 3)
            summary_prompt = (
                f"請對最近幾輪的討論做一個階段總結：\n\n{history_ctx}\n\n"
                f"包含：1. 目前共識 2. 主要分歧 3. 需要在下一階段深入的方向"
            )
            ctx.speak(moderator, summary_prompt, rnd=round_num, phase="summary")

        ctx.write_log("---\n\n")

    # ── 最終結論 ──
    if not ctx.stop_requested:
        ctx.sess.debate_state["current_round"] = ctx.rounds
        broadcast(session_id, "round", {"round": ctx.rounds, "total": ctx.rounds, "label": "最終結論"})
        ctx.write_log("## 最終結論\n\n")

        history_ctx = build_context(ctx.history, latest_n=len(ctx.history))
        final_prompt = (
            f"辯論已結束，共 {ctx.rounds} 輪。\n\n"
            f"完整討論摘要：\n{history_ctx}\n\n"
            f"請產出完整的結論報告，包含：\n"
            f"1. 辯論過程回顧\n"
            f"2. 各方核心觀點摘要\n"
            f"3. 達成共識的部分\n"
            f"4. 主要分歧與各方理由\n"
            f"5. 風險最低的前 3 個方案\n"
            f"6. 報酬潛力最高的前 3 個方案\n"
            f"7. 主持人推薦：綜合評估最值得執行的前 5 個方案\n"
            f"8. 結語與感謝"
        )
        ctx.speak(moderator, final_prompt, rnd=ctx.rounds, phase="final", max_tok=FIXED_MAX_TOKENS)
        ctx.write_log(f"\n**結束時間：** {datetime.now():%Y-%m-%d %H:%M:%S}\n")


def run_debate_no_moderator(config: dict):
    """無主持人模式"""
    session_id = config["session_id"]
    ctx = DebateContext(config)
    sess = ctx.sess

    with sess.state_lock:
        sess.debate_state["stop_requested"] = False
        sess.debate_state["total_rounds"] = config["rounds"]
        sess.debate_state["current_round"] = 0

    participants = ctx.participants

    try:
        _run_debate_no_mod_inner(ctx, session_id, participants)
    except Exception as e:
        broadcast(session_id, "status", {"message": f"辯論發生錯誤：{e}"})
    finally:
        ctx.finish()


def _run_debate_no_mod_inner(ctx, session_id, participants):
    names = ", ".join(p["name"] for p in participants)
    ctx.write_log(f"# AI 辯論：{ctx.topic[:50]}\n\n")
    ctx.write_log(f"**開始時間：** {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    ctx.write_log(f"**參與者：** {names}\n")
    ctx.write_log(f"**輪數：** {ctx.rounds}\n\n---\n\n")

    broadcast(session_id, "status", {"message": f"辯論開始！參與者：{names}", "log_file": str(ctx.log_file)})

    # 開場
    first = participants[0]
    opening = f"辯論主題：{ctx.topic}\n\n請先提出你認為最重要的 3 個觀點，並說明理由。"
    ctx.write_log(f"## 開場 - {first['name']}\n\n")
    broadcast(session_id, "round", {"round": 0, "total": ctx.rounds, "label": "開場"})
    ctx.speak(first, opening, rnd=0, phase="opening")
    ctx.write_log("---\n\n")

    for round_num in range(1, ctx.rounds + 1):
        if ctx.stop_requested:
            broadcast(session_id, "status", {"message": "辯論已被手動停止"})
            break
        ctx.sess.debate_state["current_round"] = round_num
        ctx.write_log(f"## 第 {round_num} 輪\n\n")
        broadcast(session_id, "round", {"round": round_num, "total": ctx.rounds})

        for i, participant in enumerate(participants):
            if round_num == 1 and i == 0:
                continue
            if ctx.stop_requested:
                break
            history_ctx = build_context(ctx.history)
            prompt = f"這是第 {round_num} 輪辯論。以下是近期的討論內容：\n\n{history_ctx}\n\n請回應以上論點。"
            ctx.speak(participant, prompt, rnd=round_num)
            if ctx.stop_requested:
                break

        ctx.write_log("---\n\n")

    if not ctx.stop_requested:
        broadcast(session_id, "speaking", {"speaker": "系統", "round": ctx.rounds, "status": "final_summary"})
        history_ctx = build_context(ctx.history, latest_n=len(ctx.history))
        final_prompt = (
            "請產出最終總結報告：\n"
            "1. 各方共識\n2. 主要分歧點\n3. 風險最低的前 3 個方案\n"
            "4. 報酬潛力最高的前 3 個方案\n5. 綜合推薦前 5 名\n\n"
            f"全部討論：\n{history_ctx}"
        )
        final = call_participant(session_id, participants[0], final_prompt, FIXED_MAX_TOKENS)
        ctx.write_log(f"## 最終總結\n\n{final}\n\n")
        ctx.write_log(f"**結束時間：** {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        broadcast(session_id, "message", {"speaker": "最終總結", "content": final, "round": ctx.rounds, "phase": "final"})


# ── 路由 ──────────────────────────────────────────────

@app.route("/")
def index():
    if (FRONTEND_DIST / "index.html").exists():
        return send_from_directory(FRONTEND_DIST, "index.html")
    return render_template("index.html")


@app.route("/favicon.svg")
def favicon():
    if (FRONTEND_DIST / "favicon.svg").exists():
        return send_from_directory(FRONTEND_DIST, "favicon.svg")
    public_dir = (Path(__file__).parent / "frontend" / "public").resolve()
    candidate = (public_dir / "favicon.svg").resolve()
    if candidate.exists() and str(candidate).startswith(str(public_dir)):
        return send_from_directory(public_dir, "favicon.svg")
    return jsonify({"error": "找不到檔案"}), 404


@app.route("/favicon.ico")
def favicon_legacy():
    return favicon()


@app.route("/assets/<path:filename>")
def frontend_assets(filename):
    candidate = (FRONTEND_DIST / "assets" / filename).resolve()
    assets_dir = (FRONTEND_DIST / "assets").resolve()
    if candidate.exists() and str(candidate).startswith(str(assets_dir)):
        return send_from_directory(assets_dir, filename)
    return jsonify({"error": "找不到檔案"}), 404


@app.route("/api/models")
def get_models():
    try:
        models = get_free_models()
    except Exception:
        models = [
            *FALLBACK_FREE_MODELS,
        ]

    models.append("human (you)")
    return jsonify({"models": models})


def parse_participant(p: dict) -> dict:
    model = p.get("model", DEFAULT_FREE_MODEL)
    ml = model.lower()
    if ml.startswith("human"):
        via, actual_model = "human", "human"
    else:
        via, actual_model = "api", model
    return {
        "name": p.get("name", model),
        "model": actual_model,
        "via": via,
        "system": p.get("system", ""),
    }


@app.route("/api/start", methods=["POST"])
def start_debate():
    data = request.get_json(silent=True) or {}
    try:
        rounds = max(1, min(int(data.get("rounds", 6)), 6))
    except (ValueError, TypeError):
        return jsonify({"error": "輪數格式不正確"}), 400

    raw_participants = data.get("participants", [])
    if len(raw_participants) < 2:
        return jsonify({"error": "至少需要 2 位參與者"}), 400
    human_count = 0
    ai_count = 0
    for participant in raw_participants:
        model = str(participant.get("model", "")).lower()
        if model.startswith("human"):
            human_count += 1
        else:
            ai_count += 1
    if human_count > 1:
        return jsonify({"error": "最多只能有 1 位人類參與者"}), 400
    if ai_count < 2:
        return jsonify({"error": "至少需要 2 位 AI 辯論者"}), 400
    if ai_count > MAX_AI_PARTICIPANTS:
        return jsonify({"error": f"AI 辯論者最多 {MAX_AI_PARTICIPANTS} 位"}), 400
    if len(raw_participants) > MAX_AI_PARTICIPANTS + 1:
        return jsonify({"error": "參與者數量超出上限"}), 400

    try:
        human_time_limit = int(data.get("human_time_limit", 120))
    except (ValueError, TypeError):
        return jsonify({"error": "人類回覆時限格式不正確"}), 400
    if not MIN_HUMAN_TIME_LIMIT <= human_time_limit <= MAX_HUMAN_TIME_LIMIT:
        return jsonify({"error": f"人類回覆時限需介於 {MIN_HUMAN_TIME_LIMIT} 到 {MAX_HUMAN_TIME_LIMIT} 秒"}), 400

    sess = create_session_if_available()
    if not sess:
        return jsonify({"error": f"同時進行中的辯論最多 {MAX_CONCURRENT_DEBATES} 場，請先停止或等待其中一場結束。"}), 400
    sess.human_time_limit = human_time_limit

    config = {
        "session_id": sess.session_id,
        "topic": data.get("topic", "目前最有效的賺錢方式"),
        "rounds": rounds,
        "max_tokens": FIXED_MAX_TOKENS,
        "participants": [],
    }

    # 主持人
    mod_data = data.get("moderator")
    if mod_data and mod_data.get("enabled"):
        mod = parse_participant(mod_data)
        # 用內建主持人 system prompt，但允許使用者附加指示
        extra = mod["system"].strip()
        mod["system"] = MODERATOR_SYSTEM + ("\n\n使用者附加指示：" + extra if extra else "")
        config["moderator"] = mod

    config["generate_video"] = bool(data.get("generate_video"))

    for p in raw_participants:
        config["participants"].append(parse_participant(p))

    target = run_debate if config.get("moderator") else run_debate_no_moderator
    thread = threading.Thread(target=target, args=(config,), daemon=True)
    thread.start()
    return jsonify({"status": "started", "session_id": sess.session_id})


@app.route("/api/stop", methods=["POST"])
def stop_debate():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id") or request.args.get("session_id", "")
    sess = get_session(session_id)
    if not sess:
        return jsonify({"error": "session 不存在"}), 404
    with sess.state_lock:
        if not sess.debate_state["running"]:
            return jsonify({"error": "沒有進行中的辯論"}), 400
        sess.debate_state["stop_requested"] = True
    return jsonify({"status": "stopping"})


@app.route("/api/human_input", methods=["POST"])
def submit_human_input():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")
    sess = get_session(session_id)
    if not sess or not sess.debate_state["waiting_for_human"]:
        return jsonify({"error": "目前不在等待人類輸入"}), 400
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "請輸入內容"}), 400
    try:
        sess.human_input_queue.put_nowait(text)
        return jsonify({"status": "ok"})
    except queue.Full:
        return jsonify({"error": "輸入已提交"}), 400


@app.route("/api/state")
def get_state():
    session_id = request.args.get("session_id", "")
    if session_id:
        sess = get_session(session_id)
        if not sess:
            return jsonify({"error": "session 不存在"}), 404
        with sess.state_lock:
            snapshot = dict(sess.debate_state)
        snapshot["session_id"] = session_id
        return jsonify(snapshot)
    # 無 session_id 時回傳所有 session 清單
    with _sessions_lock:
        result = []
        for sid, s in _sessions.items():
            result.append({"session_id": sid, "running": s.debate_state["running"]})
    return jsonify({"sessions": result})


@app.route("/api/events")
def events():
    session_id = request.args.get("session_id", "")
    sess = get_session(session_id)
    if not sess:
        return Response("event: error\ndata: {\"message\":\"session 不存在\"}\n\n",
                        mimetype="text/event-stream")

    q = queue.Queue(maxsize=200)
    with sess.eq_lock:
        sess.event_queues.append(q)

    def stream():
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            with sess.eq_lock:
                if q in sess.event_queues:
                    sess.event_queues.remove(q)

    return Response(stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route("/api/logs")
def list_logs():
    logs = sorted(OUTPUT_DIR.glob("debate_*.md"), reverse=True)
    result = []
    for log in logs[:20]:
        result.append({"name": log.name, "size": log.stat().st_size, "path": str(log)})
    return jsonify({"logs": result})


@app.route("/api/logs/<filename>")
def get_log(filename):
    log_path = (OUTPUT_DIR / filename).resolve()
    if not str(log_path).startswith(str(OUTPUT_DIR.resolve())):
        return jsonify({"error": "找不到檔案"}), 404
    if not log_path.exists() or not log_path.name.startswith("debate_"):
        return jsonify({"error": "找不到檔案"}), 404
    return Response(log_path.read_text(encoding="utf-8"), mimetype="text/markdown; charset=utf-8")


GENERATE_STYLE_PROFILES: dict[str, dict[str, str]] = {
    "trash-talk": {
        "label": "純嘴砲",
        "topic_rules": "整體節奏要直接、帶火藥味，允許犀利互嗆與公開拆台，但仍要保留可辯論的實質爭點。",
        "participant_rules": "角色要敢開酸、敢補刀、敢正面打臉對手，但不能只剩情緒，仍要拿出論點與例子。",
        "moderator_rules": "主持人要能控住失控場面，適度吐槽、拱火、逼雙方正面回應，不要太官腔。",
    },
    "serious": {
        "label": "正經派",
        "topic_rules": "整體風格偏理性嚴謹，著重結構、定義與政策含義，減少低階情緒發言。",
        "participant_rules": "角色應該論證完整、立場清楚，以邏輯、框架、案例推進攻防，不必刻意嘴砲。",
        "moderator_rules": "主持人重視節奏與歸納，追問要精準，語氣專業俐落但不要冷場。",
    },
    "variety": {
        "label": "綜藝感",
        "topic_rules": "整體像有張力的談話節目，節奏快、話題夠爆點，讓人一看就想聽雙方互槓。",
        "participant_rules": "角色可以更戲劇化、記憶點更高，講話要有梗、有節目效果，但核心論點要站得住腳。",
        "moderator_rules": "主持人要像節目主持一樣會接球、抖包袱、催節奏，把場子炒熱。",
    },
    "courtroom": {
        "label": "法庭攻防",
        "topic_rules": "整體像交叉詰問，題目要能讓雙方圍繞證據、責任、因果與矛盾點來回攻防。",
        "participant_rules": "角色要擅長逼問、抓漏洞、要求證據，像檢辯雙方互相拆穿說法破綻。",
        "moderator_rules": "主持人像法官兼審判長，重點是控時、要求回應問題、阻止答非所問。",
    },
    "scholar": {
        "label": "學者交鋒",
        "topic_rules": "整體維持知識密度與思辨深度，適合談理論、數據、歷史脈絡與政策後果。",
        "participant_rules": "角色偏向研究者或專家型辯手，擅長引用案例、模型、數據與原理，但仍要敢於反駁。",
        "moderator_rules": "主持人重視概念釐清與爭點收束，必要時把抽象論點拉回具體案例。",
    },
    "internet": {
        "label": "酸民開戰",
        "topic_rules": "整體像高品質網路戰場，語感更接地氣、更像留言區互槓，但主題仍需夠具體。",
        "participant_rules": "角色可以更嗆、更接地氣、更像不同派系網友開戰，但不能只會情緒輸出，仍要有可驗證觀點。",
        "moderator_rules": "主持人要像熟悉網路輿論節奏的版主，能點破幹話、抓爭點、避免洗版。",
    },
}
DEFAULT_GENERATE_STYLE = "trash-talk"


def _resolve_generate_style(style: str | None) -> dict[str, str]:
    style_key = str(style or "").strip()
    return GENERATE_STYLE_PROFILES.get(style_key, GENERATE_STYLE_PROFILES[DEFAULT_GENERATE_STYLE])


def _request_generation_json(model: str, prompt_text: str, max_tokens: int = 1100) -> dict:
    _, resp = _create_chat_completion(
        model=model,
        messages=_build_messages("", [{"role": "user", "content": prompt_text}]),
        max_tokens=max_tokens,
        temperature=0.75,
        stream=True,
    )
    chunks = []
    for chunk in resp:
        if chunk.choices and chunk.choices[0].delta.content:
            chunks.append(chunk.choices[0].delta.content)
    full = _to_taiwan_traditional("".join(chunks))
    full = re.sub(r"<think>.*?</think>\s*", "", full, flags=re.DOTALL)
    json_match = re.search(r'\{[\s\S]*\}', full)
    if not json_match:
        raise ValueError("LLM 未回傳有效 JSON")
    return json.loads(json_match.group(0))


def _generate_topic_config(model: str, category: str, outline: str, style_profile: dict[str, str]) -> str:
    prompt = f"""你是辯論題目設定器。請根據以下條件，只回傳 JSON：
{{
  "topic": "完整的辯論主題描述（120-220 字，包含背景、範圍、具體要討論的面向，以及辯論規則）"
}}

分類：{category}
大綱：{outline or '（無，請自由發揮）'}
辯論風格：{style_profile["label"]}

要求：
1. 主題必須有明確衝突與對立空間
2. 要包含「互相挑戰對方論點，不要和稀泥」，並允許合理嗆聲
3. {style_profile["topic_rules"]}
4. 全部使用繁體中文"""
    data = _request_generation_json(model, prompt, max_tokens=360)
    topic = str(data.get("topic", "")).strip()
    if not topic:
        raise ValueError("LLM 未產出有效 topic")
    return topic


def _generate_participants_config(model: str, category: str, outline: str, topic: str, count: int, style_profile: dict[str, str]) -> list[dict]:
    prompt = f"""你是辯論角色設計器。請根據以下條件，只回傳 JSON：
{{
  "participants": [
    {{
      "name": "角色名稱（有特色的暱稱 + 立場說明）",
      "system": "角色的人格設定（90-160 字，包含：身分背景、核心立場、辯論風格、偏好的論證方式）"
    }}
  ]
}}

分類：{category}
大綱：{outline or '（無，請自由發揮）'}
辯論主題：{topic}
辯論風格：{style_profile["label"]}
需要人數：{count}

要求：
1. 每位參與者的立場和觀點要有明確差異，形成強烈對立或互補
2. {style_profile["participant_rules"]}
3. 避免太溫和、太客氣、太像公關稿
4. 角色名稱要有辨識度，例如「張守正（保守派經濟學者）」而非「AI-1」
5. 全部使用繁體中文"""
    data = _request_generation_json(model, prompt, max_tokens=900)
    raw_participants = data.get("participants") or []
    participants = []
    for item in raw_participants:
        name = str((item or {}).get("name", "")).strip()
        system = str((item or {}).get("system", "")).strip()
        if name and system:
            participants.append({"name": name, "system": system})
        if len(participants) >= count:
            break
    if len(participants) < count:
        raise ValueError("LLM 產出的參與者數量不足")
    return participants


def _generate_moderator_config(model: str, category: str, topic: str, participant_names: list[str], style_profile: dict[str, str]) -> dict:
    moderator_prompt = f"""你要產出一個辯論設定中的主持人欄位。請只回傳 JSON：
{{
  "moderator": {{
    "name": "主持人名稱",
    "system": "主持人風格與引導重點（60-120 字）"
  }}
}}

辯論分類：{category}
辯論主題：{topic}
辯論風格：{style_profile["label"]}
參與者：{", ".join(participant_names) or '未命名參與者'}

要求：
1. 主持人需中立、會控場、會追問、會收斂爭點
2. 名稱要自然，不要出現模型名稱
3. system 要用繁體中文，且適合作為主持人附加指示
4. {style_profile["moderator_rules"]}"""
    data = _request_generation_json(model, moderator_prompt, max_tokens=420)
    moderator = data.get("moderator") or {}
    name = str(moderator.get("name", "")).strip()
    system = str(moderator.get("system", "")).strip()
    if not name or not system:
        raise ValueError("LLM 未產出有效主持人設定")
    return {"name": name, "system": system}


def _generate_config_data(category: str, outline: str, count: int, model: str, style: str, progress_callback=None) -> dict:
    style_profile = _resolve_generate_style(style)

    def emit(payload: dict):
        if progress_callback:
            progress_callback(payload)

    emit({
        "type": "stage",
        "stage": "ANALYZING",
        "stepIndex": 0,
        "progress": 8,
        "message": f"正在分析主題方向與「{style_profile['label']}」風格...",
    })
    topic = _generate_topic_config(model, category, outline, style_profile)
    emit({
        "type": "topic",
        "stage": "SHAPING",
        "stepIndex": 1,
        "progress": 28,
        "message": "主題已生成，準備設計角色立場...",
        "topic": topic,
    })

    emit({
        "type": "stage",
        "stage": "CASTING",
        "stepIndex": 2,
        "progress": 36,
        "message": f"正在生成「{style_profile['label']}」風格的辯論參與者...",
    })
    participants = _generate_participants_config(model, category, outline, topic, count, style_profile)
    participant_start = 36
    participant_end = 82
    participant_span = max(participant_end - participant_start, 1)
    for index, participant in enumerate(participants, start=1):
        progress = participant_start + round(participant_span * (index / len(participants)))
        emit({
            "type": "participant",
            "stage": "CASTING",
            "stepIndex": 2,
            "progress": progress,
            "message": f"已完成 {index}/{len(participants)} 位辯論者設定...",
            "participant": participant,
            "index": index,
            "count": len(participants),
        })

    emit({
        "type": "stage",
        "stage": "FINALIZING",
        "stepIndex": 3,
        "progress": 88,
        "message": "正在補完主持人與收斂設定...",
    })
    moderator = _generate_moderator_config(
        model,
        category,
        topic,
        [participant["name"] for participant in participants],
        style_profile,
    )
    config = {
        "topic": topic,
        "moderator": moderator,
        "participants": participants,
        "style": style_profile["label"],
    }
    emit({
        "type": "moderator",
        "stage": "FINALIZING",
        "stepIndex": 3,
        "progress": 94,
        "message": "主持人設定已生成，整理最終結果...",
        "moderator": moderator,
    })
    return config


@app.route("/api/generate_config", methods=["POST"])
def generate_config():
    """用 LLM 根據分類與大綱自動生成辯論設定"""
    data = request.get_json(silent=True) or {}
    category = data.get("category", "")
    outline = data.get("outline", "")
    try:
        count = int(data.get("count", 3))
    except (ValueError, TypeError):
        count = 3
    count = max(2, min(count, MAX_AI_PARTICIPANTS))
    model = data.get("model", DEFAULT_FREE_MODEL)
    style = data.get("style", DEFAULT_GENERATE_STYLE)

    try:
        config = _generate_config_data(category, outline, count, model, style)
        return jsonify(config)
    except Exception as e:
        app.logger.error("generate_config 失敗: %s", e)
        return jsonify({"error": "生成設定時發生錯誤，請稍後再試"}), 500


@app.route("/api/generate_config_stream", methods=["POST"])
def generate_config_stream():
    data = request.get_json(silent=True) or {}
    category = data.get("category", "")
    outline = data.get("outline", "")
    try:
        count = int(data.get("count", 3))
    except (ValueError, TypeError):
        count = 3
    count = max(2, min(count, MAX_AI_PARTICIPANTS))
    model = data.get("model", DEFAULT_FREE_MODEL)
    style = data.get("style", DEFAULT_GENERATE_STYLE)

    def emit_ndjson(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False) + "\n"

    @stream_with_context
    def streaming_generator():
        event_queue: queue.Queue = queue.Queue()

        def worker():
            try:
                config = _generate_config_data(
                    category,
                    outline,
                    count,
                    model,
                    style,
                    progress_callback=lambda payload: event_queue.put(payload),
                )
                event_queue.put({
                    "type": "done",
                    "stage": "FINALIZING",
                    "stepIndex": 3,
                    "progress": 100,
                    "message": "設定已生成完成。",
                    "config": config,
                })
            except Exception as e:
                app.logger.error("generate_config_stream 失敗: %s", e)
                event_queue.put({
                    "type": "error",
                    "error": "生成設定時發生錯誤，請稍後再試",
                })
            finally:
                event_queue.put(None)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            try:
                payload = event_queue.get(timeout=10)
            except queue.Empty:
                # Keep the NDJSON stream alive on hosted platforms while the model is still generating.
                yield emit_ndjson({"type": "keepalive"})
                continue
            if payload is None:
                break
            yield emit_ndjson(payload)

    return Response(streaming_generator(), mimetype="application/x-ndjson", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@app.route("/api/video/<filename>")
def get_video(filename):
    if not filename.endswith(".mp4"):
        return jsonify({"error": "找不到影片"}), 404
    for base in [VIDEO_DIR, OUTPUT_DIR]:
        candidate = (base / filename).resolve()
        if str(candidate).startswith(str(base.resolve())) and candidate.exists():
            return send_file(candidate, mimetype="video/mp4")
    return jsonify({"error": "找不到影片"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
