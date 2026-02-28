import { useEffect, useMemo, useState, useCallback, type FormEvent } from "react";
import {
  archiveManualCargo,
  addFavorite,
  addVehicle,
  createEscrow,
  createSubscription,
  createManualCargo,
  deleteSubscription,
  fetchFavorites,
  fetchMyCargos,
  fetchSubscriptions,
  fetchWebappProfile,
  markEscrowDelivered,
  disputeEscrow,
  requestEscrowRefund,
  releaseEscrow,
  fetchFeed,
  fetchSimilar,
  fetchVehicles,
  fetchVehicleMatches,
  fetchCargoMatches,
  fetchMatchSummary,
  setVehicleAvailable,
  trackClick,
  updateManualCargo,
  updateFavoriteStatus,
  type FavoriteItem,
  type CargoMatchResponse,
  type MatchSummary,
  type FeedItem,
  type MyCargoItem,
  type SimilarItem,
  type SubscriptionItem,
  type VehicleItem,
  type VehicleMatchResponse,
  type WebappProfileResponse,
} from "./api";

import { CargoMap } from "./CargoMap";
import { AddTruckForm } from "./AddTruckForm";
import { AddCargoForm } from "./AddCargoForm";
import { AddSubscriptionForm } from "./AddSubscriptionForm";

type Verdict = "green" | "yellow" | "red";
type Tab = "feed" | "map" | "dashboard" | "wallet" | "fleet" | "cargos" | "subscriptions";
type EscrowIssueKind = "dispute" | "refund";
type EscrowIssueSource = "cargos" | "wallet";
type EscrowIssueDraft = {
  kind: EscrowIssueKind;
  cargoId: number;
  source: EscrowIssueSource;
} | null;
type ActionGuideTone = "success" | "warning" | "info";
type ActionGuide = {
  title: string;
  steps: string[];
  tone: ActionGuideTone;
} | null;

const BODY_TYPES = ["тент", "рефрижератор", "трал", "борт", "контейнер", "изотерм"];

function trustStars(score: number | null): string {
  if (score == null) return "☆☆☆";
  if (score >= 80) return "★★★★★";
  if (score >= 60) return "★★★★☆";
  if (score >= 40) return "★★★☆☆";
  if (score >= 20) return "★★☆☆☆";
  return "★☆☆☆☆";
}

function paymentStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case "payment_pending":
      return "🟡 Ожидает оплату";
    case "funded":
      return "🛡️ Честный рейс";
    case "delivery_marked":
      return "🚚 Разгрузка отмечена";
    case "released":
      return "✅ Выплачено";
    case "disputed":
      return "⚠️ Спор";
    case "cancelled":
      return "⛔️ Отменено";
    default:
      return "⚪️ Без Честного рейса";
  }
}

function paymentStatusHint(status: string | null | undefined): string {
  switch (status) {
    case "payment_pending":
      return "Ссылка на оплату уже создана. После оплаты груз получит статус Честный рейс.";
    case "funded":
      return "Средства уже зарезервированы. Выплата пройдет после подтверждения разгрузки.";
    case "delivery_marked":
      return "Разгрузка отмечена. Осталось подтвердить разблокировку оплаты.";
    case "released":
      return "Сделка закрыта. Выплата перевозчику уже проведена.";
    case "disputed":
      return "По сделке открыт спор. Средства остаются под защитой до решения.";
    default:
      return "Честный рейс не включен. Чтобы защитить оплату, включите резервирование средств.";
  }
}

function disputeStatusHint(status: string | null | undefined): string {
  switch (status) {
    case "cancelled":
      return "Сделка отменена, возврат уже отражен в журнале.";
    case "disputed":
      return "Идет разбор спорной ситуации. Средства остаются под защитой.";
    default:
      return "Событие зафиксировано в журнале безопасных сделок.";
  }
}

function cargoStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case "new":
    case "active":
      return "🆕 Новый";
    case "in_progress":
      return "🚛 В работе";
    case "completed":
      return "✅ Завершён";
    case "archived":
      return "🗄️ Архив";
    case "cancelled":
      return "❌ Отменён";
    default:
      return status || "—";
  }
}

async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    return false;
  }
  return false;
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
  const [vehicleMatchMap, setVehicleMatchMap] = useState<Record<number, VehicleMatchResponse>>({});
  const [cargoMatchMap, setCargoMatchMap] = useState<Record<number, CargoMatchResponse>>({});
  const [matchSummary, setMatchSummary] = useState<MatchSummary | null>(null);
  const [matchSummaryError, setMatchSummaryError] = useState<string | null>(null);
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
  const [expandedCalcId, setExpandedCalcId] = useState<number | null>(null);
  const [tripCalc, setTripCalc] = useState({
    fuelLPer100: 35,
    fuelRubPerL: 60,
    taxPercent: 6,
    extraRub: 0,
    fallbackDistanceKm: 500,
  });
  const [profileSummary, setProfileSummary] = useState<WebappProfileResponse | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [issueDraft, setIssueDraft] = useState<EscrowIssueDraft>(null);
  const [issueReason, setIssueReason] = useState("");
  const [issueNote, setIssueNote] = useState("");
  const [issueSubmitting, setIssueSubmitting] = useState(false);
  const [actionGuide, setActionGuide] = useState<ActionGuide>(null);
  const [initData] = useState<string | null>(() => {
    const value = (window as any)?.Telegram?.WebApp?.initData || "";
    return typeof value === "string" && value.trim() ? value.trim() : null;
  });
  const canUseTelegramOnlyActions = Boolean(initData);

  function showActionGuide(title: string, steps: string[], tone: ActionGuideTone = "info") {
    setActionGuide({ title, steps, tone });
  }

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

  const loadProfileSummary = useCallback(async () => {
    setProfileLoading(true);
    setProfileError(null);
    try {
      setProfileSummary(await fetchWebappProfile());
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось загрузить кабинет";
      if (
        /401/.test(message)
        || /Missing Authorization/i.test(message)
        || /Invalid Telegram initData/i.test(message)
      ) {
        setProfileError("Не удалось подтвердить Telegram-сессию. Обновите доступ или откройте Mini App заново из бота.");
      } else {
        setProfileError(message);
      }
    } finally {
      setProfileLoading(false);
    }
  }, []);

  const loadMatchSummary = useCallback(async () => {
    setMatchSummaryError(null);
    try {
      setMatchSummary(await fetchMatchSummary());
    } catch (err) {
      setMatchSummaryError(err instanceof Error ? err.message : "Не удалось загрузить совпадения");
    }
  }, []);

  useEffect(() => {
    if (tab === "dashboard" || tab === "wallet") {
      void fetchFavorites().then(setFavorites).catch(() => setFavorites([]));
      void loadProfileSummary();
      void loadMatchSummary();
    }
    if (tab === "fleet") {
      void fetchVehicles().then(setVehicles).catch(() => setVehicles([]));
      void loadMatchSummary();
    }
    if (tab === "cargos") {
      void loadMyCargos();
      void loadMatchSummary();
    }
  }, [tab, loadMyCargos, loadProfileSummary, loadMatchSummary]);

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

  async function handleInviteColleague() {
    const link = profileSummary?.referral.link;
    const tg = (window as any)?.Telegram?.WebApp;
    if (!link) {
      const text = "Реферальная ссылка пока недоступна";
      if (tg?.showAlert) tg.showAlert(text);
      else window.alert(text);
      return;
    }

    const copiedOk = await copyText(link);
    const text = copiedOk ? "Ссылка приглашения скопирована" : `Скопируй ссылку вручную:\n${link}`;
    if (tg?.showAlert) tg.showAlert(text);
    else window.alert(text);
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
        setVehicleMatchMap((prev) => ({ ...prev, [created.id]: result }));
      }

      setVehicles(await fetchVehicles());
      setShowAddTruck(false);
      showActionGuide(
        "🚛 Машина добавлена",
        [
          payload.markAvailable && payload.locationCity
            ? "Проверь блок “🎯 Совпадения” — подбор уже выполнен."
            : "Если машина готова к рейсу, отметь её как свободную.",
          "Открой вкладку “🚛” и проверь подходящие грузы.",
          "Если маршрутов мало, включи подписки на нужные направления.",
        ],
        "success",
      );
    } catch (err) {
      setFleetError(err instanceof Error ? err.message : "Не удалось добавить машину");
    } finally {
      setAddingVehicle(false);
    }
  }

  async function showVehicleMatches(vehicleId: number) {
    try {
      const result = await fetchVehicleMatches(vehicleId);
      setVehicleMatchMap((prev) => ({ ...prev, [vehicleId]: result }));
      setMatchResult(result);
    } catch (err) {
      setFleetError(err instanceof Error ? err.message : "Не удалось загрузить совпадения");
    }
  }

  async function showCargoMatches(cargoId: number) {
    try {
      const result = await fetchCargoMatches(cargoId);
      setCargoMatchMap((prev) => ({ ...prev, [cargoId]: result }));
    } catch (err) {
      setMyCargosError(err instanceof Error ? err.message : "Не удалось загрузить совпадения");
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
    if (!canUseTelegramOnlyActions) {
      setCargoError("Публикация груза доступна только из Telegram Mini App");
      return;
    }
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

      showActionGuide(
        "📦 Груз создан",
        [
          "Проверь карточку в ленте и убедись, что маршрут и ставка выглядят корректно.",
          "При необходимости включи “🛡️ Честный рейс”, чтобы защитить оплату.",
          "Дальше жди отклики или открой “🎯 Совпадения” для подходящей техники.",
        ],
        "success",
      );
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

  async function handleCreateEscrowById(cargoId: number, amountRub: number, source: "cargos" | "wallet" = "cargos") {
    if (source === "wallet") {
      setProfileError(null);
    } else {
      setMyCargosError(null);
    }
    try {
      const result = await createEscrow(cargoId, amountRub);
      if (result.payment_url) {
        const tg = (window as any)?.Telegram?.WebApp;
        if (tg?.openLink) {
          tg.openLink(result.payment_url);
        } else {
          window.open(result.payment_url, "_blank", "noopener,noreferrer");
        }
      }
      await Promise.all([loadMyCargos(), load(true), loadProfileSummary()]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось создать безопасную сделку";
      if (source === "wallet") {
        setProfileError(message);
      } else {
        setMyCargosError(message);
      }
    }
  }

  async function handleCreateEscrow(cargo: MyCargoItem) {
    await handleCreateEscrowById(cargo.id, cargo.price, "cargos");
  }

  async function handleMarkEscrowDelivered(cargoId: number) {
    setMyCargosError(null);
    setProfileError(null);
    try {
      await markEscrowDelivered(cargoId);
      await Promise.all([loadMyCargos(), load(true), loadProfileSummary()]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось отметить доставку";
      setMyCargosError(message);
      setProfileError(message);
    }
  }

  async function handleReleaseEscrow(cargoId: number) {
    if (!window.confirm("Подтвердить выплату перевозчику?")) {
      return;
    }
    setMyCargosError(null);
    setProfileError(null);
    try {
      await releaseEscrow(cargoId);
      await Promise.all([loadMyCargos(), load(true), loadProfileSummary()]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось выполнить выплату";
      setMyCargosError(message);
      setProfileError(message);
    }
  }

  function openEscrowIssueModal(kind: EscrowIssueKind, cargoId: number, source: EscrowIssueSource) {
    setIssueDraft({ kind, cargoId, source });
    setIssueReason(kind === "refund" ? "Нужно отменить сделку" : "Проблема по условиям рейса");
    setIssueNote("");
  }

  function closeEscrowIssueModal() {
    if (issueSubmitting) {
      return;
    }
    setIssueDraft(null);
    setIssueReason("");
    setIssueNote("");
  }

  function handleDisputeEscrow(cargoId: number, source: EscrowIssueSource = "cargos") {
    openEscrowIssueModal("dispute", cargoId, source);
  }

  function handleRequestRefund(cargoId: number, source: EscrowIssueSource = "cargos") {
    openEscrowIssueModal("refund", cargoId, source);
  }

  async function submitEscrowIssue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!issueDraft) {
      return;
    }

    const reason = issueReason.trim();
    const note = issueNote.trim();
    if (!reason) {
      const message = issueDraft.kind === "refund" ? "Укажи причину возврата" : "Укажи причину спора";
      if (issueDraft.source === "wallet") {
        setProfileError(message);
      } else {
        setMyCargosError(message);
      }
      return;
    }

    if (issueDraft.source === "wallet") {
      setProfileError(null);
    } else {
      setMyCargosError(null);
    }

    setIssueSubmitting(true);
    try {
      if (issueDraft.kind === "refund") {
        await requestEscrowRefund(issueDraft.cargoId, reason, note);
      } else {
        await disputeEscrow(issueDraft.cargoId, reason, note);
      }
      await Promise.all([loadMyCargos(), load(true), loadProfileSummary()]);
      setIssueDraft(null);
      setIssueReason("");
      setIssueNote("");
      showActionGuide(
        issueDraft.kind === "refund" ? "↩️ Запрос на возврат отправлен" : "⚠️ Спор открыт",
        issueDraft.kind === "refund"
          ? [
              "Сделка переведена в разбор, средства остаются под защитой.",
              "Следи за обновлениями в “💼 Кошелек” и разделе Честного рейса.",
              "Не подтверждай выплату до решения по возврату.",
            ]
          : [
              "Сделка переведена в спорный статус, средства заморожены до решения.",
              "Проверь комментарий и историю в “💼 Кошелек”.",
              "Если нужны детали, дождись решения админа и не закрывай сделку вручную.",
            ],
        issueDraft.kind === "refund" ? "warning" : "info",
      );
    } catch (err) {
      const message = err instanceof Error
        ? err.message
        : issueDraft.kind === "refund"
          ? "Не удалось запросить возврат"
          : "Не удалось открыть спор";
      setMyCargosError(message);
      setProfileError(message);
    } finally {
      setIssueSubmitting(false);
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

  function updateTripCalc<K extends keyof typeof tripCalc>(key: K, value: number) {
    setTripCalc((prev) => ({ ...prev, [key]: Number.isFinite(value) ? value : prev[key] }));
  }

  function renderTripCalculator(item: FeedItem) {
    const distanceKm = item.distance_km ?? tripCalc.fallbackDistanceKm;
    const rateRub = item.rate_rub ?? 0;
    const fuelCost = Math.round((distanceKm * tripCalc.fuelLPer100 * tripCalc.fuelRubPerL) / 100);
    const taxCost = Math.round((rateRub * tripCalc.taxPercent) / 100);
    const extraCost = Math.round(tripCalc.extraRub);
    const netRub = rateRub - fuelCost - taxCost - extraCost;
    const margin = rateRub > 0 ? (netRub / rateRub) * 100 : 0;
    const toneClass = margin >= 20 ? "green" : netRub > 0 ? "yellow" : "red";

    return (
      <div className="trip-calc-panel">
        <div className="trip-calc-grid">
          <label className="trip-calc-field">
            <span>Расход л/100</span>
            <input
              type="number"
              min="0"
              step="0.1"
              value={tripCalc.fuelLPer100}
              onChange={(e) => updateTripCalc("fuelLPer100", Number(e.target.value))}
            />
          </label>
          <label className="trip-calc-field">
            <span>Топливо ₽/л</span>
            <input
              type="number"
              min="0"
              step="0.1"
              value={tripCalc.fuelRubPerL}
              onChange={(e) => updateTripCalc("fuelRubPerL", Number(e.target.value))}
            />
          </label>
          <label className="trip-calc-field">
            <span>Налог %</span>
            <input
              type="number"
              min="0"
              step="0.1"
              value={tripCalc.taxPercent}
              onChange={(e) => updateTripCalc("taxPercent", Number(e.target.value))}
            />
          </label>
          <label className="trip-calc-field">
            <span>Доп. расходы</span>
            <input
              type="number"
              min="0"
              step="1"
              value={tripCalc.extraRub}
              onChange={(e) => updateTripCalc("extraRub", Number(e.target.value))}
            />
          </label>
          {item.distance_km == null && (
            <label className="trip-calc-field trip-calc-distance">
              <span>км</span>
              <input
                type="number"
                min="1"
                step="1"
                value={tripCalc.fallbackDistanceKm}
                onChange={(e) => updateTripCalc("fallbackDistanceKm", Number(e.target.value))}
              />
            </label>
          )}
        </div>

        <div className={`trip-calc-result ${toneClass}`}>
          <div className="trip-calc-line">
            <span>⛽ Топливо</span>
            <span>−{fuelCost.toLocaleString("ru")} ₽</span>
          </div>
          <div className="trip-calc-line">
            <span>📋 Налог {tripCalc.taxPercent}%</span>
            <span>−{taxCost.toLocaleString("ru")} ₽</span>
          </div>
          {extraCost > 0 && (
            <div className="trip-calc-line">
              <span>🧾 Доп. расходы</span>
              <span>−{extraCost.toLocaleString("ru")} ₽</span>
            </div>
          )}
          <div className="trip-calc-divider" />
          <div className="trip-calc-line total">
            <span>💰 Чистыми</span>
            <span>{netRub >= 0 ? "+" : ""}{netRub.toLocaleString("ru")} ₽</span>
          </div>
          <div className="trip-calc-margin">
            Рентабельность {margin.toFixed(1)}%
          </div>
        </div>
      </div>
    );
  }

  function renderCard(item: FeedItem) {
    const isHot = item.is_hot_deal;
    return (
      <article className={`cargo-card${isHot ? " hot" : ""}${item.verified_payment ? " verified" : ""}`} key={item.id}>
        <div className="card-top">
          <div className="card-route">
            {isHot && <span className="hot-badge">🔥</span>}
            <span className="route-text">{item.from_city ?? "?"} → {item.to_city ?? "?"}</span>
            {item.distance_km != null && <span className="distance">{item.distance_km} км</span>}
          </div>
          <div className="card-freshness">{item.freshness}</div>
        </div>

        {item.verified_payment && (
          <div className="honest-banner">
            <div>🛡️ Честный рейс — оплата через сервис</div>
            <div className="honest-banner-note">Средства уже зарезервированы. Выплата после подтверждения разгрузки.</div>
          </div>
        )}

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
            {item.company_rating != null && (
              <span className="company-rating">🏢 {item.company_name || "Компания"} • {item.company_rating}/10</span>
            )}
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
            <button className="action-btn" onClick={() => setExpandedCalcId((current) => current === item.id ? null : item.id)}>
              {expandedCalcId === item.id ? "✖" : "🧮"}
            </button>
            <button className="action-btn similar" onClick={() => void toggleSimilar(item.id)}>
              {expandedId === item.id ? "▲" : "📦"}
            </button>
            <button className="action-btn report" onClick={() => void reportItem(item.id)}>
              🚩
            </button>
          </div>
        </div>

        {expandedCalcId === item.id && renderTripCalculator(item)}

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

  function renderVehicleMatchCards(vehicleId: number) {
    const result = vehicleMatchMap[vehicleId];
    if (!result || result.matched.length === 0) {
      return null;
    }
    return (
      <div className="match-results">
        <h3>🎯 Подходящие грузы</h3>
        {result.matched.slice(0, 3).map((m) => (
          <div className="match-card" key={`vehicle-${vehicleId}-${m.id}`}>
            <div className="match-route">
              {m.verified_payment && "🛡️ "}
              {m.is_hot_deal && "🔥 "}
              <strong>{m.from_city} → {m.to_city}</strong>
            </div>
            <div className="match-details">
              {m.body_type ?? "?"} • {m.weight_t ?? 0}т •{" "}
              <span style={{ color: "var(--green)", fontWeight: 700 }}>
                {(m.rate_rub ?? 0).toLocaleString("ru")}₽
              </span>
              {m.rate_per_km != null && ` (${m.rate_per_km} ₽/км)`}
            </div>
            <div className="muted">
              Совпадение {m.match_score}%{m.distance_to_pickup_km != null ? ` • ${m.distance_to_pickup_km} км до погрузки` : ""}
            </div>
          </div>
        ))}
      </div>
    );
  }

  function renderCargoMatchCards(cargoId: number) {
    const result = cargoMatchMap[cargoId];
    if (!result || result.matched.length === 0) {
      return null;
    }
    return (
      <div className="match-results">
        <h3>🚛 Подходящие машины</h3>
        {result.matched.slice(0, 3).map((m) => (
          <div className="match-card" key={`cargo-${cargoId}-${m.vehicle_id}`}>
            <div className="match-route">
              <strong>{m.body_type} • {m.capacity_tons}т</strong>
              {m.plate_number && <span className="muted"> • {m.plate_number}</span>}
            </div>
            <div className="match-details">
              {m.location_city ? `📍 ${m.location_city}` : "📍 Локация не указана"}
            </div>
            <div className="muted">
              Совпадение {m.match_score}%{m.distance_to_pickup_km != null ? ` • ${m.distance_to_pickup_km} км до погрузки` : ""}
            </div>
          </div>
        ))}
      </div>
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
        <section className="cabinet-summary">
          <article className="cabinet-card">
            <div className="cabinet-title">👤 Кабинет</div>
            {profileLoading && !profileSummary ? (
              <div className="cabinet-meta">Загружаем…</div>
            ) : profileSummary ? (
              <>
                <div className="cabinet-user">{profileSummary.user.name || "Пользователь"}</div>
                <div className="cabinet-meta">
                  {profileSummary.company?.name || "Компания не заполнена"}
                </div>
                <div className="cabinet-meta">
                  {profileSummary.user.is_premium ? "⭐ Premium" : "⚪️ Базовый доступ"}
                  {profileSummary.user.is_verified ? " • ✅ Проверен" : " • ⚠️ Не проверен"}
                </div>
                <div className="cabinet-meta">Размещено грузов: {profileSummary.stats.cargo_count}</div>
                <div className="cabinet-meta">👥 Приглашено: {profileSummary.referral.invited_count}</div>
                <div className="cabinet-meta">✅ Активировано: {profileSummary.referral.activated_count}</div>
                <div className="cabinet-meta">🎁 Бонусов: {profileSummary.referral.reward_days_total} дн.</div>
                <div className="cabinet-meta">
                  {profileSummary.referral.is_ambassador
                    ? "🏅 Амбассадор"
                    : `До амбассадора: ${profileSummary.referral.activated_count}/${profileSummary.referral.ambassador_target}`}
                </div>
                <button className="action-btn primary" onClick={() => void handleInviteColleague()}>
                  Пригласить коллегу
                </button>
              </>
            ) : (
              <>
                <div className="cabinet-meta">{profileError || "Кабинет недоступен"}</div>
                {profileError && (
                  <button className="action-btn" onClick={() => void loadProfileSummary()}>
                    Обновить доступ
                  </button>
                )}
              </>
            )}
          </article>

          <article className="cabinet-card">
            <div className="cabinet-title">💼 Кошелек</div>
            {profileSummary ? (
              <>
                <div className="wallet-balance">
                  {(profileSummary.wallet.balance_rub ?? 0).toLocaleString("ru")} ₽
                </div>
                <div className="cabinet-meta">В холде: {(profileSummary.wallet.frozen_balance_rub ?? 0).toLocaleString("ru")} ₽</div>
                <div className="cabinet-meta">Гарантировано: {profileSummary.stats.verified_payment_count}</div>
                <div className="cabinet-meta">Выплачено сделок: {profileSummary.stats.released_payment_count}</div>
                <button className="action-btn" onClick={() => setTab("wallet")}>Подробнее →</button>
              </>
            ) : (
              <>
                <div className="cabinet-meta">{profileLoading ? "Загружаем…" : "Нет данных"}</div>
                {profileError && !profileLoading && (
                  <button className="action-btn" onClick={() => void loadProfileSummary()}>
                    Обновить доступ
                  </button>
                )}
              </>
            )}
          </article>

          <article className="cabinet-card">
            <div className="cabinet-title">🎯 Матчинг</div>
            {matchSummary ? (
              <>
                <div className="cabinet-user">
                  Для вашей техники: {matchSummary.vehicle_match_count}
                </div>
                <div className="cabinet-meta">
                  Для ваших грузов: {matchSummary.cargo_match_count}
                </div>
                <div className="cabinet-meta">
                  Лучшая техника: {matchSummary.best_vehicle_match_score}%
                </div>
                <div className="cabinet-meta">
                  Лучший груз: {matchSummary.best_cargo_match_score}%
                </div>
                <button className="action-btn" onClick={() => setTab("fleet")}>Смотреть флот →</button>
              </>
            ) : (
              <div className="cabinet-meta">{matchSummaryError || "Совпадения появятся после загрузки"}</div>
            )}
          </article>
        </section>

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
            <div className="kanban-header done">✅ Завершён <span className="count">{completed.length}</span></div>
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
                      <span className="muted">{cargoStatusLabel(cargo.status)}</span>
                    </div>
                  </div>

                  <div className="my-cargo-extra">
                    <span>📅 {cargo.load_date}{cargo.load_time ? ` ${cargo.load_time}` : ""}</span>
                    {cargo.payment_terms && <span>{cargo.payment_terms}</span>}
                    <span className={`escrow-status${cargo.verified_payment ? " verified" : ""}`}>
                      {paymentStatusLabel(cargo.payment_status)}
                    </span>
                    {cargo.description && <span>{cargo.description}</span>}
                  </div>

                  <div className="card-actions">
                    {cargo.payment_status === "unsecured" && (
                      <button
                        className="action-btn primary"
                        onClick={() => void handleCreateEscrow(cargo)}
                      >
                        🛡️ Включить Честный рейс
                      </button>
                    )}
                    {cargo.payment_status === "payment_pending" && (
                      <button
                        className="action-btn primary"
                        onClick={() => void handleCreateEscrow(cargo)}
                      >
                        🔗 Оплатить Честный рейс
                      </button>
                    )}
                    {cargo.payment_status === "funded" && (
                      <button
                        className="action-btn primary"
                        onClick={() => void handleMarkEscrowDelivered(cargo.id)}
                      >
                        🚚 Отметить разгрузку
                      </button>
                    )}
                    {cargo.payment_status === "delivery_marked" && (
                      <button
                        className="action-btn primary"
                        onClick={() => void handleReleaseEscrow(cargo.id)}
                      >
                        💸 Разблокировать оплату
                      </button>
                    )}
                    {["payment_pending", "funded", "delivery_marked"].includes(cargo.payment_status) && (
                      <button
                        className="action-btn"
                        onClick={() => void handleDisputeEscrow(cargo.id, "cargos")}
                      >
                        ⚠️ Открыть спор
                      </button>
                    )}
                    {["payment_pending", "funded", "delivery_marked", "disputed"].includes(cargo.payment_status) && (
                      <button
                        className="action-btn report"
                        onClick={() => void handleRequestRefund(cargo.id, "cargos")}
                      >
                        ↩️ Запросить возврат
                      </button>
                    )}
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
                    <button
                      className="action-btn"
                      onClick={() => void showCargoMatches(cargo.id)}
                    >
                      🎯 Совпадения
                    </button>
                  </div>

                  {renderCargoMatchCards(cargo.id)}

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

  function renderWallet() {
    const securedHistory = (profileSummary?.cargos ?? []).filter(
      (cargo) => cargo.payment_status !== "unsecured",
    );
    const refundJournal = profileSummary?.refund_journal ?? [];

    return (
      <div className="wallet-page">
        {profileError && !profileSummary && (
          <div className="error">
            {profileError}
            <div style={{ marginTop: "10px" }}>
              <button className="action-btn" onClick={() => void loadProfileSummary()}>
                Обновить доступ
              </button>
            </div>
          </div>
        )}

        <section className="wallet-hero">
          <div className="wallet-hero-label">💼 Кошелек</div>
          <div className="wallet-hero-balance">
            {profileSummary ? (profileSummary.wallet.balance_rub ?? 0).toLocaleString("ru") : "0"} ₽
          </div>
          <div className="wallet-hold">
            В холде: {profileSummary ? (profileSummary.wallet.frozen_balance_rub ?? 0).toLocaleString("ru") : "0"} ₽
          </div>
          <div className="wallet-safe-note">
            Под защитой: {profileSummary ? (profileSummary.stats.secured_amount_rub ?? 0).toLocaleString("ru") : "0"} ₽
            {" • "}
            Выплачено: {profileSummary ? (profileSummary.stats.released_amount_rub ?? 0).toLocaleString("ru") : "0"} ₽
          </div>
        </section>

        <section className="wallet-stats-grid">
          <article className="wallet-stat-card">
            <div className="cabinet-title">Гарантировано</div>
            <div className="wallet-stat-value">{profileSummary?.stats.verified_payment_count ?? 0}</div>
          </article>
          <article className="wallet-stat-card">
            <div className="cabinet-title">Выплачено</div>
            <div className="wallet-stat-value">{profileSummary?.stats.released_payment_count ?? 0}</div>
          </article>
          <article className="wallet-stat-card">
            <div className="cabinet-title">Всего грузов</div>
            <div className="wallet-stat-value">{profileSummary?.stats.cargo_count ?? 0}</div>
          </article>
        </section>

        {profileSummary?.company && (
          <section className="wallet-company-card">
            <div className="cabinet-title">🏢 Компания</div>
            <div className="cabinet-user">{profileSummary.company.name || "Компания"}</div>
            <div className="cabinet-meta">Рейтинг: {profileSummary.company.rating}/10</div>
            {profileSummary.company.inn && (
              <div className="cabinet-meta">ИНН: {profileSummary.company.inn}</div>
            )}
          </section>
        )}

        <section className="wallet-history">
          <div className="fleet-header">
            <h2>🛡️ История Честного рейса</h2>
          </div>
          {profileLoading && !profileSummary ? (
            <p className="muted" style={{ textAlign: "center", padding: "20px" }}>Загружаем…</p>
          ) : securedHistory.length === 0 ? (
            <p className="muted" style={{ textAlign: "center", padding: "20px" }}>
              Пока нет сделок с резервом оплаты
            </p>
          ) : (
            <div className="my-cargo-list">
              {securedHistory.map((cargo) => (
                <div className="my-cargo-card published" key={cargo.id}>
                  <div className="my-cargo-head">
                    <div>
                      <div className="my-cargo-route">{cargo.from_city} → {cargo.to_city}</div>
                      <div className="my-cargo-meta">
                        {cargo.weight}т • {cargo.price.toLocaleString("ru")} ₽
                      </div>
                    </div>
                    <span className="escrow-status verified">
                      {paymentStatusLabel(cargo.payment_status)}
                    </span>
                  </div>
                  <div className="wallet-safe-breakdown">
                    <span>Сумма: {(cargo.escrow_amount_rub ?? cargo.price).toLocaleString("ru")} ₽</span>
                    {cargo.platform_fee_rub != null && <span>Комиссия: {cargo.platform_fee_rub.toLocaleString("ru")} ₽</span>}
                    {cargo.carrier_amount_rub != null && <span>К выплате: {cargo.carrier_amount_rub.toLocaleString("ru")} ₽</span>}
                  </div>
                  <div className="wallet-safe-note">
                    {paymentStatusHint(cargo.payment_status)}
                  </div>
                  <div className="card-actions">
                    {cargo.payment_status === "payment_pending" && (
                      <button
                        className="action-btn primary"
                        onClick={() => void handleCreateEscrowById(cargo.id, cargo.price, "wallet")}
                      >
                        🔗 Оплатить Честный рейс
                      </button>
                    )}
                    {cargo.payment_status === "funded" && (
                      <button
                        className="action-btn primary"
                        onClick={() => void handleMarkEscrowDelivered(cargo.id)}
                      >
                        🚚 Отметить разгрузку
                      </button>
                    )}
                    {cargo.payment_status === "delivery_marked" && (
                      <button
                        className="action-btn primary"
                        onClick={() => void handleReleaseEscrow(cargo.id)}
                      >
                        💸 Разблокировать оплату
                      </button>
                    )}
                    {["payment_pending", "funded", "delivery_marked"].includes(cargo.payment_status) && (
                      <button
                        className="action-btn"
                        onClick={() => void handleDisputeEscrow(cargo.id, "wallet")}
                      >
                        ⚠️ Открыть спор
                      </button>
                    )}
                    {["payment_pending", "funded", "delivery_marked", "disputed"].includes(cargo.payment_status) && (
                      <button
                        className="action-btn report"
                        onClick={() => void handleRequestRefund(cargo.id, "wallet")}
                      >
                        ↩️ Запросить возврат
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="wallet-history">
          <div className="fleet-header">
            <h2>↩️ Журнал возвратов и споров</h2>
          </div>
          {refundJournal.length === 0 ? (
            <p className="muted" style={{ textAlign: "center", padding: "20px" }}>
              Возвратов и спорных сделок пока нет
            </p>
          ) : (
            <div className="my-cargo-list">
              {refundJournal.map((entry) => (
                <div className="my-cargo-card" key={`${entry.escrow_id}-${entry.updated_at}`}>
                  <div className="my-cargo-head">
                    <div>
                      <div className="my-cargo-route">{entry.from_city || "—"} → {entry.to_city || "—"}</div>
                      <div className="my-cargo-meta">
                        Сделка #{entry.escrow_id} • {entry.role === "client" ? "Заказчик" : "Перевозчик"}
                      </div>
                    </div>
                    <span className={`escrow-status${entry.status === "cancelled" ? "" : " verified"}`}>
                      {paymentStatusLabel(entry.status)}
                    </span>
                  </div>
                  <div className="wallet-safe-breakdown">
                    {entry.refund_amount_rub != null && (
                      <span>Возврат: {entry.refund_amount_rub.toLocaleString("ru")} ₽</span>
                    )}
                    <span>{new Date(entry.updated_at).toLocaleString("ru-RU")}</span>
                  </div>
                  <div className="wallet-safe-note">{disputeStatusHint(entry.status)}</div>
                  {entry.reason && (
                    <div className="wallet-refund-note"><strong>Причина:</strong> {entry.reason}</div>
                  )}
                  {entry.note && (
                    <div className="wallet-refund-note"><strong>Комментарий:</strong> {entry.note}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
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
            <button className={`tab-btn${tab === "wallet" ? " active" : ""}`} onClick={() => setTab("wallet")}>💼</button>
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
                if (!canUseTelegramOnlyActions) {
                  setCargoError("Публикация груза доступна только из Telegram Mini App");
                  return;
                }
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
      {actionGuide && (
        <section className={`action-guide ${actionGuide.tone}`}>
          <div className="action-guide-head">
            <strong>{actionGuide.title}</strong>
            <button className="action-guide-close" onClick={() => setActionGuide(null)} aria-label="Закрыть">
              ✕
            </button>
          </div>
          <ol className="action-guide-steps">
            {actionGuide.steps.map((step, index) => (
              <li key={`${index}-${step}`}>{step}</li>
            ))}
          </ol>
        </section>
      )}
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
      {cargoError && !showAddCargo && (tab === "feed" || tab === "map") && (
        <div className="error">{cargoError}</div>
      )}

      {issueDraft && (
        <div className="modal-backdrop" onClick={closeEscrowIssueModal}>
          <div className="modal-card" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h3>{issueDraft.kind === "refund" ? "↩️ Запросить возврат" : "⚠️ Открыть спор"}</h3>
              <button
                type="button"
                className="modal-close"
                onClick={closeEscrowIssueModal}
                disabled={issueSubmitting}
                aria-label="Закрыть"
              >
                ✕
              </button>
            </div>
            <form className="modal-form" onSubmit={(event) => void submitEscrowIssue(event)}>
              <label className="modal-field">
                <span>{issueDraft.kind === "refund" ? "Причина возврата" : "Причина спора"}</span>
                <input
                  type="text"
                  value={issueReason}
                  onChange={(event) => setIssueReason(event.target.value)}
                  placeholder={issueDraft.kind === "refund" ? "Нужно отменить сделку" : "Проблема по условиям рейса"}
                  disabled={issueSubmitting}
                  autoFocus
                />
              </label>
              <label className="modal-field">
                <span>Комментарий</span>
                <textarea
                  value={issueNote}
                  onChange={(event) => setIssueNote(event.target.value)}
                  placeholder="Подробности, если нужны"
                  rows={4}
                  disabled={issueSubmitting}
                />
              </label>
              <div className="modal-actions">
                <button
                  type="button"
                  className="action-btn"
                  onClick={closeEscrowIssueModal}
                  disabled={issueSubmitting}
                >
                  Отмена
                </button>
                <button type="submit" className="action-btn primary" disabled={issueSubmitting}>
                  {issueSubmitting
                    ? "⏳"
                    : issueDraft.kind === "refund"
                      ? "Отправить запрос"
                      : "Открыть спор"}
                </button>
              </div>
            </form>
          </div>
        </div>
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
      {tab === "wallet" && renderWallet()}

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
                      <>
                        <span className="status-badge free">🟢 Свободен</span>
                        <button className="action-btn" onClick={() => void showVehicleMatches(v.id)}>
                          🎯 Совпадения
                        </button>
                      </>
                    ) : (
                      <button className="action-btn primary" onClick={async () => {
                        const city = window.prompt("Город, где свободна машина:", v.location_city || "");
                        if (!city) return;
                        const result = await setVehicleAvailable(v.id, city);
                        setMatchResult(result);
                        setVehicleMatchMap((prev) => ({ ...prev, [v.id]: result }));
                        setVehicles(await fetchVehicles());
                      }}>Я свободен!</button>
                    )}
                  </div>
                  {renderVehicleMatchCards(v.id)}
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
