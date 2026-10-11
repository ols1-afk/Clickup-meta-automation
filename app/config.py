"""All settings come from environment variables (see .env.example)."""

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass


class ConfigError(RuntimeError):
    pass


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


def _require(name: str) -> str:
    value = _get(name)
    if not value:
        raise ConfigError(f"Missing required environment variable {name}")
    return value


def _list(name: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in (_get(name) or "").split(",") if v.strip())


@dataclass(frozen=True)
class Config:
    # ClickUp
    clickup_token: str
    clickup_webhook_secret: str | None
    trigger_status: str
    done_status: str | None
    failed_status: str | None
    # ClickUp custom field names (matched case-insensitively)
    field_campaign: str
    field_headline: str
    field_cta: str
    field_link: str
    field_drive: str
    field_primary_text: str
    # Meta
    meta_access_token: str
    meta_ad_account_id: str
    meta_page_id: str
    meta_instagram_user_id: str | None
    meta_api_version: str
    meta_template_adset_id: str
    meta_ad_status: str
    meta_url_tags: str | None
    meta_opt_out_features: tuple[str, ...]  # empty = built-in list
    # Google Drive
    google_service_account_file: str | None
    google_service_account_json: str | None
    google_api_key: str | None

    @classmethod
    def from_env(cls) -> "Config":
        ad_status = (_get("META_AD_STATUS", "PAUSED") or "PAUSED").upper()
        if ad_status not in ("PAUSED", "ACTIVE"):
            raise ConfigError("META_AD_STATUS must be PAUSED or ACTIVE")
        cfg = cls(
            clickup_token=_require("CLICKUP_API_TOKEN"),
            clickup_webhook_secret=_get("CLICKUP_WEBHOOK_SECRET"),
            trigger_status=_get("CLICKUP_TRIGGER_STATUS", "push to facebook"),
            done_status=_get("CLICKUP_DONE_STATUS"),
            failed_status=_get("CLICKUP_FAILED_STATUS"),
            field_campaign=_get("CLICKUP_FIELD_CAMPAIGN", "Campaign"),
            field_headline=_get("CLICKUP_FIELD_HEADLINE", "Headline"),
            field_cta=_get("CLICKUP_FIELD_CTA", "CTA"),
            field_link=_get("CLICKUP_FIELD_LINK", "Link"),
            field_drive=_get("CLICKUP_FIELD_DRIVE", "Drive Link"),
            field_primary_text=_get("CLICKUP_FIELD_PRIMARY_TEXT", "Primary Text"),
            meta_access_token=_require("META_ACCESS_TOKEN"),
            meta_ad_account_id=_require("META_AD_ACCOUNT_ID"),
            meta_page_id=_require("META_PAGE_ID"),
            meta_instagram_user_id=_get("META_INSTAGRAM_USER_ID"),
            meta_api_version=_get("META_API_VERSION", "v24.0"),
            meta_template_adset_id=_require("META_TEMPLATE_ADSET_ID"),
            meta_ad_status=ad_status,
            meta_url_tags=_get("META_URL_TAGS"),
            meta_opt_out_features=_list("META_OPT_OUT_FEATURES"),
            google_service_account_file=_get("GOOGLE_SERVICE_ACCOUNT_FILE"),
            google_service_account_json=_get("GOOGLE_SERVICE_ACCOUNT_JSON"),
            google_api_key=_get("GOOGLE_API_KEY"),
        )
        if not (cfg.google_service_account_file or cfg.google_service_account_json or cfg.google_api_key):
            raise ConfigError(
                "Set GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_API_KEY for Drive access"
            )
        return cfg
