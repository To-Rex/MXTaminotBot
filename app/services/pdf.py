"""Акт сверка PDF (TZ 2.6: Акт сверкани PDF шаклида олиш).

fpdf2 bilan yaratiladi. Kirill matn uchun tizimdan Unicode TTF shrift qidiriladi
(macOS/Linux/Windows yo'llari); topilmasa matn lotinga transliteratsiya qilinib
standart Helvetica bilan chiqariladi — PDF hech qachon xato bermaydi.
"""
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fpdf import FPDF

logger = logging.getLogger(__name__)

_ENV_FONT = os.getenv("PDF_FONT", "").strip()          # admin ko'rsatgan TTF (ixtiyoriy)
_ENV_FONT_BOLD = os.getenv("PDF_FONT_BOLD", "").strip()

_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial.ttf",
    # Linux (server)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/gnu-free/FreeSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    # Windows
    "C:/Windows/Fonts/arial.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

_CYR2LAT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "'", "ы": "i", "ь": "", "э": "e",
    "ю": "yu", "я": "ya", "қ": "q", "ғ": "g'", "ў": "o'", "ҳ": "h",
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "Yo", "Ж": "J",
    "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O",
    "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "X", "Ц": "Ts",
    "Ч": "Ch", "Ш": "Sh", "Щ": "Sch", "Ъ": "'", "Ы": "I", "Ь": "", "Э": "E",
    "Ю": "Yu", "Я": "Ya", "Қ": "Q", "Ғ": "G'", "Ў": "O'", "Ҳ": "H",
    # tipografik belgilar — core shrift (latin-1) ularni qo'llab-quvvatlamaydi
    "’": "'", "‘": "'", "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "—": "-", "–": "-", "−": "-", "×": "x", "·": ".", "…": "...", "№": "N",
    "\u00a0": " ", "\u2009": " ", "\u202f": " ", "≈": "~", "€": "EUR", "₽": "RUB",
})


def _latin1_safe(text: str) -> str:
    """Core shrift faqat latin-1 ni biladi — qolgan belgilarni xavfsiz almashtiramiz.

    Transliteratsiyadan keyin ham qolgan noyob belgilar (emoji va h.k.) PDF ni
    buzmasligi uchun «?» ga aylantiriladi.
    """
    return text.encode("latin-1", "replace").decode("latin-1")


def _find_font(candidates: list[str]) -> Optional[str]:
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def _fmt_money(v: float, currency: str = "UZS") -> str:
    """Summa. UZS — butun songacha, boshqa valyutalarda sent ko'rsatiladi."""
    val = float(v or 0)
    cur = (currency or "UZS").upper()
    if cur in ("UZS", ""):
        return f"{val:,.0f}".replace(",", " ")
    has_cents = abs(val - int(val)) > 0.004
    text = f"{val:,.2f}" if has_cents else f"{val:,.0f}"
    return text.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", " ")


def _fmt_date(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%d.%m.%Y")
    return str(d or "")


_LABELS = {
    "uz": {
        "title": "АКТ СВЕРКА",
        "supplier": "Таъминотчи",
        "period": "Давр",
        "generated": "Тайёрланган вақт",
        "opening": "Давр бошидаги қолдиқ",
        "closing": "Давр охиридаги қолдиқ",
        "date": "Сана", "doc": "Ҳужжат", "note": "Изоҳ",
        "debit": "Дебет", "credit": "Кредит",
        "total": "Жами айланма",
        "currency": "Валюта",
        "supplier_id": "Таъминотчи ID",
        "price_qty": "Нарх × миқдор",
        "rows": "Ҳаракатлар",
        "page": "Саҳифа",
        "sum": "сўм",
    },
    "ru": {
        "title": "АКТ СВЕРКИ",
        "supplier": "Поставщик",
        "period": "Период",
        "generated": "Сформировано",
        "opening": "Остаток на начало",
        "closing": "Остаток на конец",
        "date": "Дата", "doc": "Документ", "note": "Примечание",
        "debit": "Дебет", "credit": "Кредит",
        "total": "Итого обороты",
        "currency": "Валюта",
        "supplier_id": "ID поставщика",
        "price_qty": "Цена × кол-во",
        "rows": "Движения",
        "page": "Страница",
        "sum": "сум",
    },
}


def _wrap(pdf: FPDF, text: str, width: float, tr) -> list[str]:
    """Matnni ustun kengligiga qarab qatorlarga bo'ladi (kesib tashlamaydi)."""
    text = str(text or "")
    if not text:
        return [""]
    lines, cur = [], ""
    for word in text.split(" "):
        probe = f"{cur} {word}".strip()
        if pdf.get_string_width(tr(probe)) <= width - 2 or not cur:
            # juda uzun bitta so'z — belgilab bo'lsa ham bo'lamiz
            while pdf.get_string_width(tr(probe)) > width - 2 and len(probe) > 1 and not cur:
                cut = max(1, int(len(probe) * (width - 2) / max(pdf.get_string_width(tr(probe)), 1)))
                lines.append(probe[:cut])
                probe = probe[cut:]
            cur = probe
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def build_akt_pdf(akt: dict, lang: str = "uz") -> bytes:
    """akt — SupplierService.get_akt_sverka natijasi. Returns PDF bytes."""
    L = _LABELS.get(lang, _LABELS["uz"])
    cur = (akt.get("currency") or "UZS").upper()
    cur_label = L["sum"] if cur in ("UZS", "") else cur
    money = lambda v: _fmt_money(v, cur)  # noqa: E731

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(14, 12, 14)
    pdf.add_page()

    regular = _find_font([_ENV_FONT] + _FONT_CANDIDATES if _ENV_FONT else _FONT_CANDIDATES)
    bold = _find_font(([_ENV_FONT_BOLD] if _ENV_FONT_BOLD else []) + _FONT_BOLD_CANDIDATES)
    if regular:
        pdf.add_font("Main", "", regular)
        pdf.add_font("Main", "B", bold or regular)
        font = "Main"
        tr = lambda s: str(s)  # noqa: E731
    else:
        font = "helvetica"
        tr = lambda s: _latin1_safe(str(s).translate(_CYR2LAT))  # noqa: E731
        logger.warning(
            "PDF: Unicode TTF shrift topilmadi — matn lotinga o'girib chiqariladi. "
            "Kirill ko'rinishi uchun serverga shrift o'rnating: "
            "Debian/Ubuntu — `apt install fonts-dejavu-core`, "
            "RHEL/Alma — `dnf install dejavu-sans-fonts`; "
            "yoki .env da PDF_FONT=/path/to/font.ttf ko'rsating."
        )

    PW = pdf.w - 28                    # foydali kenglik
    # ustunlar: sana · hujjat · izoh(nomi) · narx × miqдор · дебет · кредит
    W = [20, 26, 56, 30, 26, 24]
    W[2] += PW - sum(W)                # qolgan joy izohga
    ALIGN = ["C", "L", "L", "R", "R", "R"]
    HEAD = [L["date"], L["doc"], L["note"], L["price_qty"], L["debit"], L["credit"]]
    LH = 4.6                           # qator balandligi

    def header_block():
        pdf.set_font(font, "B", 15)
        pdf.cell(0, 9, tr(L["title"]), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        # ma'lumot kartasi
        pdf.set_fill_color(243, 245, 249)
        pdf.set_draw_color(215, 220, 230)
        info = [
            (L["supplier"], str(akt.get("name") or akt.get("supplier_id") or "—")),
            (L["supplier_id"], str(akt.get("supplier_id") or "—")),
            (L["period"], f"{_fmt_date(akt['date_from'])} — {_fmt_date(akt['date_to'])}"),
            (L["currency"], cur),
            (L["generated"], datetime.now().strftime("%d.%m.%Y %H:%M")),
        ]
        y0 = pdf.get_y()
        pdf.rect(14, y0, PW, len(info) * 5.6 + 3, style="DF")
        pdf.set_y(y0 + 1.5)
        for k, v in info:
            pdf.set_x(17)
            pdf.set_font(font, "", 9)
            pdf.set_text_color(90, 100, 115)
            pdf.cell(38, 5.6, tr(k + ":"))
            pdf.set_text_color(20, 25, 35)
            pdf.set_font(font, "B", 9)
            pdf.cell(PW - 44, 5.6, tr(v[:70]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

    def summary_block():
        rows = [
            (L["opening"], money(akt["opening_balance"]), (20, 25, 35)),
            (L["debit"] + " (+)", money(akt["total_debit"]), (5, 120, 80)),
            (L["credit"] + " (−)", money(akt["total_credit"]), (190, 45, 45)),
        ]
        pdf.set_font(font, "", 10)
        for label, val, color in rows:
            pdf.set_x(14)
            pdf.set_text_color(90, 100, 115)
            pdf.cell(70, 6, tr(label))
            pdf.set_text_color(*color)
            pdf.set_font(font, "B", 10)
            pdf.cell(PW - 70, 6, tr(f"{val} {cur_label}"), align="R", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(font, "", 10)
        y = pdf.get_y() + 1
        pdf.set_draw_color(180, 188, 200)
        pdf.line(14, y, 14 + PW, y)
        pdf.set_y(y + 1.5)
        pdf.set_x(14)
        pdf.set_font(font, "B", 11)
        pdf.set_text_color(20, 25, 35)
        pdf.cell(70, 7, tr(L["closing"]))
        pdf.cell(PW - 70, 7, tr(f"{money(akt['closing_balance'])} {cur_label}"),
                 align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

    def table_head():
        pdf.set_font(font, "B", 8.5)
        pdf.set_fill_color(232, 236, 244)
        pdf.set_draw_color(200, 208, 220)
        pdf.set_x(14)
        for w, h in zip(W, HEAD):
            pdf.cell(w, 7, tr(h), border=1, align="C", fill=True)
        pdf.ln()

    def row_cells(cells: list[str], fill: bool, bold_row: bool = False):
        """Bitta qator — izoh uzun bo'lsa bir necha satrga o'raladi."""
        pdf.set_font(font, "B" if bold_row else "", 8.5)
        wrapped = [_wrap(pdf, c, w, tr) for c, w in zip(cells, W)]
        h = max(len(x) for x in wrapped) * LH + 1.6
        if pdf.get_y() + h > pdf.h - 26:      # sahifa tugadi
            footer()
            pdf.add_page()
            table_head()
            pdf.set_font(font, "B" if bold_row else "", 8.5)
        y0 = pdf.get_y()
        x = 14
        pdf.set_fill_color(248, 249, 252) if fill else pdf.set_fill_color(255, 255, 255)
        for w, lines, al in zip(W, wrapped, ALIGN):
            pdf.rect(x, y0, w, h, style="DF")
            pdf.set_xy(x, y0 + 0.8)
            for ln in lines:
                pdf.set_x(x + 1)
                pdf.cell(w - 2, LH, tr(ln), align=al, new_x="LMARGIN", new_y="NEXT")
            x += w
        pdf.set_xy(14, y0 + h)

    def footer():
        pdf.set_y(-16)
        pdf.set_font(font, "", 7.5)
        pdf.set_text_color(130, 140, 155)
        pdf.cell(0, 5, tr(f"{L['page']} {pdf.page_no()}"), align="C")
        pdf.set_text_color(0, 0, 0)

    # ── sahifa ──
    header_block()
    summary_block()

    pdf.set_font(font, "B", 10)
    pdf.set_x(14)
    pdf.cell(0, 7, tr(f"{L['rows']} ({len(akt.get('rows') or [])})"), new_x="LMARGIN", new_y="NEXT")
    table_head()

    for i, r in enumerate(akt.get("rows") or []):
        price_qty = ""
        if r.get("price"):
            price_qty = f"{r['price']} × {r.get('qty') or '1'}"
        row_cells([
            _fmt_date(r["date"]),
            str(r.get("doc") or ""),
            str(r.get("title") or r.get("note") or ""),
            price_qty,
            money(r["debit"]) if r["debit"] else "",
            money(r["credit"]) if r["credit"] else "",
        ], fill=bool(i % 2))

    # jami qatori: sarlavha birinchi 4 ustunni egallaydi (satrga bo'linmasin)
    if pdf.get_y() + 8 > pdf.h - 26:
        footer(); pdf.add_page(); table_head()
    pdf.set_font(font, "B", 8.5)
    pdf.set_fill_color(232, 236, 244)
    pdf.set_x(14)
    pdf.cell(sum(W[:4]), 8, tr(f"  {L['total']}"), border=1, align="L", fill=True)
    pdf.cell(W[4], 8, tr(money(akt["total_debit"])), border=1, align="R", fill=True)
    pdf.cell(W[5], 8, tr(money(akt["total_credit"])), border=1, align="R", fill=True)
    pdf.ln()

    # ── imzolar ──
    if pdf.get_y() > pdf.h - 50:
        footer(); pdf.add_page()
    pdf.ln(10)
    pdf.set_x(14)
    pdf.set_font(font, "", 10)
    pdf.cell(PW / 2, 8, tr("____________________"))
    pdf.cell(PW / 2, 8, tr("____________________"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(14)
    pdf.set_font(font, "", 8)
    pdf.set_text_color(110, 120, 135)
    pdf.cell(PW / 2, 5, tr(L["supplier"]))
    pdf.cell(PW / 2, 5, tr("MX"))
    pdf.set_text_color(0, 0, 0)
    footer()

    return bytes(pdf.output())
