export type FeedItem = {
  id: number;
  stream_entry_id: string;
  from_city: string | null;
  to_city: string | null;
  body_type: string | null;
  rate_rub: number | null;
  weight_t: number | null;
  phone: string | null;
  phone_masked: boolean;
  can_view_contact: boolean;
  trust_score: number | null;
  trust_verdict: string | null;
  trust_comment: string | null;
  provider: string | null;
  status: string;
  created_at: string;
  load_date: string | null;
  load_time: string | null;
  cargo_description: string | null;
  payment_terms: string | null;
  is_direct_customer: boolean | null;
  dimensions: string | null;
  is_hot_deal: boolean;
  suggested_response: string | null;
  reply_link: string | null;
  phone_blacklisted: boolean;
  rate_per_km: number | null;
  distance_km: number | null;
  freshness: string | null;
  ati_link: string | null;
  payment_status: string | null;
  verified_payment: boolean;
  company_name: string | null;
  company_rating: number | null;
};

function getRequiredInitData(actionLabel: string): string {
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (!initData) {
    throw new Error(`${actionLabel} доступно только из Telegram Mini App`);
  }
  return initData;
}

export type SimilarItem = {
  id: number;
  from_city: string | null;
  to_city: string | null;
  body_type: string | null;
  rate_rub: number | null;
  weight_t: number | null;
  load_date: string | null;
  is_hot_deal: boolean;
  created_at: string;
};

type FeedResponse = {
  items: FeedItem[];
  limit: number;
  has_more: boolean;
  next_cursor: number | null;
};

export async function fetchFeed(params: {
  verdict: Array<"green" | "yellow" | "red">;
  cursor?: number | null;
  limit?: number;
  initData?: string | null;
  from_city?: string | null;
  to_city?: string | null;
  body_type?: string | null;
  load_date?: string | null;
}): Promise<FeedResponse> {
  const query = new URLSearchParams();
  (params.verdict || ["green", "yellow"]).forEach((v) => query.append("verdict", v));
  query.set("limit", String(params.limit ?? 20));
  if (params.cursor) query.set("cursor", String(params.cursor));
  if (params.from_city) query.set("from_city", params.from_city);
  if (params.to_city) query.set("to_city", params.to_city);
  if (params.body_type) query.set("body_type", params.body_type);
  if (params.load_date) query.set("load_date", params.load_date);

  const headers: Record<string, string> = {};
  if (params.initData) {
    headers.Authorization = `tma ${params.initData}`;
  }

  const response = await fetch(`/api/v1/feed?${query.toString()}`, {
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    throw new Error(`Feed request failed: ${response.status}`);
  }
  return (await response.json()) as FeedResponse;
}

export type FavoriteItem = {
  id: number;
  feed_id: number;
  note: string | null;
  status: string;
  from_city: string | null;
  to_city: string | null;
  body_type: string | null;
  rate_rub: number | null;
  phone: string | null;
  load_date: string | null;
  is_hot_deal: boolean;
  created_at: string;
};

export async function fetchFavorites(): Promise<FavoriteItem[]> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) headers.Authorization = `tma ${initData}`;
  const response = await fetch("/api/v1/favorites?limit=50", { credentials: "include", headers });
  if (!response.ok) return [];
  const data = await response.json();
  return data.items ?? [];
}

export async function updateFavoriteStatus(feedId: number, status: string): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) headers.Authorization = `tma ${initData}`;
  await fetch(`/api/v1/favorites/${feedId}`, {
    method: "PATCH",
    credentials: "include",
    headers,
    body: JSON.stringify({ status }),
  });
}

export async function fetchSimilar(feedId: number): Promise<SimilarItem[]> {
  const response = await fetch(`/api/v1/feed/${feedId}/similar?limit=3`, {
    credentials: "include",
  });
  if (!response.ok) return [];
  const data = await response.json();
  return data.items ?? [];
}

export async function addFavorite(feedId: number, note?: string): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) headers.Authorization = `tma ${initData}`;
  await fetch(`/api/v1/favorites/${feedId}`, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify({ note: note || null }),
  });
}

export type VehicleItem = {
  id: number;
  body_type: string;
  capacity_tons: number;
  location_city: string | null;
  is_available: boolean;
  plate_number: string | null;
  sts_verified: boolean;
};

export type ManualCargoPayload = {
  origin: string;
  destination: string;
  body_type: string;
  weight: number;
  price: number;
  load_date: string;
  load_time?: string | null;
  description?: string | null;
  payment_terms?: string | null;
};

export type ManualCargoResponse = {
  ok: boolean;
  cargo_id: number;
  feed_id: number;
};

export type MyCargoItem = {
  id: number;
  from_city: string;
  to_city: string;
  body_type: string;
  weight: number;
  price: number;
  load_date: string;
  load_time: string | null;
  description: string | null;
  payment_terms: string | null;
  status: string;
  feed_id: number | null;
  feed_status: string | null;
  is_published: boolean;
  payment_status: string;
  verified_payment: boolean;
  escrow_amount_rub: number | null;
  escrow_status: string | null;
  created_at: string;
};

export type EscrowActionResponse = {
  ok: boolean;
  cargo_id: number;
  escrow_id: number;
  status: string;
  payment_status: string;
  amount_rub: number;
  platform_fee_rub: number;
  carrier_amount_rub: number;
  payment_url: string | null;
  provider: string;
};

export type SubscriptionItem = {
  id: number;
  from_city: string | null;
  to_city: string | null;
  body_type: string | null;
  min_rate: number | null;
  max_weight: number | null;
  region: string | null;
  is_active: boolean;
  match_count: number;
};

export type WebappProfileCargo = {
  id: number;
  from_city: string;
  to_city: string;
  weight: number;
  price: number;
  status: string;
  payment_status: string;
  payment_verified: boolean;
  escrow_id: number | null;
  escrow_status: string | null;
  escrow_amount_rub: number | null;
  platform_fee_rub: number | null;
  carrier_amount_rub: number | null;
  load_date: string;
};

export type RefundJournalItem = {
  escrow_id: number;
  cargo_id: number;
  from_city: string | null;
  to_city: string | null;
  role: "client" | "carrier";
  status: string;
  reason: string | null;
  note: string | null;
  refund_amount_rub: number | null;
  updated_at: string;
};

export type WebappProfileResponse = {
  user: {
    id: number;
    name: string;
    username: string | null;
    phone: string | null;
    is_carrier: boolean;
    is_verified: boolean;
    is_premium: boolean;
    premium_until: string | null;
  };
  company: {
    id: number;
    name: string | null;
    inn: string | null;
    rating: number;
  } | null;
  wallet: {
    balance_rub: number;
    frozen_balance_rub: number;
  };
  stats: {
    cargo_count: number;
    verified_payment_count: number;
    released_payment_count: number;
    secured_amount_rub: number;
    released_amount_rub: number;
  };
  cargos: WebappProfileCargo[];
  refund_journal: RefundJournalItem[];
};

type MyCargoResponse = {
  items: MyCargoItem[];
  limit: number;
};

type SubscriptionListResponse = {
  items: SubscriptionItem[];
};

export async function fetchWebappProfile(): Promise<WebappProfileResponse> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch("/api/webapp/profile", {
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    throw new Error(`Profile request failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchVehicles(): Promise<VehicleItem[]> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) headers.Authorization = `tma ${initData}`;
  const response = await fetch("/api/v1/fleet/vehicles", { credentials: "include", headers });
  if (!response.ok) return [];
  const data = await response.json();
  return data.vehicles ?? [];
}

export async function addVehicle(
  body_type: string,
  capacity_tons: number,
  city?: string,
  plate_number?: string,
): Promise<VehicleItem> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) headers.Authorization = `tma ${initData}`;
  const response = await fetch("/api/v1/fleet/vehicles", {
    method: "POST", credentials: "include", headers,
    body: JSON.stringify({
      body_type,
      capacity_tons,
      location_city: city || null,
      plate_number: plate_number || null,
    }),
  });
  if (!response.ok) {
    throw new Error(`Add vehicle failed: ${response.status}`);
  }
  return response.json();
}

export async function setVehicleAvailable(vehicleId: number, city: string): Promise<any> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) headers.Authorization = `tma ${initData}`;
  const resp = await fetch(`/api/v1/fleet/vehicles/${vehicleId}/available?city=${encodeURIComponent(city)}`, {
    method: "POST", credentials: "include", headers,
  });
  if (!resp.ok) return null;
  return resp.json();
}

export async function trackClick(feedId: number): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch(`/api/v1/feed/${feedId}/click`, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify({ source: "twa" }),
  });

  if (!response.ok) {
    throw new Error(`Click tracking failed: ${response.status}`);
  }
}

export async function createManualCargo(payload: ManualCargoPayload): Promise<ManualCargoResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = getRequiredInitData("Публикация груза");
  headers.Authorization = `tma ${initData}`;

  const response = await fetch("/api/v1/cargos/manual", {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Create cargo failed: ${response.status}`);
  }

  return response.json();
}

export async function fetchMyCargos(limit = 50): Promise<MyCargoItem[]> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch(`/api/v1/cargos/my?limit=${limit}`, {
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    throw new Error(`My cargos request failed: ${response.status}`);
  }

  const data = (await response.json()) as MyCargoResponse;
  return data.items ?? [];
}

export async function updateManualCargo(
  cargoId: number,
  payload: Partial<ManualCargoPayload>,
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch(`/api/v1/cargos/${cargoId}`, {
    method: "PATCH",
    credentials: "include",
    headers,
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Update cargo failed: ${response.status}`);
  }
}

export async function archiveManualCargo(cargoId: number): Promise<void> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch(`/api/v1/cargos/${cargoId}/archive`, {
    method: "POST",
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    throw new Error(`Archive cargo failed: ${response.status}`);
  }
}

export async function createEscrow(cargoId: number, amount_rub?: number): Promise<EscrowActionResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch(`/api/v1/escrow/${cargoId}/create`, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify({ amount_rub: amount_rub ?? null }),
  });

  if (!response.ok) {
    throw new Error(`Create escrow failed: ${response.status}`);
  }

  return response.json();
}

export async function markEscrowDelivered(cargoId: number): Promise<EscrowActionResponse> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch(`/api/v1/escrow/${cargoId}/mark-delivered`, {
    method: "POST",
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    throw new Error(`Mark delivered failed: ${response.status}`);
  }

  return response.json();
}

export async function releaseEscrow(cargoId: number): Promise<EscrowActionResponse> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch(`/api/v1/escrow/${cargoId}/release`, {
    method: "POST",
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    throw new Error(`Release escrow failed: ${response.status}`);
  }

  return response.json();
}

export async function disputeEscrow(
  cargoId: number,
  reason?: string | null,
  note?: string | null,
): Promise<EscrowActionResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch(`/api/v1/escrow/${cargoId}/dispute`, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify({ reason: reason || null, note: note || null }),
  });

  if (!response.ok) {
    throw new Error(`Dispute escrow failed: ${response.status}`);
  }

  return response.json();
}

export async function requestEscrowRefund(
  cargoId: number,
  reason?: string | null,
  note?: string | null,
): Promise<EscrowActionResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

  const response = await fetch(`/api/v1/escrow/${cargoId}/request-refund`, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify({ reason: reason || null, note: note || null }),
  });

  if (!response.ok) {
    throw new Error(`Request refund failed: ${response.status}`);
  }

  return response.json();
}

export async function fetchSubscriptions(): Promise<SubscriptionItem[]> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }
  const response = await fetch("/api/v1/subscriptions", {
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    throw new Error(`Subscriptions request failed: ${response.status}`);
  }
  const data = (await response.json()) as SubscriptionListResponse;
  return data.items ?? [];
}

export async function createSubscription(payload: {
  from_city?: string | null;
  to_city?: string | null;
  body_type?: string | null;
  min_rate?: number | null;
  max_weight?: number | null;
  region?: string | null;
}): Promise<SubscriptionItem> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }
  const response = await fetch("/api/v1/subscriptions", {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Create subscription failed: ${response.status}`);
  }
  const data = await response.json();
  return data.item as SubscriptionItem;
}

export async function deleteSubscription(subscriptionId: number): Promise<void> {
  const headers: Record<string, string> = {};
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }
  const response = await fetch(`/api/v1/subscriptions/${subscriptionId}`, {
    method: "DELETE",
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    throw new Error(`Delete subscription failed: ${response.status}`);
  }
}
