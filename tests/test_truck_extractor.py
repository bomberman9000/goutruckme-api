from src.parser_bot.truck_extractor import parse_truck_regex


def test_parse_truck_regex_extracts_city_type_capacity_and_price_from_avito_card():
    text = (
        "Аренда и услуги манипулятора,воровайки,крана 7т\n"
        "Цена: 1500 руб.\n"
        "Ссылка: https://www.avito.ru/chita/predlozheniya_uslug/"
        "arenda_i_uslugi_manipulyatoravorovaykikrana_7t_3013015576"
    )

    parsed = parse_truck_regex(text)

    assert parsed.truck_type == "манипулятор"
    assert parsed.capacity_tons == 7.0
    assert parsed.base_city == "Чита"
    assert parsed.price_rub == 1500


def test_parse_truck_regex_uses_url_slug_and_route_hints():
    text = (
        "Грузоперевозки. Кумертау, межгород, рб,рф.Грузчики\n"
        "Цена: 300 руб.\n"
        "Ссылка: https://www.avito.ru/kumertau/predlozheniya_uslug/"
        "gruzoperevozki._kumertau_mezhgorod_rbrf.gruzchiki_972515502"
    )

    parsed = parse_truck_regex(text)

    assert parsed.base_city == "Кумертау"
    assert parsed.routes == "межгород, РФ, РБ"
    assert parsed.price_rub == 300


def test_parse_truck_regex_detects_tral_for_special_equipment_delivery():
    text = (
        "Перевозка спецтехники из Китая\n"
        "Цена: 98 руб.\n"
        "Ссылка: https://www.avito.ru/moskva_zelenograd/predlozheniya_uslug/"
        "perevozka_spetstehniki_iz_kitaya_7670672617"
    )

    parsed = parse_truck_regex(text)

    assert parsed.truck_type == "трал"
    assert parsed.base_city == "Москва"
    assert parsed.routes == "из Китая"
