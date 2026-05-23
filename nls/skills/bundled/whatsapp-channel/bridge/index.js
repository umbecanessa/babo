/**
 * Baileys WhatsApp Bridge -- HTTP server wrapping @whiskeysockets/baileys.
 *
 * Supports MULTIPLE agents, each with their own WhatsApp session.
 * Sessions are keyed by agent_id, with separate auth state per agent.
 *
 * Endpoints:
 *   GET  /health                 Health probe
 *   GET  /status/:agent_id       Connection status + linked phone for agent
 *   POST /pair/:agent_id         Start pairing session for agent, returns QR
 *   GET  /qr/:agent_id           Get current QR code for agent
 *   POST /send/:agent_id         Send a message { jid, text }
 *   POST /send-media/:agent_id   Send a file { jid, file, filename, mime_type }
 *   POST /configure              Legacy configure endpoint (no-op, kept for compat)
 *
 * Legacy unparameterized endpoints still work for backward compat,
 * falling back to the AGENT_ID env var or "default".
 *
 * Inbound messages are forwarded to the Python runtime via POST.
 */

import express from "express";
import {
  default as makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  downloadMediaMessage,
  downloadContentFromMessage,
} from "@whiskeysockets/baileys";
import QRCode from "qrcode";
import pino from "pino";
import path from "path";
import { fileURLToPath } from "url";
import fs from "fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = parseInt(process.env.BRIDGE_PORT || "9223", 10);
const BASE_AUTH_DIR = process.env.AUTH_DIR || path.join(__dirname, "..", "data", "baileys-auth");
const WEBHOOK_URL = process.env.WEBHOOK_URL || "";
const DEFAULT_AGENT_ID = process.env.AGENT_ID || "default";

const logger = pino({ level: "info" });
const app = express();
app.use(express.json());

process.on("uncaughtException", (err) => {
  logger.error(err, "Uncaught exception -- keeping bridge alive");
});
process.on("unhandledRejection", (reason) => {
  logger.error({ reason }, "Unhandled rejection -- keeping bridge alive");
});

// ── Per-agent session store ───────────────────────────────────

const sessions = new Map();

function getSession(agentId) {
  if (!sessions.has(agentId)) {
    sessions.set(agentId, {
      sock: null,
      currentQR: "",
      connectionStatus: "disconnected",
      linkedPhone: "",
    });
  }
  return sessions.get(agentId);
}

function authDirFor(agentId) {
  if (agentId === "default" || !agentId) return BASE_AUTH_DIR;
  return path.join(BASE_AUTH_DIR, agentId);
}

function resolveAgentId(req) {
  return req.params.agent_id || DEFAULT_AGENT_ID;
}

/** Which top-level keys exist on `msg.message` (for baileys.log tracing). */
function waMessageShape(m) {
  if (!m || typeof m !== "object") return [];
  const out = [];
  if (m.conversation) out.push("conversation");
  if (m.extendedTextMessage) out.push("extendedTextMessage");
  if (m.imageMessage) out.push("imageMessage");
  if (m.documentMessage) out.push("documentMessage");
  if (m.audioMessage) out.push("audioMessage");
  if (m.videoMessage) out.push("videoMessage");
  if (m.stickerMessage) out.push("stickerMessage");
  return out;
}

// ── Health ────────────────────────────────────────────────────

app.get("/health", (_req, res) => {
  const agents = [];
  for (const [id, s] of sessions) {
    agents.push({ agent_id: id, status: s.connectionStatus, phone: s.linkedPhone });
  }
  res.json({ ok: true, agents, webhook_url: WEBHOOK_URL ? WEBHOOK_URL.substring(0, 60) + "..." : "(empty)" });
});

// ── Status ────────────────────────────────────────────────────

app.get("/status/:agent_id", (req, res) => {
  const s = getSession(resolveAgentId(req));
  res.json({ connected: s.connectionStatus === "connected", status: s.connectionStatus, phone: s.linkedPhone });
});

app.get("/status", (_req, res) => {
  const s = getSession(DEFAULT_AGENT_ID);
  res.json({ connected: s.connectionStatus === "connected", status: s.connectionStatus, phone: s.linkedPhone });
});

// ── Configure (legacy compat -- agent_id now comes from URL) ──

app.post("/configure", async (req, res) => {
  const agentId = req.body.agent_id || DEFAULT_AGENT_ID;
  getSession(agentId);
  res.json({ ok: true, agent_id: agentId });
});

// ── QR Pairing ────────────────────────────────────────────────

app.post("/pair/:agent_id", async (req, res) => {
  const agentId = resolveAgentId(req);
  const s = getSession(agentId);

  if (s.connectionStatus === "connected") {
    return res.json({ status: "already_connected", phone: s.linkedPhone, qr: "" });
  }

  try {
    await startSocket(agentId);
    await new Promise((r) => setTimeout(r, 2000));
    res.json({ status: "waiting", qr: s.currentQR });
  } catch (err) {
    logger.error(err, "Failed to start pairing for %s", agentId);
    res.status(500).json({ error: err.message });
  }
});

app.post("/pair", async (_req, res) => {
  const s = getSession(DEFAULT_AGENT_ID);
  if (s.connectionStatus === "connected") {
    return res.json({ status: "already_connected", phone: s.linkedPhone, qr: "" });
  }
  try {
    await startSocket(DEFAULT_AGENT_ID);
    await new Promise((r) => setTimeout(r, 2000));
    res.json({ status: "waiting", qr: s.currentQR });
  } catch (err) {
    logger.error(err, "Failed to start pairing");
    res.status(500).json({ error: err.message });
  }
});

app.get("/qr/:agent_id", (req, res) => {
  const s = getSession(resolveAgentId(req));
  res.json({ qr: s.currentQR, status: s.connectionStatus });
});

app.get("/qr", (_req, res) => {
  const s = getSession(DEFAULT_AGENT_ID);
  res.json({ qr: s.currentQR, status: s.connectionStatus });
});

// ── Send Message ──────────────────────────────────────────────

app.post("/send/:agent_id", async (req, res) => {
  const agentId = resolveAgentId(req);
  const s = getSession(agentId);

  if (s.connectionStatus !== "connected" || !s.sock) {
    return res.status(503).json({ error: "Not connected" });
  }

  const { jid, text } = req.body;
  if (!jid || !text) return res.status(400).json({ error: "jid and text required" });

  try {
    const result = await s.sock.sendMessage(jid, { text });
    res.json({ ok: true, messageId: result?.key?.id });
  } catch (err) {
    logger.error(err, "Send failed to %s (agent %s)", jid, agentId);
    res.status(500).json({ error: err.message });
  }
});

app.post("/send", async (req, res) => {
  const s = getSession(DEFAULT_AGENT_ID);
  if (s.connectionStatus !== "connected" || !s.sock) {
    return res.status(503).json({ error: "Not connected" });
  }
  const { jid, text } = req.body;
  if (!jid || !text) return res.status(400).json({ error: "jid and text required" });
  try {
    const result = await s.sock.sendMessage(jid, { text });
    res.json({ ok: true, messageId: result?.key?.id });
  } catch (err) {
    logger.error(err, "Send failed to %s", jid);
    res.status(500).json({ error: err.message });
  }
});

// ── Send Media ────────────────────────────────────────────────

app.post("/send-media/:agent_id", async (req, res) => {
  await handleSendMedia(resolveAgentId(req), req, res);
});

app.post("/send-media", async (req, res) => {
  await handleSendMedia(DEFAULT_AGENT_ID, req, res);
});

async function handleSendMedia(agentId, req, res) {
  const s = getSession(agentId);
  if (s.connectionStatus !== "connected" || !s.sock) {
    return res.status(503).json({ error: "Not connected" });
  }

  const { jid, file, filename, mime_type, caption } = req.body;
  if (!jid || !file) return res.status(400).json({ error: "jid and file (base64) required" });

  try {
    const buffer = Buffer.from(file, "base64");
    const isImage = (mime_type || "").startsWith("image/");

    let msgPayload;
    if (isImage) {
      msgPayload = { image: buffer, mimetype: mime_type || "image/jpeg", caption: caption || "" };
    } else {
      msgPayload = { document: buffer, mimetype: mime_type || "application/octet-stream", fileName: filename || "file", caption: caption || "" };
    }

    const result = await s.sock.sendMessage(jid, msgPayload);
    res.json({ ok: true, messageId: result?.key?.id });
  } catch (err) {
    logger.error(err, "Send-media failed to %s (agent %s)", jid, agentId);
    res.status(500).json({ error: err.message });
  }
}

// ── Helpers ───────────────────────────────────────────────────

function _mimeExt(mime) {
  const map = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
    "image/gif": "gif", "audio/ogg; codecs=opus": "ogg", "audio/ogg": "ogg",
    "audio/mpeg": "mp3", "audio/mp4": "m4a", "video/mp4": "mp4",
    "application/pdf": "pdf",
  };
  return map[mime] || mime.split("/")[1] || "bin";
}

// ── Socket Management ─────────────────────────────────────────

async function startSocket(agentId) {
  const s = getSession(agentId);
  if (s.sock) return;

  s.connectionStatus = "connecting";
  s.currentQR = "";

  const agentAuthDir = authDirFor(agentId);
  fs.mkdirSync(agentAuthDir, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(agentAuthDir);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: "silent" }),
  });
  s.sock = sock;

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      try {
        s.currentQR = await QRCode.toDataURL(qr);
      } catch (e) {
        logger.error(e, "QR generation failed for %s", agentId);
      }
      s.connectionStatus = "connecting";
    }

    if (connection === "open") {
      s.connectionStatus = "connected";
      s.currentQR = "";
      s.linkedPhone = sock.user?.id?.split(":")[0] || "";
      logger.info("Agent %s connected as %s", agentId, s.linkedPhone);

      if (WEBHOOK_URL) {
        try {
          let notifyUrl = WEBHOOK_URL.replace("/webhook/", "/connected/");
          if (notifyUrl.includes("{agent_id}")) {
            notifyUrl = notifyUrl.replace("{agent_id}", agentId);
          }
          await fetch(notifyUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ phone: s.linkedPhone }),
          });
        } catch (e) {
          logger.warn("Could not notify runtime for agent %s: %s", agentId, e.message);
        }
      }
    }

    if (connection === "close") {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      s.connectionStatus = "disconnected";
      s.sock = null;

      if (shouldReconnect) {
        logger.info("Agent %s reconnecting (status %d)...", agentId, statusCode);
        setTimeout(() => startSocket(agentId), 3000);
      } else {
        logger.info("Agent %s logged out, deleting auth state", agentId);
        s.linkedPhone = "";
        try {
          fs.rmSync(agentAuthDir, { recursive: true, force: true });
        } catch (e) {
          logger.warn("Could not delete auth dir for %s: %s", agentId, e.message);
        }
      }
    }
  });

  sock.ev.on("messages.upsert", async ({ messages: msgs, type }) => {
    if (type !== "notify") return;

    for (const msg of msgs) {
      if (msg.key.fromMe) continue;

      const messageId = msg.key.id || "";
      const remoteJid = msg.key.remoteJid || "";
      const participant = msg.key.participant || "";
      const isGroup = remoteJid.endsWith("@g.us");

      const m = msg.message || {};
      let text =
        m.conversation ||
        m.extendedTextMessage?.text ||
        m.imageMessage?.caption ||
        m.documentMessage?.caption ||
        m.videoMessage?.caption ||
        "";

      const shape = waMessageShape(m);
      logger.info(
        {
          tag: "whatsapp-bridge",
          phase: "recv",
          agentId,
          messageId,
          remoteJid,
          participant: isGroup ? participant : "",
          isGroup,
          textChars: text.length,
          shape,
        },
        "whatsapp-bridge recv",
      );

      let media = null;
      const mediaTypes = [
        { key: "imageMessage",    field: m.imageMessage },
        { key: "documentMessage", field: m.documentMessage },
        { key: "audioMessage",    field: m.audioMessage },
        { key: "videoMessage",    field: m.videoMessage },
        { key: "stickerMessage",  field: m.stickerMessage },
      ];

      // Baileys requires the 4th "context" arg for reliable CDN fetch + decrypt:
      // reuploadRequest triggers WhatsApp re-upload when media expired (404/410).
      // Without it, downloadMediaMessage can fail even for small files.
      // See: https://whiskeysockets-baileys-94.mintlify.app/messaging/downloading-media
      const mediaDownloadCtx = {
        logger,
        reuploadRequest: sock.updateMediaMessage.bind(sock),
      };

      let mediaPhase = "none";
      let mediaTypeTried = null;
      let mediaBytes = null;
      let mediaMime = null;
      let mediaFilename = null;

      for (const mt of mediaTypes) {
        if (!mt.field) continue;
        mediaTypeTried = mt.key;

        const MEDIA_DL_TIMEOUT_MS = 30_000;
        const MAX_RETRIES = 2;
        const MEDIA_TYPE_MAP = {
          imageMessage: "image", documentMessage: "document",
          audioMessage: "audio", videoMessage: "video", stickerMessage: "sticker",
        };

        let lastErr = null;
        for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
          try {
            const dlPromise = downloadMediaMessage(msg, "buffer", {}, mediaDownloadCtx);
            dlPromise.catch(() => {});
            const buffer = await Promise.race([
              dlPromise,
              new Promise((_, reject) =>
                setTimeout(() => reject(new Error(`Media download timed out after ${MEDIA_DL_TIMEOUT_MS}ms`)), MEDIA_DL_TIMEOUT_MS),
              ),
            ]);
            const mimeType = mt.field.mimetype || "application/octet-stream";
            const isVoice = mt.key === "audioMessage" && !!mt.field.ptt;
            const filename =
              mt.field.fileName ||
              `${mt.key.replace("Message", "")}_${Date.now()}.${_mimeExt(mimeType)}`;
            media = { data: buffer.toString("base64"), mime_type: mimeType, filename, is_voice: isVoice, media_type: mt.key };
            mediaPhase = "ok";
            mediaBytes = buffer.length;
            mediaMime = mimeType;
            mediaFilename = filename;
            lastErr = null;
            break;
          } catch (dlErr) {
            lastErr = dlErr;
            if (attempt < MAX_RETRIES) {
              logger.warn(
                { tag: "whatsapp-bridge", phase: "media_retry", agentId, messageId, attempt, err: String(dlErr) },
                `whatsapp-bridge media download attempt ${attempt} failed, retrying...`,
              );
              await new Promise(r => setTimeout(r, 1500 * attempt));
            }
          }
        }

        if (lastErr && mt.field.directPath && mt.field.mediaKey) {
          try {
            logger.info(
              { tag: "whatsapp-bridge", phase: "media_fallback", agentId, messageId, mediaType: mt.key },
              "whatsapp-bridge trying downloadContentFromMessage fallback",
            );
            const contentType = MEDIA_TYPE_MAP[mt.key] || "document";
            const stream = await Promise.race([
              downloadContentFromMessage(mt.field, contentType),
              new Promise((_, reject) =>
                setTimeout(() => reject(new Error("Content download fallback timed out")), MEDIA_DL_TIMEOUT_MS),
              ),
            ]);
            const chunks = [];
            for await (const chunk of stream) chunks.push(chunk);
            const buffer = Buffer.concat(chunks);
            const mimeType = mt.field.mimetype || "application/octet-stream";
            const isVoice = mt.key === "audioMessage" && !!mt.field.ptt;
            const filename =
              mt.field.fileName ||
              `${mt.key.replace("Message", "")}_${Date.now()}.${_mimeExt(mimeType)}`;
            media = { data: buffer.toString("base64"), mime_type: mimeType, filename, is_voice: isVoice, media_type: mt.key };
            mediaPhase = "ok_fallback";
            mediaBytes = buffer.length;
            mediaMime = mimeType;
            mediaFilename = filename;
            lastErr = null;
          } catch (fbErr) {
            logger.error(
              { err: fbErr, tag: "whatsapp-bridge", phase: "media_fallback", agentId, messageId },
              "whatsapp-bridge content fallback also failed",
            );
          }
        }

        if (lastErr) {
          mediaPhase = "download_error";
          logger.error(
            { err: lastErr, tag: "whatsapp-bridge", phase: "media", agentId, messageId, remoteJid, mediaType: mt.key },
            "whatsapp-bridge media download failed (all attempts exhausted)",
          );
        }
        break;
      }

      logger.info(
        {
          tag: "whatsapp-bridge",
          phase: "media",
          agentId,
          messageId,
          remoteJid,
          mediaPhase,
          mediaTypeTried,
          mediaBytes,
          mediaMime,
          mediaFilename,
          willForward: !!(text || media),
        },
        "whatsapp-bridge media",
      );

      if (!text && !media) {
        if (mediaTypeTried === "audioMessage" && mediaPhase === "download_error") {
          text = "[Voice message received but audio could not be downloaded. Please ask the user to resend or type their message.]";
          logger.warn(
            {
              tag: "whatsapp-bridge",
              phase: "voice_fallback",
              agentId,
              messageId,
              remoteJid,
              mediaPhase,
            },
            "whatsapp-bridge voice download failed, forwarding placeholder",
          );
        } else {
          logger.warn(
            {
              tag: "whatsapp-bridge",
              phase: "drop",
              agentId,
              messageId,
              remoteJid,
              reason: "no_text_and_no_media",
              mediaPhase,
              mediaTypeTried,
              shape,
            },
            "whatsapp-bridge drop (no webhook)",
          );
          continue;
        }
      }

      const from = remoteJid;
      const senderPhone = isGroup ? participant || "" : from;

      const payload = {
        from: senderPhone,
        name: msg.pushName || senderPhone.split("@")[0],
        text,
        isGroup,
        groupId: isGroup ? from : null,
        messageId,
        timestamp: msg.messageTimestamp || Math.floor(Date.now() / 1000),
        bridge_phone: s.linkedPhone,
      };
      if (media) payload.media = media;

      if (!WEBHOOK_URL) {
        logger.warn(
          {
            tag: "whatsapp-bridge",
            phase: "drop",
            agentId,
            messageId,
            reason: "webhook_url_empty",
          },
          "whatsapp-bridge drop (WEBHOOK_URL not set)",
        );
        continue;
      }

      const url = WEBHOOK_URL.includes("{agent_id}")
        ? WEBHOOK_URL.replace("{agent_id}", agentId)
        : WEBHOOK_URL;
      let bodyJson;
      try {
        bodyJson = JSON.stringify(payload);
      } catch (serErr) {
        logger.error(
          {
            err: serErr,
            tag: "whatsapp-bridge",
            phase: "webhook",
            agentId,
            messageId,
          },
          "whatsapp-bridge webhook serialize failed",
        );
        continue;
      }

      const payloadBytes = Buffer.byteLength(bodyJson, "utf8");
      logger.info(
        {
          tag: "whatsapp-bridge",
          phase: "webhook_post",
          agentId,
          messageId,
          remoteJid,
          payloadBytes,
          hasMedia: !!media,
        },
        "whatsapp-bridge webhook POST",
      );

      const t0 = Date.now();
      try {
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: bodyJson,
        });
        const durationMs = Date.now() - t0;
        const logBase = {
          tag: "whatsapp-bridge",
          phase: "webhook",
          agentId,
          messageId,
          remoteJid,
          httpStatus: res.status,
          ok: res.ok,
          durationMs,
        };
        if (res.ok) {
          logger.info(logBase, "whatsapp-bridge webhook ok");
        } else {
          let bodyPreview = "";
          try {
            bodyPreview = (await res.text()).slice(0, 400);
          } catch (_) {
            /* ignore */
          }
          logger.warn({ ...logBase, bodyPreview }, "whatsapp-bridge webhook non-2xx");
        }
      } catch (e) {
        logger.error(
          {
            err: e,
            tag: "whatsapp-bridge",
            phase: "webhook",
            agentId,
            messageId,
            remoteJid,
            durationMs: Date.now() - t0,
          },
          "whatsapp-bridge webhook fetch failed",
        );
      }
    }
  });
}

// ── Migrate legacy single-agent auth to per-agent dir ─────────

function migrateLegacyAuth() {
  if (DEFAULT_AGENT_ID === "default") return;
  const newDir = authDirFor(DEFAULT_AGENT_ID);
  if (fs.existsSync(newDir)) return;
  if (!fs.existsSync(BASE_AUTH_DIR)) return;

  const entries = fs.readdirSync(BASE_AUTH_DIR);
  const hasCredFiles = entries.some(e => e.endsWith(".json") && !fs.statSync(path.join(BASE_AUTH_DIR, e)).isDirectory());
  if (hasCredFiles) {
    logger.info("Migrating legacy auth to per-agent dir for %s", DEFAULT_AGENT_ID);
    fs.mkdirSync(newDir, { recursive: true });
    for (const e of entries) {
      const src = path.join(BASE_AUTH_DIR, e);
      if (!fs.statSync(src).isDirectory()) {
        fs.copyFileSync(src, path.join(newDir, e));
      }
    }
  }
}

// ── Start Server ──────────────────────────────────────────────

app.listen(PORT, () => {
  logger.info("Baileys bridge listening on port %d (multi-agent)", PORT);
  migrateLegacyAuth();

  // Auto-connect any agents that have saved auth state
  const dirsToCheck = [];
  if (fs.existsSync(BASE_AUTH_DIR)) {
    for (const entry of fs.readdirSync(BASE_AUTH_DIR)) {
      const full = path.join(BASE_AUTH_DIR, entry);
      if (fs.statSync(full).isDirectory()) {
        dirsToCheck.push({ agentId: entry, dir: full });
      }
    }
    // Also check if legacy creds exist at base level (backward compat)
    const hasLegacy = fs.readdirSync(BASE_AUTH_DIR).some(
      e => e.endsWith(".json") && !fs.statSync(path.join(BASE_AUTH_DIR, e)).isDirectory()
    );
    if (hasLegacy && !dirsToCheck.some(d => d.agentId === DEFAULT_AGENT_ID)) {
      dirsToCheck.push({ agentId: DEFAULT_AGENT_ID, dir: BASE_AUTH_DIR });
    }
  }

  for (const { agentId } of dirsToCheck) {
    logger.info("Auto-connecting agent %s...", agentId);
    startSocket(agentId).catch((e) => logger.error(e, "Auto-connect failed for %s", agentId));
  }
});
