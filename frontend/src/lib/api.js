async function parseJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `Request failed with status ${response.status}`);
  }
  return data;
}

export function getModels() {
  return fetch("/api/models").then(parseJson);
}

export function generateConfig(payload) {
  return fetch("/api/generate_config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(parseJson);
}

export async function generateConfigStream(payload, onEvent, options = {}) {
  const response = await fetch("/api/generate_config_stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: options.signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Request failed with status ${response.status}`);
  }
  if (!response.body) {
    throw new Error("瀏覽器不支援串流回應");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalConfig = null;

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const event = JSON.parse(trimmed);
      onEvent?.(event);
      if (event.type === "done") finalConfig = event.config || null;
      if (event.type === "error") throw new Error(event.error || "生成設定失敗");
    }

    if (done) break;
  }

  if (buffer.trim()) {
    const event = JSON.parse(buffer.trim());
    onEvent?.(event);
    if (event.type === "done") finalConfig = event.config || null;
    if (event.type === "error") throw new Error(event.error || "生成設定失敗");
  }

  if (!finalConfig) {
    throw new Error("生成流程未完成");
  }
  return finalConfig;
}

export function startDebate(payload) {
  return fetch("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(parseJson);
}

export function stopDebate(sessionId) {
  return fetch("/api/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  }).then(parseJson);
}

export function sendHumanInput(sessionId, text) {
  return fetch("/api/human_input", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, text }),
  }).then(parseJson);
}

export function getState(sessionId) {
  return fetch(`/api/state?session_id=${encodeURIComponent(sessionId)}`).then(parseJson);
}

export function getLogs() {
  return fetch("/api/logs").then(parseJson);
}

export function getLog(filename) {
  return fetch(`/api/logs/${encodeURIComponent(filename)}`).then(async (response) => {
    if (!response.ok) {
      throw new Error("載入失敗");
    }
    return response.text();
  });
}
