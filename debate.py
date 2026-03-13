#!/usr/bin/env python3
"""三方 AI 徹夜辯論：全部走 OpenRouter 免費模型"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from opencc import OpenCC
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

# ── 設定 ──────────────────────────────────────────────
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
ROUNDS = int(os.environ.get("DEBATE_ROUNDS", "30"))
MAX_TOKENS = int(os.environ.get("DEBATE_MAX_TOKENS", "650"))
TARGET_DEBATE_RESPONSE_CHARS = 300
SAFETY_DEBATE_RESPONSE_CHARS = 900

DEBATE_DIR = Path(__file__).parent
OUTPUT_DIR = DEBATE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
LOG_FILE = OUTPUT_DIR / f"debate_{datetime.now():%Y%m%d_%H%M%S}.md"

TOPIC = """目前最有效的賺錢方式。涵蓋範圍包括但不限於：
- 預測市場套利（如 Polymarket）
- AI 應用變現（SaaS、自動化代理）
- 加密貨幣與 DeFi 策略
- 傳統投資（股票、房地產、債券）
- 自媒體與個人品牌
- 自動化被動收入

請提出具體、可執行的方案，附上預估報酬率與風險評估。
互相挑戰對方論點，不要和稀泥。"""

# ── AI 角色 ──────────────────────────────────────────
PARTICIPANTS = [
    {
        "name": "AI-1（策略分析師）",
        "model": DEFAULT_FREE_MODEL,
        "via": "api",
        "system": (
            "你是一位數據驅動的策略分析師。"
            "你偏好用數字和案例拆穿空話。"
            "對於過度樂觀的預期要直接吐槽漏洞，但不是亂罵。"
            "每次回覆盡量精簡在 300 字左右，但若論點未完要把句子講完，用繁體中文。"
            "直接回應其他 AI 的論點，可以反駁、補刀或延伸。"
        ),
    },
    {
        "name": "AI-2（激進創新派）",
        "model": DEFAULT_FREE_MODEL,
        "via": "api",
        "system": (
            "你是一位激進的科技與創新趨勢專家。"
            "你偏好高報酬、高風險的新機會，看到保守論點就會開酸。"
            "善於發現被低估的機會，挑戰保守觀點。"
            "每次回覆盡量精簡在 300 字左右，但若論點未完要把句子講完，用繁體中文。"
            "直接回應其他 AI 的論點，可以強力反駁或延伸。"
        ),
    },
    {
        "name": "AI-3（務實風險管理者）",
        "model": DEFAULT_FREE_MODEL,
        "via": "api",
        "system": (
            "你是一位務實的風險管理者與長期投資思考者。"
            "你重視風險調整後報酬、可持續性、以及實際可執行性。"
            "會指出其他 AI 忽略的風險和盲點，語氣冷靜但會補刀。"
            "每次回覆盡量精簡在 300 字左右，但若論點未完要把句子講完，用繁體中文。"
            "直接回應其他 AI 的論點，可以反駁或延伸。"
        ),
    },
]

client_headers = {
    "User-Agent": "ai-debate/1.0",
    "X-Title": APP_NAME,
}
if APP_URL:
    client_headers["HTTP-Referer"] = APP_URL

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, default_headers=client_headers)
opencc_tw = OpenCC("s2twp")
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


def _should_fallback_model(err_str: str) -> bool:
    lower = err_str.lower()
    return (
        "429" in lower
        or "connection error" in lower
        or "no endpoints available" in lower
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


def call_api(model: str, system: str, messages: list[dict]) -> str:
    """透過 OpenAI-compatible API 呼叫"""
    request_messages = _build_messages(system, messages)
    last_error = None
    for candidate in _candidate_models(model):
        try:
            resp = client.chat.completions.create(
                model=candidate,
                messages=request_messages,
                max_tokens=MAX_TOKENS,
                temperature=0.8,
            )
            content = resp.choices[0].message.content or ""
            content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL)
            return _truncate_debate_output(_to_taiwan_traditional(content).strip())
        except Exception as e:
            last_error = e
            if not _should_fallback_model(str(e)):
                break
    return f"[API 錯誤: {last_error}]"


def build_context(history: list[dict], latest_n: int = 4) -> str:
    """將最近 N 則對話歷史組成文字上下文"""
    recent = history[-latest_n:] if len(history) > latest_n else history
    lines = []
    for msg in recent:
        lines.append(f"【{msg['speaker']}】\n{msg['content']}\n")
    return "\n".join(lines)


def get_response(participant: dict, history: list[dict], round_num: int) -> str:
    """取得 AI 的回應"""
    context = build_context(history)
    prompt = f"這是第 {round_num} 輪辯論。以下是近期的討論內容：\n\n{context}\n\n請回應以上論點。"
    messages = [{"role": "user", "content": prompt}]
    return call_api(participant["model"], participant["system"], messages)


def write_log(content: str):
    """追加寫入 log"""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(content)


def summarize(history: list[dict]) -> str:
    """讓 OpenRouter 免費模型產出最終總結"""
    context = build_context(history, latest_n=len(history))
    prompt = (
        "請根據以上所有討論內容，產出一份完整的總結報告：\n"
        "1. 三方共識的賺錢方式（附具體方案）\n"
        "2. 主要分歧點\n"
        "3. 風險最低的前 3 個方案\n"
        "4. 報酬潛力最高的前 3 個方案\n"
        "5. 綜合推薦：現在最值得執行的前 5 個賺錢方式\n\n"
        f"討論全文：\n{context}"
    )
    messages = [{"role": "user", "content": prompt}]
    return call_api(
        DEFAULT_FREE_MODEL,
        "你是一位中立的分析師，負責總結三方 AI 的辯論結果。用繁體中文，結構化輸出。",
        messages,
    )


def main():
    print("=== 三方 AI 辯論開始 ===")
    print(f"主題：最有效的賺錢方式")
    print(f"參與者：{', '.join(p['name'] for p in PARTICIPANTS)}")
    print(f"輪數：{ROUNDS}")
    print(f"紀錄檔：{LOG_FILE}")
    print()

    write_log("# 三方 AI 徹夜辯論：最有效的賺錢方式\n\n")
    write_log(f"**開始時間：** {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    write_log(f"**參與者：** {', '.join(p['name'] for p in PARTICIPANTS)}\n")
    write_log(f"**輪數：** {ROUNDS}\n\n---\n\n")

    history: list[dict] = []

    # 開場：由第一位參與者拋出第一個論點
    opening = (
        f"辯論主題：{TOPIC}\n\n"
        "請先提出你認為目前最有效的 3 個賺錢方式，並說明理由。"
    )
    first = PARTICIPANTS[0]
    print(f"[開場] {first['name']} 發言中...")
    response = call_api(first["model"], first["system"], [{"role": "user", "content": opening}])

    history.append({"speaker": first["name"], "content": response})
    write_log(f"## 開場 - {first['name']}\n\n{response}\n\n---\n\n")
    print(f"  完成 ({len(response)} 字)\n")

    # 主要辯論循環
    for round_num in range(1, ROUNDS + 1):
        write_log(f"## 第 {round_num} 輪\n\n")
        print(f"=== 第 {round_num}/{ROUNDS} 輪 ===")

        for i, participant in enumerate(PARTICIPANTS):
            if round_num == 1 and i == 0:
                continue

            print(f"  {participant['name']} 發言中...", end=" ", flush=True)
            response = get_response(participant, history, round_num)
            history.append({"speaker": participant["name"], "content": response})
            write_log(f"### {participant['name']}\n\n{response}\n\n")
            print(f"完成 ({len(response)} 字)")

            time.sleep(2)

        write_log("---\n\n")
        print()

        # 每 10 輪做一次中期總結
        if round_num % 10 == 0 and round_num < ROUNDS:
            print(f"  [中期總結] 第 {round_num} 輪...")
            mid_summary = summarize(history)
            write_log(f"## 中期總結（第 {round_num} 輪後）\n\n{mid_summary}\n\n---\n\n")
            history.append({"speaker": "主持人", "content": f"中期總結：\n{mid_summary}"})
            print("  完成\n")

    # 最終總結
    print("=== 產出最終總結 ===")
    final_summary = summarize(history)
    write_log(f"## 最終總結\n\n{final_summary}\n\n")
    write_log(f"**結束時間：** {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    print(f"\n辯論結束！完整紀錄：{LOG_FILE}")


if __name__ == "__main__":
    main()
