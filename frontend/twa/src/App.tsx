import { useEffect, useMemo, useState, useCallback } from "react";
import {
  archiveManualCargo,
  addFavorite,
  addVehicle,
  createSubscription,
  createManualCargo,
  deleteSubscription,
  fetchMyCargos,
  fetchSubscriptions,
  fetchFavorites,
  fetchFeed,
  fetchSimilar,
  fetchVehicles,
  setVehicleAvailable,
  trackClick,
  updateManualCargo,
  updateFavoriteStatus,
  type FavoriteItem,
  type FeedItem,
  type MyCargoItem,
  type SimilarItem,
  type SubscriptionItem,
  type VehicleItem,
} from "./api";

import { CargoMap } from "./CargoMap";
import { AddTruckForm } from "./AddTruckForm";
import { AddCargoForm } from "./AddCargoForm";
import { AddSubscriptionForm } from "./AddSubscriptionForm";

type Verdict = "green" | "yellow" | "red";
type Tab = "feed" | "map" | "dashboard" | "fleet" | "cargos" | "subscriptions";

const BODY_TYPES = ["тент", "рефрижератор", "трал", "борт", "контейнер", "изотерм"];

function trustStars(score: number | null): string {
  if (score == null) return "☆☆☆";
  if (score >= 80) return "★★★★★";
  if (score >= 60) return "★★★★☆";
  if (score >= 40) return "★★★☆☆";
  if (score >= 20) return "★★☆☆☆";
  return "★☆☆☆☆";
}

export function App() {
  const [tab, setTab] = useState<Tab>("feed");
  const [items, setItems] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [selected, setSelected] = useState<Verdict[]>(["green", "yellow"]);
  const [similarMap, setSimilarMap] = useState<Record<number, SimilarItem[]>>({});
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [selectedMapId, setSelectedMapId] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [favorites, setFavorites] = useState<FavoriteItem[]>([]);
  const [vehicles, setVehicles] = useState<VehicleItem[]>([]);
  const [matchResult, setMatchResult] = useState<any>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const [showAddTruck, setShowAddTruck] = useState(false);
  const [addingVehicle, setAddingVehicle] = useState(false);
  const [fleetError, setFleetError] = useState<string | null>(null);
  const [showAddCargo, setShowAddCargo] = useState(false);
  const [addingCargo, setAddingCargo] = useState(false);
  const [cargoError, setCargoError] = useState<string | null>(null);
  const [myCargos, setMyCargos] = useState<MyCargoItem[]>([]);
  const [myCargosLoading, setMyCargosLoading] = useState(false);
  const [myCargosError, setMyCargosError] = useState<string | null>(null);
  const [editingCargoId, setEditingCargoId] = useState<number | null>(null);
  const [subscriptions, setSubscriptions] = useState<SubscriptionItem[]>([]);
  const [subscriptionsLoading, setSubscriptionsLoading] = useState(false);
  const [subscriptionsError, setSubscriptionsError] = useState<string | null>(null);
  const [showAddSubscription, setShowAddSubscription] = useState(false);
  const [addingSubscription, setAddingSubscription] = useState(false);
  const [initData] = useState<string | null>(() => {
    const value = (window as any)?.Telegram?.WebApp?.initData || "";
    return typeof value === "string" && value.trim() ? value.trim() : null;
  });

  const parsedSearch = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    if (!q) return {};
    const tokens = q.split(/\s+/);
    let from_city: string | undefined;
    let to_city: string | undefined;
    let body_type: string | undefined;
    const cityTokens: string[] = [];

    for (const t of tokens) {
      const bt = BODY_TYPES.find((b) => b.startsWith(t) || t.startsWith(b.slice(0, 3)));
      if (bt) {
        body_type = bt;
      } else if (!t.match(/^\d/) && t.length >= 2) {
        cityTokens.push(t);
      }
    }
    if (cityTokens.length >= 2) {
      from_city = cityTokens[0];
      to_city = cityTokens[1];
    } else if (cityTokens.length === 1) {
      from_city = cityTokens[0];
    }
    return { from_city, to_city, body_type };
  }, [searchQuery]);

  const load = useCallback(async (reset: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchFeed({
        verdict: selected,
        cursor: reset ? null : cursor,
        limit: 20,
        initData,
        ...parsedSearch,
      });
      setItems((prev) => (reset ? data.items : [...prev, ...data.items]));
      setCursor(data.next_cursor);
      setHasMore(data.has_more);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [selected, cursor, initData, parsedSearch]);

  useEffect(() => { void load(true); }, [selected.join(","), initData, searchQuery]);

  useEffect(() => {
    if (tab === "dashboard") fetchFavorites().then(setFavorites);
    if (tab === "fleet") fetchVehicles().then(setVehicles);
    if (tab === "cargos") void loadMyCargos();
  }, [tab]);

  const loadSubscriptions = useCallback(async () => {
    setSubscriptionsLoading(true);
    setSubscriptionsError(null);
    try {
      setSubscriptions(await fetchSubscriptions());
    } catch (err) {
      setSubscriptionsError(err instanceof Error ? err.message : "Не удалось загрузить подписки");
    } finally {
      setSubscriptionsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSubscriptions();
  }, [loadSubscriptions]);

  const loadMyCargos = useCallback(async () => {
    setMyCargosLoading(true);
    setMyCargosError(null);
    try {
      setMyCargos(await fetchMyCargos());
    } catch (err) {
      setMyCargosError(err instanceof Error ? err.message : "Не удалось загрузить мои грузы");
    } finally {
      setMyCargosLoading(false);
    }
  }, []);

  function onCallClick(item: FeedItem) {
    void trackClick(item.id).catch(() => {});
    if (item.can_view_contact && item.phone) {
      window.location.href = `tel:${item.phone}`;
      return;
    }
    window.alert("Номер скрыт. Нужна подписка Premium.");
  }

  function onReplyClick(item: FeedItem) {
    if (!item.suggested_response) return;
    navigator.clipboard?.writeText(item.suggested_response);
    setCopied(item.id);
    setTimeout(() => setCopied(null), 2000);
  }

  async function reportItem(id: number) {
    if (!window.confirm("Пожаловаться на этот груз как мошенничество?")) return;
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (initData) headers.Authorization = `tma ${initData}`;
    try {
      await fetch(`/api/v1/feed/${id}/report`, {
        method: "POST", credentials: "include", headers,
        body: JSON.stringify({ reason: "scam" }),
      });
      setCopied(-id * 100);
      setTimeout(() => setCopied(null), 2000);
    } catch { /* ignore */ }
  }

  async function onSave(item: FeedItem) {
    await addFavorite(item.id);
    setCopied(-item.id);
    setTimeout(() => setCopied(null), 1500);
  }

  async function toggleSimilar(id: number) {
    if (expandedId === id) { setExpandedId(null); return; }
    setExpandedId(id);
    if (!similarMap[id]) {
      const similar = await fetchSimilar(id);
      setSimilarMap((prev) => ({ ...prev, [id]: similar }));
    }
  }

  async function moveFavorite(feedId: number, status: string) {
    await updateFavoriteStatus(feedId, status);
    setFavorites(await fetchFavorites());
  }

  async function handleAddTruck(payload: {
    bodyType: string;
    capacityTons: number;
    locationCity?: string;
    plateNumber?: string;
    markAvailable: boolean;
  }) {
    setAddingVehicle(true);
    setFleetError(null);

    try {
      const created = await addVehicle(
        payload.bodyType,
        payload.capacityTons,
        payload.locationCity,
        payload.plateNumber,
      );

      if (payload.markAvailable && payload.locationCity) {
        const result = await setVehicleAvailable(created.id, payload.locationCity);
        setMatchResult(result);
      }

      setVehicles(await fetchVehicles());
      setShowAddTruck(false);
    } catch (err) {
      setFleetError(err instanceof Error ? err.message : "Не удалось добавить машину");
    } finally {
      setAddingVehicle(false);
    }
  }

  async function handleAddCargo(payload: {
    origin: string;
    destination: string;
    bodyType: string;
    weight: number;
    price: number;
    loadDate: string;
    loadTime?: string;
    description?: string;
    paymentTerms?: string;
  }) {
    const returnToCargos = tab === "cargos";
    setAddingCargo(true);
    setCargoError(null);

    try {
      await createManualCargo({
        origin: payload.origin,
        destination: payload.destination,
        body_type: payload.bodyType,
        weight: payload.weight,
        price: payload.price,
        load_date: payload.loadDate,
        load_time: payload.loadTime ?? null,
        description: payload.description ?? null,
        payment_terms: payload.paymentTerms ?? null,
      });

      setShowAddCargo(false);
      if (returnToCargos) {
        await Promise.all([loadMyCargos(), load(true)]);
      } else {
        setTab("feed");
        await load(true);
      }

      const tg = (window as any)?.Telegram?.WebApp;
      if (tg?.showAlert) {
        tg.showAlert("Груз опубликован");
      } else {
        window.alert("Груз опубликован");
      }
    } catch (err) {
      setCargoError(err instanceof Error ? err.message : "Не удалось добавить груз");
    } finally {
      setAddingCargo(false);
    }
  }

  async function handleEditCargo(
    cargoId: number,
    payload: {
      origin: string;
      destination: string;
      bodyType: string;
      weight: number;
      price: number;
      loadDate: string;
      loadTime?: string;
      description?: string;
      paymentTerms?: string;
    },
  ) {
    setAddingCargo(true);
    setMyCargosError(null);

    try {
      await updateManualCargo(cargoId, {
        origin: payload.origin,
        destination: payload.destination,
        body_type: payload.bodyType,
        weight: payload.weight,
        price: payload.price,
        load_date: payload.loadDate,
        load_time: payload.loadTime ?? null,
        description: payload.description ?? null,
        payment_terms: payload.paymentTerms ?? null,
      });
      setEditingCargoId(null);
      await Promise.all([loadMyCargos(), load(true)]);
    } catch (err) {
      setMyCargosError(err instanceof Error ? err.message : "Не удалось обновить груз");
    } finally {
      setAddingCargo(false);
    }
  }

  async function handleArchiveCargo(cargoId: number) {
    if (!window.confirm("Снять этот груз с публикации?")) {
      return;
    }
    setMyCargosError(null);
    try {
      await archiveManualCargo(cargoId);
      setEditingCargoId((current) => (current === cargoId ? null : current));
      await Promise.all([loadMyCargos(), load(true)]);
    } catch (err) {
      setMyCargosError(err instanceof Error ? err.message : "Не удалось архивировать груз");
    }
  }

  function hasRouteSubscription(item: FeedItem): boolean {
    return subscriptions.some((sub) =>
      (sub.from_city ?? null) === (item.from_city ?? null)
      && (sub.to_city ?? null) === (item.to_city ?? null)
      && (sub.body_type ?? null) === (item.body_type ?? null)
    );
  }

  async function handleToggleSubscription(item: FeedItem) {
    if (!item.from_city && !item.to_city && !item.body_type) {
      return;
    }

    const existing = subscriptions.find((sub) =>
      (sub.from_city ?? null) === (item.from_city ?? null)
      && (sub.to_city ?? null) === (item.to_city ?? null)
      && (sub.body_type ?? null) === (item.body_type ?? null)
    );

    try {
      if (existing) {
        await deleteSubscription(existing.id);
        setSubscriptions((prev) => prev.filter((sub) => sub.id !== existing.id));
      } else {
        const created = await createSubscription({
          from_city: item.from_city,
          to_city: item.to_city,
          body_type: item.body_type,
        });
        setSubscriptions((prev) => [created, ...prev]);
      }
    } catch {
      window.alert("Не удалось обновить подписку");
    }
  }

  async function handleCreateSubscription(payload: {
    fromCity?: string;
    toCity?: string;
    bodyType?: string;
    minRate?: number;
    maxWeight?: number;
    region?: string;
  }) {
    setAddingSubscription(true);
    setSubscriptionsError(null);
    try {
      const created = await createSubscription({
        from_city: payload.fromCity ?? null,
        to_city: payload.toCity ?? null,
        body_type: payload.bodyType ?? null,
        min_rate: payload.minRate ?? null,
        max_weight: payload.maxWeight ?? null,
        region: payload.region ?? null,
      });
      setSubscriptions((prev) => {
        const withoutSame = prev.filter((sub) => sub.id !== created.id);
        return [created, ...withoutSame];
      });
      setShowAddSubscription(false);
    } catch (err) {
      setSubscriptionsError(err instanceof Error ? err.message : "Не удалось сохранить подписку");
    } finally {
      setAddingSubscription(false);
    }
  }

  async function handleDeleteSubscription(subscriptionId: number) {
    try {
      await deleteSubscription(subscriptionId);
      setSubscriptions((prev) => prev.filter((sub) => sub.id !== subscriptionId));
    } catch {
      setSubscriptionsError("Не удалось удалить подписку");
    }
  }

  function renderCard(item: FeedItem) {
    const isHot = item.is_hot_deal;
    return (
      <article className={`cargo-card${isHot ? " hot" : ""}`} key={item.id}>
        <div className="card-top">
          <div className="card-route">
            {isHot && <span className="hot-badge">🔥</span>}
            <span className="route-text">{item.from_city ?? "?"} → {item.to_city ?? "?"}</span>
            {item.distance_km != null && <span className="distance">{item.distance_km} км</span>}
          </div>
          <div className="card-freshness">{item.freshness}</div>
        </div>

        <div className="card-body">
          <div className="card-col specs">
            <div className="spec-row">
              <span className="spec-label">Кузов</span>
              <span className="spec-value">{item.body_type ?? "—"}</span>
            </div>
            <div className="spec-row">
              <span className="spec-label">Вес</span>
              <span className="spec-value">{item.weight_t ?? 0} т</span>
            </div>
            {item.dimensions && (
              <div className="spec-row">
                <span className="spec-label">Габариты</span>
                <span className="spec-value">{item.dimensions}</span>
              </div>
            )}
            {item.cargo_description && (
              <div className="spec-row">
                <span className="spec-label">Груз</span>
                <span className="spec-value">{item.cargo_description}</span>
              </div>
            )}
          </div>

          <div className="card-col price-col">
            <div className="price-main">{(item.rate_rub ?? 0).toLocaleString("ru")} ₽</div>
            {item.rate_per_km != null && (
              <div className="price-per-km">{item.rate_per_km} ₽/км</div>
            )}
            {item.payment_terms && <div className="payment-terms">{item.payment_terms}</div>}
            {item.is_direct_customer !== null && (
              <div className="customer-type">
                {item.is_direct_customer ? "🏭 Прямой" : "👤 Посредник"}
              </div>
            )}
          </div>
        </div>

        {item.phone_blacklisted && (
          <div className="blacklist-warning">⚠️ Телефон в чёрном списке</div>
        )}

        <div className="card-footer">
          <div className="trust-row">
            <span className="trust-stars">{trustStars(item.trust_score)}</span>
            <span className={`trust-badge ${item.trust_verdict ?? "none"}`}>
              {item.trust_score ?? "?"}/100
            </span>
            {item.load_date && <span className="load-date">📅 {item.load_date}{item.load_time ? ` ${item.load_time}` : ""}</span>}
          </div>

          <div className="card-actions">
            <button className="action-btn primary" onClick={() => onCallClick(item)}>
              {item.can_view_contact && item.phone ? `📞 ${item.phone}` : "📞 Premium"}
            </button>
            {item.suggested_response && (
              <button className="action-btn reply" onClick={() => onReplyClick(item)}>
                {copied === item.id ? "✓ Скопировано" : "✉️ Отклик"}
              </button>
            )}
            <button className="action-btn fav" onClick={() => void onSave(item)}>
              {copied === -item.id ? "✓" : "⭐"}
            </button>
            <button className="action-btn" onClick={() => void handleToggleSubscription(item)}>
              {hasRouteSubscription(item) ? "🔕" : "🔔"}
            </button>
            {item.ati_link && (
              <a href={item.ati_link} target="_blank" rel="noopener noreferrer" className="action-btn ati">АТИ</a>
            )}
            <button className="action-btn similar" onClick={() => void toggleSimilar(item.id)}>
              {expandedId === item.id ? "▲" : "📦"}
            </button>
            <button className="action-btn report" onClick={() => void reportItem(item.id)}>
              🚩
            </button>
          </div>
        </div>

        {expandedId === item.id && similarMap[item.id] && (
          <div className="similar-section">
            {similarMap[item.id].length === 0 ? (
              <p className="muted">Похожих не найдено</p>
            ) : (
              similarMap[item.id].map((s) => (
                <div className="similar-row" key={s.id}>
                  <span>{s.from_city} → {s.to_city}</span>
                  <span>{s.body_type ?? "?"} • {s.weight_t ?? 0}т • {(s.rate_rub ?? 0).toLocaleString("ru")}₽</span>
                  {s.is_hot_deal && <span>🔥</span>}
                </div>
              ))
            )}
          </div>
        )}
      </article>
    );
  }

  function renderDashboard() {
    const saved = favorites.filter((f) => f.status === "saved");
    const inProgress = favorites.filter((f) => f.status === "in_progress");
    const completed = favorites.filter((f) => f.status === "completed" || f.status === "cancelled");

    function kanbanCard(fav: FavoriteItem) {
      return (
        <div className="kanban-card" key={fav.id}>
          <div className="kanban-route">
            {fav.is_hot_deal && "🔥 "}
            {fav.from_city ?? "?"} → {fav.to_city ?? "?"}
          </div>
          <div className="kanban-details">
            {fav.body_type ?? "?"} • {(fav.rate_rub ?? 0).toLocaleString("ru")} ₽
          </div>
          {fav.phone && <div className="kanban-phone">📞 {fav.phone}</div>}
          {fav.note && <div className="kanban-note">💬 {fav.note}</div>}
          <div className="kanban-actions">
            {fav.status === "saved" && (
              <button className="kb-btn" onClick={() => void moveFavorite(fav.feed_id, "in_progress")}>🚛 В работу</button>
            )}
            {fav.status === "in_progress" && (
              <>
                <button className="kb-btn" onClick={() => void moveFavorite(fav.feed_id, "completed")}>✅ Завершить</button>
                <button className="kb-btn cancel" onClick={() => void moveFavorite(fav.feed_id, "cancelled")}>✗</button>
              </>
            )}
          </div>
        </div>
      );
    }

    return (
      <div className="dashboard">
        <div className="kanban">
          <div className="kanban-col">
            <div className="kanban-header saved">📌 Выбрано <span className="count">{saved.length}</span></div>
            {saved.map(kanbanCard)}
          </div>
          <div className="kanban-col">
            <div className="kanban-header progress">🚛 В работе <span className="count">{inProgress.length}</span></div>
            {inProgress.map(kanbanCard)}
          </div>
          <div className="kanban-col">
            <div className="kanban-header done">✅ Завершено <span className="count">{completed.length}</span></div>
            {completed.map(kanbanCard)}
          </div>
        </div>
      </div>
    );
  }

  function renderMyCargos() {
    return (
      <div className="my-cargos-section">
        <div className="fleet-header">
          <h2>📦 Мои грузы</h2>
          <button
            className="action-btn primary"
            onClick={() => {
              setCargoError(null);
              setMyCargosError(null);
              setEditingCargoId(null);
              setShowAddCargo((prev) => !prev);
            }}
          >
            {showAddCargo ? "Скрыть форму" : "+ Добавить груз"}
          </button>
        </div>

        {showAddCargo && (
          <AddCargoForm
            onSubmit={handleAddCargo}
            onCancel={() => {
              setCargoError(null);
              setShowAddCargo(false);
            }}
            busy={addingCargo}
            error={cargoError}
          />
        )}

        {myCargosError && <div className="error">{myCargosError}</div>}

        {myCargosLoading ? (
          <p className="muted" style={{ textAlign: "center", padding: "20px" }}>Загружаем…</p>
        ) : myCargos.length === 0 ? (
          <p className="muted" style={{ textAlign: "center", padding: "20px" }}>
            У вас пока нет опубликованных грузов
          </p>
        ) : (
          <div className="my-cargo-list">
            {myCargos.map((cargo) => {
              const editing = editingCargoId === cargo.id;
              return (
                <div className={`my-cargo-card${cargo.is_published ? " published" : ""}`} key={cargo.id}>
                  <div className="my-cargo-head">
                    <div>
                      <div className="my-cargo-route">{cargo.from_city} → {cargo.to_city}</div>
                      <div className="my-cargo-meta">
                        {cargo.body_type} • {cargo.weight}т • {cargo.price.toLocaleString("ru")} ₽
                      </div>
                    </div>
                    <div className="my-cargo-statuses">
                      <span className={`status-badge ${cargo.is_published ? "free" : ""}`}>
                        {cargo.is_published ? "🟢 В ленте" : "⚪️ Не в ленте"}
                      </span>
                      <span className="muted">{cargo.status}</span>
                    </div>
                  </div>

                  <div className="my-cargo-extra">
                    <span>📅 {cargo.load_date}{cargo.load_time ? ` ${cargo.load_time}` : ""}</span>
                    {cargo.payment_terms && <span>{cargo.payment_terms}</span>}
                    {cargo.description && <span>{cargo.description}</span>}
                  </div>

                  <div className="card-actions">
                    <button
                      className="action-btn"
                      onClick={() => {
                        setShowAddCargo(false);
                        setMyCargosError(null);
                        setEditingCargoId((current) => (current === cargo.id ? null : cargo.id));
                      }}
                    >
                      {editing ? "Скрыть" : "✏️ Редактировать"}
                    </button>
                    <button
                      className="action-btn report"
                      onClick={() => void handleArchiveCargo(cargo.id)}
                    >
                      🗄️ Архив
                    </button>
                  </div>

                  {editing && (
                    <AddCargoForm
                      onSubmit={(payload) => handleEditCargo(cargo.id, payload)}
                      onCancel={() => {
                        setMyCargosError(null);
                        setEditingCargoId(null);
                      }}
                      busy={addingCargo}
                      error={myCargosError}
                      submitLabel="💾 Сохранить"
                      initialValues={{
                        origin: cargo.from_city,
                        destination: cargo.to_city,
                        bodyType: cargo.body_type,
                        weight: cargo.weight,
                        price: cargo.price,
                        loadDate: cargo.load_date,
                        loadTime: cargo.load_time,
                        description: cargo.description,
                        paymentTerms: cargo.payment_terms,
                      }}
                    />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  function renderSubscriptions() {
    return (
      <div className="subscriptions-section">
        <div className="fleet-header">
          <h2>🔔 Мои подписки</h2>
          <button
            className="action-btn primary"
            onClick={() => {
              setSubscriptionsError(null);
              setShowAddSubscription((prev) => !prev);
            }}
          >
            {showAddSubscription ? "Скрыть форму" : "+ Подписка"}
          </button>
        </div>

        {showAddSubscription && (
          <AddSubscriptionForm
            onSubmit={handleCreateSubscription}
            onCancel={() => {
              setSubscriptionsError(null);
              setShowAddSubscription(false);
            }}
            busy={addingSubscription}
            error={subscriptionsError}
          />
        )}

        {subscriptionsError && !showAddSubscription && <div className="error">{subscriptionsError}</div>}

        {subscriptionsLoading ? (
          <p className="muted" style={{ textAlign: "center", padding: "20px" }}>Загружаем…</p>
        ) : subscriptions.length === 0 ? (
          <p className="muted" style={{ textAlign: "center", padding: "20px" }}>
            Подписок пока нет. Нажми 🔔 на грузе или создай фильтр вручную.
          </p>
        ) : (
          <div className="subscription-list">
            {subscriptions.map((sub) => (
              <div className="subscription-card" key={sub.id}>
                <div className="subscription-main">
                  <div className="subscription-route">
                    {(sub.from_city || "Любой")} → {(sub.to_city || "Любой")}
                  </div>
                  <div className="subscription-meta">
                    {sub.body_type || "Любой кузов"}
                    {sub.min_rate != null ? ` • от ${sub.min_rate.toLocaleString("ru")} ₽` : ""}
                    {sub.max_weight != null ? ` • до ${sub.max_weight}т` : ""}
                    {sub.region ? ` • ${sub.region}` : ""}
                  </div>
                </div>
                <div className="subscription-count">
                  <span className="count">{sub.match_count}</span>
                  <span className="muted">за 24ч</span>
                </div>
                <button
                  className="action-btn report"
                  onClick={() => void handleDeleteSubscription(sub.id)}
                >
                  ✖
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <main className="layout">
      <header className="topbar">
        <div className="logo-row">
          <h1>🚛 ГрузПоток</h1>
          <div className="tab-switch">
            <button className={`tab-btn${tab === "feed" ? " active" : ""}`} onClick={() => setTab("feed")}>📋</button>
            <button className={`tab-btn${tab === "map" ? " active" : ""}`} onClick={() => setTab("map")}>🗺️</button>
            <button className={`tab-btn${tab === "dashboard" ? " active" : ""}`} onClick={() => setTab("dashboard")}>⭐</button>
            <button className={`tab-btn${tab === "fleet" ? " active" : ""}`} onClick={() => setTab("fleet")}>🚛</button>
            <button className={`tab-btn${tab === "cargos" ? " active" : ""}`} onClick={() => setTab("cargos")}>📦</button>
            <button className={`tab-btn${tab === "subscriptions" ? " active" : ""}`} onClick={() => setTab("subscriptions")}>🔔</button>
          </div>
        </div>

        {(tab === "feed" || tab === "map") && (
          <>
            <div className="smart-search">
              <input
                type="text"
                placeholder="тент 20т мск казань..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="search-input"
              />
              <span className="search-count">{items.length}</span>
            </div>
            <div className="verdict-pills">
              {(["green", "yellow", "red"] as Verdict[]).map((v) => (
                <button
                  key={v}
                  className={`pill ${v}${selected.includes(v) ? " active" : ""}`}
                  onClick={() => setSelected((prev) => {
                    const next = prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v];
                    return next.length ? next : ["green", "yellow"];
                  })}
                >
                  {v === "green" ? "✅" : v === "yellow" ? "⚠️" : "🔴"}
                </button>
              ))}
            </div>
            <button
              className="action-btn primary topbar-action"
              onClick={() => {
                setCargoError(null);
                setShowAddCargo((prev) => !prev);
              }}
            >
              {showAddCargo ? "Скрыть груз" : "+ Груз"}
            </button>
          </>
        )}
      </header>

      {error && <div className="error">{error}</div>}
      {showAddCargo && (tab === "feed" || tab === "map") && (
        <AddCargoForm
          onSubmit={handleAddCargo}
          onCancel={() => {
            setCargoError(null);
            setShowAddCargo(false);
          }}
          busy={addingCargo}
          error={cargoError}
        />
      )}

      {tab === "feed" && (
        <>
          <section className="feed">{items.map(renderCard)}</section>
          <section className="load-more">
            <button disabled={loading} onClick={() => void load(true)}>
              {loading ? "⏳" : "🔄 Обновить"}
            </button>
            <button disabled={!hasMore || loading} onClick={() => void load(false)}>
              {hasMore ? "▼ Ещё" : "Конец"}
            </button>
          </section>
        </>
      )}

      {tab === "map" && (
        <div className="split-view">
          <div className="split-map">
            <CargoMap items={items} onSelect={setSelectedMapId} selectedId={selectedMapId} />
          </div>
          <div className="split-list">
            {items.slice(0, 10).map((item) => (
              <div
                key={item.id}
                className={`split-card${selectedMapId === item.id ? " selected" : ""}${item.is_hot_deal ? " hot" : ""}`}
                onClick={() => setSelectedMapId(item.id)}
              >
                <div className="split-route">
                  {item.is_hot_deal && "🔥 "}
                  {item.from_city} → {item.to_city}
                  {item.distance_km != null && <span className="split-dist">{item.distance_km}км</span>}
                </div>
                <div className="split-meta">
                  {item.body_type ?? "?"} • {item.weight_t ?? 0}т •{" "}
                  <span className="split-price">{(item.rate_rub ?? 0).toLocaleString("ru")}₽</span>
                  {item.rate_per_km != null && <span className="split-rpk">{item.rate_per_km}₽/км</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "dashboard" && renderDashboard()}

      {tab === "fleet" && (
        <div className="fleet-section">
          <div className="fleet-header">
            <h2>🚛 Мой флот</h2>
            <button
              className="action-btn primary"
              onClick={() => {
                setFleetError(null);
                setShowAddTruck((prev) => !prev);
              }}
            >
              {showAddTruck ? "Скрыть форму" : "+ Добавить машину"}
            </button>
          </div>

          {showAddTruck && (
            <AddTruckForm
              onSubmit={handleAddTruck}
              onCancel={() => {
                setFleetError(null);
                setShowAddTruck(false);
              }}
              busy={addingVehicle}
              error={fleetError}
            />
          )}

          {vehicles.length === 0 ? (
            <p className="muted" style={{textAlign: "center", padding: "20px"}}>
              Добавьте машину, чтобы получать подбор грузов
            </p>
          ) : (
            <div className="vehicle-list">
              {vehicles.map((v) => (
                <div className={`vehicle-card${v.is_available ? " available" : ""}`} key={v.id}>
                  <div className="vehicle-info">
                    <strong>{v.body_type} • {v.capacity_tons}т</strong>
                    {v.plate_number && <span className="plate">{v.plate_number}</span>}
                    {v.location_city && <span className="muted">📍 {v.location_city}</span>}
                    {v.sts_verified && <span className="verified">✅ СТС</span>}
                  </div>
                  <div className="vehicle-status">
                    {v.is_available ? (
                      <span className="status-badge free">🟢 Свободен</span>
                    ) : (
                      <button className="action-btn primary" onClick={async () => {
                        const city = window.prompt("Город, где свободна машина:", v.location_city || "");
                        if (!city) return;
                        const result = await setVehicleAvailable(v.id, city);
                        setMatchResult(result);
                        setVehicles(await fetchVehicles());
                      }}>Я свободен!</button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {matchResult && matchResult.matched && matchResult.matched.length > 0 && (
            <div className="match-results">
              <h3>📦 Подходящие грузы в {matchResult.location_city}</h3>
              {matchResult.matched.map((m: any) => (
                <div className="match-card" key={m.id}>
                  <div className="match-route">
                    {m.is_hot_deal && "🔥 "}
                    <strong>{m.from_city} → {m.to_city}</strong>
                  </div>
                  <div className="match-details">
                    {m.body_type ?? "?"} • {m.weight_t ?? 0}т •{" "}
                    <span style={{color: "var(--green)", fontWeight: 700}}>
                      {(m.rate_rub ?? 0).toLocaleString("ru")}₽
                    </span>
                    {m.rate_per_km && ` (${m.rate_per_km} ₽/км)`}
                  </div>
                  {m.load_date && <div className="muted">📅 {m.load_date} • ⏱ {m.freshness}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "cargos" && renderMyCargos()}
      {tab === "subscriptions" && renderSubscriptions()}
    </main>
  );
}
