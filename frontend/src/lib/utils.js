export function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function renderMarkdown(text) {
  let html = escapeHtml(text ?? "");
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/^&gt; (.+)$/gm, "<blockquote>$1</blockquote>");
  html = html.replace(/^---$/gm, "<hr>");
  html = html.replace(/^(?:-|\*) (.+)$/gm, "<li>$1</li>");
  html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>");
  html = html.replace(/^\d+\. (.+)$/gm, "<oli>$1</oli>");
  html = html.replace(/((?:<oli>.*<\/oli>\n?)+)/g, (match) =>
    `<ol>${match.replaceAll("oli", "li")}</ol>`,
  );
  html = html.replace(/\n{2,}/g, "</p><p>");
  html = html.replace(/\n/g, "<br>");
  return `<p>${html}</p>`;
}

export function clampNumber(value, min, max, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

export function formatLogTimestamp(name) {
  return name
    .replace("debate_", "")
    .replace(".md", "")
    .replace(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/, "$1/$2/$3 $4:$5:$6");
}

export function buildParticipant(preset = {}) {
  return {
    id: crypto.randomUUID(),
    name: preset.name || "AI-新參與者",
    model: preset.model || "",
    system: preset.system || "你是一位 AI 辯論參與者。立場要鮮明，講話可以嗆一點，但要有邏輯。用繁體中文，盡量精簡在 300 字左右，但論點要完整收尾。",
    human: Boolean(preset.human),
    locked: Boolean(preset.locked),
  };
}

export function defaultTab(sessionId, moderatorName, participants) {
  return {
    id: `tab-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    sessionId,
    moderatorName: moderatorName || "",
    participants,
    running: true,
    statusText: "辯論中",
    roundInfo: "",
    messages: [],
    humanInputVisible: false,
    waitingSpeaker: "",
    humanInputText: "",
    countdownSeconds: 0,
    countdownWarningShown: false,
    countdownSkipped: false,
    loaderVisible: true,
    loaderStage: "initializing",
    loaderProgress: 0,
    loaderSteps: {
      connect: "active",
      prepare: "",
      moderator: "",
      start: "",
    },
  };
}

export function guessSpeakerClass(speaker, tab) {
  const text = String(speaker || "");
  const lower = text.toLowerCase();
  if (
    (tab?.moderatorName && text.includes(tab.moderatorName))
    || text.includes("主持人")
    || text.includes("主持")
  ) {
    return "moderator";
  }
  if (text.includes("總結")) return "summary";
  const participants = tab?.participants || [];
  for (let index = 0; index < participants.length; index += 1) {
    const participant = participants[index];
    if (participant.model?.toLowerCase().startsWith("human")) {
      if (text.includes(participant.name) || participant.name.includes(text.split("（")[0])) {
        return "human";
      }
    }
    if (text.includes(participant.name) || participant.name.includes(text.split("（")[0])) {
      return `ai-${index % 4}`;
    }
  }
  if (lower.includes("gpt")) return "ai-0";
  if (lower.includes("grok")) return "ai-1";
  if (lower.includes("claude")) return "ai-2";
  return "ai-0";
}
