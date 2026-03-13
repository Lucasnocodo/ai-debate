import { guessSpeakerClass } from "./utils";

function extractSpeakerNames(markdown) {
  const names = new Set();
  const moderator = markdown.match(/\*\*主持人：\*\*\s*(.+)/);
  if (moderator) names.add(moderator[1].trim());
  const participants = markdown.match(/\*\*參與者：\*\*\s*(.+)/);
  if (participants) {
    participants[1].split(/,\s*/).forEach((name) => names.add(name.trim()));
  }
  return names;
}

function isSpeakerHeading(text, speakerNames) {
  const heading = text.trim();
  if (speakerNames.has(heading)) return true;
  const lower = heading.toLowerCase();
  return lower.includes("gpt") || lower.includes("grok") || lower.includes("claude") || lower.includes("主持人");
}

function isStructuralH2(text) {
  return /^(第\s*\d+\s*輪|開場|最終結論|最終總結)/.test(text.trim());
}

function flushSpeakerContent(speaker, contentLines, target, tab) {
  if (!speaker || contentLines.length === 0) return;
  const content = contentLines.join("\n").replace(/^---\s*$/gm, "").trim();
  if (!content) return;

  const parts = content.split(/(\*\*\[場景影片\]\*\*\s+https?:\/\/[^\s]+\.mp4[^\s]*)/);
  const buffered = [];
  const pushBuffered = () => {
    const text = buffered.join("\n").trim();
    if (text) {
      target.push({
        id: crypto.randomUUID(),
        type: "message",
        speaker,
        content: text,
        className: guessSpeakerClass(speaker, tab),
      });
    }
    buffered.length = 0;
  };

  parts.forEach((part) => {
    const video = part.match(/\*\*\[場景影片\]\*\*\s+(https?:\/\/[^\s]+\.mp4[^\s]*)/);
    if (video) {
      pushBuffered();
      target.push({
        id: crypto.randomUUID(),
        type: "video",
        speaker: "場景影片",
        url: video[1],
      });
    } else {
      buffered.push(part);
    }
  });
  pushBuffered();
}

export function parseLogToMessages(markdown, tab = null) {
  const speakerNames = extractSpeakerNames(markdown);
  const messages = [];
  const header = markdown.match(/^#\s+(.+)/m);
  if (header) {
    messages.push({
      id: crypto.randomUUID(),
      type: "round",
      label: header[1],
    });
  }

  const sections = markdown.split(/^(?=##\s|###\s)/gm);
  let currentSpeaker = null;
  let currentContent = [];

  sections.forEach((section) => {
    const trimmed = section.trim();
    if (!trimmed) return;
    const h2Match = trimmed.match(/^##\s+(.+)/);
    if (h2Match && !trimmed.startsWith("###")) {
      if (isStructuralH2(h2Match[1])) {
        flushSpeakerContent(currentSpeaker, currentContent, messages, tab);
        currentSpeaker = null;
        currentContent = [];
        messages.push({
          id: crypto.randomUUID(),
          type: "round",
          label: h2Match[1],
        });
        const remaining = trimmed.replace(/^##\s+.+\n?/, "").trim();
        if (remaining && !remaining.startsWith("###")) {
          messages.push({
            id: crypto.randomUUID(),
            type: "message",
            speaker: h2Match[1],
            content: remaining,
            className: guessSpeakerClass(h2Match[1], tab),
          });
        }
      } else if (currentSpeaker) {
        currentContent.push(trimmed);
      }
      return;
    }
    const h3Match = trimmed.match(/^###\s+(.+)/);
    if (h3Match) {
      if (isSpeakerHeading(h3Match[1], speakerNames)) {
        flushSpeakerContent(currentSpeaker, currentContent, messages, tab);
        currentSpeaker = h3Match[1];
        currentContent = [];
        const remaining = trimmed.replace(/^###\s+.+\n?/, "").trim();
        if (remaining) currentContent.push(remaining);
      } else if (currentSpeaker) {
        currentContent.push(trimmed);
      }
      return;
    }
    if (currentSpeaker) currentContent.push(trimmed);
  });

  flushSpeakerContent(currentSpeaker, currentContent, messages, tab);
  return messages;
}
