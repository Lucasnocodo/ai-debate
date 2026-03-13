import { useEffect, useMemo, useRef, useState } from "react";
import CountdownModal from "./components/CountdownModal";
import DebateWorkspace from "./components/DebateWorkspace";
import LavaBackground from "./components/LavaBackground";
import LogModal from "./components/LogModal";
import SettingsPage from "./components/SettingsPage";
import {
  generateConfigStream,
  getLog,
  getLogs,
  getModels,
  getState,
  sendHumanInput,
  startDebate as startDebateRequest,
  stopDebate as stopDebateRequest,
} from "./lib/api";
import {
  DEFAULT_DEBATE_STYLE,
  DEFAULT_PARTICIPANTS,
  FIXED_MOD_MODEL,
  FIELD_LIMITS,
  FIXED_MODEL,
  HUMAN_PARTICIPANT_PRESET,
  MAX_AI_PARTICIPANTS,
  MAX_HUMAN_TIME_LIMIT,
  MIN_AI_PARTICIPANTS,
  MIN_HUMAN_TIME_LIMIT,
  SETTINGS_STORAGE_KEY,
} from "./lib/constants";
import { parseLogToMessages } from "./lib/logParser";
import { validateConfigField, validateParticipantField, validateParticipants } from "./lib/validation";
import {
  buildParticipant,
  clampNumber,
  defaultTab,
  formatLogTimestamp,
  guessSpeakerClass,
} from "./lib/utils";

function createDefaultConfig() {
  return {
    selectedCategory: "自訂",
    genOutline: "",
    debateStyle: DEFAULT_DEBATE_STYLE,
    genCount: "3",
    topic: "",
    rounds: "3",
    humanEnabled: false,
    humanTimeLimit: "120",
    modEnabled: true,
    modName: "主持人",
    modModel: FIXED_MOD_MODEL,
    modSystem: "",
    videoEnabled: false,
    participants: DEFAULT_PARTICIPANTS.map((participant) => buildParticipant(participant)),
    showTopicField: false,
    showParticipantsSection: false,
  };
}

function normalizeConfig(saved) {
  const base = createDefaultConfig();
  if (!saved) return base;

  const savedParticipants = Array.isArray(saved.participants) ? saved.participants : [];
  const aiParticipants = savedParticipants
    .filter((participant) => !String(participant.model || "").toLowerCase().startsWith("human"))
    .slice(0, MAX_AI_PARTICIPANTS)
    .map((participant) => buildParticipant(participant));

  return {
    ...base,
    selectedCategory: saved.selectedCategory || base.selectedCategory,
    genOutline: saved.genOutline || "",
    debateStyle: saved.debateStyle || base.debateStyle,
    genCount: String(
      clampNumber(
        aiParticipants.length || Number.parseInt(saved.genCount, 10) || 3,
        MIN_AI_PARTICIPANTS,
        MAX_AI_PARTICIPANTS,
        3,
      ),
    ),
    topic: saved.topic || "",
    rounds: String(clampNumber(saved.rounds || 3, 1, 6, 3)),
    humanEnabled: Boolean(saved.humanEnabled),
    humanTimeLimit: String(clampNumber(saved.humanTimeLimit || 120, MIN_HUMAN_TIME_LIMIT, MAX_HUMAN_TIME_LIMIT, 120)),
    modEnabled: saved.modEnabled !== undefined ? Boolean(saved.modEnabled) : true,
    modName: saved.modName || "主持人",
    modModel: saved.modModel || FIXED_MOD_MODEL,
    modSystem: saved.modSystem || "",
    videoEnabled: Boolean(saved.videoEnabled),
    participants: aiParticipants.length ? aiParticipants : base.participants,
    showTopicField: Boolean(saved.topic?.trim()),
    showParticipantsSection: Boolean(saved.topic?.trim()) || Boolean(saved.showParticipantsSection),
  };
}

function readSavedConfig() {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY) || sessionStorage.getItem(SETTINGS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function withHumanParticipant(config) {
  const existingHuman = config.participants.find((participant) => participant.human);
  const aiParticipants = config.participants.filter((participant) => !participant.human);
  const participants = config.humanEnabled
    ? [...aiParticipants, existingHuman || buildParticipant(HUMAN_PARTICIPANT_PRESET)]
    : aiParticipants;
  return { ...config, participants };
}

function removeLoaderState(tab) {
  if (!tab.loaderVisible) return tab;
  return {
    ...tab,
    loaderVisible: false,
  };
}

function isConfigPristine(config) {
  const base = createDefaultConfig();
  const compareParticipants = (participants) =>
    participants
      .filter((participant) => !participant.human)
      .map(({ name, model, system }) => ({ name, model, system }));

  return JSON.stringify({
    selectedCategory: config.selectedCategory,
    genOutline: config.genOutline,
    debateStyle: config.debateStyle,
    genCount: config.genCount,
    topic: config.topic,
    rounds: config.rounds,
    humanEnabled: config.humanEnabled,
    humanTimeLimit: config.humanTimeLimit,
    modEnabled: config.modEnabled,
    modName: config.modName,
    modModel: config.modModel,
    modSystem: config.modSystem,
    videoEnabled: config.videoEnabled,
    showTopicField: config.showTopicField,
    showParticipantsSection: config.showParticipantsSection,
    participants: compareParticipants(config.participants),
  }) === JSON.stringify({
    selectedCategory: base.selectedCategory,
    genOutline: base.genOutline,
    debateStyle: base.debateStyle,
    genCount: base.genCount,
    topic: base.topic,
    rounds: base.rounds,
    humanEnabled: base.humanEnabled,
    humanTimeLimit: base.humanTimeLimit,
    modEnabled: base.modEnabled,
    modName: base.modName,
    modModel: base.modModel,
    modSystem: base.modSystem,
    videoEnabled: base.videoEnabled,
    showTopicField: base.showTopicField,
    showParticipantsSection: base.showParticipantsSection,
    participants: compareParticipants(base.participants),
  });
}

const GENERATE_PROGRESS_STEPS = [
  { key: "analyze", label: "分析主題方向" },
  { key: "topic", label: "生成辯論題目" },
  { key: "roles", label: "設計角色立場" },
  { key: "finalize", label: "整理最終設定" },
];
const GENERATE_PROGRESS_STAGES = ["ANALYZING", "SHAPING", "CASTING", "FINALIZING"];

function buildGenerateSteps(activeIndex = 0) {
  return GENERATE_PROGRESS_STEPS.map((step, index) => ({
    ...step,
    status: index < activeIndex ? "done" : index === activeIndex ? "active" : "",
  }));
}

function createGenerateStatus(overrides = {}) {
  return {
    loading: false,
    error: false,
    message: "",
    stage: "ANALYZING",
    progress: 0,
    steps: buildGenerateSteps(0),
    ...overrides,
  };
}

const MAX_OPEN_DEBATES = 3;

export default function App() {
  const [config, setConfig] = useState(() => withHumanParticipant(normalizeConfig(readSavedConfig())));
  const [errors, setErrors] = useState({});
  const [generateStatus, setGenerateStatus] = useState(() => createGenerateStatus());
  const [settingsTab, setSettingsTab] = useState("config");
  const [showSettings, setShowSettings] = useState(true);
  const [settingsOverlay, setSettingsOverlay] = useState(false);
  const [tabs, setTabs] = useState([]);
  const [activeTabId, setActiveTabId] = useState(null);
  const [availableModels, setAvailableModels] = useState([FIXED_MODEL]);
  const [logsView, setLogsView] = useState({ loading: false, error: "", items: [] });
  const [logModal, setLogModal] = useState({ open: false, title: "", filename: "", loading: false, error: "", items: [] });
  const [countdownModal, setCountdownModal] = useState({ open: false, tabId: null });

  const eventSourcesRef = useRef({});
  const loaderTimersRef = useRef({});
  const countdownTimersRef = useRef({});
  const generateRequestRef = useRef({ controller: null, requestId: 0 });

  const hasTabs = tabs.length > 0;
  const canOpenMoreDebates = tabs.length < MAX_OPEN_DEBATES;
  const hasSavedDraft = !isConfigPristine(config);

  useEffect(() => {
    getModels()
      .then((data) => {
        setAvailableModels(data.models?.length ? data.models : [FIXED_MODEL]);
      })
      .catch(() => {
        setAvailableModels([FIXED_MODEL]);
      });
  }, []);

  useEffect(() => {
    const toSave = {
      selectedCategory: config.selectedCategory,
      genOutline: config.genOutline,
      debateStyle: config.debateStyle,
      topic: config.topic,
      rounds: config.rounds,
      humanEnabled: config.humanEnabled,
      modEnabled: config.modEnabled,
      modName: config.modName,
      modModel: config.modModel,
      modSystem: config.modSystem,
      videoEnabled: config.videoEnabled,
      humanTimeLimit: config.humanTimeLimit,
      participants: config.participants.map(({ name, model, system, human, locked }) => ({
        name,
        model,
        system,
        human,
        locked,
      })),
      showParticipantsSection: config.showParticipantsSection,
    };
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(toSave));
  }, [config]);

  useEffect(() => {
    if (settingsTab !== "logs") return undefined;
    setLogsView({ loading: true, error: "", items: [] });
    getLogs()
      .then((data) => {
        const items = (data.logs || []).map((log) => ({
          ...log,
          displayName: formatLogTimestamp(log.name),
          sizeLabel: `${(log.size / 1024).toFixed(1)} KB`,
        }));
        setLogsView({ loading: false, error: "", items });
      })
      .catch(() => {
        setLogsView({ loading: false, error: "載入失敗", items: [] });
      });
    return undefined;
  }, [settingsTab]);

  useEffect(() => () => {
    Object.values(eventSourcesRef.current).forEach((source) => source?.close());
    Object.values(loaderTimersRef.current).forEach((timers) => timers?.forEach((timer) => clearTimeout(timer)));
    Object.values(countdownTimersRef.current).forEach((timer) => clearInterval(timer));
    generateRequestRef.current.controller?.abort();
  }, []);

  useEffect(() => {
    if (canOpenMoreDebates) {
      clearError("sessionLimit");
    }
  }, [canOpenMoreDebates]);

  const activeTab = useMemo(
    () => tabs.find((tab) => tab.id === activeTabId) || null,
    [tabs, activeTabId],
  );

  function updateConfig(updater) {
    setConfig((current) => {
      const next = typeof updater === "function" ? updater(current) : { ...current, ...updater };
      return withHumanParticipant(next);
    });
  }

  function updateTab(tabId, updater) {
    setTabs((current) =>
      current.map((tab) => (tab.id === tabId ? (typeof updater === "function" ? updater(tab) : { ...tab, ...updater }) : tab)),
    );
  }

  function clearError(key) {
    setErrors((current) => {
      if (!(key in current)) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  function clearParticipantError(participantId, field) {
    setErrors((current) => {
      const participantErrors = current.participants || {};
      if (!participantErrors[participantId]?.[field]) return current;
      const nextParticipants = { ...participantErrors };
      const nextEntry = { ...nextParticipants[participantId] };
      delete nextEntry[field];
      if (Object.keys(nextEntry).length === 0) delete nextParticipants[participantId];
      else nextParticipants[participantId] = nextEntry;
      return { ...current, participants: nextParticipants };
    });
  }

  function updateConfigField(field, value) {
    updateConfig((current) => ({ ...current, [field]: value }));
    if (field === "genOutline") clearError("genOutline");
    if (field === "genCount") clearError("genCount");
    if (field === "topic") clearError("topic");
    if (field === "rounds") clearError("rounds");
    if (field === "humanTimeLimit") clearError("humanTimeLimit");
    if (field === "modName") clearError("modName");
    if (field === "modSystem") clearError("modSystem");
  }

  function setFieldError(key, message) {
    setErrors((current) => {
      if (!message) {
        if (!(key in current)) return current;
        const next = { ...current };
        delete next[key];
        return next;
      }
      return { ...current, [key]: message };
    });
  }

  function setParticipantFieldError(participantId, field, message) {
    setErrors((current) => {
      const nextParticipants = { ...(current.participants || {}) };
      const nextEntry = { ...(nextParticipants[participantId] || {}) };
      if (message) {
        nextEntry[field] = message;
        nextParticipants[participantId] = nextEntry;
      } else {
        delete nextEntry[field];
        if (Object.keys(nextEntry).length === 0) delete nextParticipants[participantId];
        else nextParticipants[participantId] = nextEntry;
      }
      return { ...current, participants: nextParticipants };
    });
  }

  function handleConfigBlur(field, value) {
    const nextConfig = { ...config, [field]: value };
    const message = validateConfigField(field, nextConfig);
    setFieldError(field, message);
  }

  function handleCategorySelect(category) {
    updateConfigField("selectedCategory", category);
  }

  function handleToggleHuman() {
    updateConfig((current) => ({
      ...current,
      humanEnabled: !current.humanEnabled,
      showParticipantsSection: current.showParticipantsSection || !current.humanEnabled,
    }));
    clearError("humanTimeLimit");
  }

  function handleToggleModerator() {
    updateConfig((current) => ({ ...current, modEnabled: !current.modEnabled }));
    clearError("modName");
  }

  function handleParticipantChange(participantId, patch) {
    updateConfig((current) => ({
      ...current,
      participants: current.participants.map((participant) =>
        participant.id === participantId ? { ...participant, ...patch } : participant,
      ),
    }));
    Object.keys(patch).forEach((field) => clearParticipantError(participantId, field));
    clearError("participantsSection");
  }

  function handleParticipantBlur(participantId, field, value) {
    const participant = config.participants.find((item) => item.id === participantId);
    if (!participant) return;
    const nextParticipant = { ...participant, [field]: value };
    const message = validateParticipantField(nextParticipant, field);
    setParticipantFieldError(participantId, field, message);
  }

  function handleParticipantRemove(participantId) {
    updateConfig((current) => ({
      ...current,
      participants: current.participants.filter((participant) => participant.id !== participantId),
    }));
    clearError("participantsSection");
  }

  function handleAddParticipant() {
    const aiParticipants = config.participants.filter((participant) => !participant.human);
    if (aiParticipants.length >= MAX_AI_PARTICIPANTS) {
      setErrors((current) => ({ ...current, participantsSection: `AI 辯論者最多 ${MAX_AI_PARTICIPANTS} 位。` }));
      return;
    }
    updateConfig((current) => ({
      ...current,
      showParticipantsSection: true,
      participants: [...current.participants, buildParticipant({ model: FIXED_MODEL })],
    }));
    clearError("participantsSection");
  }

  function setLoaderProgress(tabId, stepIndex) {
    const labels = ["CONNECTING", "PREPARING", "COMPOSING", "LAUNCHING"];
    const keys = ["connect", "prepare", "moderator", "start"];
    updateTab(tabId, (tab) => ({
      ...tab,
      loaderStage: labels[stepIndex],
      loaderProgress: ((stepIndex + 1) / keys.length) * 90,
      loaderSteps: keys.reduce((acc, key, index) => {
        acc[key] = index < stepIndex ? "done" : index === stepIndex ? "active" : "";
        return acc;
      }, {}),
    }));
  }

  function startLoader(tabId) {
    const delays = [2000, 6000, 14000, 25000];
    const timers = delays.map((delay, index) => setTimeout(() => setLoaderProgress(tabId, index), delay));
    loaderTimersRef.current[tabId] = timers;
  }

  function stopLoader(tabId) {
    const timers = loaderTimersRef.current[tabId];
    if (timers) timers.forEach((timer) => clearTimeout(timer));
    delete loaderTimersRef.current[tabId];
    updateTab(tabId, (tab) => removeLoaderState(tab));
  }

  function setGenerateStageStatus({ stage, stepIndex, progress, message, error = false, loading = true }) {
    setGenerateStatus(
      createGenerateStatus({
        loading,
        error,
        stage: stage || GENERATE_PROGRESS_STAGES[Math.max(stepIndex || 0, 0)] || "ANALYZING",
        progress: progress ?? 0,
        message: message || "",
        steps: buildGenerateSteps(stepIndex ?? 0),
      }),
    );
  }

  function cancelGenerateRequest() {
    generateRequestRef.current.controller?.abort();
    generateRequestRef.current = {
      controller: null,
      requestId: generateRequestRef.current.requestId + 1,
    };
  }

  function stopCountdown(tabId) {
    const timer = countdownTimersRef.current[tabId];
    if (timer) clearInterval(timer);
    delete countdownTimersRef.current[tabId];
  }

  function skipHumanTurn(tabId) {
    const target = tabs.find((tab) => tab.id === tabId);
    if (!target) return;
    sendHumanInput(target.sessionId, "（跳過此輪）").catch(() => {});
    stopCountdown(tabId);
    setCountdownModal({ open: false, tabId: null });
    updateTab(tabId, {
      humanInputVisible: false,
      waitingSpeaker: "",
      humanInputText: "",
      countdownSeconds: 0,
      countdownSkipped: true,
    });
  }

  function startCountdown(tabId) {
    stopCountdown(tabId);
    const limit = clampNumber(config.humanTimeLimit, MIN_HUMAN_TIME_LIMIT, MAX_HUMAN_TIME_LIMIT, 120);
    updateTab(tabId, {
      countdownSeconds: limit,
      countdownWarningShown: false,
      countdownSkipped: false,
    });
    countdownTimersRef.current[tabId] = setInterval(() => {
      let shouldOpenWarning = false;
      let shouldSkip = false;
      setTabs((current) =>
        current.map((tab) => {
          if (tab.id !== tabId) return tab;
          const nextSeconds = Math.max(0, tab.countdownSeconds - 1);
          shouldOpenWarning = nextSeconds <= 30 && !tab.countdownWarningShown;
          shouldSkip = nextSeconds <= 0 && !tab.countdownSkipped;
          return {
            ...tab,
            countdownSeconds: nextSeconds,
            countdownWarningShown: tab.countdownWarningShown || shouldOpenWarning,
            countdownSkipped: tab.countdownSkipped || shouldSkip,
          };
        }),
      );
      if (shouldOpenWarning) {
        setCountdownModal({ open: true, tabId });
      }
      if (shouldSkip) {
        skipHumanTurn(tabId);
      }
    }, 1000);
  }

  function connectSSE(tab) {
    const source = new EventSource(`/api/events?session_id=${encodeURIComponent(tab.sessionId)}`);
    eventSourcesRef.current[tab.id] = source;

    const removeThinking = (items) => items.filter((item) => item.type !== "thinking");

    source.addEventListener("message", (event) => {
      const data = JSON.parse(event.data || "{}");
      stopLoader(tab.id);
      updateTab(tab.id, (current) => ({
        ...current,
        messages: [
          ...removeThinking(current.messages),
          {
            id: crypto.randomUUID(),
            type: "message",
            speaker: data.speaker,
            content: data.content,
            className: guessSpeakerClass(data.speaker, current),
          },
        ],
      }));
    });

    source.addEventListener("speaking", (event) => {
      const data = JSON.parse(event.data || "{}");
      stopLoader(tab.id);
      const labels = {
        final_summary: "產出最終結論...",
        summarizing: "產出階段總結...",
        moderating: "主持中...",
        generating_video: "生成場景影片...",
      };
      updateTab(tab.id, (current) => ({
        ...current,
        messages: [
          ...removeThinking(current.messages),
          {
            id: crypto.randomUUID(),
            type: "thinking",
            content: `${data.speaker} ${labels[data.status] || "思考中..."}`,
          },
        ],
      }));
    });

    source.addEventListener("round", (event) => {
      const data = JSON.parse(event.data || "{}");
      stopLoader(tab.id);
      updateTab(tab.id, (current) => ({
        ...current,
        roundInfo: data.label || `第 ${data.round}/${data.total} 輪`,
        messages: [...current.messages, { id: crypto.randomUUID(), type: "round", label: data.label || `第 ${data.round} 輪` }],
      }));
    });

    source.addEventListener("video", (event) => {
      const data = JSON.parse(event.data || "{}");
      updateTab(tab.id, (current) => ({
        ...current,
        messages: [
          ...removeThinking(current.messages),
          {
            id: crypto.randomUUID(),
            type: "video",
            speaker: data.speaker ? `${data.speaker} - 第 ${data.round} 輪` : `第 ${data.round} 輪場景`,
            url: data.url,
          },
        ],
      }));
    });

    source.addEventListener("video_merged", (event) => {
      const data = JSON.parse(event.data || "{}");
      updateTab(tab.id, (current) => ({
        ...current,
        messages: [
          ...current.messages,
          {
            id: crypto.randomUUID(),
            type: "mergedVideo",
            speaker: `完整辯論影片（${data.count} 段合併）`,
            url: `/api/video/${data.filename}`,
          },
        ],
      }));
    });

    source.addEventListener("waiting_human", (event) => {
      const data = JSON.parse(event.data || "{}");
      updateTab(tab.id, {
        humanInputVisible: true,
        waitingSpeaker: data.speaker,
        humanInputText: "",
      });
      startCountdown(tab.id);
    });

    source.addEventListener("status", (event) => {
      const data = JSON.parse(event.data || "{}");
      stopLoader(tab.id);
      updateTab(tab.id, { statusText: data.message });
    });

    source.addEventListener("done", () => {
      stopLoader(tab.id);
      stopCountdown(tab.id);
      updateTab(tab.id, {
        running: false,
        statusText: "已結束",
        roundInfo: "已完成",
        humanInputVisible: false,
        waitingSpeaker: "",
        humanInputText: "",
      });
      source.close();
      delete eventSourcesRef.current[tab.id];
    });

    source.onerror = () => {
      setTimeout(() => {
        source.close();
        delete eventSourcesRef.current[tab.id];
        getState(tab.sessionId)
          .then((data) => {
            if (data.running) connectSSE({ ...tab, id: tab.id });
            else updateTab(tab.id, { running: false, statusText: "已結束" });
          })
          .catch(() => {
            connectSSE({ ...tab, id: tab.id });
          });
      }, 3000);
    };
  }

  async function handleGenerateConfig() {
    const nextErrors = {};
    const parsedCount = Number.parseInt(config.genCount, 10);
    const outlineError = validateConfigField("genOutline", config);
    const countError = validateConfigField("genCount", config);
    if (outlineError) nextErrors.genOutline = outlineError;
    if (countError) nextErrors.genCount = countError;
    if (Object.keys(nextErrors).length) {
      setErrors((current) => ({ ...current, ...nextErrors }));
      return;
    }

    cancelGenerateRequest();
    const controller = new AbortController();
    const requestId = generateRequestRef.current.requestId + 1;
    generateRequestRef.current = { controller, requestId };

    setGenerateStageStatus({
      stage: "ANALYZING",
      stepIndex: 0,
      progress: 4,
      message: "已送出請求，準備開始生成設定...",
    });
    try {
      const streamedParticipants = [];
      const data = await generateConfigStream({
        category: config.selectedCategory,
        outline: config.genOutline.trim(),
        style: config.debateStyle,
        count: parsedCount,
        model: FIXED_MOD_MODEL,
      }, (event) => {
        if (generateRequestRef.current.requestId !== requestId) return;
        if (event.type === "stage") {
          setGenerateStageStatus(event);
          return;
        }
        if (event.type === "topic") {
          setGenerateStageStatus(event);
          updateConfig((current) => ({
            ...current,
            topic: event.topic || current.topic,
            showTopicField: true,
          }));
          return;
        }
        if (event.type === "participant" && event.participant) {
          const nextParticipant = buildParticipant({
            name: event.participant.name,
            model: FIXED_MODEL,
            system: event.participant.system,
          });
          streamedParticipants.push(nextParticipant);
          setGenerateStageStatus(event);
          updateConfig((current) => {
            const existingHuman = current.participants.find((participant) => participant.human);
            return {
              ...current,
              showParticipantsSection: true,
              participants: [
                ...streamedParticipants,
                ...(current.humanEnabled ? [existingHuman || buildParticipant(HUMAN_PARTICIPANT_PRESET)] : []),
              ],
            };
          });
          return;
        }
        if (event.type === "moderator" && event.moderator) {
          setGenerateStageStatus(event);
          updateConfig((current) => ({
            ...current,
            modEnabled: current.modEnabled,
            modName: event.moderator.name || current.modName,
            modModel: FIXED_MOD_MODEL,
            modSystem: event.moderator.system || current.modSystem,
          }));
          return;
        }
        if (event.type === "done") {
          setGenerateStageStatus(event);
        }
      }, { signal: controller.signal });
      if (generateRequestRef.current.requestId !== requestId) return;
      updateConfig((current) => ({
        ...current,
        topic: data.topic || current.topic,
        modEnabled: current.modEnabled,
        modName: data.moderator?.name || current.modName,
        modModel: FIXED_MOD_MODEL,
        modSystem: data.moderator?.system || current.modSystem,
        showTopicField: true,
        showParticipantsSection: true,
        participants: [
          ...(data.participants || []).map((participant) => buildParticipant({
            name: participant.name,
            model: FIXED_MODEL,
            system: participant.system,
          })),
          ...(current.humanEnabled ? [buildParticipant(HUMAN_PARTICIPANT_PRESET)] : []),
        ],
      }));
      generateRequestRef.current.controller = null;
      setGenerateStatus(createGenerateStatus());
    } catch (error) {
      if (error.name === "AbortError") {
        if (generateRequestRef.current.requestId === requestId) {
          generateRequestRef.current.controller = null;
        }
        return;
      }
      if (generateRequestRef.current.requestId !== requestId) return;
      generateRequestRef.current.controller = null;
      setGenerateStatus(createGenerateStatus({
        loading: false,
        error: true,
        message: `生成失敗：${error.message}`,
      }));
    }
  }

  async function handleStartDebate() {
    if (!canOpenMoreDebates) {
      setErrors((current) => ({
        ...current,
        sessionLimit: `同時開啟的辯論最多 ${MAX_OPEN_DEBATES} 場，請先關閉其中一場。`,
      }));
      setShowSettings(true);
      setSettingsOverlay(false);
      return;
    }
    const nextErrors = {};
    const topicError = validateConfigField("topic", { ...config, showTopicField: true });
    if (topicError) nextErrors.topic = topicError;
    const rounds = Number.parseInt(config.rounds, 10);
    const roundsError = validateConfigField("rounds", config);
    if (roundsError) nextErrors.rounds = roundsError;
    const modNameError = validateConfigField("modName", config);
    if (modNameError) nextErrors.modName = modNameError;
    const modSystemError = validateConfigField("modSystem", config);
    if (modSystemError) nextErrors.modSystem = modSystemError;
    const humanTimeLimit = Number.parseInt(config.humanTimeLimit, 10);
    const humanLimitError = validateConfigField("humanTimeLimit", config);
    if (humanLimitError) nextErrors.humanTimeLimit = humanLimitError;
    const participantValidation = validateParticipants(config.participants);
    if (participantValidation.participantsSection) nextErrors.participantsSection = participantValidation.participantsSection;
    if (Object.keys(participantValidation.participants || {}).length) nextErrors.participants = participantValidation.participants;
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;

    try {
      const payload = {
        topic: config.topic,
        rounds,
        max_tokens: 650,
        participants: config.participants.map((participant) => ({
          name: participant.name,
          model: participant.model,
          system: participant.system,
        })),
        generate_video: config.videoEnabled,
      };
      if (config.humanEnabled) payload.human_time_limit = humanTimeLimit;
      if (config.modEnabled) {
        payload.moderator = {
          enabled: true,
          name: config.modName,
          model: config.modModel || FIXED_MOD_MODEL,
          system: config.modSystem,
        };
      }
      const data = await startDebateRequest(payload);
      const tab = defaultTab(data.session_id, config.modName, config.participants.map((participant) => ({
        name: participant.name,
        model: participant.model,
      })));
      setTabs((current) => [...current, tab]);
      setActiveTabId(tab.id);
      setShowSettings(false);
      setSettingsOverlay(false);
      startLoader(tab.id);
      connectSSE(tab);
    } catch (error) {
      window.alert(`啟動辯論失敗：${error.message}`);
    }
  }

  function handleSwitchSettingsTab(tab) {
    setSettingsTab(tab);
  }

  function handleOpenSettings() {
    setShowSettings(true);
    setSettingsOverlay(false);
  }

  function handleBackToDebates() {
    setShowSettings(false);
    setSettingsOverlay(false);
  }

  function handleNewDebate() {
    if (!canOpenMoreDebates) {
      setErrors((current) => ({
        ...current,
        sessionLimit: `同時開啟的辯論最多 ${MAX_OPEN_DEBATES} 場，請先關閉其中一場。`,
      }));
      setShowSettings(true);
      setSettingsOverlay(false);
      return;
    }
    setShowSettings(true);
    setSettingsOverlay(false);
  }

  function handleResetSettings() {
    cancelGenerateRequest();
    localStorage.removeItem(SETTINGS_STORAGE_KEY);
    sessionStorage.removeItem(SETTINGS_STORAGE_KEY);
    setConfig(withHumanParticipant(createDefaultConfig()));
    setErrors({});
    setGenerateStatus(createGenerateStatus());
    setSettingsTab("config");
  }

  async function handleStopDebate(tabId) {
    const tab = tabs.find((item) => item.id === tabId);
    if (!tab) return;
    try {
      await stopDebateRequest(tab.sessionId);
      updateTab(tabId, { statusText: "停止中..." });
    } catch {
      // ignore
    }
  }

  async function handleCloseTab(tabId) {
    const tab = tabs.find((item) => item.id === tabId);
    if (!tab) return;
    if (tab.running) {
      const confirmed = window.confirm("辯論進行中，確定要關閉？辯論將會停止。");
      if (!confirmed) return;
      if (tab.sessionId) {
        stopDebateRequest(tab.sessionId).catch(() => {});
      }
    }
    eventSourcesRef.current[tabId]?.close();
    delete eventSourcesRef.current[tabId];
    stopCountdown(tabId);
    setTabs((current) => {
      const remaining = current.filter((item) => item.id !== tabId);
      setActiveTabId((currentActive) => {
        if (currentActive !== tabId) return currentActive;
        return remaining.length ? remaining[remaining.length - 1].id : null;
      });
      if (remaining.length === 0) {
        setShowSettings(true);
        setSettingsOverlay(false);
      }
      return remaining;
    });
  }

  function handleHumanInputChange(tabId, value) {
    updateTab(tabId, { humanInputText: value });
  }

  async function handleSubmitHumanInput(tabId) {
    const tab = tabs.find((item) => item.id === tabId);
    if (!tab || !tab.humanInputText.trim()) return;
    try {
      await sendHumanInput(tab.sessionId, tab.humanInputText.trim());
      stopCountdown(tabId);
      setCountdownModal({ open: false, tabId: null });
      updateTab(tabId, {
        humanInputVisible: false,
        waitingSpeaker: "",
        humanInputText: "",
        countdownSeconds: 0,
      });
    } catch (error) {
      window.alert(`送出失敗：${error.message}`);
    }
  }

  async function handleOpenLog(filename) {
    setLogModal({
      open: true,
      title: filename.replace(".md", ""),
      filename,
      loading: true,
      error: "",
      items: [],
    });
    try {
      const markdown = await getLog(filename);
      setLogModal({
        open: true,
        title: filename.replace(".md", ""),
        filename,
        loading: false,
        error: "",
        items: parseLogToMessages(markdown, activeTab),
      });
    } catch {
      setLogModal({
        open: true,
        title: filename.replace(".md", ""),
        filename,
        loading: false,
        error: "載入失敗",
        items: [],
      });
    }
  }

  function handleDownloadCurrentLog() {
    if (!logModal.filename) return;
    const anchor = document.createElement("a");
    anchor.href = `/api/logs/${encodeURIComponent(logModal.filename)}`;
    anchor.download = logModal.filename;
    anchor.click();
  }

  return (
    <>
      <LavaBackground />
      <SettingsPage
        settingsTab={settingsTab}
        onSwitchTab={handleSwitchSettingsTab}
        showSettings={showSettings}
        overlay={settingsOverlay}
        showBackButton={hasTabs}
        onBackToDebates={handleBackToDebates}
        onCloseOverlay={() => setSettingsOverlay(false)}
        config={config}
        errors={errors}
        generateStatus={generateStatus}
        logsView={logsView}
        onCategorySelect={handleCategorySelect}
        onConfigChange={updateConfigField}
        onConfigBlur={handleConfigBlur}
        onGenerateConfig={handleGenerateConfig}
        onToggleHuman={handleToggleHuman}
        onToggleModerator={handleToggleModerator}
        onAddParticipant={handleAddParticipant}
        onParticipantChange={handleParticipantChange}
        onParticipantBlur={handleParticipantBlur}
        onParticipantRemove={handleParticipantRemove}
        onStartDebate={handleStartDebate}
        onOpenLog={handleOpenLog}
        limits={FIELD_LIMITS}
        canOpenMoreDebates={canOpenMoreDebates}
        maxOpenDebates={MAX_OPEN_DEBATES}
        hasSavedDraft={hasSavedDraft}
        onResetSettings={handleResetSettings}
      />

      <DebateWorkspace
        tabs={tabs}
        activeTabId={activeTabId}
        visible={!showSettings}
        humanInputMaxLength={FIELD_LIMITS.humanInput}
        onNewDebate={handleNewDebate}
        onSwitchTab={setActiveTabId}
        onCloseTab={handleCloseTab}
        onStopDebate={handleStopDebate}
        onOpenSettings={handleOpenSettings}
        onSubmitHumanInput={handleSubmitHumanInput}
        onSkipHumanTurn={skipHumanTurn}
        onHumanInputChange={handleHumanInputChange}
        onHumanInputKeyDown={(event, tabId) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            handleSubmitHumanInput(tabId);
          }
        }}
        canOpenMoreDebates={canOpenMoreDebates}
        maxOpenDebates={MAX_OPEN_DEBATES}
      />

      <CountdownModal
        open={countdownModal.open}
        onContinue={() => setCountdownModal({ open: false, tabId: null })}
        onSkip={() => {
          if (countdownModal.tabId) skipHumanTurn(countdownModal.tabId);
        }}
      />

      <LogModal
        open={logModal.open}
        title={logModal.title}
        items={logModal.items}
        loading={logModal.loading}
        error={logModal.error}
        onClose={() => setLogModal({ open: false, title: "", filename: "", loading: false, error: "", items: [] })}
        onDownload={handleDownloadCurrentLog}
      />
    </>
  );
}
