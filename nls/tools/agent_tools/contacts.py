"""Contacts tool — address book across all connected channels + personal store.

The agent can query channel-native contacts (WhatsApp known senders,
Telegram users, email known senders) AND maintain its own persistent
contact store (add / edit / delete / search) that lives at
  data/agents/{agent_id}/contacts.json

Channel-native contacts are read-only (they come from the adapters).
Personal store contacts supplement them and take priority in search.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from .base import ToolResult

logger = logging.getLogger(__name__)

_CONTACTS_FILE = "contacts.json"


class ContactsTool:
    """Agent tool for querying and managing contacts across channels."""

    def __init__(self, agent_id: str, data_dir: Path | None = None) -> None:
        self._agent_id = agent_id
        self._data_dir = data_dir

    @property
    def name(self) -> str:
        return "contacts"

    @property
    def description(self) -> str:
        return (
            "Manage and look up contacts across all connected channels. "
            "Always search before asking the user for a phone number or email. "
            "Supports adding, editing, and deleting contacts in the personal store."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "owner", "search", "list", "groups", "recent",
                        "add", "edit", "delete",
                    ],
                    "description": (
                        "owner: get the owner's contact info across all channels. "
                        "search: find a contact by name, email, or phone. "
                        "list: list all known contacts (optional channel filter). "
                        "groups: list group chats the agent is in. "
                        "recent: recently active conversations. "
                        "add: save a new contact to the personal store. "
                        "edit: update an existing contact (use contact_id). "
                        "delete: remove a contact from the personal store (use contact_id)."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Search query — name, phone fragment, or email (for 'search').",
                },
                "channel": {
                    "type": "string",
                    "description": (
                        "Filter results to a specific channel "
                        "(whatsapp, telegram, email, discord, slack, …)."
                    ),
                },
                "contact_id": {
                    "type": "string",
                    "description": "Contact ID returned by 'add' or 'search', used for 'edit' and 'delete'.",
                },
                "name": {
                    "type": "string",
                    "description": "Contact display name (for 'add' or 'edit').",
                },
                "email": {
                    "type": "string",
                    "description": "Email address (for 'add' or 'edit').",
                },
                "phone": {
                    "type": "string",
                    "description": "Phone number in E.164 format e.g. +39366... (for 'add' or 'edit').",
                },
                "telegram_id": {
                    "type": "string",
                    "description": "Telegram numeric chat ID (add/edit).",
                },
                "discord_id": {
                    "type": "string",
                    "description": "Discord user snowflake ID (add/edit).",
                },
                "slack_id": {
                    "type": "string",
                    "description": "Slack user ID U… (add/edit).",
                },
                "notes": {
                    "type": "string",
                    "description": "Free-text notes about this contact (for 'add' or 'edit').",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels e.g. ['work', 'team'] (for 'add' or 'edit').",
                },
            },
        }

    # ------------------------------------------------------------------
    # Persistent store helpers
    # ------------------------------------------------------------------

    def _store_path(self) -> Path | None:
        if self._data_dir:
            return self._data_dir / _CONTACTS_FILE
        try:
            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am:
                return am.agents_dir / self._agent_id / _CONTACTS_FILE
        except Exception:
            pass
        return None

    def _load_store(self) -> list[dict]:
        path = self._store_path()
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else []
            except Exception:
                pass
        return []

    def _save_store(self, contacts: list[dict]) -> None:
        path = self._store_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(contacts, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("contacts: failed to save store: %s", exc)

    # ------------------------------------------------------------------
    # Adapter resolution
    # ------------------------------------------------------------------

    def _get_adapters(self) -> dict[str, Any]:
        adapters: dict[str, Any] = {}
        try:
            from server.main import app
            sl = getattr(app.state, "skill_loader", None)
            if sl is None:
                return adapters
            for skill_name, sk in sl.skills.items():
                if not sk or not sk.context:
                    continue
                meta = getattr(sk, "meta", None)
                if not (
                    skill_name.endswith("-channel")
                    or (meta and getattr(meta, "contacts", None))
                ):
                    continue
                adapter = getattr(sk.context, "adapter", None)
                if adapter is None:
                    continue
                channel_key = getattr(adapter, "name", None) or getattr(adapter, "channel_name", "")
                if meta and getattr(meta, "contacts", None):
                    channel_key = meta.contacts.channel_key or channel_key
                if not channel_key:
                    if skill_name.endswith("-channel"):
                        channel_key = skill_name.replace("-channel", "")
                    else:
                        continue
                adapters[str(channel_key)] = adapter
        except Exception:
            logger.debug("contacts: adapter resolution failed", exc_info=True)
        return adapters

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(
        self,
        params: dict[str, Any],
        signal: asyncio.Event | None = None,
    ) -> ToolResult:
        action = params.get("action", "")
        channel_filter = params.get("channel", "")
        query = params.get("query", "").strip()

        handler = {
            "owner":  self._owner,
            "search": self._search,
            "list":   self._list,
            "groups": self._groups,
            "recent": self._recent,
            "add":    self._add,
            "edit":   self._edit,
            "delete": self._delete,
        }.get(action)

        if handler is None:
            return ToolResult(
                content=f"Unknown action '{action}'. Use: owner, search, list, groups, recent, add, edit, delete.",
                is_error=True,
            )
        try:
            return await handler(
                channel_filter=channel_filter,
                query=query,
                params=params,
            )
        except Exception as exc:
            logger.exception("contacts tool error (action=%s)", action)
            return ToolResult(content=f"Error: {exc}", is_error=True)

    # ------------------------------------------------------------------
    # Read actions
    # ------------------------------------------------------------------

    async def _owner(self, **_kw: Any) -> ToolResult:
        adapters = self._get_adapters()
        lines: list[str] = ["Owner contact info:"]
        found_any = False

        # Show display name when known so the agent can match "Umberto" → owner.
        owner_name = self._get_owner_name()
        if owner_name:
            lines.append(f"  name: {owner_name}")

        # Google Workspace connected email — most reliable source for the
        # user's personal email address.
        gw_email = self._get_google_workspace_email()
        if gw_email:
            found_any = True
            lines.append(f"  google: {gw_email} (owner's personal email / Gmail)")

        owner_phones: list[str] = []
        for channel, adapter in adapters.items():
            cfg = self._cfg(adapter)
            owner = cfg.get("owner_identity", "")
            if not owner:
                # For the email channel, fall back to the agent's own alias so
                # the agent knows WHAT ADDRESS to send FROM (different from the
                # user's personal address above).
                if channel == "email":
                    alias = cfg.get("alias", "") or cfg.get("from_address", "")
                    if alias:
                        lines.append(f"  email: agent sends FROM {alias}")
                continue
            found_any = True
            linked = cfg.get("linked_phone", "") or cfg.get("linked_id", "")
            connected = self._is_connected(adapter)
            status = "connected" if connected else "not connected"
            line = f"  {channel}: {owner} ({status})"
            if linked:
                line += f"  [agent's own {channel} ID: {linked}]"
            lines.append(line)
            if channel == "whatsapp":
                owner_phones.append(owner)

        # Auto-persist the owner into the personal contacts store so future
        # searches for the owner's name / email / phone always resolve.
        self._upsert_owner_contact(
            name=owner_name,
            email=gw_email,
            phones=owner_phones,
        )

        if not found_any:
            return ToolResult(content="No owner identity configured on any channel. Ask the user.")
        return ToolResult(content="\n".join(lines))

    async def _search(self, *, query: str, channel_filter: str = "", **_kw: Any) -> ToolResult:
        if not query:
            return ToolResult(content="Provide a 'query' to search for.", is_error=True)

        q_lower = query.lower()
        q_digits = "".join(c for c in query if c.isdigit())
        matches: list[str] = []

        # Search personal store first
        for c in self._load_store():
            name = (c.get("name") or "").lower()
            email = (c.get("email") or "").lower()
            phone = (c.get("phone") or "").lower()
            notes = (c.get("notes") or "").lower()
            if (q_lower in name or q_lower in email or q_lower in phone or
                    q_lower in notes or (q_digits and q_digits in phone.replace("+", ""))):
                matches.append(self._format_stored(c))

        # Search channel adapters
        adapters = self._get_adapters()
        for channel, adapter in adapters.items():
            if channel_filter and channel != channel_filter:
                continue
            for contact in self._gather_contacts(channel, adapter):
                identifier = contact.get("id", "").lower()
                name = contact.get("name", "").lower()
                if q_lower in name or q_lower in identifier:
                    matches.append(self._format_contact(channel, contact))
                elif q_digits and q_digits in identifier.replace("+", "").replace("-", ""):
                    matches.append(self._format_contact(channel, contact))

        if not matches:
            return ToolResult(content=f"No contacts matching '{query}'.")
        return ToolResult(content=f"Found {len(matches)} match(es):\n" + "\n".join(matches))

    async def _list(self, *, channel_filter: str = "", **_kw: Any) -> ToolResult:
        lines: list[str] = []

        # Personal store
        stored = self._load_store()
        if stored and not channel_filter:
            lines.append(f"[personal store] {len(stored)} contact(s):")
            for c in stored[:50]:
                lines.append(f"  {self._format_stored(c)}")
            if len(stored) > 50:
                lines.append(f"  ... and {len(stored) - 50} more")

        # Channel adapters
        adapters = self._get_adapters()
        for channel, adapter in adapters.items():
            if channel_filter and channel != channel_filter:
                continue
            contacts = self._gather_contacts(channel, adapter)
            if contacts:
                lines.append(f"[{channel}] {len(contacts)} contact(s):")
                for c in contacts[:50]:
                    lines.append(f"  {self._format_contact(channel, c)}")
                if len(contacts) > 50:
                    lines.append(f"  ... and {len(contacts) - 50} more")

        if not lines:
            return ToolResult(content="No known contacts on any connected channel.")
        return ToolResult(content="\n".join(lines))

    async def _groups(self, *, channel_filter: str = "", **_kw: Any) -> ToolResult:
        adapters = self._get_adapters()
        lines: list[str] = []
        for channel, adapter in adapters.items():
            if channel_filter and channel != channel_filter:
                continue
            groups = self._gather_groups(channel, adapter)
            if groups:
                lines.append(f"[{channel}] {len(groups)} group(s):")
                for g in groups:
                    gid = g.get("id", "?")
                    gname = g.get("name", gid)
                    lines.append(f"  {gname} (id: {gid})")

        if not lines:
            return ToolResult(content="No group chats found.")
        return ToolResult(content="\n".join(lines))

    async def _recent(self, *, channel_filter: str = "", **_kw: Any) -> ToolResult:
        sessions: dict[str, dict] = {}
        try:
            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am:
                runtime = am.get_runtime(self._agent_id)
                if runtime and hasattr(runtime, "channel_registry") and runtime.channel_registry:
                    sessions = runtime.channel_registry.session_router.list_sessions()
        except Exception as exc:
            return ToolResult(content=f"Could not load conversations: {exc}", is_error=True)

        if not sessions:
            return ToolResult(content="No recent conversations.")

        lines: list[str] = [f"{len(sessions)} conversation(s):"]
        sorted_sessions = sorted(
            sessions.items(),
            key=lambda kv: kv[1].get("last_active", 0),
            reverse=True,
        )
        for key, meta in sorted_sessions[:20]:
            channel = meta.get("channel", key.split(":")[0])
            if channel_filter and channel != channel_filter:
                continue
            sender = meta.get("sender", "")
            last_active = meta.get("last_active", 0)
            age = _format_age(last_active) if last_active else "unknown"
            lines.append(f"  [{channel}] {sender or key} — last active {age}")
            if not channel_filter and len(lines) > 20:
                lines.append("  ... use channel filter for more")
                break

        return ToolResult(content="\n".join(lines))

    # ------------------------------------------------------------------
    # Write actions
    # ------------------------------------------------------------------

    async def _add(self, *, params: dict, **_kw: Any) -> ToolResult:
        name = (params.get("name") or "").strip()
        email = (params.get("email") or "").strip()
        phone = (params.get("phone") or "").strip()
        notes = (params.get("notes") or "").strip()
        tags = params.get("tags") or []
        telegram_id = (params.get("telegram_id") or "").strip()
        discord_id = (params.get("discord_id") or "").strip()
        slack_id = (params.get("slack_id") or "").strip()

        if not name and not email and not phone:
            return ToolResult(
                content="Provide at least a name, email, or phone to add a contact.",
                is_error=True,
            )

        store = self._load_store()

        # Dedup check
        for existing in store:
            if email and (existing.get("email") or "").lower() == email.lower():
                return ToolResult(
                    content=f"Contact with email {email} already exists (id: {existing['id']}). Use 'edit' to update.",
                )
            if phone and (existing.get("phone") or "") == phone:
                return ToolResult(
                    content=f"Contact with phone {phone} already exists (id: {existing['id']}). Use 'edit' to update.",
                )
            if telegram_id and (existing.get("telegram_id") or "") == telegram_id:
                return ToolResult(
                    content=f"Contact with Telegram ID {telegram_id} already exists (id: {existing['id']}). Use 'edit' to update.",
                )
            if discord_id and (existing.get("discord_id") or "") == discord_id:
                return ToolResult(
                    content=f"Contact with Discord ID {discord_id} already exists (id: {existing['id']}). Use 'edit' to update.",
                )
            if slack_id and (existing.get("slack_id") or "") == slack_id:
                return ToolResult(
                    content=f"Contact with Slack ID {slack_id} already exists (id: {existing['id']}). Use 'edit' to update.",
                )

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        contact: dict[str, Any] = {
            "id": str(uuid.uuid4())[:8],
            "name": name,
            "email": email,
            "phone": phone,
            "telegram_id": telegram_id,
            "discord_id": discord_id,
            "slack_id": slack_id,
            "notes": notes,
            "tags": tags if isinstance(tags, list) else [],
            "created_at": now,
            "updated_at": now,
        }
        store.append(contact)
        self._save_store(store)

        parts = [f"Contact saved (id: {contact['id']})"]
        if name:
            parts.append(f"name: {name}")
        if email:
            parts.append(f"email: {email}")
        if phone:
            parts.append(f"phone: {phone}")
        if telegram_id:
            parts.append(f"telegram_id: {telegram_id}")
        if discord_id:
            parts.append(f"discord_id: {discord_id}")
        if slack_id:
            parts.append(f"slack_id: {slack_id}")
        return ToolResult(content=" | ".join(parts))

    async def _edit(self, *, params: dict, **_kw: Any) -> ToolResult:
        contact_id = (params.get("contact_id") or "").strip()
        if not contact_id:
            return ToolResult(content="Provide 'contact_id' to edit a contact.", is_error=True)

        store = self._load_store()
        for i, c in enumerate(store):
            if c.get("id") == contact_id:
                for field in (
                    "name", "email", "phone", "notes",
                    "telegram_id", "discord_id", "slack_id",
                ):
                    val = params.get(field)
                    if val is not None:
                        store[i][field] = str(val).strip()
                if params.get("tags") is not None:
                    store[i]["tags"] = params["tags"] if isinstance(params["tags"], list) else []
                store[i]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._save_store(store)
                return ToolResult(content=f"Contact {contact_id} updated: {self._format_stored(store[i])}")

        return ToolResult(content=f"No contact with id '{contact_id}' found.", is_error=True)

    async def _delete(self, *, params: dict, **_kw: Any) -> ToolResult:
        contact_id = (params.get("contact_id") or "").strip()
        if not contact_id:
            return ToolResult(content="Provide 'contact_id' to delete a contact.", is_error=True)

        store = self._load_store()
        before = len(store)
        store = [c for c in store if c.get("id") != contact_id]
        if len(store) == before:
            return ToolResult(content=f"No contact with id '{contact_id}' found.", is_error=True)

        self._save_store(store)
        return ToolResult(content=f"Contact {contact_id} deleted.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _upsert_owner_contact(
        self,
        *,
        name: str,
        email: str,
        phones: list[str],
    ) -> None:
        """Silently add-or-update the owner entry in the personal contacts store.

        Called every time contacts(action='owner') runs.  Idempotent — only
        writes when something is actually new or has changed.  The owner entry
        is tagged with ``owner=true`` so it can be identified later.
        """
        if not name and not email and not phones:
            return

        store = self._load_store()
        changed = False

        # Find existing owner record (tagged or matched by email/phone)
        owner_entry: dict | None = None
        for c in store:
            if c.get("owner"):
                owner_entry = c
                break
            if email and (c.get("email") or "").lower() == email.lower():
                owner_entry = c
                break
            for ph in phones:
                if ph and c.get("phone") == ph:
                    owner_entry = c
                    break
            if owner_entry:
                break

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if owner_entry is None:
            # Create new entry only when we have at least a name or identifer
            contact: dict[str, Any] = {
                "id": str(uuid.uuid4())[:8],
                "name": name,
                "email": email,
                "phone": phones[0] if phones else "",
                "notes": "Owner / user of this agent",
                "tags": ["owner"],
                "owner": True,
                "created_at": now,
                "updated_at": now,
            }
            store.append(contact)
            changed = True
        else:
            # Merge missing fields without overwriting existing data
            if name and not owner_entry.get("name"):
                owner_entry["name"] = name
                changed = True
            if email and not owner_entry.get("email"):
                owner_entry["email"] = email
                changed = True
            if phones and not owner_entry.get("phone"):
                owner_entry["phone"] = phones[0]
                changed = True
            if not owner_entry.get("owner"):
                owner_entry["owner"] = True
                owner_entry["tags"] = list(set(owner_entry.get("tags") or []) | {"owner"})
                changed = True
            if changed:
                owner_entry["updated_at"] = now

        if changed:
            self._save_store(store)
            logger.debug("contacts: owner entry upserted in personal store")

    def _get_google_workspace_email(self) -> str:
        """Return the owner's email — checks account meta first, then Google Workspace."""
        # Primary: owner_email stored in agent_meta.json at account creation time.
        # This is the most reliable source (set from the user's sign-up email).
        try:
            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am is not None:
                meta_path = am.agents_dir / self._agent_id / "agent_meta.json"
                if meta_path.exists():
                    import json as _j
                    meta = _j.loads(meta_path.read_text(encoding="utf-8"))
                    email = meta.get("owner_email", "")
                    if email:
                        return email
        except Exception:
            logger.debug("contacts: agent_meta owner_email lookup failed", exc_info=True)

        # Fallback: Google Workspace connected_email (set when user connects Gmail/Calendar).
        try:
            from server.main import app
            sl = getattr(app.state, "skill_loader", None)
            if sl is None:
                return ""
            sk = sl.skills.get("google-workspace")
            if sk and sk.context and hasattr(sk.context, "adapter"):
                adapter = sk.context.adapter
                if hasattr(adapter, "_agent_cfg"):
                    email = adapter._agent_cfg(self._agent_id).get("connected_email", "")
                    if email:
                        return email
                if hasattr(adapter, "_agent_configs"):
                    email = adapter._agent_configs.get(self._agent_id, {}).get("connected_email", "")
                    if email:
                        return email
        except Exception:
            logger.debug("contacts: google workspace email lookup failed", exc_info=True)
        return ""

    def _get_owner_name(self) -> str:
        """Return the owner's display name.

        Resolution order:
        1. ``owner_name`` in agent_meta.json (set from NestJS displayName on every join).
        2. ``User.Name`` fact in the agent's fact store (learned during chat).
        3. First component of the owner's email as a readable heuristic
           (e.g. ``umberto.canessa@gmail.com`` → ``Umberto``).
        """
        # 1. agent_meta.json — most reliable, updated on every WS join
        try:
            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am is not None:
                meta_path = am.agents_dir / self._agent_id / "agent_meta.json"
                if meta_path.exists():
                    import json as _j
                    meta = _j.loads(meta_path.read_text(encoding="utf-8"))
                    name = meta.get("owner_name", "")
                    if name:
                        return name
        except Exception:
            logger.debug("contacts: agent_meta owner_name lookup failed", exc_info=True)

        # 2. Fact store — agent may have learned the name from conversation
        try:
            from server.main import app
            am = getattr(app.state, "agent_manager", None)
            if am is not None:
                rt = am.get_runtime(self._agent_id) if hasattr(am, "get_runtime") else None
                if rt is None:
                    # Try the loaded runtimes dict
                    runtimes = getattr(am, "_runtimes", None) or getattr(am, "_agents", {})
                    rt = runtimes.get(self._agent_id)
                if rt is not None:
                    fs = getattr(rt, "fact_store", None)
                    if fs is not None:
                        db = getattr(fs, "domain_db", None)
                        if db is not None:
                            fact = db.get_fact("User.Name")
                            if fact and fact.current_value:
                                return str(fact.current_value).strip()
        except Exception:
            logger.debug("contacts: fact_store User.Name lookup failed", exc_info=True)

        # 3. Email heuristic — e.g. "umberto.canessa@gmail.com" → "Umberto"
        email = self._get_google_workspace_email()
        if email and "@" in email:
            local = email.split("@")[0]          # "umberto.canessa"
            first = local.split(".")[0]           # "umberto"
            if first.isalpha():
                return first.capitalize()

        return ""

    def _cfg(self, adapter: Any) -> dict:
        if hasattr(adapter, "_agent_cfg"):
            return adapter._agent_cfg(self._agent_id)
        if hasattr(adapter, "_agent_configs"):
            return adapter._agent_configs.get(self._agent_id, {})
        return {}

    def _is_connected(self, adapter: Any) -> bool:
        if hasattr(adapter, "_connected_agents"):
            return self._agent_id in adapter._connected_agents
        return False

    def _gather_contacts(self, channel: str, adapter: Any) -> list[dict]:
        contacts: list[dict] = []
        seen: set[str] = set()

        cfg = self._cfg(adapter)
        owner = cfg.get("owner_identity", "")
        if owner and owner not in seen:
            seen.add(owner)
            owner_name = self._get_owner_name() or "Owner"
            contacts.append({"id": owner, "name": owner_name, "role": "owner"})

        if hasattr(adapter, "get_known_senders"):
            for phone, name in sorted(adapter.get_known_senders(self._agent_id).items()):
                if phone not in seen:
                    seen.add(phone)
                    contacts.append({"id": phone, "name": name or "", "role": "contact"})
        elif hasattr(adapter, "_known_senders"):
            raw = adapter._known_senders.get(self._agent_id, {})
            items = raw.items() if isinstance(raw, dict) else ((p, "") for p in raw)
            for phone, name in sorted(items):
                if phone not in seen:
                    seen.add(phone)
                    contacts.append({"id": phone, "name": name or "", "role": "contact"})

        for entry in cfg.get("allow_from", []):
            norm = str(entry).lstrip("+").replace("-", "").replace(" ", "")
            if norm and norm not in seen:
                seen.add(norm)
                contacts.append({"id": entry, "name": "", "role": "allowlisted"})

        return contacts

    def _gather_groups(self, channel: str, adapter: Any) -> list[dict]:
        if hasattr(adapter, "list_groups"):
            try:
                return adapter.list_groups(self._agent_id)
            except Exception:
                pass
        cfg = self._cfg(adapter)
        groups_cfg = cfg.get("groups", {})
        return [
            {"id": gid, "name": gcfg.get("name", gid)}
            for gid, gcfg in groups_cfg.items()
            if gid != "*" and gid != "__none__"
        ]

    @staticmethod
    def _format_contact(channel: str, contact: dict) -> str:
        cid = contact.get("id", "?")
        name = contact.get("name", "")
        role = contact.get("role", "")
        parts = [cid]
        if name:
            parts.append(f"({name})")
        if role and role != "contact":
            parts.append(f"[{role}]")
        return " ".join(parts)

    @staticmethod
    def _format_stored(c: dict) -> str:
        parts = []
        if c.get("name"):
            parts.append(c["name"])
        if c.get("email"):
            parts.append(f"<{c['email']}>")
        if c.get("phone"):
            parts.append(c["phone"])
        if c.get("tags"):
            parts.append(f"[{', '.join(c['tags'])}]")
        parts.append(f"(id: {c.get('id', '?')})")
        return " ".join(parts)


def _format_age(ts: float) -> str:
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def create_contacts_tool(agent_id: str) -> ContactsTool:
    return ContactsTool(agent_id=agent_id)
