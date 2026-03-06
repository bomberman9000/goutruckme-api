import { useEffect, useId, useMemo, useState } from "react";

import {
  fetchRecommendedCargoRate,
  previewManualCargo,
  searchCities,
  type CitySuggestion,
  type ManualCargoParsedPreview,
  type RecommendedCargoRate,
} from "./api";

export type AddCargoFormPayload =
  | {
    mode: "text";
    rawText: string;
  }
  | {
    mode: "form";
    origin: string;
    destination: string;
    bodyType: string;
    weight: number;
    volume?: number;
    price: number;
    loadDate: string;
    loadTime?: string;
    description?: string;
    paymentTerms?: string;
  };

type AddCargoFormProps = {
  onSubmit: (payload: AddCargoFormPayload) => Promise<void>;
  onCancel: () => void;
  busy?: boolean;
  error?: string | null;
  allowSmartPaste?: boolean;
  initialValues?: {
    origin?: string;
    destination?: string;
    bodyType?: string;
    weight?: number;
    volume?: number | null;
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

const WEIGHT_UNITS = [
  { value: "t", label: "т" },
  { value: "kg", label: "кг" },
] as const;

type WeightUnit = (typeof WEIGHT_UNITS)[number]["value"];

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

function normalizeIntegerTyping(value: string): string {
  return value.replace(/\D/g, "").slice(0, 10);
}

function normalizeWeightTyping(value: string): string {
  const lowered = value.toLowerCase().replace(",", ".");
  const cleaned = lowered.replace(/[^0-9.\s]/g, "");
  const parts = cleaned.trim().split(".");
  if (parts.length <= 1) {
    return cleaned.trim();
  }
  return `${parts.shift()}.${parts.join("")}`.trim();
}

function detectWeightUnit(value: string): WeightUnit | null {
  const lowered = value.toLowerCase();
  if (/(?:кг|kg)/.test(lowered)) {
    return "kg";
  }
  if (/(?:^|\s)(?:т|t)(?:$|\s)/.test(lowered) || lowered.endsWith("т")) {
    return "t";
  }
  return null;
}

function initialWeightState(value?: number): { weight: string; unit: WeightUnit } {
  if (!value || !Number.isFinite(value) || value <= 0) {
    return { weight: "20", unit: "t" };
  }
  if (value < 1) {
    return {
      weight: String(Math.round(value * 1000)),
      unit: "kg",
    };
  }
  return {
    weight: String(value),
    unit: "t",
  };
}

function parseWeightInput(value: string, unit: WeightUnit): number | null {
  const normalized = value.trim().toLowerCase().replace(",", ".");
  if (!normalized) {
    return null;
  }

  const match = normalized.match(/\d+(?:\.\d+)?/);
  if (!match) {
    return null;
  }

  const numeric = Number.parseFloat(match[0]);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return null;
  }

  const explicitKg = normalized.includes("кг") || normalized.includes("kg");
  const tons = explicitKg || unit === "kg" ? numeric / 1000 : numeric;
  if (!Number.isFinite(tons) || tons <= 0 || tons > 1000) {
    return null;
  }

  return Number(tons.toFixed(3));
}

function parseTimeInput(value: string): string | null {
  const clean = value.trim();
  if (!clean) {
    return null;
  }
  if (/^\d{2}:\d{2}$/.test(clean)) {
    const [hh, mm] = clean.split(":").map((part) => Number.parseInt(part, 10));
    if (hh >= 0 && hh <= 23 && mm >= 0 && mm <= 59) {
      return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
    }
    return null;
  }

  const digits = clean.replace(/\D/g, "");
  if (digits.length === 3 || digits.length === 4) {
    const hh = Number.parseInt(digits.slice(0, digits.length - 2), 10);
    const mm = Number.parseInt(digits.slice(-2), 10);
    if (hh >= 0 && hh <= 23 && mm >= 0 && mm <= 59) {
      return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
    }
  }

  if (digits.length === 1 || digits.length === 2) {
    const hh = Number.parseInt(digits, 10);
    if (hh >= 0 && hh <= 23) {
      return `${String(hh).padStart(2, "0")}:00`;
    }
  }

  return null;
}

function normalizeTimeTyping(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, 4);
  if (digits.length <= 2) {
    return digits;
  }
  return `${digits.slice(0, digits.length - 2)}:${digits.slice(-2)}`;
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

function normalizeDecimalTyping(value: string): string {
  const lowered = value.toLowerCase().replace(",", ".");
  const cleaned = lowered.replace(/[^0-9.\s]/g, "");
  const parts = cleaned.trim().split(".");
  if (parts.length <= 1) {
    return cleaned.trim();
  }
  return `${parts.shift()}.${parts.join("")}`.trim();
}

function parseDecimalInput(value: string): number | null {
  const normalized = value.trim().replace(",", ".");
  if (!normalized) {
    return null;
  }
  const match = normalized.match(/\d+(?:\.\d+)?/);
  if (!match) {
    return null;
  }
  const numeric = Number.parseFloat(match[0]);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return null;
  }
  return Number(numeric.toFixed(3));
}

function scoreTone(score: number, verdict: string): string {
  if (verdict === "green" || score >= 75) {
    return "green";
  }
  if (verdict === "yellow" || score >= 45) {
    return "yellow";
  }
  return "red";
}

function scoreLabel(score: number, verdict: string): string {
  const tone = scoreTone(score, verdict);
  if (tone === "green") {
    return "Низкий риск";
  }
  if (tone === "yellow") {
    return "Нужно проверить";
  }
  return "Высокий риск";
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
  allowSmartPaste = true,
  initialValues,
  submitLabel,
}: AddCargoFormProps) {
  const originListId = useId();
  const destinationListId = useId();
  const [mode, setMode] = useState<"text" | "form">(allowSmartPaste ? "text" : "form");
  const [rawText, setRawText] = useState("");
  const [preview, setPreview] = useState<ManualCargoParsedPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const initialWeight = useMemo(() => initialWeightState(initialValues?.weight), [initialValues?.weight]);
  const [origin, setOrigin] = useState(initialValues?.origin ?? "");
  const [destination, setDestination] = useState(initialValues?.destination ?? "");
  const [bodyType, setBodyType] = useState(initialValues?.bodyType ?? "тент");
  const [weight, setWeight] = useState(initialWeight.weight);
  const [weightUnit, setWeightUnit] = useState<WeightUnit>(initialWeight.unit);
  const [volume, setVolume] = useState(initialValues?.volume != null ? String(initialValues.volume) : "");
  const [price, setPrice] = useState(String(initialValues?.price ?? 120000));
  const [loadDate, setLoadDate] = useState(parseDateInput(initialValues?.loadDate ?? defaultDate()) ?? defaultDate());
  const [loadTime, setLoadTime] = useState(initialValues?.loadTime ?? "");
  const [description, setDescription] = useState(initialValues?.description ?? "");
  const [paymentTerms, setPaymentTerms] = useState(initialValues?.paymentTerms ?? "");
  const [originSuggestions, setOriginSuggestions] = useState<CitySuggestion[]>([]);
  const [destinationSuggestions, setDestinationSuggestions] = useState<CitySuggestion[]>([]);
  const [recommendedRate, setRecommendedRate] = useState<RecommendedCargoRate | null>(null);
  const [recommendedRateError, setRecommendedRateError] = useState<string | null>(null);

  const canSubmit = useMemo(() => {
    if (mode === "text") {
      return rawText.trim().length >= 8 && preview !== null && !previewLoading;
    }
    const weightNumber = parseWeightInput(weight, weightUnit);
    const priceNumber = parseIntegerInput(price);
    return (
      origin.trim().length >= 2
      && destination.trim().length >= 2
      && weightNumber !== null
      && priceNumber !== null
      && parseDateInput(loadDate) !== null
    );
  }, [destination, loadDate, mode, origin, preview, previewLoading, price, rawText, weight, weightUnit]);

  useEffect(() => {
    if (!allowSmartPaste && mode !== "form") {
      setMode("form");
    }
  }, [allowSmartPaste, mode]);

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
    if (mode !== "text") {
      setPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
      return;
    }

    const text = rawText.trim();
    if (text.length < 8) {
      setPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setPreviewLoading(true);
      try {
        const parsed = await previewManualCargo(text);
        if (!cancelled) {
          setPreview(parsed);
          setPreviewError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setPreview(null);
          setPreviewError(err instanceof Error ? err.message : "Не удалось разобрать текст");
        }
      } finally {
        if (!cancelled) {
          setPreviewLoading(false);
        }
      }
    }, 350);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [mode, rawText]);

  useEffect(() => {
    if (mode !== "form") {
      setRecommendedRate(null);
      setRecommendedRateError(null);
      return;
    }

    const weightNumber = parseWeightInput(weight, weightUnit);
    if (
      origin.trim().length < 2
      || destination.trim().length < 2
      || weightNumber === null
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
  }, [bodyType, destination, mode, origin, weight, weightUnit]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    if (mode === "text") {
      await onSubmit({
        mode: "text",
        rawText: rawText.trim(),
      });
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

    const normalizedWeight = parseWeightInput(weight, weightUnit);
    if (!normalizedWeight) {
      return;
    }

    const normalizedLoadTime = parseTimeInput(loadTime);

    await onSubmit({
      mode: "form",
      origin: origin.trim(),
      destination: destination.trim(),
      bodyType: bodyType.trim(),
      weight: normalizedWeight,
      volume: parseDecimalInput(volume) ?? undefined,
      price: normalizedPrice,
      loadDate: normalizedLoadDate,
      loadTime: (normalizedLoadTime || loadTime.trim()) || undefined,
      description: description.trim() || undefined,
      paymentTerms: paymentTerms.trim() || undefined,
    });
  }

  return (
    <form className="cargo-form" onSubmit={handleSubmit}>
      {allowSmartPaste && (
        <div className="cargo-mode-toggle">
          <button
            type="button"
            className={`action-btn${mode === "text" ? " primary" : ""}`}
            disabled={busy}
            onClick={() => setMode("text")}
          >
            Текст (Smart)
          </button>
          <button
            type="button"
            className={`action-btn${mode === "form" ? " primary" : ""}`}
            disabled={busy}
            onClick={() => setMode("form")}
          >
            Форма
          </button>
        </div>
      )}

      {mode === "text" ? (
        <>
          <label className="truck-field cargo-description">
            <span>Вставь исходный текст заявки</span>
            <textarea
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              placeholder="Самара - Казань 20т 82м3 досок 145к завтра"
              disabled={busy}
              rows={6}
            />
          </label>

          <div className="cargo-form-note">
            Система сама вытащит маршрут, вес, кубатуру, характер груза и подберёт кузов.
          </div>

          {previewLoading && <div className="cargo-form-note">Разбираем текст и считаем риск…</div>}
          {previewError && <div className="error truck-form-error">{previewError}</div>}

          {preview && (
            <section className={`smart-preview-card ${scoreTone(preview.ai_score, preview.ai_verdict)}`}>
              <div className="smart-preview-head">
                <div>
                  <strong>{preview.from_city} → {preview.to_city}</strong>
                  <div className="smart-preview-meta">
                    {preview.body_type} • {preview.weight}т{preview.volume_m3 ? ` • ${preview.volume_m3}м³` : ""}
                  </div>
                </div>
                <div className={`smart-score-badge ${scoreTone(preview.ai_score, preview.ai_verdict)}`}>
                  AI-Score {preview.ai_score}/100
                </div>
              </div>
              <div className="smart-preview-grid">
                <div><span>Груз</span><strong>{preview.cargo_type}</strong></div>
                <div><span>Ставка</span><strong>{preview.price ? `${formatRub(preview.price)} ₽` : "—"}</strong></div>
                <div><span>Источник цены</span><strong>{preview.price_source === "estimated" ? "авторасчёт" : "из текста"}</strong></div>
                <div><span>Риск</span><strong>{scoreLabel(preview.ai_score, preview.ai_verdict)}</strong></div>
              </div>
              {(preview.load_date || preview.load_time) && (
                <div className="smart-preview-schedule">
                  Готовность: {preview.load_date ?? "сегодня"}{preview.load_time ? ` ${preview.load_time}` : ""}
                </div>
              )}
              <div className="smart-preview-comment">{preview.ai_comment}</div>
            </section>
          )}
        </>
      ) : (
        <div className="cargo-form-grid">
          <label className="truck-field">
            <span>Откуда</span>
            <input
              type="text"
              value={origin}
              onChange={(event) => setOrigin(event.target.value)}
              placeholder="Москва"
              list={originListId}
              disabled={busy}
              required
            />
            <datalist id={originListId}>
              {originSuggestions.map((item) => (
                <option
                  key={`origin-${item.full_name}`}
                  value={item.name}
                  label={item.full_name}
                />
              ))}
            </datalist>
          </label>

          <label className="truck-field">
            <span>Куда</span>
            <input
              type="text"
              value={destination}
              onChange={(event) => setDestination(event.target.value)}
              placeholder="Казань"
              list={destinationListId}
              disabled={busy}
              required
            />
            <datalist id={destinationListId}>
              {destinationSuggestions.map((item) => (
                <option
                  key={`destination-${item.full_name}`}
                  value={item.name}
                  label={item.full_name}
                />
              ))}
            </datalist>
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
            <span>Вес</span>
            <div className="inline-field-group">
              <input
                type="text"
                value={weight}
                onChange={(event) => {
                  const nextValue = event.target.value;
                  const typedUnit = detectWeightUnit(nextValue);
                  if (typedUnit) {
                    setWeightUnit(typedUnit);
                  }
                  setWeight(normalizeWeightTyping(nextValue));
                }}
                inputMode="decimal"
                placeholder={weightUnit === "kg" ? "200 кг" : "20 т"}
                disabled={busy}
                required
              />
              <select
                value={weightUnit}
                onChange={(event) => setWeightUnit(event.target.value as WeightUnit)}
                disabled={busy}
              >
                {WEIGHT_UNITS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
          </label>

          <label className="truck-field">
            <span>Объём, м³</span>
            <input
              type="text"
              value={volume}
              onChange={(event) => setVolume(normalizeDecimalTyping(event.target.value))}
              inputMode="decimal"
              placeholder="82"
              disabled={busy}
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
              type="text"
              value={loadTime}
              onChange={(event) => setLoadTime(normalizeTimeTyping(event.target.value))}
              inputMode="text"
              placeholder="9, 09:30 или 930"
              maxLength={5}
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
      )}

      {mode === "form" && (recommendedRate || recommendedRateError) && (
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
          {busy ? "⏳ Сохраняем" : (submitLabel ?? (mode === "text" ? "🧠 Добавить из текста" : "✅ Добавить груз"))}
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
