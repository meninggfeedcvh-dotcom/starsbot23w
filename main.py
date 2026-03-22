import os
import sqlite3
import logging
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import WebAppInfo, ReplyKeyboardMarkup, KeyboardButton

load_dotenv()

# --- Configuration ---
TOKEN = os.getenv("BOT_TOKEN")
DATABASE = "database.db" # Local to the bot directory
ADMIN_IDS = (os.getenv("ADMIN_IDS") or "").split(",") 
DB_PATH = os.path.abspath(DATABASE)

logging.basicConfig(level=logging.INFO)
class UserStates(StatesGroup):
    entering_promo = State()

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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def setup_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            stars_balance INTEGER DEFAULT 0,
            total_orders INTEGER DEFAULT 0,
            total_stars INTEGER DEFAULT 0,
            joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            referred_by TEXT,
            api_token TEXT
        )
    """)
    
    # Promo codes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, promo_id)
        )
    """)
    
    conn.commit()
    conn.close()
    logging.info("Database setup completed (tables verified/created).")

def init_user(user_id, username, referred_by=None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (str(user_id),))
    user = cursor.fetchone()
    
    if not user:
        cursor.execute(
            "INSERT INTO users (id, username, referred_by, balance, stars_balance) VALUES (?, ?, ?, 0, 0)",
            (str(user_id), username, referred_by)
        )
        if referred_by:
            # Reward the referrer
            cursor.execute("UPDATE users SET stars_balance = stars_balance + 1 WHERE id = ?", (referred_by,))
            # TODO: Send notification to referrer if possible
        conn.commit()
    conn.close()

REQUIRED_CHANNEL = "@devel0per_junior" # Updated to match your actual channel

# --- Middleware/Helper: Check Channel Subscription ---
async def check_subscription(user_id: int):
    # Admins bypass the check
    if is_admin(user_id):
        return True
        
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
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

# --- Handlers ---
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_id = str(message.from_user.id)
    username = message.from_user.username or "NoUsername"
    
    # Check for referral in args (/start ref123)
    args = message.text.split()
    referred_by = args[1] if len(args) > 1 else None

    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Check if user exists
    cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    exists = cursor.fetchone()
    
    if not exists:
        # 2. Add new user
        cursor.execute(
            "INSERT INTO users (id, username, balance, total_orders, total_stars, joined_at, referred_by, stars_balance) VALUES (?, ?, 0, 0, 0, ?, ?, 0)",
            (user_id, username, datetime.now(), referred_by)
        )
        
        # 3. Reward referrer (+1 Star)
        if referred_by and referred_by != user_id:
            cursor.execute("UPDATE users SET stars_balance = stars_balance + 1 WHERE id = ?", (referred_by,))
            try:
                await bot.send_message(referred_by, f"🎉 Yangi referal! Sizga +1 Star 💎 berildi.")
            except: pass
        
        conn.commit()
    # Check Subscription
    if not await check_subscription(message.from_user.id):
        await message.answer(
            "⚠️ Botdan foydalanish uchun kanalimizga a'zo bo'lishingiz kerak!",
            reply_markup=get_join_keyboard()
        )
        return

    # Web App Button
    kb = InlineKeyboardBuilder()
    kb.button(text="Web Appni ochish 🚀", web_app=WebAppInfo(url=f"https://buyurtma-production.up.railway.app/?user_id={user_id}")) # LOCAL TESTING: http://localhost:3000
    kb.button(text="Balans 💰", callback_data="check_balance")
    kb.button(text="Referal Havola 👥", callback_data="get_ref")
    kb.button(text="Promo Kod 🎁", callback_data="enter_promo")
    kb.adjust(1, 2)

    await message.answer(
        f"<b>Salom {message.from_user.full_name}!</b> 👋\n\n"
        "✨ <b>STARS BAZA</b> botiga xush kelibsiz!\n\n"
        "Bu yerda siz:\n"
        "💎 <b>Telegram Stars</b> - Eng arzon narxlarda\n"
        "👑 <b>Telegram Premium</b> - Tezkor va ishonchli\n"
        "💰 <b>Referal tizimi</b> - Har bir do'st uchun pul ishlang\n\n"
        "Pastdagi tugma orqali <b>Web App</b>ni oching va xaridni boshlang!",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "check_balance")
async def check_balance(callback: types.CallbackQuery):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, stars_balance FROM users WHERE id = ?", (str(callback.from_user.id),))
    res = cursor.fetchone()
    conn.close()
    
    if res:
        balance, stars = res['balance'], res['stars_balance']
        formatted_balance = "{:,}".format(balance).replace(",", " ")
        await callback.message.answer(
            f"<b>💳 Sizning balansingiz:</b>\n\n"
            f"💰 Asosiy: <b>{formatted_balance} so'm</b>\n"
            f"💎 Stars: <b>{stars} ta</b>",
            parse_mode="HTML"
        )
    else:
        await callback.answer("Foydalanuvchi topilmadi.")
    await callback.answer()

@dp.callback_query(F.data == "get_ref")
async def get_ref(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    bot_username = (await bot.get_me()).username # Get bot's actual username
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    count = cursor.fetchone()[0]
    conn.close()

    await callback.message.answer(
        f"💎 Referal tizimi\n\n"
        f"Do'stingizni taklif qiling va har biriga +1 Star 💎 oling!\n\n"
        f"🔗 Sizning havolangiz: `{ref_link}`\n"
        f"👥 Hammasi bo'lib: {count} ta referal",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: types.CallbackQuery):
    if await check_subscription(callback.from_user.id):
        await callback.message.edit_text("✅ Rahmat! Endi botdan foydalanishingiz mumkin.")
        await start_cmd(callback.message) # Re-run start
    else:
        await callback.answer("❌ Siz hali ham kanalga a'zo emassiz!", show_alert=True)

@dp.callback_query(F.data == "enter_promo")
async def cb_promo(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("🎁 Promo kodni yuboring (yoki /cancel):")
    await state.set_state(UserStates.entering_promo)

# --- Promo Handling ---
@dp.message(UserStates.entering_promo)
async def promo_handler(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Bekor qilindi.")
        return
        
    code = message.text.strip()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM promo_codes WHERE code = ?", (code,))
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
        
    cursor.execute("SELECT * FROM promo_usage WHERE user_id = ? AND promo_id = ?", (str(message.from_user.id), promo['id']))
    if cursor.fetchone():
        await message.answer("❌ Siz bu promo kodni allaqachon ishlatgansiz.")
        conn.close()
        await state.clear()
        return
        
    # All checks passed, reward the user
    reward = promo['reward']
    cursor.execute("UPDATE users SET stars_balance = stars_balance + ? WHERE id = ?", (reward, str(message.from_user.id)))
    cursor.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE id = ?", (promo['id'],))
    cursor.execute("INSERT INTO promo_usage (user_id, promo_id) VALUES (?, ?)", (str(message.from_user.id), promo['id']))
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Tabriklaymiz! Hisobingizga {reward} Stars 💎 qo'shildi.")
    await state.clear()

# --- Admin Panel ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizda ushbu komanda uchun ruxsat yo'q.")
        return
    
    conn = get_db()
    cursor = conn.cursor()
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
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall()
    conn.close()

    count = 0
    progress = await message.answer(f"⏳ {len(users)} ta foydalanuvchiga yuborish boshlandi...")
    
    for user in users:
        try:
            await bot.send_message(user['id'], msg_text)
            count += 1
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
        await message.answer("❌ Iltimos, faqat raqam kiriting!", reply_markup=get_admin_back_kb())
        await state.clear()
        return
        
    amount = int(amount_str)
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, target_id))
        if cursor.rowcount > 0:
            conn.commit()
            await message.answer(f"✅ Foydalanuvchi {target_id} balansiga {amount} so'm qo'shildi.", reply_markup=get_admin_back_kb())
            try:
                await bot.send_message(target_id, f"💰 Hisobingiz {amount} so'mga to'ldirildi!")
            except: pass
        else:
            await message.answer("❌ Foydalanuvchi topilmadi.", reply_markup=get_admin_back_kb())
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
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (target_id,))
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
        await message.answer("❌ Faqat raqam kiriting!", reply_markup=get_admin_back_kb())
        await state.clear()
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
        await message.answer("❌ Faqat raqam kiriting!", reply_markup=get_admin_back_kb())
        await state.clear()
        return
    
    data = await state.get_data()
    code = data['new_promo_code']
    reward = data['new_promo_reward']
    limit = int(message.text)
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO promo_codes (code, reward, max_uses, current_uses) VALUES (?, ?, ?, 0)",
            (code, reward, limit)
        )
        conn.commit()
        conn.close()
        await message.answer(f"✅ Promo kod yaratildi!\n\n🎫 Kod: <b>{code}</b>\n💎 Sovg'a: {reward} Stars\n🔢 Limit: {limit} ta", reply_markup=get_admin_back_kb(), parse_mode="HTML")
    except sqlite3.IntegrityError:
        await message.answer("❌ Boshqa promo kod tanlang, bu kod allaqachon mavjud.", reply_markup=get_admin_back_kb())
    except Exception as e:
        await message.answer(f"❌ Xatolik: {e}", reply_markup=get_admin_back_kb())
    
    await state.clear()

@dp.callback_query(F.data == "admin_list_promos")
async def cb_list_promos(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): return
    
    conn = get_db()
    cursor = conn.cursor()
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
