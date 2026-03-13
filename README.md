# AI Debate Arena

一個可自動生成辯論題目、角色設定與主持人，並支援多人同時觀戰的人機辯論系統。

目前專案採用：

- 後端：Flask
- 前端：React + Vite
- LLM Provider：OpenRouter
- 部署方式：可直接部署到 Render

## Features

- AI 自動生成辯論設定
  - 依主題分類、大綱補充、辯論風格，生成題目、AI 辯論者與主持人
- 六種辯論風格
  - `純嘴砲`、`正經派`、`綜藝感`、`法庭攻防`、`學者交鋒`、`酸民開戰`
- React 前端設定頁
  - 支援草稿保存、inline error、快速生成、歷史紀錄檢視
- 即時辯論事件串流
  - 透過 SSE 持續更新回合、發言、主持人提示與完成狀態
- 人類參與者模式
  - 可選擇讓真人加入戰局，並設定回覆時限
- 多場辯論管理
  - 同時最多 3 場進行中 session
- 台灣繁體中文輸出約束
  - 所有模型回應都會被要求使用台灣繁體中文，並做 OpenCC 轉換
- Render 友善部署
  - React build 後由 Flask 直接提供靜態資源與 API

## Demo Flow

1. 在設定頁選擇主題分類
2. 補充你想討論的大綱
3. 選擇辯論風格
4. 按下 `生成設定`
5. 微調題目、主持人與參與者
6. 按下 `開始辯論`

## Tech Stack

- Python 3.11+
- Flask
- OpenAI Python SDK（串 OpenRouter OpenAI-compatible API）
- OpenCC
- React 19
- Vite

## Project Structure

```text
.
├── app.py                 # Flask app + API + SSE + React dist serving
├── debate.py              # CLI 版辯論腳本
├── frontend/              # React frontend
│   ├── src/
│   ├── public/
│   └── package.json
├── templates/index.html   # 舊版 fallback 頁面
├── output/                # 辯論紀錄輸出
├── requirements.txt
└── render-build.sh        # Render build script
```

## Requirements

- Python 3.11 或以上
- Node.js 20 或以上
- npm
- 一組可用的 OpenRouter API Key

## Environment Variables

至少需要設定以下變數：

```bash
OPENROUTER_API_KEY=your_openrouter_key
LLM_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_DEFAULT_MODEL=openrouter/hunter-alpha
OPENROUTER_APP_NAME=ai-debate
OPENROUTER_APP_URL=https://your-service.onrender.com
```

支援的變數說明：

- `OPENROUTER_API_KEY`
  - 必填。主要使用的 API key
- `LLM_API_KEY`
  - 舊相容名稱。若未設 `OPENROUTER_API_KEY`，會讀這個
- `LLM_BASE_URL`
  - 預設為 `https://openrouter.ai/api/v1`
- `OPENROUTER_DEFAULT_MODEL`
  - 預設為 `openrouter/hunter-alpha`
- `OPENROUTER_APP_NAME`
  - 會作為 OpenRouter request header 的 `X-Title`
- `OPENROUTER_APP_URL`
  - 會作為 OpenRouter request header 的 `HTTP-Referer`
- `PORT`
  - 本機或雲端服務執行 port，預設 `5050`

## Local Development

### 1. Install backend dependencies

```bash
pip install -r requirements.txt
```

### 2. Install frontend dependencies

```bash
cd frontend
npm ci
cd ..
```

### 3. Build frontend

```bash
cd frontend
npm run build
cd ..
```

### 4. Run Flask server

```bash
python3 app.py
```

預設會啟在：

- [http://127.0.0.1:5050](http://127.0.0.1:5050)

## Frontend Development

如果你要只跑前端開發：

```bash
cd frontend
npm run dev
```

但正式整合時，仍建議 build 後由 Flask 提供靜態檔，因為這個專案的 SSE 與 API 原本就是同源設計。

## Render Deployment

這個專案目前最推薦的部署方式是：

- 一個 Render Web Service
- Flask 負責 API
- React build 後由 Flask 提供前端靜態資源

### Render settings

- Build Command

```bash
./render-build.sh
```

- Start Command

```bash
gunicorn app:app
```

### Render environment variables

至少填入：

- `OPENROUTER_API_KEY`
- `LLM_BASE_URL=https://openrouter.ai/api/v1`
- `OPENROUTER_DEFAULT_MODEL=openrouter/hunter-alpha`
- `OPENROUTER_APP_NAME=ai-debate`
- `OPENROUTER_APP_URL=https://your-service.onrender.com`

## API Overview

### `GET /api/models`

取得可用模型列表。

### `POST /api/generate_config_stream`

分階段生成辯論設定，回傳 NDJSON 串流事件。

主要事件類型：

- `stage`
- `topic`
- `participant`
- `moderator`
- `done`
- `error`

### `POST /api/start`

啟動一場辯論。

重要限制：

- 至少 2 位 AI 辯論者
- 最多 4 位 AI 辯論者
- 最多 1 位人類參與者
- 同時最多 3 場進行中的辯論

### `GET /api/events`

SSE 事件流，提供即時辯論狀態。

### `POST /api/human_input`

在等待人類輸入時提交內容。

### `POST /api/stop`

手動停止指定 session。

### `GET /api/logs`

列出最近辯論紀錄。

## Behavior Notes

- AI 輸出目標會盡量精簡，但不是死硬切成 300 字
- 系統會優先要求觀點短、節奏快，但仍要把句子與結論講完
- 若模型臨時不可用，後端會嘗試 fallback 到其他免費模型
- 部分免費模型不支援某些 role 行為，因此系統已把角色規則整併進 user content 做相容處理

## Known Limitations

- 生成設定的總耗時仍取決於 OpenRouter 模型速度
- 免費模型可用性會波動，偶爾會 fallback 或失敗
- 目前沒有完整自動化測試
- `templates/index.html` 仍保留作為舊版 fallback，不是主要前端來源

## Development Notes

如果你要修改前端，主要入口在：

- [frontend/src/App.jsx](/Users/chenyuda/ai-debate/frontend/src/App.jsx)
- [frontend/src/components/SettingsPage.jsx](/Users/chenyuda/ai-debate/frontend/src/components/SettingsPage.jsx)
- [frontend/src/components/DebateWorkspace.jsx](/Users/chenyuda/ai-debate/frontend/src/components/DebateWorkspace.jsx)

如果你要修改後端生成流程，主要入口在：

- [app.py](/Users/chenyuda/ai-debate/app.py)

## Verification

目前建議至少做以下檢查：

```bash
python3 -m compileall app.py debate.py
cd frontend && npm run build
```

## Security

請不要把 `.env`、API keys、或真實憑證提交到 repo。

## License

本專案採用 `MIT License`。

授權條款請見 [LICENSE](/Users/chenyuda/ai-debate/LICENSE)。
