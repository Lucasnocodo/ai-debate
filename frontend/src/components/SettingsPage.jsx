import ParticipantCard from "./ParticipantCard";
import ProgressLoader from "./ProgressLoader";
import { Field, ToggleField } from "./Field";
import { CATEGORY_PLACEHOLDERS, DEBATE_STYLE_OPTIONS, MAX_AI_PARTICIPANTS } from "../lib/constants";

const CATEGORIES = ["自訂", "政治", "財經", "科技", "遊戲", "哲學", "教育", "生活", "AI"];

export default function SettingsPage({
  settingsTab,
  onSwitchTab,
  showSettings,
  overlay,
  showBackButton,
  onBackToDebates,
  onCloseOverlay,
  config,
  errors,
  generateStatus,
  logsView,
  onCategorySelect,
  onConfigChange,
  onConfigBlur,
  onGenerateConfig,
  onToggleHuman,
  onToggleModerator,
  onAddParticipant,
  onParticipantChange,
  onParticipantBlur,
  onParticipantRemove,
  onStartDebate,
  onOpenLog,
  limits,
  canOpenMoreDebates,
  maxOpenDebates,
  hasSavedDraft,
  onResetSettings,
}) {
  const outlinePlaceholder = CATEGORY_PLACEHOLDERS[config.selectedCategory] || CATEGORY_PLACEHOLDERS.自訂;

  return (
    <>
      <div className={`settings-page ${overlay ? "overlay" : ""}`} style={{ display: showSettings ? "" : "none" }}>
        <div className="settings-inner">
          <div className="overlay-header">
            <h2 style={{ margin: 0, color: "var(--text)" }}>設定</h2>
            <button type="button" className="modal-close" onClick={onCloseOverlay}>
              x
            </button>
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <div>
              <h1>AI 辯論競技場</h1>
              <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4, letterSpacing: "0.3px" }}>
                設定 AI 角色、選定主題，開啟一場精彩辯論
              </p>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
              <button
                type="button"
                className="btn btn-outline btn-sm"
                style={{ display: hasSavedDraft ? "" : "none" }}
                onClick={onResetSettings}
              >
                新的辯論
              </button>
              <button
                type="button"
                id="btn-back-to-debates"
                className="btn btn-outline btn-sm"
                style={{ display: showBackButton ? "" : "none" }}
                onClick={onBackToDebates}
              >
                返回辯論
              </button>
            </div>
          </div>

          <div className="tabs">
            <button type="button" className={`tab ${settingsTab === "config" ? "active" : ""}`} onClick={() => onSwitchTab("config")}>
              設定
            </button>
            <button type="button" className={`tab ${settingsTab === "logs" ? "active" : ""}`} onClick={() => onSwitchTab("logs")}>
              歷史紀錄
            </button>
          </div>

          {settingsTab === "config" ? (
            <div id="tab-config">
              <div className="gen-box">
                <h2>快速生成辯論設定</h2>
                <Field label="主題分類">
                  <div className="gen-tags">
                    {CATEGORIES.map((category) => (
                      <button
                        key={category}
                        type="button"
                        className={`gen-tag ${config.selectedCategory === category ? "active" : ""}`}
                        onClick={() => onCategorySelect(category)}
                      >
                        {category}
                      </button>
                    ))}
                  </div>
                </Field>

                <Field label="大綱補充" error={errors.genOutline} className="gen-outline-wrap">
                  <textarea
                    id="gen-outline"
                    rows="3"
                    className={errors.genOutline ? "invalid" : ""}
                    placeholder={outlinePlaceholder}
                    maxLength={limits.genOutline}
                    value={config.genOutline}
                    onChange={(event) => onConfigChange("genOutline", event.target.value)}
                    onBlur={(event) => onConfigBlur("genOutline", event.target.value)}
                  />
                  <div className="gen-outline-note" id="gen-outline-note">
                    {config.selectedCategory === "自訂"
                      ? "請直接描述你想辯論的主題、立場衝突、限制條件或希望 AI 聚焦的重點。"
                      : `目前主題分類為「${config.selectedCategory}」。可在這裡補充更細的題目方向、衝突點或你想特別深挖的面向。`}
                  </div>
                </Field>

                <Field label="辯論風格">
                  <div className="gen-tags style-tags">
                    {DEBATE_STYLE_OPTIONS.map((style) => (
                      <button
                        key={style.id}
                        type="button"
                        className={`gen-tag style-tag ${config.debateStyle === style.id ? "active" : ""}`}
                        onClick={() => onConfigChange("debateStyle", style.id)}
                        title={style.description}
                      >
                        {style.label}
                      </button>
                    ))}
                  </div>
                  <div className="gen-outline-note">
                    {DEBATE_STYLE_OPTIONS.find((style) => style.id === config.debateStyle)?.description || "選一種你要的攻防氣質。"}
                  </div>
                </Field>

                <div className="gen-row">
                  <Field label="AI 辯論者人數" error={errors.genCount}>
                    <input
                      type="number"
                      id="gen-count"
                      min="2"
                      max={String(MAX_AI_PARTICIPANTS)}
                      className={errors.genCount ? "invalid" : ""}
                      value={config.genCount}
                      onChange={(event) => onConfigChange("genCount", event.target.value)}
                      onBlur={(event) => onConfigBlur("genCount", event.target.value)}
                    />
                  </Field>
                  <div className="field">
                    <button
                      type="button"
                      className="btn btn-primary"
                      id="btn-generate"
                      onClick={onGenerateConfig}
                      disabled={generateStatus.loading}
                      style={{ whiteSpace: "nowrap", marginTop: 20 }}
                    >
                      {generateStatus.loading ? "生成中..." : "生成設定"}
                    </button>
                  </div>
	                </div>
	                {generateStatus.loading ? (
	                  <ProgressLoader
	                    className="compact"
	                    stage={generateStatus.stage}
	                    steps={generateStatus.steps}
	                    progress={generateStatus.progress}
	                    text={generateStatus.message}
	                  />
	                ) : null}
	                <div
	                  id="gen-status"
	                  className={`gen-status ${generateStatus.error ? "error" : ""}`}
	                  style={{ display: !generateStatus.loading && generateStatus.message ? "block" : "none" }}
	                >
	                  {generateStatus.message}
	                </div>
	              </div>

              {config.showTopicField ? (
                <Field
                  label="辯論主題（由 AI 生成，可編輯微調）"
                  error={errors.topic}
                  id="topic-field"
                >
                  <textarea
                    id="topic"
                    rows="8"
                    className={errors.topic ? "invalid" : ""}
                    placeholder="點擊上方「生成設定」後自動填入"
                    maxLength={limits.topic}
                    value={config.topic}
                    onChange={(event) => onConfigChange("topic", event.target.value)}
                    onBlur={(event) => onConfigBlur("topic", event.target.value)}
                  />
                </Field>
              ) : null}

              <div className="row toggle-grid">
                <ToggleField label="加入戰局" checked={config.humanEnabled} onToggle={onToggleHuman} description="開啟人類參與者" />
                <ToggleField label="主持人" checked={config.modEnabled} onToggle={onToggleModerator} description="啟用主持人" />
              </div>

              <div className="row">
                <Field label="輪數" error={errors.rounds}>
                  <input
                    type="number"
                    min="1"
                    max="6"
                    className={errors.rounds ? "invalid" : ""}
                    value={config.rounds}
                    onChange={(event) => onConfigChange("rounds", event.target.value)}
                    onBlur={(event) => onConfigBlur("rounds", event.target.value)}
                  />
                </Field>
                {config.humanEnabled ? (
                  <Field label="人類回覆時限（秒）" error={errors.humanTimeLimit} id="human-time-field">
                    <input
                      type="number"
                      min="30"
                      max="300"
                      step="10"
                      className={errors.humanTimeLimit ? "invalid" : ""}
                      value={config.humanTimeLimit}
                      onChange={(event) => onConfigChange("humanTimeLimit", event.target.value)}
                      onBlur={(event) => onConfigBlur("humanTimeLimit", event.target.value)}
                    />
                  </Field>
                ) : null}
              </div>

              <div className={`collapsible ${config.modEnabled ? "open" : ""}`} id="mod-section">
                <div className="moderator-card">
                  <div className="card-header">
                    <span>主持人設定</span>
                  </div>
                  <Field label="名稱" error={errors.modName}>
                    <input
                      type="text"
                      className={errors.modName ? "invalid" : ""}
                      maxLength={limits.modName}
                      value={config.modName}
                      onChange={(event) => onConfigChange("modName", event.target.value)}
                      onBlur={(event) => onConfigBlur("modName", event.target.value)}
                    />
                  </Field>
                  <Field label="附加指示（選填）" error={errors.modSystem}>
                    <textarea
                      rows="2"
                      placeholder="例如：特別關注 AI 套利相關議題的可行性分析"
                      className={errors.modSystem ? "invalid" : ""}
                      maxLength={limits.modSystem}
                      value={config.modSystem}
                      onChange={(event) => onConfigChange("modSystem", event.target.value)}
                      onBlur={(event) => onConfigBlur("modSystem", event.target.value)}
                    />
                  </Field>
                </div>
              </div>

              {config.showParticipantsSection ? (
                <div id="participants-section">
                  <div className="section-divider">
                    <h2>參與者</h2>
                  </div>
                  <div id="participants" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                    {config.participants.map((participant, index) => (
                      <ParticipantCard
                        key={participant.id}
                        participant={participant}
                        index={index}
                        onChange={onParticipantChange}
                        onBlur={onParticipantBlur}
                        onRemove={onParticipantRemove}
                        errors={errors.participants?.[participant.id]}
                        limits={limits}
                      />
                    ))}
                  </div>
                  <button
                    type="button"
                    className="btn btn-outline"
                    style={{ width: "100%", borderStyle: "dashed", padding: "14px 20px" }}
                    onClick={onAddParticipant}
                  >
                    + 新增參與者
                  </button>
	                  <div className="section-error">{errors.participantsSection || ""}</div>
	                  <div className="section-error">{errors.sessionLimit || ""}</div>
	                  <div className="btn-start-group" style={{ marginTop: 8 }}>
	                    <button
	                      type="button"
	                      className="btn btn-primary"
	                      id="btn-start"
	                      style={{ width: "100%", padding: "16px 20px", fontSize: 16, letterSpacing: "0.5px" }}
	                      onClick={onStartDebate}
	                      disabled={!canOpenMoreDebates}
	                      title={!canOpenMoreDebates ? `同時開啟的辯論最多 ${maxOpenDebates} 場` : undefined}
	                    >
	                      開始辯論
	                    </button>
	                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div id="tab-logs">
              <div className="logs-list" id="logs-list">
                {logsView.loading ? "載入中..." : null}
                {!logsView.loading && logsView.error ? (
                  <div style={{ color: "var(--red)", padding: 20, textAlign: "center" }}>{logsView.error}</div>
                ) : null}
                {!logsView.loading && !logsView.error && logsView.items.length === 0 ? (
                  <div style={{ color: "var(--text-dim)", padding: 20, textAlign: "center" }}>尚無紀錄</div>
                ) : null}
                {!logsView.loading && !logsView.error
                  ? logsView.items.map((log) => (
                      <div key={log.name} className="log-item">
                        <div className="log-info" onClick={() => onOpenLog(log.name)} onKeyDown={() => {}} role="button" tabIndex={0}>
                          <span className="log-name">{log.displayName}</span>
                          <span className="log-size">{log.sizeLabel}</span>
                        </div>
                        <div className="log-actions">
                          <button type="button" className="btn btn-outline btn-sm" onClick={() => onOpenLog(log.name)}>
                            檢視
                          </button>
                          <a className="btn btn-outline btn-sm" href={`/api/logs/${encodeURIComponent(log.name)}`} download={log.name}>
                            下載
                          </a>
                        </div>
                      </div>
                    ))
                  : null}
              </div>
            </div>
          )}
        </div>
      </div>
      <div
        id="settings-overlay-bg"
        className={`settings-overlay-bg ${overlay ? "active" : ""}`}
        onClick={onCloseOverlay}
      />
    </>
  );
}
