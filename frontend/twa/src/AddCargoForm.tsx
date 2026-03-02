import { useEffect, useId, useMemo, useRef, useState } from "react";

import {
  fetchRecommendedCargoRate,
  searchCities,
  type CitySuggestion,
  type RecommendedCargoRate,
} from "./api";

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

function parseDateInput(value: string): string | null {
  const clean = value.trim();
  if (!clean) {
    return null;
  }

  let year = "";
  let month = "";
  let day = "";

  if (/^\d{4}-\d{2}-\d{2}$/.test(clean)) {
    [year, month, day] = clean.split("-");
  } else if (/^\d{2}\.\d{2}\.\d{4}$/.test(clean)) {
    [day, month, year] = clean.split(".");
  } else {
    return null;
  }

  const yyyy = Number.parseInt(year, 10);
  const mm = Number.parseInt(month, 10);
  const dd = Number.parseInt(day, 10);
  if (!Number.isFinite(yyyy) || !Number.isFinite(mm) || !Number.isFinite(dd)) {
    return null;
  }

  const dt = new Date(Date.UTC(yyyy, mm - 1, dd));
  if (
    dt.getUTCFullYear() !== yyyy
    || dt.getUTCMonth() + 1 !== mm
    || dt.getUTCDate() !== dd
  ) {
    return null;
  }

  return `${year.padStart(4, "0")}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
}

function formatDateForField(value: string): string {
  const iso = parseDateInput(value);
  if (!iso) {
    return value;
  }
  const [year, month, day] = iso.split("-");
  return `${day}.${month}.${year}`;
}

function normalizeDateTyping(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 2) {
    return digits;
  }
  if (digits.length <= 4) {
    return `${digits.slice(0, 2)}.${digits.slice(2)}`;
  }
  return `${digits.slice(0, 2)}.${digits.slice(2, 4)}.${digits.slice(4)}`;
}

function normalizeIntegerTyping(value: string): string {
  return value.replace(/\D/g, "").slice(0, 10);
}

function parseIntegerInput(value: string): number | null {
  const digits = normalizeIntegerTyping(value);
  if (!digits) {
    return null;
  }
  const parsed = Number.parseInt(digits, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return null;
  }
  return parsed;
}

function formatRub(value: number | null | undefined): string {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return "—";
  }
  return new Intl.NumberFormat("ru-RU").format(numeric);
}

export function AddCargoForm({
  onSubmit,
  onCancel,
  busy = false,
  error = null,
  initialValues,
  submitLabel,
}: AddCargoFormProps) {
  const originListId = useId();
  const destinationListId = useId();
  const [origin, setOrigin] = useState(initialValues?.origin ?? "");
  const [destination, setDestination] = useState(initialValues?.destination ?? "");
  const [bodyType, setBodyType] = useState(initialValues?.bodyType ?? "тент");
  const [weight, setWeight] = useState(String(initialValues?.weight ?? 20));
  const [price, setPrice] = useState(String(initialValues?.price ?? 120000));
  const [loadDate, setLoadDate] = useState(formatDateForField(initialValues?.loadDate ?? defaultDate()));
  const [loadTime, setLoadTime] = useState(initialValues?.loadTime ?? "");
  const [description, setDescription] = useState(initialValues?.description ?? "");
  const [paymentTerms, setPaymentTerms] = useState(initialValues?.paymentTerms ?? "");
  const [originSuggestions, setOriginSuggestions] = useState<CitySuggestion[]>([]);
  const [destinationSuggestions, setDestinationSuggestions] = useState<CitySuggestion[]>([]);
  const [originFocused, setOriginFocused] = useState(false);
  const [destinationFocused, setDestinationFocused] = useState(false);
  const originRef = useRef<HTMLDivElement>(null);
  const destinationRef = useRef<HTMLDivElement>(null);
  const [recommendedRate, setRecommendedRate] = useState<RecommendedCargoRate | null>(null);
  const [recommendedRateError, setRecommendedRateError] = useState<string | null>(null);

  const canSubmit = useMemo(() => {
    const weightNumber = Number.parseFloat(weight);
    const priceNumber = parseIntegerInput(price);
    return (
      origin.trim().length >= 2
      && destination.trim().length >= 2
      && Number.isFinite(weightNumber)
      && weightNumber > 0
      && priceNumber !== null
      && parseDateInput(loadDate) !== null
    );
  }, [destination, loadDate, origin, price, weight]);

  useEffect(() => {
    if (origin.trim().length < 2) {
      setOriginSuggestions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const items = await searchCities(origin, 5);
        if (!cancelled) {
          setOriginSuggestions(items);
        }
      } catch {
        if (!cancelled) {
          setOriginSuggestions([]);
        }
      }
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [origin]);

  useEffect(() => {
    if (destination.trim().length < 2) {
      setDestinationSuggestions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const items = await searchCities(destination, 5);
        if (!cancelled) {
          setDestinationSuggestions(items);
        }
      } catch {
        if (!cancelled) {
          setDestinationSuggestions([]);
        }
      }
    }, 220);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [destination]);

  useEffect(() => {
    const weightNumber = Number.parseFloat(weight);
    if (
      origin.trim().length < 2
      || destination.trim().length < 2
      || !Number.isFinite(weightNumber)
      || weightNumber <= 0
      || bodyType.trim().length < 2
    ) {
      setRecommendedRate(null);
      setRecommendedRateError(null);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const result = await fetchRecommendedCargoRate({
          origin,
          destination,
          weight: weightNumber,
          body_type: bodyType,
        });
        if (!cancelled) {
          setRecommendedRate(result);
          setRecommendedRateError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setRecommendedRate(null);
          const message = err instanceof Error ? err.message : "Не удалось рассчитать ставку";
          setRecommendedRateError(message === "Invalid cities detected" ? null : message);
        }
      }
    }, 320);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [bodyType, destination, origin, weight]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    const normalizedLoadDate = parseDateInput(loadDate);
    if (!normalizedLoadDate) {
      return;
    }

    const normalizedPrice = parseIntegerInput(price);
    if (!normalizedPrice) {
      return;
    }

    await onSubmit({
      origin: origin.trim(),
      destination: destination.trim(),
      bodyType: bodyType.trim(),
      weight: Number.parseFloat(weight),
      price: normalizedPrice,
      loadDate: normalizedLoadDate,
      loadTime: loadTime.trim() || undefined,
      description: description.trim() || undefined,
      paymentTerms: paymentTerms.trim() || undefined,
    });
  }

  return (
    <form className="cargo-form" onSubmit={handleSubmit}>
      <div className="cargo-form-grid">
        <div className="truck-field city-autocomplete" ref={originRef}>
          <span>Откуда</span>
          <input
            type="text"
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
            onFocus={() => setOriginFocused(true)}
            onBlur={() => setTimeout(() => setOriginFocused(false), 150)}
            placeholder="Москва"
            disabled={busy}
            required
            autoComplete="off"
          />
          {originFocused && originSuggestions.length > 0 && (
            <ul className="city-suggestions">
              {originSuggestions.map((item) => (
                <li
                  key={`origin-${item.full_name}`}
                  onMouseDown={() => { setOrigin(item.name); setOriginFocused(false); }}
                >
                  {item.full_name}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="truck-field city-autocomplete" ref={destinationRef}>
          <span>Куда</span>
          <input
            type="text"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            onFocus={() => setDestinationFocused(true)}
            onBlur={() => setTimeout(() => setDestinationFocused(false), 150)}
            placeholder="Казань"
            disabled={busy}
            required
            autoComplete="off"
          />
          {destinationFocused && destinationSuggestions.length > 0 && (
            <ul className="city-suggestions">
              {destinationSuggestions.map((item) => (
                <li
                  key={`destination-${item.full_name}`}
                  onMouseDown={() => { setDestination(item.name); setDestinationFocused(false); }}
                >
                  {item.full_name}
                </li>
              ))}
            </ul>
          )}
        </div>

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
            min="0.01"
            step="0.01"
            value={weight}
            onChange={(event) => setWeight(event.target.value)}
            disabled={busy}
            required
          />
        </label>

        <label className="truck-field">
          <span>Ставка, ₽</span>
          <input
            type="text"
            value={price}
            onChange={(event) => setPrice(normalizeIntegerTyping(event.target.value))}
            inputMode="numeric"
            placeholder="120000"
            disabled={busy}
            required
          />
        </label>

        <label className="truck-field">
          <span>Дата готовности</span>
          <input
            type="text"
            value={loadDate}
            onChange={(event) => setLoadDate(normalizeDateTyping(event.target.value))}
            inputMode="numeric"
            placeholder="01.03.2026"
            maxLength={10}
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

      {(recommendedRate || recommendedRateError) && (
        <div className="cargo-rate-hint">
          <div className="cargo-rate-hint-head">
            <strong>💡 Рекомендованная ставка</strong>
            {recommendedRate && (
              <button
                type="button"
                className="action-btn"
                disabled={busy}
                onClick={() => setPrice(String(recommendedRate.recommended_rate_rub))}
              >
                Подставить
              </button>
            )}
          </div>
          {recommendedRate ? (
            <>
              <div className="cargo-rate-main">
                {formatRub(recommendedRate.recommended_rate_rub)} ₽
                <span>
                  ~ {recommendedRate.rate_per_km} ₽/км · {recommendedRate.distance_km} км
                </span>
              </div>
              <div className="cargo-rate-range">
                Диапазон: {formatRub(recommendedRate.min_rate_rub)} — {formatRub(recommendedRate.max_rate_rub)} ₽
              </div>
              <div className="cargo-rate-details">{recommendedRate.details}</div>
            </>
          ) : (
            <div className="cargo-rate-error">{recommendedRateError}</div>
          )}
        </div>
      )}

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
