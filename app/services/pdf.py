"""Акт сверка PDF (TZ 2.6: Акт сверкани PDF шаклида олиш).

fpdf2 bilan yaratiladi. Kirill matn uchun tizimdan Unicode TTF shrift qidiriladi
(macOS/Linux/Windows yo'llari); topilmasa matn lotinga transliteratsiya qilinib
standart Helvetica bilan chiqariladi — PDF hech qachon xato bermaydi.
"""
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fpdf import FPDF

logger = logging.getLogger(__name__)

_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    # Windows
    "C:/Windows/Fonts/arial.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
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
})


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
        "sum": "сум",
    },
}


def build_akt_pdf(akt: dict, lang: str = "uz") -> bytes:
    """akt — SupplierService.get_akt_sverka natijasi. Returns PDF bytes."""
    L = _LABELS.get(lang, _LABELS["uz"])
    cur = (akt.get("currency") or "UZS").upper()
    # UZS uchun «сўм/сум», boshqasi uchun valyuta kodi (USD, EUR…)
    cur_label = L["sum"] if cur in ("UZS", "") else cur
    money = lambda v: _fmt_money(v, cur)  # noqa: E731

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    regular = _find_font(_FONT_CANDIDATES)
    bold = _find_font(_FONT_BOLD_CANDIDATES)
    if regular:
        pdf.add_font("Main", "", regular)
        pdf.add_font("Main", "B", bold or regular)
        font = "Main"
        tr = lambda s: str(s)  # noqa: E731
    else:
        # Unicode shrift topilmadi — kirillni lotinga o'girib chiqaramiz
        font = "helvetica"
        tr = lambda s: str(s).translate(_CYR2LAT)  # noqa: E731
        logger.warning("PDF: unicode font not found, falling back to transliteration")

    def cell_row(widths, texts, style="", fill=False, align=None):
        pdf.set_font(font, style, 9)
        for w, txt, al in zip(widths, texts, align or ["L"] * len(texts)):
            pdf.cell(w, 7, tr(txt), border=1, align=al, fill=fill)
        pdf.ln()

    # header
    pdf.set_font(font, "B", 16)
    pdf.cell(0, 10, tr(L["title"]), align="C")
    pdf.ln(12)

    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, tr(f"{L['supplier']}: {akt.get('name') or akt.get('supplier_id')}"))
    pdf.ln(6)
    pdf.cell(0, 6, tr(f"{L['period']}: {_fmt_date(akt['date_from'])} - {_fmt_date(akt['date_to'])}"))
    pdf.ln(6)
    pdf.cell(0, 6, tr(f"{L['currency']}: {cur}"))
    pdf.ln(6)
    pdf.cell(0, 6, tr(f"{L['generated']}: {datetime.now().strftime('%d.%m.%Y %H:%M')}"))
    pdf.ln(6)
    pdf.ln(2)

    pdf.set_font(font, "B", 10)
    pdf.cell(0, 7, tr(f"{L['opening']}: {money(akt['opening_balance'])} {cur_label}"))
    pdf.ln(9)

    # table
    widths = [22, 38, 60, 35, 35]
    pdf.set_fill_color(235, 238, 245)
    cell_row(widths, [L["date"], L["doc"], L["note"], L["debit"], L["credit"]],
             style="B", fill=True, align=["C"] * 5)
    for r in akt.get("rows", []):
        cell_row(
            widths,
            [
                _fmt_date(r["date"]),
                r["doc"],
                (r["note"][:38] + "…") if len(str(r["note"])) > 39 else r["note"],
                money(r["debit"]) if r["debit"] else "",
                money(r["credit"]) if r["credit"] else "",
            ],
            align=["C", "L", "L", "R", "R"],
        )
    # totals row: label spans the first three columns so it never truncates
    pdf.set_font(font, "B", 9)
    pdf.cell(sum(widths[:3]), 7, tr(L["total"]), border=1, align="L", fill=True)
    pdf.cell(widths[3], 7, tr(money(akt["total_debit"])), border=1, align="R", fill=True)
    pdf.cell(widths[4], 7, tr(money(akt["total_credit"])), border=1, align="R", fill=True)
    pdf.ln()

    pdf.ln(4)
    pdf.set_font(font, "B", 11)
    pdf.cell(0, 8, tr(f"{L['closing']}: {money(akt['closing_balance'])} {cur_label}"))
    pdf.ln(10)

    # signatures
    pdf.set_font(font, "", 10)
    pdf.cell(90, 8, tr("____________________"))
    pdf.cell(90, 8, tr("____________________"))
    pdf.ln(6)
    pdf.set_font(font, "", 8)
    pdf.cell(90, 5, tr(L["supplier"]))
    pdf.cell(90, 5, tr("MX"))

    return bytes(pdf.output())
