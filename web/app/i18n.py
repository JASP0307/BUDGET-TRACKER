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
    "Abrir menú": "Open menu",
    "Cerrar menú": "Close menu",

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
    "Sugerido": "Suggested",
    "Aceptar": "Accept",
    "Crear regla con este comercio": "Create a rule from this merchant",
    "Cada vez que el comercio contenga este texto, se guardará en esta categoría — incluidos los consumos que ya tienes sin categoría.":
        "Whenever the merchant contains this text, it will be filed under this category — including transactions you already have uncategorized.",
    "Elige una categoría": "Choose a category",
    "Guardar regla": "Save rule",
    "Cancelar": "Cancel",
    "Descartar sugerencia": "Dismiss suggestion",
    "Sin transacciones todavía. Los consumos que lleguen por correo aparecerán aquí.":
        "No transactions yet. Charges that arrive by email will appear here.",
    "Reenvía las notificaciones de tus tarjetas a":
        "Forward your card notifications to",

    # manual transactions
    "Añadir a mano": "Add by hand",
    "Añadir transacción": "Add transaction",
    "Editar transacción": "Edit transaction",
    "Para lo que el banco no te notifica: efectivo, o una transferencia a otra persona.":
        "For what your bank doesn't email you about: cash, or a transfer to another person.",
    "Comercio o persona": "Merchant or person",
    "p. ej. Juan Pérez": "e.g. Jane Doe",
    "Fecha": "Date",
    "Monto": "Amount",
    "Moneda": "Currency",
    "Tipo": "Type",
    "Consumo": "Purchase",
    "Retiro de efectivo": "Cash withdrawal",
    "Reverso (devolución)": "Reversal (refund)",
    "Cuenta o tarjeta": "Account or card",
    "Elige una cuenta": "Choose an account",
    "Efectivo / otro": "Cash / other",
    "Automática (por reglas)": "Automatic (by rules)",
    "Es un cargo aparte, no un duplicado": "This is a separate charge, not a duplicate",
    "Guardar": "Save",
    "Eliminar transacción": "Delete transaction",
    "Sale del presupuesto de inmediato. No se puede deshacer.":
        "It leaves your budget immediately. This can't be undone.",
    "¿Eliminar esta transacción?": "Delete this transaction?",
    "¿Un gasto que el banco no te notifica?":
        "Spent something your bank doesn't email about?",
    "Añádelo a mano": "Add it by hand",
    # …and the form's own errors, rendered through t() on that page.
    "Escribe el comercio o la persona.": "Enter the merchant or person.",
    "Fecha inválida.": "Invalid date.",
    "Monto inválido.": "Invalid amount.",
    "El monto debe ser mayor que cero.": "The amount must be greater than zero.",
    "Moneda inválida.": "Invalid currency.",
    "Tipo inválido.": "Invalid type.",
    "Elige una cuenta o tarjeta.": "Choose an account or card.",
    "Categoría inválida.": "Invalid category.",
    "Ya tienes una transacción idéntica: mismo comercio, fecha, monto y cuenta. Si de verdad es un cargo aparte, márcalo abajo.":
        "You already have an identical transaction: same merchant, date, amount "
        "and account. If it really is a separate charge, tick the box below.",

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
    "Un aviso cuando una categoría llega a su umbral de presupuesto — no por cada compra.":
        "One alert when a category hits its budget threshold — not one per purchase.",
    "Umbral por categoría (% del presupuesto mensual). 100% = solo al excederlo.":
        "Per-category threshold (% of the monthly budget). 100% = only once exceeded.",
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
    "Confirmar reenvío desde": "Confirm forwarding from",
    "tu correo": "your email",
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
    "Hazlo desde el mismo Gmail con el que te registraste":
        "Do this from the same Gmail you signed up with",
    ". Solo mostramos confirmaciones que vengan de esa cuenta.":
        ". We only show confirmations coming from that account.",
    "Recibimos una confirmación de reenvío desde":
        "We received a forwarding confirmation from",
    ", que no es el correo de esta cuenta.": ", which is not this account's email.",
    "Vuelve a configurar el reenvío desde": "Set the forwarding up again from",
    ", o cambia el correo de tu cuenta.": ", or change your account's email.",
    "Primero termina el paso 1.": "Finish step 1 first.",
    "Gmail rechaza la importación si la dirección de reenvío todavía no está confirmada.":
        "Gmail rejects the import if the forwarding address isn't confirmed yet.",
    "Disponible en cuanto confirmes el reenvío.":
        "Available as soon as you confirm the forwarding.",
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
    "Copia esta búsqueda y pégala en el buscador de Gmail:":
        "Copy this search and paste it into Gmail's search box:",
    "Selecciona todos los resultados con la casilla de arriba. Ya vienen limitados a este mes, así que no tienes que escogerlos uno por uno.":
        "Select every result with the checkbox at the top. They're already limited to this month, so you don't have to pick them one by one.",
    "Si Gmail ofrece": "If Gmail offers",
    "Seleccionar todas las conversaciones que coinciden con esta búsqueda":
        "Select all conversations that match this search",
    ", pulsa ahí también.": ", click that too.",
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
    # Names both channels because they behave differently: Telegram fires per
    # transaction, email only when a category crosses its threshold.
    "¿Quieres un aviso al instante en cada compra, o un correo solo cuando te pases del presupuesto?":
        "Want an instant alert on every purchase, or an email only when you go over budget?",
    "Configura tus alertas": "Set up your alerts",
    "No se pudo generar tu dirección de ingesta. Recarga la página o contacta soporte.":
        "We couldn't generate your intake address. Reload the page or contact support.",

    # help / setup guide
    "Cómo funciona": "How it works",
    "Ver cómo se hace": "Watch how it's done",
    "Ver el recorrido completo": "Watch the full walkthrough",
    "Cualto lee las notificaciones que tu banco ya te envía por correo y arma tu presupuesto solo. No pide claves del banco: tú decides qué correos reenviar, y puedes cortarlo cuando quieras.":
        "Cualto reads the notifications your bank already emails you and builds your budget on its own. It never asks for bank passwords: you choose which emails to forward, and you can cut it off whenever you want.",
    "Lo que necesitas": "What you need",
    "Una cuenta de Gmail que reciba las notificaciones de tus tarjetas.":
        "A Gmail account that receives your cards' notifications.",
    "Una computadora. Gmail para teléfono no tiene la pantalla de reenvío ni los filtros.":
        "A computer. Gmail on the phone has neither the forwarding screen nor filters.",
    "Unos diez minutos, una sola vez.": "About ten minutes, once.",
    "Bancos que entendemos hoy": "Banks we understand today",
    "Si tu banco no está en la lista, todavía no podemos leer sus correos.":
        "If your bank isn't on the list, we can't read its emails yet.",
    "Los tres pasos": "The three steps",
    "en Gmail agregas nuestra dirección personal de ingesta como dirección de reenvío. Google te manda una confirmación y nosotros te la mostramos en la página; no tienes que buscarla en tu bandeja.":
        "in Gmail you add your personal intake address as a forwarding address. Google sends a confirmation and we show it to you on the page; you don't have to hunt for it in your inbox.",
    "importas un filtro que te damos hecho, para que se reenvíen únicamente los correos de tus bancos y nada más de tu bandeja. Este paso va después del primero: Gmail rechaza el filtro si la dirección de reenvío todavía no está confirmada.":
        "you import a ready-made filter so that only your banks' emails are forwarded and nothing else from your inbox. This step comes after the first one: Gmail rejects the filter if the forwarding address isn't confirmed yet.",
    "el reenvío solo captura correos nuevos, así que una vez reenvías los del mes en curso para que el tablero no empiece vacío.":
        "forwarding only catches new email, so you forward this month's once so the dashboard doesn't start empty.",
    "Todo eso vive en tu página de conexión, con tu dirección de ingesta ya puesta:":
        "All of that lives on your connection page, with your intake address already filled in:",
    "Después del primer día": "After the first day",
    "Al principio casi todo cae en «Otros / sin categoría». Tú creas reglas —o aceptas las que te sugerimos— y desde entonces se clasifica solo.":
        "At first almost everything lands in \"Other / uncategorized\". You create rules — or accept the ones we suggest — and from then on it sorts itself.",
    "Los montos del presupuesto son por mes: cada mes nuevo los defines otra vez.":
        "Budget amounts are per month: each new month you set them again.",
    "Las categorías que vienen puestas son un punto de partida; puedes borrarlas y crear las tuyas.":
        "The categories that come preset are a starting point; you can delete them and create your own.",
    "Cómo se corta": "How to cut it off",
    "Borra el filtro de reenvío en tu Gmail y dejamos de recibir correos en ese mismo instante. También puedes descargar todos tus datos o borrar la cuenta entera desde":
        "Delete the forwarding filter in your Gmail and we stop receiving email that same instant. You can also download all your data or delete the whole account from",
    "Qué guardamos y por cuánto tiempo está en": "What we keep and for how long is in",

    # footer + account page
    "Cuenta": "Account",
    "Privacidad": "Privacy",
    "Seguridad": "Security",
    "Tus datos": "Your data",
    "Tu cuenta": "Your account",
    "Tus datos son tuyos. Descárgalos o bórralos cuando quieras, sin pedirnos permiso.":
        "Your data is yours. Download it or delete it whenever you want, without asking us.",
    "Descargar mis datos": "Download my data",
    "Un archivo JSON con todas tus transacciones, categorías, reglas y presupuestos.":
        "A JSON file with all your transactions, categories, rules and budgets.",
    "Descargar JSON": "Download JSON",
    # verified forwarders
    "Correos que pueden reenviarte": "Mailboxes that can forward to you",
    "Solo aceptamos correos reenviados desde estas direcciones. Si reenvías desde otra, lo ignoramos — así nadie más puede meter consumos en tu cuenta.":
        "We only accept mail forwarded from these addresses. If you forward from "
        "another one we ignore it — that way nobody else can put transactions "
        "into your account.",
    "tu cuenta": "your account",
    "Y solo si los reenvía un buzón que registraste en tu cuenta: aunque alguien adivine tu dirección de reenvío, no puede meterte consumos.":
        "And only if a mailbox you registered on your account forwarded them: "
        "even if someone guesses your forwarding address, they cannot push "
        "transactions onto you.",
    "Ignoramos correos reenviados desde:": "We ignored mail forwarded from:",
    "Solo aceptamos reenvíos desde el correo de tu cuenta. Si ese buzón también es tuyo, agrégalo en":
        "We only accept forwards from your account's own address. If that "
        "mailbox is yours too, add it in",
    "Agregar otro correo": "Add another address",
    "Agregar": "Add",
    "Quitar": "Remove",
    "Qué guardamos": "What we store",
    "Los últimos 4 dígitos de tu tarjeta — nunca el número completo.":
        "The last 4 digits of your card — never the full number.",
    "El comercio, el monto y la fecha de cada consumo.":
        "The merchant, amount and date of each transaction.",
    "Nunca tu clave del banco: no la pedimos y no podríamos usarla.":
        "Never your bank password: we don't ask for it and couldn't use it.",
    "El correo original del banco se borra en cuanto lo leemos.":
        "The bank's original email is deleted as soon as we read it.",
    "Leer la política de privacidad completa": "Read the full privacy policy",
    "Eliminar mi cuenta": "Delete my account",
    "Borra tu cuenta y todos tus datos de inmediato. No se puede deshacer.":
        "Deletes your account and all your data immediately. This cannot be undone.",
    "Después de borrar, quita el filtro de reenvío en Gmail para que dejemos de recibir correos.":
        "After deleting, remove the Gmail forwarding filter so we stop receiving emails.",
    "Tu contraseña": "Your password",
    "Escribe ELIMINAR para confirmar": "Type DELETE to confirm",
    "Eliminar mi cuenta y mis datos": "Delete my account and my data",

    # privacy page
    "En lenguaje claro: qué recibimos, qué guardamos, quién más lo ve y cómo borrarlo todo.":
        "In plain language: what we receive, what we store, who else sees it, and how to delete all of it.",
    "Nunca te pedimos la clave de tu banco": "We never ask for your bank password",
    "No usamos agregadores bancarios ni te pedimos tu usuario y contraseña del banco. No vemos tu balance, ni tus estados de cuenta, ni tu historial, y no podemos mover dinero. Lo único que recibimos son los correos de notificación que tú decides reenviarnos.":
        "We don't use banking aggregators and we don't ask for your bank username or password. We can't see your balance, your statements or your history, and we can't move money. All we receive are the notification emails you choose to forward to us.",
    "Tú decides qué nos llega": "You decide what reaches us",
    "Durante la configuración creas un filtro en Gmail que reenvía únicamente los correos que vienen de tu banco, a una dirección personal tuya en nuestro servidor. Ningún otro correo de tu bandeja se nos envía nunca.":
        "During setup you create a Gmail filter that forwards only the emails coming from your bank, to a personal address of yours on our server. No other mail in your inbox is ever sent to us.",
    "Ese filtro es tuyo y vive en tu Gmail. Si lo borras, dejamos de recibir correos en ese mismo instante — no tienes que pedírnoslo ni esperar a que hagamos nada.":
        "That filter is yours and lives in your Gmail. If you delete it, we stop receiving mail that same instant — you don't have to ask us or wait for us to do anything.",
    "Tu correo electrónico y tu contraseña, guardada como hash Argon2id (no podemos leerla).":
        "Your email address and your password, stored as an Argon2id hash (we can't read it).",
    "Los últimos 4 dígitos de cada tarjeta, más la etiqueta que tú le pongas. Nunca el número completo — los correos del banco no lo incluyen.":
        "The last 4 digits of each card, plus whatever label you give it. Never the full number — the bank's emails don't include it.",
    "De cada consumo: comercio, monto, moneda, fecha y la categoría asignada.":
        "For each transaction: merchant, amount, currency, date and assigned category.",
    "Tus categorías, reglas y montos de presupuesto.":
        "Your categories, rules and budget amounts.",
    "Lo que no guardamos:": "What we don't store:",
    "tu balance, tus claves, el número completo de tu tarjeta, ni tus otros correos.":
        "your balance, your passwords, your full card number, or any of your other email.",
    "El correo original se borra": "The original email is deleted",
    "Cuando llega un correo del banco lo leemos, extraemos el consumo y borramos el cuerpo del correo de inmediato. Lo único que queda es la transacción.":
        "When an email arrives from your bank we read it, extract the transaction, and delete the email body immediately. All that remains is the transaction.",
    "La excepción: si un correo tiene un formato que aún no sabemos leer, guardamos su contenido hasta 30 días para poder darle soporte a ese banco. Pasado ese plazo se borra automáticamente.":
        "The exception: if an email has a format we can't read yet, we keep its content for up to 30 days so we can add support for that bank. After that it is deleted automatically.",
    "Quién más ve tus datos": "Who else sees your data",
    "Usamos estos proveedores, y ninguno más:": "We use these providers, and no others:",
    "parte de ActiveCampaign, Estados Unidos": "part of ActiveCampaign, United States",
    "recibe los correos que reenvías y nos los entrega; también envía nuestros correos de confirmación y de contraseña. Al estar en EE. UU., le aplican leyes como el CLOUD Act. Configuramos su retención en el mínimo que ofrecen.":
        "receives the emails you forward and delivers them to us; it also sends our confirmation and password emails. Being US-based, laws such as the CLOUD Act apply to it. We set its retention to the minimum they offer.",
    "solo si activas las alertas. En ese caso el nombre del comercio y el monto se envían a los servidores de Telegram. Si no lo activas, Telegram no recibe nada.":
        "only if you enable alerts. In that case the merchant name and amount are sent to Telegram's servers. If you don't enable it, Telegram receives nothing.",
    "Nuestro proveedor de servidor, donde corre la aplicación y la base de datos.":
        "Our server provider, where the application and database run.",
    "No vendemos tus datos, no hacemos publicidad y no usamos rastreadores ni analítica de terceros.":
        "We don't sell your data, we don't run ads, and we use no third-party trackers or analytics.",
    "Qué puede ver el desarrollador": "What the developer can see",
    "Preferimos decirlo que esconderlo: cuando un correo llega con un formato que la aplicación no logra leer, aparece en un panel interno donde yo (el desarrollador) puedo abrir su contenido para agregar soporte a ese banco. Es el mismo correo de notificación que ya recibiste tú, y se borra a los 30 días.":
        "We'd rather say it than hide it: when an email arrives in a format the app can't read, it shows up in an internal panel where I (the developer) can open its content to add support for that bank. It's the same notification email you already received, and it's deleted after 30 days.",
    "Los correos que sí se procesan correctamente no pasan por ahí: su contenido ya se borró.":
        "Emails that are processed successfully never go through there: their content has already been deleted.",
    "Cifrado": "Encryption",
    "Todo el tráfico va por HTTPS. Los cuerpos de correo que se conservan (los que no pudimos leer) se guardan cifrados en la base de datos, lo que protege copias de seguridad y discos, pero no es cifrado de extremo a extremo: el servidor tiene la llave para poder leerlos. Ver":
        "All traffic goes over HTTPS. The email bodies we do keep (the ones we couldn't read) are stored encrypted in the database, which protects backups and disks, but this is not end-to-end encryption: the server holds the key so it can read them. See",
    "la página de seguridad": "the security page",
    "Borrar todo": "Delete everything",
    "Desde": "From",
    "puedes descargar todos tus datos en JSON y eliminar tu cuenta. El borrado es inmediato y definitivo: se eliminan tus transacciones, tarjetas, categorías, reglas, presupuestos y tu dirección de reenvío. No guardamos copias.":
        "you can download all your data as JSON and delete your account. Deletion is immediate and permanent: your transactions, cards, categories, rules, budgets and forwarding address are all removed. We keep no copies.",
    "Contacto": "Contact",
    "Escríbeme a": "Email me at",
    "si tienes dudas o quieres reportar un problema de seguridad.":
        "if you have questions or want to report a security problem.",
    "Última actualización": "Last updated",

    # security page
    "Qué hacemos para proteger tus datos, y qué no hacemos. Sin exageraciones.":
        "What we do to protect your data, and what we don't. No exaggeration.",
    "Lo más importante: no hay nada que robar": "The main thing: there's nothing to steal",
    "El riesgo de una app de finanzas suele ser que guarda las llaves de tu banco. Aquí no existen: no pedimos credenciales bancarias, no tenemos acceso a tu cuenta y nadie que entrara a nuestro servidor podría mover tu dinero. En el peor de los casos vería un historial de consumos.":
        "The risk with a finance app is usually that it holds the keys to your bank. Here those keys don't exist: we don't ask for banking credentials, we have no access to your account, and anyone breaking into our server could not move your money. At worst they would see a list of purchases.",
    "Cómo protegemos la cuenta": "How we protect your account",
    "Contraseñas guardadas con Argon2id, el algoritmo recomendado hoy para esto. No podemos ver tu contraseña.":
        "Passwords stored with Argon2id, the currently recommended algorithm. We cannot see your password.",
    "Sesiones en cookies firmadas, marcadas Secure y HttpOnly, con vencimiento a 14 días.":
        "Sessions in signed cookies, marked Secure and HttpOnly, expiring after 14 days.",
    "Al cambiar tu contraseña se cierran todas las sesiones abiertas en otros dispositivos y se anulan los enlaces de recuperación pendientes.":
        "Changing your password closes every session open on other devices and voids any pending recovery links.",
    "Los enlaces de confirmación y de recuperación están firmados y vencen (24 horas y 1 hora). Cada enlace de recuperación sirve una sola vez.":
        "Confirmation and recovery links are signed and expire (24 hours and 1 hour). Each recovery link works only once.",
    "Límites de intentos en el inicio de sesión y en el envío de correos, para frenar ataques de fuerza bruta.":
        "Attempt limits on sign-in and on outgoing email, to stop brute-force attacks.",
    "Todo el tráfico va por HTTPS, con HSTS activado.":
        "All traffic goes over HTTPS, with HSTS enabled.",
    "Cómo protegemos los datos": "How we protect the data",
    "Cada consulta está limitada a tu usuario: una cuenta no puede leer ni modificar los datos de otra.":
        "Every query is scoped to your user: one account cannot read or modify another's data.",
    "Guardamos lo mínimo: últimos 4 dígitos, comercio, monto, fecha. Nunca el número completo de tarjeta ni tu balance.":
        "We store the minimum: last 4 digits, merchant, amount, date. Never the full card number or your balance.",
    "El cuerpo del correo del banco se borra en cuanto se procesa; los que no pudimos leer se guardan cifrados y se eliminan a los 30 días.":
        "The bank email's body is deleted as soon as it is processed; the ones we couldn't read are stored encrypted and removed after 30 days.",
    "Solo aceptamos correos que vengan de un dominio bancario conocido; el resto se descarta.":
        "We only accept email coming from a known bank domain; everything else is discarded.",
    "Los límites de ese cifrado": "The limits of that encryption",
    "Los cuerpos de correo que se conservan están cifrados en la base de datos, y la llave vive en la configuración del servidor. Eso protege copias de seguridad, volcados de base de datos y discos robados. No protege contra alguien que logre entrar al servidor mismo, porque ahí está la llave. Preferimos decirlo así en vez de llamarlo cifrado de extremo a extremo, que no lo es.":
        "The email bodies we keep are encrypted in the database, and the key lives in the server's configuration. That protects backups, database dumps and stolen disks. It does not protect against someone who gets into the server itself, because that's where the key is. We'd rather say it that way than call it end-to-end encryption, which it isn't.",
    "Lo que este proyecto no es": "What this project is not",
    "Es un proyecto de una sola persona. No tengo certificación SOC 2 ni ISO, no tengo un equipo de seguridad y no voy a decirte que tenemos \"seguridad de nivel bancario\" porque sería mentira.":
        "This is a one-person project. I have no SOC 2 or ISO certification, no security team, and I'm not going to tell you we have \"bank-level security\" because that would be a lie.",
    "Lo que sí puedo ofrecerte es verificable:": "What I can offer you is verifiable:",
    "El código es abierto: puedes leer exactamente qué hacemos con tus correos.":
        "The code is open: you can read exactly what we do with your email.",
    "Puedes descargar todos tus datos y borrar tu cuenta en cualquier momento, sin pedir permiso.":
        "You can download all your data and delete your account at any time, without asking permission.",
    "Puedes cortar el reenvío desde tu propio Gmail cuando quieras.":
        "You can cut off the forwarding from your own Gmail whenever you want.",
    "Esta página dice qué guardamos y quién más lo ve, incluido lo incómodo.":
        "This page says what we store and who else sees it, including the uncomfortable parts.",
    "Ver el código": "View the code",
    "Reportar una vulnerabilidad": "Report a vulnerability",
    "Respondo en un máximo de 72 horas. No hay recompensa económica, pero sí crédito si lo quieres. Por favor no pruebes contra cuentas que no sean tuyas.":
        "I respond within 72 hours at most. There's no bug bounty, but there is credit if you want it. Please don't test against accounts that aren't yours.",
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
