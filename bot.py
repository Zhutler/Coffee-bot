import os
import re
import sys
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, Defaults
)
from telegram.constants import ParseMode

# ====== НАСТРОЙКИ ЧЕРЕЗ ENV ======
TOKEN = os.getenv("BOT_TOKEN")                         # задаёшь в Render → Environment Variables
TABLE_NAME = os.getenv("TABLE_NAME", "Калькуляции для GPT")
# ================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

# ---- красота для карточек ----
NAMES_MAP = {
    "СПРАЙТ": ("🥤", "Спрайт", "Основа"),
    "ВОДА С/Г": ("💧", "Вода с/г", "Основа"),
    "ШВЕПС": ("🫧", "Швепс", "Основа"),
    "МОЛОКО": ("🥛", "Молоко", "Молочные"),
    "СЛИВКИ": ("🧁", "Сливки", "Молочные"),
    "СОК": ("🍹", "Сок", "Соки"),
    "ФРУКТЫ": ("🍓", "Фрукты", "Фрукты"),
    "СИРОП": ("🍯", "Сироп", "Сиропы"),
    "ДОБАВКИ": ("✨", "Добавки", "Добавки"),
    "(кол-во)": (None, "(кол-во)", None),
}
ORDER_GROUPS = ["Основа", "Молочные", "Соки", "Фрукты", "Сиропы", "Добавки", "Прочее"]
# --------------------------------

def clean_text(text: str) -> str:
    if text is None:
        return ""
    return re.sub(r"[^\w\s]", "", str(text)).strip().lower()

def connect_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_key.json", scope)
    client = gspread.authorize(creds)
    return client.open(TABLE_NAME)

def friendly_header(h: str):
    raw = (h or "").strip()
    em, name, group = NAMES_MAP.get(raw, ("•", raw, "Прочее"))
    return em, name, group

def parse_row(headers, row):
    items = []
    i = 1
    while i < min(len(headers), len(row)):
        head = headers[i]
        val = str(row[i]).strip() if i < len(row) else ""
        if not head or not val or val == "-" or val.lower() == "—":
            i += 1
            continue
        em, name, group = friendly_header(head)
        qty = ""
        if i + 1 < len(headers) and (headers[i + 1] or "").strip().upper() == "(КОЛ-ВО)":
            qty = str(row[i + 1]).strip() if (i + 1) < len(row) else ""
            i += 1
        items.append((group, em, name, val, qty))
        i += 1

    grouped = {}
    for group, em, name, val, qty in items:
        grouped.setdefault(group, []).append((em, name, val, qty))

    sorted_groups = []
    for g in ORDER_GROUPS:
        if g in grouped:
            sorted_groups.append((g, grouped[g]))
    for g, arr in grouped.items():
        if g not in ORDER_GROUPS:
            sorted_groups.append((g, arr))
    return sorted_groups

def box_table(groups):
    lines = []
    lines.append("┌─ РЕЦЕПТ ───────────────────────────────┐")
    for group, arr in groups:
        if not arr:
            continue
        lines.append(f"│ [{group}]")
        w = max(len(name) for _, name, _, _ in arr)
        for em, name, val, qty in arr:
            prefix = (em + " ") if em else ""
            left = f"{prefix}{name}".ljust(w + (2 if em else 0))
            right = val if not qty else f"{val}  ({qty})"
            lines.append(f"│ {left} │ {right}")
        lines.append("│")
    if lines and lines[-1] == "│":
        lines.pop()
    lines.append("└────────────────────────────────────────┘")
    return "\n".join(lines)

def format_recipe(sheet_title: str, headers, row) -> str:
    groups = parse_row(headers, row)
    if not any(arr for _, arr in groups):
        return f"<b>📋 {row[0]}</b> · <i>{sheet_title}</i>\nНет данных по ингредиентам."
    table = box_table(groups)
    title = f"<b>📋 {row[0]}</b> · <i>{sheet_title}</i>"
    return f"{title}\n<pre>{table}</pre>"

def find_matches_all_tabs(spread, query: str):
    q = clean_text(query)
    results = []
    for sh in spread.worksheets():
        data = sh.get_all_values()
        if not data:
            continue
        headers = data[0]
        for row in data[1:]:
            if not row:
                continue
            raw = row[0] if len(row) > 0 else ""
            cn = clean_text(raw)
            if cn and q in cn:
                results.append((sh.title, headers, row, cn))
    return results

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Пиши название напитка — пришлю рецепт.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    spread = context.bot_data["spread"]

    matches = find_matches_all_tabs(spread, msg)
    if not matches:
        return await update.message.reply_text("❌ Напиток не найден.")

    sh, headers, row, _ = matches[0]
    await update.message.reply_text(format_recipe(sh, headers, row))

def main():
    if not TOKEN:
        log.error("BOT_TOKEN не задан. Укажи переменную окружения BOT_TOKEN.")
        sys.exit(1)

    log.info("Подключаюсь к Google Sheet…")
    spread = connect_sheet()
    log.info("Открыт файл: %s", TABLE_NAME)

    log.info("Стартую Telegram-бота…")
    app = ApplicationBuilder().token(TOKEN).defaults(Defaults(parse_mode=ParseMode.HTML)).build()
    app.bot_data["spread"] = spread

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
