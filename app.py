#!/usr/bin/env python3
"""AI 辯論 - Web 控制面板（含主持人機制，支援多場同時辯論）"""

import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

app = Flask(__name__)

API_KEY = os.environ["LLM_API_KEY"]
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.banana2556.com/v1")
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# proxy base（去掉 /v1）
PROXY_BASE = BASE_URL.rstrip("/")
if PROXY_BASE.endswith("/v1"):
    PROXY_BASE = PROXY_BASE[:-3]

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, default_headers={"User-Agent": "ai-debate/1.0"})

_anthropic_client = None


def get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(
            api_key=API_KEY,
            base_url=PROXY_BASE,
            default_headers={"User-Agent": "ai-debate/1.0"},
        )
    return _anthropic_client


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

RETRYABLE_KEYWORDS = ("503", "overload", "timeout", "502", "429")


def _is_retryable(err_str: str) -> bool:
    return any(k in err_str for k in RETRYABLE_KEYWORDS)


def _with_retry(session_id: str, model: str, fn, error_prefix: str = "API") -> str:
    """共用重試包裝：fn() 應回傳 str 或拋例外"""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            if _is_retryable(str(e)) and attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                broadcast(session_id, "status", {
                    "message": f"{model} 暫時不可用，{delay}s 後重試（{attempt+1}/{MAX_RETRIES}）..."
                })
                time.sleep(delay)
                continue
            return f"[{error_prefix} 錯誤: {e}]"
    return f"[{error_prefix} 錯誤: 重試次數已用盡]"


def call_api(session_id: str, model: str, system: str, messages: list[dict], max_tokens: int) -> str:
    def _call():
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}] + messages,
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
        return full.strip()

    return _with_retry(session_id, model, _call, "API")


def call_gemini(session_id: str, model: str, system: str, prompt: str, max_tokens: int) -> str:
    """透過 proxy 的 Gemini relay API 呼叫"""
    import requests

    def _call():
        url = f"{PROXY_BASE}/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.8,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=180,
        )
        data = resp.json()
        if "error" in data:
            err_msg = str(data["error"].get("message", data["error"]))
            raise RuntimeError(err_msg)
        candidates = data.get("candidates", [])
        if not candidates:
            return "[Gemini 錯誤: 回應中無 candidates]"
        text = (candidates[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", ""))
        return text.strip()

    return _with_retry(session_id, model, _call, "Gemini")


def call_claude_api(session_id: str, model: str, system: str, prompt: str, max_tokens: int) -> str:
    def _call():
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.8,
        )
        return resp.choices[0].message.content.strip()

    return _with_retry(session_id, model, _call, "Claude API")


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
        response = sess.human_input_queue.get(timeout=600)
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
    elif via == "claude":
        return call_claude_api(session_id, participant["model"], participant["system"], prompt, max_tokens)
    else:
        # GPT / Grok / Gemini 全部走 OpenAI 格式
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


def build_context(history: list[dict], latest_n: int = 6) -> str:
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
風格：專業、中立、有條理，適時展現幽默感
每次發言控制在 300 字以內（結論報告除外）"""


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

        sess.debate_state["current_round"] = round_num
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
        sess.debate_state["current_round"] = ctx.rounds
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
        ctx.speak(moderator, final_prompt, rnd=ctx.rounds, phase="final", max_tok=3000)
        ctx.write_log(f"\n**結束時間：** {datetime.now():%Y-%m-%d %H:%M:%S}\n")

    ctx.finish()


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
        sess.debate_state["current_round"] = round_num
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
        final = call_participant(session_id, participants[0], final_prompt, 2000)
        ctx.write_log(f"## 最終總結\n\n{final}\n\n")
        ctx.write_log(f"**結束時間：** {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        broadcast(session_id, "message", {"speaker": "最終總結", "content": final, "round": ctx.rounds, "phase": "final"})

    ctx.finish()


# ── 路由 ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/models")
def get_models():
    try:
        resp = client.models.list()
        models = [m.id for m in resp.data if "imagine" not in m.id]
        models.sort()
    except Exception:
        models = ["claude-sonnet-4-6", "claude-opus-4-6"]

    models.append("human (you)")
    return jsonify({"models": models})


def parse_participant(p: dict) -> dict:
    model = p.get("model", "gpt-5.4")
    ml = model.lower()
    if ml.startswith("human"):
        via, actual_model = "human", "human"
    elif ml.startswith("claude"):
        via, actual_model = "claude", model
    else:
        # GPT / Grok / Gemini 都走 OpenAI 格式
        via, actual_model = "api", model
    return {
        "name": p.get("name", model),
        "model": actual_model,
        "via": via,
        "system": p.get("system", ""),
    }


@app.route("/api/start", methods=["POST"])
def start_debate():
    sess = create_session()

    data = request.get_json(silent=True) or {}
    try:
        rounds = max(1, min(int(data.get("rounds", 10)), 200))
        max_tokens = max(100, min(int(data.get("max_tokens", 1000)), 4000))
    except (ValueError, TypeError):
        return jsonify({"error": "輪數或 Token 上限格式不正確"}), 400

    config = {
        "session_id": sess.session_id,
        "topic": data.get("topic", "目前最有效的賺錢方式"),
        "rounds": rounds,
        "max_tokens": max_tokens,
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

    for p in data.get("participants", []):
        config["participants"].append(parse_participant(p))

    if len(config["participants"]) < 2:
        return jsonify({"error": "至少需要 2 位參與者"}), 400

    with sess.state_lock:
        sess.debate_state["running"] = True

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


@app.route("/api/generate_config", methods=["POST"])
def generate_config():
    """用 LLM 根據分類與大綱自動生成辯論設定"""
    data = request.get_json(silent=True) or {}
    category = data.get("category", "")
    outline = data.get("outline", "")
    count = int(data.get("count", 3))
    count = max(2, min(count, 6))

    prompt = f"""你是辯論設定產生器。請根據以下條件產出 JSON 設定：

分類：{category}
大綱：{outline or '（無，請自由發揮）'}
辯論人數：{count}

請產出 JSON，格式如下（不要其他文字，純 JSON）：
{{
  "topic": "完整的辯論主題描述（200-400 字，包含背景、範圍、具體要討論的面向，以及辯論規則）",
  "participants": [
    {{
      "name": "角色名稱（有特色的暱稱 + 立場說明）",
      "system": "角色的人格設定（150-250 字，包含：身分背景、核心立場、辯論風格、偏好的論證方式）"
    }}
  ]
}}

要求：
1. 每位參與者的立場和觀點要有明確差異，形成對立或互補
2. 角色名稱要有辨識度，例如「張守正（保守派經濟學者）」而非「AI-1」
3. 人格設定要具體、有深度，讓 AI 能據此產出有特色的觀點
4. topic 要包含「互相挑戰對方論點，不要和稀泥」
5. 全部使用繁體中文"""

    model = data.get("model", "claude-sonnet")

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.9,
            stream=True,
        )
        chunks = []
        for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                    chunks.append(chunk.choices[0].delta.content)
            full = "".join(chunks)

        full = re.sub(r"<think>.*?</think>\s*", "", full, flags=re.DOTALL)
        # 提取 JSON
        json_match = re.search(r'\{[\s\S]*\}', full)
        if json_match:
            config = json.loads(json_match.group(0))
            return jsonify(config)
        return jsonify({"error": "LLM 未回傳有效 JSON"}), 500
    except Exception as e:
        app.logger.error("generate_config 失敗: %s", e)
        return jsonify({"error": "生成設定時發生錯誤，請稍後再試"}), 500


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
    app.run(host="127.0.0.1", port=5050)
