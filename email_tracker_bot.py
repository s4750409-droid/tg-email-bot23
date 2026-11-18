#!/usr/bin/env python3
"""
Telegram-бот для учёта почт (доступные / недоступные)
с автоматическим возвратом по таймеру.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ==================== НАСТРОЙКИ ====================
# Токен берётся из переменной окружения (Railway) или из кода
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_СВОЙ_ТОКЕН_СЮДА")

# Файл для хранения данных
DATA_FILE = Path(__file__).parent / "emails_data.json"

# Состояния ConversationHandler
WAITING_EMAIL = 1
WAITING_DURATION = 2

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ==================== РАБОТА С ДАННЫМИ ====================
def load_data() -> Dict:
    """Загружает данные из JSON-файла."""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Преобразуем строки дат обратно в datetime
                for email, info in data.get("unavailable", {}).items():
                    if isinstance(info.get("until"), str):
                        info["until"] = datetime.fromisoformat(info["until"])
                return data
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
    return {"available": [], "unavailable": {}}


def save_data(data: Dict) -> None:
    """Сохраняет данные в JSON-файл."""
    # Преобразуем datetime в строки для сериализации
    to_save = {
        "available": data["available"],
        "unavailable": {},
    }
    for email, info in data["unavailable"].items():
        to_save["unavailable"][email] = {
            "until": info["until"].isoformat() if isinstance(info["until"], datetime) else info["until"],
            "duration_hours": info.get("duration_hours"),
        }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура."""
    keyboard = [
        [KeyboardButton("➕ Добавить почту")],
        [KeyboardButton("✅ Доступные почты"), KeyboardButton("❌ Недоступные почты")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# ==================== ХЕНДЛЕРЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start."""
    await update.message.reply_text(
        "👋 Привет! Я бот для учёта почт.\n\n"
        "• Добавляй почты\n"
        "• Отмечай их как недоступные на определённое время\n"
        "• По истечении времени они автоматически вернутся в доступные\n\n"
        "Выбери действие:",
        reply_markup=get_main_keyboard(),
    )


async def add_email_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало добавления почты."""
    await update.message.reply_text(
        "📧 Введи адрес почты, которую хочешь добавить:\n"
        "(или /cancel для отмены)"
    )
    return WAITING_EMAIL


async def add_email_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохранение новой почты."""
    email = update.message.text.strip().lower()

    if "@" not in email or "." not in email:
        await update.message.reply_text(
            "⚠️ Это не похоже на email. Попробуй ещё раз или /cancel:"
        )
        return WAITING_EMAIL

    data = load_data()

    if email in data["available"] or email in data["unavailable"]:
        await update.message.reply_text(
            f"⚠️ Почта <b>{email}</b> уже есть в списке.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(),
        )
        return ConversationHandler.END

    data["available"].append(email)
    save_data(data)

    await update.message.reply_text(
        f"✅ Почта <b>{email}</b> добавлена в доступные!",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена текущего действия."""
    await update.message.reply_text(
        "❌ Действие отменено.",
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def show_available(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список доступных почт."""
    data = load_data()
    available = data["available"]

    if not available:
        await update.message.reply_text(
            "📭 Список доступных почт пуст.\nДобавь почту через кнопку «➕ Добавить почту».",
            reply_markup=get_main_keyboard(),
        )
        return

    keyboard = []
    for email in available:
        keyboard.append([InlineKeyboardButton(email, callback_data=f"av_{email}")])

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])

    await update.message.reply_text(
        f"✅ <b>Доступные почты</b> ({len(available)}):\n\n"
        "Нажми на почту, чтобы сделать её недоступной:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_unavailable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список недоступных почт."""
    data = load_data()
    unavailable = data["unavailable"]

    if not unavailable:
        await update.message.reply_text(
            "📭 Список недоступных почт пуст.",
            reply_markup=get_main_keyboard(),
        )
        return

    now = datetime.now()
    text_lines = [f"❌ <b>Недоступные почты</b> ({len(unavailable)}):\n"]

    keyboard = []
    for email, info in unavailable.items():
        until = info["until"]
        if isinstance(until, str):
            until = datetime.fromisoformat(until)

        remaining = until - now
        if remaining.total_seconds() <= 0:
            hours_left = "0"
            status = "⏱ скоро вернётся"
        else:
            total_seconds = int(remaining.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            hours_left = f"{hours}ч {minutes}м"
            status = f"осталось {hours_left}"

        text_lines.append(f"• <code>{email}</code>\n  └ {status}")
        keyboard.append([
            InlineKeyboardButton(
                f"🔄 {email} (вернуть сейчас)",
                callback_data=f"make_av_{email}",
            )
        ])

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])

    await update.message.reply_text(
        "\n".join(text_lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def available_email_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Меню для конкретной доступной почты."""
    query = update.callback_query
    await query.answer()

    email = query.data[3:]  # убираем "av_"

    keyboard = [
        [InlineKeyboardButton("❌ Убрать из доступных", callback_data=f"unav_{email}")],
        [InlineKeyboardButton("◀️ Назад к списку", callback_data="back_available")],
    ]

    await query.edit_message_text(
        f"📧 <b>{email}</b>\n\nВыбери действие:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def make_unavailable_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос времени недоступности."""
    query = update.callback_query
    await query.answer()

    email = query.data[5:]  # убираем "unav_"
    context.user_data["pending_email"] = email

    await query.edit_message_text(
        f"⏱ На сколько часов сделать почту <b>{email}</b> недоступной?\n\n"
        "Напиши число (например: <code>15</code> или <code>2.5</code>)\n"
        "Или /cancel для отмены.",
        parse_mode="HTML",
    )
    return WAITING_DURATION


async def make_unavailable_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Перевод почты в недоступные с таймером."""
    text = update.message.text.strip().replace(",", ".")

    try:
        hours = float(text)
        if hours <= 0:
            raise ValueError("Должно быть положительное число")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введи положительное число часов (например 15 или 0.5):"
        )
        return WAITING_DURATION

    email = context.user_data.get("pending_email")
    if not email:
        await update.message.reply_text(
            "⚠️ Ошибка: почта не найдена. Попробуй снова.",
            reply_markup=get_main_keyboard(),
        )
        return ConversationHandler.END

    data = load_data()

    if email not in data["available"]:
        await update.message.reply_text(
            f"⚠️ Почта {email} уже не в доступных.",
            reply_markup=get_main_keyboard(),
        )
        return ConversationHandler.END

    # Перемещаем
    data["available"].remove(email)
    until = datetime.now() + timedelta(hours=hours)
    data["unavailable"][email] = {
        "until": until,
        "duration_hours": hours,
    }
    save_data(data)

    # Ставим задачу на возврат
    job_name = f"return_{email}"
    # Удаляем старую задачу, если была
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_once(
        return_email_job,
        when=timedelta(hours=hours),
        data={"email": email},
        name=job_name,
    )

    hours_str = f"{hours:g}"
    await update.message.reply_text(
        f"❌ Почта <b>{email}</b> теперь недоступна на {hours_str} ч.\n"
        f"Вернётся автоматически: <b>{until.strftime('%d.%m.%Y %H:%M')}</b>",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(),
    )

    context.user_data.pop("pending_email", None)
    return ConversationHandler.END


async def return_email_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Фоновая задача: возвращает почту в доступные."""
    email = context.job.data["email"]
    data = load_data()

    if email in data["unavailable"]:
        del data["unavailable"][email]
        if email not in data["available"]:
            data["available"].append(email)
        save_data(data)
        logger.info(f"Почта {email} автоматически возвращена в доступные")


async def make_available_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Мгновенно вернуть почту в доступные."""
    query = update.callback_query
    await query.answer()

    email = query.data[8:]  # убираем "make_av_"
    data = load_data()

    if email in data["unavailable"]:
        del data["unavailable"][email]
        if email not in data["available"]:
            data["available"].append(email)
        save_data(data)

        # Удаляем задачу, если есть
        job_name = f"return_{email}"
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

        await query.edit_message_text(
            f"✅ Почта <b>{email}</b> возвращена в доступные!",
            parse_mode="HTML",
        )
    else:
        await query.edit_message_text("⚠️ Почта уже не в недоступных.")


async def back_to_available(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вернуться к списку доступных."""
    query = update.callback_query
    await query.answer()

    data = load_data()
    available = data["available"]

    if not available:
        await query.edit_message_text("📭 Список доступных почт пуст.")
        return

    keyboard = []
    for email in available:
        keyboard.append([InlineKeyboardButton(email, callback_data=f"av_{email}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])

    await query.edit_message_text(
        f"✅ <b>Доступные почты</b> ({len(available)}):\n\n"
        "Нажми на почту, чтобы сделать её недоступной:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Закрыть inline-меню."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Выбери действие на клавиатуре ниже 👇")


async def restore_jobs(application: Application) -> None:
    """При старте бота восстанавливает таймеры для уже недоступных почт."""
    data = load_data()
    now = datetime.now()

    for email, info in list(data["unavailable"].items()):
        until = info["until"]
        if isinstance(until, str):
            until = datetime.fromisoformat(until)

        remaining = (until - now).total_seconds()

        if remaining <= 0:
            # Время уже вышло — сразу возвращаем
            del data["unavailable"][email]
            if email not in data["available"]:
                data["available"].append(email)
            logger.info(f"При старте: {email} возвращена (время вышло)")
        else:
            job_name = f"return_{email}"
            application.job_queue.run_once(
                return_email_job,
                when=remaining,
                data={"email": email},
                name=job_name,
            )
            logger.info(f"Восстановлен таймер для {email}: через {remaining:.0f} сек")

    save_data(data)


def main() -> None:
    """Запуск бота."""
    if not BOT_TOKEN or BOT_TOKEN == "ВСТАВЬ_СВОЙ_ТОКЕН_СЮДА":
        print("=" * 50)
        print("❗ Не задан BOT_TOKEN")
        print("   • Локально: вставь токен в код")
        print("   • Railway: добавь переменную BOT_TOKEN в Variables")
        print("   Получить токен: https://t.me/BotFather")
        print("=" * 50)
        return

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(restore_jobs)
        .build()
    )

    # Conversation: добавление почты
    add_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^➕ Добавить почту$"), add_email_start),
            CommandHandler("add", add_email_start),
        ],
        states={
            WAITING_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_email_finish)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # Conversation: установка времени недоступности
    duration_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(make_unavailable_start, pattern=r"^unav_")
        ],
        states={
            WAITING_DURATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, make_unavailable_finish)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(add_conv)
    application.add_handler(duration_conv)

    application.add_handler(
        MessageHandler(filters.Regex("^✅ Доступные почты$"), show_available)
    )
    application.add_handler(
        MessageHandler(filters.Regex("^❌ Недоступные почты$"), show_unavailable)
    )

    application.add_handler(CallbackQueryHandler(available_email_menu, pattern=r"^av_"))
    application.add_handler(CallbackQueryHandler(make_available_now, pattern=r"^make_av_"))
    application.add_handler(CallbackQueryHandler(back_to_available, pattern=r"^back_available$"))
    application.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))

    print("🤖 Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
