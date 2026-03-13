export default function ProgressLoader({ visible = true, stage, steps, progress, text, className = "" }) {
  if (!visible) return null;

  return (
    <div className={`init-loader ${className}`.trim()}>
      <div className="loader-ring" />
      <div className="loader-stage">{stage}</div>
      <div className="loader-steps">
        {steps.map((step) => (
          <div key={step.key} className={`step ${step.status || ""}`} data-step={step.key}>
            <span className="step-dot" />
            {step.label}
          </div>
        ))}
      </div>
      <div className="loader-bar">
        <div className="loader-bar-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="loader-text">{text}</div>
    </div>
  );
}
