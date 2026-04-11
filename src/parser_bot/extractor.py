from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(
    r"(?:"
    r"(?:\+7|7|8)\D{0,3}\d{3}\D{0,3}\d{3}\D{0,3}\d{2}\D{0,3}\d{2}"
    r"|(?:\+998)\D{0,3}\d{2}\D{0,3}\d{3}\D{0,3}\d{2}\D{0,3}\d{2}"
    r"|(?:\b9\d)\D{0,3}\d{3}\D{0,3}\d{2}\D{0,3}\d{2}\b"
    r")"
)
INN_RE = re.compile(r"\b\d{10}(?:\d{2})?\b")
ROUTE_RE = re.compile(
    r"(?P<from>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ.,\- '’ʻ`]{2,60}?)\s*(?:[)\]}])?\s*(?:>{2,}|->|=>|→|➞|➡|—|–|_{2,}|\s-\s|\s/\s|\s\|\s)\s*(?P<to>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ.,\- '’ʻ`]{2,60})",
    re.IGNORECASE,
)
ROUTE_COMPACT_RE = re.compile(
    r"\b(?P<from>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ'’ʻ`]{3,20})-(?P<to>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ'’ʻ`]{3,20})\b",
    re.IGNORECASE,
)
ROUTE_UZ_FROM_SUFFIX_RE = re.compile(
    r"\b(?P<from>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ'’ʻ`]{3,25}?)\s*(?:dan|den|дан)\s+"
    r"(?P<to>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ'’ʻ`]{3,25})\b",
    re.IGNORECASE,
)
ROUTE_UZ_SUFFIX_RE = re.compile(
    r"\b(?P<from>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ'’ʻ`]{3,25}?)\s*(?:dan|den|дан)\s+"
    r"(?P<to>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ'’ʻ`]{3,25}?)\s*(?:ga|qa|ka|га|қа|ка)\b",
    re.IGNORECASE,
)
WEIGHT_RE = re.compile(
    r"(?P<weight>\d{1,2}(?:[.,]\d+)?)\s*(?:т(?:онн|онны|онна)?|t|ton(?:na|a)?)\b",
    re.IGNORECASE,
)
PRICE_RE = re.compile(
    r"(?P<price>\d{1,3}(?:[\s.,]\d{3})+|\d{2,8}(?:[.,]\d+)?)\s*(?P<suffix>(?:к(?![A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ])|k(?![A-Za-z])|тыс\b|тыс\.(?!\w)|млн\b|мил\b|₽|(?:р|руб(?:лей)?)\b|\$|usd\b|дол(?:лар(?:ов|а)?)?\b))",
    re.IGNORECASE,
)
PRICE_BY_KEYWORD_RE = re.compile(
    r"(?:фрахт|ставка|цена|оплата)\s*[:=]?\s*(?P<price>\d{1,3}(?:[\s.,]\d{3})+|\d{5,9}(?:[.,]\d+)?)(?:\s*(?P<suffix>(?:к(?![A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ])|k(?![A-Za-z])|тыс\b|тыс\.(?!\w)|млн\b|мил\b|₽|(?:р|руб(?:лей)?)\b|\$|usd\b|дол(?:лар(?:ов|а)?)?\b)))?",
    re.IGNORECASE,
)
PRICE_BY_NDS_RE = re.compile(
    r"(?P<price>\d{1,3}(?:[\s.,]\d{3})+|\d{5,9}(?:[.,]\d+)?)\s*(?:с|без)\s*ндс",
    re.IGNORECASE,
)
NON_RUB_MILLION_HINT_RE = re.compile(
    r"(?:"
    r"сум|сўм|сумм|sum\b|som\b|so'm\b"
    r"|перечис(?:ление|л)?|пречесл|ичида|пули|берилади"
    r"|тулов|тўлов|tolov"
    r"|накд|нақд|naqd"
    r"|\+998"
    r")",
    re.IGNORECASE,
)
RUB_HINT_RE = re.compile(
    r"(?:₽|руб(?:лей)?|рос(?:сийских)?\s*руб)",
    re.IGNORECASE,
)

BODY_TYPES = {
    "тент": "тент",
    "tent": "тент",
    "реф": "рефрижератор",
    "ref": "рефрижератор",
    "рефрижератор": "рефрижератор",
    "изотерм": "изотерм",
    "борт": "борт",
    "контейнер": "контейнер",
    "шаланда": "шаланда",
    "трал": "трал",
    "площадка": "трал",
    "телега": "трал",
    "низкорамник": "трал",
    "фура": "тент",
    "fura": "тент",
    "газель": "борт",
}

CITY_STOP_WORDS = {
    "нужен",
    "нужна",
    "нужно",
    "город",
    "погрузка",
    "выгрузка",
    "груз",
    "машина",
    "срочно",
    "на",
    "в",
    "готов",
    "готово",
    "кофе",
    "тент",
    "tent",
    "реф",
    "ref",
    "рефрижератор",
    "изотерм",
    "контейнер",
    "борт",
    "трал",
    "фура",
    "fura",
    "догруз",
    "растаможка",
    "растоможка",
    "таможня",
    "перецепка",
    "перегруз",
    "kerak",
    "керак",
    "yuk",
    "tuman",
    "tumani",
    "rayon",
    "district",
    "oblast",
    "область",
    "обл",
    "г",
    "г.о",
    "г о",
    "городской",
    "городского",
    "округ",
    "округа",
    "viloyat",
    "viloyati",
    "region",
    "chiqindi",
    "poligoni",
    "poligon",
    "polygon",
    "beach",
    "sanatorium",
    "resort",
    "hotel",
    "hostel",
    "ёки",
    "еки",
    "yoki",
    "или",
    "or",
    "ru",
    "by",
    "uz",
    "kz",
    "kg",
    "cn",
    "тентлар",
    "рефлар",
}

CITY_INVALID_EXACT = {
    "оплата",
    "оптала",
    "борди",
    "bordi",
    "келди",
    "keldi",
    "нал",
    "без нала",
    "belarussia",
    "belarus",
    "russia",
    "россия",
    "узбекистан",
    "казахстан",
    "кыргызстан",
    "киргизия",
    "беларусь",
    "белоруссия",
    "беларуссия",
    "погрузка",
    "выгрузка",
    "кофе готов",
    "перечис",
    "перечисление",
    "перечисл",
    "растаможка",
    "растоможка",
    "верхняя",
    "боковая",
    "тулов",
    "тўлов",
    "tolov",
    "юк",
    "накд",
    "нақд",
    "naqd",
    "тентлар",
    "рефлар",
    "cn",
    "ru",
    "uz",
    "kz",
    "kg",
    "by",
    "рф",
    "сегодня",
    "завтра",
    "послезавтра",
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
    "юклаш",
    "yuklash",
    "юқори",
}

CITY_INVALID_PREFIX_WORDS = {
    "ооо",
    "ип",
    "ук",
    "ao",
    "ooo",
    "llc",
    "company",
    "компания",
}

CITY_INVALID_BUSINESS_WORDS = {
    "строй",
    "логистик",
    "логистика",
    "транс",
    "карго",
    "cargo",
    "экспресс",
    "express",
    "group",
    "групп",
    "группа",
    "beach",
    "sanatorium",
    "resort",
    "hotel",
    "hostel",
    "warehouse",
    "terminal",
    "poligoni",
    "poligon",
    "polygon",
    "chiqindi",
    "tuman",
    "tumani",
    "rayon",
    "district",
    "oblast",
    "region",
    "область",
    "район",
}

_STACKED_ROUTE_KEEP_MULTIWORD_KEYS = {
    "санкт петербург",
    "нижний новгород",
    "ростов на дону",
    "йошкар ола",
}

_STACKED_ROUTE_PREFIX_TOKENS = {
    "узб",
    "узбекистан",
    "россия",
    "рф",
    "рб",
    "беларусь",
    "белоруссия",
    "казахстан",
    "кыргызстан",
    "киргизия",
    "uzb",
    "uz",
    "ru",
    "kg",
    "kz",
    "by",
}

_STACKED_ROUTE_STOP_TOKENS = {
    "тент",
    "реф",
    "фура",
    "tent",
    "ref",
    "fura",
    "yuk",
    "yoki",
    "груз",
    "машина",
    "мдф",
    "дсп",
    "аванс",
    "оплата",
    "нал",
    "нахт",
    "naqd",
    "nakd",
    "тонна",
    "тонн",
    "тона",
    "tona",
    "ton",
}

CITY_ALIASES = {
    "мск": "Москва",
    "москва": "Москва",
    "спб": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "санкт-петербург": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "екб": "Екатеринбург",
    "нн": "Нижний Новгород",
    "нижний новогород": "Нижний Новгород",
    "краснадар": "Краснодар",
    "городно": "Гродно",
    "piter": "Санкт-Петербург",
    "барисов": "Борисов",
    "иванцаевич": "Ивацевичи",
    "ивацевичи": "Ивацевичи",
    "новогрудок": "Новогрудок",
    "сморгонь": "Сморгонь",
    "йошкар ола": "Йошкар-Ола",
    "йошкар-ола": "Йошкар-Ола",
    "ростов на дону": "Ростов-на-Дону",
    "ростов-на-дону": "Ростов-на-Дону",
    "ташкен": "Ташкент",
    "ташкенд": "Ташкент",
    "тошкент": "Ташкент",
    "toshkent": "Ташкент",
    "сырдаря": "Сырдарья",
    "сирдарё": "Сырдарья",
    "бухоро": "Бухара",
    "buxoro": "Бухара",
    "самарқанд": "Самарканд",
    "самарканд": "Самарканд",
    "samarqand": "Самарканд",
    "жиззах": "Джизак",
    "карши": "Карши",
    "нукус": "Нукус",
    "водий": "Водий",
    "хоразм": "Ургенч",
    "фаргона": "Фергана",
    "фарғона": "Фергана",
    "фергана": "Фергана",
    "namangan": "Наманган",
    "andijon": "Андижан",
    "jizzax": "Джизак",
    "qashqadaryo": "Кашкадарья",
    "qoqon": "Коканд",
    "xorazm": "Ургенч",
    "хорезм": "Ургенч",
    "fargona": "Фергана",
    "farg'ona": "Фергана",
    "fargʻona": "Фергана",
    "farg’ona": "Фергана",
    "андижон": "Андижан",
    "сурхандарё": "Сурхандарья",
    "сурхандарья": "Сурхандарья",
    "surxondaryo": "Сурхандарья",
    "кашкадарё": "Кашкадарья",
    "маргилон": "Маргилан",
    "олмалиқ": "Алмалык",
    "денов": "Денау",
    "хонобод": "Ханабад",
    "кувасой": "Кувасай",
    "бобруйск": "Бобруйск",
    "барановичи": "Барановичи",
    "могилёв": "Могилев",
    "актобе": "Актобе",
    "актюбинск": "Актобе",
    "уральск": "Уральск",
    "тараз": "Тараз",
    "джамбул": "Тараз",
    "семей": "Семей",
    "семипалатинск": "Семей",
    "шимкент": "Шымкент",
    "чимкент": "Шымкент",
    "нур-султан": "Астана",
    "нурсултан": "Астана",
    "душанбе": "Душанбе",
    "худжанд": "Худжанд",
    "ходжент": "Худжанд",
    "куляб": "Куляб",
    "бишкек": "Бишкек",
    "баку": "Баку",
    "тбилиси": "Тбилиси",
    "ереван": "Ереван",
    "стамбул": "Стамбул",
    "istanbul": "Стамбул",
    "табольск": "Тобольск",
    "газган": "Газган",
    "алашань": "Алашань",
    "алашанькоу": "Алашанькоу",
    "хоргос": "Хоргос",
}

CITY_ALIAS_INDEX: dict[str, set[str]] = {}
for _alias, _city in CITY_ALIASES.items():
    _city_key = _city.lower()
    CITY_ALIAS_INDEX.setdefault(_city_key, set()).add(_alias.lower())
    CITY_ALIAS_INDEX[_city_key].add(_city_key)

_LLM_SYSTEM_PROMPT = """\
Ты — парсер сообщений о грузоперевозках. Работаешь как опытный диспетчер: \
понимаешь сленг, исправляешь опечатки, вычисляешь даты.

Из текста извлеки ВСЕ возможные данные и верни СТРОГО JSON (без пояснений, без markdown):
{{
  "from_city": "Город отправления (полное название с большой буквы)",
  "to_city": "Город назначения (полное название с большой буквы)",
  "body_type": "Тип кузова/транспорта",
  "weight": число в тоннах (float),
  "rate": число в рублях (int),
  "load_date": "YYYY-MM-DD",
  "load_time": "HH:MM",
  "phone": "телефон в формате +7XXXXXXXXXX",
  "cargo_description": "краткое описание груза, если указано",
  "payment_terms": "условия оплаты",
  "is_direct_customer": true/false,
  "dimensions": "ДxШxВ в метрах, если указаны"
}}

Правила парсинга:

ГОРОДА:
- Исправляй опечатки: «самар» → Самара, «масква» → Москва.
- Сокращения: мск=Москва, спб/питер=Санкт-Петербург, екб=Екатеринбург, \
нск=Новосибирск, рнд=Ростов-на-Дону, нн=Нижний Новгород, крд=Краснодар, \
крск=Красноярск, кзн=Казань, чел/челяба=Челябинск, врн=Воронеж, \
тмн=Тюмень, влг=Волгоград.
- Никогда не считай городами слова: «Оплата», «Нал», «Без нала», «Растаможка», \
«Растоможка», «Верхняя», «Боковая».
- Если не уверен в городе или это похоже на условие оплаты/комментарий, \
не выдумывай маршрут и не заполняй from_city/to_city.
- Если указан регион вместо города (Сибирь, Урал, Поволжье, Юг, Центр, \
Северо-Запад, Дальний Восток, Кубань, Кавказ), верни крупнейший город \
региона: Сибирь→Новосибирск, Урал→Екатеринбург, Поволжье→Самара, \
Юг/Кубань/Кавказ→Краснодар, Центр→Москва, Северо-Запад→Санкт-Петербург, \
Дальний Восток→Хабаровск.

ТИП КУЗОВА:
- площадка / телега / низкорамник / негабарит → трал
- фура / еврофура / полуприцеп → тент
- газель / газелька / малотоннажник → борт
- термос → изотерм
- холодильник → рефрижератор
- Стандартные: тент, рефрижератор, изотерм, борт, контейнер, шаланда, трал.

ДАТЫ:
- Сегодня: {today}, день недели: {weekday}.
- «сегодня» = {today}, «завтра» = {tomorrow}, «послезавтра» = {day_after}.
- «в понедельник» / «в пн» / «в субботу» — вычисли ближайшую будущую дату.
- «20 числа» / «20-го» — текущий или следующий месяц (если 20-е уже прошло).
- «через 3 дня» — прибавь к {today}.

ЦЕНА:
- 120к / 120K = 120000; 50 тыс = 50000; 80к с ндс → rate=80000.
- Если указана ставка за км — умножь примерно: «35 руб/км» при ~1000 км → 35000.

ТЕЛЕФОН:
- Извлеки номер, даже если написан словами: «восемь девятьсот…» → +79...
- Форматы: +7(...), 8-..., 8 (...), без пробелов — приведи к +7XXXXXXXXXX.

ОПИСАНИЕ ГРУЗА:
- Если указан тип/характер груза (напр. «трубы», «стройматериалы», \
«продукты», «оборудование»), верни в cargo_description.

УСЛОВИЯ ОПЛАТЫ (payment_terms):
- «с НДС» / «без НДС» / «нал» / «безнал» / «предоплата» / «по факту» / \
«на карту» / «карта» — извлеки как строку, напр. "без НДС, нал".

ПРЯМОЙ ЗАКАЗЧИК (is_direct_customer):
- true если текст указывает на прямого заказчика/завод/производителя \
(«от завода», «напрямую», «без посредников», «собственный груз»).
- false если посредник/диспетчер/экспедитор («диспетчер», «экспедитор», \
«ищем машину для клиента»).
- Не включай поле, если невозможно определить.

ГАБАРИТЫ (dimensions):
- Если указаны размеры груза (длина, ширина, высота), верни строкой: \
«6x2.4x2.5» (м). Примеры: «длина 12м» → «12», «6*2.4*2.5» → «6x2.4x2.5».

ВАЖНО:
- Если поле не удалось извлечь — НЕ включай его в JSON.
- Верни ТОЛЬКО JSON-объект, ничего больше.\
"""

_WEEKDAY_NAMES_RU = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]

_ROUTE_NOISE_RE = re.compile(r"[⛳⚖📄💵📦📞🔺❇‼🟡🟢🔴⛽🛢🚛✅🧑💻🔜]+|[\uFE0F]", re.UNICODE)
_COUNTRY_CODE_SUFFIX_RE = re.compile(r",\s*(?:RU|BY|UZ|KZ|KG|CN)\b", re.IGNORECASE)
_CITY_OPTION_MARKERS = {"ёки", "еки", "yoki", "или", "or"}


@dataclass(slots=True)
class ParsedCargo:
    from_city: str
    to_city: str
    body_type: str | None
    rate_rub: int | None
    weight_t: float | None
    phone: str | None
    inn: str | None
    matched_keywords: list[str]
    raw_text: str
    load_date: str | None = None
    load_time: str | None = None
    cargo_description: str | None = None
    payment_terms: str | None = None
    is_direct_customer: bool | None = None
    dimensions: str | None = None
    is_hot_deal: bool = False
    suggested_response: str | None = None
    phone_blacklisted: bool = False
    route_distance_km: int | None = None
    from_lat: float | None = None
    from_lon: float | None = None
    to_lat: float | None = None
    to_lon: float | None = None


def _normalize_city(value: str) -> str:
    head = (value or "").split(",", 1)[0]
    words = [
        w.strip(".,:;()[]{}-–—><")
        for w in head.replace("ё", "е").replace("Ё", "Е").split()
    ]
    words = [w for w in words if w]
    for idx, word in enumerate(words[1:], start=1):
        if word.lower() in _CITY_OPTION_MARKERS:
            words = words[:idx]
            break
    if len(words) >= 2 and tuple(word.lower() for word in words[:2]) in {
        ("г", "о"),
        ("городской", "округ"),
        ("городского", "округа"),
        ("городское", "поселение"),
        ("городского", "поселения"),
    }:
        words = words[2:]
    if len(words) >= 2 and tuple(word.lower() for word in words[:2]) in {
        ("г", "п"),
        ("городской", "поселок"),
        ("городской", "посёлок"),
    }:
        words = words[2:]
    while words and words[0].lower() in CITY_STOP_WORDS:
        words.pop(0)
    while words and words[-1].lower() in CITY_STOP_WORDS:
        words.pop()
    if len(words) > 2:
        words = words[-2:]
    raw = " ".join(words).strip()
    if not raw:
        return ""
    lookup = raw.lower().strip(".")
    lookup = lookup.replace("ʻ", "'").replace("’", "'").replace("`", "'")
    lookup_variants = {
        lookup,
        lookup.replace("-", " "),
        lookup.replace(" ", "-"),
    }
    for variant in lookup_variants:
        if variant in CITY_ALIASES:
            return CITY_ALIASES[variant]
    return raw.title()


def _city_key(value: str) -> str:
    key = (value or "").strip().lower()
    key = key.replace("ё", "е").replace("-", " ")
    key = re.sub(r"[^0-9a-zа-яқғўҳүұ\\s'’ʻ`]", " ", key)
    key = key.replace("ʻ", "'").replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", key).strip()


def _is_invalid_city_name(value: str) -> bool:
    key = _city_key(value)
    if key in CITY_INVALID_EXACT:
        return True
    words = key.split()
    if not words:
        return True
    if len(words) == 1 and words[0].endswith(("ская", "ский", "ское")):
        return True
    if len(words) >= 2 and words[0] in CITY_INVALID_PREFIX_WORDS:
        return True
    if len(words) >= 2 and words[-1] in CITY_INVALID_BUSINESS_WORDS:
        return True
    if len(words) >= 2 and any(word in CITY_INVALID_BUSINESS_WORDS for word in words):
        return True
    return False


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 9 and digits.startswith("9"):
        return f"+998{digits}"
    if len(digits) == 12 and digits.startswith("998"):
        return f"+{digits}"
    if len(digits) == 10:
        digits = f"7{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    if len(digits) != 11:
        return digits
    return f"+{digits}"


def _normalize_inn(value: str) -> str | None:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) not in (10, 12):
        return None
    return digits


def _extract_inn(text: str, *, phone: str | None = None) -> str | None:
    phone_digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    for match in INN_RE.finditer(text):
        candidate = _normalize_inn(match.group(0))
        if not candidate:
            continue
        if candidate.startswith("998") and len(candidate) == 12:
            continue
        if phone_digits and candidate == phone_digits:
            continue
        return candidate
    return None


def _parse_price(text: str) -> int | None:
    usd_to_rub = 100
    text_lc = (text or "").lower()
    has_non_rub_million_hint = bool(NON_RUB_MILLION_HINT_RE.search(text_lc))
    has_rub_hint = bool(RUB_HINT_RE.search(text_lc))

    def _parse_amount(raw: str, *, suffix: str = "") -> int | None:
        token = (raw or "").strip().replace("\xa0", " ")
        if not token:
            return None

        multiplier = 1
        suffix_norm = (suffix or "").strip().lower()
        if suffix_norm in {"к", "k", "тыс", "тыс."}:
            multiplier = 1000
        elif suffix_norm in {"млн", "мил"}:
            if has_non_rub_million_hint and not has_rub_hint:
                return None
            multiplier = 1_000_000
        elif suffix_norm in {"$", "usd", "дол", "доллар", "доллара", "долларов"}:
            multiplier = usd_to_rub

        compact = token.replace(" ", "")
        if multiplier == 1 and compact.isdigit() and len(compact) >= 8:
            # Long bare numeric ids in chats are usually references, not rates.
            return None
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", compact):
            digits = re.sub(r"\D", "", compact)
            return int(digits) * multiplier if digits else None

        numeric = re.sub(r"[^0-9.,]", "", compact)
        if not numeric:
            return None

        if numeric.count(".") + numeric.count(",") > 1:
            digits = re.sub(r"\D", "", numeric)
            return int(digits) * multiplier if digits else None

        value = float(numeric.replace(",", "."))
        return int(round(value * multiplier))

    match = PRICE_RE.search(text)
    if match:
        parsed = _parse_amount(match.group("price"), suffix=match.group("suffix") or "")
        if parsed:
            return parsed

    keyword_match = PRICE_BY_KEYWORD_RE.search(text)
    if keyword_match:
        parsed = _parse_amount(
            keyword_match.group("price"),
            suffix=keyword_match.group("suffix") or "",
        )
        if parsed:
            return parsed

    nds_match = PRICE_BY_NDS_RE.search(text)
    if nds_match:
        parsed = _parse_amount(nds_match.group("price"))
        if parsed:
            return parsed

    return None


def _parse_weight(text: str) -> float | None:
    match = WEIGHT_RE.search(text)
    if not match:
        return None
    return float(match.group("weight").replace(",", "."))


def _parse_body_type(text_lc: str) -> str | None:
    for token, norm in BODY_TYPES.items():
        if token in text_lc:
            return norm
    return None


def _parse_route(text: str) -> tuple[str, str] | tuple[None, None]:
    route_text = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", text or "")
    route_text = _ROUTE_NOISE_RE.sub(" ", route_text)
    route_text = _COUNTRY_CODE_SUFFIX_RE.sub("", route_text)
    route_text = route_text.replace('"', " ")
    route_text = re.sub(r"[ \t]+", " ", route_text).strip()

    candidates: list[tuple[int, str, str]] = []

    for route in ROUTE_RE.finditer(route_text):
        from_city = _normalize_city(route.group("from"))
        to_city = _normalize_city(route.group("to"))
        if "/" in route.group(0):
            from_key = _city_key(from_city)
            to_key = _city_key(to_city)
            if from_key not in CITY_ALIASES and to_key not in CITY_ALIASES:
                continue
        if from_city and to_city and not _is_invalid_city_name(from_city) and not _is_invalid_city_name(to_city):
            candidates.append((route.start(), from_city, to_city))

    for compact in ROUTE_COMPACT_RE.finditer(route_text):
        compact_token = f"{compact.group('from')}-{compact.group('to')}"
        compact_lookup = compact_token.lower().replace("ʻ", "'").replace("’", "'").replace("`", "'")
        if compact_lookup in CITY_ALIASES or compact_lookup.replace("-", " ") in CITY_ALIASES:
            continue
        from_city = _normalize_city(compact.group("from"))
        to_city = _normalize_city(compact.group("to"))
        if from_city and to_city and not _is_invalid_city_name(from_city) and not _is_invalid_city_name(to_city):
            candidates.append((compact.start(), from_city, to_city))

    for suffix_route in ROUTE_UZ_FROM_SUFFIX_RE.finditer(route_text):
        raw_to = (suffix_route.group("to") or "").strip().lower()
        if raw_to.endswith(("ga", "qa", "ka", "га", "қа", "ка")):
            continue
        from_city = _normalize_city(suffix_route.group("from"))
        to_city = _normalize_city(suffix_route.group("to"))
        if from_city and to_city and not _is_invalid_city_name(from_city) and not _is_invalid_city_name(to_city):
            candidates.append((suffix_route.start(), from_city, to_city))

    for suffix_route in ROUTE_UZ_SUFFIX_RE.finditer(route_text):
        from_city = _normalize_city(suffix_route.group("from"))
        to_city = _normalize_city(suffix_route.group("to"))
        if from_city and to_city and not _is_invalid_city_name(from_city) and not _is_invalid_city_name(to_city):
            candidates.append((suffix_route.start(), from_city, to_city))

    if candidates:
        candidates.sort(key=lambda item: item[0])
        _, from_city, to_city = candidates[0]
        return from_city, to_city

    return None, None


def _extract_matched_keywords(text_lc: str, keywords: Iterable[str]) -> list[str]:
    found: list[str] = []
    for word in keywords:
        marker = (word or "").strip().lower()
        if marker and marker in text_lc:
            found.append(marker)
    return found


def parse_cargo_message(text: str, *, keywords: Iterable[str]) -> ParsedCargo | None:
    clean_text = (text or "").strip()
    if not clean_text:
        return None

    from_city, to_city = _parse_route(clean_text)
    if not from_city or not to_city:
        return None

    text_lc = clean_text.lower()
    matched_keywords = _extract_matched_keywords(text_lc, keywords)
    if not matched_keywords:
        if not looks_like_cargo(clean_text):
            return None
        matched_keywords = ["auto"]

    phone_match = PHONE_RE.search(clean_text)
    phone = _normalize_phone(phone_match.group(0)) if phone_match else None
    inn = _extract_inn(clean_text, phone=phone)

    return ParsedCargo(
        from_city=from_city,
        to_city=to_city,
        body_type=_parse_body_type(text_lc),
        rate_rub=_parse_price(clean_text),
        weight_t=_parse_weight(clean_text),
        phone=phone,
        inn=inn,
        matched_keywords=matched_keywords,
        raw_text=clean_text,
    )


def _build_llm_system_prompt() -> str:
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    day_after = today + timedelta(days=2)
    weekday = _WEEKDAY_NAMES_RU[today.weekday()]
    return _LLM_SYSTEM_PROMPT.format(
        today=today.strftime("%Y-%m-%d"),
        tomorrow=tomorrow.strftime("%Y-%m-%d"),
        day_after=day_after.strftime("%Y-%m-%d"),
        weekday=weekday,
    )


def _extract_json(text: str) -> dict | None:
    """Safely extract a JSON object from LLM output (removes markdown, <think>, etc.)."""
    import re
    cleaned = text.strip()
    
    if "<think>" in cleaned:
        cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL).strip()
        
    cleaned = re.sub(r'^```[a-zA-Z]*\n?', '', cleaned)
    cleaned = re.sub(r'\n?```$', '', cleaned)
    
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    
    if start == -1 or end == -1 or end <= start:
        return None
        
    json_str = cleaned[start : end + 1]
    
    try:
        return json.loads(json_str)
    except Exception as e:
        try:
            # Fix unescaped newlines inside JSON strings
            fixed_str = json_str.replace('\n', ' ')
            return json.loads(fixed_str)
        except Exception:
            return None

async def parse_cargo_message_llm(
    text: str, *, keywords: Iterable[str]
) -> ParsedCargo | None:
    """Parse a cargo message using LLM, falling back to regex.

    Includes a lightweight pre-filter: if the text has no logistics
    signal words at all, it is skipped before the LLM is called.
    This saves 3-5x on API costs by filtering chat noise.

    Supports two LLM providers:
    - **Groq** (preferred): set ``GROQ_API_KEY``.
    - **OpenAI** fallback: set ``OPENAI_API_KEY`` (uses ``OPENAI_MODEL``,
      default ``gpt-4o-mini``).

    Falls back to the regex-based ``parse_cargo_message`` when no API
    key is configured or the LLM call fails.
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return None

    if not looks_like_cargo(clean_text):
        logger.debug("pre-filter skip (no cargo signals): %.60s…", clean_text)
        return None

    from src.core.config import settings
    from src.parser_bot.truck_extractor import (
        _call_gemini,
        _call_groq,
        _call_openai,
    )

    gemini_key = getattr(settings, "gemini_api_key", None)
    if not isinstance(gemini_key, str) or not gemini_key:
        gemini_key = None
    has_openai = bool(getattr(settings, "openai_api_key", None))
    has_groq = bool(getattr(settings, "groq_api_key", None))

    if not gemini_key and not has_openai and not has_groq:
        return parse_cargo_message(clean_text, keywords=keywords)

    try:
        system_prompt = _build_llm_system_prompt()

        if gemini_key:
            gemini_model = getattr(settings, "gemini_model", None) or "gemini-2.0-flash"
            llm_output = await _call_gemini(
                system_prompt, clean_text, gemini_key, gemini_model
            )
            provider = f"gemini/{gemini_model}"
        elif has_openai:
            openai_model = getattr(settings, "openai_model", None) or "gpt-4o-mini"
            llm_output = await _call_openai(
                system_prompt, clean_text, settings.openai_api_key, openai_model
            )
            provider = f"openai/{openai_model}"
        else:
            llm_output = await _call_groq(
                system_prompt, clean_text, settings.groq_api_key
            )
            provider = "groq/llama-3.1-8b-instant"

        logger.info("LLM extractor [%s] raw: %s", provider, llm_output)

        data = _extract_json(llm_output)
        if data is None:
            logger.warning("LLM returned non-JSON, falling back to regex")
            return parse_cargo_message(clean_text, keywords=keywords)

        parsed = _llm_result_to_parsed(data, clean_text, keywords=keywords)
        if parsed is None:
            logger.warning("LLM result missing route, falling back to regex")
            return parse_cargo_message(clean_text, keywords=keywords)

        parsed.is_hot_deal = evaluate_hot_deal(parsed)

        logger.info(
            "LLM parsed [%s]: %s → %s, body=%s, wt=%s, rate=%s, "
            "date=%s %s, desc=%s, pay=%s, direct=%s, hot=%s",
            provider,
            parsed.from_city,
            parsed.to_city,
            parsed.body_type,
            parsed.weight_t,
            parsed.rate_rub,
            parsed.load_date,
            parsed.load_time,
            parsed.cargo_description,
            parsed.payment_terms,
            parsed.is_direct_customer,
            parsed.is_hot_deal,
        )
        return parsed

    except Exception as exc:
        logger.warning("LLM extractor failed (%s), falling back to regex", exc)
        return parse_cargo_message(clean_text, keywords=keywords)


def build_dedupe_key(parsed: ParsedCargo, *, chat_id: int | str, fallback_id: str) -> str:
    phone_part = (parsed.phone or "").strip().lower()
    from_part = parsed.from_city.strip().lower()
    to_part = parsed.to_city.strip().lower()
    if phone_part:
        stable = f"{phone_part}|{from_part}|{to_part}"
    else:
        stable = f"{from_part}|{to_part}|{fallback_id}"
    digest = hashlib.sha1(stable.encode("utf-8")).hexdigest()
    return f"parser-dedupe:{chat_id}:{digest}"


def build_content_dedupe_key(parsed: ParsedCargo) -> str:
    """Build a cross-chat content-based dedupe key.

    Catches the same cargo posted by different dispatchers or across
    multiple chats.  Uses route + weight + body_type + date so that
    ``трал 20т Самара-Москва завтра`` is recognized as a duplicate
    regardless of who posted it or in which chat.
    """
    parts = [
        parsed.from_city.strip().lower(),
        parsed.to_city.strip().lower(),
        str(parsed.weight_t or ""),
        (parsed.body_type or "").strip().lower(),
        (parsed.load_date or "").strip(),
    ]
    stable = "|".join(parts)
    digest = hashlib.sha1(stable.encode("utf-8")).hexdigest()
    return f"parser-content-dedupe:{digest}"


# ---------------------------------------------------------------------------
# looks_like_cargo — lightweight pre-filter for LLM calls
# ---------------------------------------------------------------------------

_CARGO_STRONG_RE = re.compile(
    r"(?:"
    r"\d+\s*(?:т(?:онн|онны|онна)?)\b"
    r"|[→➞➡]"
    r"|>{2,}"
    r"|\b(?:тент|реф(?:рижератор)?|трал|борт|контейнер|фура|газель|изотерм|шаланда)\b"
    r"|\b(?:ставка|фрахт|погрузка|выгрузка|загрузка)\b"
    r")",
    re.IGNORECASE | re.UNICODE,
)

_CARGO_WEAK_RE = re.compile(
    r"\b(?:груз|машина|авто|тягач|перевоз|безнал|ндс|предоплат|нал)\b",
    re.IGNORECASE,
)


def looks_like_cargo(text: str) -> bool:
    """Return True if text has at least one strong logistics signal,
    or two or more weak signals.  Used as a cheap pre-filter before LLM calls."""
    if len(text.strip()) < 10:
        return False
    if _CARGO_STRONG_RE.search(text):
        return True
    weak = _CARGO_WEAK_RE.findall(text.lower())
    return len(weak) >= 2


# ---------------------------------------------------------------------------
# _llm_result_to_parsed — convert LLM JSON dict → ParsedCargo
# ---------------------------------------------------------------------------

_LLM_PLACEHOLDER_CITIES: frozenset[str] = frozenset({
    "нет данных", "не указан", "не указано", "неизвестно",
    "не установлен", "не определён", "не определен",
    "null", "none", "n/a", "n/d", "этрн",
})


def _llm_result_to_parsed(
    data: dict, raw_text: str, *, keywords: Iterable[str]
) -> "ParsedCargo | None":
    """Convert a dict produced by LLM JSON output into a ParsedCargo instance."""
    from_city = (data.get("from_city") or "").strip()
    to_city = (data.get("to_city") or "").strip()

    if not from_city or not to_city:
        return None

    if (
        from_city.lower() in _LLM_PLACEHOLDER_CITIES
        or to_city.lower() in _LLM_PLACEHOLDER_CITIES
    ):
        return None

    if _is_invalid_city_name(from_city) or _is_invalid_city_name(to_city):
        return None

    body_type_raw = (data.get("body_type") or "").strip().lower()
    body_type = BODY_TYPES.get(body_type_raw) if body_type_raw else None

    weight_raw = data.get("weight")
    weight_t: float | None = None
    if weight_raw is not None:
        try:
            weight_t = float(str(weight_raw).replace(",", "."))
        except (ValueError, TypeError):
            weight_t = _parse_weight(raw_text)

    rate_raw = data.get("rate")
    rate_rub: int | None = None
    if rate_raw is not None:
        try:
            rate_rub = int(rate_raw)
        except (ValueError, TypeError):
            rate_rub = _parse_price(raw_text)

    phone_raw = (data.get("phone") or "").strip()
    if phone_raw:
        phone: str | None = _normalize_phone(phone_raw)
    else:
        phone_match = PHONE_RE.search(raw_text)
        phone = _normalize_phone(phone_match.group(0)) if phone_match else None

    inn = _extract_inn(raw_text, phone=phone)
    text_lc = raw_text.lower()
    matched_keywords = _extract_matched_keywords(text_lc, keywords) or ["auto"]

    cargo_desc = (data.get("cargo_description") or "").strip() or None
    payment_terms = (data.get("payment_terms") or "").strip() or None

    dc_raw = data.get("is_direct_customer")
    is_direct_customer: bool | None = None
    if dc_raw is not None:
        if isinstance(dc_raw, bool):
            is_direct_customer = dc_raw
        elif isinstance(dc_raw, str):
            is_direct_customer = dc_raw.lower() in ("true", "1", "yes", "да")
        else:
            is_direct_customer = bool(dc_raw)

    dimensions = (data.get("dimensions") or "").strip() or None

    return ParsedCargo(
        from_city=from_city,
        to_city=to_city,
        body_type=body_type,
        rate_rub=rate_rub,
        weight_t=weight_t,
        phone=phone,
        inn=inn,
        matched_keywords=matched_keywords,
        raw_text=raw_text,
        load_date=data.get("load_date") or None,
        load_time=data.get("load_time") or None,
        cargo_description=cargo_desc,
        payment_terms=payment_terms,
        is_direct_customer=is_direct_customer,
        dimensions=dimensions,
    )


# ---------------------------------------------------------------------------
# evaluate_hot_deal — rate-per-km threshold check
# ---------------------------------------------------------------------------

_HOT_DEAL_RATE_THRESHOLD = 100  # rub/km


def evaluate_hot_deal(parsed: "ParsedCargo") -> bool:
    """Return True if the cargo rate is notably above the market rate-per-km."""
    if not parsed.rate_rub:
        return False
    try:
        from src.core.geo import city_coords, haversine_km

        from_c = city_coords(parsed.from_city)
        to_c = city_coords(parsed.to_city)
        if not from_c or not to_c:
            return False
        dist = haversine_km(from_c[0], from_c[1], to_c[0], to_c[1])
        if dist < 100:
            return False
        return (parsed.rate_rub / dist) >= _HOT_DEAL_RATE_THRESHOLD
    except Exception:
        return False


# ---------------------------------------------------------------------------
# contains_invalid_geo_token — detect payment/noise strings in route text
# ---------------------------------------------------------------------------

_INVALID_GEO_TOKEN_RE = re.compile(
    r"(?:"
    r"опла(?:та|ты|тить|т)\b"
    r"|опта(?:ла|ло|лы)\b"
    r"|перечис(?:ление|л)?\b"
    r"|без\s*нала?"
    r"|безнал\b"
    r"|\bнал\b"
    r"|накд|нақд|naqd|nakd"
    r"|тулов|тўлов|tolov"
    r"|\bборди\b|\bkeldi\b"
    r")",
    re.IGNORECASE,
)


def contains_invalid_geo_token(text: str) -> bool:
    """Return True if text contains payment or noise tokens
    that should never appear as a city/geo name."""
    return bool(_INVALID_GEO_TOKEN_RE.search(text))


# ---------------------------------------------------------------------------
# split_cargo_message_blocks — multi-route message splitter
# ---------------------------------------------------------------------------

_FLAG_EMOJI_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")


def _extract_cities_from_stacked_line(line: str) -> list[str]:
    """Extract normalised city names from a flag-emoji-prefixed stacked line."""
    clean = _FLAG_EMOJI_RE.sub("", line)
    clean = re.sub(r"[^\w\s\-'ʻ`.,]", " ", clean, flags=re.UNICODE)
    clean = re.sub(r"\s+", " ", clean).strip()
    cities: list[str] = []
    for token in clean.split():
        token = token.strip(".,:-")
        if not token or len(token) < 3:
            continue
        city = _normalize_city(token)
        if city and not _is_invalid_city_name(city):
            cities.append(city)
    return cities


def _expand_stacked_cities(lines: list[str], flag_indices: list[int]) -> list[str]:
    """Expand a stacked city-list post into individual route blocks."""
    groups: list[list[str]] = []
    current_flag: str | None = None
    current_cities: list[str] = []

    for idx in flag_indices:
        line = lines[idx]
        flags = _FLAG_EMOJI_RE.findall(line)
        flag = flags[0] if flags else ""
        cities = _extract_cities_from_stacked_line(line)
        if flag != current_flag:
            if current_cities:
                groups.append(current_cities)
            current_flag = flag
            current_cities = list(cities)
        else:
            current_cities.extend(cities)

    if current_cities:
        groups.append(current_cities)

    if len(groups) < 2:
        return ["\n".join(lines).strip()]

    max_flag_idx = max(flag_indices)
    tail_lines = [lines[i] for i in range(max_flag_idx + 1, len(lines)) if lines[i].strip()]
    tail = "\n".join(tail_lines).strip()

    from_city = groups[0][-1]  # last (most specific) city on the origin line
    blocks: list[str] = []
    for to_cities in groups[1:]:
        for to_city in to_cities:
            route_line = f"{from_city} - {to_city}"
            block = route_line + ("\n" + tail if tail else "")
            blocks.append(block)

    return blocks if blocks else ["\n".join(lines).strip()]


def split_cargo_message_blocks(text: str) -> list[str]:
    """Split a multi-route message into individual cargo blocks.

    Handles two formats:

    1. Multiple paragraphs separated by blank lines, each with its own route.
    2. Stacked city-list format (flag emoji + city names on separate lines).
    """
    clean = (text or "").strip()
    if not clean:
        return []

    lines = clean.split("\n")
    flag_indices = [i for i, ln in enumerate(lines) if _FLAG_EMOJI_RE.search(ln)]

    if len(flag_indices) >= 2:
        return _expand_stacked_cities(lines, flag_indices)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]
    if len(paragraphs) >= 2 and all(_parse_route(p)[0] is not None for p in paragraphs):
        return paragraphs

    return [clean]
