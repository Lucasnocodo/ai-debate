import { renderMarkdown } from "../lib/utils";

function MessageItem({ item }) {
  if (item.type === "round") {
    return <div className="round-divider">{item.label}</div>;
  }
  if (item.type === "video") {
    return (
      <div className="message video-msg">
        <div className="speaker">{item.speaker}</div>
        <div className="content">
          <video src={item.url} controls muted loop playsInline />
        </div>
      </div>
    );
  }
  return (
    <div className={`message ${item.className || "ai-0"}`}>
      <div className="speaker">{item.speaker}</div>
      <div className="content" dangerouslySetInnerHTML={{ __html: renderMarkdown(item.content) }} />
    </div>
  );
}

export default function LogModal({ open, title, items, loading, error, onClose, onDownload }) {
  return (
    <div className={`modal-overlay ${open ? "active" : ""}`} id="log-modal">
      <div className="modal">
        <div className="modal-header">
          <h2>{title || "辯論紀錄"}</h2>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button type="button" className="btn btn-outline btn-sm" onClick={onDownload}>
              下載 .md
            </button>
            <button type="button" className="modal-close" onClick={onClose}>
              x
            </button>
          </div>
        </div>
        <div className="modal-body">
          {loading ? <div style={{ textAlign: "center", color: "var(--text-dim)", padding: 40 }}>載入中...</div> : null}
          {error ? <div style={{ color: "var(--red)", padding: 40, textAlign: "center" }}>{error}</div> : null}
          {!loading && !error ? items.map((item) => <MessageItem key={item.id} item={item} />) : null}
        </div>
      </div>
    </div>
  );
}
