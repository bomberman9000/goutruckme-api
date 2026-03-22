"""
SEO-страницы ГрузПоток.
/gruz/{from_city}-{to_city}     — грузы по маршруту
/gruz/{city}                    — грузы из/в город
/perevozchiki/{city}            — перевозчики в городе
/mashiny/{body_type}            — машины по типу кузова

Возвращают полноценный HTML с мета-тегами, H1, JSON-LD (Schema.org),
структурированным списком грузов — для индексации Яндекс/Google.
"""
import re
import json as _json
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from sqlalchemy import desc as sa_desc
from fastapi import Depends

from app.db.database import get_db
from app.models.models import Load, User, Vehicle

router = APIRouter()

SITE_URL = "https://gruzpotok.ru"
SITE_NAME = "ГрузПоток"

CITY_MAP = {
    "moskva": "Москва", "moscow": "Москва",
    "sankt-peterburg": "Санкт-Петербург", "spb": "Санкт-Петербург",
    "ekaterinburg": "Екатеринбург", "yekaterinburg": "Екатеринбург",
    "novosibirsk": "Новосибирск", "kazan": "Казань",
    "tashkent": "Ташкент", "bishkek": "Бишкек",
    "almaty": "Алматы", "nur-sultan": "Нур-Султан", "astana": "Нур-Султан",
    "krasnodar": "Краснодар", "rostov-na-donu": "Ростов-на-Дону",
    "ufa": "Уфа", "samara": "Самара", "chelyabinsk": "Челябинск",
    "omsk": "Омск", "krasnoyarsk": "Красноярск", "irkutsk": "Иркутск",
    "vladivostok": "Владивосток", "khabarovsk": "Хабаровск",
    "perm": "Пермь", "voronezh": "Воронеж", "volgograd": "Волгоград",
    "saratov": "Саратов", "tyumen": "Тюмень", "tolyatti": "Тольятти",
    "izhevsk": "Ижевск", "barnaul": "Барнаул", "ulyanovsk": "Ульяновск",
    "vladikavkaz": "Владикавказ", "mahachkala": "Махачкала",
    "minsk": "Минск", "kiev": "Киев", "kharkov": "Харьков",
    "kirov": "Киров", "orenburg": "Оренбург", "termez": "Термез",
    "borisov": "Борисов", "brest": "Брест", "grodno": "Гродно",
    "samarkand": "Самарканд", "bukhara": "Бухара",
    "novorossiysk": "Новороссийск", "sochi": "Сочи",
}


def _city_from_slug(slug: str) -> str:
    """Return Russian city name for a slug, or best-effort deslug."""
    if slug in CITY_MAP:
        return CITY_MAP[slug]
    # Try multi-word slug
    for k, v in CITY_MAP.items():
        if slug == k or slug.replace("-", "") == k.replace("-", ""):
            return v
    return _deslug(slug).title()


BODY_LABELS = {
    "tent":        "Тент",
    "ref":         "Рефрижератор",
    "isoterm":     "Изотерм",
    "konteyner":   "Контейнер",
    "bort":        "Борт",
    "manipulator": "Манипулятор",
    "samosval":    "Самосвал",
    "evakuator":   "Эвакуатор",
    "auto":        "Автовоз",
    "lowboy":      "Низкорамник",
}

_CITY_CACHE: dict = {}
_CITY_CACHE_TS: datetime | None = None
_CITY_TTL = 600  # 10 min


def _slug(text: str) -> str:
    """Город → slug: 'Санкт-Петербург' → 'sankt-peterburg'."""
    t = text.lower().strip()
    t = re.sub(r'[ёЁ]', 'yo', t)
    t = t.replace('а','a').replace('б','b').replace('в','v').replace('г','g')
    t = t.replace('д','d').replace('е','e').replace('ж','zh').replace('з','z')
    t = t.replace('и','i').replace('й','y').replace('к','k').replace('л','l')
    t = t.replace('м','m').replace('н','n').replace('о','o').replace('п','p')
    t = t.replace('р','r').replace('с','s').replace('т','t').replace('у','u')
    t = t.replace('ф','f').replace('х','kh').replace('ц','ts').replace('ч','ch')
    t = t.replace('ш','sh').replace('щ','shch').replace('ъ','').replace('ы','y')
    t = t.replace('ь','').replace('э','e').replace('ю','yu').replace('я','ya')
    t = re.sub(r'[^a-z0-9]+', '-', t).strip('-')
    return t


def _deslug(slug: str) -> str:
    """Обратное — для поиска в БД. Приблизительно."""
    return slug.replace('-', ' ')


def _page_html(
    title: str,
    description: str,
    canonical: str,
    h1: str,
    content_html: str,
    breadcrumbs: list[tuple[str, str]],
    json_ld: dict | None = None,
) -> str:
    bc_json = _json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1,
             "name": name, "item": SITE_URL + url}
            for i, (name, url) in enumerate(breadcrumbs)
        ]
    }, ensure_ascii=False)

    extra_ld = ""
    if json_ld:
        extra_ld = f'<script type="application/ld+json">{_json.dumps(json_ld, ensure_ascii=False)}</script>'

    bc_html = " › ".join(
        f'<a href="{url}" style="color:#3b82f6;text-decoration:none">{name}</a>'
        for name, url in breadcrumbs
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:url" content="{SITE_URL}{canonical}">
<meta property="og:site_name" content="{SITE_NAME}">
<link rel="canonical" href="{SITE_URL}{canonical}">
<link rel="icon" href="/static/icons/logo-64.png">
<script type="application/ld+json">{bc_json}</script>
{extra_ld}
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f8fafc; color: #111827; margin: 0; padding: 0; line-height: 1.6; }}
  .site-header {{ background: #1e3a8a; color: #fff; padding: 14px 0; }}
  .header-inner {{ max-width: 1100px; margin: 0 auto; padding: 0 20px;
                   display: flex; align-items: center; gap: 16px; }}
  .logo {{ font-size: 1.25rem; font-weight: 800; color: #fff; text-decoration: none; }}
  .logo span {{ color: #f97316; }}
  .header-links {{ margin-left: auto; display: flex; gap: 16px; }}
  .header-links a {{ color: rgba(255,255,255,.8); text-decoration: none; font-size: .9rem; }}
  .header-links a:hover {{ color: #fff; }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 24px 20px; }}
  .breadcrumb {{ font-size: .82rem; color: #6b7280; margin-bottom: 20px; }}
  h1 {{ font-size: 1.75rem; font-weight: 800; margin: 0 0 8px; color: #111827; }}
  .subtitle {{ color: #6b7280; font-size: .95rem; margin-bottom: 28px; }}
  .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 14px;
           padding: 18px 20px; margin-bottom: 12px; transition: box-shadow .15s; }}
  .card:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,.08); }}
  .card-route {{ font-size: 1rem; font-weight: 700; color: #111827; }}
  .card-meta {{ font-size: .82rem; color: #6b7280; margin-top: 4px; }}
  .card-price {{ font-size: 1.05rem; font-weight: 700; color: #1e3a8a; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 20px;
            font-size: .75rem; font-weight: 600; margin-right: 4px; }}
  .badge-blue {{ background: #eff6ff; color: #1e3a8a; }}
  .badge-green {{ background: #f0fdf4; color: #15803d; }}
  .badge-orange {{ background: #fff7ed; color: #c2410c; }}
  .cta-block {{ background: linear-gradient(135deg, #1e3a8a, #2563eb);
                border-radius: 16px; padding: 32px 28px; color: #fff;
                text-align: center; margin: 36px 0; }}
  .cta-block h2 {{ font-size: 1.4rem; margin: 0 0 10px; }}
  .cta-block p {{ opacity: .85; margin: 0 0 20px; font-size: .95rem; }}
  .cta-btn {{ display: inline-block; background: #f97316; color: #fff;
              padding: 12px 32px; border-radius: 10px; font-weight: 700;
              text-decoration: none; font-size: 1rem; }}
  .related {{ margin-top: 36px; }}
  .related h3 {{ font-size: 1.1rem; font-weight: 700; margin-bottom: 14px; }}
  .related-links {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .related-links a {{ padding: 6px 14px; border-radius: 8px; background: #eff6ff;
                      color: #1e3a8a; text-decoration: none; font-size: .85rem;
                      border: 1px solid #bfdbfe; }}
  .related-links a:hover {{ background: #dbeafe; }}
  .empty {{ text-align: center; padding: 48px 20px; color: #6b7280; }}
  footer {{ background: #1e293b; color: rgba(255,255,255,.6); text-align: center;
            padding: 24px; font-size: .82rem; margin-top: 48px; }}
  footer a {{ color: rgba(255,255,255,.7); text-decoration: none; }}
</style>
</head>
<body>
<header class="site-header">
  <div class="header-inner">
    <a href="/" class="logo">Груз<span>Поток</span></a>
    <nav class="header-links">
      <a href="/">Биржа грузов</a>
      <a href="/#trucks">Машины</a>
      <a href="/perevozchiki/moskva">Перевозчики</a>
    </nav>
  </div>
</header>
<main class="container">
  <div class="breadcrumb">{bc_html}</div>
  <h1>{h1}</h1>
  {content_html}
</main>
<footer>
  © 2024 {SITE_NAME} — биржа грузов и перевозчиков России ·
  <a href="/">Главная</a> · <a href="/gruz/moskva-sankt-peterburg">Москва — СПб</a>
</footer>
</body>
</html>"""


def _load_card(load: Load) -> str:
    weight = f"{load.weight} т" if load.weight else ""
    volume = f"{load.volume} м³" if load.volume else ""
    price = f"{int(load.total_price):,} ₽".replace(",", " ") if load.total_price else "Договорная"
    body = load.required_body_type or ""
    distance = f"{int(load.distance_km)} км" if load.distance_km else ""
    date = load.loading_date.strftime("%-d %b") if load.loading_date else ""

    badges = ""
    if body:
        badges += f'<span class="badge badge-blue">{BODY_LABELS.get(body, body)}</span>'
    if weight:
        badges += f'<span class="badge badge-green">{weight}</span>'
    if volume:
        badges += f'<span class="badge badge-orange">{volume}</span>'

    meta_parts = [p for p in [date, distance, weight, volume] if p]

    return f"""<div class="card" itemscope itemtype="https://schema.org/Service">
  <div class="card-route" itemprop="name">🚚 {load.from_city} → {load.to_city}</div>
  <div class="card-meta">{" · ".join(meta_parts)}{(" · " + badges) if badges else ""}</div>
  <div style="margin-top:8px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
    <div class="card-price" itemprop="offers" itemscope itemtype="https://schema.org/Offer">
      <span itemprop="price">{price}</span>
    </div>
    <a href="/" style="padding:7px 18px;background:#1e3a8a;color:#fff;border-radius:8px;
       text-decoration:none;font-size:.85rem;font-weight:600">Подробнее →</a>
  </div>
</div>"""


POPULAR_ROUTES = [
    ("Москва", "Санкт-Петербург"),
    ("Москва", "Екатеринбург"),
    ("Москва", "Новосибирск"),
    ("Казань", "Ташкент"),
    ("Санкт-Петербург", "Москва"),
    ("Екатеринбург", "Ташкент"),
    ("Новосибирск", "Москва"),
    ("Москва", "Краснодар"),
]


def _related_routes(from_city: str | None = None, to_city: str | None = None) -> str:
    links = []
    for fc, tc in POPULAR_ROUTES:
        if fc == from_city and tc == to_city:
            continue
        slug = f"{_slug(fc)}-{_slug(tc)}"
        links.append(f'<a href="/gruz/{slug}">{fc} → {tc}</a>')
    if not links:
        return ""
    return f'<div class="related"><h3>Похожие маршруты</h3><div class="related-links">{"".join(links[:8])}</div></div>'


# ── /gruz/{from}-{to} ──────────────────────────────────────────────────────
@router.get("/gruz/{route_slug}", response_class=HTMLResponse)
def seo_route(route_slug: str, db: Session = Depends(get_db)):
    """SEO страница маршрута: /gruz/moskva-sankt-peterburg"""
    parts = route_slug.split("-", 1)

    # Try split: найти разделение slug на два города
    # Перебираем все варианты разделения
    from_city = None
    to_city = None
    loads = []

    # Build candidate pairs by splitting on each '-'
    slug_parts = route_slug.split("-")
    best = None
    best_count = -1

    for split_at in range(1, len(slug_parts)):
        fc_slug = "-".join(slug_parts[:split_at])
        tc_slug = "-".join(slug_parts[split_at:])
        fc_ru = _city_from_slug(fc_slug)
        tc_ru = _city_from_slug(tc_slug)
        fc_like = f"%{fc_ru}%"
        tc_like = f"%{tc_ru}%"
        cnt = db.query(func.count(Load.id)).filter(
            Load.from_city.ilike(fc_like),
            Load.to_city.ilike(tc_like),
        ).scalar()
        if cnt > best_count:
            best_count = cnt
            best = (fc_slug, tc_slug)

    if best and best_count > 0:
        fc_slug, tc_slug = best
        fc_like = f"%{_city_from_slug(fc_slug)}%"
        tc_like = f"%{_city_from_slug(tc_slug)}%"
        loads = (
            db.query(Load)
            .filter(Load.from_city.ilike(fc_like), Load.to_city.ilike(tc_like))
            .order_by(sa_desc(Load.created_at))
            .limit(20)
            .all()
        )
        if loads:
            from_city = loads[0].from_city or ""
            to_city = loads[0].to_city or ""

    if not (from_city and to_city):
        # Single city — treats slug as single city
        city_like = f"%{_city_from_slug(route_slug)}%"
        loads = (
            db.query(Load)
            .filter(or_(Load.from_city.ilike(city_like), Load.to_city.ilike(city_like)))
            .order_by(sa_desc(Load.created_at))
            .limit(20)
            .all()
        )
        # Use actual city name from DB if found
        if loads:
            for l in loads:
                if city_like.strip('%').lower() in l.from_city.lower():
                    display_city = l.from_city
                    break
                if city_like.strip('%').lower() in l.to_city.lower():
                    display_city = l.to_city
                    break
            else:
                display_city = _deslug(route_slug).title()
        else:
            display_city = _deslug(route_slug).title()
        h1 = f"Грузоперевозки {display_city} — актуальные грузы"
        title = f"Грузоперевозки {display_city} 2024 | {SITE_NAME}"
        desc = f"Актуальные грузы из/в {display_city}. Найдите перевозчика или груз на ГрузПоток — бирже грузов России."
        canonical = f"/gruz/{route_slug}"
        bc = [("/", "Главная"), ("/gruz/moskva", "Грузы"), (canonical, display_city)]
        json_ld = None
    else:
        count = len(loads)
        h1 = f"Грузоперевозки {from_city} — {to_city}: {count} активных грузов"
        title = f"Грузоперевозки {from_city} {to_city} 2024 — найти перевозчика | {SITE_NAME}"
        desc = (f"Грузоперевозки {from_city} — {to_city}. {count} актуальных грузов. "
                f"Найдите надёжного перевозчика или разместите груз бесплатно на ГрузПоток.")
        canonical = f"/gruz/{route_slug}"
        bc = [("/", "Главная"), ("/gruz/moskva", "Грузы"), (canonical, f"{from_city} → {to_city}")]
        json_ld = {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": f"Грузоперевозки {from_city} — {to_city}",
            "numberOfItems": count,
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1,
                 "name": f"Груз {l.from_city} → {l.to_city}" + (f", {int(l.weight)} т" if l.weight else "")}
                for i, l in enumerate(loads[:5])
            ]
        }

    if loads:
        cards = "".join(_load_card(l) for l in loads)
        word = "груз" if len(loads) == 1 else "грузов"
        subtitle = f'<p class="subtitle">Найдено {len(loads)} {word} · обновлено {datetime.now().strftime("%-d %b %Y")}</p>'
        content = subtitle + f'<div itemscope itemtype="https://schema.org/ItemList">{cards}</div>'
    else:
        content = '<div class="empty">По этому маршруту пока нет актуальных грузов.<br>Разместите груз бесплатно — перевозчики найдут вас сами.</div>'

    content += '<div class="cta-block"><h2>Найдите перевозчика за 5 минут</h2><p>Более 200 активных грузов каждый день. Бесплатное размещение.</p><a href="/" class="cta-btn">Разместить груз бесплатно</a></div>'
    content += _related_routes(from_city, to_city)

    return _page_html(title, desc, canonical, h1, content, bc, json_ld)


# ── /perevozchiki/{city} ───────────────────────────────────────────────────
@router.get("/perevozchiki/{city_slug}", response_class=HTMLResponse)
def seo_carriers(city_slug: str, db: Session = Depends(get_db)):
    display_city = _city_from_slug(city_slug)
    city_like = f"%{display_city}%"

    carriers = (
        db.query(User)
        .filter(
            or_(User.city.ilike(city_like), User.city.ilike(city_like)),
            User.role.in_(["carrier", "forwarder"]),
        )
        .order_by(sa_desc(User.rating))
        .limit(20)
        .all()
    )

    title = f"Перевозчики {display_city} 2024 — транспортные компании | {SITE_NAME}"
    desc = f"Найдите надёжного перевозчика в {display_city}. Проверенные транспортные компании и ИП с рейтингом на ГрузПоток."
    h1 = f"Перевозчики {display_city} — {len(carriers)} компаний на бирже"
    canonical = f"/perevozchiki/{city_slug}"
    bc = [("/", "Главная"), ("/perevozchiki/moskva", "Перевозчики"), (canonical, display_city)]

    if carriers:
        cards = ""
        for u in carriers:
            name = u.company or u.organization_name or u.fullname or "Перевозчик"
            verified = "✅ Верифицировано" if u.verified else ""
            cards += f"""<div class="card">
  <div class="card-route">{name} {verified}</div>
  <div class="card-meta">{u.city or display_city} · Рейтинг {u.rating or 5:.1f}/5 · {u.successful_deals or 0} сделок</div>
  <div style="margin-top:10px">
    <a href="/" style="padding:7px 18px;background:#1e3a8a;color:#fff;border-radius:8px;text-decoration:none;font-size:.85rem;font-weight:600">Связаться →</a>
  </div>
</div>"""
        content = f'<p class="subtitle">Найдено {len(carriers)} перевозчиков · {display_city} и регион</p>' + cards
    else:
        content = f'<div class="empty">Перевозчики {display_city} скоро появятся на платформе.</div>'

    content += '<div class="cta-block"><h2>Вы перевозчик?</h2><p>Зарегистрируйтесь и получайте заявки от грузовладельцев бесплатно.</p><a href="/" class="cta-btn">Зарегистрироваться</a></div>'

    return _page_html(title, desc, canonical, h1, content, bc)


# ── /mashiny/{body_type} ───────────────────────────────────────────────────
@router.get("/mashiny/{body_type}", response_class=HTMLResponse)
def seo_trucks(body_type: str, db: Session = Depends(get_db)):
    label = BODY_LABELS.get(body_type, body_type.title())
    bt_like = f"%{body_type}%"

    trucks = (
        db.query(Vehicle)
        .filter(
            or_(
                Vehicle.body_type.ilike(bt_like),
                Vehicle.vehicle_kind.ilike(bt_like),
            ),
            Vehicle.available_from != None,
        )
        .order_by(sa_desc(Vehicle.created_at))
        .limit(20)
        .all()
    )

    title = f"Грузовые машины {label} в аренду 2024 | {SITE_NAME}"
    desc = f"Найдите машину {label} для грузоперевозки. {len(trucks)} предложений от проверенных перевозчиков на ГрузПоток."
    h1 = f"Машины {label} — {len(trucks)} доступных автомобилей"
    canonical = f"/mashiny/{body_type}"
    bc = [("/", "Главная"), ("/mashiny/tent", "Машины"), (canonical, label)]

    if trucks:
        cards = ""
        for t in trucks:
            cap = f"{t.capacity_tons} т" if t.capacity_tons else ""
            vol = f"{t.volume_m3} м³" if t.volume_m3 else ""
            city = getattr(t, 'location_city', '') or ""
            cards += f"""<div class="card">
  <div class="card-route">🚛 {t.brand or ''} {t.model or ''} · {label}</div>
  <div class="card-meta">{city}{(" · " + cap) if cap else ""}{(" · " + vol) if vol else ""}</div>
  <div style="margin-top:10px">
    <a href="/" style="padding:7px 18px;background:#1e3a8a;color:#fff;border-radius:8px;text-decoration:none;font-size:.85rem;font-weight:600">Заказать →</a>
  </div>
</div>"""
        content = f'<p class="subtitle">Доступно {len(trucks)} машин · обновлено сегодня</p>' + cards
    else:
        content = f'<div class="empty">Машины типа {label} скоро появятся на платформе.</div>'

    body_links = "".join(
        f'<a href="/mashiny/{k}">{v}</a>'
        for k, v in BODY_LABELS.items() if k != body_type
    )
    content += f'<div class="related"><h3>Другие типы кузовов</h3><div class="related-links">{body_links}</div></div>'
    content += '<div class="cta-block"><h2>Разместите машину бесплатно</h2><p>Получайте заявки от грузовладельцев напрямую.</p><a href="/" class="cta-btn">Добавить машину</a></div>'

    return _page_html(title, desc, canonical, h1, content, bc)


# ── /sitemap.xml ───────────────────────────────────────────────────────────
from fastapi.responses import Response

@router.get('/sitemap.xml', response_class=Response)
def sitemap(db: Session = Depends(get_db)):
    """XML sitemap for Yandex/Google."""
    urls = [
        (SITE_URL + '/', '1.0', 'daily'),
        (SITE_URL + '/gruz/moskva-ekaterinburg', '0.9', 'daily'),
        (SITE_URL + '/gruz/moskva-tashkent', '0.9', 'daily'),
        (SITE_URL + '/gruz/kazan-tashkent', '0.9', 'daily'),
        (SITE_URL + '/gruz/moskva-novosibirsk', '0.9', 'daily'),
        (SITE_URL + '/gruz/moskva', '0.9', 'daily'),
        (SITE_URL + '/gruz/novosibirsk', '0.8', 'weekly'),
        (SITE_URL + '/perevozchiki/moskva', '0.8', 'weekly'),
        (SITE_URL + '/perevozchiki/ekaterinburg', '0.8', 'weekly'),
    ]
    for bt in BODY_LABELS:
        urls.append((SITE_URL + f'/mashiny/{bt}', '0.8', 'weekly'))

    # Top routes from DB
    try:
        top = db.query(Load.from_city, Load.to_city, func.count().label('c'))\
            .group_by(Load.from_city, Load.to_city)\
            .order_by(func.count().desc())\
            .limit(50).all()
        for r in top:
            if r.from_city and r.to_city:
                slug = f'{_slug(r.from_city)}-{_slug(r.to_city)}'
                urls.append((SITE_URL + f'/gruz/{slug}', '0.9', 'daily'))
    except Exception:
        pass

    xml_urls = '\n'.join(
        f'  <url><loc>{u}</loc><priority>{p}</priority><changefreq>{f}</changefreq></url>'
        for u, p, f in urls
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{xml_urls}\n</urlset>'
    return Response(content=xml, media_type='application/xml')
