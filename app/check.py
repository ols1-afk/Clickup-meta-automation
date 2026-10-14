"""Setup checker: tests every key, ID, status and field and prints a checklist.

Run from GitHub: Actions -> Push to Meta -> Run workflow -> action "check setup".
"""

import os

from .clickup import ClickUpClient, normalize_cta
from .config import Config
from .meta import MetaClient

REQUIRED_ENV = (
    "CLICKUP_API_TOKEN",
    "CLICKUP_TEAM_ID",
    "META_ACCESS_TOKEN",
    "META_AD_ACCOUNT_ID",
    "META_PAGE_ID",
    "META_TEMPLATE_ADSET_ID",
)
META_PERMISSIONS = ("ads_management", "ads_read", "business_management", "pages_read_engagement", "pages_show_list")


class Report:
    def __init__(self):
        self.failures = 0

    def ok(self, msg: str) -> None:
        print(f"  OK    {msg}")

    def fail(self, msg: str) -> None:
        self.failures += 1
        print(f"  FIX   {msg}")

    def note(self, msg: str) -> None:
        print(f"  NOTE  {msg}")

    def section(self, title: str) -> None:
        print(f"\n{title}")


def _check_clickup(cfg: Config, r: Report) -> list[str]:
    """Returns the Campaign dropdown options (to compare with Meta)."""
    r.section("ClickUp")
    client = ClickUpClient(cfg.clickup_token)
    try:
        user = client._call("GET", "/user")["user"]
        r.ok(f"API token works (user: {user.get('username') or user.get('email')})")
    except Exception as exc:
        r.fail(f"CLICKUP_API_TOKEN rejected: {exc}")
        return []

    teams = {t["id"]: t["name"] for t in client._call("GET", "/team").get("teams", [])}
    if cfg.clickup_team_id in teams:
        r.ok(f"Workspace {cfg.clickup_team_id} ({teams[cfg.clickup_team_id]})")
    else:
        r.fail(f"CLICKUP_TEAM_ID {cfg.clickup_team_id!r} not found. Your Workspaces: {teams}")

    if not cfg.clickup_list_ids:
        r.note("Set CLICKUP_LIST_IDS to check the list's statuses and fields too")
        return []

    campaign_options: list[str] = []
    for list_id in cfg.clickup_list_ids:
        try:
            lst = client._call("GET", f"/list/{list_id}")
        except Exception as exc:
            r.fail(f"List {list_id}: {exc}")
            continue
        print(f"  List {list_id} ({lst.get('name')})")
        statuses = {s["status"].lower() for s in lst.get("statuses", [])}
        for status in (cfg.trigger_status, cfg.done_status, cfg.failed_status):
            if status.lower() in statuses:
                r.ok(f"status '{status}'")
            else:
                r.fail(f"status '{status}' missing on the list (has: {', '.join(sorted(statuses))})")

        fields = {f["name"].strip().lower(): f for f in client._call("GET", f"/list/{list_id}/field").get("fields", [])}
        for name in (cfg.field_campaign, cfg.field_headline, cfg.field_cta, cfg.field_link, cfg.field_primary_text):
            field = fields.get(name.lower())
            if not field:
                r.fail(f"field '{name}' missing on the list")
                continue
            r.ok(f"field '{name}' ({field.get('type')})")
            options = [o.get("name", "") for o in (field.get("type_config") or {}).get("options", [])]
            if name == cfg.field_primary_text and field.get("type") == "short_text":
                r.fail(f"'{name}' is Short Text, which drops line breaks. Use a Text field.")
            if name == cfg.field_cta and options:
                if not any(normalize_cta(o) == "NO_BUTTON" for o in options if o.strip()):
                    r.fail(f"CTA dropdown has no 'No button' option (has: {', '.join(options)})")
            if name == cfg.field_campaign:
                campaign_options += options
    return campaign_options


def _check_meta(cfg: Config, r: Report, campaign_options: list[str]) -> None:
    r.section("Meta")
    meta = MetaClient(cfg.meta_access_token, cfg.meta_ad_account_id, cfg.meta_api_version)
    try:
        me = meta.get("me", fields="id,name")
        r.ok(f"access token works ({me.get('name')})")
    except Exception as exc:
        r.fail(f"META_ACCESS_TOKEN rejected: {exc}")
        return

    try:
        granted = {p["permission"] for p in meta.get("me/permissions").get("data", []) if p.get("status") == "granted"}
        missing = [p for p in META_PERMISSIONS if p not in granted]
        if missing:
            r.fail(f"token is missing permissions: {', '.join(missing)} (generate a new token with them ticked)")
        else:
            r.ok("token permissions")
    except Exception as exc:
        r.note(f"couldn't read token permissions: {exc}")

    try:
        acct = meta.get(meta.account, fields="name,account_status,currency")
        if acct.get("account_status") == 1:
            r.ok(f"ad account {meta.account} ({acct.get('name')}, {acct.get('currency')})")
        else:
            r.fail(f"ad account {meta.account} is not active (status {acct.get('account_status')})")
    except Exception as exc:
        r.fail(f"ad account {meta.account}: {exc} (is it assigned to the system user?)")

    try:
        page = meta.get(cfg.meta_page_id, fields="name")
        r.ok(f"Page {cfg.meta_page_id} ({page.get('name')})")
    except Exception as exc:
        r.fail(f"META_PAGE_ID {cfg.meta_page_id}: {exc} (is the Page assigned to the system user?)")

    try:
        tpl = meta.get(cfg.meta_template_adset_id, fields="name,effective_status")
        r.ok(f"template ad set {cfg.meta_template_adset_id} ({tpl.get('name')}, {tpl.get('effective_status')})")
        for warning in meta.adset_warnings(cfg.meta_template_adset_id):
            r.fail(f"template ad set: {warning}")
    except Exception as exc:
        r.fail(f"META_TEMPLATE_ADSET_ID {cfg.meta_template_adset_id}: {exc}")

    if campaign_options:
        try:
            names = {c["name"] for c in meta.get_all(f"{meta.account}/campaigns", fields="name,effective_status", limit=500)
                     if c.get("effective_status") not in ("DELETED", "ARCHIVED")}
            for option in campaign_options:
                if option in names:
                    r.ok(f"campaign '{option}' exists in Meta")
                else:
                    r.fail(f"ClickUp Campaign option '{option}' has no Meta campaign with exactly that name")
        except Exception as exc:
            r.note(f"couldn't list campaigns: {exc}")


def check() -> int:
    r = Report()
    r.section("Settings")
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    for name in REQUIRED_ENV:
        (r.fail if name in missing else r.ok)(f"{name} {'is not set (add it as a repository secret)' if name in missing else 'is set'}")
    if missing:
        print(f"\n{r.failures} thing(s) to fix.")
        return 1

    cfg = Config.from_env()
    campaign_options = _check_clickup(cfg, r)
    _check_meta(cfg, r, campaign_options)
    print("\nAll good." if not r.failures else f"\n{r.failures} thing(s) to fix.")
    return 1 if r.failures else 0
