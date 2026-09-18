import asyncio
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import database as db

# ============================================================
#                       الإعدادات
# ============================================================
BOT_TOKEN = "8963186902:AAGA1cW-DwFLtXdE4If93019eax05YoCnXw"
ADMIN_IDS = [5669045945]  # ضع آيدي التليجرام الخاص بك/بالأدمنز هنا

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# حالات المحادثة (ConversationHandler states)
(
    ADD_NAME,
    ADD_CONTENT,
    ADD_TIMER_CHOICE,
    ADD_TIMER_SECONDS,
    ADD_TIMER_MESSAGE,
    DEL_SELECT,
    BROADCAST_WAIT,
    FORCESUB_WAIT,
    FAKESUB_COUNT_WAIT,
    FAKESUB_CHANNELS_WAIT,
) = range(10)

CONTENT_TYPE_MAP = {
    "photo": "photo",
    "video": "video",
    "document": "document",
    "audio": "audio",
    "voice": "voice",
    "animation": "animation",
}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ============================================================
#                    الاشتراك الإجباري
# ============================================================
async def get_unjoined_channels(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> list[str]:
    """يرجع قائمة القنوات التي لم يشترك بها المستخدم بعد."""
    channels = db.get_force_sub_channels()
    unjoined = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status in ("left", "kicked"):
                unjoined.append(ch)
        except (Forbidden, BadRequest):
            # البوت ليس أدمن في القناة أو خطأ آخر - نتجاهلها حتى لا نعطل البوت
            logger.warning(f"لا يمكن التحقق من العضوية في {ch}")
    return unjoined


def build_force_sub_keyboard(channels: list[str], check_callback: str = "check_sub") -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        handle = ch if ch.startswith("@") else f"@{ch}"
        rows.append([InlineKeyboardButton(f"📢 اشترك في {handle}", url=f"https://t.me/{handle.lstrip('@')}")])
    rows.append([InlineKeyboardButton("✅ لقد اشتركت", callback_data=check_callback)])
    return InlineKeyboardMarkup(rows)


async def send_force_sub_message(update_or_query, channels: list[str]):
    text = "⚠️ يجب عليك الاشتراك في القنوات التالية أولاً لاستخدام البوت:"
    kb = build_force_sub_keyboard(channels)
    if isinstance(update_or_query, Update):
        await update_or_query.message.reply_text(text, reply_markup=kb)
    else:
        await update_or_query.edit_message_text(text, reply_markup=kb)


# ---------------- الاشتراك الإجباري الوهمي ----------------
async def try_fake_sub_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    إذا كانت الميزة مفعّلة ولم يصل المستخدم للعدد المطلوب من محاولات /start،
    يعرض رسالة اشتراك وهمية ويرجع True (أي: يجب إيقاف التنفيذ هنا).
    """
    if not db.is_fake_sub_enabled():
        return False

    user_id = update.effective_user.id
    required = db.get_fake_sub_required()
    count = db.increment_start_count(user_id)

    if count >= required:
        return False

    channels = db.get_fake_sub_channels()
    remaining = required - count
    text = (
        "⚠️ يجب عليك الاشتراك في القنوات التالية أولاً لاستخدام البوت:\n"
        f"(بعد الاشتراك اضغط تحقق، أو أرسل /start مرة أخرى)"
    )
    kb = build_force_sub_keyboard(channels, check_callback="fake_check_sub")
    await update.message.reply_text(text, reply_markup=kb)
    return True


async def fake_check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    required = db.get_fake_sub_required()
    count = db.increment_start_count(user.id)

    if count < required:
        await query.answer("لم يتم التحقق من اشتراكك بعد، حاول مرة أخرى ❌", show_alert=True)
        return

    await query.answer("تم التحقق ✅")
    db.add_subscriber(user.id, user.username)

    # بعد اجتياز البوابة الوهمية، تحقق من الاشتراك الإجباري الحقيقي إن وُجد
    unjoined = await get_unjoined_channels(context, user.id)
    if unjoined:
        await send_force_sub_message(query, unjoined)
        return

    await query.edit_message_text("تم التحقق من اشتراكك بنجاح ✅")
    await context.bot.send_message(
        chat_id=user.id,
        text="اختر من الأزرار أدناه:",
        reply_markup=build_user_keyboard(),
    )


# ============================================================
#                    لوحة المستخدم (الأزرار)
# ============================================================
def build_user_keyboard() -> ReplyKeyboardMarkup:
    buttons = db.get_all_buttons()
    if not buttons:
        return ReplyKeyboardRemove()
    kb = [[b["name"]] for b in buttons]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # بوابة الاشتراك الإجباري الوهمي (تعتمد على عدد مرات إرسال /start)
    if await try_fake_sub_gate(update, context):
        return

    db.add_subscriber(user.id, user.username)

    unjoined = await get_unjoined_channels(context, user.id)
    if unjoined:
        await send_force_sub_message(update, unjoined)
        return

    await update.message.reply_text(
        f"أهلاً بك {user.first_name} 👋\nاختر من الأزرار أدناه:",
        reply_markup=build_user_keyboard(),
    )


async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    unjoined = await get_unjoined_channels(context, user.id)
    if unjoined:
        await query.answer("لم تشترك بعد في جميع القنوات ❌", show_alert=True)
        return
    await query.answer("تم التحقق ✅")
    await query.edit_message_text("تم التحقق من اشتراكك بنجاح ✅")
    await context.bot.send_message(
        chat_id=user.id,
        text="اختر من الأزرار أدناه:",
        reply_markup=build_user_keyboard(),
    )


async def handle_user_button_press(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعالج ضغط المستخدم على أحد الأزرار في لوحة الرد."""
    user = update.effective_user
    text = update.message.text

    unjoined = await get_unjoined_channels(context, user.id)
    if unjoined:
        await send_force_sub_message(update, unjoined)
        return

    button = db.get_button_by_name(text)
    if not button:
        return  # ليس زراً معروفاً، تجاهل الرسالة

    content_type = button["content_type"]
    caption = button["content_text"]
    file_id = button["file_id"]

    sent_msg = None
    if content_type == "text":
        sent_msg = await update.message.reply_text(caption or "-")
    elif content_type == "photo":
        sent_msg = await update.message.reply_photo(file_id, caption=caption)
    elif content_type == "video":
        sent_msg = await update.message.reply_video(file_id, caption=caption)
    elif content_type == "document":
        sent_msg = await update.message.reply_document(file_id, caption=caption)
    elif content_type == "audio":
        sent_msg = await update.message.reply_audio(file_id, caption=caption)
    elif content_type == "voice":
        sent_msg = await update.message.reply_voice(file_id, caption=caption)
    elif content_type == "animation":
        sent_msg = await update.message.reply_animation(file_id, caption=caption)

    # جدولة حذف الرسالة إن كان مفعّلاً على هذا الزر
    delete_after = button["delete_after"]
    if delete_after and sent_msg:
        context.job_queue.run_once(
            delete_message_job,
            when=delete_after,
            data={
                "chat_id": sent_msg.chat_id,
                "message_id": sent_msg.message_id,
                "after_text": button["after_delete_text"],
            },
        )


async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    message_id = job_data["message_id"]
    after_text = job_data.get("after_text")

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (Forbidden, BadRequest) as e:
        logger.warning(f"تعذر حذف الرسالة {message_id} في {chat_id}: {e}")

    if after_text:
        try:
            await context.bot.send_message(chat_id=chat_id, text=after_text)
        except Forbidden:
            pass


# ============================================================
#                    لوحة تحكم الأدمن
# ============================================================
def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 إذاعة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("➕ إضافة زر", callback_data="admin_addbtn"),
         InlineKeyboardButton("➖ حذف زر", callback_data="admin_delbtn")],
        [InlineKeyboardButton("📋 عرض الأزرار", callback_data="admin_listbtn")],
        [InlineKeyboardButton("🔒 الاشتراك الإجباري", callback_data="admin_forcesub")],
        [InlineKeyboardButton("🎭 اشتراك إجباري وهمي", callback_data="admin_fakesub")],
        [InlineKeyboardButton("👥 عدد المشتركين", callback_data="admin_count")],
    ])


def fakesub_menu_keyboard() -> InlineKeyboardMarkup:
    enabled = db.is_fake_sub_enabled()
    toggle_label = "🔴 إيقاف الميزة" if enabled else "🟢 تفعيل الميزة"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data="fakesub_toggle")],
        [InlineKeyboardButton("🔢 تحديد عدد المحاولات", callback_data="fakesub_setcount")],
        [InlineKeyboardButton("📢 تحديد القنوات المعروضة", callback_data="fakesub_setchannels")],
        [InlineKeyboardButton("« رجوع", callback_data="admin_back")],
    ])


async def fakesub_menu_text() -> str:
    enabled = db.is_fake_sub_enabled()
    required = db.get_fake_sub_required()
    channels = db.get_fake_sub_channels()
    status = "🟢 مفعّلة" if enabled else "🔴 متوقفة"
    channels_str = ", ".join(channels) if channels else "لا يوجد"
    return (
        "🎭 الاشتراك الإجباري الوهمي\n\n"
        "لا يتحقق هذا الوضع من عضوية القنوات فعلياً، بل يطلب من المستخدم "
        "إرسال /start (أو الضغط على «لقد اشتركت») عدة مرات قبل عرض المحتوى.\n\n"
        f"الحالة: {status}\n"
        f"عدد المحاولات المطلوبة: {required}\n"
        f"القنوات المعروضة (شكلية فقط): {channels_str}"
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🛠 لوحة تحكم الأدمن", reply_markup=admin_panel_keyboard())


async def admin_panel_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("غير مصرح لك ❌", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    action = query.data

    if action == "admin_count":
        count = db.subscribers_count()
        await query.edit_message_text(
            f"👥 عدد المشتركين: {count}",
            reply_markup=admin_panel_keyboard(),
        )
        return ConversationHandler.END

    if action == "admin_listbtn":
        buttons = db.get_all_buttons()
        if not buttons:
            text = "لا توجد أزرار مضافة حالياً."
        else:
            lines = []
            for b in buttons:
                line = f"• {b['name']}  ({b['content_type']})"
                if b["delete_after"]:
                    line += f" ⏱ يُحذف بعد {b['delete_after']} ثانية"
                lines.append(line)
            text = "📋 الأزرار الحالية:\n" + "\n".join(lines)
        await query.edit_message_text(text, reply_markup=admin_panel_keyboard())
        return ConversationHandler.END

    if action == "admin_broadcast":
        await query.edit_message_text(
            "✏️ أرسل الآن الرسالة التي تريد إذاعتها (نص، صورة، فيديو، ...).\n"
            "أرسل /cancel للإلغاء."
        )
        return BROADCAST_WAIT

    if action == "admin_addbtn":
        context.user_data.pop("new_btn", None)
        await query.edit_message_text(
            "✏️ أرسل اسم الزر الجديد (النص الذي سيظهر على الزر).\n"
            "أرسل /cancel للإلغاء."
        )
        return ADD_NAME

    if action == "admin_delbtn":
        buttons = db.get_all_buttons()
        if not buttons:
            await query.edit_message_text("لا توجد أزرار لحذفها.", reply_markup=admin_panel_keyboard())
            return ConversationHandler.END
        kb = [[InlineKeyboardButton(f"🗑 {b['name']}", callback_data=f"delbtn_{b['id']}")] for b in buttons]
        kb.append([InlineKeyboardButton("« رجوع", callback_data="admin_back")])
        await query.edit_message_text("اختر الزر الذي تريد حذفه:", reply_markup=InlineKeyboardMarkup(kb))
        return DEL_SELECT

    if action == "admin_forcesub":
        channels = db.get_force_sub_channels()
        current = ", ".join(channels) if channels else "لا يوجد"
        kb = [
            [InlineKeyboardButton("✏️ تعديل القنوات", callback_data="forcesub_edit")],
            [InlineKeyboardButton("❌ إلغاء الاشتراك الإجباري", callback_data="forcesub_clear")],
            [InlineKeyboardButton("« رجوع", callback_data="admin_back")],
        ]
        await query.edit_message_text(
            f"🔒 الاشتراك الإجباري\nالقنوات الحالية: {current}",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return ConversationHandler.END

    if action == "admin_fakesub":
        await query.edit_message_text(await fakesub_menu_text(), reply_markup=fakesub_menu_keyboard())
        return ConversationHandler.END

    if action == "admin_back":
        await query.edit_message_text("🛠 لوحة تحكم الأدمن", reply_markup=admin_panel_keyboard())
        return ConversationHandler.END


async def forcesub_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ أرسل يوزرات القنوات مفصولة بفاصلة، مثال:\n"
        "@channel1, @channel2\n\n"
        "⚠️ يجب أن يكون البوت أدمن في هذه القنوات.\n"
        "أرسل /cancel للإلغاء."
    )
    return FORCESUB_WAIT


async def forcesub_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text
    channels = [c.strip() for c in raw.split(",") if c.strip()]
    db.set_force_sub_channels(channels)
    await update.message.reply_text(
        f"✅ تم تحديث قنوات الاشتراك الإجباري:\n{', '.join(channels)}",
        reply_markup=admin_panel_keyboard(),
    )
    return ConversationHandler.END


async def forcesub_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db.set_force_sub_channels([])
    await query.edit_message_text("✅ تم إلغاء الاشتراك الإجباري.", reply_markup=admin_panel_keyboard())


# ---------------- الاشتراك الإجباري الوهمي (إعدادات الأدمن) ----------------
async def fakesub_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db.set_fake_sub_enabled(not db.is_fake_sub_enabled())
    await query.edit_message_text(await fakesub_menu_text(), reply_markup=fakesub_menu_keyboard())


async def fakesub_setcount_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔢 أرسل عدد المحاولات (مرات إرسال /start) المطلوبة قبل ظهور المحتوى، مثال: 3\n"
        "أرسل /cancel للإلغاء."
    )
    return FAKESUB_COUNT_WAIT


async def fakesub_setcount_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return FAKESUB_COUNT_WAIT

    db.set_fake_sub_required(int(text))
    await update.message.reply_text(
        f"✅ تم تحديد عدد المحاولات المطلوبة: {text}",
        reply_markup=fakesub_menu_keyboard(),
    )
    return ConversationHandler.END


async def fakesub_setchannels_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📢 أرسل يوزرات القنوات (شكلية فقط، لا يتم التحقق منها فعلياً) مفصولة بفاصلة، مثال:\n"
        "@channel1, @channel2\n\n"
        "يمكنك أيضاً إرسال كلمة \"بدون\" لعدم عرض أي قنوات (فقط زر التحقق).\n"
        "أرسل /cancel للإلغاء."
    )
    return FAKESUB_CHANNELS_WAIT


async def fakesub_setchannels_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == "بدون":
        channels = []
    else:
        channels = [c.strip() for c in raw.split(",") if c.strip()]
    db.set_fake_sub_channels(channels)
    await update.message.reply_text(
        "✅ تم تحديث القنوات المعروضة.",
        reply_markup=fakesub_menu_keyboard(),
    )
    return ConversationHandler.END


# ---------------- إضافة زر ----------------
async def add_btn_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if db.get_button_by_name(name):
        await update.message.reply_text("⚠️ يوجد زر بنفس الاسم بالفعل، أرسل اسماً آخر أو /cancel للإلغاء.")
        return ADD_NAME
    context.user_data["new_btn"] = {"name": name}
    await update.message.reply_text(
        "📎 الآن أرسل محتوى الزر:\n"
        "- نص عادي، أو\n"
        "- صورة / فيديو / ملف / صوت (مع كابشن اختياري)\n\n"
        "أرسل /cancel للإلغاء."
    )
    return ADD_CONTENT


async def add_btn_receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    new_btn = context.user_data.get("new_btn")
    if not new_btn:
        await msg.reply_text("حدث خطأ، ابدأ من جديد عبر /admin")
        return ConversationHandler.END

    content_type = "text"
    content_text = None
    file_id = None

    if msg.photo:
        content_type = "photo"
        file_id = msg.photo[-1].file_id
        content_text = msg.caption
    elif msg.video:
        content_type = "video"
        file_id = msg.video.file_id
        content_text = msg.caption
    elif msg.document:
        content_type = "document"
        file_id = msg.document.file_id
        content_text = msg.caption
    elif msg.audio:
        content_type = "audio"
        file_id = msg.audio.file_id
        content_text = msg.caption
    elif msg.voice:
        content_type = "voice"
        file_id = msg.voice.file_id
        content_text = msg.caption
    elif msg.animation:
        content_type = "animation"
        file_id = msg.animation.file_id
        content_text = msg.caption
    elif msg.text:
        content_type = "text"
        content_text = msg.text
    else:
        await msg.reply_text("نوع محتوى غير مدعوم، حاول مجدداً أو أرسل /cancel")
        return ADD_CONTENT

    new_btn["content_type"] = content_type
    new_btn["content_text"] = content_text
    new_btn["file_id"] = file_id
    context.user_data["new_btn"] = new_btn

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ نعم، فعّل مؤقت الحذف", callback_data="timer_yes")],
        [InlineKeyboardButton("🚫 لا، بدون مؤقت", callback_data="timer_no")],
    ])
    await msg.reply_text(
        "هل تريد أن تُحذف هذه الرسالة تلقائياً بعد مدة معينة، "
        "وتُرسل رسالة أخرى بعد حذفها؟",
        reply_markup=kb,
    )
    return ADD_TIMER_CHOICE


async def add_btn_timer_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "timer_no":
        new_btn = context.user_data.get("new_btn")
        if not new_btn:
            await query.edit_message_text("حدث خطأ، ابدأ من جديد عبر /admin")
            return ConversationHandler.END
        db.add_button(
            new_btn["name"], new_btn["content_type"], new_btn["content_text"], new_btn["file_id"]
        )
        context.user_data.pop("new_btn", None)
        await query.edit_message_text(f"✅ تم إضافة الزر «{new_btn['name']}» بنجاح.")
        await context.bot.send_message(
            chat_id=query.from_user.id, text="🛠 لوحة تحكم الأدمن", reply_markup=admin_panel_keyboard()
        )
        return ConversationHandler.END

    # timer_yes
    await query.edit_message_text(
        "⏱ أرسل مدة الحذف بالثواني (مثال: 60 لحذف الرسالة بعد دقيقة واحدة).\n"
        "أرسل /cancel للإلغاء."
    )
    return ADD_TIMER_SECONDS


async def add_btn_timer_seconds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ أرسل رقماً صحيحاً أكبر من صفر (بالثواني).")
        return ADD_TIMER_SECONDS

    new_btn = context.user_data.get("new_btn")
    if not new_btn:
        await update.message.reply_text("حدث خطأ، ابدأ من جديد عبر /admin")
        return ConversationHandler.END

    new_btn["delete_after"] = int(text)
    context.user_data["new_btn"] = new_btn

    await update.message.reply_text(
        "✏️ الآن أرسل نص الرسالة التي تريد إرسالها للمستخدم بعد حذف الرسالة الأصلية.\n"
        "أرسل /cancel للإلغاء."
    )
    return ADD_TIMER_MESSAGE


async def add_btn_timer_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    after_text = update.message.text
    new_btn = context.user_data.get("new_btn")
    if not new_btn:
        await update.message.reply_text("حدث خطأ، ابدأ من جديد عبر /admin")
        return ConversationHandler.END

    db.add_button(
        new_btn["name"],
        new_btn["content_type"],
        new_btn["content_text"],
        new_btn["file_id"],
        delete_after=new_btn["delete_after"],
        after_delete_text=after_text,
    )
    context.user_data.pop("new_btn", None)

    await update.message.reply_text(
        f"✅ تم إضافة الزر «{new_btn['name']}» بنجاح، مع حذف تلقائي بعد {new_btn['delete_after']} ثانية.",
        reply_markup=admin_panel_keyboard(),
    )
    return ConversationHandler.END


# ---------------- حذف زر ----------------
async def del_btn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "admin_back":
        await query.edit_message_text("🛠 لوحة تحكم الأدمن", reply_markup=admin_panel_keyboard())
        return ConversationHandler.END

    btn_id = int(query.data.split("_", 1)[1])
    button = db.get_button(btn_id)
    if button:
        db.delete_button(btn_id)
        await query.edit_message_text(f"🗑 تم حذف الزر «{button['name']}».", reply_markup=admin_panel_keyboard())
    else:
        await query.edit_message_text("لم يتم العثور على الزر.", reply_markup=admin_panel_keyboard())
    return ConversationHandler.END


# ---------------- إذاعة ----------------
async def broadcast_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    subscribers = db.get_all_subscribers()
    await msg.reply_text(f"⏳ جارِ الإذاعة إلى {len(subscribers)} مشترك...")

    success = 0
    failed = 0
    for user_id in subscribers:
        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=msg.chat_id,
                message_id=msg.message_id,
            )
            success += 1
        except Forbidden:
            failed += 1
            db.remove_subscriber(user_id)  # المستخدم حظر البوت
        except Exception as e:
            failed += 1
            logger.warning(f"فشل الإرسال إلى {user_id}: {e}")
        await asyncio.sleep(0.05)  # تجنب حدود تيليجرام (flood control)

    await msg.reply_text(
        f"✅ اكتملت الإذاعة.\nنجح: {success}\nفشل: {failed}",
        reply_markup=admin_panel_keyboard(),
    )
    return ConversationHandler.END


# ---------------- إلغاء عام ----------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ تم الإلغاء.", reply_markup=admin_panel_keyboard())
    return ConversationHandler.END


# ============================================================
#                          التشغيل
# ============================================================
def main():
    db.init_db()
    application: Application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_panel_router, pattern="^admin_"),
            CallbackQueryHandler(forcesub_edit_start, pattern="^forcesub_edit$"),
            CallbackQueryHandler(fakesub_setcount_start, pattern="^fakesub_setcount$"),
            CallbackQueryHandler(fakesub_setchannels_start, pattern="^fakesub_setchannels$"),
        ],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_btn_receive_name)],
            ADD_CONTENT: [MessageHandler(~filters.COMMAND, add_btn_receive_content)],
            ADD_TIMER_CHOICE: [CallbackQueryHandler(add_btn_timer_choice, pattern="^timer_(yes|no)$")],
            ADD_TIMER_SECONDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_btn_timer_seconds)],
            ADD_TIMER_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_btn_timer_message)],
            DEL_SELECT: [CallbackQueryHandler(del_btn_callback, pattern="^(delbtn_|admin_back)")],
            BROADCAST_WAIT: [MessageHandler(~filters.COMMAND, broadcast_receive)],
            FORCESUB_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, forcesub_receive)],
            FAKESUB_COUNT_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, fakesub_setcount_receive)],
            FAKESUB_CHANNELS_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, fakesub_setchannels_receive)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_cmd))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))
    application.add_handler(CallbackQueryHandler(fake_check_sub_callback, pattern="^fake_check_sub$"))
    application.add_handler(CallbackQueryHandler(forcesub_clear, pattern="^forcesub_clear$"))
    application.add_handler(CallbackQueryHandler(fakesub_toggle, pattern="^fakesub_toggle$"))
    # أي رسالة نصية أخرى تعتبر ضغطاً محتملاً على أحد أزرار المستخدم
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_button_press))

    logger.info("البوت يعمل الآن...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
