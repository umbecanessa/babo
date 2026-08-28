#!/usr/bin/env node
/**
 * French i18n quality pass: informal tu tone, natural UI copy, fix mistranslations.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frPath = path.join(__dirname, '../frontend/src/assets/i18n/fr.json');
const fr = JSON.parse(fs.readFileSync(frPath, 'utf8'));

function getByPath(obj, dotPath) {
  return dotPath.split('.').reduce((o, k) => o?.[k], obj);
}

function setByPath(obj, dotPath, val) {
  const parts = dotPath.split('.');
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (!(parts[i] in cur)) cur[parts[i]] = {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = val;
}

/** Path-specific overrides (highest priority). */
const OVERRIDES = {
  // common
  'common.suggested': 'Recommandé',
  'common.easier_setup': 'Plus simple',
  'common.on': 'activé',

  // setup
  'setup.welcome.title': 'Bienvenue sur Babo',
  'setup.welcome.back': 'Bon retour',
  'setup.welcome.lead': 'Ton agent tourne sur ce {{device}}.',
  'setup.welcome.languageHint': 'Auto suit ton appareil. L\'agent répond quand même dans la langue dans laquelle tu écris.',
  'setup.welcome.accountHint': 'Installe d\'abord Babo sur cet ordinateur. Tu configureras la réflexion et les fonctions, puis tu te connecteras.',
  'setup.steps.thinking': 'Réflexion',
  'setup.steps.features': 'Fonctions',
  'setup.steps.placement': 'Compte',
  'setup.steps.signin': 'Connexion',
  'setup.visionPrefetch.fallback': 'Préparation de la perception d\'écran…',
  'setup.visionPrefetch.hint': 'Tu peux continuer — l\'installation se poursuit en arrière-plan.',
  'setup.prepare.title': 'Installation',
  'setup.prepare.lead': 'On installe ce dont Babo a besoin sur cet ordinateur.',
  'setup.device.nextHint': 'À l\'écran suivant, choisis <strong>Babo Cloud</strong> pour la voie la plus simple, ou une option locale avancée si tu préfères la confidentialité ou les modèles hors ligne.',
  'setup.device.taglineReady': 'Ton ordinateur a l\'air prêt. Ensuite, tu choisiras où tourne le chat — Babo Cloud est la voie la plus simple.',
  'setup.device.taglineCloud': 'Ton ordinateur a l\'air prêt. Babo Cloud est la voie la plus simple ; les modèles locaux restent une option avancée.',
  'setup.thinking.needScan': 'Termine d\'abord l\'étape appareil pour qu\'on puisse te recommander une config.',
  'setup.thinking.goScan': 'Aller à la détection de l\'appareil',
  'setup.thinking.title': 'Comment Babo doit-il réfléchir ?',
  'setup.thinking.lead': 'Où tourne le chat. La plupart commencent avec <strong>Babo Cloud</strong>.',
  'setup.thinking.lanFound': 'On a trouvé un serveur chat sur ton réseau — <strong>Mon serveur</strong> est suggéré.',
  'setup.thinking.cloudModel': 'Modèle chat (Babo Cloud)',
  'setup.thinking.cloudHint': 'Modèles et accès distant passent par <strong>api.babo.agency</strong>. Tes agents tournent toujours sur cet ordinateur — Babo Cloud synchronise ton compte et fait relais quand tu es absent.',
  'setup.thinking.useApiKey': 'Utiliser une clé API uniquement sur cet ordinateur',
  'setup.thinking.useCloud': 'Utiliser Babo Cloud à la place',
  'setup.thinking.apiKeyPlaceholder': 'Colle ta clé',
  'setup.thinking.ollamaUrl': 'URL Ollama',
  'setup.thinking.localHint': 'On vérifiera si ce PC peut exécuter un modèle local fiable. Installe Ollama, puis teste la connexion.',
  'setup.thinking.lanHint': 'Utilise ton serveur vLLM ou compatible OpenAI (port 8000), pas le 11434 d\'Ollama.',
  'setup.thinking.cards.cloud.subtitle': 'Voie la plus simple — modèles hébergés et sync du compte. L\'agent tourne quand même sur cet ordinateur.',
  'setup.thinking.cards.local.subtitle': 'Avancé — on vérifie si ce PC peut exécuter un modèle local fiable (Ollama). Idéal pour la confidentialité ou l\'usage hors ligne.',
  'setup.thinking.cards.lan.title': 'Mon serveur',
  'setup.thinking.cards.lan.subtitle': 'Avancé — serveur vLLM ou compatible OpenAI sur ton réseau local.',
  'setup.thinking.placeLan': 'Ton serveur LAN',
  'setup.thinking.placeLanSame': 'Même serveur LAN',
  'setup.thinking.placeLanShort': 'Serveur LAN',
  'setup.thinking.placePcMoondream': 'Ce PC (Moondream)',
  'setup.thinking.placePc': 'Ce PC',
  'setup.features.title': 'Fonctions optionnelles',
  'setup.features.lead': 'Tu peux les modifier quand tu veux dans Paramètres.',
  'setup.features.screen': 'Perception d\'écran',
  'setup.features.screenDesc': 'Vue en arrière-plan de ton bureau',
  'setup.features.code': 'Recherche de code',
  'setup.features.codeDesc': 'Trouver du code par le sens',
  'setup.features.saving': 'Enregistrement des préférences…',
  'setup.placement.title': 'Où ton compte se synchronise',
  'setup.placement.chatRuns': 'Le chat tourne sur <strong>{{location}}</strong> — séparé de ce serveur NestJS.',
  'setup.placement.nestjsUrl': 'URL serveur NestJS',
  'setup.placement.testOptional': 'Facultatif — vérifie que api.babo.agency est accessible',
  'setup.placement.venvWait': 'Termine l\'installation de Babo sur cet ordinateur (étape de préparation) avant de continuer.',
  'setup.placement.leadByok': 'Tes clés API peuvent passer par Babo Cloud (recommandé) ou un serveur NestJS que tu gères. Choisis où synchroniser ton compte Babo — les agents restent en local.',
  'setup.placement.leadDefault': 'Choisis où synchroniser ton compte Babo. Le chat tourne sur {{location}} ; les agents restent sur tes appareils.',
  'setup.placement.locCloud': 'Babo Cloud',
  'setup.placement.locByok': 'ton fournisseur API',
  'setup.placement.locLocal': 'cet ordinateur',
  'setup.placement.locLan': 'ton serveur LAN',
  'setup.placement.locOther': 'le fournisseur choisi',
  'setup.auth.signin': 'Connexion',
  'setup.auth.signup': 'Créer un compte',
  'setup.auth.change': 'Changer',
  'setup.auth.changeThinking': 'Changer la réflexion',
  'setup.auth.displayName': 'Nom affiché',
  'setup.auth.password': 'Mot de passe',
  'setup.auth.passwordSigninPh': 'Ton mot de passe',
  'setup.auth.signedIn': 'Connecté — continue pour terminer l\'installation.',
  'setup.auth.leadSignup': 'Crée ton compte sur {{server}}. Tu finiras l\'installation juste après.',
  'setup.auth.leadSignin': 'Connecte-toi sur {{server}} — requis pour les agents et la sync.',
  'setup.billing.lead': 'Une dernière étape avant ta première conversation : modèles hébergés, sync du compte et accès distant pendant que ton agent continue sur cet ordinateur.',
  'setup.billing.perk2': 'Modèles via ta clé API ou paiement à l\'usage en option (sans majoration)',
  'setup.billing.perk4': 'Remboursement sous 31 jours sur ton premier mois',
  'setup.billing.waitingPayment': 'Effectue le paiement dans ton navigateur — on le détectera automatiquement.',
  'setup.billing.allSet': 'Tout est prêt — créons ton agent.',
  'setup.billing.altHint': 'Tu utilises plutôt tes propres clés API ?',
  'setup.ready.yourApiKey': 'Ta clé API',
  'setup.ready.account': 'Compte',
  'setup.name.title': 'Nomme ton agent',
  'setup.name.lead': 'C\'est ainsi que Babo apparaîtra dans ton espace de travail.',
  'setup.lan.lead': 'vLLM, Whisper ou vision sur ton LAN. Ajoute SSH pour voir quels modèles conviennent au GPU de cette machine.',
  'setup.runtime.creatingAgent': 'Création de ton agent…',
  'setup.errors.subscribeBeforeAgent': 'Abonne-toi à Babo Cloud avant de créer ton agent.',
  'setup.errors.chooseBackend': 'Choisis où ton compte se synchronise ou entre une URL de serveur valide.',
  'setup.errors.installBeforeSignIn': 'Termine l\'installation de Babo sur cet ordinateur avant de te connecter.',
  'setup.errors.enterCredentials': 'Entre ton email et ton mot de passe, ou crée un compte.',
  'setup.test.signInAfterSetup': 'Connecte-toi après l\'installation pour vérifier Babo Cloud',
  'setup.test.subscribeNext': 'Abonne-toi à l\'étape suivante pour activer les modèles Babo Cloud',
  'setup.test.signInNext': 'Connecte-toi à l\'étape suivante',
  'setup.ollama.installBody': 'Télécharge et lance l\'installateur Windows. Ollama s\'ajoute à ton PATH.',
  'setup.lanGuide.sshBody': 'Utilise l\'hôte et l\'utilisateur SSH ci-dessus. Confirme que nvidia-smi affiche ton GPU.',
  'setup.lanGuide.title': '{{name}} sur ton serveur',
  'setup.lanGuide.stepConnect': '3. Connecte-toi depuis Babo',
  'setup.billingToast.subActive': 'Abonnement actif — tu es prêt.',
  'setup.billingToast.checkoutCanceled': 'Paiement annulé — tu pourras réessayer quand tu seras prêt.',
  'setup.thinking.thinking': 'Réflexion',
  'setup.experience.thinking': 'Réflexion',
  'setup.lanLabels.vision': 'Vision',
  'setup.lanLabels.visionSubtitle': 'Perception d\'écran (Moondream / VLM)',

  // settings
  'settings.appearance.desc': 'Thème et langue de l\'interface Babo. Le thème Système suit ton OS.',
  'settings.appearance.languageHint': 'Langue de l\'interface uniquement. L\'agent répond dans la langue dans laquelle tu écris. Auto suit ton appareil.',
  'settings.account.serverUrl': 'URL de ton serveur',
  'settings.integrations.localhostTitle': 'NestJS localhost',
  'settings.general.version': 'Version',
  'settings.billing.subscribe': 'Abonne-toi pour utiliser les modèles Babo Cloud',
  'settings.billing.lifetimeHint': 'Tu bénéficies d\'un accès gratuit à Babo Cloud',
  'settings.billing.subscribeHint': 'Les modèles hébergés nécessitent un abonnement actif. Ta clé API sur cet appareil (BYOK) fonctionne toujours sans abonnement.',
  'settings.billing.subscribeBtn': 'S\'abonner — {{price}}',
  'settings.billing.checklistByok': 'Modèles via ta clé API (BYOK) ou paiement à l\'usage au coût amont',
  'settings.billing.refundNote': 'Remboursement sous 31 jours disponible sur ton premier paiement d\'abonnement jusqu\'au {{date}}. Les frais de modèle à l\'usage ne sont pas remboursables.',
  'settings.billing.paygHint': 'Facultatif pour les modèles routés par Babo quand tu n\'utilises pas BYOK. Facturé au coût amont sans majoration Babo (un plafond mensuel s\'applique).',
  'settings.integrations.telegramSelf': 'Telegram — ton jeton BotFather (libre-service)',
  'settings.integrations.emailHint': 'Le courrier entrant arrive sur ton webhook NestJS, puis est transmis à Babo Desktop. Configure Resend ici ou définis RESEND_API_KEY et RESEND_INBOUND_DOMAIN sur ton déploiement NestJS.',
  'settings.integrations.resendSaved': 'Tes identifiants Resend enregistrés',
  'settings.integrations.resendKey': 'Clé API Resend',
  'settings.integrations.saveResend': 'Enregistrer les identifiants Resend',
  'settings.integrations.emailActive': 'E-mail (Resend) actif',
  'settings.integrations.baboCloudHint': 'E-mail et Google Workspace utilisent les identifiants fournis par Babo sur {{url}}. Connecte les canaux par agent dans Outils.',
  'settings.integrations.resendOnServer': 'Resend configuré sur le serveur',
  'settings.integrations.webhookUrl': 'URL webhook entrant Resend :',
  'settings.integrations.inboundHint': 'Domaine vérifié dans Resend pour le routage entrant.',
  'settings.integrations.googleHint': 'Crée une app Google Cloud OAuth et enregistre client_id / client_secret dans Outils → Google Workspace de chaque agent, ou demande à l\'agent dans le chat de te guider.',
  'settings.integrations.messagingHint': 'Telegram utilise ton jeton BotFather et enregistre les webhooks sur ton serveur NestJS. WhatsApp s\'associe via QR sur Babo Desktop — pas de webhook NestJS pour WhatsApp entrant.',
  'settings.keys.desc': 'Clés pour l\'accès programmatique à ton compte cloud Babo.',
  'settings.confirm.resetPython': 'Réinitialiser l\'environnement Python ? Tu devras relancer l\'installateur.',
  'settings.confirm.resetPermissions': 'Effacer toutes les décisions d\'autorisation enregistrées ? Tu seras invité à nouveau si nécessaire.',

  // info
  'info.hormones.title': 'Hormones',
  'info.hormones.legend.dopamine': 'Dopamine',
  'info.hormones.legend.cortisol': 'Cortisol',
  'info.neural_state.p1': 'Ton agent a un <b>heartbeat</b> virtuel qui reflète son activité cognitive. Le BPM monte quand l\'agent réfléchit, converse ou explore, et baisse au repos ou au sommeil. L\'étiquette d\'état (<b>Active</b>, <b>Resting</b>, <b>Drowsy</b>, <b>Deep Sleep</b>) vient directement du BPM.',
  'info.neural_state.p2': '<b>Valence</b> est le ton émotionnel de l\'agent — négatif (rouge) à positif (vert). <b>Arousal</b> mesure l\'intensité d\'activation. <b>Engagement</b> suit à quel point l\'agent est absorbé par le sujet actuel. <b>Bonding</b> reflète le lien ressenti avec toi. <b>Coherence</b> indique l\'alignement de son raisonnement interne.',
  'info.neural_state.p3': 'Ce n\'est pas cosmétique : ça <b>module le comportement</b>. Un agent à faible valence et riche en cortisol réagit différemment d\'un agent détendu et riche en dopamine. Tu observes un esprit se réguler en temps réel.',
  'info.activity.p1': 'Ce flux montre ce que ton agent fait <b>de manière autonome</b> — les actions qu\'il entreprend de lui-même, sans qu\'on le lui demande. Chaque type apparaît avec un code couleur.',
  'info.activity.legend.reach_out_desc': 'Initiative sociale proactive — l\'agent décide de te contacter de lui-même',
  'info.activity.legend.finding_desc': 'Une découverte que l\'agent juge digne d\'être partagée avec toi',
  'info.working_memory.p1': 'Le contexte <b>actif</b> de l\'agent — à quoi il « pense » en ce moment. Ces éléments tournent dynamiquement selon la conversation. Tu peux <b>modifier</b> ou <b>supprimer</b> des éléments pour orienter le focus de l\'agent.',

  // locale / nav
  'locale.autoHint': 'Suit la langue de ton appareil',
  'nav.agents': 'Agents',

  // chat
  'chat.nav.conversations': 'Conversations',
  'chat.mobile.conversations': 'Conversations',
  'chat.nav.resetHomeMessage': 'Démarrer un nouveau fil Home ?\n\nUn nouveau branchement est créé et défini comme Home. Ton Home actuel reste dans la liste. La connaissance de l\'agent (faits, mémoire) ne change pas.',
  'chat.composer.askPlaceholder': 'Écris ta réponse…',
  'chat.composer.budgetPlaceholder': 'Réponds 10, 20, 40 ou stop…',
  'chat.composer.askBanner': 'Réponds ci-dessous — l\'agent attend ta réponse avant de continuer.',
  'chat.composer.discordChannel': 'Réponds dans {{channel}} sur Discord',
  'chat.composer.telegramGroup': 'Réponds dans {{label}} sur Telegram',
  'chat.composer.whatsappGroup': 'Réponds dans {{label}} sur WhatsApp',
  'chat.composer.surfaceReply': 'Réponds sur {{surface}}',
  'chat.message.feedbackBtnTitle': 'Envoie un retour sur cette réponse',
  'chat.tool.thinking': 'Réflexion…',
  'chat.tool.stillThinking': 'Encore en réflexion… ({{elapsed}})',
  'chat.tool.thought': 'Réflexion',
  'chat.tool.thoughtStep': 'Réflexion (étape {{step}})',
  'chat.tool.waitAnswer': 'En attente de ta réponse…',
  'chat.tool.waitDecision': 'En attente de ta décision…',
  'chat.workbench.entries.thinking': 'Réflexion…',
  'chat.workbench.entries.waitingAnswer': 'En attente de ta réponse',
  'chat.workbench.entries.waitingDecision': 'En attente de ta décision',
  'chat.workbench.entries.budgetReplyHint': 'Réponds 10, 20, 40 ou stop',
  'chat.workbench.emptyHint': 'Utilise Tâche chat pour ce fil, Arrière-plan pour le travail autonome.',
  'chat.status.waitAnswer': 'En attente de ta réponse…',
  'chat.status.waitDecision': 'En attente de ta décision…',
  'chat.prompt.askWaiting': 'L\'agent attend ta réponse',
  'chat.model.noMatch': 'Aucun modèle ne correspond à ta recherche',
  'chat.context.session': 'Session',
  'chat.context.reachHint': 'Mets ce fil en avant pour que Babo sache où te joindre pour les rapports et la progression quand aucun objectif explicite n\'est défini.',
  'chat.context.privateBody': 'Les messages ici restent sur ton bureau. Rien n\'est envoyé sur Discord, e-mail ou d\'autres surfaces connectées sauf si Babo utilise explicitement un outil de canal.',
  'chat.runtime.terminalChip': 'Terminal',
  'chat.runtime.bgTool.readingFile': 'Lecture du fichier…',
  'chat.runtime.stayAwakeAck': 'Tu as dit : « Reste éveillé ». L\'agent continue.',
  'chat.runtime.browserSignIn': 'Ouverture de ton navigateur pour te connecter — complète la connexion, puis clique sur « Terminé » dans la boîte de dialogue.',
  'chat.runtime.loopInterrupted': 'La tâche précédente a été interrompue à l\'étape {{step}}. Utilise Continuer pour reprendre.',
  'chat.tools.terminal': 'Terminal',
  'chat.tools.bash': 'Terminal',
  'chat.live.hormones': 'Hormones',
  'chat.live.orchestration': 'Orchestration',
  'chat.live.metric.valence': 'Valence',
  'chat.live.stat.style': 'Style',
  'chat.live.stat.patience': 'Patience',
  'chat.live.episodeArc': 'Arc',

  // dashboard
  'dashboard.title': 'Agents',
  'dashboard.subtitle': 'Ta flotte neuronale',
  'dashboard.total': 'Total',
  'dashboard.agent.pause': 'Pause',
  'dashboard.emptyHintBefore': 'Clique sur l\'orbe ou appuie sur',
  'dashboard.emptyHintAfter': 'pour commencer',
  'dashboard.runtime.stillLoading': 'Encore en chargement — un instant…',
  'dashboard.runtime.initPython': 'Initialisation de l\'environnement Python…',
  'dashboard.runtimeFailed': 'Le runtime agent n\'a pas pu démarrer. Vérifie ta connexion runtime.',
  'dashboard.squads.subtitle': 'Flottes persistantes avec un lead, inbox partagée et coordination entre membres',
  'dashboard.squads.create': 'Créer une squad',
  'dashboard.squads.cancel': 'Annuler',
  'dashboard.squads.loading': 'Chargement des squads…',
  'dashboard.squads.empty': 'Pas encore de squad. Regroupe des agents avec un lead pour une inbox et une coordination partagées.',
  'dashboard.squads.delete': 'Supprimer',
  'dashboard.squads.checkbackOff': 'Checkback programmé désactivé',
  'dashboard.squads.proposalsPending': '{{count}} proposition(s) en attente d\'approbation du lead',
  'dashboard.squads.approve': 'Approuver',
  'dashboard.squads.deny': 'Refuser',
  'dashboard.squads.addMember': 'Ajouter un membre',
  'dashboard.squads.chooseAgent': 'Choisis un agent…',
  'dashboard.squads.add': 'Ajouter',
  'dashboard.squads.pendingDeleteAgent': 'Supprimer l\'agent {{name}}',
  'dashboard.squads.pendingPatchTrust': 'Mettre à jour la confiance pour {{name}}',
  'dashboard.squads.pendingPatchJob': 'Mettre à jour le rôle pour {{name}}',
  'dashboard.squads.cannotRemoveOnly': 'Impossible de retirer le seul membre. Supprime plutôt la squad.',
  'dashboard.squads.removeTitle': 'Retirer de la squad ?',
  'dashboard.squads.removeLeadMessage': 'Retirer {{name}} de la squad ? Le rôle de lead passera à un autre membre.',
  'dashboard.squads.removeConfirm': 'Retirer le membre',
  'dashboard.squads.removeLeadConfirm': 'Transférer et retirer',
  'dashboard.squads.deleteTitle': 'Supprimer la squad ?',
  'dashboard.squads.deleteConfirm': 'Supprimer la squad',
  'dashboard.squads.deleteMessage': 'Par défaut, les agents restent dans ta flotte en indépendants — seuls l\'inbox et le contexte de coordination de la squad sont supprimés.',
  'dashboard.squads.deleteAgentsOption': 'Supprimer aussi les {{count}} agent(s) de cette squad (définitif)',
  'dashboard.squads.loadError': 'Impossible de charger les squads',
  'dashboard.squads.addFailed': 'Ajout du membre échoué',
  'dashboard.squads.boardLoadError': 'Impossible de charger le board',

  // toast
  'toast.settings.latestVersion': 'Tu es sur la dernière version',
  'toast.settings.debugBundleSaved': 'Bundle de debug enregistré. Tu peux le joindre quand tu contactes le support.',
  'toast.settings.resendSaved': 'Identifiants Resend enregistrés',
  'toast.settings.resendRemoved': 'Identifiants Resend supprimés',

  // tools
  'tools.sections.integrationsHint': 'Connexions de canaux pour ton agent',
  'tools.relay.checkingHint': 'Connexion à ton serveur NestJS pour vérifier si Babo Desktop est en ligne.',
  'tools.relay.offlineHint': 'Distinct de Babo Cloud en ligne — ton bureau doit garder un WebSocket ouvert vers {{url}} pour cet agent. Redémarre Babo Desktop si tu l\'as fermé.',
  'tools.selfHosted.hint': 'Telegram et e-mail nécessitent NestJS accessible en HTTPS et Babo Desktop en ligne (relay). Configure les identifiants dans Paramètres → Intégrations.',
  'tools.intro.discord': 'Configure Discord pour ton agent.',
  'tools.intro.slack': 'Configure Slack pour ton agent.',
  'tools.intro.telegram': 'Configure Telegram pour ton agent.',
  'tools.intro.whatsapp': 'Associe WhatsApp à ton agent.',
  'tools.intro.google': 'Connecte ton compte Google pour que ton agent gère Gmail, Calendar, Drive et Sheets.',
  'tools.intro.email': 'Active le canal e-mail.',
  'tools.channelScope.hint': 'Choisis les canaux où cet agent écoute et répond. La sync se met à jour depuis {{platform}}. Utilise Enregistrer la config ci-dessous pour garder ta sélection.',
  'tools.community.extension': 'Extension',
  'tools.skill.tabs.config': 'Configuration',
  'tools.setup.common.localNestWarn': 'NestJS est sur {{host}} — Resend et Telegram ne peuvent pas appeler localhost. Déploie NestJS sur une URL HTTPS publique (Railway, VPS, homelab) ou utilise un tunnel, définis PUBLIC_API_URL sur le serveur et pointe les webhooks vers cette origine publique.',
  'tools.setup.common.desktopRelay': 'Laisse Babo Desktop tourner pour que le relay NestJS transmette le courrier entrant à ton agent.',
  'tools.setup.email.deployPublic': 'Déploie NestJS avec une URL HTTPS publique (Railway, VPS, etc.) pour que Resend atteigne ton webhook.',
  'tools.setup.email.resendAccount': 'Crée un compte Resend, vérifie un domaine inbound et obtiens une clé API.',
  'tools.setup.email.resendWebhook': 'Pointe le webhook inbound Resend vers : {{url}}',
  'tools.setup.email.activate': 'Clique sur Activer l\'e-mail — Babo provisionne une adresse @inbox pour ton agent.',
  'tools.setup.email.blockedResend': 'Configure Resend avant d\'activer le canal e-mail.',
  'tools.setup.email.resendCredentialsSettings': 'ou enregistre tes identifiants Resend dans Paramètres → Intégrations.',
  'tools.setup.email.resendCredentialsServer': 'Définis RESEND_API_KEY + RESEND_INBOUND_DOMAIN sur ton serveur NestJS,',
  'tools.setup.google.byo1': 'Crée un projet Google Cloud et active les API Gmail, Calendar, Drive et Sheets.',
  'tools.setup.google.byo2': 'Crée des identifiants OAuth 2.0 (application de bureau ou Web).',
  'tools.setup.google.byo5': 'Tu peux aussi demander à l\'agent dans le chat de t\'aider à configurer Google Cloud.',
  'tools.setup.google.cloud1': 'Clique sur Connecter — Babo ouvre la connexion Google avec l\'app OAuth intégrée.',
  'tools.setup.telegram.step2': 'Copie le jeton du bot que BotFather te donne.',
  'tools.setup.telegram.step3Self': 'Assure-toi que ton serveur NestJS est public ({{url}}) et que Babo Desktop est en ligne (relay).',
  'tools.setup.telegram.step4': 'Colle le jeton du bot quand tu y es invité, puis termine l\'identité propriétaire et la politique DM dans la config.',
  'tools.setup.whatsapp.step1': 'WhatsApp utilise un pont Baileys local sur ton bureau — pas de webhook NestJS pour l\'entrant.',
  'tools.setup.whatsapp.step2': 'Clique sur Démarrer l\'appairage et scanne le QR avec WhatsApp → Appareils liés.',
  'tools.setup.whatsapp.step4': 'Laisse Babo Desktop tourner pendant que tu attends les messages WhatsApp de l\'agent.',
  'tools.setup.discord.step1': 'Crée un bot sur le portail développeur Discord et copie le jeton.',
  'tools.setup.discord.step2Babo': 'Configure dans le chat — Babo Cloud exécute la passerelle Discord sur NestJS et relaie les messages vers ton bureau.',
  'tools.setup.discord.step3': 'Invite le bot sur ton serveur et tes canaux Discord — le scope se synchronise automatiquement avec Babo.',
  'tools.setup.discord.step2Self': 'Assure-toi que NestJS est public ({{url}}) et que le relay Babo Desktop est en ligne.',
  'tools.setup.slack.step1': 'Crée une app Slack sur api.slack.com/apps avec les scopes bot (app_mentions:read, chat:write, im:history, channels:history).',
  'tools.setup.slack.step4': 'Copie le jeton bot (xoxb-…) et le signing secret dans Configurer dans le chat ou le formulaire ci-dessous.',
  'tools.setup.selfHosted.publicApiUrl': 'Définis PUBLIC_API_URL sur ton déploiement NestJS à l\'origine HTTPS publique pour que paramètres et outils affichent les bonnes URL de webhook.',
  'tools.setup.selfHosted.desktopRelay': 'Babo Desktop doit tourner — NestJS transmet les webhooks à ton agent local via le relay WebSocket.',
  'tools.setup.selfHosted.credentials': 'E-mail et Google utilisent les identifiants fournis par Babo sur Babo Cloud ; sur ton propre serveur, tu configures toi-même Resend et Google OAuth.',
  'tools.setup.selfHosted.httpsRequired': 'Ton serveur NestJS ({{url}}) doit être accessible en HTTPS pour les webhooks Telegram et e-mail.',
  'tools.channelScope.emptyHint': 'Clique sur Synchroniser les canaux — le bot doit être sur un serveur autorisé à lister les canaux.',

  // memory / brain
  'memory.wm.instructions': 'Instructions',
  'memory.wm.intentions': 'Intentions',
  'memory.wm.otherWm': 'WM {{label}}',
  'memory.overview.question': 'Question',
  'memory.overview.flips': 'Basculements',
  'brain.agentFallback': 'Agent',
  'brain.tabs.hormones': 'Hormones',
  'brain.stats.network': 'Réseau',
  'brain.schedule.desc': 'Configure quand cet agent dort et se réveille. Les changements s\'appliquent immédiatement.',
  'brain.schedule.tz.eastern': 'Est (US)',
  'brain.schedule.tz.central': 'Centre (US)',
  'brain.schedule.tz.mountain': 'Montagnes (US)',
  'brain.schedule.tz.centralEurope': 'Europe centrale',
  'brain.schedule.tz.rome': 'Rome',
  'brain.empty.events': 'Aucun événement',
  'brain.overview.hormones': 'Niveaux d\'hormones',
  'brain.overview.networkTimeline': 'Chronologie réseau',
  'brain.overview.signalsMeta': '{{total}} signaux, {{learnable}} apprenables',
  'brain.vc.allChannels': 'Tous les canaux',
  'brain.vc.channelAgent': 'Agent',
  'brain.vc.running': 'En cours',
  'brain.vc.stopped': 'Arrêté',
  'brain.vc.disabled': 'Désactivé',
  'brain.vc.agentActive': 'Agent actif',
  'brain.vc.model': 'Modèle : {{model}}',
  'brain.vc.disabledEmpty': 'Visual Cortex est désactivé dans la config de l\'agent.',
  'brain.vc.noEvents': 'Aucun événement visuel dans le buffer.',
  'brain.vc.noEventsChannel': 'Aucun événement visuel dans le buffer pour le canal « {{channel}} ».',
  'brain.vc.changed': 'Modifié :',
  'brain.vc.ocr': 'Texte OCR',
  'brain.selfState.title': 'Moi temporel',
  'brain.selfState.energy': 'Énergie',
  'brain.selfState.valence': 'Valence',
  'brain.selfState.engagement': 'Engagement',
  'brain.wm.intentionsStat': '{{count}} intentions',
  'brain.wm.intentions': 'Intentions',
  'brain.tom.activeUserModel': 'Modèle utilisateur actif',
  'brain.tom.style': 'Style :',
  'brain.tom.patience': 'Patience :',

  // tasks / creation / projects / auth / capabilities
  'tasks.priority.urgent': 'Urgent',
  'creation.subtitle': 'Chaque parcours façonne la façon de penser de ton agent',
  'creation.soulWishTitle': 'Nomme ton agent',
  'creation.strengths.Arts': 'Arts',
  'creation.strengths.Science': 'Sciences',
  'creation.strengths.Architecture': 'Architecture',
  'creation.strengths.Imagination': 'Imagination',
  'creation.strengths.Communication': 'Communication',
  'creation.strengths.Persuasion': 'Persuasion',
  'projects.teams.pause': 'Pause',
  'charter.mission': 'Mission',
  'auth.login.lead': 'Ton compte Babo sur le serveur que tu as configuré.',
  'auth.register.hasAccount': 'Tu as déjà un compte ?',
  'auth.register.lead': 'Inscris-toi sur ton serveur Babo — le même que celui utilisé par l\'app.',
  'auth.passwordLoginPlaceholder': 'Saisis ton mot de passe',
  'auth.password': 'Mot de passe',
  'apiKeys.subtitle': 'Accès programmatique à tes agents',
  'apiKeys.copyWarning': 'Copie cette clé maintenant. Tu ne pourras plus la revoir.',
  'capabilities.primaryDesc': 'Par défaut pour tes messages et la boucle agent principale.',
  'capabilities.cloudRoutedHint': 'Routé via ton compte Babo après connexion.',
  'capabilities.lanFitHint': 'Connecte-toi en SSH à ton homelab (ex. GX10) pour voir quels modèles conviennent à son GPU.',
  'capabilities.useOwnApiKey': 'Utilise plutôt ta propre clé API',
  'capabilities.apiKeyPlaceholder': 'Colle ta clé',
  'capabilities.localOllamaHint': 'Recherche Ollama sur cet ordinateur (port 11434). Utilise Test pour détecter ton modèle.',
  'capabilities.ambientDesc': 'Vue d\'arrière-plan optionnelle de ton bureau (petit modèle local).',
  'capabilities.codeSearchDesc': 'Recherche des fichiers par sens dans tes projets.',
  'capabilities.brain.cloud.subtitle': 'Voie la plus simple — modèles hébergés via ton compte Babo',
  'capabilities.brain.byok.title': 'Ta clé API',
  'capabilities.brain.lan.subtitle': 'Avancé — vLLM ou serveur compatible sur ton réseau local',
};

/** Global substring fixes applied to every string value. */
const GLOBAL_REPLACEMENTS = [
  [/\bRenvoyer\b/g, 'Resend'],
  [/\brenvoyer\b/g, 'resend'],
  [/\bchemin de fer\b/gi, 'Railway'],
  [/\bsans sous-marin\b/g, 'sans abonnement'],
  [/\bCaractéristiques\b/g, 'Fonctions'],
  [/\bContent de te revoir\b/g, 'Bon retour'],
  [/\bConscience des écrans\b/g, 'Perception d\'écran'],
  [/\bConsience des écrans\b/g, 'Perception d\'écran'],
  [/\bsensibilisation aux écrans\b/g, 'perception d\'écran'],
  [/\bserveur de discussion\b/g, 'serveur chat'],
  [/\bModèle de discussion\b/g, 'Modèle chat'],
  [/\bSynchronisation du compte\b/g, 'Compte'],
  [/\bfonctionnalités\b/g, 'fonctions'],
  [/\bMise en place\b/g, 'Installation'],
  [/\bAccédez à l'analyse des appareils\b/g, 'Aller à la détection de l\'appareil'],
  [/\bUtilisez plutôt\b/g, 'Utilise plutôt'],
  [/\bchoisissez\b/g, 'choisis'],
  [/\bChoisissez\b/g, 'Choisis'],
  [/\bConnectez-vous\b/g, 'Connecte-toi'],
  [/\bAbonnez-vous\b/g, 'Abonne-toi'],
  [/\bInscrivez-vous\b/g, 'Inscris-toi'],
  [/\bCréez\b/g, 'Crée'],
  [/\bTerminez\b/g, 'Termine'],
  [/\bInstallez\b/g, 'Installe'],
  [/\bEntrez\b/g, 'Entre'],
  [/\bEffectuez\b/g, 'Effectue'],
  [/\bRépondez\b/g, 'Réponds'],
  [/\bDonnez\b/g, 'Donne'],
  [/\bMettez\b/g, 'Mets'],
  [/\bCliquez\b/g, 'Clique'],
  [/\bNommez\b/g, 'Nomme'],
  [/\bUtilisez\b/g, 'Utilise'],
  [/\bCollez\b/g, 'Colle'],
  [/\bCopiez\b/g, 'Copie'],
  [/\bAssurez-vous\b/g, 'Assure-toi'],
  [/\bConfigurez\b/g, 'Configure'],
  [/\bInvitez\b/g, 'Invite'],
  [/\bSuivez\b/g, 'Suis'],
  [/\bLaissez\b/g, 'Laisse'],
  [/\bFaites correspondre\b/g, 'Utilise la langue de'],
  [/\bVotre agent\b/g, 'Ton agent'],
  [/\bvotre agent\b/g, 'ton agent'],
  [/\bVotre ordinateur\b/g, 'Ton ordinateur'],
  [/\bVotre compte\b/g, 'Ton compte'],
  [/\bvotre compte\b/g, 'ton compte'],
  [/\bVotre clé\b/g, 'Ta clé'],
  [/\bvotre clé\b/g, 'ta clé'],
  [/\bVotre mot de passe\b/g, 'Ton mot de passe'],
  [/\bVotre serveur\b/g, 'Ton serveur'],
  [/\bvotre serveur\b/g, 'ton serveur'],
  [/\bVotre réseau\b/g, 'Ton réseau'],
  [/\bvotre réseau\b/g, 'ton réseau'],
  [/\bVotre bureau\b/g, 'Ton bureau'],
  [/\bvotre bureau\b/g, 'ton bureau'],
  [/\bVotre navigateur\b/g, 'Ton navigateur'],
  [/\bvotre navigateur\b/g, 'ton navigateur'],
  [/\bVotre flotte\b/g, 'Ta flotte'],
  [/\bVotre espace de travail\b/g, 'Ton espace de travail'],
  [/\bVotre maison\b/g, 'Ta maison'],
  [/\bVos agents\b/g, 'Tes agents'],
  [/\bVos identifiants\b/g, 'Tes identifiants'],
  [/\bvos appareils\b/g, 'tes appareils'],
  [/\bvos propres clés\b/g, 'tes propres clés'],
  [/\bvos projets\b/g, 'tes projets'],
  [/\bvos chaînes\b/g, 'tes chaînes'],
  [/\bvotre appareil\b/g, 'ton appareil'],
  [/\bvotre système d'exploitation\b/g, 'ton OS'],
  [/\bvotre réponse\b/g, 'ta réponse'],
  [/\bVotre réponse\b/g, 'Ta réponse'],
  [/\bvotre décision\b/g, 'ta décision'],
  [/\bVotre décision\b/g, 'Ta décision'],
  [/\bvotre recherche\b/g, 'ta recherche'],
  [/\bvotre webhook\b/g, 'ton webhook'],
  [/\bvotre GPU\b/g, 'ton GPU'],
  [/\bvotre CHEMIN\b/g, 'ton PATH'],
  [/\bvotre premier\b/g, 'ton premier'],
  [/\bVous pouvez\b/g, 'Tu peux'],
  [/\bvous pouvez\b/g, 'tu peux'],
  [/\bVous configurerez\b/g, 'Tu configureras'],
  [/\bvous connecterez\b/g, 'tu te connecteras'],
  [/\bvous écrivez\b/g, 'tu écris'],
  [/\bvous préférez\b/g, 'tu préfères'],
  [/\bvous choisirez\b/g, 'tu choisiras'],
  [/\bvous êtes absent\b/g, 'tu es absent'],
  [/\bVous êtes prêt\b/g, 'Tu es prêt'],
  [/\bvous êtes prêt\b/g, 'tu es prêt'],
  [/\bvous êtes invité\b/g, 'tu es invité'],
  [/\bvous attendez\b/g, 'tu attends'],
  [/\bvous contactez\b/g, 'tu contactes'],
  [/\bVous avez dit\b/g, 'Tu as dit'],
  [/\bvous avez choisi\b/g, 'tu as choisi'],
  [/\bvous avez configuré\b/g, 'tu as configuré'],
  [/\bvous connectez\b/g, 'tu te connectes'],
  [/\bvous n'utilisez\b/g, 'tu n\'utilises'],
  [/\bvous configurez\b/g, 'tu configures'],
  [/\bVous bénéficiez\b/g, 'Tu bénéficies'],
  [/\bVous devrez\b/g, 'Tu devras'],
  [/\bVous serez\b/g, 'Tu seras'],
  [/\bvous pourrez\b/g, 'tu pourras'],
  [/\bVous regardez\b/g, 'Tu regardes'],
  [/\bVous terminerez\b/g, 'Tu finiras'],
  [/\bVous utilisez\b/g, 'Tu utilises'],
  [/\bVous avez déjà\b/g, 'Tu as déjà'],
  [/\bVous ne pourrez\b/g, 'Tu ne pourras'],
  [/\bveuillez le compléter\b/g, 'complète la connexion'],
  [/\baccrochez-vous bien\b/gi, 'un instant'],
  [/\bPensée\b/g, 'Réflexion'],
  [/\bpensée\b/g, 'réflexion'],
  [/\b<\/b>edit<\/b>/g, '</b>modifier</b>'],
  [/\b<\/b>delete<\/b>/g, '</b>supprimer</b>'],
  [/\b<\/b> ou <\/b>delete<\/b>/g, '</b> ou </b>supprimer</b>'],
];

/** Paths where formal vous or English placeholders must stay. */
const SKIP_INFORMAL = new Set([
  'auth.emailPlaceholder',
]);

/** Paths where Pensée->Réflexion global replace should NOT apply (if any). */
const SKIP_GLOBAL = new Set([]);

let changed = 0;
const changedPaths = [];

function applyGlobal(text, pathKey) {
  if (typeof text !== 'string') return text;
  let out = text;
  if (!SKIP_INFORMAL.has(pathKey)) {
    for (const [re, rep] of GLOBAL_REPLACEMENTS) {
      if (SKIP_GLOBAL.has(pathKey)) continue;
      out = out.replace(re, rep);
    }
  } else {
    // still apply non-pronoun fixes
    for (const [re, rep] of GLOBAL_REPLACEMENTS) {
      if (/vous|Votre|votre|Vous/.test(re.source)) continue;
      out = out.replace(re, rep);
    }
  }
  return out;
}

function walk(obj, prefix = '') {
  for (const [k, v] of Object.entries(obj)) {
    const p = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') {
      let next = v;
      if (p in OVERRIDES) next = OVERRIDES[p];
      else next = applyGlobal(next, p);
      if (next !== v) {
        obj[k] = next;
        changed++;
        changedPaths.push(p);
      }
    } else if (v && typeof v === 'object') {
      walk(v, p);
    }
  }
}

// Snapshot before
function flatten(obj, prefix = '') {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') out[key] = v;
    else if (v && typeof v === 'object') Object.assign(out, flatten(v, key));
  }
  return out;
}

const before = flatten(fr);
walk(fr);

// Apply overrides that might have been missed (ensure all override keys set)
for (const [p, val] of Object.entries(OVERRIDES)) {
  const cur = getByPath(fr, p);
  if (cur !== val) {
    setByPath(fr, p, val);
    if (before[p] !== val && !changedPaths.includes(p)) {
      changed++;
      changedPaths.push(p);
    }
  }
}

fs.writeFileSync(frPath, JSON.stringify(fr, null, 2) + '\n');

const namespaces = {};
for (const p of changedPaths) {
  const ns = p.split('.')[0];
  namespaces[ns] = (namespaces[ns] || 0) + 1;
}

console.log(JSON.stringify({ changed, namespaces: Object.fromEntries(Object.entries(namespaces).sort((a, b) => b[1] - a[1])) }, null, 2));
