import os
import psycopg2
from psycopg2.extras import DictCursor
import logging
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import WebAppInfo, ReplyKeyboardMarkup, KeyboardButton

load_dotenv()

# --- Configuration ---
TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

class UserStates(StatesGroup):
    entering_promo = State()

# --- Cache ---
subscription_cache = {} # {user_id: (status, timestamp)}
CACHE_EXPIRY = 300 # 5 minutes

# --- Admin States ---
class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()
    waiting_for_balance_user_id = State()
    waiting_for_balance_amount = State()
    waiting_for_user_info_id = State()
    # Promo Creation
    waiting_for_promo_code = State()
    waiting_for_promo_reward = State()
    waiting_for_promo_limit = State()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- Admin Check ---
def is_admin(user_id: int):
    return str(user_id) in [id.strip() for id in ADMIN_IDS if id.strip()]

# --- Database Helpers ---
def get_db():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    return conn

def setup_db():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            stars_balance INTEGER DEFAULT 0,
            total_orders INTEGER DEFAULT 0,
            total_stars INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            referred_by TEXT,
            api_token TEXT
        )
    """)
    
    # Promo codes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE,
            reward INTEGER,
            max_uses INTEGER,
            current_uses INTEGER DEFAULT 0
        )
    """)
    
    # Promo usage table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_usage (
            user_id TEXT,
            promo_id INTEGER,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, promo_id)
        )
    """)

    # Orders table (Added to prevent crash)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            amount TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Services table (Dynamic)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id SERIAL PRIMARY KEY,
            name TEXT,
            type TEXT,
            val INTEGER,
            price INTEGER
        )
    """)
    
    # Check and populate services if empty
    cursor.execute("SELECT COUNT(*) FROM services")
    if cursor.fetchone()[0] == 0:
        initial_services = [
            ("💎 50 Stars", "stars", 50, 10500),
            ("💎 100 Stars", "stars", 100, 21000),
            ("💎 200 Stars", "stars", 200, 42000),
            ("💎 400 Stars", "stars", 400, 84000),
            ("💎 1000 Stars", "stars", 1000, 210000),
            ("👑 Premium 3 oy", "premium", 3, 190000),
            ("👑 Premium 6 oy", "premium", 6, 350000),
            ("👑 Premium 12 oy", "premium", 12, 600000)
        ]
        cursor.executemany("INSERT INTO services (name, type, val, price) VALUES (%s, %s, %s, %s)", initial_services)
    
    conn.commit()
    conn.close()
    logging.info("Database setup completed (tables verified/created).")

# Note: init_user is consolidated into start_cmd for monolithic simplicity.

REQUIRED_CHANNEL = "@devel0per_junior" # Updated to match your actual channel

# --- Middleware/Helper: Check Channel Subscription ---
async def check_subscription(user_id: int):
    # Admins bypass the check
    if is_admin(user_id):
        return True
    
    # Check Cache
    now = datetime.now().timestamp()
    if user_id in subscription_cache:
        status, ts = subscription_cache[user_id]
        if now - ts < CACHE_EXPIRY:
            return status

    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        is_subbed = member.status in ["member", "administrator", "creator"]
        # Update Cache
        subscription_cache[user_id] = (is_subbed, now)
        return is_subbed
    except Exception as e:
        logging.error(f"Subscription check error: {e}")
    return False

def get_join_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="Kanalga a'zo bo'lish 📢", url=f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}")
    kb.button(text="Tekshirish ✅", callback_data="check_sub")
    kb.adjust(1)
    return kb.as_markup()

def get_cancel_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="❌ Bekor qilish")
    return kb.as_markup(resize_keyboard=True)

def get_admin_back_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Admin Panelga qaytish", callback_data="admin_main")
    return kb.as_markup()

def get_main_menu_kb(user_id):
    kb = ReplyKeyboardBuilder()
    webapp_url = os.getenv("WEBAPP_URL", "https://ishla-production.up.railway.app")
    kb.button(text="🛍️ Onlayn do'kon", web_app=WebAppInfo(url=f"{webapp_url}/?user_id={user_id}"))
    kb.button(text="📦 Xizmatlar")
    kb.button(text="📊 Buyurtmalarim")
    kb.button(text="💳 Hisobim")
    kb.button(text="💵 Pul kiritish")
    kb.button(text="💰 Pul yig'ish")
    kb.button(text="🎁 Promo Kod")
    kb.button(text="☎️ Qo'llab-quvvatlash")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

async def show_main_menu(message: types.Message, user_id: str):
    await message.answer(
        f"<b>Salom {message.from_user.full_name}!</b> 👋\n\n"
        "✨ <b>STARS BAZA</b> botiga xush kelibsiz!\n\n"
        "Pastdagi menyu orqali xizmatlardan foydalanishingiz mumkin.",
        reply_markup=get_main_menu_kb(user_id),
        parse_mode="HTML"
    )

# --- Handlers ---
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or "NoUsername"
    
    # Check for referral in args (/start ref123)
    args = message.text.split()
    referred_by = args[1] if len(args) > 1 else None

    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    
    # 1. Check if user exists
    cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
    exists = cursor.fetchone()
    
    if not exists:
        # 2. Add new user
        cursor.execute(
            "INSERT INTO users (id, username, balance, total_orders, total_stars, joined_at, referred_by, stars_balance) VALUES (%s, %s, 0, 0, 0, %s, %s, 0)",
            (user_id, username, datetime.now(), referred_by)
        )
        
        # 3. Reward referrer (+1 Star)
        if referred_by and referred_by != user_id:
            cursor.execute("UPDATE users SET stars_balance = stars_balance + 1 WHERE id = %s", (referred_by,))
            try:
                await bot.send_message(referred_by, f"🎉 Yangi referal! Sizga +1 Star 💎 berildi.")
            except: pass
        
        conn.commit()
    
    # Handle Auto-Promo from start args (/start promo_NEWYEAR)
    if referred_by and referred_by.startswith("promo_"):
        promo_code = referred_by.replace("promo_", "").upper()
        
        # 1. Check if promo exists and is valid
        cursor.execute("SELECT * FROM promo_codes WHERE code = %s", (promo_code,))
        promo = cursor.fetchone()
        
        if promo:
            if promo['current_uses'] < promo['max_uses']:
                # 2. Check if user already used this promo
                cursor.execute("SELECT user_id FROM promo_usage WHERE user_id = %s AND promo_id = %s", (user_id, promo['id']))
                already_used = cursor.fetchone()
                
                if not already_used:
                    # 3. Apply Promo
                    reward = promo['reward']
                    cursor.execute("UPDATE users SET stars_balance = stars_balance + %s WHERE id = %s", (reward, user_id))
                    cursor.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE id = %s", (promo['id'],))
                    cursor.execute("INSERT INTO promo_usage (user_id, promo_id) VALUES (%s, %s)", (user_id, promo['id']))
                    
                    # Add to orders/history (new)
                    cursor.execute(
                        "INSERT INTO orders (user_id, amount, status) VALUES (%s, %s, 'Accepted')",
                        (user_id, f"+{reward} Stars (Auto-Promo)")
                    )
                    
                    conn.commit()
                    await message.answer(f"🎁 <b>Tabriklaymiz!</b>\n\nHavola orqali kelganingiz uchun <b>{reward} Stars</b> 💎 balansigizga qo'shildi!", parse_mode="HTML")
                else:
                    await message.answer("⚠️ Siz ushbu promo kodni allaqachon ishlatgansiz.")
            else:
                await message.answer("😔 Afsuski, bu promo kodning limiti tugagan.")
        else:
            await message.answer("❌ Noto'g'ri promo kod havolasi.")

    # Check Subscription
    if not await check_subscription(message.from_user.id):
        await message.answer(
            "⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'lishingiz kerak!",
            reply_markup=get_join_keyboard()
        )
        return

    await show_main_menu(message, user_id)

@dp.message(F.text == "💳 Hisobim")
async def msg_check_balance(message: types.Message):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT balance, stars_balance FROM users WHERE id = %s", (str(message.from_user.id),))
    res = cursor.fetchone()
    conn.close()
    
    if res:
        balance, stars = res['balance'], res['stars_balance']
        formatted_balance = "{:,}".format(balance).replace(",", " ")
        await message.answer(
            f"<b>💳 Sizning balansingiz:</b>\n\n"
            f"💰 Asosiy: <b>{formatted_balance} so'm</b>\n"
            f"💎 Stars: <b>{stars} ta</b>",
            parse_mode="HTML"
        )

@dp.message(F.text == "💰 Pul yig'ish")
async def msg_get_ref(message: types.Message):
    user_id = str(message.from_user.id)
    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = %s", (user_id,))
    count = cursor.fetchone()[0]
    conn.close()

    await message.answer(
        f"💎 <b>Referal tizimi</b>\n\n"
        f"Do'stingizni taklif qiling va har biriga +1 Star 💎 oling!\n\n"
        f"🔗 Sizning havolangiz:\n<code>{ref_link}</code>\n\n"
        f"👥 Hammasi bo'lib: <b>{count} ta</b> referal",
        parse_mode="HTML"
    )

@dp.message(F.text == "🎁 Promo Kod")
async def msg_enter_promo(message: types.Message, state: FSMContext):
    await message.answer("🎁 Promo kodni yuboring (yoki /cancel):", reply_markup=get_cancel_kb())
    await state.set_state(UserStates.entering_promo)

@dp.message(F.text == "☎️ Qo'llab-quvvatlash")
async def msg_support(message: types.Message):
    await message.answer("🆘 Savollaringiz bormi? Admin bilan bog'laning:\n\n👤 Admin: @devc0derweb")

@dp.message(F.text == "📦 Xizmatlar")
async def msg_services(message: types.Message):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM services")
    services = cursor.fetchall()
    conn.close()
    
    if not services:
        await message.answer("📦 Hozircha xizmatlar topilmadi.")
        return
        
    kb = InlineKeyboardBuilder()
    for s in services:
        # Use web_app to open the specific package in the shop
        webapp_url = os.getenv("WEBAPP_URL", "https://ishla-production.up.railway.app")
        url = f"{webapp_url}/?tab={s['type']}&val={s['val']}&user_id={message.from_user.id}"
        kb.button(text=f"{s['name']} - {s['price']} so'm", web_app=WebAppInfo(url=url))
    kb.adjust(1)
    await message.answer("📦 Kerakli xizmatni tanlang:", reply_markup=kb.as_markup())

@dp.message(F.text == "📊 Buyurtmalarim")
async def msg_orders(message: types.Message):
    # Retrieve orders from DB
    user_id = str(message.from_user.id)
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM orders WHERE user_id = %s ORDER BY id DESC LIMIT 5", (user_id,))
    orders = cursor.fetchall()
    conn.close()
    
    if not orders:
        await message.answer("📊 Sizning buyurtmalaringiz hozircha yo'q.")
        return
        
    text = "<b>📊 Oxirgi 5 ta buyurtmangiz:</b>\n\n"
    for o in orders:
        text += f"🔹 Order ID: {o['id']} | {o['amount']} Stars | {o['status']}\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "💳 Hisobim")
async def msg_balance(message: types.Message):
    user_id = str(message.from_user.id)
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT balance, stars_balance FROM users WHERE id = %s", (user_id,))
    res = cursor.fetchone()
    conn.close()
    
    balance, stars = res['balance'], res['stars_balance']
    text = (
        f"💰 <b>Sizning balansingiz:</b>\n\n"
        f"💵 Asosiy Hisob: <b>{balance} so'm</b>\n"
        f"💎 Stars Balansi: <b>{stars}</b>\n\n"
        f"💳 <b>Hisobni to'ldirish:</b>\n"
        f"Karta: <code>6262 5702 0537 1009</code>\n\n"
        f"❗️ To'lovdan so'ng chekni @devc0derweb ga yuboring."
    )
    await message.answer(text, parse_mode="HTML")

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: types.CallbackQuery):
    if await check_subscription(callback.from_user.id):
        await callback.message.edit_text("✅ Rahmat! Endi botdan foydalanishingiz mumkin.")
        await show_main_menu(callback.message, str(callback.from_user.id))
    else:
        await callback.answer("❌ Siz hali ham kanalga a'zo emassiz!", show_alert=True)

# --- Promo Handling ---
@dp.message(UserStates.entering_promo)
async def promo_handler(message: types.Message, state: FSMContext):
    if message.text == "/cancel" or message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=get_main_menu_kb(str(message.from_user.id)))
        return
        
    code = message.text.strip().upper()
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM promo_codes WHERE code = %s", (code,))
    promo = cursor.fetchone()
    
    if not promo:
        await message.answer("❌ Bunday promo kod mavjud emas.")
        conn.close()
        return
        
    if promo['current_uses'] >= promo['max_uses']:
        await message.answer("❌ Bu promo kodning ishlatilish soni tugagan.")
        conn.close()
        await state.clear()
        return
        
    cursor.execute("SELECT * FROM promo_usage WHERE user_id = %s AND promo_id = %s", (str(message.from_user.id), promo['id']))
    if cursor.fetchone():
        await message.answer("❌ Siz bu promo koddan oldin foydalangansiz!")
        conn.close()
        await state.clear()
        return
        
    # All checks passed, reward the user
    reward = promo['reward']
    cursor.execute("UPDATE users SET stars_balance = stars_balance + %s, total_stars = total_stars + %s WHERE id = %s", (reward, reward, str(message.from_user.id)))
    cursor.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE id = %s", (promo['id'],))
    cursor.execute("INSERT INTO promo_usage (user_id, promo_id) VALUES (%s, %s)", (str(message.from_user.id), promo['id']))
    
    # Add to orders/history (new)
    cursor.execute(
        "INSERT INTO orders (user_id, amount, status) VALUES (%s, %s, 'Accepted')",
        (str(message.from_user.id), f"+{reward} Stars (Promo)")
    )
    
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Tabriklaymiz! Hisobingizga {reward} Stars 💎 qo'shildi va tarixingizga yozildi.", reply_markup=get_main_menu_kb(str(message.from_user.id)))
    await state.clear()

# --- Admin Panel ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizda ushbu komanda uchun ruxsat yo'q.")
        return
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(total_stars) FROM users")
    total_stars_val = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM promo_codes")
    total_promos = cursor.fetchone()[0]
    conn.close()

    text = (
        "<b>👨‍💻 Admin Paneli</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total_users}</b>\n"
        f"💎 Jami sotilgan Stars: <b>{total_stars_val}</b>\n"
        f"🎁 Faol promo kodlar: <b>{total_promos}</b>\n"
    )
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📣 Xabar yuborish", callback_data="admin_broadcast")
    kb.button(text="💰 Balans qo'shish", callback_data="admin_add_balance")
    kb.button(text="👤 Foydalanuvchi ma'lumoti", callback_data="admin_user_info")
    kb.button(text="🎁 Promo kod yaratish", callback_data="admin_create_promo")
    kb.button(text="📜 Promo kodlar ro'yxati", callback_data="admin_list_promos")
    kb.adjust(2)
    
    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_main")
async def cb_admin_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await admin_panel(callback.message)
    await callback.answer()

# --- Admin Callback Handlers & FSM ---

@dp.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("📣 Hammuaga yuboriladigan xabar matnini yuboring:", reply_markup=get_cancel_kb())
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.answer()

@dp.message(Command("broadcast"))
async def cmd_admin_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("📣 Hammuaga yuboriladigan xabar matnini yuboring:", reply_markup=get_cancel_kb())
    await state.set_state(AdminStates.waiting_for_broadcast_text)

@dp.message(AdminStates.waiting_for_broadcast_text)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=types.ReplyKeyboardRemove())
        return
        
    msg_text = message.text
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall()
    conn.close()

    count = 0
    progress = await message.answer(f"⏳ {len(users)} ta foydalanuvchiga yuborish boshlandi...")
    
    for user in users:
        try:
            await bot.send_message(user['id'], msg_text)
            count = count + 1
            await asyncio.sleep(0.05)
        except: pass
        
    await progress.edit_text(f"✅ Xabar {count} ta foydalanuvchiga muvaffaqiyatli yuborildi.")
    await state.clear()

@dp.callback_query(F.data == "admin_add_balance")
async def cb_admin_balance(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("💰 Foydalanuvchi ID sini yuboring:", reply_markup=get_cancel_kb())
    await state.set_state(AdminStates.waiting_for_balance_user_id)
    await callback.answer()

@dp.message(Command("addbalance"))
async def cmd_admin_balance(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("💰 Foydalanuvchi ID sini yuboring:", reply_markup=get_cancel_kb())
    await state.set_state(AdminStates.waiting_for_balance_user_id)

@dp.message(AdminStates.waiting_for_balance_user_id)
async def process_balance_id(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=types.ReplyKeyboardRemove())
        return
    await state.update_data(target_user_id=message.text.strip())
    await message.answer("💵 Qancha summa qo'shmoqchisiz (so'mda)?")
    await state.set_state(AdminStates.waiting_for_balance_amount)

@dp.message(AdminStates.waiting_for_balance_amount)
async def process_balance_amount(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=types.ReplyKeyboardRemove())
        return
        
    data = await state.get_data()
    target_id = data.get('target_user_id')
    amount_str = message.text.strip()
    
    if not target_id:
        await message.answer("❌ Foydalanuvchi ID topilmadi. Qayta urinib ko'ring.", reply_markup=get_admin_back_kb())
        await state.clear()
        return

    if not amount_str.isdigit():
        await message.answer("❌ Iltimos, faqat raqam kiriting (masalan: 10000):", reply_markup=get_cancel_kb())
        return
        
    amount = int(amount_str)
    
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, target_id))
        if cursor.rowcount > 0:
            conn.commit()
            await message.answer(f"✅ Foydalanuvchi {target_id} balansiga {amount} so'm qo'shildi.", reply_markup=get_admin_back_kb())
            try:
                await bot.send_message(target_id, f"💰 Hisobingiz {amount} so'mga to'ldirildi!")
            except: pass
        else:
            await message.answer("❌ Foydalanuvchi topilmadi. ID to'g'riligini tekshiring.", reply_markup=get_admin_back_kb())
        conn.close()
    except Exception as e:
        await message.answer(f"❌ Xatolik: {e}", reply_markup=get_admin_back_kb())
    await state.clear()

@dp.callback_query(F.data == "admin_user_info")
async def cb_admin_user(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("👤 Foydalanuvchi ID sini yuboring:", reply_markup=get_cancel_kb())
    await state.set_state(AdminStates.waiting_for_user_info_id)
    await callback.answer()

@dp.message(Command("user"))
async def cmd_admin_user(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await message.answer("👤 Foydalanuvchi ID sini yuboring:", reply_markup=get_cancel_kb())
    await state.set_state(AdminStates.waiting_for_user_info_id)

@dp.message(AdminStates.waiting_for_user_info_id)
async def process_user_info(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=types.ReplyKeyboardRemove())
        return
        
    target_id = message.text.strip()
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM users WHERE id = %s", (target_id,))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        text = (
            f"👤 <b>Foydalanuvchi:</b> @{user['username'] if user['username'] else 'Noma\'lum'}\n"
            f"🆔 ID: <code>{user['id']}</code>\n"
            f"💰 Balans: {user['balance']} so'm\n"
            f"💎 Stars Balans: {user['stars_balance']}\n"
            f"📦 Buyurtmalar: {user['total_orders']}\n"
            f"📅 Qo'shilgan: {user['joined_at']}"
        )
        await message.answer(text, reply_markup=get_admin_back_kb(), parse_mode="HTML")
    else:
        await message.answer("❌ Foydalanuvchi topilmadi.", reply_markup=get_admin_back_kb())
    await state.clear()

# --- Admin Promo Management ---

@dp.callback_query(F.data == "admin_create_promo")
async def cb_create_promo(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("🎁 Yangi promo kodni yuboring (masalan: NEW2024):", reply_markup=get_cancel_kb())
    await state.set_state(AdminStates.waiting_for_promo_code)
    await callback.answer()

@dp.message(AdminStates.waiting_for_promo_code)
async def process_promo_code(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=types.ReplyKeyboardRemove())
        return
    await state.update_data(new_promo_code=message.text.strip().upper())
    await message.answer("💰 Ushbu promo kod uchun qancha Stars 💎 berilsin?")
    await state.set_state(AdminStates.waiting_for_promo_reward)

@dp.message(AdminStates.waiting_for_promo_reward)
async def process_promo_reward(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=types.ReplyKeyboardRemove())
        return
    if not message.text.isdigit():
        await message.answer("❌ Miqdorni raqamda kiriting (masalan: 50):", reply_markup=get_cancel_kb())
        return
    await state.update_data(new_promo_reward=int(message.text))
    await message.answer("🔢 Maksimal foydalanish sonini kiriting (masalan: 100):")
    await state.set_state(AdminStates.waiting_for_promo_limit)

@dp.message(AdminStates.waiting_for_promo_limit)
async def process_promo_limit(message: types.Message, state: FSMContext):
    if message.text == "❌ Bekor qilish" or message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=types.ReplyKeyboardRemove())
        return
    if not message.text.isdigit():
        await message.answer("❌ Limitni raqamda kiriting (masalan: 100):", reply_markup=get_cancel_kb())
        return
    
    data = await state.get_data()
    code = data['new_promo_code']
    reward = data['new_promo_reward']
    limit = int(message.text)
    
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute(
            "INSERT INTO promo_codes (code, reward, max_uses, current_uses) VALUES (%s, %s, %s, 0)",
            (code, reward, limit)
        )
        conn.commit()
        conn.close()
        
        # Notify the admin
        await message.answer(f"✅ Promo kod yaratildi!\n\n🎫 Kod: <b>{code}</b>\n💎 Sovg'a: {reward} Stars\n🔢 Limit: {limit} ta", reply_markup=get_admin_back_kb(), parse_mode="HTML")
        
        # Post to the channel
        try:
            bot_me = await bot.get_me()
            # Deep link to open the bot and potentially handle the code (though we just link for now)
            bot_link = f"https://t.me/{bot_me.username}?start=promo_{code}"
            
            channel_text = (
                "🎁 <b>Yangi Promo Kod!</b>\n\n"
                f"🎫 Kod: <code>{code}</code>\n"
                f"💎 Sovg'a: <b>{reward} Stars</b>\n"
                f"🔢 Limit: <b>{limit} ta</b> foydalanuvchi uchun!\n\n"
                "🏃‍♂️ Shoshiling! Pastdagi tugmani bosing va kodni ishlating!"
            )
            
            chan_kb = InlineKeyboardBuilder()
            chan_kb.button(
                text="Botga kirish va ishlatish 🚀", 
                url=bot_link
            )
            
            await bot.send_message(
                chat_id=REQUIRED_CHANNEL, 
                text=channel_text, 
                reply_markup=chan_kb.as_markup(),
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Failed to post promo to channel: {e}")
            await message.answer("⚠️ Eslatma: Promo kod yaratildi, lekin kanalga yuborishda xatolik yuz berdi (Bot kanalda admin ekanligini tekshiring).")
    except psycopg2.IntegrityError:
        await message.answer("❌ Boshqa promo kod tanlang, bu kod allaqachon mavjud.", reply_markup=get_admin_back_kb())
    except Exception as e:
        await message.answer(f"❌ Xatolik: {e}", reply_markup=get_admin_back_kb())
    
    await state.clear()

@dp.callback_query(F.data == "admin_list_promos")
async def cb_list_promos(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): return
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM promo_codes ORDER BY id DESC LIMIT 20")
    promos = cursor.fetchall()
    conn.close()
    
    if not promos:
        await callback.message.answer("📭 Hozircha promo kodlar yo'q.", reply_markup=get_admin_back_kb())
        await callback.answer()
        return
        
    text = "<b>📜 Oxirgi 20 ta promo kod:</b>\n\n"
    for p in promos:
        text += f"🎫 <code>{p['code']}</code> | 💎 {p['reward']} | 🔢 {p['current_uses']}/{p['max_uses']}\n"
    
    await callback.message.answer(text, reply_markup=get_admin_back_kb(), parse_mode="HTML")
    await callback.answer()

@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Amallar bekor qilindi.", reply_markup=types.ReplyKeyboardRemove())

async def main():
    setup_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
