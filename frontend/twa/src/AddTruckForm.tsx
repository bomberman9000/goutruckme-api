import { useState } from "react";

type AddTruckFormProps = {
  onSubmit: (payload: {
    bodyType: string;
    capacityTons: number;
    locationCity?: string;
    plateNumber?: string;
    markAvailable: boolean;
  }) => Promise<void>;
  onCancel: () => void;
  busy?: boolean;
  error?: string | null;
};

const BODY_TYPES = [
  "тент",
  "рефрижератор",
  "трал",
  "борт",
  "контейнер",
  "изотерм",
];

export function AddTruckForm({
  onSubmit,
  onCancel,
  busy = false,
  error = null,
}: AddTruckFormProps) {
  const [bodyType, setBodyType] = useState("тент");
  const [capacityTons, setCapacityTons] = useState("20");
  const [locationCity, setLocationCity] = useState("");
  const [plateNumber, setPlateNumber] = useState("");
  const [markAvailable, setMarkAvailable] = useState(true);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const parsedTons = Number.parseFloat(capacityTons);
    if (!Number.isFinite(parsedTons) || parsedTons <= 0) {
      return;
    }

    await onSubmit({
      bodyType: bodyType.trim(),
      capacityTons: parsedTons,
      locationCity: locationCity.trim() || undefined,
      plateNumber: plateNumber.trim() || undefined,
      markAvailable,
    });
  }

  return (
    <form className="truck-form" onSubmit={handleSubmit}>
      <div className="truck-form-grid">
        <label className="truck-field">
          <span>Тип кузова</span>
          <select
            value={bodyType}
            onChange={(event) => setBodyType(event.target.value)}
            disabled={busy}
          >
            {BODY_TYPES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>

        <label className="truck-field">
          <span>Тоннаж, т</span>
          <input
            type="number"
            min="0.5"
            step="0.5"
            value={capacityTons}
            onChange={(event) => setCapacityTons(event.target.value)}
            disabled={busy}
            required
          />
        </label>

        <label className="truck-field">
          <span>Откуда / город базирования</span>
          <input
            type="text"
            value={locationCity}
            onChange={(event) => setLocationCity(event.target.value)}
            placeholder="Казань"
            disabled={busy}
          />
        </label>

        <label className="truck-field">
          <span>Госномер</span>
          <input
            type="text"
            value={plateNumber}
            onChange={(event) => setPlateNumber(event.target.value.toUpperCase())}
            placeholder="A123BC 116"
            disabled={busy}
          />
        </label>
      </div>

      <label className="truck-check">
        <input
          type="checkbox"
          checked={markAvailable}
          onChange={(event) => setMarkAvailable(event.target.checked)}
          disabled={busy}
        />
        <span>Сразу отметить машину как свободную</span>
      </label>

      {error && <div className="error truck-form-error">{error}</div>}

      <div className="truck-form-actions">
        <button type="submit" className="action-btn primary" disabled={busy}>
          {busy ? "⏳ Сохраняем" : "✅ Добавить машину"}
        </button>
        <button
          type="button"
          className="action-btn"
          onClick={onCancel}
          disabled={busy}
        >
          Отмена
        </button>
      </div>
    </form>
  );
}
