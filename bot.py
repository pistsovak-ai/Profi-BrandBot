import os
import json
import base64
import asyncio
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY", "")
KREA_KEY       = os.environ.get("KREA_KEY", "")
VERCEL_KREA    = "https://profi-brand-generator.vercel.app/api/krea"

BRANDBOOK_SYSTEM = """You are a Profi.ru brand expert. Analyze layouts strictly by the brandbook.

PROFI.RU BRANDBOOK:
Colors: red #FA2A48 (main accent), blue #E6EBFF / #A9BAFD (backgrounds), white, black
Illustrations: flat 3D, parallel camera, rounded shapes, red accent, blue palette, no realistic perspective
Photo (Human Lifestyle): natural light, muted saturation, candid scenes, people over objects
Logo: safe zone = 3 circles of the sign, red or white depending on background
Typography: one sans-serif, clear hierarchy, no decorative fonts
Composition: clean and airy, one visual center, not cluttered

Reply STRICTLY in JSON without markdown:
{"score": <0-100>, "verdict": "<one phrase>", "items": [{"category": "<name>", "status": "<ok|warn|fail>", "comment": "<text>"}]}"""

# ── STATE MACHINE ──
# state: None | "il_type" | "il_deco" | "il_ratio" | "ph_type" | "ph_loc" | "ph_mood" | "ph_ratio" | "bc_wait"

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Иллюстрация", callback_data="mode_illustration"),
         InlineKeyboardButton("Фото", callback_data="mode_photo")],
        [InlineKeyboardButton("Бренд-чек макета", callback_data="mode_brandcheck")],
    ])

def il_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Один объект", callback_data="il_type_single"),
         InlineKeyboardButton("Сюжетная сцена", callback_data="il_type_scene")],
        [InlineKeyboardButton("Назад", callback_data="cancel")],
    ])

def il_deco_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Sparkles", callback_data="il_deco_sparkles"),
         InlineKeyboardButton("Clouds", callback_data="il_deco_clouds")],
        [InlineKeyboardButton("Orbit lines", callback_data="il_deco_orbit"),
         InlineKeyboardButton("Coins", callback_data="il_deco_coins")],
        [InlineKeyboardButton("Ничего", callback_data="il_deco_none")],
        [InlineKeyboardButton("Назад", callback_data="cancel")],
    ])

def il_ratio_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1:1", callback_data="ratio_1:1"),
         InlineKeyboardButton("4:5", callback_data="ratio_4:5"),
         InlineKeyboardButton("9:16", callback_data="ratio_9:16"),
         InlineKeyboardButton("16:9", callback_data="ratio_16:9")],
        [InlineKeyboardButton("Назад", callback_data="cancel")],
    ])

def ph_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Специалист за работой", callback_data="ph_type_specialist")],
        [InlineKeyboardButton("Специалист + клиент", callback_data="ph_type_together")],
        [InlineKeyboardButton("Результат / до-после", callback_data="ph_type_result"),
         InlineKeyboardButton("Домашний момент", callback_data="ph_type_home")],
        [InlineKeyboardButton("Назад", callback_data="cancel")],
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
        [InlineKeyboardButton("Назад", callback_data="cancel")],
    ])

def ph_mood_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Доверие", callback_data="ph_mood_trust"),
         InlineKeyboardButton("Комфорт", callback_data="ph_mood_comfort"),
         InlineKeyboardButton("Спокойствие", callback_data="ph_mood_calm")],
        [InlineKeyboardButton("Уверенность", callback_data="ph_mood_confidence"),
         InlineKeyboardButton("Уют", callback_data="ph_mood_cozy"),
         InlineKeyboardButton("Веселье", callback_data="ph_mood_fun")],
        [InlineKeyboardButton("Назад", callback_data="cancel")],
    ])

def ph_ratio_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1:1", callback_data="ratio_1:1"),
         InlineKeyboardButton("4:5", callback_data="ratio_4:5"),
         InlineKeyboardButton("16:9", callback_data="ratio_16:9"),
         InlineKeyboardButton("3:2", callback_data="ratio_3:2")],
        [InlineKeyboardButton("Назад", callback_data="cancel")],
    ])

def result_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Ещё вариант", callback_data="again"),
         InlineKeyboardButton("В меню", callback_data="menu")],
    ])

def bc_result_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Проверить другой", callback_data="mode_brandcheck"),
         InlineKeyboardButton("В меню", callback_data="menu")],
    ])

# ── PROMPT BUILDERS ──
def build_illustration_prompt(topic, il_type, deco, ratio):
    deco_map = {
        "il_deco_sparkles": "small sparkle stars around object",
        "il_deco_clouds":   "soft cloud shapes in background",
        "il_deco_orbit":    "orbit lines circles around object",
        "il_deco_coins":    "small coin elements scattered",
        "il_deco_none":     "",
    }
    type_map = {
        "il_type_single": "single object centered, minimalist",
        "il_type_scene":  "narrative scene with 2-3 objects",
    }
    deco_str = deco_map.get(deco, "")
    type_str = type_map.get(il_type, "single object centered")
    no_deco  = "NO decorative elements, NO extra shapes, " if not deco_str else f"{deco_str}, "

    return (
        f"{topic}, {type_str}, "
        f"2D flat vector illustration, strictly frontal view, zero perspective, "
        f"no 3D depth, no foreshortening, no isometric angle, "
        f"smooth rounded shapes, soft edges, no outlines no strokes, "
        f"light periwinkle blue #A9BAFD to #E6EBFF flat gradient fill, "
        f"single red accent #FA2A48 on one small detail only, "
        f"pure white background, "
        f"heavy film grain noise texture overlay all over the image, "
        f"no shadows, no drop shadow, no cast shadow, no ambient occlusion, "
        f"{no_deco}"
        f"NOT 3D render, NOT isometric, NOT perspective view, "
        f"NOT photorealistic, NOT glossy, NOT metallic, NOT shiny"
    )

def build_photo_prompt(topic, ph_type, loc, mood):
    type_map = {
        "ph_type_specialist": "professional specialist at work focused and skilled",
        "ph_type_together":   "specialist and client together warm interaction",
        "ph_type_result":     "before and after result transformation moment",
        "ph_type_home":       "casual home moment everyday life",
    }
    loc_map = {
        "ph_loc_home":    "apartment interior home environment",
        "ph_loc_kitchen": "kitchen cooking area",
        "ph_loc_work":    "workspace desk office",
        "ph_loc_kids":    "children room study area",
        "ph_loc_outdoor": "yard park outdoor",
        "ph_loc_bath":    "bathroom",
        "ph_loc_dacha":   "dacha country house garden",
        "ph_loc_living":  "living room cozy interior",
    }
    mood_map = {
        "ph_mood_trust":      "trust reliability",
        "ph_mood_comfort":    "comfort ease relaxed",
        "ph_mood_calm":       "calm peaceful serene",
        "ph_mood_confidence": "confidence professional pride",
        "ph_mood_cozy":       "cozy warm homey",
        "ph_mood_fun":        "fun joyful lighthearted",
    }
    return (
        f"{topic}, {type_map.get(ph_type,'specialist at work')}, "
        f"{loc_map.get(loc,'home interior')}, mood: {mood_map.get(mood,'warm comfortable')}, "
        f"candid lifestyle documentary photography, "
        f"natural soft daylight slightly cool temperature, "
        f"low contrast muted desaturated colors, "
        f"light blue #E6EBFF tones in atmosphere, "
        f"real authentic moment not staged, warm human connection, "
        f"shot on mirrorless camera f/2.8, "
        f"NOT stock photo, NOT posed, NOT studio lighting"
    )

# ── KREA + CLAUDE ──
async def generate_with_krea(prompt, aspect_ratio="1:1"):
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(VERCEL_KREA, json={
            "prompt": prompt,
            "kreaKey": KREA_KEY,
            "aspectRatio": aspect_ratio,
            "resolution": "1K",
        })
        data = r.json()
        return data.get("imageUrl")

async def analyze_with_claude(image_b64, checks):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": "claude-opus-4-5",
                "max_tokens": 1500,
                "system": BRANDBOOK_SYSTEM,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": f"Check this layout. Parameters: {', '.join(checks)}. Return only JSON."}
                ]}]
            }
        )
        raw = r.json()["content"][0]["text"].strip().replace("```json","").replace("```","").strip()
        return json.loads(raw)

# ── HANDLERS ──
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(
        "Привет! Я *Profi Brand Generator* — генерирую фирменную графику Профи.ру по токенам брендбука.\n\nВыбери что хочешь сделать:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    ud = ctx.user_data

    # ── MENU ──
    if d == "menu":
        ud.clear()
        await q.edit_message_text("Что делаем дальше?", reply_markup=main_menu_keyboard())
        return

    if d == "cancel":
        ud.clear()
        await q.edit_message_text("Отменено. Что делаем дальше?", reply_markup=main_menu_keyboard())
        return

    # ── START ILLUSTRATION ──
    if d == "mode_illustration":
        ud["mode"] = "illustration"
        ud["state"] = "il_topic"
        await q.edit_message_text(
            "*Иллюстрация · Шаг 1 из 4*\n\nОпиши тему или объект:\n\n_Например: будильник, свинья-копилка, психолог_",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="cancel")]]))
        return

    # ── START PHOTO ──
    if d == "mode_photo":
        ud["mode"] = "photo"
        ud["state"] = "ph_topic"
        await q.edit_message_text(
            "*Фото · Шаг 1 из 5*\n\nОпиши сцену — что происходит:\n\n_Например: репетитор объясняет ребёнку математику_",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="cancel")]]))
        return

    # ── START BRANDCHECK ──
    if d == "mode_brandcheck":
        ud["mode"] = "brandcheck"
        ud["state"] = "bc_wait"
        await q.edit_message_text(
            "*Бренд-чек макета*\n\nПришли скрин баннера, иллюстрации или поста — проверю на соответствие брендбуку Профи.ру.",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="cancel")]]))
        return

    # ── ILLUSTRATION STEPS ──
    if d in ("il_type_single", "il_type_scene"):
        ud["il_type"] = d
        ud["state"] = "il_deco"
        await q.edit_message_text("*Иллюстрация · Шаг 3 из 4*\n\nДекоративные элементы:", parse_mode="Markdown", reply_markup=il_deco_keyboard())
        return

    if d.startswith("il_deco_"):
        ud["il_deco"] = d
        ud["state"] = "il_ratio"
        await q.edit_message_text("*Иллюстрация · Шаг 4 из 4*\n\nСоотношение сторон:", parse_mode="Markdown", reply_markup=il_ratio_keyboard())
        return

    # ── PHOTO STEPS ──
    if d.startswith("ph_type_"):
        ud["ph_type"] = d
        ud["state"] = "ph_loc"
        await q.edit_message_text("*Фото · Шаг 3 из 5*\n\nЛокация:", parse_mode="Markdown", reply_markup=ph_loc_keyboard())
        return

    if d.startswith("ph_loc_"):
        ud["ph_loc"] = d
        ud["state"] = "ph_mood"
        await q.edit_message_text("*Фото · Шаг 4 из 5*\n\nНастроение:", parse_mode="Markdown", reply_markup=ph_mood_keyboard())
        return

    if d.startswith("ph_mood_"):
        ud["ph_mood"] = d
        ud["state"] = "ph_ratio"
        await q.edit_message_text("*Фото · Шаг 5 из 5*\n\nСоотношение сторон:", parse_mode="Markdown", reply_markup=ph_ratio_keyboard())
        return

    # ── RATIO → GENERATE ──
    if d.startswith("ratio_"):
        ud["ratio"] = d.replace("ratio_", "")
        mode = ud.get("mode", "illustration")
        topic = ud.get("topic", "")
        ratio = ud.get("ratio", "1:1")

        if mode == "illustration":
            prompt = build_illustration_prompt(topic, ud.get("il_type","il_type_single"), ud.get("il_deco","il_deco_none"), ratio)
        else:
            prompt = build_photo_prompt(topic, ud.get("ph_type","ph_type_specialist"), ud.get("ph_loc","ph_loc_home"), ud.get("ph_mood","ph_mood_comfort"))

        ud["last_prompt"] = prompt
        ud["state"] = "done"
        msg = await q.edit_message_text("Генерирую... обычно 20–40 секунд")
        await _do_generate(q.message.chat_id, msg, prompt, ratio, mode, ctx)
        return

    # ── AGAIN ──
    if d == "again":
        prompt = ud.get("last_prompt", "")
        ratio  = ud.get("ratio", "1:1")
        mode   = ud.get("mode", "illustration")
        if prompt:
            msg = await q.edit_message_text("Генерирую ещё вариант...")
            await _do_generate(q.message.chat_id, msg, prompt, ratio, mode, ctx)
        return

async def _do_generate(chat_id, msg, prompt, ratio, mode, ctx):
    try:
        url = await generate_with_krea(prompt, ratio)
        if not url:
            raise ValueError("Krea не вернула изображение")
        label = "Иллюстрация" if mode == "illustration" else "Фото"
        topic = ctx.user_data.get("topic", "")
        await msg.delete()
        await ctx.bot.send_photo(
            chat_id=chat_id,
            photo=url,
            caption=f"{label} · Nano Banana 2\n\n_{topic}_",
            parse_mode="Markdown",
            reply_markup=result_keyboard()
        )
    except Exception as e:
        await msg.edit_text(f"Ошибка генерации: {e}", reply_markup=main_menu_keyboard())

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = ctx.user_data
    state = ud.get("state")
    text = update.message.text.strip()

    if state == "il_topic":
        ud["topic"] = text
        ud["state"] = "il_type"
        await update.message.reply_text(
            "*Иллюстрация · Шаг 2 из 4*\n\nТип иллюстрации:",
            parse_mode="Markdown", reply_markup=il_type_keyboard())
        return

    if state == "ph_topic":
        ud["topic"] = text
        ud["state"] = "ph_type"
        await update.message.reply_text(
            "*Фото · Шаг 2 из 5*\n\nТип сцены:",
            parse_mode="Markdown", reply_markup=ph_type_keyboard())
        return

    # default
    await update.message.reply_text("Используй кнопки меню или напиши /start", reply_markup=main_menu_keyboard())

async def photo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = ctx.user_data
    if ud.get("state") != "bc_wait":
        await update.message.reply_text("Сначала выбери режим Бренд-чек в меню.", reply_markup=main_menu_keyboard())
        return

    msg = await update.message.reply_text("Анализирую макет по брендбуку...")
    try:
        photo = update.message.photo
        doc   = update.message.document
        file  = await (photo[-1] if photo else doc).get_file()
        image_b64 = base64.b64encode(await file.download_as_bytearray()).decode()

        checks = ["Цвета", "Типографика", "Стиль иллюстрации", "Логотип", "Композиция"]
        result = await analyze_with_claude(image_b64, checks)

        score   = result.get("score", 0)
        verdict = result.get("verdict", "Проверка завершена")
        items   = result.get("items", [])
        score_mark = "+" if score >= 75 else "~" if score >= 50 else "-"
        icons = {"ok": "+", "warn": "!", "fail": "-"}

        lines = [f"[{score_mark}] *Соответствие бренду: {score}/100*", f"_{verdict}_\n"]
        for item in items:
            lines.append(f"[{icons.get(item.get('status',''),'?')}] *{item['category']}*")
            lines.append(f"    {item['comment']}\n")

        await msg.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=bc_result_keyboard())
        ud["state"] = "done"
    except Exception as e:
        await msg.edit_text(f"Ошибка анализа: {e}", reply_markup=main_menu_keyboard())

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("Bot started")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
