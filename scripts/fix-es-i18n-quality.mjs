/**
 * Quality pass for frontend/src/assets/i18n/es.json
 * Informal tú tone, ordenador over computadora, natural LA-friendly Spanish.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');
const en = JSON.parse(fs.readFileSync(path.join(root, 'frontend/src/assets/i18n/en.json'), 'utf8'));
const it = JSON.parse(fs.readFileSync(path.join(root, 'frontend/src/assets/i18n/it.json'), 'utf8'));
let es = JSON.parse(fs.readFileSync(path.join(root, 'frontend/src/assets/i18n/es.json'), 'utf8'));

/** Path-specific overrides (EN meaning → quality ES). */
const PATH_OVERRIDES = {
  'setup.welcome.lead': 'Tu agente se ejecuta en este {{device}}.',
  'setup.welcome.haveAccount': 'Ya tengo una cuenta',
  'setup.welcome.accountHint':
    'Instala Babo en este ordenador primero. Configurarás el pensamiento y las funciones, y luego iniciarás sesión.',
  'setup.prepare.title': 'Instalación',
  'setup.prepare.lead': 'Instalando lo que Babo necesita en este ordenador.',
  'setup.device.title': 'Este dispositivo',
  'setup.device.nextHint':
    'En la siguiente pantalla, elige <strong>Babo Cloud</strong> para la configuración más sencilla, o una opción local avanzada si prefieres privacidad o modelos sin conexión.',
  'setup.device.venvWait':
    'Todavía se están instalando las dependencias de Python — espera a que termine el paso de preparación.',
  'setup.device.taglineReady':
    'Tu ordenador parece listo. A continuación elegirás dónde se ejecuta el chat — Babo Cloud es el camino más sencillo.',
  'setup.device.taglineCloud':
    'Tu ordenador parece listo. Babo Cloud es el camino más sencillo; los modelos locales siguen disponibles como opción avanzada.',
  'setup.thinking.needScan':
    'Termina primero el paso del dispositivo para que podamos recomendar una configuración.',
  'setup.thinking.lanFound':
    'Encontramos un servidor de chat en tu red — se sugiere <strong>Mi servidor</strong>.',
  'setup.thinking.cloudHint':
    'Los modelos y el acceso remoto pasan por <strong>api.babo.agency</strong>. Tus agentes siempre se ejecutan en este ordenador — Babo Cloud sincroniza tu cuenta y retransmite cuando no estás.',
  'setup.thinking.useApiKey': 'Usa la clave API solo en este ordenador',
  'setup.thinking.cards.cloud.subtitle':
    'El camino más sencillo — modelos alojados y sincronización de cuenta. Tu agente sigue ejecutándose en este ordenador.',
  'setup.thinking.cards.local.title': 'Este ordenador',
  'setup.thinking.cards.local.subtitle':
    'Avanzado — comprobaremos si este PC puede ejecutar un modelo local confiable (Ollama). Ideal para privacidad o uso sin conexión.',
  'setup.thinking.cards.lan.title': 'Mi servidor',
  'setup.thinking.cards.lan.subtitle':
    'Avanzado — servidor vLLM o compatible con OpenAI en tu red doméstica.',
  'setup.thinking.useCloud': 'Usa Babo Cloud en su lugar',
  'setup.thinking.lanHint': 'Usa tu servidor vLLM o compatible con OpenAI (puerto 8000), no Ollama 11434.',
  'setup.thinking.localHint':
    'Comprobaremos este PC en busca de un modelo local confiable. Instala Ollama y luego prueba la conexión.',
  'setup.thinking.placePc': 'Este ordenador',
  'setup.thinking.placeLan': 'Tu servidor LAN',
  'setup.features.screenDesc': 'Vista en segundo plano de tu escritorio',
  'setup.placement.leadDefault':
    'Elige dónde se sincroniza tu cuenta de Babo. El chat se ejecuta en {{location}}; los agentes permanecen en tus dispositivos.',
  'setup.placement.leadByok':
    'Tus claves API pueden transmitirse por Babo Cloud (recomendado) o por un servidor NestJS que administres. Elige dónde se sincroniza tu cuenta de Babo — los agentes siguen ejecutándose localmente.',
  'setup.placement.venvWait':
    'Termina de instalar Babo en este ordenador (paso de preparación) antes de continuar.',
  'setup.placement.locLocal': 'este ordenador',
  'setup.placement.locLan': 'tu servidor LAN',
  'setup.placement.locByok': 'tu proveedor de API',
  'setup.placement.locOther': 'tu proveedor elegido',
  'setup.auth.leadSignin': 'Inicia sesión en {{server}} — necesario para agentes y sincronización.',
  'setup.billing.title': 'Activa Babo Cloud',
  'setup.billing.perk2': 'Modelos con tu clave API o pay-as-you-go opcional (sin margen)',
  'setup.billing.perk3': 'Paga con tarjeta o Link — tarda aproximadamente un minuto',
  'setup.billing.allSet': 'Todo listo — creemos tu agente.',
  'setup.billing.lead':
    'Un último paso antes de tu primer chat — modelos alojados, sincronización de cuenta y acceso remoto mientras tu agente sigue en este ordenador.',
  'setup.billing.waitingPayment':
    'Completa el pago en tu navegador — lo detectaremos automáticamente.',
  'setup.billing.altHint': '¿Usar tus propias claves API en su lugar?',
  'setup.ready.title': 'Estás listo',
  'setup.ready.changeHint': 'Puedes cambiarlo en cualquier momento en Configuración.',
  'setup.ready.yourApiKey': 'Tu clave API',
  'setup.name.title': 'Ponle nombre a tu agente',
  'setup.name.open': 'Abrir Babo',
  'setup.fit.good': 'Buen ajuste',
  'setup.voice.thisPc': 'Este ordenador',
  'setup.voice.thisComputer': 'este ordenador',
  'setup.lan.lead':
    'vLLM, Whisper o visión en tu LAN. Agrega SSH para ver qué modelos caben en la GPU de esa máquina.',
  'setup.lan.findServices': 'Buscar servicios',
  'setup.runtime.downloadingVision': 'Descargando modelo de screen awareness…',
  'setup.errors.nameTooShort': 'Elige un nombre de al menos 2 caracteres.',
  'setup.errors.subscribeBeforeAgent': 'Suscríbete a Babo Cloud antes de crear tu agente.',
  'setup.errors.waitPython': 'Espera a que termine la configuración de Python antes de continuar.',
  'setup.errors.scanFailed': 'Escaneo fallido',
  'setup.errors.enterCredentials': 'Ingresa tu correo y contraseña, o crea una cuenta.',
  'setup.errors.installBeforeSignIn': 'Termina de instalar Babo en este ordenador antes de iniciar sesión.',
  'setup.errors.chooseBackend': 'Elige dónde se sincroniza tu cuenta o ingresa una URL de servidor válida.',
  'setup.errors.chooseAccountServer': 'Elige primero un servidor de cuenta (paso anterior).',
  'setup.errors.pythonNotReady':
    'El entorno Python aún no está listo. Vuelve al paso de preparación y espera a que termine la configuración.',
  'setup.test.signInAfterSetup': 'Inicia sesión después de la configuración para verificar Babo Cloud',
  'setup.test.signInNext': 'Inicia sesión en el siguiente paso para conectarte',
  'setup.test.enterServer': 'Introduce primero una dirección de servidor',
  'setup.tier.thisComputer': 'este ordenador',
  'setup.reasons.codeSearchLocal': 'Búsqueda semántica de código en este ordenador.',
  'setup.ollama.checking': 'Comprobando…',
  'setup.ollama.running': 'En ejecución',
  'setup.ollama.notFound': 'No encontrado',
  'setup.ollama.installBody':
    'Descarga y ejecuta el instalador para Windows. Ollama se agrega a tu PATH.',
  'setup.ollama.startBody': 'Abre Ollama desde el menú Inicio o ejecuta ollama serve en una terminal.',
  'setup.ollama.lead': 'Babo usa Ollama en este PC. Sigue estos pasos y luego elige este modelo para chatear.',
  'setup.ollama.close': 'Cerrar',
  'setup.ollama.pullBody': 'Descarga {{modelId}} — puede tardar varios minutos según el tamaño.',
  'setup.lanGuide.sshBody': 'Usa el host y el usuario SSH de arriba. Confirma que nvidia-smi muestra tu GPU.',
  'setup.lanGuide.connectBody':
    'Elige Mi servidor en la configuración y establece la dirección en http://<server-ip>:8000, luego prueba la conexión.',
  'setup.lanSheet.gpuSecretHint':
    'Requerido para Vision en el puerto 8443. Usa el mismo valor que BABO_VISION_SECRET en el servidor (p. ej., nls-dev-secret).',
  'setup.lanSheet.passwordHint':
    'Úsalo cuando este PC no tenga una clave SSH en el servidor. No se guarda en disco.',
  'setup.lanSheet.sshHint':
    'Requerido para el escaneo de GPU. Usa usuario@host arriba o establece usuario + contraseña aquí.',
  'settings.appearance.desc':
    'Tema e idioma para la interfaz de Babo. El tema Sistema sigue tu sistema operativo.',
  'settings.appearance.themeLight': 'Claro',
  'settings.appearance.languageHint':
    'Solo idioma de interfaz. Tu agente responde en el idioma en el que escribes. Auto sigue tu dispositivo.',
  'settings.models.desc':
    'Modelo de chat predeterminado, Babo Cloud, subagentes, voz y pantalla. Elige un modelo distinto por mensaje en el compositor del chat.',
  'settings.system.running': 'En ejecución',
  'settings.system.stopped': 'Detenido',
  'settings.support.exporting': 'Exportando…',
  'settings.support.refreshing': 'Actualizando…',
  'settings.support.notFound': 'No encontrado',
  'settings.permissions.desc': 'A qué puede acceder el agente local en este ordenador.',
  'settings.billing.subscribeHint':
    'Los modelos alojados requieren una suscripción activa. Tu clave API en este dispositivo (BYOK) sigue funcionando sin suscripción.',
  'settings.billing.checklistByok': 'Modelos con tu clave API (BYOK) o pay-as-you-go al costo del proveedor',
  'settings.billing.refundNote':
    'Reembolso de 31 días disponible en tu primer pago de suscripción hasta el {{date}}. Los cargos pay-as-you-go de modelos no son reembolsables.',
  'settings.integrations.messaging': 'Telegram y WhatsApp',
  'settings.integrations.saveResend': 'Guardar credenciales de Resend',
  'settings.integrations.resendKey': 'Clave API de Resend',
  'settings.integrations.baboCloudHint':
    'Email y Google Workspace usan credenciales proporcionadas por Babo en {{url}}. Conecta canales por agente en Herramientas.',
  'settings.integrations.emailActive': 'Email (Resend) activo',
  'settings.integrations.emailHint':
    'El correo entrante llega a tu webhook NestJS y luego se retransmite a Babo Desktop. Configura Resend aquí o establece RESEND_API_KEY y RESEND_INBOUND_DOMAIN en tu despliegue NestJS.',
  'settings.integrations.webhookUrl': 'URL del webhook entrante de Resend:',
  'settings.integrations.resendSaved': 'Tus credenciales de Resend guardadas',
  'settings.integrations.resendOnServer': 'Resend configurado en el servidor',
  'settings.integrations.messagingHint':
    'Telegram usa tu token de BotFather y registra webhooks en tu servidor NestJS. WhatsApp se empareja por QR en Babo Desktop — no hay webhook NestJS para mensajes entrantes de WhatsApp.',
  'settings.integrations.inboundHint': 'Dominio verificado en Resend para enrutamiento entrante.',
  'settings.integrations.googleHint':
    'Crea una app OAuth de Google Cloud y guarda client_id / client_secret en Herramientas → Google Workspace de cada agente, o pídele al agente en el chat que te guíe en la configuración.',
  'settings.keys.desc': 'Claves para acceso programático a tu cuenta en la nube de Babo.',
  'settings.general.checkingUpdates': 'Comprobando…',
  'settings.general.onboardingHint':
    'Vuelve a mostrar consejos introdutorios en Chat, Herramientas, Memoria y otras páginas.',
  'settings.confirm.resetPermissions':
    '¿Borrar todas las decisiones de permisos guardadas? Te volveremos a preguntar cuando haga falta.',
  'settings.backendTest.enterServer': 'Introduce primero una dirección de servidor',
  'info.neural_state.p1':
    'Tu agente tiene un <b>heartbeat</b> virtual que refleja su actividad cognitiva. El BPM sube cuando piensa, conversa o explora, y baja cuando descansa o duerme. La etiqueta de estado (<b>Active</b>, <b>Resting</b>, <b>Drowsy</b>, <b>Deep Sleep</b>) se deriva directamente del BPM.',
  'info.activity.p1':
    'Este feed muestra lo que hace tu agente <b>de forma autónoma</b> — acciones que realiza por sí solo, sin que se lo pidas. Cada tipo aparece con color en el feed.',
  'info.activity.legend.reach_out': '💬 Reach Out',
  'info.activity.legend.reach_out_desc':
    'Iniciativa social proactiva — el agente decide contactarte por su cuenta',
  'info.activity.legend.finding': '📋 Finding',
  'info.activity.legend.finding_desc':
    'Un descubrimiento que el agente considera que vale la pena compartir contigo',
  'info.activity.legend.drive': '⚡ Drive',
  'info.working_memory.p1':
    'El <b>contexto activo</b> del agente — en qué está \"pensando\" ahora. Estos elementos rotan según el flujo de la conversación. Puedes <b>editar</b> o <b>eliminar</b> elementos para orientar en qué se enfoca el agente.',
  'nav.signOut': 'Cerrar sesión',
  'chat.nav.close': 'Cerrar',
  'chat.nav.rename': 'Renombrar',
  'chat.nav.resetHomeMessage':
    '¿Iniciar un nuevo hilo de Inicio?\n\nSe crea una nueva rama y se establece como Inicio. Tu Inicio actual permanece en la lista como una rama. El conocimiento del agente (hechos, memoria) no cambia.',
  'chat.nav.cannotDeleteHome':
    'No puedes eliminar el hilo de Inicio actual. Restablece Inicio para empezar de cero, o establece otro hilo como Inicio primero.',
  'chat.composer.askBanner':
    'Responde abajo — el agente está esperando tu respuesta antes de poder continuar.',
  'chat.composer.budgetBanner':
    'Se alcanzó el límite de pasos — responde con <strong>10</strong>, <strong>20</strong> o <strong>40</strong> para más pasos, o <strong>stop</strong> para terminar.',
  'chat.workbench.close': 'Cerrar workbench',
  'chat.workbench.emptyHint': 'Usa Chat task para este hilo, Background para trabajo autónomo.',
  'chat.workbench.emptyFocusedHint': 'Cambia a Standard o Debug para ver lecturas y actividad.',
  'chat.workbench.entries.working': 'Trabajando…',
  'chat.workbench.running': 'Trabajando',
  'chat.inbox.empty': 'Todo al día.',
  'chat.live.cryptexEmpty': 'Sin contexto activo — inicia una conversación para llenar el Cryptex',
  'chat.live.metric.engage': 'Engagement',
  'chat.live.metric.cohere': 'Coherencia',
  'chat.live.noActivity': 'Sin actividad aún…',
  'chat.prompt.stayAwake': 'Quédate despierto',
  'chat.prompt.askWaiting': 'El agente está esperando tu respuesta.',
  'chat.prompt.restYes': 'Sí, descansa',
  'chat.delegate.workingDots': 'trabajando...',
  'chat.delegate.working': 'trabajando…',
  'chat.context.reachHint':
    'Destaca este hilo para que Babo sepa dónde contactarte con informes y avances cuando no haya un objetivo explícito.',
  'chat.context.privateBody':
    'Los mensajes aquí permanecen en tu escritorio. No se envía nada a Discord, email u otras superficies conectadas a menos que Babo use explícitamente una herramienta de canal.',
  'chat.runtime.browserSignIn':
    'Abre tu navegador para iniciar sesión — complétalo allí y luego haz clic en \"Listo\" en el diálogo.',
  'chat.runtime.loopInterrupted':
    'La tarea anterior se interrumpió en el paso {{step}}. Usa Continuar para reanudar.',
  'chat.runtime.processing': 'procesando',
  'chat.runtime.bgTool.managing': 'gestionando',
  'chat.drive.web_search': 'Búsqueda web',
  'chat.drive.reach_out': 'Reach Out',
  'creation.soulWishTitle': 'Ponle nombre a tu agente',
  'tools.integrationsHint': 'Conexiones de canal para tu agente',
  'tools.save': 'Guardar configuración',
  'tools.setup.save': 'Guardar',
  'tools.setup.completeSetupBody': 'Completa los campos obligatorios abajo y luego guarda.',
  'brain.schedule.save': 'Guardar horario',
  'charter.saveJob': 'Guardar rol',
  'charter.saveTrust': 'Guardar confianza',
  'capabilities.save': 'Guardar y reiniciar runtime',
  'capabilities.thisPc': 'Este ordenador',
  'capabilities.localOllamaHint':
    'Busca Ollama en este ordenador (puerto 11434). Usa Prueba para detectar tu modelo.',
  'capabilities.useCloudInstead': 'Usa Babo Cloud en su lugar',
  'capabilities.useOwnApiKey': 'Usa tu propia clave API en su lugar',
  'capabilities.cards.local.title': 'Este ordenador',
  'memory.soul.importWarning':
    'Esto sobrescribirá el estado actual del agente. Haz una exportación primero si hace falta.',
  'memory.soul.exportDesc':
    'Descarga el estado completo de este agente como archivo .soul.zip portable.',
  'tools.files.saved': '¡Guardado!',
  'tools.repair.fixed': '¡Corregido!',
  'tools.setup.email.deployPublic':
    'Implementa NestJS con una URL HTTPS pública (Railway, VPS, etc.) para que Resend pueda llegar a tu webhook.',
  'tools.setup.email.resendAccount':
    'Crea una cuenta de Resend, verifica un dominio entrante y obtén una clave API.',
  'tools.setup.email.resendCredentialsSettings':
    'o guarda tus credenciales de Resend en Configuración → Integraciones.',
  'tools.setup.email.unavailable':
    'El email aún no está disponible en este servidor de Babo Cloud — contacta a soporte.',
  'tools.setup.email.resendWebhook': 'Apunta el webhook entrante de Resend a: {{url}}',
  'tools.setup.common.localNestWarn':
    'NestJS está en {{host}} — Resend y Telegram no pueden llamar a localhost. Implementa NestJS en una URL HTTPS pública (Railway, VPS, homelab) o usa un túnel, configura PUBLIC_API_URL en el servidor y apunta los webhooks a esa URL pública.',
  'tools.setup.google.cloud1':
    'Haz clic en Conectar — Babo abre el inicio de sesión de Google con la app OAuth integrada.',
  'tools.setup.google.cloud2':
    'Concede los permisos solicitados para Gmail, Calendar, Drive y Sheets.',
  'tools.setup.telegram.step1':
    'Abre Telegram y escribe a @BotFather — envía /newbot y sigue las instrucciones.',
  'tools.setup.telegram.step4':
    'Pega el token del bot cuando te lo pidan y completa identidad del propietario y política de DM en la config.',
  'tools.setup.telegram.step3Self':
    'Asegúrate de que tu servidor NestJS sea público ({{url}}) y Babo Desktop esté en línea (relay).',
  'tools.setup.telegram.step3Local':
    'Cuando NestJS tenga una URL HTTPS pública, usa Configuración en el chat — el agente registra el webhook de Telegram en esa URL (no localhost).',
  'tools.setup.whatsapp.step2':
    'Haz clic en Iniciar emparejamiento y escanea el código QR con WhatsApp → Dispositivos vinculados.',
  'tools.setup.whatsapp.step4':
    'Mantén Babo Desktop en ejecución mientras esperas que el agente reciba mensajes de WhatsApp.',
  'tools.setup.slack.step2': 'Habilita Event Subscriptions y establece Request URL en:',
  'tools.setup.slack.step4':
    'Copia el token del bot (xoxb-…) y el signing secret en Configuración en el chat o en el formulario de abajo.',
  'tools.setup.discord.step1':
    'Crea un bot en Discord Developer Portal y copia el token del bot.',
  'tools.setup.discord.step2Self':
    'Asegúrate de que NestJS sea público ({{url}}) y el relay de Babo Desktop esté en línea.',
  'dashboard.emptyHintBefore': 'Haz clic en el orbe o presiona',
  'creation.clickOrb': 'Haz clic en el orbe para dar inicio a la vida',
  'capabilities.lanFitHint':
    'Conéctate por SSH a tu homelab (p. ej., GX10) para ver qué modelos caben en su GPU.',
  'tools.channels.emptyHint':
    'Haz clic en Sincronizar canales — el bot debe estar en un servidor con permiso para listar canales.',
  'tools.errors.configureResend': 'Configura Resend primero en Configuración → Integraciones',
};

/** When ES still equals EN but IT is translated — keep EN terms IT also keeps, else translate. */
const EN_KEEP = new Set([
  'computer', 'device', 'GPU', 'Provider', 'API key', 'Babo Cloud', 'Email', 'Password', 'Account',
  'Extras', 'on', 'off', 'Off', 'Host', 'Cloud', 'Babo hosted', 'General', 'Server:', 'Setup {{status}}',
  'Screen awareness', 'Screen awareness (Moondream / VLM)', 'Find services', 'Good fit', 'Name your agent',
  'Open Babo', 'Scan failed', 'Downloading screen awareness model…', 'Ollama', 'Lifetime', 'Active',
  'Browser', 'Context', 'Live', 'Inbox', 'Home', 'Thread', 'Workbench', 'Standard', 'Debug', 'Task',
  'Team', 'Input', 'Budget', 'Background', 'Tool', 'Bash', 'Orchestrator', 'Planning', 'Delegating',
  'Monitoring', 'Evaluating', 'Executing', 'Responding', 'Unknown', 'Mode', 'Plan', 'Todo', 'Write', 'Edit',
  'Read', 'Delete', 'Move', 'List', 'Glob', 'Grep', 'Delegate', 'Comms', 'Search', 'Fetch', 'Sub-task',
  'Arxiv', 'Wikipedia', 'Cryptex', 'Arousal', 'Engagement', 'Default', 'LIVE', 'Query:', 'OK', 'Momentum',
  'Reach Out', 'Drive', 'Finding', 'Daydream', 'Active Dream', 'file', 'Git', 'Terminal', 'BYOK', 'Stripe',
  'Link', 'OAuth', 'NestJS', 'Resend', 'Discord', 'Slack', 'Telegram', 'WhatsApp', 'Ollama', 'vLLM',
  'MCP', 'ClawHub', 'VRAM', 'SSH', 'LAN', 'BPM', 'ANS', 'ECN', 'SN', 'DMN', 'GX10', 'Moondream', 'Whisper',
  'Python', 'Windows', 'PATH', 'Tools → Integrations', 'Settings → Integrations', 'Tools → Google Workspace',
]);

const EN_TO_ES = {
  'Monthly pay-as-you-go spend cap (USD)': 'Límite mensual de gasto pay-as-you-go (USD)',
  'Optional hard cap on pay-as-you-go spend.': 'Límite opcional en el gasto pay-as-you-go.',
  'Loading subscription…': 'Cargando suscripción…',
  'You have complimentary Babo Cloud access': 'Tienes acceso gratuito a Babo Cloud',
  'including Babo Brain (GX10).': 'incluye Babo Brain (GX10).',
  'No subscription required': 'No se requiere suscripción',
  'Hosted models require an active subscription. Your API key on this device (BYOK) still works without a sub.':
    'Los modelos alojados requieren una suscripción activa. Tu clave API en este dispositivo (BYOK) sigue funcionando sin suscripción.',
  'Subscribe — {{price}}': 'Suscribirse — {{price}}',
  'Renews {{date}}': 'Se renueva el {{date}}',
  '{{used}} used of {{total}} · {{remaining}}% remaining':
    '{{used}} usados de {{total}} · {{remaining}}% restante',
  'Default $15/mo cap on pay-as-you-go model usage.':
    'Límite predeterminado de $15/mes en uso pay-as-you-go de modelos.',
  '{{price}} — billed via Stripe': '{{price}} — facturado vía Stripe',
  'WhatsApp, Telegram, Gmail relay & Google integrations':
    'Relay de WhatsApp, Telegram, Gmail e integraciones de Google',
  'Models via your API key (BYOK) or pay-as-you-go at upstream cost':
    'Modelos con tu clave API (BYOK) o pay-as-you-go al costo del proveedor',
  'No Babo markup on token usage': 'Sin margen de Babo en el uso de tokens',
  'Save cap': 'Guardar límite',
  'Platform credentials and channel prerequisites.':
    'Credenciales de plataforma y requisitos previos de canales.',
  'Telegram & WhatsApp': 'Telegram y WhatsApp',
  'Resend API key': 'Clave API de Resend',
  'Inbound domain': 'Dominio entrante',
  'Save Resend credentials': 'Guardar credenciales de Resend',
  'Remove saved credentials': 'Eliminar credenciales guardadas',
  'Localhost NestJS': 'NestJS en localhost',
  'Check for updates': 'Buscar actualizaciones',
  'Version': 'Versión',
  'Reset first-run tour': 'Restablecer tour inicial',
  'Onboarding tutorials': 'Tutoriales de onboarding',
  'Re-show introductory tips on Chat, Tools, Memory, and other pages.':
    'Vuelve a mostrar consejos introductorios en Chat, Herramientas, Memoria y otras páginas.',
  'Agent integrations': 'Integraciones de agentes',
  'Manage API keys': 'Administrar claves API',
  'Keys for programmatic access to your Babo cloud account.':
    'Claves para acceso programático a tu cuenta en la nube de Babo.',
  'Payment issue': 'Problema de pago',
  'Canceled': 'Cancelado',
  'Not subscribed': 'Sin suscripción',
  'Connected ({{code}})': 'Conectado ({{code}})',
  'Could not reach server': 'No se pudo alcanzar el servidor',
  'Neural State': 'Estado neuronal',
  'Memory & State': 'Memoria y estado',
  'Hormones': 'Hormonas',
  'Activity Feed': 'Feed de actividad',
  'Working Memory': 'Memoria de trabajo',
  'Network Dynamics': 'Dinámica de red',
  'Match your device language': 'Seguir el idioma del dispositivo',
  'Agents': 'Agentes',
  'Projects': 'Proyectos',
  'Tools': 'Herramientas',
  'Brain': 'Cerebro',
  'Settings': 'Configuración',
  'Sign out': 'Cerrar sesión',
  'Get help on Discord': 'Obtener ayuda en Discord',
  'Cycle theme: light, dark, or system': 'Alternar tema: claro, oscuro o sistema',
  'Early access': 'Acceso anticipado',
  'App status': 'Estado de la app',
  'Stopping...': 'Deteniendo…',
  'Step {{current}}/{{max}}': 'Paso {{current}}/{{max}}',
  '{{count}} steps': '{{count}} pasos',
  'Workbench — tools & steps': 'Workbench — herramientas y pasos',
  'Inbox — channel messages': 'Inbox — mensajes de canal',
  'Live — brain signals & activity': 'Live — señales cerebrales y actividad',
  'Focus mode': 'Modo enfoque',
  'Task was stopped — open workbench': 'Tarea detenida — abrir workbench',
  'Task completed — open workbench': 'Tarea completada — abrir workbench',
  'Conversations': 'Conversaciones',
  'New branch': 'Nueva rama',
  'Thread options': 'Opciones de hilo',
  'Set as Home': 'Establecer como Inicio',
  'Reset Home…': 'Restablecer Inicio…',
  'Reset Home': 'Restablecer Inicio',
  'Rename': 'Renombrar',
  'Delete': 'Eliminar',
  'Delete branch': 'Eliminar rama',
  'Collapse sidebar': 'Contraer barra lateral',
  'Expand sidebar': 'Expandir barra lateral',
  'Drop files or folders here': 'Suelta archivos o carpetas aquí',
  'Transcribing...': 'Transcribiendo…',
  'Stop & Transcribe': 'Detener y transcribir',
  'Attach files (Shift+click for folder)': 'Adjuntar archivos (Mayús+clic para carpeta)',
  'Send': 'Enviar',
  'Send guidance': 'Enviar guía',
  'Start a conversation': 'Inicia una conversación',
  'New Skill': 'Nueva skill',
  'Send Feedback': 'Enviar feedback',
  'Sending...': 'Enviando…',
  'Feedback on agent reply': 'Feedback sobre la respuesta del agente',
  'This thread': 'Este hilo',
  'All channels': 'Todos los canales',
  'Searching': 'Buscando',
  'Result preview': 'Vista previa del resultado',
  'Success': 'Éxito',
  'Failed': 'Fallido',
  'Read file': 'Leer archivo',
  'Writing…': 'Escribiendo…',
  'Written': 'Escrito',
  'Reading…': 'Leyendo…',
  'Edited': 'Editado',
  'Shell output': 'Salida de shell',
  'Planning next step…': 'Planificando siguiente paso…',
  'Thinking…': 'Pensando…',
  'Open workbench': 'Abrir workbench',
  'Reasoning…': 'Razonando…',
  'Reasoned': 'Razonado',
  'Thought': 'Pensamiento',
  'Loading...': 'Cargando…',
  'Download': 'Descargar',
  'Approve & Restart': 'Aprobar y reiniciar',
  'Reject': 'Rechazar',
  'Approved': 'Aprobado',
  'Rejected': 'Rechazado',
  'Agent workbench': 'Workbench del agente',
  'All branches': 'Todas las ramas',
  'This branch': 'Esta rama',
  'Parallel': 'Paralelo',
  'Full output': 'Salida completa',
  'Warning': 'Advertencia',
  'Focused': 'Enfocado',
  'Background task': 'Tarea en segundo plano',
  'Working…': 'Trabajando…',
  'Step': 'Paso',
  'Wave': 'Ola',
  'Comms': 'Comunicaciones',
  'Switch mode': 'Cambiar modo',
  'From': 'De',
  'To': 'A',
  'Error': 'Error',
  'Activity': 'Actividad',
  'Event': 'Evento',
  'All caught up.': 'Todo al día.',
  'Needs you': 'Te necesita',
  'Unread': 'No leídos',
  'Skipped / blocked': 'Omitidos / bloqueados',
  'Recent': 'Recientes',
  'Skipped': 'Omitido',
  'Orchestration': 'Orquestación',
  'User Model': 'Modelo de usuario',
  'Facts': 'Hechos',
  'Sleep Cycles': 'Ciclos de sueño',
  'ANS State': 'Estado ANS',
  'Coherence': 'Coherencia',
  'Style': 'Estilo',
  'Temperature': 'Temperatura',
  'Interests': 'Intereses',
  'Patience': 'Paciencia',
  'Executive': 'Ejecutivo',
  'Salience': 'Prominencia',
  'Arc': 'Arco',
  'Turns': 'Turnos',
  'Resonance': 'Resonancia',
  'Relevance': 'Relevancia',
  'Starting task...': 'Iniciando tarea…',
  'Responded': 'Respondido',
  'Search models…': 'Buscar modelos…',
  'Type': 'Tipo',
  'Shared channel / group': 'Canal / grupo compartido',
  'Direct message': 'Mensaje directo',
  'Session': 'Sesión',
  'Subject': 'Asunto',
  'Last speaker': 'Último interlocutor',
  'Close browser panel': 'Cerrar panel del navegador',
  'Create Agent': 'Crear agente',
  'Total': 'Total',
  'Paused': 'En pausa',
  'Sleeping': 'Durmiendo',
  'No agents yet': 'Aún no hay agentes',
  'Pause': 'Pausar',
  'Resume': 'Reanudar',
  'Job': 'Rol',
  'Trust': 'Confianza',
  'Open chat': 'Abrir chat',
  'Working': 'Trabajando',
  'Daydreaming': 'Soñando despierto',
  'Unreachable': 'Inalcanzable',
  'Desktop Offline': 'Desktop sin conexión',
  'Squads': 'Squads',
  'Create squad': 'Crear squad',
  'Name': 'Nombre',
  'Members': 'Miembros',
  'Create': 'Crear',
  'Approve': 'Aprobar',
  'Deny': 'Denegar',
  'Add member': 'Agregar miembro',
  'Add': 'Agregar',
  'Remove member': 'Quitar miembro',
  'Overview': 'Resumen',
  'Knowledge': 'Conocimiento',
  'Chain': 'Cadena',
  'Episodes': 'Episodios',
  'Edit': 'Editar',
  'Instructions': 'Instrucciones',
  'Goals': 'Objetivos',
  'Import': 'Importar',
  'Fork': 'Duplicar',
  'Restore': 'Restaurar',
  'Agent': 'Agente',
  'Sleep': 'Dormir',
  'Network': 'Red',
  'Narrative': 'Narrativa',
  'Visual Cortex': 'Visual Cortex',
  'Signals': 'Señales',
  'Events': 'Eventos',
  'Schedule': 'Horario',
  'Status': 'Estado',
  'Energy': 'Energía',
  'Mood': 'Ánimo',
  'Saved': 'Guardado',
  'Bedtime': 'Hora de dormir',
  'Wake Time': 'Hora de despertar',
  'Timezone': 'Zona horaria',
  'Yes': 'Sí',
  'Task Board': 'Tablero de tareas',
  'New list': 'Nueva lista',
  'Queued': 'En cola',
  'In Progress': 'En progreso',
  'Blocked': 'Bloqueadas',
  'Deferred': 'Pospuestas',
  'Expand': 'Expandir',
  'Collapse': 'Contraer',
  'Empty': 'Vacío',
  'Low': 'Baja',
  'Normal': 'Normal',
  'High': 'Alta',
  'Urgent': 'Urgente',
  'Choose a Mind': 'Elige una mente',
  'Model': 'Modelo',
  'Skip': 'Omitir',
  'Scientist': 'Científico',
  'Engineer': 'Ingeniero',
  'Creative': 'Creativo',
  'Coordinator': 'Coordinador',
  'Analyst': 'Analista',
  'Diplomat': 'Diplomático',
  'Strategist': 'Estratega',
  'Tabula Rasa': 'Tabula Rasa',
  'Math': 'Matemáticas',
  'Physics': 'Física',
  'Chemistry': 'Química',
  'Biology': 'Biología',
  'Technology': 'Tecnología',
  'Philosophy': 'Filosofía',
  'History': 'Historia',
  'Arts': 'Artes',
  'Linguistics': 'Lingüística',
  'Science': 'Ciencia',
  'Humanities': 'Humanidades',
  'Research': 'Investigación',
  'Analysis': 'Análisis',
  'Logic': 'Lógica',
  'Systems': 'Sistemas',
  'Architecture': 'Arquitectura',
  'Debugging': 'Depuración',
  'Aesthetics': 'Estética',
  'Delegation': 'Delegación',
  'Empathy': 'Empatía',
  'Persuasion': 'Persuasión',
  'Save': 'Guardar',
  'Close': 'Cerrar',
  'Save Configuration': 'Guardar configuración',
  'Save Schedule': 'Guardar horario',
  'Already have an account?': '¿Ya tienes una cuenta?',
  'No account yet?': '¿Aún no tienes cuenta?',
  'Create one': 'Crea una',
  'Checking server…': 'Comprobando servidor…',
  'Server unreachable': 'Servidor inalcanzable',
  'Server reachable': 'Servidor accesible',
  'Login failed': 'Error al iniciar sesión',
  'Enter password': 'Ingresa la contraseña',
  'Generate': 'Generar',
  'Revoke': 'Revocar',
  'Requests': 'Solicitudes',
  'Actions': 'Acciones',
  'Copied!': '¡Copiado!',
  'Title': 'Título',
  'Mission': 'Misión',
  'Install': 'Instalar',
  'Connect': 'Conectar',
  'Disconnect': 'Desconectar',
  'Connected': 'Conectado',
  'Enable': 'Habilitar',
  'Disable': 'Deshabilitar',
  'Configuration': 'Configuración',
  'Files': 'Archivos',
  'Popular': 'Populares',
  'Newest': 'Más recientes',
  'Dismiss': 'Cerrar',
};

function polishSpanish(text) {
  if (typeof text !== 'string' || !text) return text;
  let t = text;

  // Exact-word fixes
  if (t === 'Ahorrar') return 'Guardar';
  if (t === 'Cerca') return 'Cerrar';

  // computadora → ordenador
  t = t.replace(/\bEsta computadora\b/g, 'Este ordenador');
  t = t.replace(/\besta computadora\b/g, 'este ordenador');
  t = t.replace(/\bSu computadora\b/g, 'Tu ordenador');
  t = t.replace(/\bsu computadora\b/g, 'tu ordenador');
  t = t.replace(/\bComputadora\b/g, 'Ordenador');
  t = t.replace(/\bcomputadora\b/g, 'ordenador');

  // User-facing possessives (formal → tú)
  const poss = [
    [/\bSu agente\b/g, 'Tu agente'],
    [/\bsu agente\b/g, 'tu agente'],
    [/\bSus agentes\b/g, 'Tus agentes'],
    [/\bsus agentes\b/g, 'tus agentes'],
    [/\bSu servidor\b/g, 'Tu servidor'],
    [/\bsu servidor\b/g, 'tu servidor'],
    [/\bSu cuenta\b/g, 'Tu cuenta'],
    [/\bsu cuenta\b/g, 'tu cuenta'],
    [/\bSu clave\b/g, 'Tu clave'],
    [/\bsu clave\b/g, 'tu clave'],
    [/\bSu red\b/g, 'Tu red'],
    [/\bsu red\b/g, 'tu red'],
    [/\bSu escritorio\b/g, 'Tu escritorio'],
    [/\bsu escritorio\b/g, 'tu escritorio'],
    [/\bSu dispositivo\b/g, 'Tu dispositivo'],
    [/\bsu dispositivo\b/g, 'tu dispositivo'],
    [/\bSu navegador\b/g, 'Tu navegador'],
    [/\bsu navegador\b/g, 'tu navegador'],
    [/\bSu propia\b/g, 'Tu propia'],
    [/\bsu propia\b/g, 'tu propia'],
    [/\bSu propio\b/g, 'Tu propio'],
    [/\bsu propio\b/g, 'tu propio'],
    [/\bSus propias\b/g, 'Tus propias'],
    [/\bsus propias\b/g, 'tus propias'],
    [/\bSus propios\b/g, 'Tus propios'],
    [/\bsus propios\b/g, 'tus propios'],
    [/\bSus claves\b/g, 'Tus claves'],
    [/\bsus claves\b/g, 'tus claves'],
    [/\bSus dispositivos\b/g, 'Tus dispositivos'],
    [/\bsus dispositivos\b/g, 'tus dispositivos'],
    [/\bSu casa\b/g, 'Tu Inicio'],
    [/\bsu casa\b/g, 'tu Inicio'],
    [/\bSu suscripción\b/g, 'Tu suscripción'],
    [/\bsu suscripción\b/g, 'tu suscripción'],
    [/\bSu implementación\b/g, 'Tu implementación'],
    [/\bsu implementación\b/g, 'tu implementación'],
    [/\bSu token\b/g, 'Tu token'],
    [/\bsu token\b/g, 'tu token'],
    [/\bSu modelo\b/g, 'Tu modelo'],
    [/\bsu modelo\b/g, 'tu modelo'],
    [/\bSu selección\b/g, 'Tu selección'],
    [/\bsu selección\b/g, 'tu selección'],
    [/\bSu primer\b/g, 'Tu primer'],
    [/\bsu primer\b/g, 'tu primer'],
    [/\bSu pago\b/g, 'Tu pago'],
    [/\bsu pago\b/g, 'tu pago'],
    [/\bSu correo\b/g, 'Tu correo'],
    [/\bsu correo\b/g, 'tu correo'],
    [/\bSu contraseña\b/g, 'Tu contraseña'],
    [/\bsu contraseña\b/g, 'tu contraseña'],
    [/\bSu API\b/g, 'Tu API'],
    [/\bsu API\b/g, 'tu API'],
    [/\bSu proveedor\b/g, 'Tu proveedor'],
    [/\bsu proveedor\b/g, 'tu proveedor'],
    [/\bSu webhook\b/g, 'Tu webhook'],
    [/\bsu webhook\b/g, 'tu webhook'],
    [/\bSu RUTA\b/g, 'tu PATH'],
    [/\bsu RUTA\b/g, 'tu PATH'],
  ];
  for (const [re, rep] of poss) t = t.replace(re, rep);

  // usted → tú
  t = t.replace(/\bcon usted\b/gi, 'contigo');
  t = t.replace(/\bpara usted\b/gi, 'para ti');
  t = t.replace(/\ba usted\b/gi, 'a ti');
  t = t.replace(/\bde usted\b/gi, 'tuyo');
  t = t.replace(/\ben el que usted escribe\b/g, 'en el que escribes');
  t = t.replace(/\busted escribe\b/g, 'escribes');
  t = t.replace(/\busted mismo\b/g, 'tú mismo');
  t = t.replace(/\bSe le avisará\b/g, 'Te volveremos a preguntar');

  // Formal imperatives → informal
  const imperatives = [
    [/\bUtilice\b/g, 'Usa'],
    [/\butilice\b/g, 'usa'],
    [/\bInstale\b/g, 'Instala'],
    [/\bTermine\b/g, 'Termina'],
    [/\bConfigure\b/g, 'Configura'],
    [/\bComplete\b/g, 'Completa'],
    [/\bHaga clic\b/g, 'Haz clic'],
    [/\bCree\b/g, 'Crea'],
    [/\bMantenga\b/g, 'Mantén'],
    [/\bConecte\b/g, 'Conecta'],
    [/\bElija\b/g, 'Elige'],
    [/\bDestaque\b/g, 'Destaca'],
    [/\bSuscríbase\b/g, 'Suscríbete'],
    [/\bInicie sesión\b/g, 'Inicia sesión'],
    [/\bIntroduzca\b/g, 'Introduce'],
    [/\bIngrese\b/g, 'Ingresa'],
    [/\bEspere\b/g, 'Espera'],
    [/\bVuelva\b/g, 'Vuelve'],
    [/\bCambie\b/g, 'Cambia'],
    [/\bSiga\b/g, 'Sigue'],
    [/\bToque\b/g, 'Toca'],
    [/\bAbra\b/g, 'Abre'],
    [/\bEscriba\b/g, 'Escribe'],
    [/\bResponda\b/g, 'Responde'],
    [/\bEstablezca\b/g, 'Establece'],
    [/\bRestablezca\b/g, 'Restablece'],
    [/\bGuarde\b/g, 'Guarda'],
    [/\bguarde\b/g, 'guarda'],
    [/\bLlame\b/g, 'Ponle nombre a'],
    [/\bpídale\b/g, 'pídele'],
    [/\bcontactarlo\b/g, 'contactarte'],
    [/\bImplemente\b/g, 'Implementa'],
    [/\bDescargue\b/g, 'Descarga'],
    [/\bverifique\b/g, 'verifica'],
    [/\bobtenga\b/g, 'obtén'],
    [/\bcomuníquese\b/g, 'contacta'],
    [/\bOtorgue\b/g, 'Concede'],
    [/\benvíe\b/g, 'envía'],
    [/\bsiga\b/g, 'sigue'],
    [/\bPegue\b/g, 'Pega'],
    [/\bAsegúrese\b/g, 'Asegúrate'],
    [/\bHabilite\b/g, 'Habilita'],
    [/\bestablezca\b/g, 'establece'],
    [/\bCopie\b/g, 'Copia'],
    [/\bcopie\b/g, 'copia'],
    [/\bescanee\b/g, 'escanea'],
    [/\bpresione\b/g, 'presiona'],
    [/\bSeñale\b/g, 'Apunta'],
    [/\bsus credenciales\b/g, 'tus credenciales'],
    [/\bse le solicite\b/g, 'te lo pidan'],
    [/\bferrocarril\b/g, 'Railway'],
    [/\blaboratorio doméstico\b/g, 'homelab'],
    [/\breenvíe\b/g, 'Resend'],
    [/\b¡Salvado!\b/g, '¡Guardado!'],
    [/\b¡Fijado!\b/g, '¡Corregido!'],
    [/\bHaga una\b/g, 'Haz una'],
    [/\belegirá\b/g, 'elegirás'],
    [/\biniciará\b/g, 'iniciarás'],
    [/\bConfigurará\b/g, 'Configurarás'],
    [/\bpreferencia\b/g, 'preferencia'],
    [/\bpre prefiera\b/g, 'prefieres'],
    [/\bSi prefiere\b/g, 'Si prefieres'],
    [/\belija\b/g, 'elige'],
    [/\bespere\b/g, 'espera'],
  ];
  for (const [re, rep] of imperatives) t = t.replace(re, rep);

  // MT / literal fixes
  const fixes = [
    ['De cheques…', 'Comprobando…'],
    ['De cheques', 'Comprobando'],
    ['Correr', 'En ejecución'],
    ['Extraviado', 'No encontrado'],
    ['Exportador…', 'Exportando…'],
    ['Refrescante…', 'Actualizando…'],
    ['Rebautizar', 'Renombrar'],
    ['Telegrama y WhatsApp', 'Telegram y WhatsApp'],
    ['Telegrama —', 'Telegram —'],
    ['Reenviar', 'Resend'],
    ['reenviar', 'Resend'],
    ['Reenvío', 'Resend'],
    ['banco de trabajo', 'workbench'],
    ['Banco de trabajo', 'Workbench'],
    ['Laboral…', 'Trabajando…'],
    ['laboral…', 'trabajando…'],
    ['laboral...', 'trabajando...'],
    ['Laboral', 'Trabajando'],
    ['laboral', 'trabajando'],
    ['Desvelarse', 'Quédate despierto'],
    ['gerente', 'gestionando'],
    ['tratamiento', 'procesando'],
    ['Hojeada', 'Navegación web'],
    ['ollama save', 'ollama serve'],
    ['Tu RUTA', 'tu PATH'],
    ['tu RUTA', 'tu PATH'],
    ['Comprometer', 'Engagement'],
    ['Adherirse', 'Coherencia'],
    ['Llegar', 'Reach Out'],
    ['Conducir', 'Drive'],
    ['Encontrar', 'Finding'],
    ['Soñar despierto', 'Daydream'],
    ['Sueño Activo', 'Active Dream'],
    ['Nueva sucursal', 'Nueva rama'],
    ['sucursal', 'rama'],
    ['Sucursal', 'Rama'],
    ['Establecer como hogar', 'Establecer como Inicio'],
    ['iniciar una conversación', 'Inicia una conversación'],
    ['Parada...', 'Deteniendo…'],
    ['Envío...', 'Enviando…'],
    ['Aprobatorio...', 'Aprobando…'],
    ['Todos atrapados.', 'Todo al día.'],
    ['vencer a #', 'latido #'],
    ['Prominencia', 'Salience'],
    ['SN - Prominencia', 'SN – Salience'],
    ['hormonas', 'Hormonas'],
    ['Extendiendo la mano', 'Reach Out'],
    ['extendiendo la mano', 'Reach Out'],
    ['departamentos:', 'deps:'],
    ['llegar -', 'reach out —'],
    [' conducir', ' drive'],
    ['Problema de pago', 'Problema de pago'],
    ['Cancelado', 'Cancelado'],
    ['Facturado a través de', 'Facturado vía'],
    ['margen de beneficio', 'margen'],
    ['costo ascendente', 'costo del proveedor'],
    ['subscripción', 'suscripción'],
    ['Listo.', 'Listo.'],
    ['estas listo', 'Estás listo'],
    ['desconectar', 'Cerrar sesión'],
    ['Mi servidor LAN', 'Tu servidor LAN'],
    ['mi servidor LAN', 'tu servidor LAN'],
    ['Su servidor LAN', 'Tu servidor LAN'],
    ['su servidor LAN', 'tu servidor LAN'],
  ];
  for (const [from, to] of fixes) {
    if (t.includes(from)) t = t.split(from).join(to);
  }

  // Resend brand (avoid "Reenviar" as verb for Resend product)
  t = t.replace(/Guardar Reenviar credenciales/g, 'Guardar credenciales de Resend');
  t = t.replace(/Correo electrónico \(Reenviar\)/g, 'Email (Resend)');
  t = t.replace(/Eliminar las credenciales guardadas para reenviar/g, '¿Eliminar las credenciales guardadas de Resend?');
  t = t.replace(/Configure Reenviar antes/g, 'Configura Resend antes');
  t = t.replace(/Dominio verificado en Reenvío/g, 'Dominio verificado en Resend');
  t = t.replace(/cuenta de Reenvío/g, 'cuenta de Resend');
  t = t.replace(/credenciales de Reenvío/g, 'credenciales de Resend');
  t = t.replace(/Reenviar configurado/g, 'Resend configurado');
  t = t.replace(/Reenviar la URL/g, 'URL del webhook entrante de Resend');
  t = t.replace(/Reenviar credenciales/g, 'credenciales de Resend');

  return t;
}

let changed = 0;
const changedPaths = [];

function walk(obj, pathParts = []) {
  for (const key of Object.keys(obj)) {
    const p = [...pathParts, key];
    const pathStr = p.join('.');
    if (typeof obj[key] === 'object' && obj[key] !== null && !Array.isArray(obj[key])) {
      walk(obj[key], p);
    } else {
      const enVal = p.reduce((o, k) => o?.[k], en);
      const itVal = p.reduce((o, k) => o?.[k], it);
      const oldVal = obj[key];
      let newVal = oldVal;

      if (PATH_OVERRIDES[pathStr] !== undefined) {
        newVal = PATH_OVERRIDES[pathStr];
      } else if (typeof oldVal === 'string') {
        newVal = polishSpanish(oldVal);
        if (oldVal === enVal && itVal !== enVal) {
          if (EN_TO_ES[enVal]) {
            newVal = EN_TO_ES[enVal];
          } else if (!EN_KEEP.has(enVal)) {
            // leave polished MT unless we have explicit mapping
          }
        }
        if (EN_TO_ES[oldVal] && oldVal === enVal) {
          newVal = EN_TO_ES[enVal];
        }
      }

      if (newVal !== oldVal) {
        obj[key] = newVal;
        changed++;
        changedPaths.push(pathStr);
      }
    }
  }
}

walk(es);

const outPath = path.join(root, 'frontend/src/assets/i18n/es.json');
fs.writeFileSync(outPath, JSON.stringify(es, null, 2) + '\n');

console.log(`Changed ${changed} strings`);
if (changedPaths.length <= 80) {
  console.log(changedPaths.join('\n'));
} else {
  console.log('First 40:');
  console.log(changedPaths.slice(0, 40).join('\n'));
  console.log('...');
  console.log('Last 20:');
  console.log(changedPaths.slice(-20).join('\n'));
}
