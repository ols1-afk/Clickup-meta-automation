"""ClickUp task -> one Meta ad set with one plain image ad per attached image."""

import logging
import os
from dataclasses import dataclass, field
from typing import Callable

from .clickup import AdBrief, BriefError, ClickUpClient, build_brief, image_attachments
from .config import Config
from .drive import DriveClient
from .meta import OPT_OUT_FEATURES, MetaClient, build_link_spec, verify_creative

log = logging.getLogger(__name__)


@dataclass
class Image:
    name: str
    load: Callable[[], bytes]


@dataclass
class AdResult:
    image: str
    ad_name: str
    ad_id: str | None = None
    status: str = ""  # planned | created | skipped | failed
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


def ad_names_for(adset_name: str, image_names: list[str]) -> list[str]:
    """`<ad set> - <file name>`, made unique if two files share a name."""
    names, seen = [], {}
    for image_name in image_names:
        name = f"{adset_name} - {os.path.splitext(image_name)[0]}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return names


def collect_images(task: dict, brief: AdBrief, clickup: ClickUpClient, drive: DriveClient | None) -> tuple[list[Image], list[str]]:
    """Images attached to the task; falls back to a Drive link when nothing is attached."""
    attached = image_attachments(task)
    if attached:
        return [Image(a["name"], lambda url=a["url"]: clickup.download(url)) for a in attached], []
    if brief.drive_url and drive is not None:
        files, skipped = drive.list_images(brief.drive_url)
        warnings = ["Skipped non-image files in Drive: " + ", ".join(skipped)] if skipped else []
        return [Image(f.name, lambda f=f: drive.download(f)) for f in files], warnings
    raise BriefError("No images found. Attach the JPG/PNG images to the task.")


def run(task_id: str, cfg: Config, clickup: ClickUpClient, drive: DriveClient | None, meta: MetaClient, dry_run: bool = False) -> RunResult:
    task = clickup.get_task(task_id)
    brief = build_brief(task, cfg)
    result = RunResult(brief=brief, dry_run=dry_run)

    images, warnings = collect_images(task, brief, clickup, drive)
    result.warnings.extend(warnings)
    if not images:
        raise BriefError("No JPG/PNG/GIF images found.")
    names = ad_names_for(brief.adset_name, [img.name for img in images])

    if dry_run:
        result.ads = [AdResult(image=img.name, ad_name=name, status="planned") for img, name in zip(images, names)]
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

    for image, name in zip(images, names):
        ad = AdResult(image=image.name, ad_name=name)
        result.ads.append(ad)
        if ad.ad_name in existing:
            ad.status = "skipped"
            continue
        try:
            image_hash = meta.upload_image(image.name, image.load())
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


def clients(cfg: Config) -> tuple[ClickUpClient, DriveClient | None, MetaClient]:
    drive = None
    if cfg.google_service_account_file or cfg.google_service_account_json or cfg.google_api_key:
        drive = DriveClient(cfg.google_service_account_file, cfg.google_service_account_json, cfg.google_api_key)
    meta = MetaClient(cfg.meta_access_token, cfg.meta_ad_account_id, cfg.meta_api_version)
    return ClickUpClient(cfg.clickup_token), drive, meta


def process_task(task_id: str, cfg: Config, dry_run: bool = False) -> RunResult:
    """Full run with ClickUp feedback: a summary comment, then the done/failed status."""
    clickup, drive, meta = clients(cfg)
    try:
        result = run(task_id, cfg, clickup, drive, meta, dry_run=dry_run)
    except Exception as exc:
        log.exception("Task %s failed", task_id)
        if not dry_run:
            clickup.comment(task_id, f"Meta upload failed, nothing more was created.\n{exc}")
            clickup.set_status(task_id, cfg.failed_status)
        raise

    log.info("Task %s:\n%s", task_id, summary(result))
    if not dry_run:
        clickup.comment(task_id, summary(result))
        clickup.set_status(task_id, cfg.done_status if result.ok else cfg.failed_status)
    return result
