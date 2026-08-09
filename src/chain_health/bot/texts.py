from html import escape as _esc

from chain_health.bot.limits import TELEGRAM_INPUT_PLACEHOLDER_MAX_LEN
from chain_health.db.models import Chain, Group
from chain_health.domain.constants import MAX_NAME_LENGTH
from chain_health.domain.values import ChainStatus, StatusView

# Every function below is rendered with parse_mode=HTML (di.py). User-supplied
# group/chain names must be escaped with _esc() at every interpolation point,
# not just where a literal <b> tag is nearby — an unescaped "&" or "<" in a
# name breaks Telegram's HTML entity parser and the send raises, regardless
# of whether the surrounding template happens to use any tags itself.

BTN_STATUS = "📊 Статус"
BTN_MENU = "☰ Меню"
BTN_MY_GROUPS = "🚲 Мои группы"
BTN_RECENT_RIDES = "📜 Последние поездки"
BTN_ADD_GROUP = "➕ Новая группа"
BTN_ADD_CHAIN = "➕ Добавить цепь"
BTN_SET_CURRENT = "✅ Сделать текущей"
BTN_GROUP_LIMIT = "📏 Лимит цикла по умолчанию"
BTN_CHAIN_LIMIT = "📏 Изменить лимит цикла"
BTN_CHAIN_RESOURCE = "🎯 Задать ресурс"
BTN_RETIRE = "🗑 Списать"
BTN_BACK = "⬅️ Назад"
ROTATE_BUTTON = "🔄 Сменить цепь"

WELCOME_BACK = "С возвращением! Отправь число — запишу пробег."

ONBOARDING_ASK_GROUP_NAME = (
    "Привет! Я буду считать пробег твоих цепей и подсказывать, когда пора менять цепь.\n\n"
    "Для начала создадим группу цепей — это комплект на одну трансмиссию "
    "(например: «Шоссе» или «МТБ»). Как назовём первую группу?"
)
ONBOARDING_ASK_CHAIN_NAME = "Отлично! Теперь добавь первую цепь в эту группу — как её назвать?"

ALLOW_USAGE = "Использование: /allow <telegram_id>"

NO_ACTIVE_CHAIN = (
    "У тебя пока нет активной цепи. Добавь группу и цепь через ☰ Меню, прежде чем вносить пробег."
)

MENU_ROOT = "Меню:"
ASK_NEW_GROUP_NAME = "Как назвать новую группу?"
ASK_NEW_CHAIN_NAME = "Как назвать новую цепь?"
ASK_GROUP_LIMIT = "Новый лимит цикла по умолчанию для группы (км):"
ASK_CHAIN_LIMIT = "Новый лимит цикла для этой цепи (км):"
ASK_CHAIN_RESOURCE = (
    "Ресурс цепи в км (когда пора проверять износ калибром). Отправь «-», чтобы убрать."
)
ASK_RIDE_AMOUNT = "Новое значение пробега для этой поездки (км):"
ASK_NUMBER_RETRY = "Нужно положительное число. Попробуй ещё раз."
ASK_DISTANCE_RETRY = "Нужно число километров больше 0 и не больше 1000. Попробуй ещё раз."
ASK_NAME_TOO_LONG_RETRY = (
    f"Слишком длинное имя (максимум {MAX_NAME_LENGTH} символов). Попробуй короче."
)
NO_ROTATION_CANDIDATES = "В группе больше нет других цепей. Добавь ещё одну через ➕ Добавить цепь."
RIDES_EMPTY = "Пока нет ни одной поездки."
RIDES_LIST_HEADER = "Последние поездки (нажми на поездку, чтобы изменить, или 🗑, чтобы удалить):"
ROTATION_PROMPT = "Выбери новую цепь (⭐ — рекомендуем, наименьший общий пробег):"

# Error-handler texts (see bot/errors.py). Deliberately generic — never leak
# internals, and never distinguish "not found" from "not yours".
ERR_UNEXPECTED = "Что-то пошло не так. Попробуй ещё раз или открой ☰ Меню."
ERR_NOT_FOUND = "Не нашёл — похоже, кнопка устарела. Открой ☰ Меню заново."
ERR_INVALID_OPERATION = "Так больше нельзя — состояние изменилось. Открой ☰ Меню заново."
ERR_STALE_MESSAGE = "Это сообщение слишком старое. Открой ☰ Меню заново."
ERR_STALE_BUTTON = "Кнопка устарела. Открой ☰ Меню заново."


def onboarding_done(group_name: str, chain_name: str) -> str:
    return (
        f"Готово! Группа «{_esc(group_name)}» с цепью «{_esc(chain_name)}» созданы.\n\n"
        "Теперь просто отправляй число километров после каждой поездки."
    )


def user_allowed(user_id: int) -> str:
    return f"Пользователь {user_id} допущен к боту."


def invite_created(link: str) -> str:
    return f"Одноразовая ссылка (действует 48 часов):\n{link}"


def fmt_km(value: float) -> str:
    return f"{value:g}"


def placeholder_text(view: StatusView) -> str:
    # Not sent as message text (it's a ReplyKeyboardMarkup.input_field_placeholder
    # UI hint, never HTML-parsed) — no escaping needed here.
    if view.group is None or view.active is None:
        return "Км → нет активной цепи"
    active = view.active
    text = (
        f"Км → {view.group.name} · {active.chain.name} "
        f"({fmt_km(active.cycle_km)}/{fmt_km(active.cycle_limit_km)})"
    )
    return text[:TELEGRAM_INPUT_PLACEHOLDER_MAX_LEN]


def _chain_line(status: ChainStatus) -> str:
    marker = "🟢" if status.chain.is_active else "⚪"
    cycle = f"{fmt_km(status.cycle_km)}/{fmt_km(status.cycle_limit_km)}"
    line = f"{marker} {_esc(status.chain.name)}: {cycle} км (всего {fmt_km(status.total_km)} км)"
    if status.resource_warning:
        line += " ⚠️ проверь износ калибром"
    return line


def pinned_status_text(view: StatusView) -> str:
    if view.group is None:
        return "Нет активной группы. Добавь группу через ☰ Меню."
    lines = [f"<b>{_esc(view.group.name)}</b>"]
    lines.extend(_chain_line(status) for status in view.all_chains)
    return "\n".join(lines)


def ride_recorded_text(group_name: str, status: ChainStatus, distance_km: float) -> str:
    lines = [f"+{fmt_km(distance_km)} км → {_esc(group_name)} · {_esc(status.chain.name)}"]
    lines.append(f"Цикл: {fmt_km(status.cycle_km)}/{fmt_km(status.cycle_limit_km)} км")
    if status.over_limit:
        lines.append("⚠️ Лимит цикла превышен — пора менять цепь.")
    if status.resource_warning:
        lines.append("⚠️ Цепь приближается к своему ресурсу — проверь износ калибром.")
    return "\n".join(lines)


def groups_list_text(groups_count: int) -> str:
    if groups_count == 0:
        return "У тебя пока нет групп. Добавь первую:"
    return "Твои группы (✅ — текущая):"


def group_detail_text(group: Group, chain_statuses: list[ChainStatus]) -> str:
    lines = [
        f"<b>{_esc(group.name)}</b>",
        f"Лимит цикла по умолчанию: {fmt_km(group.default_cycle_limit_km)} км",
    ]
    if not chain_statuses:
        lines.append("Пока нет цепей.")
    else:
        lines.extend(_chain_line(status) for status in chain_statuses)
    return "\n".join(lines)


def chain_detail_text(status: ChainStatus) -> str:
    chain = status.chain
    lines = [
        f"<b>{_esc(chain.name)}</b>{' (списана)' if chain.is_retired else ''}",
        f"Цикл: {fmt_km(status.cycle_km)}/{fmt_km(status.cycle_limit_km)} км",
        f"Общий пробег: {fmt_km(status.total_km)} км",
    ]
    if chain.resource_km is not None:
        lines.append(f"Ресурс: {fmt_km(chain.resource_km)} км")
    return "\n".join(lines)


def chain_retired_text(chain: Chain) -> str:
    return f"Цепь «{_esc(chain.name)}» списана и убрана из ротации."


def rotation_confirmed_text(chain_name: str) -> str:
    return f"🔄 Активная цепь: «{_esc(chain_name)}». Цикл обнулён."


def ride_updated_text(distance_km: float) -> str:
    return f"Поездка обновлена: {fmt_km(distance_km)} км."


def ride_deleted_text() -> str:
    return "Поездка удалена."


def reminder_text(status: ChainStatus) -> str:
    cycle = f"{fmt_km(status.cycle_km)}/{fmt_km(status.cycle_limit_km)}"
    lines = [f"⏰ «{_esc(status.chain.name)}»: {cycle} км."]
    if status.over_limit:
        lines.append("Лимит цикла превышен — самое время сменить цепь (☰ Меню → 🔄 Сменить цепь).")
    if status.resource_warning:
        lines.append("Плюс приближается ресурс цепи — проверь износ калибром.")
    return "\n".join(lines)
