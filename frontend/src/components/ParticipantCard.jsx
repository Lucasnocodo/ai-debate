import { Field } from "./Field";

export default function ParticipantCard({ participant, index, onChange, onBlur, onRemove, errors, limits }) {
  return (
    <div className="participant-card" data-human={participant.human ? "true" : undefined}>
      <div className="card-header">
        <span>{participant.human ? "人類參與者" : `參與者 ${index + 1}`}</span>
        {participant.locked ? null : (
          <button type="button" className="btn-remove" onClick={() => onRemove(participant.id)}>
            x
          </button>
        )}
      </div>
      <Field label="名稱" error={errors?.name}>
        <input
          type="text"
          className={errors?.name ? "invalid" : ""}
          maxLength={limits.participantName}
          value={participant.name}
          onChange={(event) => onChange(participant.id, { name: event.target.value })}
          onBlur={(event) => onBlur(participant.id, "name", event.target.value)}
        />
      </Field>
      {participant.human ? null : (
        <Field label="角色設定" error={errors?.system}>
          <textarea
            rows="5"
            className={errors?.system ? "invalid" : ""}
            maxLength={limits.participantSystem}
            value={participant.system}
            onChange={(event) => onChange(participant.id, { system: event.target.value })}
            onBlur={(event) => onBlur(participant.id, "system", event.target.value)}
          />
        </Field>
      )}
    </div>
  );
}
