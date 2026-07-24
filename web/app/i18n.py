"""Minimal i18n. The Spanish source string is the translation key, so any
string not yet translated falls back to Spanish (readable) instead of showing a
raw key. English strings live in ``_EN``; add a Spanish→English entry to
translate one. ``t(text)`` in templates does the lookup for the active language.
"""

from __future__ import annotations

LANGS = ("es", "en")
DEFAULT_LANG = "es"

MESES = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
           "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
}
MESES_ABR = {
    "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul",
           "ago", "sep", "oct", "nov", "dic"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}

# Spanish source → English. Keep phrases whole and single-line.
_EN = {
    # nav + user menu
    "Inicio": "Home",
    "Presupuesto": "Budget",
    "Reglas": "Rules",
    "Categorías": "Categories",
    "Conectar": "Connect",
    "Notificaciones": "Notifications",
    "Salir": "Sign out",
    "Cambiar entre tema claro y oscuro": "Toggle light/dark theme",
    "Cambiar idioma": "Change language",

    # auth — login
    "Iniciar sesión": "Sign in",
    "Entra para ver tu presupuesto del mes.": "Sign in to see this month's budget.",
    "Correo": "Email",
    "Contraseña": "Password",
    "Entrar": "Sign in",
    "¿Olvidaste tu contraseña?": "Forgot your password?",
    "Restablécela": "Reset it",
    "¿No tienes cuenta?": "No account?",
    "Crear una": "Create one",
    # auth — register
    "Crear cuenta": "Create account",
    "Registra tu correo para empezar a seguir tus consumos.":
        "Register your email to start tracking your spending.",
    "Mínimo 8 caracteres.": "At least 8 characters.",
    "¿Ya tienes cuenta?": "Already have an account?",
    # auth — forgot / reset
    "Restablecer contraseña": "Reset password",
    "Escribe tu correo y te enviaremos un enlace para crear una contraseña nueva.":
        "Enter your email and we'll send you a link to set a new password.",
    "Enviar enlace": "Send link",
    "Volver a iniciar sesión": "Back to sign in",
    "Nueva contraseña": "New password",
    "Elige una contraseña para tu cuenta.": "Choose a password for your account.",
    "Guardar contraseña": "Save password",

    # dashboard
    "Conecta tu correo para empezar.": "Connect your email to get started.",
    "Reenvía las notificaciones de tus tarjetas y tus consumos aparecerán aquí automáticamente.":
        "Forward your card notifications and your transactions will show up here automatically.",
    "Conectar correo": "Connect email",
    "tarjeta(s) sin etiquetar": "card(s) to label",
    "correo(s) sin procesar": "email(s) not processed",
    "Presupuesto excedido": "Budget exceeded",
    "Disponible": "Available",
    "gastado": "spent",
    "de": "of",
    "Sin presupuesto definido para este mes.": "No budget set for this month.",
    "Definir presupuesto": "Set budget",
    "sin presupuesto": "no budget",
    "Movimientos recientes": "Recent activity",
    "Sin transacciones todavía. Los consumos que lleguen por correo aparecerán aquí.":
        "No transactions yet. Charges that arrive by email will appear here.",
    "Reenvía las notificaciones de tus tarjetas a":
        "Forward your card notifications to",

    # budgets
    "Monto mensual por categoría, en RD$.": "Monthly amount per category, in RD$.",
    "Guardar cambios": "Save changes",

    # rules
    "Reglas de categorización": "Categorization rules",
    "La primera coincidencia (por substring del comercio, sin distinguir mayúsculas) gana. Sin coincidencia → «Otros / sin categoría».":
        "The first match (by merchant substring, case-insensitive) wins. No match → «Otros / sin categoría».",
    "Sin reglas todavía. Agrega una abajo para categorizar tus consumos automáticamente.":
        "No rules yet. Add one below to categorize your spending automatically.",
    "Eliminar": "Delete",
    "Nueva regla": "New rule",
    "Substring del comercio": "Merchant substring",
    "Categoría": "Category",
    "Agregar regla": "Add rule",
    "p. ej. UBER EATS": "e.g. UBER EATS",

    # categories
    "Tus categorías": "Your categories",
    "Crea las que se ajusten a tu presupuesto y quita las que no uses. Las de sistema no se pueden eliminar.":
        "Create the ones that fit your budget and remove those you don't use. System ones can't be deleted.",
    "Sistema": "System",
    "transacción": "transaction",
    "transacciones": "transactions",
    "Quitar": "Remove",
    "¿Quitar": "Remove",
    "Sus transacciones pasarán a «Otros / sin categoría» y se borrarán sus reglas y presupuestos.":
        "Its transactions will move to «Otros / sin categoría» and its rules and budgets will be deleted.",
    "Nueva categoría": "New category",
    "Nombre de la categoría": "Category name",
    "Agregar categoría": "Add category",
    "p. ej. Supermercado": "e.g. Groceries",

    # notifications
    "Recibe un aviso al instante cuando uses una tarjeta, con lo que te queda del presupuesto de esa categoría.":
        "Get an instant alert when you use a card, with how much is left in that category's budget.",
    "Alertas por chat, gratis y al instante.": "Chat alerts, free and instant.",
    "Conectado": "Connected",
    "Las alertas por Telegram no están disponibles todavía en este servidor.":
        "Telegram alerts aren't available on this server yet.",
    "Alertas": "Alerts",
    "activadas": "on",
    "pausadas": "paused",
    "Enviar mensaje de prueba": "Send test message",
    "Desconectar": "Disconnect",
    "Abre el chat con nuestro bot y presiona": "Open the chat with our bot and press",
    "Iniciar": "Start",
    "Quedarás conectado automáticamente.": "You'll be connected automatically.",
    "Conectar Telegram": "Connect Telegram",
    "El enlace es personal y vence en 15 minutos. Si expira, recarga esta página.":
        "The link is personal and expires in 15 minutes. If it expires, reload this page.",

    # setup / onboarding
    "Conecta tu correo": "Connect your email",
    "Haz estos pasos desde una": "Do these steps from a",
    "computadora": "computer",
    "Tu dirección personal de ingesta (la usarás en los pasos de abajo):":
        "Your personal intake address (you'll use it in the steps below):",
    "Copiar": "Copy",
    "Copiado": "Copied",
    "Autoriza el reenvío": "Authorize forwarding",
    "Filtra solo tus tarjetas": "Filter only your cards",
    "Descargar filtro": "Download filter",
    "Prefiero crearlo a mano": "I'd rather create it by hand",
    "Trae tus consumos de este mes": "Bring in this month's transactions",
    "Reenviar como archivo adjunto": "Forward as attachment",
    "Tus tarjetas": "Your cards",
    "Nueva": "New",
    "Guardar": "Save",
    "Agregar una tarjeta manualmente": "Add a card manually",
    "Banco": "Bank",
    "Últimos 4": "Last 4",
    "Últimos 4 dígitos": "Last 4 digits",
    "Etiqueta (opcional)": "Label (optional)",
    "Etiqueta": "Label",
    "Agregar tarjeta": "Add card",
    "Confirmar reenvío en Gmail": "Confirm forwarding in Gmail",
    "Google envió un enlace de confirmación": "Google sent a confirmation link",
    "Código de confirmación de Google": "Google confirmation code",
    "Esperando la confirmación de Google…": "Waiting for Google's confirmation…",
    "Reenvía las notificaciones de tus tarjetas y tus consumos aparecerán aquí solos. No compartes claves del banco — solo los correos que tú eliges reenviar.":
        "Forward your card notifications and your transactions show up here on their own. You never share bank passwords — only the emails you choose to forward.",
    "Tu dirección personal de ingesta (la usarás en los pasos de abajo):":
        "Your personal intake address (you'll use it in the steps below):",
    "Abre": "Open",
    "Gmail → Reenvío y correo POP/IMAP": "Gmail → Forwarding and POP/IMAP",
    "y haz clic en": "and click",
    "Agregar una dirección de reenvío": "Add a forwarding address",
    "Pega ahí tu dirección de ingesta (la de arriba) y acepta. Google enviará una confirmación —":
        "Paste your intake address (the one above) and accept. Google will send a confirmation —",
    "aparecerá aquí abajo automáticamente": "it will appear below automatically",
    ", no tienes que buscarla.": ", you don't have to look for it.",
    "Para reenviar solo los correos de tus tarjetas (y nada más de tu bandeja), importa este filtro listo:":
        "To forward only your card emails (and nothing else from your inbox), import this ready-made filter:",
    "Gmail → Filtros y direcciones bloqueadas": "Gmail → Filters and Blocked Addresses",
    "baja hasta": "scroll down to",
    "Importar filtros": "Import filters",
    "elige el archivo descargado y pulsa": "choose the downloaded file and press",
    "Crear filtros": "Create filters",
    "Crea un filtro con esta búsqueda y elige la acción":
        "Create a filter with this search and choose the action",
    "Reenviar a": "Forward to",
    "tu dirección de ingesta:": "your intake address:",
    "El reenvío solo captura correos nuevos. Para llenar tu tablero con lo que ya gastaste este mes, reenvíanos esos correos una sola vez:":
        "Forwarding only captures new emails. To fill your dashboard with what you've already spent this month, forward us those emails once:",
    "En Gmail busca": "In Gmail search for",
    "y selecciona los correos de este mes.": "and select this month's emails.",
    "En el menú": "In the menu",
    "Más": "More",
    "elige": "choose",
    "y envíalos a tu dirección de ingesta.": "and send them to your intake address.",
    "Los verás aparecer abajo en unos segundos.":
        "You'll see them appear below in a few seconds.",
    "Opcional — puedes saltarte este paso y tus consumos se irán registrando solos desde ahora.":
        "Optional — you can skip this step and your transactions will be recorded automatically from now on.",
    "Detectamos estas tarjetas en tus correos. Ponles una etiqueta si quieres (opcional):":
        "We detected these cards in your emails. Give them a label if you like (optional):",
    "¡Listo! Ya recibimos transacciones de tus tarjetas.":
        "Done! We've received transactions from your cards.",
    "Ver mi presupuesto": "See my budget",
    "En cuanto llegue tu primera transacción, aparecerá aquí.":
        "As soon as your first transaction arrives, it will appear here.",
    "¿Quieres un aviso al instante en cada compra?":
        "Want an instant alert on every purchase?",
    "Activa las alertas por Telegram": "Enable Telegram alerts",
    "No se pudo generar tu dirección de ingesta. Recarga la página o contacta soporte.":
        "We couldn't generate your intake address. Reload the page or contact support.",
}


def normalize_lang(code: str | None) -> str:
    return code if code in LANGS else DEFAULT_LANG


def lang_from_locale(locale: str | None) -> str:
    """Map a stored User.locale ("es-DO", "en", …) to a supported UI language."""
    return "en" if (locale or "").lower().startswith("en") else "es"


def translator(lang: str):
    """Return ``t(text)`` for the active language. Unknown language or missing
    translation → the Spanish source text."""
    lang = normalize_lang(lang)

    def t(text: str) -> str:
        if lang == "es":
            return text
        return _EN.get(text, text)

    return t
