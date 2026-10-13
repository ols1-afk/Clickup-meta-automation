"""Minimal ClickUp API v2 client plus the task -> ad brief mapping."""

import os
import re
from dataclasses import dataclass

import requests

from .drive import natural_key
from .text import DRIVE_URL_RE, format_primary_text

API = "https://api.clickup.com/api/v2"
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif"}


class ClickUpError(RuntimeError):
    pass


class BriefError(ValueError):
    """The task is missing something we need. The message is shown in ClickUp."""


class ClickUpClient:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": token})

    def _call(self, method: str, path: str, **kwargs) -> dict:
        resp = self.session.request(method, f"{API}{path}", timeout=60, **kwargs)
        if resp.status_code >= 400:
            raise ClickUpError(f"ClickUp {method} {path} failed ({resp.status_code}): {resp.text[:500]}")
        return resp.json() if resp.content else {}

    def get_task(self, task_id: str) -> dict:
        return self._call("GET", f"/task/{task_id}", params={"include_markdown_description": "true"})

    def comment(self, task_id: str, text: str) -> None:
        self._call("POST", f"/task/{task_id}/comment", json={"comment_text": text, "notify_all": False})

    def set_status(self, task_id: str, status: str) -> None:
        self._call("PUT", f"/task/{task_id}", json={"status": status})

    def tasks_in_status(self, team_id: str, status: str, list_ids: tuple[str, ...] = ()) -> list[str]:
        """IDs of every task (and subtask) currently in `status`."""
        ids, page = [], 0
        while True:
            params = [("statuses[]", status.lower()), ("subtasks", "true"), ("page", str(page))]
            params += [("list_ids[]", list_id) for list_id in list_ids]
            batch = self._call("GET", f"/team/{team_id}/task", params=params).get("tasks", [])
            ids += [t["id"] for t in batch]
            if len(batch) < 100:
                return ids
            page += 1

    def download(self, url: str) -> bytes:
        """Download an attachment (works whether or not the Workspace requires auth for files)."""
        resp = requests.get(url, timeout=120)
        if resp.status_code in (401, 403):
            resp = self.session.get(url, timeout=120)
        if resp.status_code >= 400:
            raise ClickUpError(f"Could not download attachment ({resp.status_code}): {url}")
        return resp.content


def image_attachments(task: dict) -> list[dict]:
    """Image files attached to the task, in name order: [{"name", "url"}]."""
    out = []
    for att in task.get("attachments") or []:
        if att.get("deleted") or att.get("is_folder") or not att.get("url"):
            continue
        title = att.get("title") or os.path.basename(att["url"].split("?")[0])
        ext = (att.get("extension") or os.path.splitext(title)[1]).lower().lstrip(".")
        if ext in IMAGE_EXTENSIONS:
            out.append({"name": title, "url": att["url"]})
    return sorted(out, key=lambda a: natural_key(a["name"]))


# --------------------------------------------------------------------------- brief


@dataclass
class AdBrief:
    task_id: str
    adset_name: str
    campaign_name: str
    headline: str
    cta: str  # Meta call_to_action type, e.g. LEARN_MORE or NO_BUTTON
    link: str
    primary_text: str
    drive_url: str | None  # only used when no images are attached to the task


# CTA labels as they appear in Ads Manager -> API enum.
_CTA_ALIASES = {
    "NO_BUTTON": "NO_BUTTON",
    "NO": "NO_BUTTON",
    "NONE": "NO_BUTTON",
    "NO_CTA": "NO_BUTTON",
    "LEARN_MORE": "LEARN_MORE",
    "SHOP_NOW": "SHOP_NOW",
    "SIGN_UP": "SIGN_UP",
    "SUBSCRIBE": "SUBSCRIBE",
    "ORDER_NOW": "ORDER_NOW",
    "BUY_NOW": "BUY_NOW",
    "GET_OFFER": "GET_OFFER",
    "GET_QUOTE": "GET_QUOTE",
    "CONTACT_US": "CONTACT_US",
    "DOWNLOAD": "DOWNLOAD",
    "BOOK_NOW": "BOOK_TRAVEL",
    "BOOK_TRAVEL": "BOOK_TRAVEL",
    "APPLY_NOW": "APPLY_NOW",
    "WATCH_MORE": "WATCH_MORE",
    "SEE_MENU": "SEE_MENU",
    "GET_STARTED": "GET_STARTED",
    "SEND_MESSAGE": "MESSAGE_PAGE",
    "MESSAGE_PAGE": "MESSAGE_PAGE",
}


def normalize_cta(value: str | None) -> str:
    if not value or not value.strip():
        raise BriefError('CTA is empty. Pick a button, or "No button" to run without one.')
    key = re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
    return _CTA_ALIASES.get(key, key)


def _field_value(field: dict):
    """Human-readable value of a ClickUp custom field (dropdowns resolved to the option name)."""
    value = field.get("value")
    if value is None or value == "":
        return None
    if field.get("type") == "drop_down":
        for opt in (field.get("type_config") or {}).get("options", []):
            if value in (opt.get("id"), opt.get("orderindex")) or str(value) == str(opt.get("orderindex")):
                return opt.get("name")
        return None
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return None


def custom_fields(task: dict) -> dict[str, str]:
    out = {}
    for field in task.get("custom_fields") or []:
        value = _field_value(field)
        if value:
            out[field.get("name", "").strip().lower()] = value
    return out


def build_brief(task: dict, cfg) -> AdBrief:
    fields = custom_fields(task)

    def field(name: str) -> str | None:
        return fields.get(name.strip().lower())

    description = task.get("markdown_description") or task.get("text_content") or task.get("description") or ""

    adset_name = (task.get("name") or "").strip()
    campaign = field(cfg.field_campaign)
    headline = field(cfg.field_headline)
    link = field(cfg.field_link)
    cta = normalize_cta(field(cfg.field_cta))

    drive_url = field(cfg.field_drive)
    if not drive_url:
        match = DRIVE_URL_RE.search(description)
        drive_url = match.group(0).rstrip(").,>]") if match else None

    primary_text = format_primary_text(field(cfg.field_primary_text) or description)

    missing = [
        label
        for label, value in (
            ("task title (ad set name)", adset_name),
            (f'"{cfg.field_campaign}"', campaign),
            (f'"{cfg.field_headline}"', headline),
            (f'"{cfg.field_link}"', link),
            (f'"{cfg.field_primary_text}" (or the task description)', primary_text),
        )
        if not value
    ]
    if missing:
        raise BriefError("Missing: " + ", ".join(missing))
    if not re.match(r"https?://", link):
        raise BriefError(f"{cfg.field_link} must start with http:// or https:// (got {link!r})")

    return AdBrief(
        task_id=task["id"],
        adset_name=adset_name,
        campaign_name=campaign,
        headline=headline,
        cta=cta,
        link=link,
        primary_text=primary_text,
        drive_url=drive_url,
    )
