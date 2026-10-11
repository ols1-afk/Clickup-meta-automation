import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from app.clickup import BriefError, build_brief, normalize_cta, status_change_target, verify_signature
from app.drive import DriveFile, natural_key, parse_drive_id
from app.meta import MetaAPIError, MetaClient, build_link_spec, verify_creative
from app.pipeline import run, summary
from app.text import format_primary_text

CFG = SimpleNamespace(
    field_campaign="Campaign",
    field_headline="Headline",
    field_cta="CTA",
    field_link="Link",
    field_drive="Drive Link",
    field_primary_text="Primary Text",
    meta_page_id="PAGE",
    meta_instagram_user_id=None,
    meta_template_adset_id="TEMPLATE",
    meta_ad_status="PAUSED",
    meta_url_tags=None,
    meta_opt_out_features=(),
    meta_api_version="v24.0",
)


def task(**overrides):
    t = {
        "id": "abc123",
        "name": "UK | Native | Hook A",
        "markdown_description": (
            "Tired of bloating?\n"
            "Most people don't realise this.\n\n\n"
            "**Try it today** at [our site](https://example.com/offer)\n"
            "Assets: https://drive.google.com/drive/folders/FOLDER123?usp=sharing\n"
        ),
        "custom_fields": [
            {"name": "Campaign", "type": "short_text", "value": "UK Prospecting"},
            {"name": "Headline", "type": "short_text", "value": "The 10-second fix"},
            {"name": "Link", "type": "url", "value": "https://example.com/offer"},
            {
                "name": "CTA",
                "type": "drop_down",
                "value": 1,
                "type_config": {"options": [
                    {"id": "o0", "name": "Learn More", "orderindex": 0},
                    {"id": "o1", "name": "No button", "orderindex": 1},
                ]},
            },
        ],
    }
    t.update(overrides)
    return t


# ----------------------------------------------------------------- text


def test_every_line_gets_one_blank_line_between():
    raw = "Line one.\nLine two.\n\n\n\nLine three.\r\n   Line four.  "
    assert format_primary_text(raw) == "Line one.\n\nLine two.\n\nLine three.\n\nLine four."


def test_markdown_is_cleaned_and_links_kept():
    raw = "# Big news\n**Bold** and __bold__\n[Click here](https://x.com/a)\n[https://x.com](https://x.com)\nSave 20%\\!"
    assert format_primary_text(raw) == (
        "Big news\n\nBold and bold\n\nClick here https://x.com/a\n\nhttps://x.com\n\nSave 20%!"
    )


def test_drive_link_lines_removed_and_spaces_normalised():
    raw = "Hello  world​\nhttps://drive.google.com/drive/folders/abc\n---\nBye"
    assert format_primary_text(raw) == "Hello world\n\nBye"


# ----------------------------------------------------------------- clickup


def test_build_brief_from_task():
    brief = build_brief(task(), CFG)
    assert brief.adset_name == "UK | Native | Hook A"
    assert brief.campaign_name == "UK Prospecting"
    assert brief.cta == "NO_BUTTON"
    assert brief.drive_url == "https://drive.google.com/drive/folders/FOLDER123?usp=sharing"
    assert brief.primary_text == (
        "Tired of bloating?\n\nMost people don't realise this.\n\nTry it today at our site https://example.com/offer"
    )


def test_build_brief_reports_missing_fields():
    t = task(custom_fields=[{"name": "CTA", "type": "short_text", "value": "Learn more"}])
    with pytest.raises(BriefError, match="Campaign.*Headline.*Link"):
        build_brief(t, CFG)


@pytest.mark.parametrize(
    "label,expected",
    [("Learn More", "LEARN_MORE"), ("shop now", "SHOP_NOW"), ("No button", "NO_BUTTON"), ("None", "NO_BUTTON"),
     ("Book Now", "BOOK_TRAVEL"), ("SIGN_UP", "SIGN_UP")],
)
def test_normalize_cta(label, expected):
    assert normalize_cta(label) == expected


def test_empty_cta_is_an_error():
    with pytest.raises(BriefError):
        normalize_cta("  ")


def test_webhook_helpers():
    body = b'{"event":"taskStatusUpdated"}'
    sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3cret", body, sig)
    assert not verify_signature("s3cret", body, "nope")
    payload = {"event": "taskStatusUpdated", "history_items": [{"field": "status", "after": {"status": "Push to Facebook"}}]}
    assert status_change_target(payload) == "Push to Facebook"
    assert status_change_target({"event": "taskUpdated"}) is None


# ----------------------------------------------------------------- drive


def test_parse_drive_id_and_sorting():
    assert parse_drive_id("https://drive.google.com/drive/folders/1AbC-d_E?usp=sharing") == "1AbC-d_E"
    assert parse_drive_id("https://drive.google.com/file/d/XYZ/view") == "XYZ"
    assert parse_drive_id("https://drive.google.com/open?id=QQ") == "QQ"
    assert sorted(["img 10.png", "img 2.png", "img 1.png"], key=natural_key) == ["img 1.png", "img 2.png", "img 10.png"]


# ----------------------------------------------------------------- meta


def spec_for(cta="LEARN_MORE"):
    brief = SimpleNamespace(link="https://e.com", primary_text="A\n\nB", headline="H", cta=cta)
    return build_link_spec("PAGE", None, brief, "HASH")


def test_no_button_spec_has_no_link_value():
    assert spec_for("NO_BUTTON")["link_data"]["call_to_action"] == {"type": "NO_BUTTON"}
    assert spec_for()["link_data"]["call_to_action"] == {"type": "LEARN_MORE", "value": {"link": "https://e.com"}}


def test_verify_creative_clean_and_dirty():
    spec = spec_for()
    clean = {
        "object_story_spec": spec,
        "degrees_of_freedom_spec": {"creative_features_spec": {"music": {"enroll_status": "OPT_OUT"}}},
    }
    assert verify_creative(clean, spec) == []

    dirty_link = dict(spec["link_data"], message="A B", description="auto text")
    dirty = {
        "object_story_spec": {"page_id": "PAGE", "link_data": dirty_link},
        "degrees_of_freedom_spec": {"creative_features_spec": {"music": {"enroll_status": "OPT_IN"}}},
        "asset_feed_spec": {"bodies": []},
        "contextual_multi_ads": {"enroll_status": "OPT_IN"},
    }
    problems = " | ".join(verify_creative(dirty, spec))
    for needle in ("asset feed", "music", "multi-advertiser", "primary text", "description"):
        assert needle in problems


def test_create_creative_drops_unknown_feature_keys():
    client = MetaClient("t", "123", "v24.0", session=object())
    calls = []

    def fake_post(path, files=None, **params):
        feats = params["degrees_of_freedom_spec"]["creative_features_spec"]
        calls.append(set(feats))
        if "music" in feats:
            raise MetaAPIError({"message": "Invalid parameter: creative_features_spec[music] is not supported"}, 400)
        return {"id": "CR1"}

    client.post = fake_post
    creative_id, rejected = client.create_creative("ad", spec_for(), ["image_touchups", "music"])
    assert (creative_id, rejected) == ("CR1", ["music"])
    assert calls[-1] == {"image_touchups"}


# ----------------------------------------------------------------- pipeline


class FakeClickUp:
    def get_task(self, task_id):
        return task()


class FakeDrive:
    def list_images(self, url):
        return [DriveFile("1", "Image 1.jpg", "image/jpeg"), DriveFile("2", "Image 2.png", "image/png")], ["brief.pdf"]

    def download(self, f):
        return b"bytes-" + f.id.encode()


class FakeMeta:
    def __init__(self, existing_ads=()):
        self.existing_ads = set(existing_ads)
        self.created_ads = []
        self.creatives = {}

    def find_campaign(self, name):
        return {"id": "C1", "name": name}

    def find_adset(self, campaign_id, name):
        return None

    def copy_adset(self, template, campaign_id, name):
        assert (template, campaign_id) == ("TEMPLATE", "C1")
        return "AS1"

    def adset_warnings(self, adset_id):
        return []

    def ad_names(self, adset_id):
        return self.existing_ads

    def upload_image(self, name, data):
        return "hash-" + name

    def create_creative(self, name, spec, features, url_tags=None):
        assert "music" in features and "image_touchups" in features
        cid = f"CR{len(self.creatives)}"
        self.creatives[cid] = {"object_story_spec": json.loads(json.dumps(spec))}
        return cid, []

    def get_creative(self, cid):
        return self.creatives[cid]

    def create_ad(self, name, adset_id, creative_id):
        self.created_ads.append((name, adset_id, creative_id))
        return f"AD{len(self.created_ads)}"


def test_pipeline_one_ad_per_image_same_copy():
    meta = FakeMeta(existing_ads={"UK | Native | Hook A - Image 1"})
    result = run("abc123", CFG, FakeClickUp(), FakeDrive(), meta)

    assert result.ok and result.adset_id == "AS1" and result.adset_created
    assert [(a.image, a.status) for a in result.ads] == [("Image 1.jpg", "skipped"), ("Image 2.png", "created")]
    assert meta.created_ads == [("UK | Native | Hook A - Image 2", "AS1", "CR0")]
    link_data = meta.creatives["CR0"]["object_story_spec"]["link_data"]
    assert link_data["image_hash"] == "hash-Image 2.png"
    assert link_data["call_to_action"] == {"type": "NO_BUTTON"}
    assert "brief.pdf" in summary(result)


def test_pipeline_dry_run_touches_nothing_in_meta():
    result = run("abc123", CFG, FakeClickUp(), FakeDrive(), meta=None, dry_run=True)
    assert [a.status for a in result.ads] == ["planned", "planned"]
