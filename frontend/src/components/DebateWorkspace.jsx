import { useEffect, useRef } from "react";
import ProgressLoader from "./ProgressLoader";
import { renderMarkdown } from "../lib/utils";

function Loader({ tab }) {
  const steps = [
    { key: "connect", label: "連接辯論伺服器", status: tab.loaderSteps?.connect },
    { key: "prepare", label: "準備參與者角色", status: tab.loaderSteps?.prepare },
    { key: "moderator", label: "主持人構思開場白", status: tab.loaderSteps?.moderator },
    { key: "start", label: "辯論即將開始", status: tab.loaderSteps?.start },
  ];
  return (
    <ProgressLoader
      visible={tab.loaderVisible}
      stage={tab.loaderStage}
      steps={steps}
      progress={tab.loaderProgress}
      text="AI 正在分析辯論主題並準備論點，請稍候..."
    />
  );
}

function DebateMessage({ item }) {
  if (item.type === "round") return <div className="round-divider">{item.label}</div>;
  if (item.type === "thinking") return <div className="message thinking">{item.content}</div>;
  if (item.type === "video") {
    return (
      <div className="message video-msg">
        <div className="speaker">{item.speaker}</div>
        <div className="content">
          <video src={item.url} controls autoPlay muted loop playsInline />
        </div>
      </div>
    );
  }
  if (item.type === "mergedVideo") {
    return (
      <div className="message video-msg" style={{ maxWidth: "95%" }}>
        <div className="speaker">{item.speaker}</div>
        <div className="content" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <video src={item.url} controls playsInline style={{ maxWidth: "100%", borderRadius: 8 }} />
          <a href={item.url} download className="btn btn-outline btn-sm" style={{ alignSelf: "flex-start", textDecoration: "none", color: "var(--blue)" }}>
            下載完整影片
          </a>
        </div>
      </div>
    );
  }
  return (
    <div className={`message ${item.className}`}>
      <div className="speaker">{item.speaker}</div>
      <div className="content" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.content) }} />
    </div>
  );
}

export default function DebateWorkspace({
  tabs,
  activeTabId,
  visible,
  humanInputMaxLength,
  onNewDebate,
  onSwitchTab,
  onCloseTab,
  onStopDebate,
  onOpenSettings,
  onSubmitHumanInput,
  onSkipHumanTurn,
  onHumanInputChange,
  onHumanInputKeyDown,
  canOpenMoreDebates,
  maxOpenDebates,
}) {
  const messageRefs = useRef({});

  useEffect(() => {
    const activeMessages = messageRefs.current[activeTabId];
    if (activeMessages) {
      activeMessages.scrollTop = activeMessages.scrollHeight;
    }
  }, [activeTabId, tabs]);

  return (
    <div id="debate-container" style={{ display: visible ? "flex" : "none" }}>
      <div id="tab-bar">
        {tabs.map((tab, index) => (
          <div
            key={tab.id}
            className={`debate-tab ${tab.id === activeTabId ? "active" : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => onSwitchTab(tab.id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSwitchTab(tab.id);
              }
            }}
          >
            <span className={`tab-dot ${tab.running ? "running" : ""}`} />
            {`辯論 ${index + 1}`}
            <button
              type="button"
              className="tab-close"
              onClick={(event) => {
                event.stopPropagation();
                onCloseTab(tab.id);
              }}
            >
              x
            </button>
          </div>
        ))}
        <button
          type="button"
          className="tab-add-btn"
          title={canOpenMoreDebates ? "新增辯論" : `同時開啟的辯論最多 ${maxOpenDebates} 場`}
          onClick={onNewDebate}
          disabled={!canOpenMoreDebates}
        >
          +
        </button>
      </div>
      <div id="tab-contents">
        {tabs.map((tab) => (
          <div key={tab.id} className={`debate-panel ${tab.id === activeTabId ? "active" : ""}`} data-tab-id={tab.id}>
            <div className="chat-area">
              <div className="chat-header">
                <div className="status">
                  <div className={`status-dot ${tab.running ? "active" : ""}`} />
                  <span className="status-text">{tab.statusText}</span>
                </div>
                <div className="actions">
                  <span className="round-info" style={{ fontSize: 13, color: "var(--text-dim)" }}>{tab.roundInfo}</span>
                  <button type="button" className="btn btn-outline btn-sm" onClick={onOpenSettings}>設定</button>
                  {tab.running ? (
                    <button type="button" className="btn btn-danger btn-sm btn-stop" onClick={() => onStopDebate(tab.id)}>
                      停止
                    </button>
                  ) : (
                    <button type="button" className="btn btn-outline btn-sm btn-back" onClick={onNewDebate}>
                      返回設定
                    </button>
                  )}
                </div>
              </div>
              <div
                className="chat-messages"
                ref={(node) => {
                  if (node) messageRefs.current[tab.id] = node;
                  else delete messageRefs.current[tab.id];
                }}
              >
                <Loader tab={tab} />
                {tab.messages.map((item) => <DebateMessage key={item.id} item={item} />)}
              </div>
              <div className={`human-input-bar ${tab.humanInputVisible ? "active" : ""}`}>
                <div className="input-wrap">
                  <div className="input-label-row">
                    <span className="input-label">{tab.waitingSpeaker ? `輪到 ${tab.waitingSpeaker} 發言` : "輪到你發言了"}</span>
                    <span className={`countdown-display ${tab.countdownSeconds <= 10 ? "critical" : tab.countdownSeconds <= 30 ? "warning" : ""}`}>
                      {tab.humanInputVisible
                        ? `${Math.floor(tab.countdownSeconds / 60)}:${String(tab.countdownSeconds % 60).padStart(2, "0")}`
                        : ""}
                    </span>
                  </div>
                  <textarea
                    className="human-input-text"
                    placeholder="輸入你的觀點..."
                    maxLength={humanInputMaxLength}
                    value={tab.humanInputText}
                    onChange={(event) => onHumanInputChange(tab.id, event.target.value)}
                    onKeyDown={(event) => onHumanInputKeyDown(event, tab.id)}
                  />
                </div>
                <button type="button" className="btn btn-primary" onClick={() => onSubmitHumanInput(tab.id)}>
                  送出
                </button>
                <button type="button" className="btn btn-outline" onClick={() => onSkipHumanTurn(tab.id)}>
                  跳過
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
