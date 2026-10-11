"""Meta Marketing API client: campaign lookup, ad set copy, plain image ads.

Everything Meta can add on top of an ad is explicitly switched off when the
creative is created, then read back and checked (see verify_creative).
"""

import json
import re

import requests

# Advantage+ creative enhancements, each switched off individually (the
# "standard enhancements" bundle was split up in API v22). Covers touch-ups,
# overlays, music, animation, text variations, comments, site links/highlights,
# product/catalog extensions, etc. Keys the API version doesn't know are dropped
# automatically (and reported). Anything Meta still switches on is caught by
# verify_creative, which reads every ad back after creation.
OPT_OUT_FEATURES = (
    "image_touchups",
    "image_template",
    "image_brightness_and_contrast",
    "image_uncrop",
    "image_background_gen",
    "image_animation",
    "image_auto_crop",
    "adapt_to_placement",
    "add_text_overlay",
    "text_optimizations",
    "text_translation",
    "description_automation",
    "enhance_cta",
    "inline_comment",
    "music",
    "video_auto_crop",
    "ig_video_native_subtitle",
    "multi_photo_to_video",
    "media_type_automation",
    "cv_transformation",
    "reveal_details_over_time",
    "show_summary",
    "show_destination_blurbs",
    "site_extensions",
    "product_extensions",
    "product_metadata_automation",
    "catalog_feed_tag",
    "local_store_extension",
    "profile_card",
    "advantage_plus_creative",
    "hide_price",
)

# Feature keys the API rejected earlier in this process; skipped on later ads.
_REJECTED_FEATURES: set[str] = set()


class MetaAPIError(RuntimeError):
    def __init__(self, error: dict, status: int):
        self.error = error or {}
        self.status = status
        parts = [self.error.get("message", "Unknown Meta API error")]
        for key in ("error_user_title", "error_user_msg"):
            if self.error.get(key):
                parts.append(self.error[key])
        super().__init__(" | ".join(parts) + f" (HTTP {status})")


def _encode(params: dict) -> dict:
    return {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items() if v is not None}


class MetaClient:
    def __init__(self, access_token: str, ad_account_id: str, api_version: str, session: requests.Session | None = None):
        self.token = access_token
        self.account = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        self.base = f"https://graph.facebook.com/{api_version}"
        self.session = session or requests.Session()

    # -------------------------------------------------------------- transport

    def _request(self, method: str, path: str, params: dict | None = None, files: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"
        params = _encode(params or {})
        if method == "GET":
            resp = self.session.get(url, params={**params, "access_token": self.token}, timeout=120)
        else:
            resp = self.session.post(url, data={**params, "access_token": self.token}, files=files, timeout=300)
        try:
            body = resp.json()
        except ValueError:
            body = {"error": {"message": resp.text[:500]}}
        if resp.status_code >= 400 or (isinstance(body, dict) and "error" in body):
            raise MetaAPIError(body.get("error", {}), resp.status_code)
        return body

    def get(self, path: str, **params) -> dict:
        return self._request("GET", path, params)

    def post(self, path: str, files: dict | None = None, **params) -> dict:
        return self._request("POST", path, params, files)

    def get_all(self, path: str, **params) -> list[dict]:
        out, page = [], self.get(path, **params)
        while True:
            out.extend(page.get("data", []))
            nxt = (page.get("paging") or {}).get("next")
            if not nxt:
                return out
            resp = self.session.get(nxt, timeout=120)
            page = resp.json()
            if "error" in page:
                raise MetaAPIError(page["error"], resp.status_code)

    # -------------------------------------------------------------- lookups

    def _by_name(self, path: str, name: str, fields: str) -> list[dict]:
        rows = self.get_all(
            path,
            fields=fields,
            filtering=[{"field": "name", "operator": "EQUAL", "value": name}],
            limit=100,
        )
        return [r for r in rows if r.get("name") == name and r.get("effective_status") not in ("DELETED", "ARCHIVED")]

    def find_campaign(self, name: str) -> dict:
        rows = self._by_name(f"{self.account}/campaigns", name, "id,name,effective_status,smart_promotion_type")
        if not rows:
            raise ValueError(f'No campaign named "{name}" in {self.account}. Create it in Ads Manager first.')
        if len(rows) > 1:
            ids = ", ".join(r["id"] for r in rows)
            raise ValueError(f'More than one campaign is named "{name}" ({ids}). Rename one so the match is exact.')
        return rows[0]

    def find_adset(self, campaign_id: str, name: str) -> dict | None:
        rows = self._by_name(f"{campaign_id}/adsets", name, "id,name,effective_status")
        return rows[0] if rows else None

    def ad_names(self, adset_id: str) -> set[str]:
        rows = self.get_all(f"{adset_id}/ads", fields="id,name,effective_status", limit=500)
        return {r["name"] for r in rows if r.get("effective_status") not in ("DELETED", "ARCHIVED")}

    # -------------------------------------------------------------- ad set

    def copy_adset(self, template_id: str, campaign_id: str, name: str) -> str:
        """Copy the template ad set (targeting, budget, placements, pixel) into the campaign."""
        res = self.post(
            f"{template_id}/copies",
            campaign_id=campaign_id,
            deep_copy="false",
            status_option="PAUSED",
        )
        adset_id = res.get("copied_adset_id") or (res.get("ad_object_ids") or [{}])[0].get("copied_id")
        if not adset_id:
            raise MetaAPIError({"message": f"Unexpected ad set copy response: {res}"}, 200)

        update = {"name": name}
        targeting = self.get(adset_id, fields="targeting").get("targeting")
        if targeting is not None:
            # Advantage+ audience off: use exactly the template's audience.
            targeting["targeting_automation"] = {"advantage_audience": 0}
            update["targeting"] = targeting
        self.post(adset_id, **update)
        return adset_id

    def adset_warnings(self, adset_id: str) -> list[str]:
        info = self.get(adset_id, fields="is_dynamic_creative,targeting")
        warnings = []
        if info.get("is_dynamic_creative"):
            warnings.append("Ad set has Dynamic Creative switched on (fix the template ad set).")
        targeting = info.get("targeting") or {}
        if (targeting.get("targeting_automation") or {}).get("advantage_audience") == 1:
            warnings.append("Ad set has Advantage+ audience switched on.")
        if not targeting.get("publisher_platforms"):
            warnings.append("Ad set uses Advantage+ placements (set manual placements on the template ad set).")
        return warnings

    def set_status(self, object_id: str, status: str) -> None:
        self.post(object_id, status=status)

    # -------------------------------------------------------------- ads

    def upload_image(self, filename: str, data: bytes) -> str:
        res = self.post(f"{self.account}/adimages", files={"filename": (filename, data)})
        images = res.get("images") or {}
        for image in images.values():
            if image.get("hash"):
                return image["hash"]
        raise MetaAPIError({"message": f"Image upload returned no hash: {res}"}, 200)

    def create_creative(
        self, name: str, spec: dict, features: list[str], url_tags: str | None = None
    ) -> tuple[str, list[str]]:
        """Create the creative with every enhancement opted out.

        Returns (creative_id, feature keys this API version rejected).
        """
        features = [f for f in dict.fromkeys(features) if f not in _REJECTED_FEATURES]
        rejected: list[str] = []
        while True:
            try:
                res = self.post(
                    f"{self.account}/adcreatives",
                    name=name,
                    object_story_spec=spec,
                    degrees_of_freedom_spec={
                        "creative_features_spec": {f: {"enroll_status": "OPT_OUT"} for f in features}
                    },
                    contextual_multi_ads={"enroll_status": "OPT_OUT"},
                    url_tags=url_tags,
                )
                return res["id"], rejected
            except MetaAPIError as exc:
                text = f"{exc.error.get('message', '')} {exc.error.get('error_user_msg', '')}"
                bad = [f for f in features if re.search(rf"\b{re.escape(f)}\b", text)]
                if not bad:
                    raise
                rejected.extend(bad)
                _REJECTED_FEATURES.update(bad)
                features = [f for f in features if f not in bad]

    def get_creative(self, creative_id: str) -> dict:
        return self.get(
            creative_id,
            fields="object_story_spec,degrees_of_freedom_spec,asset_feed_spec,contextual_multi_ads,product_set_id,template_url_spec",
        )

    def create_ad(self, name: str, adset_id: str, creative_id: str) -> str:
        res = self.post(
            f"{self.account}/ads",
            name=name,
            adset_id=adset_id,
            creative={"creative_id": creative_id},
            status="PAUSED",
        )
        return res["id"]


def build_link_spec(page_id: str, instagram_user_id: str | None, brief, image_hash: str) -> dict:
    link_data = {
        "link": brief.link,
        "message": brief.primary_text,
        "name": brief.headline,
        "image_hash": image_hash,
    }
    if brief.cta == "NO_BUTTON":
        link_data["call_to_action"] = {"type": "NO_BUTTON"}
    else:
        link_data["call_to_action"] = {"type": brief.cta, "value": {"link": brief.link}}
    spec = {"page_id": page_id, "link_data": link_data}
    if instagram_user_id:
        spec["instagram_user_id"] = instagram_user_id
    return spec


def verify_creative(creative: dict, expected_spec: dict) -> list[str]:
    """Return problems found on a created creative. Empty list = clean, plain ad."""
    problems = []
    if creative.get("asset_feed_spec"):
        problems.append("has an asset feed (dynamic/Advantage+ creative variations)")
    if creative.get("product_set_id") or creative.get("template_url_spec"):
        problems.append("is linked to a catalog/product set")

    features = ((creative.get("degrees_of_freedom_spec") or {}).get("creative_features_spec")) or {}
    opted_in = sorted(k for k, v in features.items() if (v or {}).get("enroll_status") == "OPT_IN")
    if opted_in:
        problems.append("enhancements switched on: " + ", ".join(opted_in))
    if (creative.get("contextual_multi_ads") or {}).get("enroll_status") == "OPT_IN":
        problems.append("multi-advertiser ads switched on")

    actual = (creative.get("object_story_spec") or {}).get("link_data") or {}
    want = expected_spec["link_data"]
    if actual.get("message") != want["message"]:
        problems.append("primary text differs from the brief")
    if actual.get("name") != want["name"]:
        problems.append("headline differs from the brief")
    if actual.get("link") != want["link"]:
        problems.append("destination link differs from the brief")
    if actual.get("image_hash") and actual["image_hash"] != want["image_hash"]:
        problems.append("image differs from the uploaded one")
    actual_cta = (actual.get("call_to_action") or {}).get("type") or "NO_BUTTON"
    if actual_cta != want["call_to_action"]["type"]:
        problems.append(f"CTA is {actual_cta}, expected {want['call_to_action']['type']}")
    for extra in ("child_attachments", "multi_share_optimized", "description"):
        if actual.get(extra):
            problems.append(f"has extra '{extra}' added")
    return problems
