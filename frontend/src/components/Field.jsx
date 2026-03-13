export function Field({ label, error, className = "", children, id }) {
  return (
    <div className={`field ${error ? "has-error" : ""} ${className}`.trim()} id={id}>
      {label ? <label>{label}</label> : null}
      {children}
      <div className="field-error">{error || ""}</div>
    </div>
  );
}

export function ToggleField({ label, checked, onToggle, description }) {
  return (
    <Field label={label}>
      <div className="toggle-row">
        <button
          type="button"
          className={`toggle ${checked ? "on" : ""}`}
          onClick={onToggle}
        />
        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text)", letterSpacing: "0.2px" }}>
          {description}
        </span>
      </div>
    </Field>
  );
}
