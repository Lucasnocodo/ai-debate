import {
  FIELD_LIMITS,
  MAX_AI_PARTICIPANTS,
  MAX_HUMAN_TIME_LIMIT,
  MIN_AI_PARTICIPANTS,
  MIN_HUMAN_TIME_LIMIT,
} from "./constants";

function trimmed(value) {
  return String(value ?? "").trim();
}

function isBlank(value) {
  return trimmed(value) === "";
}

function exceeds(value, maxLength) {
  return String(value ?? "").length > maxLength;
}

export function validateConfigField(field, config) {
  switch (field) {
    case "genOutline":
      if (config.selectedCategory === "自訂" && isBlank(config.genOutline)) {
        return "使用「自訂」時，請輸入主題或大綱內容。";
      }
      if (exceeds(config.genOutline, FIELD_LIMITS.genOutline)) {
        return `大綱補充不可超過 ${FIELD_LIMITS.genOutline} 字。`;
      }
      return "";
    case "genCount": {
      const parsed = Number.parseInt(config.genCount, 10);
      if (
        String(config.genCount).trim() === ""
        || Number.isNaN(parsed)
        || parsed < MIN_AI_PARTICIPANTS
        || parsed > MAX_AI_PARTICIPANTS
      ) {
        return `AI 辯論者人數需介於 ${MIN_AI_PARTICIPANTS} 到 ${MAX_AI_PARTICIPANTS} 之間。`;
      }
      return "";
    }
    case "topic":
      if (config.showTopicField && isBlank(config.topic)) {
        return "請先生成或輸入辯論主題。";
      }
      if (exceeds(config.topic, FIELD_LIMITS.topic)) {
        return `辯論主題不可超過 ${FIELD_LIMITS.topic} 字。`;
      }
      return "";
    case "rounds": {
      const rounds = Number.parseInt(config.rounds, 10);
      if (String(config.rounds).trim() === "" || Number.isNaN(rounds) || rounds < 1 || rounds > 6) {
        return "輪數需介於 1 到 6 之間。";
      }
      return "";
    }
    case "humanTimeLimit": {
      if (!config.humanEnabled) return "";
      const value = Number.parseInt(config.humanTimeLimit, 10);
      if (
        String(config.humanTimeLimit).trim() === ""
        || Number.isNaN(value)
        || value < MIN_HUMAN_TIME_LIMIT
        || value > MAX_HUMAN_TIME_LIMIT
      ) {
        return `人類回覆時限需介於 ${MIN_HUMAN_TIME_LIMIT} 到 ${MAX_HUMAN_TIME_LIMIT} 秒。`;
      }
      return "";
    }
    case "modName":
      if (config.modEnabled && isBlank(config.modName)) {
        return "啟用主持人時，請輸入主持人名稱。";
      }
      if (exceeds(config.modName, FIELD_LIMITS.modName)) {
        return `主持人名稱不可超過 ${FIELD_LIMITS.modName} 字。`;
      }
      return "";
    case "modSystem":
      if (exceeds(config.modSystem, FIELD_LIMITS.modSystem)) {
        return `主持人附加指示不可超過 ${FIELD_LIMITS.modSystem} 字。`;
      }
      return "";
    default:
      return "";
  }
}

export function validateParticipantField(participant, field) {
  if (field === "name") {
    if (isBlank(participant.name)) {
      return "請輸入參與者名稱。";
    }
    if (exceeds(participant.name, FIELD_LIMITS.participantName)) {
      return `參與者名稱不可超過 ${FIELD_LIMITS.participantName} 字。`;
    }
    return "";
  }
  if (field === "system") {
    if (participant.human) return "";
    if (isBlank(participant.system)) {
      return "請輸入角色設定。";
    }
    if (exceeds(participant.system, FIELD_LIMITS.participantSystem)) {
      return `角色設定不可超過 ${FIELD_LIMITS.participantSystem} 字。`;
    }
    return "";
  }
  return "";
}

export function validateParticipants(participants) {
  const aiParticipants = participants.filter((participant) => !participant.human);
  const errors = {};
  if (aiParticipants.length < MIN_AI_PARTICIPANTS) {
    return { participantsSection: `至少需要 ${MIN_AI_PARTICIPANTS} 位 AI 辯論者。`, participants: {} };
  }
  if (aiParticipants.length > MAX_AI_PARTICIPANTS) {
    return { participantsSection: `AI 辯論者最多 ${MAX_AI_PARTICIPANTS} 位。`, participants: {} };
  }
  participants.forEach((participant) => {
    const entry = {};
    const nameError = validateParticipantField(participant, "name");
    const systemError = validateParticipantField(participant, "system");
    if (nameError) entry.name = nameError;
    if (systemError) entry.system = systemError;
    if (Object.keys(entry).length) errors[participant.id] = entry;
  });
  if (Object.keys(errors).length) {
    return { participantsSection: "請先修正參與者欄位錯誤。", participants: errors };
  }
  return { participantsSection: "", participants: {} };
}
