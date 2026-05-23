"""Calendar tools -- list, create, update events.

Write operations (create, update) support a confirmation gate.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from nls.tools.agent_tools.base import ToolResult

logger = logging.getLogger(__name__)


_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


async def _get_user_timezone(service: Any) -> str | None:
    """Fetch the user's IANA timezone from Google Calendar settings."""
    try:
        setting = await asyncio.to_thread(
            lambda: service.settings().get(setting="timezone").execute()
        )
        return setting.get("value")
    except Exception:
        return None


def _human_dt(iso_str: str, user_tz: str | None = None) -> str:
    """Turn an ISO-8601 datetime/date string into 'DayName, YYYY-MM-DD HH:MM (tz)'.

    If *user_tz* is a valid IANA timezone (e.g. "Europe/Amsterdam"), the
    datetime is converted to that timezone before formatting.
    """
    try:
        if "T" in iso_str:
            dt = datetime.fromisoformat(iso_str)
            if user_tz:
                try:
                    dt = dt.astimezone(ZoneInfo(user_tz))
                except Exception:
                    pass
            day = _DAY_NAMES[dt.weekday()]
            tz_label = user_tz if user_tz else (dt.strftime("%z") or "UTC")
            return f"{day}, {dt.strftime('%Y-%m-%d %H:%M')} ({tz_label})"
        dt = datetime.fromisoformat(iso_str)
        day = _DAY_NAMES[dt.weekday()]
        return f"{day}, {iso_str} (all day)"
    except Exception:
        return iso_str


def _not_connected() -> ToolResult:
    return ToolResult(
        content="Error: Google account not connected. Use google_workspace_connect first.",
        is_error=True,
    )


async def _resolve_calendar_id(
    service: Any, calendar_id: str,
) -> tuple[str | None, str | None]:
    """Resolve a human-readable calendar name to a Google Calendar ID.

    Returns (resolved_id, error_message).  If the input already looks
    like a proper calendar ID (contains '@' or is 'primary'), it is
    returned as-is.
    """
    if not calendar_id or calendar_id == "primary" or "@" in calendar_id:
        return calendar_id or "primary", None

    try:
        cal_list = await asyncio.to_thread(
            lambda: service.calendarList().list().execute()
        )
        calendars = cal_list.get("items", [])
        query = calendar_id.lower().strip()

        for cal in calendars:
            name = (cal.get("summary") or "").lower()
            if name == query:
                return cal["id"], None

        for cal in calendars:
            name = (cal.get("summary") or "").lower()
            if query in name or name in query:
                return cal["id"], None

        available = [
            f"  - {c.get('summary', '?')} (id: {c['id']})"
            for c in calendars
        ]
        return None, (
            f"No calendar matching '{calendar_id}' found.\n"
            f"Available calendars:\n" + "\n".join(available)
        )
    except Exception as exc:
        return None, f"Failed to list calendars for name resolution: {exc}"


class CalendarListTool:
    """List upcoming calendar events."""

    def __init__(self, adapter: Any, agent_id: str) -> None:
        self._adapter = adapter
        self._agent_id = agent_id

    @property
    def name(self) -> str:
        return "calendar_list"

    @property
    def description(self) -> str:
        return (
            "List upcoming Google Calendar events. Returns title, time, "
            "location, attendees for events in the specified time range. "
            "You can pass a human-readable calendar name (e.g. 'Famiglia') "
            "and it will be resolved automatically. Use "
            "calendar_id='discover' to list all available calendars. "
            "NOTE: Calendar data reflects what the user entered — it is "
            "not a source of verified facts (addresses, phone numbers). "
            "Use web_search to look up real-world details."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days ahead to look (default 7, max 30)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum events to return (default 10, max 50)",
                },
                "calendar_id": {
                    "type": "string",
                    "description": (
                        "Calendar ID, human-readable name, or 'discover' to "
                        "list all available calendars. Default: 'primary'"
                    ),
                },
            },
        }

    async def _discover_calendars(self, service: Any) -> ToolResult:
        """List all calendars the user has access to."""
        try:
            cal_list = await asyncio.to_thread(
                lambda: service.calendarList().list().execute()
            )
            calendars = cal_list.get("items", [])
            if not calendars:
                return ToolResult(content="No calendars found.")

            lines: list[str] = []
            for cal in calendars:
                name = cal.get("summary", "(untitled)")
                cal_id = cal.get("id", "?")
                role = cal.get("accessRole", "?")
                primary = " (primary)" if cal.get("primary") else ""
                lines.append(f"- **{name}**{primary}\n  ID: {cal_id}\n  Role: {role}")

            return ToolResult(
                content=f"Available calendars ({len(calendars)}):\n\n"
                + "\n\n".join(lines),
                details={"count": len(calendars)},
            )
        except Exception as exc:
            return ToolResult(
                content=f"Failed to list calendars: {exc}", is_error=True,
            )

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        days = min(params.get("days", 7), 30)
        max_results = min(params.get("max_results", 10), 50)
        calendar_id = params.get("calendar_id", "primary")

        try:
            service = await asyncio.to_thread(flow.build_service, "calendar", "v3")
        except Exception as exc:
            return ToolResult(content=f"Calendar service error: {exc}", is_error=True)

        if calendar_id == "discover":
            return await self._discover_calendars(service)

        resolved_id, resolve_err = await _resolve_calendar_id(
            service, calendar_id,
        )
        if resolve_err:
            return ToolResult(content=resolve_err, is_error=True)

        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days)).isoformat()

        user_tz = await _get_user_timezone(service)

        try:
            results = await asyncio.to_thread(
                lambda: service.events().list(
                    calendarId=resolved_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=max_results,
                ).execute()
            )
            events = results.get("items", [])
            cal_label = (
                f" ({calendar_id})" if calendar_id != "primary" else ""
            )
            if not events:
                return ToolResult(
                    content=f"No events{cal_label} in the next {days} day(s).",
                )

            lines: list[str] = []
            for ev in events:
                start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "?"))
                end = ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", ""))
                summary = ev.get("summary", "(no title)")
                location = ev.get("location", "")
                attendees = [
                    a.get("email", "") for a in ev.get("attendees", [])
                ]
                event_id = ev.get("id", "")

                entry = f"- **{summary}**\n  Start: {_human_dt(start, user_tz)}"
                if end:
                    entry += f" | End: {_human_dt(end, user_tz)}"
                if location:
                    entry += f"\n  Location: {location}"
                if attendees:
                    entry += f"\n  Attendees: {', '.join(attendees[:5])}"
                entry += f"\n  Event ID: {event_id}"
                lines.append(entry)

            return ToolResult(
                content=f"Upcoming events{cal_label} ({len(events)}):\n\n"
                + "\n\n".join(lines),
                details={"count": len(events)},
            )
        except Exception as exc:
            return ToolResult(content=f"Calendar list failed: {exc}", is_error=True)


class CalendarCreateTool:
    """Create a new calendar event."""

    def __init__(self, adapter: Any, agent_id: str, require_confirmation: bool = True) -> None:
        self._adapter = adapter
        self._agent_id = agent_id
        self._require_confirmation = require_confirmation

    @property
    def name(self) -> str:
        return "calendar_create"

    @property
    def description(self) -> str:
        return (
            "Create a new Google Calendar event. Supports title, start/end times, "
            "location, description, and attendees. Use calendar_id to target a "
            "specific calendar (e.g. 'Famiglia'); defaults to primary. "
            "If confirmation required, first call without confirmed=true to preview."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
                "start": {
                    "type": "string",
                    "description": "Start time in ISO 8601 format (e.g. '2026-03-01T10:00:00')",
                },
                "end": {
                    "type": "string",
                    "description": "End time in ISO 8601 format",
                },
                "location": {"type": "string", "description": "Event location"},
                "description": {"type": "string", "description": "Event description/notes"},
                "attendees": {
                    "type": "string",
                    "description": "Attendee emails (comma-separated)",
                },
                "timezone": {
                    "type": "string",
                    "description": "Timezone (e.g. 'America/New_York', default: UTC)",
                },
                "calendar_id": {
                    "type": "string",
                    "description": (
                        "Target calendar — human-readable name (e.g. 'Famiglia') "
                        "or calendar ID. Default: 'primary'"
                    ),
                },
                "recurrence": {
                    "type": "string",
                    "description": (
                        "Recurrence rule in RFC 5545 format "
                        "(e.g. 'RRULE:FREQ=WEEKLY;BYDAY=MO' for every Monday). "
                        "Omit for a one-time event."
                    ),
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set to true to create after reviewing the draft",
                },
            },
            "required": ["summary", "start"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        summary = params.get("summary", "")
        start = params.get("start", "")
        end = params.get("end", "")
        location = params.get("location", "")
        description = params.get("description", "")
        attendees_str = params.get("attendees", "")
        timezone = params.get("timezone", "UTC")
        confirmed = params.get("confirmed", False)

        if not end:
            try:
                dt = datetime.fromisoformat(start)
                end = (dt + timedelta(hours=1)).isoformat()
            except Exception:
                end = start

        calendar_id = params.get("calendar_id", "primary")
        recurrence = params.get("recurrence", "")

        if self._require_confirmation and not confirmed:
            try:
                _draft_service = await asyncio.to_thread(
                    flow.build_service, "calendar", "v3",
                )
                _draft_tz = await _get_user_timezone(_draft_service)
            except Exception:
                _draft_tz = None
            draft = (
                f"Title: {summary}\n"
                f"Start: {_human_dt(start, _draft_tz)}\n"
                f"End: {_human_dt(end, _draft_tz)}"
            )
            if calendar_id != "primary":
                draft += f"\nCalendar: {calendar_id}"
            if location:
                draft += f"\nLocation: {location}"
            if description:
                draft += f"\nDescription: {description}"
            if attendees_str:
                draft += f"\nAttendees: {attendees_str}"
            if recurrence:
                draft += f"\nRecurrence: {recurrence}"
            return ToolResult(
                content=(
                    "**Draft calendar event for review:**\n\n"
                    f"```\n{draft}\n```\n\n"
                    "Present this to the user. If approved, call "
                    "calendar_create with the same parameters and confirmed=true."
                ),
                details={"draft": True, "needs_confirmation": True},
            )

        event_body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
        }
        if location:
            event_body["location"] = location
        if description:
            event_body["description"] = description
        if attendees_str:
            event_body["attendees"] = [
                {"email": e.strip()} for e in attendees_str.split(",") if e.strip()
            ]
        if recurrence:
            rule = recurrence if recurrence.startswith("RRULE:") else f"RRULE:{recurrence}"
            event_body["recurrence"] = [rule]

        try:
            service = await asyncio.to_thread(flow.build_service, "calendar", "v3")

            resolved_id, resolve_err = await _resolve_calendar_id(service, calendar_id)
            if resolve_err:
                return ToolResult(content=resolve_err, is_error=True)

            event = await asyncio.to_thread(
                lambda: service.events().insert(
                    calendarId=resolved_id, body=event_body,
                ).execute()
            )
            self._adapter.audit(
                self._agent_id, "calendar_create",
                event_id=event.get("id", ""), summary=summary,
                start=start, end=end, attendees=attendees_str or None,
            )
            return ToolResult(
                content=f"Event created: {event.get('summary', summary)}\nLink: {event.get('htmlLink', '')}",
                details={"event_id": event.get("id", "")},
            )
        except Exception as exc:
            return ToolResult(content=f"Failed to create event: {exc}", is_error=True)


class CalendarUpdateTool:
    """Update or cancel an existing calendar event."""

    def __init__(self, adapter: Any, agent_id: str, require_confirmation: bool = True) -> None:
        self._adapter = adapter
        self._agent_id = agent_id
        self._require_confirmation = require_confirmation

    @property
    def name(self) -> str:
        return "calendar_update"

    @property
    def description(self) -> str:
        return (
            "Update or cancel a Google Calendar event. "
            "Provide the event_id and fields to change. "
            "Set action='cancel' to delete the event. "
            "Use calendar_id if the event is on a non-primary calendar."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID to update"},
                "action": {
                    "type": "string",
                    "enum": ["update", "cancel"],
                    "description": "Update fields or cancel the event entirely",
                },
                "summary": {"type": "string", "description": "New event title"},
                "start": {"type": "string", "description": "New start time (ISO 8601)"},
                "end": {"type": "string", "description": "New end time (ISO 8601)"},
                "location": {"type": "string", "description": "New location"},
                "description": {"type": "string", "description": "New description"},
                "timezone": {"type": "string", "description": "Timezone (default: UTC)"},
                "calendar_id": {
                    "type": "string",
                    "description": (
                        "Calendar the event belongs to — human-readable name "
                        "or calendar ID. Default: 'primary'"
                    ),
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set to true to execute after reviewing",
                },
            },
            "required": ["event_id"],
        }

    async def execute(self, params: dict[str, Any], signal: Any = None) -> ToolResult:
        flow = self._adapter.get_oauth_flow(self._agent_id)
        if not flow or not flow.is_authenticated:
            return _not_connected()

        event_id = params.get("event_id", "")
        action = params.get("action", "update")
        confirmed = params.get("confirmed", False)
        calendar_id = params.get("calendar_id", "primary")

        if action == "cancel":
            if self._require_confirmation and not confirmed:
                cal_note = f" (calendar: {calendar_id})" if calendar_id != "primary" else ""
                return ToolResult(
                    content=(
                        f"**Cancel event {event_id}{cal_note}?**\n\n"
                        "Present this to the user. If approved, call "
                        "calendar_update with action='cancel' and confirmed=true."
                    ),
                    details={"draft": True, "needs_confirmation": True},
                )
            try:
                service = await asyncio.to_thread(flow.build_service, "calendar", "v3")
                resolved_id, resolve_err = await _resolve_calendar_id(service, calendar_id)
                if resolve_err:
                    return ToolResult(content=resolve_err, is_error=True)
                await asyncio.to_thread(
                    lambda: service.events().delete(
                        calendarId=resolved_id, eventId=event_id,
                    ).execute()
                )
                self._adapter.audit(
                    self._agent_id, "calendar_cancel", event_id=event_id,
                )
                return ToolResult(content=f"Event {event_id} cancelled.")
            except Exception as exc:
                return ToolResult(content=f"Calendar cancel failed: {exc}", is_error=True)

        # Draft preview -- no API call needed, just show the proposed changes
        if self._require_confirmation and not confirmed:
            changes = []
            for field in ("summary", "start", "end", "location", "description"):
                if params.get(field):
                    changes.append(f"{field}: {params[field]}")
            if not changes:
                return ToolResult(
                    content="No fields to update. Provide summary, start, end, location, or description.",
                    is_error=True,
                )
            return ToolResult(
                content=(
                    f"**Update event {event_id}:**\n\n"
                    + "\n".join(changes) + "\n\n"
                    "Present this to the user. If approved, call "
                    "calendar_update with the same parameters and confirmed=true."
                ),
                details={"draft": True, "needs_confirmation": True},
            )

        try:
            service = await asyncio.to_thread(flow.build_service, "calendar", "v3")

            resolved_id, resolve_err = await _resolve_calendar_id(service, calendar_id)
            if resolve_err:
                return ToolResult(content=resolve_err, is_error=True)

            event = await asyncio.to_thread(
                lambda: service.events().get(
                    calendarId=resolved_id, eventId=event_id,
                ).execute()
            )

            tz = params.get("timezone", "UTC")
            if params.get("summary"):
                event["summary"] = params["summary"]
            if params.get("start"):
                event["start"] = {"dateTime": params["start"], "timeZone": tz}
            if params.get("end"):
                event["end"] = {"dateTime": params["end"], "timeZone": tz}
            if params.get("location"):
                event["location"] = params["location"]
            if params.get("description"):
                event["description"] = params["description"]

            updated = await asyncio.to_thread(
                lambda: service.events().update(
                    calendarId=resolved_id, eventId=event_id, body=event,
                ).execute()
            )
            changed = {f: params[f] for f in ("summary", "start", "end", "location", "description") if params.get(f)}
            self._adapter.audit(
                self._agent_id, "calendar_update",
                event_id=event_id, changes=changed,
            )
            return ToolResult(
                content=f"Event updated: {updated.get('summary', '')}\nLink: {updated.get('htmlLink', '')}",
            )
        except Exception as exc:
            return ToolResult(content=f"Calendar update failed: {exc}", is_error=True)
