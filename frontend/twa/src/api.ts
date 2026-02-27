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
};

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
  const initData = (window as any)?.Telegram?.WebApp?.initData || null;
  if (initData) {
    headers.Authorization = `tma ${initData}`;
  }

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
