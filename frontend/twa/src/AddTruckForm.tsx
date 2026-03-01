import { useEffect, useId, useMemo, useState } from "react";

import { searchCities, type CitySuggestion } from "./api";

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
  const locationListId = useId();
  const [bodyType, setBodyType] = useState("тент");
  const [capacityTons, setCapacityTons] = useState("20");
  const [locationCity, setLocationCity] = useState("");
  const [plateNumber, setPlateNumber] = useState("");
  const [markAvailable, setMarkAvailable] = useState(true);
  const [locationSuggestions, setLocationSuggestions] = useState<CitySuggestion[]>([]);

  const canSubmit = useMemo(() => {
    const parsedTons = Number.parseFloat(capacityTons);
    if (!Number.isFinite(parsedTons) || parsedTons <= 0) {
      return false;
    }
    if (markAvailable && locationCity.trim().length < 2) {
      return false;
    }
    return true;
  }, [capacityTons, locationCity, markAvailable]);

  useEffect(() => {
    if (locationCity.trim().length < 2) {
      setLocationSuggestions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const items = await searchCities(locationCity, 5);
        if (!cancelled) {
          setLocationSuggestions(items);
        }
      } catch {
        if (!cancelled) {
          setLocationSuggestions([]);
        }
      }
    }, 220);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [locationCity]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    const parsedTons = Number.parseFloat(capacityTons);

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
            list={locationListId}
            disabled={busy}
            required={markAvailable}
          />
          <datalist id={locationListId}>
            {locationSuggestions.map((item) => (
              <option
                key={`vehicle-location-${item.full_name}`}
                value={item.name}
                label={item.full_name}
              />
            ))}
          </datalist>
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
        <button type="submit" className="action-btn primary" disabled={busy || !canSubmit}>
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
      {!canSubmit && markAvailable && (
        <div className="muted">Чтобы сразу вывести машину на линию, укажи город базирования.</div>
      )}
    </form>
  );
}
