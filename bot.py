import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters.callback_data import CallbackData
from aiogram.client.session.aiohttp import AiohttpSession
import re

# ==========================================
# تنظیمات دیتابیس (SQLite)
# ==========================================
conn = sqlite3.connect('reports_v2.db')
cursor = conn.cursor()
# جدول گزارش‌ها
cursor.execute('''CREATE TABLE IF NOT EXISTS reports 
                  (id INTEGER PRIMARY KEY, 
                   user_id INTEGER, 
                   username TEXT, 
                   path TEXT, 
                   text TEXT, 
                   created_at TEXT)''')
# جدول کاربران (برای ارسال همگانی)
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                  (user_id INTEGER PRIMARY KEY)''')
conn.commit()

# تابع کمکی برای ثبت نام کاربر در دیتابیس
def register_user(user_id):
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()

# ==========================================
# تنظیمات متغیرهای محیطی
# ==========================================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0)) 
PROXY_URL = os.getenv("PROXY_URL") 

logging.basicConfig(level=logging.INFO)
router = Router()

class DirectMessageForm(StatesGroup):
    waiting_for_dm = State()

class ReportForm(StatesGroup):
    waiting_for_details = State()

class ContentForm(StatesGroup):
    waiting_for_content = State()

class BroadcastForm(StatesGroup):
    waiting_for_message = State()

class ReportCB(CallbackData, prefix="rep"):
    action: str
    value: str = ""

# ==========================================
# توابع کیبورد و دکمه بازگشت
# ==========================================
def get_back_button():
    return [InlineKeyboardButton(text="🏠 بازگشت به منوی اصلی", callback_data=ReportCB(action="menu", value="main").pack())]

def translate_path(raw_value):
    labels = {
        "to_dorm": "سرویس دانشکده به خوابگاه",
        "to_uni": "سرویس خوابگاه به دانشکده",
        "تاخیر": "تاخیر",
        "نظافت": "نظافت",
        "راننده": "مشکلات اخلاقی راننده",
        "دما": "سیستم سرمایشی / گرمایشی"
    }
    
    if "issue_" in raw_value:
        parts = raw_value.replace(" > ", "_").split("_")
        route_key = f"{parts[1]}_{parts[2]}"
        route_name = labels.get(route_key, "مسیر نامشخص")
        time = f"{parts[3][:2]}:{parts[3][2:]}"
        issue = labels.get(parts[4], parts[4])
        return f"نقلیه > {route_name} > ساعت {time} > {issue}"
        
    return raw_value

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 گزارش مشکلات", callback_data=ReportCB(action="menu", value="problems").pack())],
        [InlineKeyboardButton(text="💡 انتقاد و پیشنهادات", callback_data=ReportCB(action="category", value="انتقاد و پیشنهاد").pack())],
        [InlineKeyboardButton(text="📰 ارسال محتوا نشریه", callback_data=ReportCB(action="category", value="ارسال محتوا").pack())],
        [InlineKeyboardButton(text="📞 ارتباط مستقیم", callback_data=ReportCB(action="category", value="ارتباط مستقیم").pack())]
    ])

def get_problems_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏢 مشکلات رفاهی", callback_data=ReportCB(action="menu", value="welfare").pack())],
        [InlineKeyboardButton(text="📚 مشکلات آموزشی", callback_data=ReportCB(action="category", value="آموزشی").pack())],
        [InlineKeyboardButton(text="📝 سایر موارد", callback_data=ReportCB(action="category", value="سایر رفاهی").pack())],
        get_back_button()
    ])

def get_welfare_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚌 سرویس حمل و نقل", callback_data=ReportCB(action="menu", value="transport").pack())],
        [InlineKeyboardButton(text="🍱 تغذیه", callback_data=ReportCB(action="menu", value="food").pack())],
        [InlineKeyboardButton(text="🧊 آبسردکن", callback_data=ReportCB(action="category", value="رفاهی - آبسردکن").pack())],
        [InlineKeyboardButton(text="🏪 بوفه و کپی", callback_data=ReportCB(action="category", value="رفاهی - بوفه/کپی").pack())],
        [InlineKeyboardButton(text="📝 سایر موارد", callback_data=ReportCB(action="category", value="رفاهی - سایر موارد").pack())],
        get_back_button()
    ])

def get_transport_routes():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="دانشکده به خوابگاه", callback_data=ReportCB(action="submenu", value="to_dorm").pack())],
        [InlineKeyboardButton(text="خوابگاه به دانشکده", callback_data=ReportCB(action="submenu", value="to_uni").pack())],
        get_back_button()
    ])

def get_times(route_type):
    hours = ["0830", "0900", "0930", "1000", "1030", "1100", "1130", "1200", "1230", "1300" , "1400" , "1430" , "1500"] if route_type == "to_dorm" else ["0730", "0800", "0830", "0900", "0930", "1000", "1030", "1100", "1130", "1200", "1230", "1300", "1330", "1400", "1430" , "1500"]
    keyboard = []
    for h in hours:
        keyboard.append([InlineKeyboardButton(text=f"ساعت {h[:2]}:{h[2:]}", callback_data=ReportCB(action="submenu", value=f"issue_{route_type}_{h}").pack())])
    keyboard.append(get_back_button())
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_transport_issues(base_val):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ تاخیر", callback_data=ReportCB(action="category", value=f"{base_val} > تاخیر").pack())],
        [InlineKeyboardButton(text="🧹 نظافت", callback_data=ReportCB(action="category", value=f"{base_val} > نظافت").pack())],
        [InlineKeyboardButton(text="👨‍✈️ راننده", callback_data=ReportCB(action="category", value=f"{base_val} > راننده").pack())],
        [InlineKeyboardButton(text="🌡 گرمایش/سرمایش", callback_data=ReportCB(action="category", value=f"{base_val} > دما").pack())],
        get_back_button()
    ])

# ==========================================
# هندلرهای ربات
# ==========================================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    register_user(message.from_user.id) # ثبت نام کاربر در دیتابیس
    await message.answer(f"درود خدمت {message.from_user.first_name} عزیز، به ربات گزارش‌دهی شورای صنفی دانشکده داروسازی شیراز خوش آمدی.\n\nشما می‌توانید از منوی زیر استفاده کنید یا اگر سوالی دارید همینجا تایپ کنید تا مستقیم با ما در ارتباط باشید:", reply_markup=get_main_menu())

@router.callback_query(ReportCB.filter(F.action == "menu"))
async def handle_menus(query: CallbackQuery, callback_data: ReportCB, state: FSMContext):
    if callback_data.value == "main": 
        await state.clear()
        await query.message.edit_text("🏠 به منوی اصلی بازگشتید. لطفاً یک گزینه را انتخاب کنید:", reply_markup=get_main_menu())
    elif callback_data.value == "problems": 
        await query.message.edit_text("انتخاب دسته مشکل:", reply_markup=get_problems_menu())
    elif callback_data.value == "welfare": 
        await query.message.edit_text("انتخاب رفاهی:", reply_markup=get_welfare_menu())
    elif callback_data.value == "transport": 
        await query.message.edit_text("انتخاب مسیر:", reply_markup=get_transport_routes())
    elif callback_data.value == "food":
        await query.message.edit_text("مشکل تغذیه:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="تاخیر غذا", callback_data=ReportCB(action="category", value="تغذیه > تاخیر").pack())],
            [InlineKeyboardButton(text="کیفیت غذا", callback_data=ReportCB(action="category", value="تغذیه > کیفیت غذا").pack())],
            [InlineKeyboardButton(text="کارکنان", callback_data=ReportCB(action="category", value="تغذیه > کارکنان").pack())],
            [InlineKeyboardButton(text="سایر", callback_data=ReportCB(action="category", value="تغذیه > سایر").pack())],
            get_back_button()
        ]))
    await query.answer()

@router.callback_query(ReportCB.filter(F.action == "category" and F.value == "ارسال محتوا"))
async def start_content_submission(query: CallbackQuery, state: FSMContext):
    await state.set_state(ContentForm.waiting_for_content)
    await query.message.edit_text("لطفا محتوای خود را اعم از متن، ویدیو ، تصویر و ... را برای ما ارسال کنید:", 
                                  reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_back_button()]))
    await query.answer()

@router.message(ContentForm.waiting_for_content)
async def process_content_submission(message: Message, state: FSMContext, bot: Bot):
    user = message.from_user
    user_name = f"@{user.username}" if user.username else user.first_name
    text = message.text or message.caption or "[بدون توضیحات متنی]"
    
    await bot.send_message(
        ADMIN_ID, 
        f"📰 <b>محتوای جدید برای نشریه</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>فرستنده:</b> {user_name}\n"
        f"🆔 <b>آیدی عددی:</b> <code>{user.id}</code>\n\n"
        f"💬 <b>متن/کپشن:</b>\n{text}\n"
        f"━━━━━━━━━━━━━━━━━━", 
        parse_mode="HTML"
    )
    
    if message.content_type != 'text':
        await bot.copy_message(ADMIN_ID, message.chat.id, message.message_id)
        
    await message.answer("✅ محتوای شما با موفقیت دریافت شد و برای تیم نشریه ارسال گردید.", reply_markup=get_main_menu())
    await state.clear()

@router.callback_query(ReportCB.filter(F.action == "category" and F.value == "ارتباط مستقیم"))
async def start_dm(query: CallbackQuery, state: FSMContext):
    await state.set_state(DirectMessageForm.waiting_for_dm)
    await query.message.edit_text("لطفاً پیام، انتقاد یا توضیحات خود را برای شورای صنفی بنویسید:", 
                                  reply_markup=InlineKeyboardMarkup(inline_keyboard=[get_back_button()]))
    await query.answer()

@router.message(DirectMessageForm.waiting_for_dm)
async def process_dm(message: Message, state: FSMContext, bot: Bot):
    user = message.from_user
    user_name = f"@{user.username}" if user.username else user.first_name
    text = message.text or message.caption or "[بدون توضیحات متنی]"
    
    await bot.send_message(
        ADMIN_ID, 
        f"📩 <b>پیام مستقیم جدید</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>فرستنده:</b> {user_name}\n"
        f"🆔 <b>آیدی عددی:</b> <code>{user.id}</code>\n\n"
        f"💬 <b>پیام:</b>\n{text}\n"
        f"━━━━━━━━━━━━━━━━━━", 
        parse_mode="HTML"
    )
    
    if message.content_type != 'text':
        await bot.copy_message(ADMIN_ID, message.chat.id, message.message_id)
        
    await message.answer("✅ پیام شما ثبت شد. به زودی توسط شورای صنفی پاسخ داده خواهد شد.", reply_markup=get_main_menu())
    await state.clear()

@router.callback_query(ReportCB.filter(F.action == "submenu"))
async def handle_submenu(query: CallbackQuery, callback_data: ReportCB):
    val = callback_data.value
    if val in ["to_dorm", "to_uni"]: await query.message.edit_text("انتخاب ساعت:", reply_markup=get_times(val))
    elif val.startswith("issue_"): await query.message.edit_text("نوع مشکل:", reply_markup=get_transport_issues(val))
    await query.answer()

@router.callback_query(ReportCB.filter(F.action == "category"))
async def final_category(query: CallbackQuery, callback_data: ReportCB, state: FSMContext):
    full_path = translate_path(callback_data.value)
    await state.update_data(full_path=full_path)
    await state.set_state(ReportForm.waiting_for_details)
    await query.message.edit_text(f"✅ مسیر: <b>{full_path}</b>\n\nلطفاً توضیحات را بنویسید یا تایید کنید (پشتیبانی از عکس و ویدیو):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تایید و ثبت نهایی", callback_data="confirm_final")],
        get_back_button()
    ]), parse_mode="HTML")

@router.message(ReportForm.waiting_for_details)
async def process_report_text(message: Message, state: FSMContext, bot: Bot):
    text = message.text or message.caption or "[محتوای چندرسانه‌ای]"
    await state.update_data(report_text=text)
    await finalize_and_send_report(message, state, bot, original_message=message)

@router.callback_query(F.data == "confirm_final")
async def confirm_report(query: CallbackQuery, state: FSMContext, bot: Bot):
    await state.update_data(report_text="[بدون توضیحات اضافی]")
    await finalize_and_send_report(query.message, state, bot, is_callback=True)
    await query.answer()

@router.message(F.text == "/list_reports")
async def list_reports(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    cursor.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    
    if not rows:
        await message.answer("هیچ گزارشی یافت نشد.")
        return
        
    response = "📑 ۱۰ گزارش اخیر:\n\n"
    for row in rows:
        response += (f"👤 کاربر: {row[2]} (<code>{row[1]}</code>)\n"
                     f"🕒 زمان: {row[5]}\n"
                     f"📌 مسیر: {row[3]}\n"
                     f"📝 شرح: {row[4][:40]}...\n━━━━━━━━━━━━\n")
    await message.answer(response, parse_mode="HTML")

# ==========================================
# سیستم ارسال پیام همگانی (Broadcast) مخصوص ادمین
# ==========================================
@router.message(Command("broadcast"))
async def start_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BroadcastForm.waiting_for_message)
    await message.answer("📢 <b>حالت ارسال همگانی فعال شد.</b>\n\nلطفاً پیام خود را بفرستید (متن، عکس، ویدیو، فایل و ... پشتیبانی می‌شود).\nبرای لغو این عملیات دستور /cancel را ارسال کنید.", parse_mode="HTML")

@router.message(Command("cancel"), StateFilter(BroadcastForm.waiting_for_message))
async def cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ عملیات ارسال همگانی لغو شد.")

@router.message(BroadcastForm.waiting_for_message)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    
    if not users:
        await message.answer("❌ هیچ کاربری در دیتابیس یافت نشد.")
        return
        
    await message.answer(f"⏳ در حال ارسال پیام به {len(users)} کاربر...\nلطفاً صبور باشید.")
    
    success_count = 0
    fail_count = 0
    
    for (u_id,) in users:
        try:
            # کپی کردن پیام ادمین برای کاربران (پشتیبانی از همه مدیاها)
            await bot.copy_message(chat_id=u_id, from_chat_id=message.chat.id, message_id=message.message_id)
            success_count += 1
            # ایجاد تاخیر برای جلوگیری از مسدود شدن توسط تلگرام
            await asyncio.sleep(0.05) 
        except Exception:
            fail_count += 1
            
    await message.answer(f"✅ <b>گزارش ارسال همگانی:</b>\n\n🟢 موفق: {success_count}\n🔴 ناموفق (ربات را بلاک کرده‌اند): {fail_count}", parse_mode="HTML")

# ==========================================
# سیستم ریپلای ادمین (پشتیبانی از عکس و ویدیو)
# ==========================================
@router.message(F.chat.id == ADMIN_ID)
async def admin_reply(message: Message, bot: Bot):
    if message.reply_to_message and message.reply_to_message.text:
        match = re.search(r'(\d{5,12})', message.reply_to_message.text)
        if match:
            target_id = match.group(1)
            try:
                text_content = message.text or message.caption or ""
                reply_text = f"💬 <b>پاسخ شورای صنفی:</b>\n\n{text_content}" if text_content else "💬 <b>پاسخ شورای صنفی:</b>"
                
                if message.content_type == 'text':
                    await bot.send_message(target_id, reply_text, parse_mode="HTML")
                else:
                    await bot.copy_message(target_id, message.chat.id, message.message_id, caption=reply_text, parse_mode="HTML")
                        
                await message.answer("✅ پاسخ برای کاربر ارسال شد.")
            except Exception as e:
                await message.answer(f"❌ خطا در ارسال (شاید کاربر ربات را بلاک کرده): {e}")
        else:
            await message.answer("❌ آیدی عددی در پیام پیدا نشد!")

# ==========================================
# سیستم چت آزاد (اگر کاربر در منویی نبود و چت کرد)
# ==========================================
@router.message(StateFilter(None), F.chat.id != ADMIN_ID)
async def process_free_chat(message: Message, bot: Bot):
    register_user(message.from_user.id) # اطمینان از ثبت کاربر
    user = message.from_user
    user_name = f"@{user.username}" if user.username else user.first_name
    text = message.text or message.caption or "[بدون توضیحات متنی]"
    
    await bot.send_message(
        ADMIN_ID, 
        f"💬 <b>پیام چت آزاد (خارج از فرم)</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>فرستنده:</b> {user_name}\n"
        f"🆔 <b>آیدی عددی:</b> <code>{user.id}</code>\n\n"
        f"📝 <b>متن:</b>\n{text}\n"
        f"━━━━━━━━━━━━━━━━━━", 
        parse_mode="HTML"
    )
    
    if message.content_type != 'text':
        await bot.copy_message(ADMIN_ID, message.chat.id, message.message_id)

async def finalize_and_send_report(msg_query, state, bot, is_callback=False, original_message=None):
    data = await state.get_data()
    full_path = data.get("full_path", "نامشخص")
    report_text = data.get("report_text", "[بدون توضیحات اضافی]")
    user = msg_query.from_user if not is_callback else msg_query.chat
    
    iran_time = datetime.utcnow() + timedelta(hours=3, minutes=30)
    time_str = iran_time.strftime("%Y/%m/%d - %H:%M")
    username_str = f"@{user.username}" if user.username else user.first_name
    
    cursor.execute("INSERT INTO reports (user_id, username, path, text, created_at) VALUES (?, ?, ?, ?, ?)", 
                   (user.id, username_str, full_path, report_text, time_str))
    conn.commit()
    
    admin_text = (f"🚨 <b>گزارش جدید شورای صنفی</b>\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"📌 <b>مسیر:</b> {full_path}\n"
                  f"🕒 <b>زمان ثبت:</b> {time_str}\n\n"
                  f"👤 <b>فرستنده:</b> {username_str}\n"
                  f"🆔 <b>آیدی عددی:</b> <code>{user.id}</code>\n\n"
                  f"📝 <b>شرح:</b>\n{report_text}\n"
                  f"━━━━━━━━━━━━━━━━━━")
    
    await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
    
    if original_message and original_message.content_type != 'text':
        await bot.copy_message(ADMIN_ID, original_message.chat.id, original_message.message_id)
        
    text_to_user = f"✅ گزارش شما برای مسیرِ «{full_path}» ثبت شد.\nسپاس از همکاری شما. به زودی رسیدگی خواهد شد."
    if is_callback: await msg_query.edit_text(text_to_user, reply_markup=get_main_menu())
    else: await msg_query.answer(text_to_user, reply_markup=get_main_menu())
    await state.clear()

# ==========================================
# وب سرور و حلقه اجرایی
# ==========================================
async def handle_web(request):
    return web.Response(text="Bot is running smoothly!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else None
    bot = Bot(token=TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(router)
    
    await start_web_server()
    
    while True:
        try:
            logging.info("Bot is polling...")
            await dp.start_polling(bot)
        except Exception as e:
            logging.error(f"Telegram API Error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
