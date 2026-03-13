export default function CountdownModal({ open, onContinue, onSkip }) {
  return (
    <div className={`countdown-modal-overlay ${open ? "active" : ""}`} role="dialog" aria-modal="true" aria-label="倒數計時確認">
      <div className="countdown-modal" aria-live="assertive">
        <h3>時間即將到期</h3>
        <p>剩餘不到 30 秒，是否繼續輸入？</p>
        <div className="btn-group" style={{ justifyContent: "center" }}>
          <button type="button" className="btn btn-primary" onClick={onContinue}>
            繼續輸入
          </button>
          <button type="button" className="btn btn-outline" onClick={onSkip}>
            跳過此輪
          </button>
        </div>
      </div>
    </div>
  );
}
