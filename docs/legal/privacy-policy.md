# Privacy Policy

**Effective date:** 24 May 2026  
**Last updated:** 24 May 2026

This Privacy Policy describes how **Babo Agency** (“**we**”, “**us**”, “**our**”) handles information when you use:

- the website and documentation at **[babo.agency](https://babo.agency)** (and our GitHub Pages mirror);
- the **Babo** desktop application and local agent runtime (open source, MIT);
- optional **Babo Cloud** services at **api.babo.agency** (hosted control plane, relay, and integrations);
- integrations you connect (Google Workspace, messaging channels, email, and AI inference providers).

Babo is **local-first**: agent memory, workspace files, and most runtime state stay on **your computer** unless you choose cloud features or connect a third-party service.

---

## 1. Who we are

| Role | Details |
|------|---------|
| **Product** | Babo — persistent AI agents with memory, projects, and integrations |
| **Open source** | [github.com/umbecanessa/babo](https://github.com/umbecanessa/babo) (MIT) |
| **Operator** | Babo Agency — website **babo.agency**, optional Babo Cloud **api.babo.agency** |
| **Contact** | [privacy@babo.agency](mailto:privacy@babo.agency) |

If you **self-host** Babo (your own NestJS/backend and data directory), you are the operator for that deployment. This policy still applies when you use **our** OAuth app, Babo Cloud, or this website.

---

## 2. Summary

| Area | Typical behavior |
|------|------------------|
| **Agent memory & chat** | Stored locally under your Babo data directory (desktop: app user data). Not uploaded to us by default. |
| **Babo Cloud account** | Email/account identifiers, agent metadata, relay and usage records on our servers when you use Babo Cloud. |
| **Google Workspace** | OAuth tokens stored **on your machine** (encrypted when possible). API calls go **Google ↔ your runtime**, not through us for self-host. |
| **AI models** | Prompts/completions go to **the inference provider you configure** (Ollama, OpenRouter, etc.). Babo Cloud may proxy hosted models through our API. |
| **Channels** | WhatsApp/Telegram/email traffic flows through providers you connect; Babo Cloud relay may carry message payloads while your desktop is online. |
| **Website** | Standard server logs; no ad tracking cookies on the marketing site. |

---

## 3. Information we collect

### 3.1 Information you provide

- **Account** (Babo Cloud / hosted NestJS): email address, display name, password (stored hashed), and profile settings.
- **Support & community**: messages you send us (email, GitHub issues, Discord).
- **Billing** (Babo Cloud): subscription status and payment metadata via **Stripe** (we do not store full card numbers).
- **Integrations**: API keys, bot tokens, or OAuth credentials you enter (stored in your deployment’s database or local config, depending on topology).

### 3.2 Information collected automatically

**Babo Cloud (when used)**

- Authentication tokens (JWT), session/relay connection events.
- **Usage metering** for hosted inference: token counts and upstream cost estimates for billing and dashboards — not used for advertising.
- Channel **relay** metadata and message payloads needed to deliver WhatsApp/Telegram/email webhooks to your online desktop.
- Server logs (IP address, user agent, timestamps, error traces) for security and operations.

**Desktop / self-hosted runtime (local)**

- The runtime writes agent state, sessions, memory, plans, and tool results under **`NLS_DATA_DIR`** (see [Data directory](../reference/data-directory.md)).
- We do **not** receive this local data unless you enable a feature that explicitly sends it to Babo Cloud or a third party.

**Website**

- Hosting and CDN logs (IP, referrer, pages viewed) from GitHub Pages or our domain host.

### 3.3 Information from third parties

- **Google** — account email and API access per OAuth scopes you approve (see §6).
- **Stripe** — payment confirmation and subscription state.
- **Messaging/email providers** — Telegram, WhatsApp bridge, Resend, etc., per channel setup.
- **Inference providers** — model API responses when you use BYOK or Babo Cloud-hosted models.

---

## 4. How we use information

We use information to:

- provide, maintain, and secure Babo and Babo Cloud;
- authenticate users and connect channel relays;
- run integrations you enable (Gmail, Calendar, Drive, Sheets, channels);
- meter and bill Babo Cloud subscriptions and included model usage;
- diagnose bugs and abuse;
- comply with law and enforce terms.

We **do not** sell your personal information. We **do not** use Google user data for advertising.

---

## 5. Local-first storage

When you run Babo on your computer:

- **Agent memory** (Cryptex, sessions, workspace files, plans, skill config) stays on disk locally.
- **Google OAuth refresh tokens** are stored under your local data directory, encrypted with a per-installation key when the `cryptography` package is available.
- **Inference**: requests go from your runtime to the endpoint you configure (local Ollama/vLLM or remote API). We are not in the path unless you point the app at Babo Cloud or another hosted proxy.

You can export or delete local data by removing the agent directory or uninstalling the app.

---

## 6. Google Workspace (Gmail, Calendar, Drive, Sheets)

Babo’s Google Workspace integration uses **Google OAuth 2.0**. Depending on setup:

| Mode | OAuth app | Where tokens live |
|------|-----------|-------------------|
| **Babo Cloud default** | Babo-operated Google Cloud OAuth client | Your desktop/runtime data directory |
| **Self-hosted BYO** | Your own Google Cloud project | Your deployment |

### 6.1 Scopes (what you authorize)

Access is limited to what you enable per service (read/write or read-only):

- Gmail (`gmail.readonly`, `gmail.send`, `gmail.modify` as configured)
- Google Calendar (`calendar` or `calendar.readonly`)
- Google Drive (`drive.readonly`, `drive.file` as configured)
- Google Sheets (`spreadsheets` or `spreadsheets.readonly`)
- Email address (`userinfo.email`)

### 6.2 How Google data is used

- Only to provide features you request (read/send mail, manage calendar events, access files/sheets, agent tools).
- Processed by **your Babo agent** on your machine; not used for unrelated profiling or ads.
- Not sold or transferred except as needed to operate the integration (Google’s APIs, or your self-hosted infrastructure).

### 6.3 Google API Services User Data Policy

Babo’s use of information received from **Google APIs** adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the **Limited Use** requirements:

- use data only to provide or improve user-facing features of Babo;
- not transfer Google user data to third parties except as necessary to provide the service, comply with law, or in a merger with notice;
- not use Google user data for ads;
- not allow humans to read Google user data except with your consent, for security, legal compliance, or aggregated anonymized internal operations.

### 6.4 Revoking Google access

- In Babo: disconnect Google Workspace in **Tools → Integrations**.
- In Google: [Google Account → Third-party access](https://myaccount.google.com/permissions) → remove Babo.
- Locally: tokens are deleted when you disconnect or revoke.

---

## 7. Other integrations & processors

| Service | Purpose | Data involved |
|---------|---------|----------------|
| **Inference** (OpenRouter, OpenAI-compatible, Ollama, vLLM, etc.) | LLM requests | Prompts, tool output, model responses — per your configuration |
| **Babo Cloud inference proxy** | Hosted models | Same, routed through api.babo.agency for billing |
| **Resend** | Agent email channel | Addresses, message content for inbound/outbound mail |
| **Telegram / WhatsApp** | Messaging channels | Message content and identifiers per provider rules |
| **Stripe** | Subscriptions | Customer ID, payment status |
| **GitHub** | Source, issues | Public repo data; issue content you post |

Each provider has its own privacy policy. You choose which integrations to enable.

---

## 8. Sharing and disclosure

We may share information:

- **With service providers** (hosting, Stripe, email, Google APIs) solely to operate features you use;
- **For legal reasons** if required by law or to protect rights, safety, and security;
- **In a business transfer** with notice (e.g. merger), subject to this policy;
- **With your direction** (e.g. when you connect a channel or export data).

We do not share Google user data with third parties except as described in §6 and the Limited Use policy.

---

## 9. Retention

| Data | Retention |
|------|-----------|
| Babo Cloud account | While account is active; deleted or anonymized after deletion request |
| Billing records | As required for tax/accounting (Stripe retains per their policy) |
| Server logs | Limited operational retention (typically days to weeks) |
| Local agent data | Until you delete agents or uninstall |

---

## 10. Security

We use industry-standard measures for Babo Cloud (TLS, hashed passwords, access controls, secrets in environment configuration). Local token encryption uses Fernet when `cryptography` is installed.

No system is perfectly secure. Keep your OS, Babo install, and API keys updated.

---

## 11. Your rights and choices

Depending on your location, you may have rights to **access**, **correct**, **delete**, or **export** personal data, and to **object** or **restrict** certain processing.

- **Babo Cloud account:** contact [privacy@babo.agency](mailto:privacy@babo.agency).
- **Local data:** delete under your data directory or via in-app agent removal.
- **Google:** revoke as in §6.4.
- **Marketing:** we do not send promotional email by default from this policy’s scope.

EU/UK users may lodge a complaint with a supervisory authority.

---

## 12. Children

Babo is not directed at children under 16. We do not knowingly collect their personal information.

---

## 13. International transfers

Babo Cloud may be operated from jurisdictions where our hosting providers run. By using Babo Cloud you acknowledge data may be processed outside your country with appropriate safeguards where required.

---

## 14. Open-source and self-hosting

You may run Babo without Babo Cloud. In that case:

- you control the server and database;
- you are responsible for privacy notices to **your** users if you operate a multi-user instance;
- our built-in Google OAuth app may still be used on Babo Cloud desktop defaults — Google data handling in §6 applies.

---

## 15. Changes to this policy

We may update this policy. We will post the new version at this URL with an updated “Last updated” date. Material changes may be announced on the website or in release notes.

---

## 16. Contact

**Babo Agency**  
Email: [privacy@babo.agency](mailto:privacy@babo.agency)  
Website: [https://babo.agency](https://babo.agency)

For open-source security issues, see the repository’s security policy on GitHub.
