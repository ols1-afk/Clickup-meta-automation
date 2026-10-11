"""ClickUp task -> one Meta ad set with one plain image ad per Drive image."""

import logging
import os
from dataclasses import dataclass, field

from .clickup import AdBrief, BriefError, ClickUpClient, build_brief
from .config import Config
from .drive import DriveClient
from .meta import OPT_OUT_FEATURES, MetaClient, build_link_spec, verify_creative

log = logging.getLogger(__name__)


@dataclass
class AdResult:
    image: str
    ad_name: str
    ad_id: str | None = None
    status: str = ""  # created | skipped | failed
    problems: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class RunResult:
    brief: AdBrief
    campaign_id: str | None = None
    adset_id: str | None = None
    adset_created: bool = False
    warnings: list[str] = field(default_factory=list)
    ads: list[AdResult] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return all(a.status != "failed" and not a.problems for a in self.ads)


def ad_name_for(adset_name: str, image_name: str) -> str:
    return f"{adset_name} - {os.path.splitext(image_name)[0]}"


def run(task_id: str, cfg: Config, clickup: ClickUpClient, drive: DriveClient, meta: MetaClient, dry_run: bool = False) -> RunResult:
    brief = build_brief(clickup.get_task(task_id), cfg)
    result = RunResult(brief=brief, dry_run=dry_run)

    images, skipped = drive.list_images(brief.drive_url)
    if skipped:
        result.warnings.append("Skipped non-image files in Drive: " + ", ".join(skipped))
    if not images:
        raise BriefError("No JPG/PNG/GIF images found at the Drive link.")

    if dry_run:
        result.ads = [AdResult(image=img.name, ad_name=ad_name_for(brief.adset_name, img.name), status="planned") for img in images]
        return result

    campaign = meta.find_campaign(brief.campaign_name)
    result.campaign_id = campaign["id"]
    if campaign.get("smart_promotion_type") == "AUTOMATED_SHOPPING_ADS":
        raise BriefError(f'Campaign "{brief.campaign_name}" is an Advantage+ shopping campaign. Use a manual campaign.')

    adset = meta.find_adset(campaign["id"], brief.adset_name)
    if adset:
        result.adset_id = adset["id"]
    else:
        result.adset_id = meta.copy_adset(cfg.meta_template_adset_id, campaign["id"], brief.adset_name)
        result.adset_created = True
    result.warnings.extend(meta.adset_warnings(result.adset_id))

    existing = meta.ad_names(result.adset_id)
    features = list(cfg.meta_opt_out_features or OPT_OUT_FEATURES)

    for image in images:
        ad = AdResult(image=image.name, ad_name=ad_name_for(brief.adset_name, image.name))
        result.ads.append(ad)
        if ad.ad_name in existing:
            ad.status = "skipped"
            continue
        try:
            image_hash = meta.upload_image(image.name, drive.download(image))
            spec = build_link_spec(cfg.meta_page_id, cfg.meta_instagram_user_id, brief, image_hash)
            creative_id, rejected = meta.create_creative(ad.ad_name, spec, features, cfg.meta_url_tags)
            if rejected:
                log.info("Meta API %s does not know features %s; skipped them", cfg.meta_api_version, rejected)
            ad.problems = verify_creative(meta.get_creative(creative_id), spec)
            ad.ad_id = meta.create_ad(ad.ad_name, result.adset_id, creative_id)
            # Ads are always created paused; only switch on ones that passed the check.
            if cfg.meta_ad_status == "ACTIVE" and not ad.problems:
                meta.set_status(ad.ad_id, "ACTIVE")
            ad.status = "created"
        except Exception as exc:  # keep going with the other images
            log.exception("Ad for %s failed", image.name)
            ad.status = "failed"
            ad.error = str(exc)

    if cfg.meta_ad_status == "ACTIVE" and result.ok and not result.warnings:
        meta.set_status(result.adset_id, "ACTIVE")
    return result


def summary(result: RunResult) -> str:
    b = result.brief
    cta = "No button" if b.cta == "NO_BUTTON" else b.cta.replace("_", " ").title()
    lines = [
        ("DRY RUN - nothing was created in Meta." if result.dry_run else "Meta upload finished." if result.ok else "Meta upload finished WITH PROBLEMS."),
        f"Campaign: {b.campaign_name}" + (f" ({result.campaign_id})" if result.campaign_id else ""),
        f"Ad set: {b.adset_name}" + (f" ({result.adset_id}, {'new' if result.adset_created else 'existing'})" if result.adset_id else ""),
        f"Headline: {b.headline}",
        f"CTA: {cta}",
        f"Link: {b.link}",
        "",
    ]
    for ad in result.ads:
        line = f"- {ad.image}: {ad.status}"
        if ad.ad_id:
            line += f" (ad {ad.ad_id})"
        if ad.problems:
            line += " - left PAUSED, check: " + "; ".join(ad.problems)
        if ad.error:
            line += f" - {ad.error}"
        lines.append(line)
    if result.warnings:
        lines += ["", "Warnings:"] + [f"- {w}" for w in result.warnings]
    return "\n".join(lines)


def process_task(task_id: str, cfg: Config, dry_run: bool = False) -> RunResult | None:
    """Full run with ClickUp feedback (comment + status). Used by the webhook and CLI."""
    clickup = ClickUpClient(cfg.clickup_token)
    drive = DriveClient(cfg.google_service_account_file, cfg.google_service_account_json, cfg.google_api_key)
    meta = MetaClient(cfg.meta_access_token, cfg.meta_ad_account_id, cfg.meta_api_version)
    try:
        result = run(task_id, cfg, clickup, drive, meta, dry_run=dry_run)
    except Exception as exc:
        log.exception("Task %s failed", task_id)
        if not dry_run:
            clickup.comment(task_id, f"Meta upload failed, nothing more was created.\n{exc}")
            if cfg.failed_status:
                clickup.set_status(task_id, cfg.failed_status)
        raise

    text = summary(result)
    log.info("Task %s:\n%s", task_id, text)
    if not dry_run:
        clickup.comment(task_id, text)
        status = cfg.done_status if result.ok else cfg.failed_status
        if status:
            clickup.set_status(task_id, status)
    return result
