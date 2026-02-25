from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.barcode import qr
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from datetime import datetime
import io
import logging
import os
import re

logger = logging.getLogger(__name__)

# Фактические имена шрифтов для PDF заявок (после _register_application_fonts: DejaVu или Helvetica fallback)
_APP_FONT_REGULAR = "DejaVuSans"
_APP_FONT_BOLD = "DejaVuSans-Bold"

def _find_font_path(name: str) -> str | None:
    """Ищет .ttf в проекте, в reportlab, в системе."""
    candidates = []
    try:
        import reportlab
        rl_dir = os.path.dirname(reportlab.__file__)
        candidates.extend([
            os.path.join(rl_dir, "fonts", name + ".ttf"),
            os.path.join(rl_dir, "..", "fonts", name + ".ttf"),
        ])
    except Exception:
        pass
    candidates.extend([
        os.path.join(os.path.dirname(__file__), "fonts", name + ".ttf"),
        "/usr/share/fonts/truetype/dejavu/" + name + ".ttf",
        "/usr/share/fonts/TTF/" + name + ".ttf",
        "/Library/Fonts/" + name + ".ttf",
        os.path.expanduser("~/Library/Fonts/" + name + ".ttf"),
    ])
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None

def _register_application_fonts() -> None:
    """Регистрирует DejaVuSans (или fallback на Helvetica). Bold при отсутствии = regular. Не падать при отсутствии шрифтов."""
    global _APP_FONT_REGULAR, _APP_FONT_BOLD
    try:
        if pdfmetrics.getFont(_APP_FONT_REGULAR):
            return
    except Exception:
        pass
    path_reg = _find_font_path("DejaVuSans")
    path_bold = _find_font_path("DejaVuSans-Bold")
    if not path_reg:
        _APP_FONT_REGULAR = "Helvetica"
        _APP_FONT_BOLD = "Helvetica-Bold"
        logger.warning(
            "DejaVuSans.ttf не найден, используется Helvetica — кириллица в PDF заявок может отображаться некорректно. "
            "Добавьте src/core/fonts/DejaVuSans.ttf или установите пакет dejavu-fonts."
        )
        return
    pdfmetrics.registerFont(TTFont("DejaVuSans", path_reg))
    if path_bold:
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", path_bold))
    else:
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", path_reg))
    _APP_FONT_REGULAR = "DejaVuSans"
    _APP_FONT_BOLD = "DejaVuSans-Bold"

# A4 portrait, margins in mm
MM_PT = 2.834645669
PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT_MM, RIGHT_MM, TOP_MM, BOTTOM_MM = 20, 15, 15, 15
LEFT_PT = LEFT_MM * MM_PT
RIGHT_PT = RIGHT_MM * MM_PT
TOP_PT = TOP_MM * MM_PT
BOTTOM_PT = BOTTOM_MM * MM_PT
CONTENT_WIDTH = PAGE_WIDTH - LEFT_PT - RIGHT_PT
DEFAULT_FONT_SIZE = 11
LEADING = 14

# Известные подзаголовки (Bold 13) — фиксированный набор, без зависимости от .isupper()
HEADING_SUB_STRIPS = frozenset({
    "РЕКВИЗИТЫ СТОРОН",
    "Приложение №1",
    "ПРИЛОЖЕНИЕ №1",
    "ПОДПИСИ",
    "Приложение N1",
    "ПРИЛОЖЕНИЕ N1",
})

PARTIES_START = "[[PARTIES_START]]"
PARTIES_END = "[[PARTIES_END]]"
PARTIES_HEADER_LEFT = "Экспедитор:"
PARTIES_HEADER_RIGHT_A = "Клиент:"
PARTIES_HEADER_RIGHT_B = "Перевозчик:"
COL_GAP_MM = 10
PARTIES_HEADING_FONT_SIZE = 12
PARTIES_BODY_FONT_SIZE = 11

QR_SIZE_MM = 25
QR_CAPTION_FONT_SIZE = 8
QR_CAPTION_TEXT = "Проверка и подписание заявки в Telegram"


def draw_qr(c: canvas.Canvas, url: str, x: float, y: float, size_mm: float = 25) -> None:
    """Рисует QR-код с данными url в позиции (x, y) нижний левый угол, размер size_mm x size_mm мм."""
    try:
        qr_widget = qr.QrCodeWidget(url)
        bounds = qr_widget.getBounds()
        w = bounds[2] - bounds[0] or 1
        h = bounds[3] - bounds[1] or 1
        size_pt = size_mm * mm
        d = Drawing(size_pt, size_pt, transform=[size_pt / w, 0, 0, size_pt / h, 0, 0])
        d.add(qr_widget)
        renderPDF.draw(d, c, x, y)
    except Exception as e:
        logger.warning("Не удалось нарисовать QR-код: %s", e)


def _draw_page_footer(c: canvas.Canvas, url: str | None) -> None:
    """В правом нижнем углу текущей страницы рисует QR (если url) и подпись «Проверка и подписание заявки в Telegram»."""
    if not url:
        return
    size_pt = QR_SIZE_MM * mm
    qr_x = PAGE_WIDTH - RIGHT_PT - size_pt
    caption_y = BOTTOM_PT
    qr_y = caption_y + QR_CAPTION_FONT_SIZE + 2
    draw_qr(c, url, qr_x, qr_y, QR_SIZE_MM)
    c.setFont(_APP_FONT_REGULAR, QR_CAPTION_FONT_SIZE)
    c.drawString(qr_x, caption_y, QR_CAPTION_TEXT)


def wrap_text(line: str, font_name: str, font_size: int, max_width: float) -> list[str]:
    """
    Перенос по ширине страницы: по словам, длинные «слова» режем по символам.
    Возвращает список строк.
    """
    line = (line or "").strip()
    if not line:
        return []
    try:
        sw = pdfmetrics.stringWidth(line, font_name, font_size)
    except Exception:
        sw = len(line) * (font_size * 0.5)
    if sw <= max_width:
        return [line]
    out = []
    words = re.split(r"(\s+)", line)
    current = ""
    for w in words:
        if re.match(r"^\s+$", w):
            current = (current + w) if current else ""
            continue
        candidate = (current + w).strip() if current else w
        try:
            cw = pdfmetrics.stringWidth(candidate, font_name, font_size)
        except Exception:
            cw = len(candidate) * (font_size * 0.5)
        if cw <= max_width:
            current = candidate
            continue
        if current:
            out.append(current)
        try:
            w_width = pdfmetrics.stringWidth(w, font_name, font_size)
        except Exception:
            w_width = len(w) * (font_size * 0.5)
        if w_width <= max_width:
            current = w
            continue
        chunk = ""
        for ch in w:
            chunk += ch
            try:
                cw_chunk = pdfmetrics.stringWidth(chunk, font_name, font_size)
            except Exception:
                cw_chunk = len(chunk) * (font_size * 0.5)
            if cw_chunk > max_width:
                if len(chunk) > 1:
                    out.append(chunk[:-1])
                    chunk = ch
                else:
                    out.append(chunk)
                    chunk = ""
        current = chunk
    if current:
        out.append(current)
    return out


def classify_line(line: str) -> tuple[str, int, float, bool]:
    """
    Возвращает (font_name, font_size, extra_gap, force_pagebreak).
    extra_gap в пунктах; force_pagebreak — перед «Приложение №1».
    Подзаголовки (Bold 13) — по фиксированному списку, без зависимости от регистра символов.
    """
    stripped = (line or "").strip()
    if not stripped:
        return (_APP_FONT_REGULAR, DEFAULT_FONT_SIZE, LEADING * 0.8, False)
    if stripped.startswith("Приложение №1") or stripped.startswith("Приложение N1") or stripped.startswith("ПРИЛОЖЕНИЕ №1") or stripped.startswith("ПРИЛОЖЕНИЕ N1"):
        return (_APP_FONT_REGULAR, DEFAULT_FONT_SIZE, 0.0, True)
    if (
        stripped.startswith("Договор")
        or stripped.startswith("договор-")
        or "Договор - заявка" in stripped
        or "договор-заявка" in stripped
    ):
        return (_APP_FONT_BOLD, 15, LEADING * 0.5, False)
    if stripped in HEADING_SUB_STRIPS:
        return (_APP_FONT_BOLD, 13, 0.0, False)
    return (_APP_FONT_REGULAR, DEFAULT_FONT_SIZE, 0.0, False)


def split_by_markers(rendered_text: str) -> tuple[str, str | None, str]:
    """
    Разбивает текст по маркерам [[PARTIES_START]] и [[PARTIES_END]].
    Возвращает (before_parties, parties_block | None, after_parties).
    Если маркеров нет — parties_block = None, before = весь текст, after = "".
    """
    text = rendered_text or ""
    if PARTIES_START not in text or PARTIES_END not in text:
        return (text, None, "")
    parts = text.split(PARTIES_START, 1)
    if len(parts) != 2:
        return (text, None, "")
    before = parts[0].rstrip()
    rest = parts[1]
    parts2 = rest.split(PARTIES_END, 1)
    if len(parts2) != 2:
        return (text, None, "")
    parties_block = parts2[0].strip()
    after = parts2[1].lstrip()
    return (before, parties_block, after)


def _parse_parties_sections(parties_block: str) -> tuple[str, list[str], str, list[str]]:
    """
    Парсит блок реквизитов на левую (Экспедитор) и правую (Клиент или Перевозчик) секции.
    Возвращает (left_title, left_lines, right_title, right_lines).
    """
    left_title = PARTIES_HEADER_LEFT
    left_lines: list[str] = []
    right_title = ""
    right_lines: list[str] = []
    lines = [ln.rstrip() for ln in parties_block.splitlines()]
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == PARTIES_HEADER_LEFT:
            i += 1
            while i < len(lines) and lines[i].strip() not in (PARTIES_HEADER_RIGHT_A, PARTIES_HEADER_RIGHT_B):
                left_lines.append(lines[i])
                i += 1
            continue
        if stripped == PARTIES_HEADER_RIGHT_A or stripped == PARTIES_HEADER_RIGHT_B:
            right_title = stripped
            i += 1
            while i < len(lines):
                right_lines.append(lines[i])
                i += 1
            break
        i += 1
    return (left_title, left_lines, right_title, right_lines)


def _parties_block_height(
    parties_block: str,
    col_width: float,
    leading: float,
) -> float:
    """Оценка высоты блока реквизитов в две колонки (в пунктах)."""
    _, left_lines, _, right_lines = _parse_parties_sections(parties_block)
    font_reg = _APP_FONT_REGULAR
    font_bold = _APP_FONT_BOLD
    n_left = 1  # заголовок
    for ln in left_lines:
        n_left += len(wrap_text(ln, font_reg, PARTIES_BODY_FONT_SIZE, col_width)) or 1
    n_right = 1 if right_lines else 0
    for ln in right_lines:
        n_right += len(wrap_text(ln, font_reg, PARTIES_BODY_FONT_SIZE, col_width)) or 1
    return (max(n_left, n_right) + 1) * leading  # +1 отступ после заголовков


def draw_parties_two_columns(
    c: canvas.Canvas,
    parties_block: str,
    x: float,
    y: float,
    col_width: float,
    col_gap: float,
    leading: float,
    bottom_y: float,
) -> float:
    """
    Рисует блок реквизитов в две колонки: слева Экспедитор, справа Клиент/Перевозчик.
    Заголовки — Bold PARTIES_HEADING_FONT_SIZE, текст — Regular PARTIES_BODY_FONT_SIZE.
    wrap_text по col_width. Возвращает новую y (после блока).
    """
    left_title, left_lines, right_title, right_lines = _parse_parties_sections(parties_block)
    right_x = x + col_width + col_gap

    def draw_column(cx: float, title: str, body_lines: list[str]) -> float:
        cy = y
        c.setFont(_APP_FONT_BOLD, PARTIES_HEADING_FONT_SIZE)
        title_wrapped = wrap_text(title, _APP_FONT_BOLD, PARTIES_HEADING_FONT_SIZE, col_width)
        for part in title_wrapped or [title]:
            if cy < bottom_y:
                break
            c.drawString(cx, cy, part)
            cy -= leading
        c.setFont(_APP_FONT_REGULAR, PARTIES_BODY_FONT_SIZE)
        for ln in body_lines:
            wrapped = wrap_text(ln, _APP_FONT_REGULAR, PARTIES_BODY_FONT_SIZE, col_width)
            for part in wrapped or [ln]:
                if cy < bottom_y:
                    break
                c.drawString(cx, cy, part)
                cy -= leading
        return cy

    y_left_end = draw_column(x, left_title, left_lines)
    y_right_end = draw_column(right_x, right_title, right_lines) if right_title else y
    new_y = min(y_left_end, y_right_end)
    return new_y


def _draw_text_block(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    bottom_y: float,
    footer_url: str | None = None,
) -> float:
    """
    Рисует текст построчно с wrap и классификацией строк (заголовки, перенос страницы).
    Не рисует строки, равные маркерам [[PARTIES_START]] / [[PARTIES_END]].
    Перед каждым showPage() при footer_url рисует футер (QR + подпись) внизу страницы.
    Возвращает итоговую y после отрисовки.
    """
    lines = (text or "").split("\n")
    current_y = y
    for raw_line in lines:
        if raw_line.strip() in (PARTIES_START, PARTIES_END):
            continue
        font_name, font_size, extra_gap, force_pagebreak = classify_line(raw_line)
        if force_pagebreak and current_y < PAGE_HEIGHT - TOP_PT:
            if footer_url:
                _draw_page_footer(c, footer_url)
            c.showPage()
            c.setFont(_APP_FONT_REGULAR, DEFAULT_FONT_SIZE)
            current_y = PAGE_HEIGHT - TOP_PT
        current_y -= extra_gap
        if raw_line.strip() == "":
            current_y -= LEADING * 0.8
            if current_y < bottom_y:
                if footer_url:
                    _draw_page_footer(c, footer_url)
                c.showPage()
                c.setFont(_APP_FONT_REGULAR, DEFAULT_FONT_SIZE)
                current_y = PAGE_HEIGHT - TOP_PT
            continue
        wrapped = wrap_text(raw_line, font_name, font_size, CONTENT_WIDTH)
        c.setFont(font_name, font_size)
        for part in wrapped:
            if current_y < bottom_y:
                if footer_url:
                    _draw_page_footer(c, footer_url)
                c.showPage()
                c.setFont(font_name, font_size)
                current_y = PAGE_HEIGHT - TOP_PT
            c.drawString(x, current_y, part)
            current_y -= LEADING
    return current_y


def generate_application_pdf(rendered_text: str, *, app_id: int | None = None) -> bytes:
    """
    Генерация PDF заявки A/B из rendered_text с типографикой:
    заголовки (Bold 15), подзаголовки (Bold 13), перенос по ширине, отступы, «Приложение №1» с новой страницы.
    Блок между [[PARTIES_START]] и [[PARTIES_END]] рисуется в две колонки (Экспедитор | Клиент/Перевозчик);
    маркеры в PDF не выводятся.
    Если заданы app_id и settings.bot_username, внизу каждой страницы рисуется QR со ссылкой на заявку в боте.
    """
    from src.core.config import settings

    _register_application_fonts()
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    y = PAGE_HEIGHT - TOP_PT
    x = LEFT_PT
    bottom_y = BOTTOM_PT

    footer_url: str | None = None
    if app_id is not None and getattr(settings, "bot_username", None):
        username = (settings.bot_username or "").lstrip("@").strip()
        if username:
            footer_url = f"https://t.me/{username}?start=app_{app_id}"

    before, parties_block, after = split_by_markers(rendered_text or "")

    y = _draw_text_block(c, before, x, y, bottom_y, footer_url=footer_url)

    if parties_block:
        col_gap_pt = COL_GAP_MM * MM_PT
        col_width = (CONTENT_WIDTH - col_gap_pt) / 2
        needed_height = _parties_block_height(parties_block, col_width, LEADING)
        if y - needed_height < bottom_y:
            if footer_url:
                _draw_page_footer(c, footer_url)
            c.showPage()
            c.setFont(_APP_FONT_REGULAR, DEFAULT_FONT_SIZE)
            y = PAGE_HEIGHT - TOP_PT
        y = draw_parties_two_columns(c, parties_block, x, y, col_width, col_gap_pt, LEADING, bottom_y)

    y = _draw_text_block(c, after, x, y, bottom_y, footer_url=footer_url)

    if footer_url:
        _draw_page_footer(c, footer_url)

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def generate_ttn(cargo, owner, carrier) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Заголовок
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height - 50, "ТРАНСПОРТНАЯ НАКЛАДНАЯ")
    
    c.setFont("Helvetica", 10)
    c.drawString(50, height - 80, f"No {cargo.id} от {datetime.now().strftime('%d.%m.%Y')}")
    
    # Грузоотправитель
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 120, "1. Грузоотправитель:")
    c.setFont("Helvetica", 10)
    c.drawString(70, height - 140, f"{owner.full_name}")
    c.drawString(70, height - 155, f"ID: {owner.id}")
    
    # Грузополучатель
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 190, "2. Грузополучатель:")
    c.setFont("Helvetica", 10)
    c.drawString(70, height - 210, f"Указать при получении")
    
    # Перевозчик
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 250, "3. Перевозчик:")
    c.setFont("Helvetica", 10)
    if carrier:
        c.drawString(70, height - 270, f"{carrier.full_name}")
        c.drawString(70, height - 285, f"ID: {carrier.id}")
    else:
        c.drawString(70, height - 270, "Не назначен")
    
    # Маршрут
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 320, "4. Маршрут:")
    c.setFont("Helvetica", 10)
    c.drawString(70, height - 340, f"Откуда: {cargo.from_city}")
    c.drawString(70, height - 355, f"Куда: {cargo.to_city}")
    
    # Груз
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 390, "5. Груз:")
    c.setFont("Helvetica", 10)
    c.drawString(70, height - 410, f"Наименование: {cargo.cargo_type}")
    c.drawString(70, height - 425, f"Вес: {cargo.weight} т")
    c.drawString(70, height - 440, f"Дата загрузки: {cargo.load_date.strftime('%d.%m.%Y')}")
    
    # Стоимость
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 480, "6. Стоимость перевозки:")
    c.setFont("Helvetica", 10)
    c.drawString(70, height - 500, f"{cargo.price} руб.")
    
    # Подписи
    c.setFont("Helvetica", 9)
    c.drawString(50, 150, "Грузоотправитель ________________")
    c.drawString(300, 150, "Перевозчик ________________")
    c.drawString(50, 100, "Дата ________________")
    c.drawString(300, 100, "Дата ________________")
    
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
