import os
import asyncio
import json
import logging
from typing import Optional, List, Dict
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ContentType, InputMediaPhoto
)
import aiosqlite

# ==================== КОНФІГУРАЦІЯ (з змінних середовища) ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "9163548713:AAHkzTYjj1JKm4kPYZlFHBS4Q_GIznGjJjA")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5515955211"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1002683821244"))
THREAD_ID = os.getenv("23155")
THREAD_ID = int(THREAD_ID) if THREAD_ID and THREAD_ID.lower() != "none" else None
DB_PATH = "announcements.db"

# ======================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Буфер для альбомів
media_groups: Dict[str, List[Message]] = defaultdict(list)
media_group_tasks: Dict[str, asyncio.Task] = {}

# Стани
class AdminStates(StatesGroup):
    waiting_for_edit_reason = State()

# ==================== БАЗА ДАНИХ ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                text TEXT,
                media_type TEXT,
                media_file_ids TEXT,
                status TEXT DEFAULT 'pending',
                admin_message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def save_ad(user_id: int, username: str, text: str, media_type: str, media_file_ids: List[str]) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO ads (user_id, username, text, media_type, media_file_ids)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, username, text, media_type, json.dumps(media_file_ids))
        )
        await db.commit()
        return cursor.lastrowid

async def get_ad(ad_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM ads WHERE id = ?", (ad_id,))
        row = await cursor.fetchone()
        if row:
            row = dict(row)
            row["media_file_ids"] = json.loads(row["media_file_ids"]) if row["media_file_ids"] else []
        return row

async def update_ad_status(ad_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE ads SET status = ? WHERE id = ?", (status, ad_id))
        await db.commit()

async def set_admin_message_id(ad_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE ads SET admin_message_id = ? WHERE id = ?", (message_id, ad_id))
        await db.commit()

# ==================== КЛАВІАТУРИ ====================
def admin_keyboard(ad_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опублікувати", callback_data=f"approve:{ad_id}"),
            InlineKeyboardButton(text="❌ Відхилити", callback_data=f"reject:{ad_id}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Потрібне редагування", callback_data=f"edit:{ad_id}"),
        ]
    ])

# ==================== ОБРОБКА АЛЬБОМІВ ====================
async def process_media_group(media_group_id: str):
    await asyncio.sleep(0.8)
    messages = media_groups.pop(media_group_id, [])
    media_group_tasks.pop(media_group_id, None)

    if not messages:
        return

    first = messages[0]
    if first.from_user.id == ADMIN_ID:
        return

    user = first.from_user
    username = f"@{user.username}" if user.username else user.full_name
    text = first.caption or ""

    photo_ids = []
    for msg in messages:
        if msg.photo:
            photo_ids.append(msg.photo[-1].file_id)

    if not photo_ids:
        return

    file_ids = photo_ids[:10]
    ad_id = await save_ad(user.id, username, text, "album_photo", file_ids)

    await first.answer("✅ Оголошення отримано і надіслано на перевірку. Очікуй!")

    admin_text = f"🆕 Нове оголошення #{ad_id}\nВід: {username} (ID: {user.id})\n\n{text}"

    try:
        media = [InputMediaPhoto(media=fid, caption=admin_text if i == 0 else None) for i, fid in enumerate(file_ids)]
        sent_messages = await bot.send_media_group(ADMIN_ID, media=media)
        await bot.edit_message_reply_markup(
            chat_id=ADMIN_ID,
            message_id=sent_messages[0].message_id,
            reply_markup=admin_keyboard(ad_id)
        )
        await set_admin_message_id(ad_id, sent_messages[0].message_id)
    except Exception as e:
        logger.error(f"Помилка надсилання альбому адміну: {e}")
        await first.answer("Помилка при надсиланні на модерацію. Спробуй ще раз.")

# ==================== ОБРОБНИКИ ====================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привіт!\n\n"
        "Надішли мені своє оголошення:\n"
        "• Текст\n"
        "• Фото (до 10 штук в одному альбомі)\n"
        "• Відео або документ\n\n"
        "Я надішлю його на перевірку адміністратору.\n"
        "Після схвалення воно буде опубліковано в спільноті."
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📌 Як користуватися:\n"
        "1. Надішли оголошення (текст + до 10 фото альбомом).\n"
        "2. Зачекай на перевірку.\n"
        "3. Якщо потрібно виправити — отримаєш повідомлення.\n\n"
        "Адмін може схвалити або попросити редагування."
    )

@router.message(F.content_type.in_({ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.DOCUMENT}))
async def handle_message(message: Message):
    if message.from_user.id == ADMIN_ID:
        return

    if message.media_group_id:
        media_groups[message.media_group_id].append(message)
        if message.media_group_id not in media_group_tasks:
            media_group_tasks[message.media_group_id] = asyncio.create_task(
                process_media_group(message.media_group_id)
            )
        return

    user = message.from_user
    username = f"@{user.username}" if user.username else user.full_name
    text = message.caption or message.text or ""

    media_type = "text"
    file_ids: List[str] = []

    if message.photo:
        media_type = "photo"
        file_ids = [message.photo[-1].file_id]
    elif message.video:
        media_type = "video"
        file_ids = [message.video.file_id]
    elif message.document:
        media_type = "document"
        file_ids = [message.document.file_id]

    if not text and not file_ids:
        await message.answer("Надішли текст або медіа для оголошення.")
        return

    ad_id = await save_ad(user.id, username, text, media_type, file_ids)
    await message.answer("✅ Оголошення отримано і надіслано на перевірку. Очікуй!")

    admin_text = f"🆕 Нове оголошення #{ad_id}\nВід: {username} (ID: {user.id})\n\n{text}"

    try:
        if media_type == "photo":
            sent = await bot.send_photo(ADMIN_ID, file_ids[0], caption=admin_text, reply_markup=admin_keyboard(ad_id))
        elif media_type == "video":
            sent = await bot.send_video(ADMIN_ID, file_ids[0], caption=admin_text, reply_markup=admin_keyboard(ad_id))
        elif media_type == "document":
            sent = await bot.send_document(ADMIN_ID, file_ids[0], caption=admin_text, reply_markup=admin_keyboard(ad_id))
        else:
            sent = await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_keyboard(ad_id))

        await set_admin_message_id(ad_id, sent.message_id)
    except Exception as e:
        logger.error(f"Помилка надсилання адміну: {e}")
        await message.answer("Помилка при надсиланні на модерацію. Спробуй ще раз.")

# ==================== CALLBACKS АДМІНА ====================
@router.callback_query(F.data.startswith("approve:"))
async def approve_ad(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ заборонено", show_alert=True)
        return

    ad_id = int(callback.data.split(":")[1])
    ad = await get_ad(ad_id)
    if not ad or ad["status"] != "pending":
        await callback.answer("Оголошення вже оброблено", show_alert=True)
        return

    text = ad["text"] or ""
    media_type = ad["media_type"]
    file_ids = ad["media_file_ids"]

    try:
        kwargs = {"disable_notification": True}
        if THREAD_ID:
            kwargs["message_thread_id"] = THREAD_ID

        if media_type == "album_photo" and file_ids:
            media = [InputMediaPhoto(media=fid, caption=text if i == 0 else None) for i, fid in enumerate(file_ids)]
            await bot.send_media_group(CHANNEL_ID, media=media, **kwargs)
        elif media_type == "photo" and file_ids:
            await bot.send_photo(CHANNEL_ID, file_ids[0], caption=text, **kwargs)
        elif media_type == "video" and file_ids:
            await bot.send_video(CHANNEL_ID, file_ids[0], caption=text, **kwargs)
        elif media_type == "document" and file_ids:
            await bot.send_document(CHANNEL_ID, file_ids[0], caption=text, **kwargs)
        else:
            await bot.send_message(CHANNEL_ID, text, **kwargs)

        await update_ad_status(ad_id, "approved")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply("✅ Опубліковано (без звуку)!")
        await bot.send_message(ad["user_id"], f"✅ Твоє оголошення #{ad_id} опубліковано!")
        await callback.answer("Опубліковано")
    except Exception as e:
        logger.error(f"Помилка публікації: {e}")
        await callback.answer("Помилка публікації", show_alert=True)

@router.callback_query(F.data.startswith("reject:"))
async def reject_ad(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ заборонено", show_alert=True)
        return

    ad_id = int(callback.data.split(":")[1])
    ad = await get_ad(ad_id)
    if not ad or ad["status"] != "pending":
        await callback.answer("Оголошення вже оброблено", show_alert=True)
        return

    await update_ad_status(ad_id, "rejected")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply("❌ Відхилено — користувачу надіслано запит на редагування")

    await bot.send_message(
        ad["user_id"],
        f"❌ Твоє оголошення #{ad_id} потребує редагування.\n\n"
        "Будь ласка, виправ попередній варіант і надішли його знову."
    )
    await callback.answer("Відхилено")

@router.callback_query(F.data.startswith("edit:"))
async def request_edit(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ заборонено", show_alert=True)
        return

    ad_id = int(callback.data.split(":")[1])
    ad = await get_ad(ad_id)
    if not ad or ad["status"] != "pending":
        await callback.answer("Оголошення вже оброблено", show_alert=True)
        return

    await state.update_data(edit_ad_id=ad_id)
    await state.set_state(AdminStates.waiting_for_edit_reason)
    await callback.message.reply("✏️ Напиши причину / що потрібно виправити:")
    await callback.answer()

@router.message(AdminStates.waiting_for_edit_reason, F.from_user.id == ADMIN_ID)
async def process_edit_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    ad_id = data.get("edit_ad_id")
    if not ad_id:
        await state.clear()
        return

    ad = await get_ad(ad_id)
    if not ad:
        await state.clear()
        return

    reason = message.text or "Потрібне редагування."
    await bot.send_message(
        ad["user_id"],
        f"✏️ Твоє оголошення #{ad_id} потребує редагування:\n\n{reason}\n\n"
        "Надішли виправлену версію (можна знову до 10 фото)."
    )
    await update_ad_status(ad_id, "needs_edit")
    await message.answer("Повідомлення про редагування надіслано користувачу.")
    await state.clear()

# ==================== ЗАПУСК ====================
async def main():
    await init_db()
    logger.info("Бот запущено")
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
