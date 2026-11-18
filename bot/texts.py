"""What the bot says in the chat, in Uzbek and Russian. English is the source and the key:
`t(lang, "English text", **values)`. Refusals that come from the backend (a name or a phone number that
is not accepted, …) arrive as English text and are looked up here too.

Notifications about money and access are written by the backend (backend/texts.py); the bot only
delivers them.
"""

LANGUAGE_NAMES = {"uz": "O'zbekcha", "ru": "Русский", "en": "English"}
LANGUAGES = tuple(LANGUAGE_NAMES)

# With STRICT on (tests), a text that has no translation is an error instead of silently English
STRICT = False

TEXTS: dict[str, tuple[str, str]] = {
    # key (English): (Uzbek, Russian)
    # registration (section 4)
    "👋 Welcome! This bot shows what you earned for recording on your phone and keeps track of your payments.": (
        "👋 Xush kelibsiz! Bu bot telefoningizda yozib olganingiz uchun qancha ishlab topganingizni ko'rsatadi va to'lovlaringizni hisobga oladi.",
        "👋 Добро пожаловать! Этот бот показывает, сколько вы заработали за записи на телефоне, и ведёт учёт ваших выплат.",
    ),
    "To register, send your <b>first and last name</b>.": (
        "Ro'yxatdan o'tish uchun <b>ism va familiyangizni</b> yuboring.",
        "Чтобы зарегистрироваться, отправьте <b>имя и фамилию</b>.",
    ),
    "If your Telegram name below is right, just tap it.": (
        "Agar quyidagi Telegram ismingiz to'g'ri bo'lsa, uni bosing.",
        "Если ваше имя из Telegram ниже верно, просто нажмите на него.",
    ),
    "Please send your first and last name as a text message.": (
        "Iltimos, ism va familiyangizni matnli xabar qilib yuboring.",
        "Пожалуйста, отправьте имя и фамилию текстовым сообщением.",
    ),
    "Thanks, {name}! Now your <b>phone number</b>: tap the button below, or type it like +998 90 123 45 67.": (
        "Rahmat, {name}! Endi <b>telefon raqamingiz</b>: quyidagi tugmani bosing yoki +998 90 123 45 67 ko'rinishida yozing.",
        "Спасибо, {name}! Теперь <b>номер телефона</b>: нажмите кнопку ниже или напишите его в виде +998 90 123 45 67.",
    ),
    "📱 Share phone number": ("📱 Telefon raqamini yuborish", "📱 Отправить номер телефона"),
    "Please share <b>your own</b> contact with the button below, or type your number.": (
        "Iltimos, quyidagi tugma orqali <b>o'zingizning</b> kontaktingizni yuboring yoki raqamingizni yozing.",
        "Пожалуйста, отправьте <b>свой</b> контакт кнопкой ниже или напишите свой номер.",
    ),
    "Please send your phone number: tap the button below, or type it like +998 90 123 45 67.": (
        "Iltimos, telefon raqamingizni yuboring: quyidagi tugmani bosing yoki +998 90 123 45 67 ko'rinishida yozing.",
        "Пожалуйста, отправьте номер телефона: нажмите кнопку ниже или напишите его в виде +998 90 123 45 67.",
    ),
    "✅ You are registered, {name}!": ("✅ Ro'yxatdan o'tdingiz, {name}!", "✅ Вы зарегистрированы, {name}!"),
    "Open the app to link your UseMinute account and see your recordings and money.": (
        "UseMinute hisobingizni ulash hamda yozuvlaringiz va pulingizni ko'rish uchun ilovani oching.",
        "Откройте приложение, чтобы привязать аккаунт UseMinute и видеть свои записи и деньги.",
    ),
    "The app opens with the button next to the message field.": (
        "Ilova xabar yozish maydoni yonidagi tugma orqali ochiladi.",
        "Приложение открывается кнопкой рядом с полем ввода сообщения.",
    ),
    "📱 Open app": ("📱 Ilovani ochish", "📱 Открыть приложение"),
    "Hello, {name}! Open the app to see your recordings and money.": (
        "Salom, {name}! Yozuvlaringiz va pulingizni ko'rish uchun ilovani oching.",
        "Здравствуйте, {name}! Откройте приложение, чтобы видеть свои записи и деньги.",
    ),
    "You are not registered yet. Press /start to register.": (
        "Siz hali ro'yxatdan o'tmagansiz. Ro'yxatdan o'tish uchun /start ni bosing.",
        "Вы ещё не зарегистрированы. Нажмите /start, чтобы зарегистрироваться.",
    ),
    "⛔ Your access has been disabled. Contact an administrator.": (
        "⛔ Kirishingiz o'chirilgan. Administratorga murojaat qiling.",
        "⛔ Ваш доступ отключён. Обратитесь к администратору.",
    ),
    # refusals from the backend (backend/services/registration.py)
    "That is a command, not a name. Please send your first and last name.": (
        "Bu buyruq, ism emas. Iltimos, ism va familiyangizni yuboring.",
        "Это команда, а не имя. Пожалуйста, отправьте имя и фамилию.",
    ),
    "The name must be 2 to 128 characters. Please send your first and last name.": (
        "Ism 2 tadan 128 tagacha belgidan iborat bo'lishi kerak. Iltimos, ism va familiyangizni yuboring.",
        "Имя должно быть от 2 до 128 символов. Пожалуйста, отправьте имя и фамилию.",
    ),
    "A name has only letters. Please send your first and last name.": (
        "Ismda faqat harflar bo'ladi. Iltimos, ism va familiyangizni yuboring.",
        "В имени могут быть только буквы. Пожалуйста, отправьте имя и фамилию.",
    ),
    "Please send both your first name and your last name.": (
        "Iltimos, ismingizni ham, familiyangizni ham yuboring.",
        "Пожалуйста, отправьте и имя, и фамилию.",
    ),
    "That doesn't look like a phone number. Send it like +998 90 123 45 67, or tap the button.": (
        "Bu telefon raqamiga o'xshamaydi. Uni +998 90 123 45 67 ko'rinishida yuboring yoki tugmani bosing.",
        "Это не похоже на номер телефона. Отправьте его в виде +998 90 123 45 67 или нажмите кнопку.",
    ),
    "This phone number is already used by another account. Please contact an administrator.": (
        "Bu telefon raqami boshqa hisobda ishlatilgan. Iltimos, administratorga murojaat qiling.",
        "Этот номер телефона уже используется в другом аккаунте. Пожалуйста, обратитесь к администратору.",
    ),
    "You are not registered yet. Open the bot, press Start and register first.": (
        "Siz hali ro'yxatdan o'tmagansiz. Ro'yxatdan o'tish uchun /start ni bosing.",
        "Вы ещё не зарегистрированы. Нажмите /start, чтобы зарегистрироваться.",
    ),
    "Your access has been disabled. Contact an administrator.": (
        "Kirishingiz o'chirilgan. Administratorga murojaat qiling.",
        "Ваш доступ отключён. Обратитесь к администратору.",
    ),
    "This was already answered": ("Bunga allaqachon javob berilgan", "На это уже дан ответ"),
    "This transfer was not found": ("Bu o'tkazma topilmadi", "Эта передача не найдена"),
    "Only the receiver can answer this": ("Bunga faqat qabul qiluvchi javob bera oladi", "Ответить может только получатель"),
    "Choose Uzbek, Russian or English": ("O'zbek, rus yoki ingliz tilini tanlang", "Выберите узбекский, русский или английский"),
    "Check what you entered and try again": ("Kiritganingizni tekshirib, qayta urinib ko'ring", "Проверьте введённое и попробуйте ещё раз"),
    "Something went wrong": ("Xatolik yuz berdi", "Что-то пошло не так"),
    # language, help
    "🌐 Choose your language:": ("🌐 Tilni tanlang:", "🌐 Выберите язык:"),
    "Language: {name} ✅": ("Til: {name} ✅", "Язык: {name} ✅"),
    "This bot shows what you earned for recording on your phone and keeps track of the money you are paid.\n\n"
    "/start — register or open the app\n/app — open the app\n/language — change the language": (
        "Bu bot telefoningizda yozib olganingiz uchun qancha ishlab topganingizni ko'rsatadi va sizga to'langan pulni hisobga oladi.\n\n"
        "/start — ro'yxatdan o'tish yoki ilovani ochish\n/app — ilovani ochish\n/language — tilni o'zgartirish",
        "Этот бот показывает, сколько вы заработали за записи на телефоне, и ведёт учёт выплаченных вам денег.\n\n"
        "/start — регистрация или открыть приложение\n/app — открыть приложение\n/language — сменить язык",
    ),
    "Register or open the app": ("Ro'yxatdan o'tish yoki ilovani ochish", "Регистрация или открыть приложение"),
    "Open the app": ("Ilovani ochish", "Открыть приложение"),
    "Change the language": ("Tilni o'zgartirish", "Сменить язык"),
    "Help": ("Yordam", "Помощь"),
    # answering a transfer in the chat (section 9)
    "✅ Recorded: received": ("✅ Qayd etildi: oldim", "✅ Записано: получено"),
    "⛔ Recorded: not received": ("⛔ Qayd etildi: olmadim", "⛔ Записано: не получено"),
    "You are about to answer <b>not received</b>. Send the reason as a message, or decline without a reason.": (
        "Siz <b>olmadim</b> deb javob bermoqchisiz. Sababini xabar qilib yuboring yoki sababsiz rad eting.",
        "Вы собираетесь ответить <b>не получено</b>. Отправьте причину сообщением или отклоните без причины.",
    ),
    "Decline without a reason": ("Sababsiz rad etish", "Отклонить без причины"),
    "↩️ Back": ("↩️ Orqaga", "↩️ Назад"),
    "Nothing was changed. You can still answer with the buttons above.": (
        "Hech narsa o'zgarmadi. Yuqoridagi tugmalar orqali javob berishingiz mumkin.",
        "Ничего не изменено. Вы всё ещё можете ответить кнопками выше.",
    ),
    "⛔ Recorded: not received. The sender has been told.": (
        "⛔ Qayd etildi: olmadim. Yuboruvchiga xabar berildi.",
        "⛔ Записано: не получено. Отправителю сообщено.",
    ),
    "The service is temporarily unavailable. Please try again in a minute.": (
        "Xizmat vaqtincha ishlamayapti. Iltimos, bir daqiqadan so'ng qayta urinib ko'ring.",
        "Сервис временно недоступен. Пожалуйста, попробуйте через минуту.",
    ),
}


def t(lang: str, text: str, **values) -> str:
    if lang != "en":
        entry = TEXTS.get(text)
        if entry is not None:
            text = entry[0 if lang == "uz" else 1]
        elif STRICT:
            raise KeyError(f"No translation for: {text}")
    try:
        return text.format(**values) if values else text
    except (KeyError, IndexError):
        return text


def default_language(telegram_language: str | None) -> str:
    """Uzbek, or Russian when the person's Telegram is set to Russian (NFR-15)."""
    return "ru" if (telegram_language or "").lower().startswith("ru") else "uz"
