import csv
import json
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE_URL = "https://magnit.ru/"
STORE = "Магнит"
MROT_RUB = 27093

ROOT = Path(__file__).resolve().parent
CURRENT_FILE = ROOT / "products.csv"
HISTORY_FILE = ROOT / "price_history.csv"
REGISTRY_FILE = ROOT / "products.toml"
REPORT_FILE = ROOT / "basket_report.txt"
CHART_FILE = ROOT / "price_trends.svg"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

FIELDS = [
    "date_collected", "week", "store", "category",
    "product_name", "product_url", "package_amount",
    "package_unit", "package_price_rub",
    "price_per_kg_or_l_rub", "normalized_unit",
]


def classify(name):
    n = name.lower().replace("ё", "е")

    groups = [
        ("Напитки", [
            "сок", "нектар", "вода", "напиток", "кола",
            "лимонад", "морс", "чай", "кофе", "энергетик",
            "газирован", "компот"
        ]),
        ("Соусы и приправы", [
            "кетчуп", "соус", "майонез", "горчица",
            "хрен столовый", "аджика", "уксус", "приправа",
            "специи", "томатная паста"
        ]),
        ("Овощи и фрукты", [
            "яблок", "банан", "картоф", "морков", "томат",
            "помидор", "огур", "капуст", "груш", "апельсин",
            "лимон", "тыкв", "хурм", "фейхоа", "виноград",
            "перец", "мандари", "свекл", "кабач", "баклажан",
            "чеснок", "лук репчат", "гриб", "зелень"
        ]),
        ("Молоко, сыр и яйца", [
            "молок", "кефир", "йогурт", "творог", "сыр",
            "масло слив", "сметан", "яйц", "ряженка",
            "сливки", "простокваша"
        ]),
        ("Мясо, птица и колбасы", [
            "куриц", "цыплен", "говядин", "свинин", "фарш",
            "колбас", "сосиск", "ветчин", "индейк", "бекон",
            "мясо", "филе кур", "крылышки"
        ]),
        ("Рыба и морепродукты", [
            "рыб", "лосос", "горбуш", "сельд", "краб",
            "кревет", "минтай", "тунец", "форель", "скумбри"
        ]),
        ("Бакалея", [
            "макарон", "рис", "греч", "овсян", "мук",
            "сахар", "горох", "фасол", "масло подсол",
            "круп", "чечевиц", "спагетти", "манка", "пшено",
            "соль", "уксус"
        ]),
        ("Хлеб и выпечка", [
            "хлеб", "батон", "булоч", "лаваш", "круассан",
            "багет", "сушка", "баранк"
        ]),
        ("Сладости и снеки", [
            "шоколад", "печенье", "конфет", "вафл", "чипс",
            "орех", "мармелад", "зефир", "торт", "пирожное",
            "жевательн"
        ]),
        ("Замороженные продукты", [
            "пельмен", "вареник", "заморож", "пицца"
        ]),
    ]

    for category, keywords in groups:
        if any(word in n for word in keywords):
            return category

    return "Прочее"


class CardParser(HTMLParser):
    """Извлекает карточки товаров из HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.card = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()

        if tag == "article" and (
            "unit-catalog-product-preview" in classes
        ):
            self.card = {"name": "", "url": "", "text": ""}
            self.depth = 1
            return

        if self.card is None:
            return

        if tag == "article":
            self.depth += 1

        if tag == "a":
            href = attrs.get("href", "")
            title = attrs.get("title", "")

            if "/product/" in href or "/promo-product/" in href:
                self.card["name"] = title or self.card["name"]
                self.card["url"] = urljoin(BASE_URL, href)

    def handle_data(self, data):
        if self.card is not None:
            self.card["text"] += " " + data

    def handle_endtag(self, tag):
        if self.card is None or tag != "article":
            return

        self.depth -= 1

        if self.depth == 0:
            if self.card["url"] and self.card["name"]:
                self.cards.append(self.card)
            self.card = None


def extract_price(text):
    """Ищет цену рядом с обозначением рублей."""
    text = text.replace("\xa0", " ").replace("\u202f", " ")

    patterns = [
        r"(\d[\d ]*[,.]\d{2})\s*(?:₽|руб\.?)",
        r"(\d[\d ]*[,.]\d{2})\s*₽",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        for match in matches:
            value = float(match.replace(" ", "").replace(",", "."))
            if 0.5 <= value <= 100000:
                return round(value, 2)

    # Резервный поиск, если сайт убрал знак рубля из HTML.
    matches = re.findall(r"(?<!\d)(\d{1,6}[,.]\d{2})(?!\d)", text)

    for match in matches:
        value = float(match.replace(",", "."))
        if 0.5 <= value <= 100000:
            return round(value, 2)

    return None


def extract_amount(name):
    """Определяет массу/объём упаковки и единицу нормализации."""
    text = name.lower().replace(",", ".")

    match = re.search(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*"
        r"(кг|kg|г|гр|g|л|l|мл|ml)\b",
        text,
    )

    if not match:
        return None, None, None, None

    amount = float(match.group(1))
    unit = match.group(2)

    if unit in ("кг", "kg"):
        return amount, "kg", amount, "кг"
    if unit in ("г", "гр", "g"):
        return amount, "g", amount / 1000, "кг"
    if unit in ("л", "l"):
        return amount, "l", amount, "л"
    if unit in ("мл", "ml"):
        return amount, "ml", amount / 1000, "л"

    return None, None, None, None


def read_csv(path):
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def save_registry(products):
    """Сохраняет ссылки на товары в TOML."""
    with REGISTRY_FILE.open("w", encoding="utf-8") as file:
        file.write("# Реестр товаров с magnit.ru\n\n")

        for i, product in enumerate(products, start=1):
            file.write("[[products]]\n")
            file.write(f"id = {json.dumps(str(i))}\n")
            file.write(
                f"name = {json.dumps(product['product_name'], ensure_ascii=False)}\n"
            )
            file.write(
                f"category = {json.dumps(product['category'], ensure_ascii=False)}\n"
            )
            file.write(
                f"url = {json.dumps(product['product_url'], ensure_ascii=False)}\n\n"
            )


def make_report(products):
    """Формирует учебную оценку стоимости набора продуктов."""
    basket = [
        ("Овощи и фрукты", 3.0, "кг"),
        ("Молоко, сыр и яйца", 2.0, "кг/л"),
        ("Мясо, птица и колбасы", 1.0, "кг"),
        ("Рыба и морепродукты", 0.5, "кг"),
        ("Бакалея", 2.0, "кг"),
        ("Хлеб и выпечка", 1.0, "кг"),
        ("Напитки", 1.0, "л"),
    ]

    lines = [
        "ОЦЕНКА СТОИМОСТИ НАБОРА ПРОДУКТОВ",
        f"Дата сбора: {date.today().isoformat()}",
        f"МРОТ для сравнения: {MROT_RUB:.2f} руб.",
        f"Четверть МРОТ: {MROT_RUB / 4:.2f} руб.",
        f"Диапазон ±5%: {MROT_RUB / 4 * 0.95:.2f}–"
        f"{MROT_RUB / 4 * 1.05:.2f} руб.",
        "",
        "В каждой категории выбирается товар с минимальной ценой за кг/л.",
        "Это учебная оценка, а не официальная потребительская корзина.",
        "Расчёт условный: объёмы категорий заданы для сравнения.",
        "",
    ]

    total = 0.0
    available = 0

    for category, quantity, unit in basket:
        candidates = []

        for product in products:
            if product["category"] != category:
                continue

            try:
                price = float(product["price_per_kg_or_l_rub"])
            except (ValueError, TypeError):
                continue

            if price > 0 and product["normalized_unit"]:
                candidates.append((price, product))

        if not candidates:
            lines.append(f"{category}: нет подходящих данных")
            continue

        price, product = min(candidates, key=lambda item: item[0])
        cost = price * quantity
        total += cost
        available += 1

        lines.append(
            f"{category}: {product['product_name']} — "
            f"{price:.2f} руб./{product['normalized_unit']} × "
            f"{quantity:g} {unit} = {cost:.2f} руб."
        )

    quarter = MROT_RUB / 4
    lines.extend([
        "",
        f"Итого по доступным категориям: {total:.2f} руб.",
        f"Покрыто категорий: {available} из {len(basket)}",
        f"Четверть МРОТ для сравнения: {quarter:.2f} руб.",
        f"Разница: {total - quarter:.2f} руб.",
        "",
        "Важно: результат нельзя считать полной стоимостью официальной",
        "потребительской корзины. Доступный ассортимент ограничен.",
        "Цена за кг/л не всегда подходит для сравнения разных продуктов.",
    ])

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def make_chart(history):
    """Создаёт SVG-график по товарам, у которых есть данные за 2+ недели."""
    grouped = {}

    for row in history:
        try:
            price = float(row.get("price_per_kg_or_l_rub", ""))
        except (ValueError, TypeError):
            continue

        if price <= 0:
            continue

        key = row.get("product_url", "")
        week = row.get("week", "")

        if not key or not week:
            continue

        grouped.setdefault(key, {
            "name": row.get("product_name", key),
            "unit": row.get("normalized_unit", ""),
            "prices": {},
        })
        grouped[key]["prices"][week] = price

    candidates = [
        item for item in grouped.values()
        if len(item["prices"]) >= 2
    ]
    candidates.sort(key=lambda item: len(item["prices"]), reverse=True)
    candidates = candidates[:6]

    if not candidates:
        CHART_FILE.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="180">'
            '<rect width="100%" height="100%" fill="white"/>'
            '<text x="25" y="45" font-size="20">'
            'Недостаточно данных для графика динамики цен.</text>'
            '<text x="25" y="85" font-size="15">'
            'Нужны цены одного и того же товара минимум за две недели.</text>'
            '</svg>',
            encoding="utf-8",
        )
        print("Для динамического графика пока недостаточно недельных данных.")
        return

    weeks = sorted({
        week
        for item in candidates
        for week in item["prices"]
    })

    width, height = 1000, 560
    left, right, top, bottom = 280, 35, 70, 80
    plot_w = width - left - right
    plot_h = height - top - bottom

    all_prices = [
        price
        for item in candidates
        for price in item["prices"].values()
    ]
    low, high = min(all_prices), max(all_prices)

    if high == low:
        high = low + 1

    colors = ["#2563eb", "#dc2626", "#16a34a",
              "#9333ea", "#ea580c", "#0891b2"]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="25" y="35" font-size="24" font-weight="bold">'
        'Динамика цены товаров, руб./кг или руб./л</text>',
    ]

    for tick in range(5):
        value = low + (high - low) * tick / 4
        y = top + plot_h - plot_h * tick / 4
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" '
            f'y2="{y:.1f}" stroke="#dddddd"/>'
        )
        parts.append(
            f'<text x="{left-10}" y="{y+5:.1f}" '
            f'text-anchor="end" font-size="13">{value:.2f}</text>'
        )

    x_positions = {}
    for i, week in enumerate(weeks):
        x = left if len(weeks) == 1 else (
            left + plot_w * i / (len(weeks) - 1)
        )
        x_positions[week] = x
        parts.append(
            f'<text x="{x:.1f}" y="{height-bottom+25}" '
            f'text-anchor="middle" font-size="12">{week}</text>'
        )

    for index, item in enumerate(candidates):
        color = colors[index % len(colors)]
        points = []

        for week in weeks:
            if week not in item["prices"]:
                continue
            price = item["prices"][week]
            x = x_positions[week]
            y = top + plot_h - ((price - low) / (high - low)) * plot_h
            points.append((x, y))

        if points:
            coordinates = " ".join(
                f"{x:.1f},{y:.1f}" for x, y in points
            )
            parts.append(
                f'<polyline points="{coordinates}" fill="none" '
                f'stroke="{color}" stroke-width="3"/>'
            )

            for x, y in points:
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" '
                    f'fill="{color}"/>'
                )

        label = item["name"]
        if len(label) > 34:
            label = label[:31] + "..."
        label_y = top + index * 55
        parts.append(
            f'<line x1="20" y1="{label_y}" x2="42" y2="{label_y}" '
            f'stroke="{color}" stroke-width="4"/>'
        )
        parts.append(
            f'<text x="50" y="{label_y+5}" font-size="13">'
            f'{label}</text>'
        )

    parts.append("</svg>")
    CHART_FILE.write_text("\n".join(parts), encoding="utf-8")
    print("График:", CHART_FILE)


def main():
    today = date.today()
    iso = today.isocalendar()
    week_id = f"{iso.year}-W{iso.week:02d}"

    print("Загружаю главную страницу Магнита...")

    try:
        response = requests.get(
            BASE_URL,
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        print("Не удалось загрузить страницу:", error)
        return

    parser = CardParser()
    parser.feed(response.text)

    print("Найдено карточек:", len(parser.cards))

    current = []
    seen_urls = set()

    for card in parser.cards:
        if card["url"] in seen_urls:
            continue

        price = extract_price(card["text"])
        if price is None:
            continue

        seen_urls.add(card["url"])

        amount, package_unit, normalized_amount, normalized_unit = (
            extract_amount(card["name"])
        )

        normalized_price = None
        if normalized_amount and normalized_amount > 0:
            normalized_price = round(price / normalized_amount, 2)

        current.append({
            "date_collected": today.isoformat(),
            "week": week_id,
            "store": STORE,
            "category": classify(card["name"]),
            "product_name": card["name"],
            "product_url": card["url"],
            "package_amount": amount if amount is not None else "",
            "package_unit": package_unit or "",
            "package_price_rub": f"{price:.2f}",
            "price_per_kg_or_l_rub": (
                f"{normalized_price:.2f}"
                if normalized_price is not None else ""
            ),
            "normalized_unit": normalized_unit or "",
        })

    print("Карточек с распознанной ценой:", len(current))

    if not current:
        print("Товары не распознаны. Файлы с текущими данными не обновлены.")
        return

    write_csv(CURRENT_FILE, current)

    # Для той же недели обновляем старые цены текущим снимком.
    # Записи за предыдущие недели остаются в истории.
    history = read_csv(HISTORY_FILE)
    current_keys = {
        (row.get("week"), row.get("product_url"))
        for row in current
    }

    history = [
        row for row in history
        if (row.get("week"), row.get("product_url")) not in current_keys
    ]

    history.extend(current)
    write_csv(HISTORY_FILE, history)

    save_registry(current)

    print("Записей в текущем недельном снимке:", len(current))
    print("Текущий список:", CURRENT_FILE)
    print("История цен:", HISTORY_FILE)
    print("Реестр товаров:", REGISTRY_FILE)

    make_report(current)
    make_chart(history)


if __name__ == "__main__":
    main()
