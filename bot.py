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
# путь к секретному файлу; в Render заливай Secret File с таким именем
SECRET_PATHS = ["/etc/secrets/google_key.json", "google_key.json"]
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

def _find_secret_path() -> str:
    for p in SECRET_PATHS:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "google_key.json не найден. Либо добавь Secret File в Render как /etc/secrets/google_key.json, "
        "либо положи google_key.json рядом с bot.py"
    )

def connect_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    key_path = _find_secret_path()
    creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
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

def list_by_sheet_query(spread, title_query: str):
    tq = clean_text(title_query)
    results = []
    for sh in spread.worksheets():
        if tq not in clean_text(sh.title):
            continue
        data = sh.get_all_values()
        if not data:
            continue
        headers = data[0]
        for row in data[1:]:
            if not row:
                continue
            raw = row[0] if len(row) > 0 else ""
            cn = clean_text(raw)
            if cn:
                results.append((sh.title, headers, row, cn))
    return results

# ─────────── Handlers ───────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пиши название напитка — пришлю рецепт.\n"
        "Команды:\n"
        "• /tabs — показать вкладки\n"
        "• /tabs &lt;категория&gt; — список напитков в категории (пример: <code>/tabs Классика</code>)\n"
        "• /list &lt;слово&gt; — список совпадений (пример: <code>/list мох</code>)\n"
        "• /all &lt;слово&gt; — вывести все совпадения рецептами (пример: <code>/all латте</code>)\n"
        "Если пришлю список — ответь цифрой (1,2,…), и я пришлю карточку рецепта."
    )

async def tabs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    spread = context.bot_data["spread"]
    if context.args:
        q = " ".join(context.args)
        matches = list_by_sheet_query(spread, q)
        if not matches:
            titles = [ws.title for ws in spread.worksheets()]
            return await update.message.reply_text(
                "Не нашёл такую категорию.\nДоступные вкладки:\n" + "\n".join(f"• {t}" for t in titles)
            )
        context.user_data["last_results"] = matches
        names = [f"{i+1}. {m[2][0]} (🗂 {m[0]})" for i, m in enumerate(matches)]
        return await update.message.reply_text(
            f"Напитки в категории «{q}». Выбери номер:\n" + "\n".join(names)
        )
    titles = [ws.title for ws in spread.worksheets()]
    await update.message.reply_text("Вкладки:\n" + "\n".join(f"• {t}" for t in titles))

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Использование: <code>/list мох</code>")
    q = " ".join(context.args)
    spread = context.bot_data["spread"]
    matches = find_matches_all_tabs(spread, q)
    if not matches:
        return await update.message.reply_text("Ничего не нашёл.")
    context.user_data["last_results"] = matches
    names = [f"{i+1}. {m[2][0]} (🗂 {m[0]})" for i, m in enumerate(matches)]
    await update.message.reply_text("Совпадения:\n" + "\n".join(names))

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Использование: <code>/all латте</code>")
    q = " ".join(context.args)
    spread = context.bot_data["spread"]
    matches = find_matches_all_tabs(spread, q)
    if not matches:
        return await update.message.reply_text("Ничего не нашёл.")
    q_clean = clean_text(q)
    matches.sort(key=lambda m: (m[3] != q_clean, m[2][0]))
    chunk, acc = [], 0
    for sh, headers, row, _ in matches:
        block = format_recipe(sh, headers, row) + "\n\n"
        if acc + len(block) > 3500:
            await update.message.reply_text("".join(chunk).strip())
            chunk, acc = [], 0
        chunk.append(block); acc += len(block)
    if chunk:
        await update.message.reply_text("".join(chunk).strip())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    spread = context.bot_data["spread"]

    if msg.isdigit():
        idx = int(msg) - 1
        results = context.user_data.get("last_results") or []
        if 0 <= idx < len(results):
            sh, headers, row, _ = results[idx]
            return await update.message.reply_text(format_recipe(sh, headers, row))

    matches = find_matches_all_tabs(spread, msg)
    if not matches:
        return await update.message.reply_text("❌ Напиток не найден.")
    q_clean = clean_text(msg)
    exact = [m for m in matches if m[3] == q_clean]
    if exact:
        sh, headers, row, _ = exact[0]
        return await update.message.reply_text(format_recipe(sh, headers, row))

    context.user_data["last_results"] = matches
    names = [f"{i+1}. {m[2][0]} (🗂 {m[0]})" for i, m in enumerate(matches)]
    if len(names) > 10:
        names = names[:10] + [f"…и ещё {len(matches)-10}. Используй <code>/all {msg}</code>"]
    await update.message.reply_text("Нашёл несколько вариантов, выбери номер:\n" + "\n".join(names))

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
    app.add_handler(CommandHandler("tabs", tabs))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("all", all_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Бот запускается…")
    app.run_polling()

if __name__ == "__main__":
    main()
