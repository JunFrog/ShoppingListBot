import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
from html import escape
import json
import os
import secrets
import sqlite3
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATA_FILE = "data.json"
DB_FILE = "shopping_list.db"
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
PROXY = os.getenv("BOT_PROXY")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Add it to .env or environment variables.")

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lists (
                code TEXT PRIMARY KEY,
                owner TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS list_users (
                list_code TEXT NOT NULL,
                user_id TEXT NOT NULL,
                PRIMARY KEY (list_code, user_id),
                FOREIGN KEY (list_code) REFERENCES lists(code) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_current_list (
                user_id TEXT PRIMARY KEY,
                list_code TEXT NOT NULL,
                FOREIGN KEY (list_code) REFERENCES lists(code) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_code TEXT NOT NULL,
                name TEXT NOT NULL,
                quantity REAL NOT NULL,
                unit TEXT NOT NULL,
                bought INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (list_code) REFERENCES lists(code) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_code TEXT NOT NULL,
                name TEXT NOT NULL,
                quantity REAL NOT NULL,
                unit TEXT NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

def load_json_data():
    if not os.path.exists(DATA_FILE):
        return {"lists": {}, "user_current_list": {}}

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "lists" not in data:
        data["lists"] = {}
    if "user_current_list" not in data:
        data["user_current_list"] = {}
    return data

def migrate_json_to_sqlite():
    if not os.path.exists(DATA_FILE):
        return

    with get_connection() as conn:
        lists_count = conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0]
    if lists_count:
        return

    data = load_json_data()
    if data["lists"] or data["user_current_list"]:
        save_data(data)
        logger.info("Data migrated from %s to %s", DATA_FILE, DB_FILE)

session = AiohttpSession(proxy=PROXY) if PROXY else None
bot = Bot(token=TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Инлайн-клавиатура с примерами команд
inline_template_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="/add молоко 1.5 л", callback_data="example_add"),
        InlineKeyboardButton(text="/addmany\nмолоко 1.5 л\nяблоки 2 кг", callback_data="example_addmany"),
    ]
])

def load_data():
    data = {"lists": {}, "user_current_list": {}}

    with get_connection() as conn:
        for row in conn.execute("SELECT code, owner FROM lists ORDER BY code"):
            data["lists"][row["code"]] = {
                "owner": row["owner"],
                "users": [],
                "products": [],
            }

        for row in conn.execute("SELECT list_code, user_id FROM list_users ORDER BY user_id"):
            if row["list_code"] in data["lists"]:
                data["lists"][row["list_code"]]["users"].append(row["user_id"])

        for row in conn.execute(
            """
            SELECT list_code, name, quantity, unit, bought
            FROM products
            ORDER BY list_code, position, id
            """
        ):
            if row["list_code"] in data["lists"]:
                data["lists"][row["list_code"]]["products"].append(
                    {
                        "name": row["name"],
                        "quantity": row["quantity"],
                        "unit": row["unit"],
                        "bought": bool(row["bought"]),
                    }
                )

        for row in conn.execute("SELECT user_id, list_code FROM user_current_list"):
            data["user_current_list"][row["user_id"]] = row["list_code"]

    return data

def save_data(data):
    with get_connection() as conn:
        conn.execute("DELETE FROM products")
        conn.execute("DELETE FROM list_users")
        conn.execute("DELETE FROM user_current_list")
        conn.execute("DELETE FROM lists")

        for code, list_data in data["lists"].items():
            conn.execute(
                "INSERT INTO lists (code, owner) VALUES (?, ?)",
                (code, list_data["owner"]),
            )

            for user_id in list_data.get("users", []):
                conn.execute(
                    "INSERT OR IGNORE INTO list_users (list_code, user_id) VALUES (?, ?)",
                    (code, user_id),
                )

            for position, item in enumerate(list_data.get("products", [])):
                conn.execute(
                    """
                    INSERT INTO products (list_code, name, quantity, unit, bought, position)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code,
                        item["name"],
                        item["quantity"],
                        item["unit"],
                        int(item.get("bought", False)),
                        position,
                    ),
                )

        for user_id, code in data["user_current_list"].items():
            if code in data["lists"]:
                conn.execute(
                    "INSERT INTO user_current_list (user_id, list_code) VALUES (?, ?)",
                    (user_id, code),
                )

init_db()
migrate_json_to_sqlite()

def generate_access_code():
    return secrets.token_hex(3).upper()

def get_user_current_list(user_id, data):
    return data["user_current_list"].get(str(user_id))

def format_quantity(quantity):
    return f"{quantity:g}"

def build_products_keyboard(code, products):
    rows = []
    for index, item in enumerate(products):
        status = "✅" if item.get("bought") else "⬜"
        quantity = format_quantity(item["quantity"])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {item['name']} - {quantity} {item['unit']}",
                    callback_data=f"product:toggle:{code}:{index}",
                ),
                InlineKeyboardButton(
                    text="🗑",
                    callback_data=f"product:delete:{code}:{index}",
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

def render_shopping_list(code, products):
    if not products:
        return "📝 Ваш список покупок пуст."

    lines = [f"🛒 Список покупок ({code}):", ""]
    for index, item in enumerate(products, 1):
        status = "✅" if item.get("bought") else "⬜"
        name = escape(item["name"])
        quantity = format_quantity(item["quantity"])
        unit = escape(item["unit"])
        lines.append(f"{index}. {status} {name} — {quantity} {unit}")
    return "\n".join(lines)

def add_history_items(code, items, action):
    if not items:
        return

    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO history (list_code, name, quantity, unit, action)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    code,
                    item["name"],
                    item["quantity"],
                    item["unit"],
                    action,
                )
                for item in items
            ],
        )

def get_history_items(code, limit=20):
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT name, quantity, unit, action, created_at
            FROM history
            WHERE list_code = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (code, limit),
        ).fetchall()

def render_history(items):
    if not items:
        return "📦 История пока пустая."

    action_labels = {
        "deleted": "удалено",
        "cleared": "очищено",
    }
    lines = ["📦 История покупок:", ""]
    for index, item in enumerate(items, 1):
        action = action_labels.get(item["action"], item["action"])
        quantity = format_quantity(item["quantity"])
        lines.append(
            f"{index}. {escape(item['name'])} — {quantity} {escape(item['unit'])} "
            f"({action}, {item['created_at']})"
        )
    return "\n".join(lines)

async def send_no_active_list(message: types.Message):
    await message.answer(
        "❌ У вас нет активного списка.\n"
        "Используйте /newlist чтобы создать новый или /join код чтобы присоединиться к существующему.",
        reply_markup=inline_template_keyboard
    )

@dp.message(Command("start"))
async def start(message: types.Message):
    start_text = (
        "🛒 Привет! Я помогу вести совместный список покупок.\n\n"
        "Доступные команды:\n"
        "/newlist — создать новый список\n"
        "/join код — присоединиться к списку\n"
        "/add название количество> единица — добавить продукт\n"
        "/addmany — добавить сразу несколько продуктов (каждый с новой строки)\n"
        "/show — показать список продуктов\n"
        "/remove название — удалить продукт\n"
        "/mycode — показать код вашего списка\n"
        "/history — история удаленных товаров\n"
        "/clear — очистить текущий список\n\n"
        "Вы также можете использовать кнопки ниже для вставки примеров команд быстро."
    )
    await message.answer(start_text, reply_markup=inline_template_keyboard)

# Обработка нажатий на кнопки инлайн-клавиатуры
@dp.callback_query(lambda c: c.data == "example_add")
async def example_add_handler(callback_query: types.CallbackQuery):
    await callback_query.message.answer("/add молоко 1.5 л")
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "example_addmany")
async def example_addmany_handler(callback_query: types.CallbackQuery):
    await callback_query.message.answer("/addmany\nмолоко 1.5 л\nяблоки 2 кг")
    await callback_query.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("product:"))
async def product_action_handler(callback_query: types.CallbackQuery):
    parts = callback_query.data.split(":")
    if len(parts) != 4:
        await callback_query.answer("Не удалось обработать кнопку.", show_alert=True)
        return

    _, action, code, index_text = parts
    try:
        index = int(index_text)
    except ValueError:
        await callback_query.answer("Не удалось найти товар.", show_alert=True)
        return

    user_id = str(callback_query.from_user.id)
    data = load_data()
    if code not in data["lists"] or user_id not in data["lists"][code]["users"]:
        await callback_query.answer("У вас нет доступа к этому списку.", show_alert=True)
        return

    products = data["lists"][code]["products"]
    if index < 0 or index >= len(products):
        await callback_query.answer("Список уже изменился. Откройте /show заново.", show_alert=True)
        return

    item = products[index]
    if action == "toggle":
        item["bought"] = not item.get("bought", False)
        answer_text = "Отмечено как купленное." if item["bought"] else "Вернули в список."
    elif action == "delete":
        removed_item = products.pop(index)
        add_history_items(code, [removed_item], "deleted")
        answer_text = "Товар удален и сохранен в истории."
    else:
        await callback_query.answer("Неизвестное действие.", show_alert=True)
        return

    save_data(data)

    await callback_query.message.edit_text(
        render_shopping_list(code, products),
        reply_markup=build_products_keyboard(code, products),
    )
    await callback_query.answer(answer_text)

@dp.message(Command("newlist"))
async def newlist(message: types.Message):
    user_id = str(message.from_user.id)
    data = load_data()
    code = generate_access_code()
    data["lists"][code] = {"owner": user_id, "users": [user_id], "products": []}
    data["user_current_list"][user_id] = code
    save_data(data)
    await message.answer(
        f"✅ Создан новый список.\nВаш код доступа: <b>{code}</b>\nПоделитесь этим кодом для совместного использования.",
        reply_markup=inline_template_keyboard
    )

@dp.message(Command("join"))
async def join(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Укажите код доступа:\n/join код",
            reply_markup=inline_template_keyboard
        )
        return
    code = args[1].strip().upper()
    data = load_data()
    if code in data["lists"]:
        user_id = str(message.from_user.id)
        if user_id not in data["lists"][code]["users"]:
            data["lists"][code]["users"].append(user_id)
        data["user_current_list"][user_id] = code
        save_data(data)
        await message.answer(f"✅ Вы присоединились к списку {code}!", reply_markup=inline_template_keyboard)
    else:
        await message.answer("❌ Список с таким кодом не найден.", reply_markup=inline_template_keyboard)

@dp.message(Command("mycode"))
async def mycode(message: types.Message):
    user_id = str(message.from_user.id)
    data = load_data()
    code = get_user_current_list(user_id, data)
    if code:
        users_count = len(data["lists"][code]["users"])
        await message.answer(
            f"🔑 Ваш код доступа: <b>{code}</b>\nУчастников в списке: {users_count}",
            reply_markup=inline_template_keyboard
        )
    else:
        await send_no_active_list(message)

@dp.message(Command("add"))
async def add_product(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Напишите продукт в формате:\n/add название количество единица\n\nПример:",
            reply_markup=inline_template_keyboard
        )
        return

    user_id = str(message.from_user.id)
    data = load_data()
    code = get_user_current_list(user_id, data)
    if not code:
        await send_no_active_list(message)
        return

    parts = args[1].split()
    if len(parts) < 3:
        await message.answer(
            "❌ Неверный формат. Используйте:\n/add название количество единица",
            reply_markup=inline_template_keyboard
        )
        return

    product_name = " ".join(parts[:-2])
    try:
        quantity = float(parts[-2])
    except ValueError:
        await message.answer("❌ Количество должно быть числом.", reply_markup=inline_template_keyboard)
        return
    unit = parts[-1]

    plist = data["lists"][code]["products"]
    for item in plist:
        if item["name"].lower() == product_name.lower() and item["unit"] == unit:
            item["quantity"] += quantity
            save_data(data)
            await message.answer(f"✅ Количество <b>{product_name}</b> увеличено на {quantity} {unit}.", reply_markup=inline_template_keyboard)
            return

    plist.append({"name": product_name, "quantity": quantity, "unit": unit, "bought": False})
    save_data(data)
    await message.answer(f"✅ Добавлен продукт: <b>{product_name}</b> - {quantity} {unit}.", reply_markup=inline_template_keyboard)

@dp.message(Command("addmany"))
async def add_many_products(message: types.Message):
    if "\n" not in message.text or len(message.text.splitlines()) < 2:
        await message.answer(
            "❌ Отправьте список продуктов построчно после команды.\nПример:\n/addmany\nмолоко 1.5 л\nяблоки 2 кг",
            reply_markup=inline_template_keyboard
        )
        return

    user_id = str(message.from_user.id)
    data = load_data()
    code = get_user_current_list(user_id, data)
    if not code:
        await send_no_active_list(message)
        return

    lines = message.text.split("\n")[1:]
    added = []
    errors = []

    for line in lines:
        parts = line.strip().split()
        if len(parts) < 3:
            errors.append(f"Неверный формат: {line}")
            continue
        product_name = " ".join(parts[:-2])
        try:
            quantity = float(parts[-2])
            unit = parts[-1]
        except ValueError:
            errors.append(f"Неверное количество в строке: {line}")
            continue

        plist = data["lists"][code]["products"]
        for item in plist:
            if item["name"].lower() == product_name.lower() and item["unit"] == unit:
                item["quantity"] += quantity
                break
        else:
            plist.append({"name": product_name, "quantity": quantity, "unit": unit, "bought": False})
        added.append(f"{product_name} {quantity} {unit}")

    save_data(data)
    response = ""
    if added:
        response += "✅ Добавлено продуктов:\n" + "\n".join(added) + "\n"
    if errors:
        response += "⚠️ Ошибки:\n" + "\n".join(errors)
    await message.answer(response, reply_markup=inline_template_keyboard)

@dp.message(Command("show"))
async def show(message: types.Message):
    user_id = str(message.from_user.id)
    data = load_data()
    code = get_user_current_list(user_id, data)
    if not code:
        await send_no_active_list(message)
        return

    plist = data["lists"][code]["products"]
    if not plist:
        await message.answer("📝 Ваш список покупок пуст.", reply_markup=inline_template_keyboard)
        return

    await message.answer(
        render_shopping_list(code, plist),
        reply_markup=build_products_keyboard(code, plist),
    )

@dp.message(Command("history"))
async def history(message: types.Message):
    user_id = str(message.from_user.id)
    data = load_data()
    code = get_user_current_list(user_id, data)
    if not code:
        await send_no_active_list(message)
        return

    history_items = get_history_items(code)
    await message.answer(render_history(history_items), reply_markup=inline_template_keyboard)

@dp.message(Command("remove"))
async def remove(message: types.Message):
    user_id = str(message.from_user.id)
    data = load_data()
    code = get_user_current_list(user_id, data)
    if not code:
        await send_no_active_list(message)
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Укажите название продукта для удаления:\n/remove название", reply_markup=inline_template_keyboard)
        return

    product_name = args[1].strip().lower()
    plist = data["lists"][code]["products"]

    for i, item in enumerate(plist):
        if item["name"].lower() == product_name:
            removed_item = plist.pop(i)
            add_history_items(code, [removed_item], "deleted")
            save_data(data)
            await message.answer(f"✅ Продукт <b>{item['name']}</b> удалён из списка.", reply_markup=inline_template_keyboard)
            return

    await message.answer("❌ Продукт не найден в списке.", reply_markup=inline_template_keyboard)

@dp.message(Command("clear"))
async def clear(message: types.Message):
    user_id = str(message.from_user.id)
    data = load_data()
    code = get_user_current_list(user_id, data)
    if not code:
        await send_no_active_list(message)
        return

    plist = data["lists"][code]["products"]
    if not plist:
        await message.answer("📝 Ваш список покупок уже пуст.", reply_markup=inline_template_keyboard)
        return

    add_history_items(code, plist, "cleared")
    data["lists"][code]["products"] = []
    save_data(data)
    await message.answer("✅ Список очищен. Товары сохранены в /history.", reply_markup=inline_template_keyboard)


async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
