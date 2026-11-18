"""The backend's messages to people in Uzbek and Russian. English is the source and the key:
`t(lang, "English text", **values)` returns the translation with the values filled in.

Section names (Payments → My workers, Users…) are spelled as the Web App shows them: backend/webapp/i18n.js.
"""

# With STRICT on (tests), a text that has no translation is an error instead of silently English
STRICT = False

TEXTS: dict[str, tuple[str, str]] = {
    # key (English): (Uzbek, Russian)
    "📱 Open app": ("📱 Ilovani ochish", "📱 Открыть приложение"),
    # transfers
    "✅ Accept": ("✅ Qabul qilish", "✅ Принять"),
    "⛔ Decline": ("⛔ Rad etish", "⛔ Отклонить"),
    "✅ Received": ("✅ Oldim", "✅ Получил(а)"),
    "⛔ Not received": ("⛔ Olmadim", "⛔ Не получил(а)"),
    "💸 Payment of <b>{amount}</b> from {sender}": ("💸 To'lov: <b>{amount}</b> — {sender} dan", "💸 Выплата <b>{amount}</b> от {sender}"),
    "💼 Funding of <b>{amount}</b> from {sender}": ("💼 Mablag': <b>{amount}</b> — {sender} dan", "💼 Финансирование <b>{amount}</b> от {sender}"),
    "🗓 Date paid: {date}": ("🗓 To'langan sana: {date}", "🗓 Дата выплаты: {date}"),
    "Reason: {reason}": ("Sabab: {reason}", "Причина: {reason}"),
    "Did you receive this money?": ("Bu pulni oldingizmi?", "Вы получили эти деньги?"),
    "⏰ Reminder: this is still waiting for your answer.": ("⏰ Eslatma: bu hali javobingizni kutmoqda.", "⏰ Напоминание: это всё ещё ждёт вашего ответа."),
    "✅ You confirmed: received": ("✅ Tasdiqladingiz: oldim", "✅ Вы подтвердили: получено"),
    "⛔ You answered: not received": ("⛔ Javobingiz: olmadim", "⛔ Вы ответили: не получено"),
    "↩️ The sender cancelled this": ("↩️ Yuboruvchi buni bekor qildi", "↩️ Отправитель отменил это"),
    "🚫 Voided by the Super Admin: it no longer counts": ("🚫 Super admin bekor qildi: endi hisobga olinmaydi", "🚫 Аннулировано супер-админом: больше не учитывается"),
    "✅ {name} accepted your payment of <b>{amount}</b>": ("✅ {name} <b>{amount}</b> to'lovingizni qabul qildi", "✅ {name} принял(а) вашу выплату <b>{amount}</b>"),
    "⛔ {name} declined your payment of <b>{amount}</b>: they say they did not get it": (
        "⛔ {name} <b>{amount}</b> to'lovingizni rad etdi: pulni olmaganini aytmoqda",
        "⛔ {name} отклонил(а) вашу выплату <b>{amount}</b>: говорит, что не получил(а) деньги",
    ),
    "✅ {name} confirmed receiving <b>{amount}</b>": ("✅ {name} <b>{amount}</b> olganini tasdiqladi", "✅ {name} подтвердил(а) получение <b>{amount}</b>"),
    "⛔ {name} says they did <b>not</b> receive <b>{amount}</b>": ("⛔ {name} <b>{amount}</b> <b>olmaganini</b> aytdi", "⛔ {name} сообщает, что <b>не</b> получил(а) <b>{amount}</b>"),
    "⚠️ Declined payment: {receiver} says they did not get <b>{amount}</b> from {sender}": (
        "⚠️ Rad etilgan to'lov: {receiver} {sender} dan <b>{amount}</b> olmaganini aytmoqda",
        "⚠️ Отклонённая выплата: {receiver} говорит, что не получил(а) <b>{amount}</b> от {sender}",
    ),
    "⚠️ Declined funding: {receiver} says they did not get <b>{amount}</b> from {sender}": (
        "⚠️ Rad etilgan mablag': {receiver} {sender} dan <b>{amount}</b> olmaganini aytmoqda",
        "⚠️ Отклонённое финансирование: {receiver} говорит, что не получил(а) <b>{amount}</b> от {sender}",
    ),
    "↩️ {sender} cancelled the transfer of <b>{amount}</b> ({date}). You don't need to answer it.": (
        "↩️ {sender} <b>{amount}</b> ({date}) o'tkazmasini bekor qildi. Unga javob berish shart emas.",
        "↩️ {sender} отменил(а) передачу <b>{amount}</b> ({date}). Отвечать на неё не нужно.",
    ),
    "🚫 The Super Admin voided the transfer of <b>{amount}</b> from {sender} to {receiver} ({date}). It no longer counts in any balance.": (
        "🚫 Super admin {sender} dan {receiver} ga berilgan <b>{amount}</b> ({date}) o'tkazmasini bekor qildi. U endi hech qaysi balansda hisobga olinmaydi.",
        "🚫 Супер-админ аннулировал передачу <b>{amount}</b> от {sender} к {receiver} ({date}). Она больше не учитывается ни в одном балансе.",
    ),
    # people
    "✅ Your UseMinute account <b>{email}</b> is linked.": ("✅ UseMinute hisobingiz <b>{email}</b> ulandi.", "✅ Ваш аккаунт UseMinute <b>{email}</b> привязан."),
    "Recordings found: <b>{count}</b>.": ("Topilgan yozuvlar: <b>{count}</b>.", "Найдено записей: <b>{count}</b>."),
    "No recordings were found yet. They appear here after UseMinute receives them.": (
        "Hozircha yozuvlar topilmadi. UseMinute ularni qabul qilgach, shu yerda ko'rinadi.",
        "Записей пока не найдено. Они появятся здесь, когда UseMinute их получит.",
    ),
    "👤 Your staff member is now {contact}. Ask them about your payments.": (
        "👤 Endi sizga mas'ul xodim: {contact}. To'lovlar bo'yicha unga murojaat qiling.",
        "👤 Ваш ответственный сотрудник теперь {contact}. По выплатам обращайтесь к нему.",
    ),
    "👷 {contact} is now your worker. You pay them in Payments → My workers.": (
        "👷 {contact} endi sizning ishchingiz. Unga To'lovlar → Ishchilarim bo'limida to'laysiz.",
        "👷 {contact} теперь ваш работник. Выплаты ему — в разделе Платежи → Мои работники.",
    ),
    "👤 You have no responsible staff member now. An administrator will assign one.": (
        "👤 Hozir sizga mas'ul xodim biriktirilmagan. Administrator biriktiradi.",
        "👤 Сейчас у вас нет ответственного сотрудника. Администратор назначит его.",
    ),
    "👷 {name} is no longer your worker.": ("👷 {name} endi sizning ishchingiz emas.", "👷 {name} больше не ваш работник."),
    "…and {count} more": ("…va yana {count} ta", "…и ещё {count}"),
    "👷 {count} workers were moved to you:": ("👷 Sizga {count} ta ishchi o'tkazildi:", "👷 Вам переведены работники ({count}):"),
    "Your role is now <b>{role}</b>.": ("Endi sizning rolingiz: <b>{role}</b>.", "Ваша роль теперь: <b>{role}</b>."),
    "Worker": ("Ishchi", "Работник"),
    "Staff": ("Xodim", "Сотрудник"),
    "Admin": ("Admin", "Админ"),
    "Super Admin": ("Super admin", "Супер-админ"),
    "You see your own phone and money.": ("Siz o'z telefoningiz va pulingizni ko'rasiz.", "Вы видите свой телефон и свои деньги."),
    "You can now pay your workers: Payments → My workers. An Admin gives you the money first: Payments → My funds.": (
        "Endi ishchilaringizga to'lashingiz mumkin: To'lovlar → Ishchilarim. Avval admin sizga pul beradi: To'lovlar → Mablag'larim.",
        "Теперь вы можете платить своим работникам: Платежи → Мои работники. Сначала админ выдаёт вам деньги: Платежи → Мои средства.",
    ),
    "You can now give funding to staff (Payments → Staff) and manage people and settings (Users).": (
        "Endi xodimlarga mablag' berishingiz (To'lovlar → Xodimlar) hamda odamlar va sozlamalarni boshqarishingiz mumkin (Foydalanuvchilar).",
        "Теперь вы можете выдавать деньги сотрудникам (Платежи → Сотрудники) и управлять людьми и настройками (Пользователи).",
    ),
    "⛔ Your access has been disabled. Contact an administrator.": ("⛔ Kirishingiz o'chirilgan. Administratorga murojaat qiling.", "⛔ Ваш доступ отключён. Обратитесь к администратору."),
    "✅ Your access has been restored.": ("✅ Kirishingiz tiklandi.", "✅ Ваш доступ восстановлен."),
    "ℹ️ From {date} no pay rate is set for you. Ask an administrator.": (
        "ℹ️ {date} dan boshlab siz uchun stavka belgilanmagan. Administratorga murojaat qiling.",
        "ℹ️ С {date} для вас не задана ставка. Обратитесь к администратору.",
    ),
    "ℹ️ Your pay rate from {date}: <b>{amount}</b> per hour of accepted recording.": (
        "ℹ️ {date} dan boshlab stavkangiz: qabul qilingan yozuvning har soati uchun <b>{amount}</b>.",
        "ℹ️ Ваша ставка с {date}: <b>{amount}</b> за час принятой записи.",
    ),
    # UseMinute
    "⛔ UseMinute rejected {count} of your recordings on <b>{email}</b>:": (
        "⛔ UseMinute <b>{email}</b> dagi {count} ta yozuvingizni rad etdi:",
        "⛔ UseMinute отклонил ваши записи на <b>{email}</b> ({count}):",
    ),
    "Rejected time is not paid. The app shows every recording and its reason.": (
        "Rad etilgan vaqt uchun pul to'lanmaydi. Ilovada har bir yozuv va uning sababi ko'rinadi.",
        "Отклонённое время не оплачивается. В приложении видны все записи и причины.",
    ),
    "⚠️ UseMinute sync failed:": ("⚠️ UseMinute bilan sinxronlash amalga oshmadi:", "⚠️ Синхронизация с UseMinute не удалась:"),
    # Excel files (PAYA-9)
    "📎 Staff table, {date}": ("📎 Xodimlar jadvali, {date}", "📎 Таблица сотрудников, {date}"),
    "📎 Statement of {name}, {date}": ("📎 {name} hisoboti, {date}", "📎 Выписка: {name}, {date}"),
    "Staff member": ("Xodim", "Сотрудник"),
    "Phone": ("Telefon", "Телефон"),
    "Workers": ("Ishchilar", "Работники"),
    "Phones": ("Telefonlar", "Телефоны"),
    "To be paid": ("To'lanishi kerak", "К выплате"),
    "Pending review": ("Tekshiruvda", "На проверке"),
    "Cash on hand": ("Qo'ldagi naqd pul", "Наличные на руках"),
    "Funding awaiting": ("Tasdiq kutilayotgan mablag'", "Финансирование ждёт подтверждения"),
    "Needs funding": ("Mablag' kerak", "Нужно финансирование"),
    "Total": ("Jami", "Итого"),
    "Date paid": ("To'langan sana", "Дата выплаты"),
    "Recorded": ("Kiritilgan", "Внесено"),
    "Kind": ("Turi", "Вид"),
    "With": ("Kim bilan", "С кем"),
    "Received, so'm": ("Olingan, so'm", "Получено, сум"),
    "Paid out, so'm": ("To'langan, so'm", "Выплачено, сум"),
    "Status": ("Holati", "Статус"),
    "Cash after, so'm": ("Qoldiq, so'm", "Остаток, сум"),
    "Note": ("Izoh", "Заметка"),
    "Reason": ("Sabab", "Причина"),
    "Funding received": ("Olingan mablag'", "Получено финансирование"),
    "Payment to worker": ("Ishchiga to'lov", "Выплата работнику"),
    "awaiting confirmation": ("tasdiq kutilmoqda", "ждёт подтверждения"),
    "accepted": ("qabul qilingan", "принято"),
    "declined": ("rad etilgan", "отклонено"),
    "cancelled": ("bekor qilingan", "отменено"),
    "voided": ("bekor qilingan (admin)", "аннулировано"),
    "Statement": ("Hisobot", "Выписка"),
}


def t(lang: str, text: str, **values) -> str:
    if lang != "en":
        entry = TEXTS.get(text)
        if entry is not None:
            text = entry[0 if lang == "uz" else 1]
        elif STRICT:
            raise KeyError(f"No translation for: {text}")
    return text.format(**values) if values else text
