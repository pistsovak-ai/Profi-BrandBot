import os
import json
import time
import base64
import asyncio
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

# ── CONFIG ──
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY", "")
KREA_KEY       = os.environ.get("KREA_KEY", "")
KREA_API_URL   = "https://api.krea.ai/v1"
VERCEL_KREA    = "https://profi-brand-generator.vercel.app/api/krea"

# Conversation states
WAITING_TOPIC      = 1
WAITING_IL_TYPE    = 2
WAITING_IL_DECO    = 3
WAITING_IL_RATIO   = 4
WAITING_PH_TYPE    = 5
WAITING_PH_LOC     = 6
WAITING_PH_MOOD    = 7
WAITING_PH_RATIO   = 8
WAITING_IMAGE_BC   = 9

# ── BRAND TOKENS ──
BRAND_TOKENS = {
    "illustration": {
        "style": "flat 3D illustration, parallel camera, no realistic perspective",
        "colors": "primary blue #E6EBFF to #A9BAFD gradient, red accent #FA2A48, white background",
        "geometry": "rounded shapes, smooth curves, no outlines",
        "texture": "subtle grain noise",
        "lighting": "stylized top-left light, no real shadows",
    },
    "photo": {
        "style": "Human Lifestyle documentary photography",
        "lighting": "natural daylight, low contrast, slightly cool temperature",
        "colors": "muted saturation, #E6EBFF atmosphere, #FA2A48 accent 5-15% of frame",
        "mood": "candid, warm, real moments not staged",
        "people": "real people, authentic emotions, comfort over status",
    }
}

BRANDBOOK_SYSTEM = """Ты — эксперт по брендингу Профи.ру. Анализируй макеты строго по брендбуку.

БРЕНДБУК ПРОФИ.РУ:
Цвета: красный #FA2A48 (основной акцент), синие #E6EBFF / #A9BAFD (фон и элементы), белый, чёрный
Иллюстрации: плоский 3D, параллельная камера, округлые формы, красный акцент, синяя палитра, без реалистичной перспективы
Фото (Human Lifestyle): натуральный свет, приглушённая насыщенность, живые сцены, люди важнее предметов
Логотип: охранное поле = 3 окружности знака, красный или белый логотип в зависимости от фона
Типографика: один гротеск, чёткая иерархия, без декоративных шрифтов
Композиция: чистота и воздух, один визуальный центр, нет перегруженности

Отвечай СТРОГО в JSON без markdown:
{"score": <0-100>, "verdict": "<одна фраза>", "items": [{"category": "<название>", "status": "<ok|warn|fail>", "comment": "<текст>"}]}"""


def build_illustration_prompt(topic: str, il_type: str = "single object", deco: str = "no decorative elements") -> str:
    deco_part = f"{deco}, " if deco != "no decorative elements" else "NO decorative elements, NO extra shapes, "
    return (
        f"{topic}, {il_type}, "
        f"flat 3D illustration, pseudo-3D, parallel orthographic camera, "
        f"smooth rounded shapes, soft edges, no outlines, "
        f"color: light periwinkle blue #A9BAFD to #E6EBFF gradient body, "
        f"single red accent #FA2A48 on one small detail only, "
        f"pure white background, "
        f"subtle grain noise texture, "
        f"very soft diffuse light no hard shadows no drop shadow, "
        f"{deco_part}"
        f"NO ground shadow, NO cast shadow, NO reflection, "
        f"NOT photorealistic, NOT glossy, NOT metallic, NOT shiny"
    )

def build_photo_prompt(topic: str, ph_type: str = "specialist at work", loc: str = "home interior", mood: str = "warm and comfortable") -> str:
    return (
        f"{topic}, {ph_type}, {loc}, mood: {mood}, "
        f"candid lifestyle documentary photography, "
        f"natural soft daylight, slightly cool color temperature, "
        f"low contrast, muted desaturated colors, "
        f"light blue #E6EBFF tones in atmosphere, "
        f"small red accent detail in frame, "
        f"real authentic moment not staged, "
        f"warm human connection, everyday life, "
        f"shot on mirrorless camera, f/2.8, "
        f"NOT stock photo, NOT posed, NOT studio lighting"
    )


async def generate_with_krea(prompt: str, aspect_ratio: str = "1:1") -> str | None:
    """Generate image via Vercel proxy → Krea API, returns image URL."""
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(VERCEL_KREA, json={
            "prompt": prompt,
            "kreaKey": KREA_KEY,
            "aspectRatio": aspect_ratio,
            "resolution": "1K",
        })
        data = r.json()
        print(f"Krea response: {data}")
        return data.get("imageUrl")


async def analyze_with_claude(image_b64: str, checks: list[str]) -> dict:
    """Send image to Claude for brand check, returns parsed JSON."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-opus-4-5",
                "max_tokens": 1500,
                "system": BRANDBOOK_SYSTEM,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                        {"type": "text", "text": f"Проверь макет. Параметры: {', '.join(checks)}. Верни только JSON."}
                    ]
                }]
            }
        )
        raw = r.json()["content"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)


# ── KEYBOARDS ──
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Иллюстрация", callback_data="mode_illustration"),
         InlineKeyboardButton("Фото", callback_data="mode_photo")],
        [InlineKeyboardButton("Бренд-чек макета", callback_data="mode_brandcheck")],
    ])

def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="cancel")]])

def il_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Один объект", callback_data="il_type_single"),
         InlineKeyboardButton("Сюжетная сцена", callback_data="il_type_scene")],
        [InlineKeyboardButton("Отмена", callback_data="cancel")],
    ])

def il_deco_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Sparkles", callback_data="il_deco_sparkles"),
         InlineKeyboardButton("Clouds", callback_data="il_deco_clouds")],
        [InlineKeyboardButton("Orbit lines", callback_data="il_deco_orbit"),
         InlineKeyboardButton("Coins", callback_data="il_deco_coins")],
        [InlineKeyboardButton("Ничего", callback_data="il_deco_none")],
        [InlineKeyboardButton("Отмена", callback_data="cancel")],
    ])

def il_ratio_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1:1", callback_data="ratio_1:1"),
         InlineKeyboardButton("4:5", callback_data="ratio_4:5"),
         InlineKeyboardButton("9:16", callback_data="ratio_9:16"),
         InlineKeyboardButton("16:9", callback_data="ratio_16:9")],
        [InlineKeyboardButton("Отмена", callback_data="cancel")],
    ])

def ph_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Специалист за работой", callback_data="ph_type_specialist")],
        [InlineKeyboardButton("Специалист + клиент", callback_data="ph_type_together")],
        [InlineKeyboardButton("Результат / до-после", callback_data="ph_type_result"),
         InlineKeyboardButton("Домашний момент", callback_data="ph_type_home")],
        [InlineKeyboardButton("Отмена", callback_data="cancel")],
    ])

def ph_loc_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Дом / квартира", callback_data="ph_loc_home"),
         InlineKeyboardButton("Кухня", callback_data="ph_loc_kitchen")],
        [InlineKeyboardButton("Рабочее место", callback_data="ph_loc_work"),
         InlineKeyboardButton("Детская / учёба", callback_data="ph_loc_kids")],
        [InlineKeyboardButton("Двор / парк", callback_data="ph_loc_outdoor"),
         InlineKeyboardButton("Ванная", callback_data="ph_loc_bath")],
        [InlineKeyboardButton("Дача", callback_data="ph_loc_dacha"),
         InlineKeyboardButton("Гостиная", callback_data="ph_loc_living")],
        [InlineKeyboardButton("Отмена", callback_data="cancel")],
    ])

def ph_mood_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Доверие", callback_data="ph_mood_trust"),
         InlineKeyboardButton("Комфорт", callback_data="ph_mood_comfort"),
         InlineKeyboardButton("Спокойствие", callback_data="ph_mood_calm")],
        [InlineKeyboardButton("Уверенность", callback_data="ph_mood_confidence"),
         InlineKeyboardButton("Уют", callback_data="ph_mood_cozy"),
         InlineKeyboardButton("Веселье", callback_data="ph_mood_fun")],
        [InlineKeyboardButton("Отмена", callback_data="cancel")],
    ])

def ph_ratio_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1:1", callback_data="ratio_1:1"),
         InlineKeyboardButton("4:5", callback_data="ratio_4:5"),
         InlineKeyboardButton("16:9", callback_data="ratio_16:9"),
         InlineKeyboardButton("3:2", callback_data="ratio_3:2")],
        [InlineKeyboardButton("Отмена", callback_data="cancel")],
    ])


# ── HANDLERS ──
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я *Profi Brand Generator* — генерирую фирменную графику Профи.ру по токенам брендбука.\n\n"
        "Выбери что хочешь сделать:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        await query.edit_message_text("Окей, отменила 👌\n\nЧто делаем дальше?", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    if data == "mode_illustration":
        ctx.user_data["mode"] = "illustration"
        await query.edit_message_text(
            "*Иллюстрация в стиле Профи.ру*\n\n"
            "Опиши что должно быть на картинке — тему, объект или сцену.\n\n"
            "_Например: будильник, свинья-копилка, ремонт квартиры, психолог_",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return WAITING_TOPIC

    if data == "mode_photo":
        ctx.user_data["mode"] = "photo"
        await query.edit_message_text(
            "*Фото в стиле Human Lifestyle*\n\n"
            "Опиши сцену или тему.\n\n"
            "_Например: репетитор с учеником, уборка дома, курьер у двери_",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return WAITING_TOPIC

    if data == "mode_brandcheck":
        ctx.user_data["mode"] = "brandcheck"
        await query.edit_message_text(
            "*Бренд-чек макета*\n\n"
            "Пришли скрин или фото макета — баннера, иллюстрации, поста.\n"
            "Я проверю его на соответствие брендбуку Профи.ру и дам оценку.",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return WAITING_IMAGE_BC

    if data == "again":
        mode = ctx.user_data.get("mode", "illustration")
        topic = ctx.user_data.get("last_topic", "")
        if topic:
            await query.edit_message_text(f"Генерирую ещё вариант... ⏳")
            await _generate_image(query.message, ctx, mode, topic, edit=False)
        return ConversationHandler.END

    if data == "menu":
        await query.edit_message_text("Что делаем дальше?", reply_markup=main_menu_keyboard())
        return ConversationHandler.END


async def topic_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text.strip()
    mode  = ctx.user_data.get("mode", "illustration")
    ctx.user_data["last_topic"] = topic

    if mode == "illustration":
        await update.message.reply_text(
            "*Иллюстрация · Шаг 2 из 4*\n\nТип иллюстрации:",
            parse_mode="Markdown", reply_markup=il_type_keyboard()
        )
        return WAITING_IL_TYPE
    else:
        await update.message.reply_text(
            "*Фото · Шаг 2 из 5*\n\nТип сцены:",
            parse_mode="Markdown", reply_markup=ph_type_keyboard()
        )
        return WAITING_PH_TYPE


async def _generate_image(msg, ctx, mode: str, topic: str, edit: bool, is_prompt: bool = False):
    if is_prompt:
        prompt = topic  # already built
    elif mode == "illustration":
        prompt = build_illustration_prompt(topic)
    else:
        prompt = build_photo_prompt(topic)
    label = "Иллюстрация" if mode == "illustration" else "Фото"

    try:
        image_url = await generate_with_krea(prompt)
        if not image_url:
            raise ValueError("Krea не вернула изображение")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Ещё вариант", callback_data="again"),
             InlineKeyboardButton("В меню", callback_data="menu")]
        ])

        caption = f"{label} · Nano Banana 2\n\n_{topic}_"
        if edit:
            await msg.delete()

        await msg.reply_photo(
            photo=image_url,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=keyboard
        ) if not edit else await msg.get_bot().send_photo(
            chat_id=msg.chat_id,
            photo=image_url,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=keyboard
        )

    except Exception as e:
        text = f"Ошибка генерации: {e}\n\nПопробуй ещё раз."
        if edit:
            await msg.edit_text(text, reply_markup=main_menu_keyboard())
        else:
            await msg.reply_text(text, reply_markup=main_menu_keyboard())


async def image_received_for_brandcheck(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo
    doc   = update.message.document

    if not photo and not doc:
        await update.message.reply_text("Пришли изображение (фото или файл PNG/JPG)")
        return WAITING_IMAGE_BC

    msg = await update.message.reply_text("Анализирую макет по брендбуку...")

    try:
        if photo:
            file = await photo[-1].get_file()
        else:
            file = await doc.get_file()

        file_bytes = await file.download_as_bytearray()
        image_b64  = base64.b64encode(file_bytes).decode()

        checks = ["Цвета", "Типографика", "Стиль иллюстрации", "Логотип", "Композиция"]
        result = await analyze_with_claude(image_b64, checks)

        score   = result.get("score", 0)
        verdict = result.get("verdict", "Проверка завершена")
        items   = result.get("items", [])

        score_emoji = "●" if score >= 75 else "○" if score >= 50 else "×"
        status_icon = {"ok": "+", "warn": "!", "fail": "−"}

        lines = [
            f"{score_emoji} *Соответствие бренду: {score}/100*",
            f"_{verdict}_\n",
        ]
        for item in items:
            icon = status_icon.get(item.get("status", ""), "•")
            lines.append(f"{icon} *{item['category']}*")
            lines.append(f"    {item['comment']}\n")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Проверить другой", callback_data="mode_brandcheck"),
             InlineKeyboardButton("В меню", callback_data="menu")]
        ])

        await msg.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)

    except Exception as e:
        await msg.edit_text(f"Ошибка анализа: {e}", reply_markup=main_menu_keyboard())

    return ConversationHandler.END


async def fallback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Используй кнопки меню или напиши /start",
        reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END


# ── MAIN ──
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(button_handler, pattern="^mode_"),
        ],
        states={
            WAITING_TOPIC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, topic_received),
                CallbackQueryHandler(button_handler, pattern="^cancel$"),
            ],
            WAITING_IL_TYPE: [
                CallbackQueryHandler(button_handler, pattern="^il_type_"),
                CallbackQueryHandler(button_handler, pattern="^cancel$"),
            ],
            WAITING_IL_DECO: [
                CallbackQueryHandler(button_handler, pattern="^il_deco_"),
                CallbackQueryHandler(button_handler, pattern="^cancel$"),
            ],
            WAITING_IL_RATIO: [
                CallbackQueryHandler(button_handler, pattern="^ratio_"),
                CallbackQueryHandler(button_handler, pattern="^cancel$"),
            ],
            WAITING_PH_TYPE: [
                CallbackQueryHandler(button_handler, pattern="^ph_type_"),
                CallbackQueryHandler(button_handler, pattern="^cancel$"),
            ],
            WAITING_PH_LOC: [
                CallbackQueryHandler(button_handler, pattern="^ph_loc_"),
                CallbackQueryHandler(button_handler, pattern="^cancel$"),
            ],
            WAITING_PH_MOOD: [
                CallbackQueryHandler(button_handler, pattern="^ph_mood_"),
                CallbackQueryHandler(button_handler, pattern="^cancel$"),
            ],
            WAITING_PH_RATIO: [
                CallbackQueryHandler(button_handler, pattern="^ratio_"),
                CallbackQueryHandler(button_handler, pattern="^cancel$"),
            ],
            WAITING_IMAGE_BC: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, image_received_for_brandcheck),
                CallbackQueryHandler(button_handler, pattern="^cancel$"),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.ALL, fallback))

    print("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
