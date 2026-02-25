from __future__ import annotations

VEHICLE_KINDS: dict[str, dict[str, str]] = {
    "EUROFURA_TENT_20T": {"label": "Еврофура (тент) 20т", "group": "Универсальные", "body_type": "тент"},
    "JUMBO": {"label": "Jumbo (джамбо)", "group": "Универсальные", "body_type": "тент"},
    "REFRIGERATOR": {"label": "Рефрижератор", "group": "Температурные", "body_type": "реф"},
    "ISOTHERM": {"label": "Изотерм", "group": "Температурные", "body_type": "изотерм"},
    "DUMP_TRUCK": {"label": "Самосвал", "group": "Сыпучие/наливные", "body_type": "самосвал"},
    "TANKER": {"label": "Цистерна", "group": "Сыпучие/наливные", "body_type": "цистерна"},
    "CONTAINER_CARRIER": {"label": "Контейнеровоз", "group": "Платформы/контейнеры", "body_type": "контейнеровоз"},
    "FLATBED": {"label": "Платформа/борт", "group": "Платформы/контейнеры", "body_type": "платформа"},
    "LOWBOY_TRAL": {"label": "Трал (низкорамный)", "group": "Негабарит", "body_type": "трал"},
    "VAN_UP_TO_3_5T": {"label": "Фургон до 3.5т (город)", "group": "Малотоннажные", "body_type": "фургон"},
    "CAR_CARRIER": {"label": "Автовоз", "group": "Спецтехника", "body_type": "автовоз"},
    "TIMBER_TRUCK": {"label": "Лесовоз/сортиментовоз", "group": "Спецтехника", "body_type": "лесовоз"},
    "MANIPULATOR": {"label": "Манипулятор", "group": "Спецтехника", "body_type": "манипулятор"},
}

LOADING_TYPES: dict[str, str] = {
    "top": "Верхняя",
    "side": "Боковая",
    "back": "Задняя",
}

VEHICLE_OPTIONS: dict[str, str] = {
    "liftgate": "Гидроборт",
    "conics": "Коники",
    "adr": "ADR (опасные)",
    "reefer_unit": "Холодильная установка",
    "straps": "Ремни",
    "tarpaulin": "Тент",
    "pallet_jack": "Рокла",
    "crane": "Кран (манипулятор)",
    "oversize_ok": "Негабарит допускается",
    "city_access": "Пропуск/въезд",
}

ADR_CLASSES: list[str] = ["1", "2", "3", "4.1", "4.2", "4.3", "5.1", "5.2", "6.1", "6.2", "7", "8", "9"]

BODY_TYPE_ALIASES: dict[str, str] = {
    "тент": "тент",
    "tent": "тент",
    "реф": "реф",
    "рефрижератор": "реф",
    "ref": "реф",
    "изотерм": "изотерм",
    "isotherm": "изотерм",
    "площадка": "платформа",
    "platform": "платформа",
    "платформа": "платформа",
    "коники": "коники",
    "самосвал": "самосвал",
    "цистерна": "цистерна",
    "контейнеровоз": "контейнеровоз",
    "контейнер": "контейнеровоз",
    "трал": "трал",
    "низкорамник": "трал",
    "фургон": "фургон",
    "автовоз": "автовоз",
    "лесовоз": "лесовоз",
    "манипулятор": "манипулятор",
    "any": "",
    "любой": "",
}

LEGACY_BODY_KIND: dict[str, str] = {
    "тент": "EUROFURA_TENT_20T",
    "реф": "REFRIGERATOR",
    "изотерм": "ISOTHERM",
    "площадка": "FLATBED",
    "платформа": "FLATBED",
    "коники": "TIMBER_TRUCK",
    "самосвал": "DUMP_TRUCK",
    "цистерна": "TANKER",
    "контейнеровоз": "CONTAINER_CARRIER",
    "трал": "LOWBOY_TRAL",
    "фургон": "VAN_UP_TO_3_5T",
    "автовоз": "CAR_CARRIER",
    "лесовоз": "TIMBER_TRUCK",
    "манипулятор": "MANIPULATOR",
}

LOADING_TYPE_ALIASES: dict[str, str] = {
    "top": "top",
    "верх": "top",
    "upper": "top",
    "side": "side",
    "бок": "side",
    "боковая": "side",
    "back": "back",
    "rear": "back",
    "зад": "back",
    "задняя": "back",
}

LOADING_OPTION_TO_TYPE: dict[str, str] = {
    "top_loading": "top",
    "side_loading": "side",
    "back_loading": "back",
}

BODY_COMPATIBILITY: dict[str, set[str]] = {
    "тент": {"тент", "фургон"},
    "реф": {"реф", "изотерм"},
    "изотерм": {"изотерм"},
    "самосвал": {"самосвал"},
    "цистерна": {"цистерна"},
    "контейнеровоз": {"контейнеровоз", "платформа"},
    "платформа": {"платформа", "коники"},
    "коники": {"коники", "платформа"},
    "трал": {"трал", "платформа"},
    "фургон": {"фургон", "тент"},
    "автовоз": {"автовоз"},
    "лесовоз": {"лесовоз", "коники", "платформа"},
    "манипулятор": {"манипулятор", "платформа"},
}

TEMP_VEHICLE_KINDS: set[str] = {"REFRIGERATOR", "ISOTHERM"}
