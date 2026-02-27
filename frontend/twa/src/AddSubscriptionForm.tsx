import { useMemo, useState } from "react";

type AddSubscriptionFormProps = {
  onSubmit: (payload: {
    fromCity?: string;
    toCity?: string;
    bodyType?: string;
    minRate?: number;
    maxWeight?: number;
    region?: string;
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

export function AddSubscriptionForm({
  onSubmit,
  onCancel,
  busy = false,
  error = null,
}: AddSubscriptionFormProps) {
  const [fromCity, setFromCity] = useState("");
  const [toCity, setToCity] = useState("");
  const [bodyType, setBodyType] = useState("");
  const [minRate, setMinRate] = useState("");
  const [maxWeight, setMaxWeight] = useState("");
  const [region, setRegion] = useState("");

  const canSubmit = useMemo(() => (
    Boolean(fromCity.trim())
    || Boolean(toCity.trim())
    || Boolean(bodyType.trim())
    || Boolean(minRate.trim())
    || Boolean(maxWeight.trim())
    || Boolean(region.trim())
  ), [bodyType, fromCity, maxWeight, minRate, region, toCity]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    await onSubmit({
      fromCity: fromCity.trim() || undefined,
      toCity: toCity.trim() || undefined,
      bodyType: bodyType.trim() || undefined,
      minRate: minRate.trim() ? Number.parseInt(minRate, 10) : undefined,
      maxWeight: maxWeight.trim() ? Number.parseFloat(maxWeight) : undefined,
      region: region.trim() || undefined,
    });
  }

  return (
    <form className="cargo-form" onSubmit={handleSubmit}>
      <div className="cargo-form-grid">
        <label className="truck-field">
          <span>Откуда</span>
          <input
            type="text"
            value={fromCity}
            onChange={(event) => setFromCity(event.target.value)}
            placeholder="Москва"
            disabled={busy}
          />
        </label>

        <label className="truck-field">
          <span>Куда</span>
          <input
            type="text"
            value={toCity}
            onChange={(event) => setToCity(event.target.value)}
            placeholder="Казань"
            disabled={busy}
          />
        </label>

        <label className="truck-field">
          <span>Тип кузова</span>
          <select
            value={bodyType}
            onChange={(event) => setBodyType(event.target.value)}
            disabled={busy}
          >
            <option value="">Любой</option>
            {BODY_TYPES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>

        <label className="truck-field">
          <span>Мин. ставка, ₽</span>
          <input
            type="number"
            min="0"
            step="1000"
            value={minRate}
            onChange={(event) => setMinRate(event.target.value)}
            placeholder="100000"
            disabled={busy}
          />
        </label>

        <label className="truck-field">
          <span>Макс. вес, т</span>
          <input
            type="number"
            min="0"
            step="0.5"
            value={maxWeight}
            onChange={(event) => setMaxWeight(event.target.value)}
            placeholder="20"
            disabled={busy}
          />
        </label>

        <label className="truck-field">
          <span>Регион</span>
          <input
            type="text"
            value={region}
            onChange={(event) => setRegion(event.target.value)}
            placeholder="Приволжье"
            disabled={busy}
          />
        </label>
      </div>

      {error && <div className="error truck-form-error">{error}</div>}

      <div className="truck-form-actions">
        <button type="submit" className="action-btn primary" disabled={busy || !canSubmit}>
          {busy ? "⏳ Сохраняем" : "✅ Сохранить подписку"}
        </button>
        <button type="button" className="action-btn" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
      </div>
    </form>
  );
}
