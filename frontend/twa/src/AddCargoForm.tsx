import { useMemo, useState } from "react";

type AddCargoFormProps = {
  onSubmit: (payload: {
    origin: string;
    destination: string;
    bodyType: string;
    weight: number;
    price: number;
    loadDate: string;
    loadTime?: string;
    description?: string;
    paymentTerms?: string;
  }) => Promise<void>;
  onCancel: () => void;
  busy?: boolean;
  error?: string | null;
  initialValues?: {
    origin?: string;
    destination?: string;
    bodyType?: string;
    weight?: number;
    price?: number;
    loadDate?: string;
    loadTime?: string | null;
    description?: string | null;
    paymentTerms?: string | null;
  };
  submitLabel?: string;
};

const BODY_TYPES = [
  "тент",
  "рефрижератор",
  "трал",
  "борт",
  "контейнер",
  "изотерм",
];

function defaultDate(): string {
  return new Date().toISOString().slice(0, 10);
}

export function AddCargoForm({
  onSubmit,
  onCancel,
  busy = false,
  error = null,
  initialValues,
  submitLabel,
}: AddCargoFormProps) {
  const [origin, setOrigin] = useState(initialValues?.origin ?? "");
  const [destination, setDestination] = useState(initialValues?.destination ?? "");
  const [bodyType, setBodyType] = useState(initialValues?.bodyType ?? "тент");
  const [weight, setWeight] = useState(String(initialValues?.weight ?? 20));
  const [price, setPrice] = useState(String(initialValues?.price ?? 120000));
  const [loadDate, setLoadDate] = useState(initialValues?.loadDate ?? defaultDate);
  const [loadTime, setLoadTime] = useState(initialValues?.loadTime ?? "");
  const [description, setDescription] = useState(initialValues?.description ?? "");
  const [paymentTerms, setPaymentTerms] = useState(initialValues?.paymentTerms ?? "");

  const canSubmit = useMemo(() => {
    const weightNumber = Number.parseFloat(weight);
    const priceNumber = Number.parseInt(price, 10);
    return (
      origin.trim().length >= 2
      && destination.trim().length >= 2
      && Number.isFinite(weightNumber)
      && weightNumber > 0
      && Number.isFinite(priceNumber)
      && priceNumber > 0
      && loadDate.trim().length === 10
    );
  }, [destination, loadDate, origin, price, weight]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    await onSubmit({
      origin: origin.trim(),
      destination: destination.trim(),
      bodyType: bodyType.trim(),
      weight: Number.parseFloat(weight),
      price: Number.parseInt(price, 10),
      loadDate: loadDate.trim(),
      loadTime: loadTime.trim() || undefined,
      description: description.trim() || undefined,
      paymentTerms: paymentTerms.trim() || undefined,
    });
  }

  return (
    <form className="cargo-form" onSubmit={handleSubmit}>
      <div className="cargo-form-grid">
        <label className="truck-field">
          <span>Откуда</span>
          <input
            type="text"
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
            placeholder="Москва"
            disabled={busy}
            required
          />
        </label>

        <label className="truck-field">
          <span>Куда</span>
          <input
            type="text"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            placeholder="Казань"
            disabled={busy}
            required
          />
        </label>

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
            value={weight}
            onChange={(event) => setWeight(event.target.value)}
            disabled={busy}
            required
          />
        </label>

        <label className="truck-field">
          <span>Ставка, ₽</span>
          <input
            type="number"
            min="1"
            step="1000"
            value={price}
            onChange={(event) => setPrice(event.target.value)}
            disabled={busy}
            required
          />
        </label>

        <label className="truck-field">
          <span>Дата готовности</span>
          <input
            type="date"
            value={loadDate}
            onChange={(event) => setLoadDate(event.target.value)}
            disabled={busy}
            required
          />
        </label>

        <label className="truck-field">
          <span>Время (опционально)</span>
          <input
            type="time"
            value={loadTime}
            onChange={(event) => setLoadTime(event.target.value)}
            disabled={busy}
          />
        </label>

        <label className="truck-field">
          <span>Условия оплаты</span>
          <input
            type="text"
            value={paymentTerms}
            onChange={(event) => setPaymentTerms(event.target.value)}
            placeholder="без НДС, безнал"
            disabled={busy}
          />
        </label>

        <label className="truck-field cargo-description">
          <span>Описание</span>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Что за груз, сколько машин, особые условия"
            disabled={busy}
            rows={4}
          />
        </label>
      </div>

      {error && <div className="error truck-form-error">{error}</div>}

      <div className="truck-form-actions">
        <button type="submit" className="action-btn primary" disabled={busy || !canSubmit}>
          {busy ? "⏳ Сохраняем" : (submitLabel ?? "✅ Добавить груз")}
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
