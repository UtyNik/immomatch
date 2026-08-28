"""Локализация интерфейса: украинский, русский, английский.

Все строки размечены HTML (parse_mode по умолчанию задан в bot.py).
"""

from __future__ import annotations

from typing import Final

DEFAULT_LANG: Final[str] = "ua"

# Подписи кнопок выбора языка одинаковы для всех локалей.
LANGUAGES: Final[dict[str, str]] = {
    "ua": "🇺🇦 Українська",
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English",
}

# Приветствие и выбор языка показываются до того, как язык известен,
# поэтому они многоязычные.
WELCOME_TEXT: Final[str] = (
    "🏠 <b>Вітаємо в ImmoMatch AI! / Добро пожаловать в ImmoMatch AI!</b>"
    "\n\nЯ допоможу тобі знайти житло в Німеччині."
)

CHOOSE_LANGUAGE: Final[str] = (
    "🌐 <b>Оберіть мову / Выберите язык / Choose your language</b>"
)

TEXTS: Final[dict[str, dict[str, str]]] = {
    "ua": {
        "ask_city": (
            "🏙 У якому <b>місті</b> шукаєте житло?\n\n"
            "<i>Наприклад: Berlin, München, Hamburg</i>"
        ),
        "ask_budget": (
            "💶 Який ваш <b>максимальний бюджет</b> (тепла оренда, Warmmiete) у євро?\n\n"
            "<i>Введіть число, наприклад: <code>1200</code></i>"
        ),
        "ask_rooms": (
            "🚪 Скільки <b>мінімум кімнат</b> вам потрібно?\n\n"
            "<i>Можна дробове число, наприклад: <code>2.5</code></i>"
        ),
        "ask_sqm_min": (
            "📐 Вкажіть <b>мінімальну</b> площу квартири у м².\n\n"
            "<i>Наприклад: <code>40</code></i>"
        ),
        "ask_sqm_max": (
            "📐 Вкажіть <b>максимальну</b> площу квартири у м².\n\n"
            "<i>Число або кнопка «Без обмежень».</i>"
        ),
        "ask_household": (
            "👨‍👩‍👧 Скільки <b>людей</b> буде жити у квартирі?\n\n"
            "<i>Разом з вами та дітьми, наприклад: <code>3</code></i>"
        ),
        "ask_wbs": "📄 Чи є у вас <b>WBS</b> (Wohnberechtigungsschein)?",
        "ask_jobcenter": (
            "🏛 Чи оплачується оренда через <b>Jobcenter</b> / соціальну допомогу?"
        ),
        "ask_pets": "🐾 Чи є у вас <b>домашні тварини</b>?",
        "ask_income": (
            "💰 Який <b>чистий дохід</b> сім'ї на місяць (Nettoeinkommen) у євро?\n\n"
            "<i>Введіть число, наприклад: <code>2500</code>, "
            "або натисніть «Пропустити».</i>"
        ),
        "ask_notes": (
            "📝 Додаткові <b>побажання</b>: район, поверх, школи, балкон тощо.\n\n"
            "<i>Напишіть текстом або натисніть «Пропустити».</i>"
        ),
        "ask_first_name": (
            "👤 Як вас <b>звати</b>? Напишіть ім'я.\n\n"
            "<i>Можна українською, російською або латиницею: <code>Микита</code></i>"
        ),
        "ask_gender": (
            "👤 Вкажіть вашу <b>стать</b> (потрібно для листа і фільтрації "
            "оголошень «тільки для жінок / чоловіків»):"
        ),
        "btn_gender_male": "👨 Чоловіча",
        "btn_gender_female": "👩 Жіноча",
        "ask_household_type": "👥 Хто житиме разом з вами?",
        "btn_htype_partner_female": "👩‍❤️‍👨 З дівчиною / дружиною",
        "btn_htype_partner_male": "👨‍❤️‍👨 З хлопцем / чоловіком",
        "btn_htype_family": "👨‍👩‍👧 Сім'я з дітьми",
        "btn_htype_wg": "👥 Спільна оренда (WG / друзі)",
        "ask_last_name": (
            "👤 Ваше <b>прізвище</b>?\n\n"
            "<i>Наприклад: <code>Литвинов</code></i>"
        ),
        "ask_radius": "📍 У якому <b>радіусі</b> шукати оголошення?",
        "btn_radius_0": "📍 Тільки місто",
        "btn_radius_5": "📍 +5 км",
        "btn_radius_10": "📍 +10 км",
        "btn_radius_20": "📍 +20 км",
        "btn_radius_50": "📍 +50 км",
        "btn_yes": "✅ Так",
        "btn_no": "❌ Ні",
        "btn_skip": "⏭ Пропустити",
        "btn_skip_income": "⏩ Пропустити",
        "btn_sqm_unlimited": "❌ Без обмежень",
        "btn_new_profile": "🆕 Створити нову анкету",
        "btn_edit_profile": "✏️ Змінити поле",
        "ask_edit_field": "✏️ <b>Що змінити в анкеті?</b>",
        "btn_edit_back": "⬅️ До анкети",
        "btn_edit_name": "👤 Ім'я",
        "btn_edit_gender": "👤 Стать",
        "btn_edit_city": "🏙 Місто",
        "btn_edit_radius": "📍 Радіус",
        "btn_edit_budget": "💶 Бюджет",
        "btn_edit_rooms": "🚪 Кімнати",
        "btn_edit_sqm": "📐 Площа",
        "btn_edit_household": "👥 Мешканці",
        "btn_edit_wbs": "📄 WBS",
        "btn_edit_jobcenter": "🏛 Jobcenter",
        "btn_edit_pets": "🐾 Тварини",
        "btn_edit_income": "💰 Дохід",
        "btn_edit_notes": "📝 Побажання",
        "btn_change_lang": "🌐 Змінити мову",
        "lang_changed": "✅ <b>Мову змінено.</b> Анкета збережена без змін.",
        "no_profile": "ℹ️ Анкети ще немає. Натисніть /start, щоб заповнити її.",
        "search_started": "🔎 Шукаю свіжі оголошення в <b>{city}</b>…",
        "search_no_fresh": "✅ Нових оголошень немає — усі знайдені ви вже бачили.",
        "search_status": "🔍 <b>Шукаю житло за вашими критеріями...</b>",
        "search_no_match": (
            "😕 За вашими параметрами відповідних оголошень не знайдено.\n"
            "Спробуйте розширити бюджет або змінити вимоги.\n\n"
            "<i>Перевірено нових оголошень: {checked}.</i>"
        ),
        "btn_search": "🔍 Шукати житло",
        "btn_open_profile": "📋 Відкрити анкету",
        "btn_skip_next": "❌ Пропустити / Шукати далі",
        "btn_open_listing": "🔗 Перейти до оголошення",
        "btn_auto_search_on": (
            "🔔 Автопошук: Увімкнено (натисніть, щоб вимкнути)"
        ),
        "btn_auto_search_off": (
            "🔕 Автопошук: Вимкнено (натисніть, щоб увімкнути)"
        ),
        "auto_search_enabled_toast": "Автопошук увімкнено",
        "auto_search_disabled_toast": "Автопошук вимкнено",
        "auto_search_found": "🔔 <b>Автопошук знайшов нове житло</b>",
        "your_profile": "📋 Ваша збережена анкета:",
        "household_missing": (
            "ℹ️ В анкеті з'явилися нові питання — залишилося відповісти на них."
        ),
        "search_failed": "⚠️ Не вдалося отримати оголошення: <i>{error}</i>",
        "ai_failed": "⚠️ AI-оцінка недоступна: <i>{error}</i>\n\nПеревірте <code>OPENAI_API_KEY</code> у <code>.env</code>.",
        "card_match_yes": "✅ <b>Підходить</b>",
        "card_match_no": "❌ <b>Не підходить</b>",
        "card_letter": "✉️ <b>Супровідний лист (Anschreiben)</b>",
        "btn_open": "🔗 Перейти",
        "ai_limit_reached": (
            "🚫 На сьогодні вичерпано ліміт AI-оцінок (<b>{limit}</b>). "
            "Спробуйте завтра."
        ),
        "err_city": "⚠️ Введіть назву міста текстом (від {min} до {max} символів).",
        "err_number": (
            "⚠️ Це не схоже на число. Введіть лише цифри, "
            "наприклад: <code>1200</code>"
        ),
        "err_budget_range": "⚠️ Бюджет має бути від <b>{min}</b> до <b>{max}</b> €.",
        "err_rooms_range": "⚠️ Кількість кімнат має бути від <b>{min}</b> до <b>{max}</b>.",
        "err_sqm_range": "⚠️ Площа має бути від <b>{min}</b> до <b>{max}</b> м².",
        "err_sqm_max_low": (
            "⚠️ Максимум не може бути меншим за мінімум (<b>{min}</b> м²)."
        ),
        "err_household_range": (
            "⚠️ Вкажіть кількість людей цілим числом від <b>{min}</b> до <b>{max}</b>."
        ),
        "err_income_range": "⚠️ Дохід має бути від <b>{min}</b> до <b>{max}</b> €.",
        "err_notes_long": "⚠️ Занадто довго. Максимум <b>{max}</b> символів.",
        "err_name": (
            "⚠️ Введіть ім'я літерами (від {min} до {max} символів), без цифр."
        ),
        "err_text_only": "⚠️ Надішліть відповідь текстом.",
        "use_buttons": "⚠️ Скористайтеся кнопками під повідомленням.",
        "saved": "✅ <b>Готово! Анкету збережено.</b>",
        "save_failed": "⚠️ Не вдалося зберегти анкету. Спробуйте ще раз трохи пізніше.",
        "card_title": "🏠 <b>Ваша анкета</b>",
        "f_name": "Ім'я",
        "f_applicant": "Заявник",
        "gender_male": "Чоловіча",
        "gender_female": "Жіноча",
        "htype_single": "один / одна",
        "htype_partner_female": "з дівчиною / дружиною",
        "htype_partner_male": "з хлопцем / чоловіком",
        "htype_family": "сім'я з дітьми",
        "htype_wg": "спільна оренда",
        "household_with_type": "{count} ({kind})",
        "f_city": "Місто",
        "city_radius": "{city} (+{km} км)",
        "f_budget": "Бюджет (Warm)",
        "f_rooms": "Мінімум кімнат",
        "f_sqm": "Площа",
        "sqm_from": "від {min} м²",
        "sqm_range": "від {min} м² до {max} м²",
        "sqm_unlimited": "без обмежень",
        "f_household": "Мешканці",
        "f_wbs": "WBS",
        "f_jobcenter": "Jobcenter",
        "f_pets": "Тварини",
        "f_income": "Дохід (Netto)",
        "f_income_skipped": "Не вказано (пропущено)",
        "f_notes": "Побажання",
        "yes": "Так",
        "no": "Ні",
        "empty": "—",
        "session_lost": "⏳ Сесія втрачена. Натисніть /start, щоб почати спочатку.",
    },
    "ru": {
        "ask_city": (
            "🏙 В каком <b>городе</b> ищете жильё?\n\n"
            "<i>Например: Berlin, München, Hamburg</i>"
        ),
        "ask_budget": (
            "💶 Какой у вас <b>максимальный бюджет</b> (тёплая аренда, Warmmiete) в евро?\n\n"
            "<i>Введите число, например: <code>1200</code></i>"
        ),
        "ask_rooms": (
            "🚪 Сколько <b>минимум комнат</b> вам нужно?\n\n"
            "<i>Можно дробное число, например: <code>2.5</code></i>"
        ),
        "ask_sqm_min": (
            "📐 Укажите <b>минимальную</b> площадь квартиры в м².\n\n"
            "<i>Например: <code>40</code></i>"
        ),
        "ask_sqm_max": (
            "📐 Укажите <b>максимальную</b> площадь квартиры в м².\n\n"
            "<i>Число или кнопка «Без ограничений».</i>"
        ),
        "ask_household": (
            "👨‍👩‍👧 Сколько <b>человек</b> будет жить в квартире?\n\n"
            "<i>Вместе с вами и детьми, например: <code>3</code></i>"
        ),
        "ask_wbs": "📄 Есть ли у вас <b>WBS</b> (Wohnberechtigungsschein)?",
        "ask_jobcenter": (
            "🏛 Оплачивается ли аренда через <b>Jobcenter</b> / социальную помощь?"
        ),
        "ask_pets": "🐾 Есть ли у вас <b>домашние животные</b>?",
        "ask_income": (
            "💰 Какой <b>чистый доход</b> семьи в месяц (Nettoeinkommen) в евро?\n\n"
            "<i>Введите число, например: <code>2500</code>, "
            "или нажмите «Пропустить».</i>"
        ),
        "ask_notes": (
            "📝 Дополнительные <b>пожелания</b>: район, этаж, школы, балкон и т.д.\n\n"
            "<i>Напишите текстом или нажмите «Пропустить».</i>"
        ),
        "ask_first_name": (
            "👤 Как вас <b>зовут</b>? Напишите имя.\n\n"
            "<i>Можно по-русски, по-украински или латиницей: <code>Никита</code></i>"
        ),
        "ask_gender": (
            "👤 Укажите ваш <b>пол</b> (нужно для корректного письма и фильтрации "
            "объявлений «только для женщин / мужчин»):"
        ),
        "btn_gender_male": "👨 Мужской",
        "btn_gender_female": "👩 Женский",
        "ask_household_type": "👥 Кто будет жить вместе с вами?",
        "btn_htype_partner_female": "👩‍❤️‍👨 С девушкой / женой",
        "btn_htype_partner_male": "👨‍❤️‍👨 С парнем / мужем",
        "btn_htype_family": "👨‍👩‍👧 Семья с детьми",
        "btn_htype_wg": "👥 Совместная аренда (WG / друзья)",
        "ask_last_name": (
            "👤 Ваша <b>фамилия</b>?\n\n"
            "<i>Например: <code>Литвинов</code></i>"
        ),
        "ask_radius": "📍 В каком <b>радиусе</b> искать объявления?",
        "btn_radius_0": "📍 Только город",
        "btn_radius_5": "📍 +5 км",
        "btn_radius_10": "📍 +10 км",
        "btn_radius_20": "📍 +20 км",
        "btn_radius_50": "📍 +50 км",
        "btn_yes": "✅ Да",
        "btn_no": "❌ Нет",
        "btn_skip": "⏭ Пропустить",
        "btn_skip_income": "⏩ Пропустить",
        "btn_sqm_unlimited": "❌ Без ограничений",
        "btn_new_profile": "🆕 Создать новую анкету",
        "btn_edit_profile": "✏️ Изменить поле",
        "ask_edit_field": "✏️ <b>Что изменить в анкете?</b>",
        "btn_edit_back": "⬅️ К анкете",
        "btn_edit_name": "👤 Имя",
        "btn_edit_gender": "👤 Пол",
        "btn_edit_city": "🏙 Город",
        "btn_edit_radius": "📍 Радиус",
        "btn_edit_budget": "💶 Бюджет",
        "btn_edit_rooms": "🚪 Комнаты",
        "btn_edit_sqm": "📐 Площадь",
        "btn_edit_household": "👥 Жильцы",
        "btn_edit_wbs": "📄 WBS",
        "btn_edit_jobcenter": "🏛 Jobcenter",
        "btn_edit_pets": "🐾 Животные",
        "btn_edit_income": "💰 Доход",
        "btn_edit_notes": "📝 Пожелания",
        "btn_change_lang": "🌐 Сменить язык",
        "lang_changed": "✅ <b>Язык изменён.</b> Анкета сохранена без изменений.",
        "no_profile": "ℹ️ Анкеты пока нет. Нажмите /start, чтобы заполнить её.",
        "search_started": "🔎 Ищу свежие объявления в <b>{city}</b>…",
        "search_no_fresh": "✅ Новых объявлений нет — все найденные вы уже видели.",
        "search_status": "🔍 <b>Ищу жильё по вашим критериям...</b>",
        "search_no_match": (
            "😕 По вашим критериям подходящих объявлений не найдено.\n"
            "Попробуйте расширить бюджет или изменить требования.\n\n"
            "<i>Проверено новых объявлений: {checked}.</i>"
        ),
        "btn_search": "🔍 Искать жильё",
        "btn_open_profile": "📋 Открыть анкету",
        "btn_skip_next": "❌ Пропустить / Искать дальше",
        "btn_open_listing": "🔗 Перейти к объявлению",
        "btn_auto_search_on": (
            "🔔 Автопоиск: Включён (нажмите, чтобы выключить)"
        ),
        "btn_auto_search_off": (
            "🔕 Автопоиск: Выключен (нажмите, чтобы включить)"
        ),
        "auto_search_enabled_toast": "Автопоиск включён",
        "auto_search_disabled_toast": "Автопоиск выключен",
        "auto_search_found": "🔔 <b>Автопоиск нашёл новое жильё</b>",
        "your_profile": "📋 Ваша сохранённая анкета:",
        "household_missing": (
            "ℹ️ В анкете появились новые вопросы — осталось ответить на них."
        ),
        "search_failed": "⚠️ Не удалось получить объявления: <i>{error}</i>",
        "ai_failed": "⚠️ AI-оценка недоступна: <i>{error}</i>\n\nПроверьте <code>OPENAI_API_KEY</code> в <code>.env</code>.",
        "card_match_yes": "✅ <b>Подходит</b>",
        "card_match_no": "❌ <b>Не подходит</b>",
        "card_letter": "✉️ <b>Сопроводительное письмо (Anschreiben)</b>",
        "btn_open": "🔗 Перейти",
        "ai_limit_reached": (
            "🚫 На сегодня лимит AI-оценок исчерпан (<b>{limit}</b>). "
            "Попробуйте завтра."
        ),
        "err_city": "⚠️ Введите название города текстом (от {min} до {max} символов).",
        "err_number": (
            "⚠️ Это не похоже на число. Введите только цифры, "
            "например: <code>1200</code>"
        ),
        "err_budget_range": "⚠️ Бюджет должен быть от <b>{min}</b> до <b>{max}</b> €.",
        "err_rooms_range": "⚠️ Количество комнат должно быть от <b>{min}</b> до <b>{max}</b>.",
        "err_sqm_range": "⚠️ Площадь должна быть от <b>{min}</b> до <b>{max}</b> м².",
        "err_sqm_max_low": (
            "⚠️ Максимум не может быть меньше минимума (<b>{min}</b> м²)."
        ),
        "err_household_range": (
            "⚠️ Укажите количество человек целым числом от <b>{min}</b> до <b>{max}</b>."
        ),
        "err_income_range": "⚠️ Доход должен быть от <b>{min}</b> до <b>{max}</b> €.",
        "err_notes_long": "⚠️ Слишком длинно. Максимум <b>{max}</b> символов.",
        "err_name": (
            "⚠️ Введите имя буквами (от {min} до {max} символов), без цифр."
        ),
        "err_text_only": "⚠️ Отправьте ответ текстом.",
        "use_buttons": "⚠️ Воспользуйтесь кнопками под сообщением.",
        "saved": "✅ <b>Готово! Анкета сохранена.</b>",
        "save_failed": "⚠️ Не удалось сохранить анкету. Попробуйте ещё раз чуть позже.",
        "card_title": "🏠 <b>Ваша анкета</b>",
        "f_name": "Имя",
        "f_applicant": "Заявитель",
        "gender_male": "Мужской",
        "gender_female": "Женский",
        "htype_single": "один / одна",
        "htype_partner_female": "с девушкой / женой",
        "htype_partner_male": "с парнем / мужем",
        "htype_family": "семья с детьми",
        "htype_wg": "совместная аренда",
        "household_with_type": "{count} ({kind})",
        "f_city": "Город",
        "city_radius": "{city} (+{km} км)",
        "f_budget": "Бюджет (Warm)",
        "f_rooms": "Минимум комнат",
        "f_sqm": "Площадь",
        "sqm_from": "от {min} м²",
        "sqm_range": "от {min} м² до {max} м²",
        "sqm_unlimited": "без ограничений",
        "f_household": "Жильцы",
        "f_wbs": "WBS",
        "f_jobcenter": "Jobcenter",
        "f_pets": "Животные",
        "f_income": "Доход (Netto)",
        "f_income_skipped": "Не указан (пропущено)",
        "f_notes": "Пожелания",
        "yes": "Да",
        "no": "Нет",
        "empty": "—",
        "session_lost": "⏳ Сессия потеряна. Нажмите /start, чтобы начать заново.",
    },
    "en": {
        "ask_city": (
            "🏙 Which <b>city</b> are you looking in?\n\n"
            "<i>For example: Berlin, München, Hamburg</i>"
        ),
        "ask_budget": (
            "💶 What is your <b>maximum budget</b> (warm rent, Warmmiete) in euros?\n\n"
            "<i>Enter a number, for example: <code>1200</code></i>"
        ),
        "ask_rooms": (
            "🚪 What is the <b>minimum number of rooms</b> you need?\n\n"
            "<i>Decimals are fine, for example: <code>2.5</code></i>"
        ),
        "ask_sqm_min": (
            "📐 What is the <b>minimum</b> floor area in m²?\n\n"
            "<i>For example: <code>40</code></i>"
        ),
        "ask_sqm_max": (
            "📐 What is the <b>maximum</b> floor area in m²?\n\n"
            "<i>Enter a number or tap “No limit”.</i>"
        ),
        "ask_household": (
            "👨‍👩‍👧 How many <b>people</b> will live in the flat?\n\n"
            "<i>Including you and any children, for example: <code>3</code></i>"
        ),
        "ask_wbs": "📄 Do you have a <b>WBS</b> (Wohnberechtigungsschein)?",
        "ask_jobcenter": (
            "🏛 Is the rent paid through <b>Jobcenter</b> / social benefits?"
        ),
        "ask_pets": "🐾 Do you have any <b>pets</b>?",
        "ask_income": (
            "💰 What is your household <b>net income</b> per month "
            "(Nettoeinkommen) in euros?\n\n"
            "<i>Enter a number, for example: <code>2500</code>, "
            "or tap “Skip”.</i>"
        ),
        "ask_notes": (
            "📝 Any extra <b>preferences</b>: district, floor, schools, balcony, etc.\n\n"
            "<i>Send a message or tap “Skip”.</i>"
        ),
        "ask_first_name": (
            "👤 What is your <b>first name</b>?\n\n"
            "<i>Ukrainian, Russian or Latin is fine: <code>Mykyta</code></i>"
        ),
        "ask_gender": (
            "👤 What is your <b>gender</b>? It is needed for the cover letter "
            "and to skip listings that are women-only or men-only."
        ),
        "btn_gender_male": "👨 Male",
        "btn_gender_female": "👩 Female",
        "ask_household_type": "👥 Who will live with you?",
        "btn_htype_partner_female": "👩‍❤️‍👨 With a girlfriend / wife",
        "btn_htype_partner_male": "👨‍❤️‍👨 With a boyfriend / husband",
        "btn_htype_family": "👨‍👩‍👧 Family with children",
        "btn_htype_wg": "👥 Shared rent (friends / WG)",
        "ask_last_name": (
            "👤 What is your <b>last name</b>?\n\n"
            "<i>For example: <code>Lytvynov</code></i>"
        ),
        "ask_radius": "📍 Within what <b>radius</b> should we search?",
        "btn_radius_0": "📍 City only",
        "btn_radius_5": "📍 +5 km",
        "btn_radius_10": "📍 +10 km",
        "btn_radius_20": "📍 +20 km",
        "btn_radius_50": "📍 +50 km",
        "btn_yes": "✅ Yes",
        "btn_no": "❌ No",
        "btn_skip": "⏭ Skip",
        "btn_skip_income": "⏩ Skip",
        "btn_sqm_unlimited": "❌ No limit",
        "btn_new_profile": "🆕 Create new profile",
        "btn_edit_profile": "✏️ Edit a field",
        "ask_edit_field": "✏️ <b>Which field do you want to change?</b>",
        "btn_edit_back": "⬅️ Back to profile",
        "btn_edit_name": "👤 Name",
        "btn_edit_gender": "👤 Gender",
        "btn_edit_city": "🏙 City",
        "btn_edit_radius": "📍 Radius",
        "btn_edit_budget": "💶 Budget",
        "btn_edit_rooms": "🚪 Rooms",
        "btn_edit_sqm": "📐 Area",
        "btn_edit_household": "👥 Occupants",
        "btn_edit_wbs": "📄 WBS",
        "btn_edit_jobcenter": "🏛 Jobcenter",
        "btn_edit_pets": "🐾 Pets",
        "btn_edit_income": "💰 Income",
        "btn_edit_notes": "📝 Preferences",
        "btn_change_lang": "🌐 Change language",
        "lang_changed": "✅ <b>Language changed.</b> Your profile is unchanged.",
        "no_profile": "ℹ️ You have no profile yet. Send /start to fill it in.",
        "search_started": "🔎 Looking for fresh listings in <b>{city}</b>…",
        "search_no_fresh": "✅ No new listings — you have seen all of them already.",
        "search_status": "🔍 <b>Searching for a home that fits your criteria...</b>",
        "search_no_match": (
            "😕 No listings match your criteria.\n"
            "Try raising your budget or relaxing the requirements.\n\n"
            "<i>New listings checked: {checked}.</i>"
        ),
        "btn_search": "🔍 Search housing",
        "btn_open_profile": "📋 Open profile",
        "btn_skip_next": "❌ Skip / keep searching",
        "btn_open_listing": "🔗 Open listing",
        "btn_auto_search_on": "🔔 Auto-search: On (tap to turn off)",
        "btn_auto_search_off": "🔕 Auto-search: Off (tap to turn on)",
        "auto_search_enabled_toast": "Auto-search enabled",
        "auto_search_disabled_toast": "Auto-search disabled",
        "auto_search_found": "🔔 <b>Auto-search found a new listing</b>",
        "your_profile": "📋 Your saved profile:",
        "household_missing": (
            "ℹ️ Your profile has new questions left to answer."
        ),
        "search_failed": "⚠️ Could not fetch listings: <i>{error}</i>",
        "ai_failed": "⚠️ AI review unavailable: <i>{error}</i>\n\nCheck <code>OPENAI_API_KEY</code> in <code>.env</code>.",
        "card_match_yes": "✅ <b>Good match</b>",
        "card_match_no": "❌ <b>Not a match</b>",
        "card_letter": "✉️ <b>Cover letter (Anschreiben)</b>",
        "btn_open": "🔗 Open",
        "ai_limit_reached": (
            "🚫 Daily limit of AI reviews reached (<b>{limit}</b>). "
            "Please try again tomorrow."
        ),
        "err_city": "⚠️ Please send the city name as text ({min}–{max} characters).",
        "err_number": (
            "⚠️ That does not look like a number. Digits only, "
            "for example: <code>1200</code>"
        ),
        "err_budget_range": "⚠️ Budget must be between <b>{min}</b> and <b>{max}</b> €.",
        "err_rooms_range": "⚠️ Rooms must be between <b>{min}</b> and <b>{max}</b>.",
        "err_sqm_range": "⚠️ Floor area must be between <b>{min}</b> and <b>{max}</b> m².",
        "err_sqm_max_low": (
            "⚠️ The maximum cannot be below the minimum (<b>{min}</b> m²)."
        ),
        "err_household_range": (
            "⚠️ Enter the number of people as a whole number "
            "between <b>{min}</b> and <b>{max}</b>."
        ),
        "err_income_range": "⚠️ Income must be between <b>{min}</b> and <b>{max}</b> €.",
        "err_notes_long": "⚠️ Too long. Maximum <b>{max}</b> characters.",
        "err_name": (
            "⚠️ Please enter a name in letters ({min}–{max} characters), no digits."
        ),
        "err_text_only": "⚠️ Please reply with text.",
        "use_buttons": "⚠️ Please use the buttons below the message.",
        "saved": "✅ <b>All set! Your profile has been saved.</b>",
        "save_failed": "⚠️ Could not save your profile. Please try again a bit later.",
        "card_title": "🏠 <b>Your profile</b>",
        "f_name": "Name",
        "f_applicant": "Applicant",
        "gender_male": "Male",
        "gender_female": "Female",
        "htype_single": "single",
        "htype_partner_female": "with girlfriend / wife",
        "htype_partner_male": "with boyfriend / husband",
        "htype_family": "family with children",
        "htype_wg": "shared rent with friends",
        "household_with_type": "{count} ({kind})",
        "f_city": "City",
        "city_radius": "{city} (+{km} km)",
        "f_budget": "Budget (warm)",
        "f_rooms": "Min. rooms",
        "f_sqm": "Floor area",
        "sqm_from": "from {min} m²",
        "sqm_range": "from {min} m² to {max} m²",
        "sqm_unlimited": "no limit",
        "f_household": "Occupants",
        "f_wbs": "WBS",
        "f_jobcenter": "Jobcenter",
        "f_pets": "Pets",
        "f_income": "Income (net)",
        "f_income_skipped": "Not specified (skipped)",
        "f_notes": "Preferences",
        "yes": "Yes",
        "no": "No",
        "empty": "—",
        "session_lost": "⏳ Session expired. Send /start to begin again.",
    },
}


def t(lang: str | None, key: str, **kwargs: object) -> str:
    """Возвращает строку на нужном языке, подставляя значения через format().

    Неизвестный язык молча заменяется на украинский: пользователь получит
    понятный текст вместо KeyError посреди диалога.
    """
    locale = TEXTS.get(lang or DEFAULT_LANG, TEXTS[DEFAULT_LANG])
    template = locale.get(key) or TEXTS[DEFAULT_LANG][key]
    return template.format(**kwargs) if kwargs else template
