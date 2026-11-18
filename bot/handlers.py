"""The conversation in the bot chat: registration (section 4), opening the app, the language, and
answering a transfer with the buttons under a message (PAY-2).

The steps of a conversation live in `context.user_data`:
    step      "name" | "phone" (registration), "decline" (typing a decline reason)
    lang      the language to speak
    name      the name accepted in the name step
    transfer  the transfer being declined
"""

import logging
from html import escape

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    WebAppInfo,
)
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from bot.backend import Backend, BackendError, BackendUnavailable
from bot.texts import LANGUAGE_NAMES, LANGUAGES, default_language, t

log = logging.getLogger(__name__)
Context = ContextTypes.DEFAULT_TYPE

UNAVAILABLE = "The service is temporarily unavailable. Please try again in a minute."
NOT_REGISTERED = "You are not registered yet. Press /start to register."
BLOCKED = "⛔ Your access has been disabled. Contact an administrator."


def _backend(context: Context) -> Backend:
    return context.application.bot_data["backend"]


def _lang(update: Update, context: Context) -> str:
    return context.user_data.get("lang") or default_language(update.effective_user.language_code)


def _refusal(lang: str, error: BackendError) -> str:
    return t(lang, error.message, **error.params)


def open_app(lang: str, url: str | None) -> InlineKeyboardMarkup | None:
    """ONB-5: Telegram doesn't let a bot open a Web App by itself, so messages carry an Open app button."""
    if not url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "📱 Open app"), web_app=WebAppInfo(url=url))]])


def language_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(name, callback_data=f"lang:{code}") for code, name in LANGUAGE_NAMES.items()]])


def phone_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton(t(lang, "📱 Share phone number"), request_contact=True)]],
                               resize_keyboard=True, one_time_keyboard=True)


# ---- registration


async def _ask_name(update: Update, context: Context) -> None:
    """Step 1. The Telegram profile name is offered as a one-tap suggestion when it is a full name (ONB-2)."""
    lang, tg = _lang(update, context), update.effective_user
    text, keyboard = t(lang, "To register, send your <b>first and last name</b>."), ReplyKeyboardRemove()
    try:
        suggestion = await _backend(context).check_name(" ".join(x for x in (tg.first_name, tg.last_name) if x))
        text += "\n" + t(lang, "If your Telegram name below is right, just tap it.")
        keyboard = ReplyKeyboardMarkup([[KeyboardButton(suggestion)]], resize_keyboard=True, one_time_keyboard=True)
    except BackendError:
        pass  # the profile name is not a first and last name: nothing to suggest
    await update.effective_chat.send_message(text, reply_markup=keyboard)


async def _ask_phone(update: Update, context: Context) -> None:
    """Step 2."""
    lang = _lang(update, context)
    text = t(lang, "Thanks, {name}! Now your <b>phone number</b>: tap the button below, or type it like +998 90 123 45 67.",
             name=escape(context.user_data.get("name", "")))
    await update.effective_chat.send_message(text, reply_markup=phone_keyboard(lang))


async def _greet(update: Update, context: Context, info: dict) -> None:
    """A registered person: greet by name and show the Open app button (ONB-8)."""
    user = info["user"]
    lang = context.user_data["lang"] = user["language"]
    if user["blocked"]:
        await update.effective_chat.send_message(t(lang, BLOCKED), reply_markup=ReplyKeyboardRemove())  # ROLE-6
        return
    text = t(lang, "Hello, {name}! Open the app to see your recordings and money.", name=escape(user["name"]))
    if not info.get("webapp_url"):
        text += "\n" + t(lang, "The app opens with the button next to the message field.")
    await update.effective_chat.send_message(text, reply_markup=open_app(lang, info.get("webapp_url")) or ReplyKeyboardRemove())


async def start(update: Update, context: Context) -> None:
    """/start. Registered: greet. Otherwise registration starts, or starts over, from the name (ONB-9)."""
    tg = update.effective_user
    info = await _backend(context).lookup(tg.id, tg.username)
    if info["registered"]:
        context.user_data.clear()
        await _greet(update, context, info)
        return
    lang = _lang(update, context)
    context.user_data.clear()
    context.user_data.update(step="name", lang=lang)
    await update.effective_chat.send_message(
        t(lang, "👋 Welcome! This bot shows what you earned for recording on your phone and keeps track of your payments."),
        reply_markup=language_buttons(),
    )
    await _ask_name(update, context)


async def _register(update: Update, context: Context, phone: str, verified: bool) -> None:
    """Save the account. The phone number is marked as shared through Telegram or typed (ONB-5)."""
    tg, lang = update.effective_user, _lang(update, context)
    try:
        info = await _backend(context).register(
            telegram_id=tg.id, username=tg.username, full_name=context.user_data.get("name", ""), phone=phone,
            phone_verified=verified, language=lang,
        )
    except BackendError as e:
        # ONB-6, ONB-7: the same question again, with a short explanation
        await update.effective_chat.send_message(_refusal(lang, e), reply_markup=phone_keyboard(lang))
        return
    context.user_data.clear()
    lang = context.user_data["lang"] = info["user"]["language"]
    await update.effective_chat.send_message(t(lang, "✅ You are registered, {name}!", name=escape(info["user"]["name"])),
                                             reply_markup=ReplyKeyboardRemove())
    text = t(lang, "Open the app to link your UseMinute account and see your recordings and money.")
    if not info.get("webapp_url"):
        text += "\n" + t(lang, "The app opens with the button next to the message field.")
    await update.effective_chat.send_message(text, reply_markup=open_app(lang, info.get("webapp_url")))


async def on_text(update: Update, context: Context) -> None:
    """A text message: the answer to the current step, or small talk."""
    step, lang, text = context.user_data.get("step"), _lang(update, context), update.effective_message.text or ""
    backend = _backend(context)
    if step == "name":
        try:
            context.user_data["name"] = await backend.check_name(text)
        except BackendError as e:
            await update.effective_chat.send_message(_refusal(lang, e))  # ONB-6: the same question again
            return
        context.user_data["step"] = "phone"
        await _ask_phone(update, context)
    elif step == "phone":
        try:
            phone = await backend.check_phone(text)
        except BackendError as e:
            await update.effective_chat.send_message(_refusal(lang, e), reply_markup=phone_keyboard(lang))
            return
        await _register(update, context, phone, verified=False)
    elif step == "decline":
        await _decline(update, context, context.user_data.get("transfer"), text)
    else:
        await _small_talk(update, context)


async def on_contact(update: Update, context: Context) -> None:
    """The Share phone number button. Only the person's own contact counts (ONB-3)."""
    lang, contact = _lang(update, context), update.effective_message.contact
    if context.user_data.get("step") != "phone":
        await _small_talk(update, context)
        return
    if contact.user_id != update.effective_user.id:
        await update.effective_chat.send_message(
            t(lang, "Please share <b>your own</b> contact with the button below, or type your number."), reply_markup=phone_keyboard(lang))
        return
    try:
        phone = await _backend(context).check_phone(contact.phone_number)
    except BackendError as e:
        await update.effective_chat.send_message(_refusal(lang, e), reply_markup=phone_keyboard(lang))
        return
    await _register(update, context, phone, verified=True)


async def on_other(update: Update, context: Context) -> None:
    """A photo, a sticker, an unknown command…: during registration the question is asked again (ONB-6)."""
    step, lang = context.user_data.get("step"), _lang(update, context)
    if step == "name":
        key = "That is a command, not a name. Please send your first and last name." \
            if (update.effective_message.text or "").startswith("/") else "Please send your first and last name as a text message."
        await update.effective_chat.send_message(t(lang, key))
    elif step == "phone":
        await update.effective_chat.send_message(
            t(lang, "Please send your phone number: tap the button below, or type it like +998 90 123 45 67."),
            reply_markup=phone_keyboard(lang))
    else:
        await _small_talk(update, context)


async def _small_talk(update: Update, context: Context) -> None:
    tg = update.effective_user
    info = await _backend(context).lookup(tg.id, tg.username)
    if info["registered"]:
        await _greet(update, context, info)
    else:
        await update.effective_chat.send_message(t(_lang(update, context), NOT_REGISTERED))


# ---- commands


async def app_command(update: Update, context: Context) -> None:
    if context.user_data.get("step") in ("name", "phone"):
        await on_other(update, context)
    else:
        await _small_talk(update, context)


async def language_command(update: Update, context: Context) -> None:
    await update.effective_chat.send_message(t(_lang(update, context), "🌐 Choose your language:"), reply_markup=language_buttons())


async def help_command(update: Update, context: Context) -> None:
    await update.effective_chat.send_message(t(
        _lang(update, context),
        "This bot shows what you earned for recording on your phone and keeps track of the money you are paid.\n\n"
        "/start — register or open the app\n/app — open the app\n/language — change the language",
    ))


async def on_language(update: Update, context: Context) -> None:
    """A language button. The choice also applies to the Web App and to notifications (APP-8)."""
    query, tg = update.callback_query, update.effective_user
    code = query.data.split(":", 1)[1]
    if code not in LANGUAGES:
        await query.answer()
        return
    info = await _backend(context).lookup(tg.id, tg.username)
    if info["registered"]:
        await _backend(context).set_language(tg.id, code)
    context.user_data["lang"] = code
    await query.answer(t(code, "Language: {name} ✅", name=LANGUAGE_NAMES[code]))
    step = context.user_data.get("step")
    if step == "name":  # registration goes on in the new language
        await _ask_name(update, context)
    elif step == "phone":
        await _ask_phone(update, context)
    elif info["registered"]:
        await _greet(update, context, {**info, "user": {**info["user"], "language": code}})


# ---- answering a transfer (section 9)


async def on_transfer(update: Update, context: Context) -> None:
    """Accept / Decline (a payment) or Received / Not received (funding) under a bot message."""
    query, tg, lang = update.callback_query, update.effective_user, _lang(update, context)
    _, transfer_id, action = query.data.split(":")
    backend = _backend(context)
    try:
        if action == "a":
            await backend.answer(int(transfer_id), tg.id, True)
            await query.answer(t(lang, "✅ Recorded: received"))
            return
        info = await backend.transfer(int(transfer_id), tg.id)
        if not info["can_answer"]:
            # Answered already, here or in the Web App (PAY-5): asking again makes the backend tidy the buttons
            await backend.answer(int(transfer_id), tg.id, False)
            return
    except BackendError as e:
        await query.answer(_refusal(lang, e), show_alert=True)
        return
    # PAY-3: declining offers an optional reason
    context.user_data.update(step="decline", transfer=int(transfer_id))
    await query.answer()
    await update.effective_chat.send_message(
        t(lang, "You are about to answer <b>not received</b>. Send the reason as a message, or decline without a reason."),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "Decline without a reason"), callback_data=f"trd:{transfer_id}:skip"),
            InlineKeyboardButton(t(lang, "↩️ Back"), callback_data=f"trd:{transfer_id}:back"),
        ]]),
    )


def _leave_decline(context: Context) -> None:
    context.user_data.pop("step", None)
    context.user_data.pop("transfer", None)


async def on_decline_choice(update: Update, context: Context) -> None:
    """The buttons under the "send the reason" message."""
    query, tg, lang = update.callback_query, update.effective_user, _lang(update, context)
    _, transfer_id, choice = query.data.split(":")
    if context.user_data.get("step") == "decline":
        _leave_decline(context)
    if choice == "back":
        await query.answer()
        await query.edit_message_text(t(lang, "Nothing was changed. You can still answer with the buttons above."))
        return
    try:
        await _backend(context).answer(int(transfer_id), tg.id, False)
    except BackendError as e:
        await query.answer(_refusal(lang, e), show_alert=True)
        await query.edit_message_text(_refusal(lang, e))
        return
    await query.answer(t(lang, "⛔ Recorded: not received"))
    await query.edit_message_text(t(lang, "⛔ Recorded: not received. The sender has been told."))


async def _decline(update: Update, context: Context, transfer_id: int | None, reason: str) -> None:
    """The reason arrived as a message."""
    lang = _lang(update, context)
    _leave_decline(context)
    if transfer_id is None:
        return
    try:
        await _backend(context).answer(transfer_id, update.effective_user.id, False, reason)
    except BackendError as e:
        await update.effective_chat.send_message(_refusal(lang, e))
        return
    await update.effective_chat.send_message(t(lang, "⛔ Recorded: not received. The sender has been told."))


# ---- errors


async def on_error(update: object, context: Context) -> None:
    """The backend is down: say so instead of going silent. Anything else is logged without message content."""
    if not isinstance(context.error, BackendUnavailable):
        log.error("Handler failed: %s", type(context.error).__name__, exc_info=context.error)
    if not isinstance(update, Update) or update.effective_chat is None:
        return
    lang = _lang(update, context) if update.effective_user else "uz"
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(t(lang, UNAVAILABLE), show_alert=True)
        else:
            await update.effective_chat.send_message(t(lang, UNAVAILABLE))
    except Exception:
        pass


def add_handlers(application: Application) -> None:
    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("start", start, filters=private))
    application.add_handler(CommandHandler("app", app_command, filters=private))
    application.add_handler(CommandHandler("language", language_command, filters=private))
    application.add_handler(CommandHandler("help", help_command, filters=private))
    application.add_handler(CallbackQueryHandler(on_language, pattern=r"^lang:\w+$"))
    application.add_handler(CallbackQueryHandler(on_transfer, pattern=r"^tr:\d+:[ad]$"))
    application.add_handler(CallbackQueryHandler(on_decline_choice, pattern=r"^trd:\d+:(skip|back)$"))
    application.add_handler(MessageHandler(private & filters.CONTACT, on_contact))
    application.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(MessageHandler(private, on_other))
    application.add_error_handler(on_error)
