#!/usr/bin/env python3
"""三方 AI 徹夜辯論：GPT-5.4 vs Grok-4.1-expert vs Claude (CLI)"""

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

# ── 設定 ──────────────────────────────────────────────
API_KEY = os.environ["LLM_API_KEY"]
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.banana2556.com/v1")
ROUNDS = int(os.environ.get("DEBATE_ROUNDS", "30"))
MAX_TOKENS = int(os.environ.get("DEBATE_MAX_TOKENS", "1000"))

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
        "name": "GPT-5.4（策略分析師）",
        "model": "gpt-5.4",
        "via": "api",
        "system": (
            "你是 GPT-5.4，一位數據驅動的策略分析師。"
            "你偏好有數據支撐、可量化的賺錢策略。"
            "對於過度樂觀的預期要用數據反駁。"
            "每次回覆控制在 500 字內，用繁體中文。"
            "直接回應其他 AI 的論點，可以同意、反駁或延伸。"
        ),
    },
    {
        "name": "Grok-4.1（激進創新派）",
        "model": "grok-4.1-expert",
        "via": "api",
        "system": (
            "你是 Grok-4.1，一位激進的科技與創新趨勢專家。"
            "你偏好高報酬、前沿技術的賺錢方式，願意承擔較高風險。"
            "善於發現被低估的機會，挑戰保守觀點。"
            "每次回覆控制在 500 字內，用繁體中文。"
            "直接回應其他 AI 的論點，可以同意、反駁或延伸。"
        ),
    },
    {
        "name": "Claude（務實風險管理者）",
        "model": "claude",
        "via": "cli",
        "system": (
            "你是 Claude，一位務實的風險管理者與長期投資思考者。"
            "你重視風險調整後報酬、可持續性、以及實際可執行性。"
            "會指出其他 AI 忽略的風險和盲點。"
            "每次回覆控制在 500 字內，用繁體中文。"
            "直接回應其他 AI 的論點，可以同意、反駁或延伸。"
        ),
    },
]

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def call_api(model: str, system: str, messages: list[dict]) -> str:
    """透過 OpenAI-compatible API 呼叫"""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}] + messages,
            max_tokens=MAX_TOKENS,
            temperature=0.8,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"[API 錯誤: {e}]"


def call_claude_cli(system: str, prompt: str) -> str:
    """透過 claude CLI pipe mode 呼叫"""
    try:
        result = subprocess.run(
            ["claude", "-p", "--system", system],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return f"[CLI 錯誤: {result.stderr.strip()}]"
    except subprocess.TimeoutExpired:
        return "[CLI 逾時]"
    except Exception as e:
        return f"[CLI 錯誤: {e}]"


def build_context(history: list[dict], latest_n: int = 6) -> str:
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

    if participant["via"] == "api":
        messages = [{"role": "user", "content": prompt}]
        return call_api(participant["model"], participant["system"], messages)
    else:
        return call_claude_cli(participant["system"], prompt)


def write_log(content: str):
    """追加寫入 log"""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(content)


def summarize(history: list[dict]) -> str:
    """讓 GPT-5.4 產出最終總結"""
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
        "gpt-5.4",
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

    # 開場：由 GPT 拋出第一個論點
    opening = (
        f"辯論主題：{TOPIC}\n\n"
        "請先提出你認為目前最有效的 3 個賺錢方式，並說明理由。"
    )
    first = PARTICIPANTS[0]
    print(f"[開場] {first['name']} 發言中...")
    if first["via"] == "api":
        response = call_api(first["model"], first["system"], [{"role": "user", "content": opening}])
    else:
        response = call_claude_cli(first["system"], opening)

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
