# ClickUp → Meta ads automation

Attach the images to a ClickUp task, fill in the fields, and move the task to **Push to Facebook**. Within about 15 minutes the automation:

1. Finds the Meta **campaign** chosen in the task's `Campaign` field.
2. Creates an **ad set** named exactly like the task title. It is a copy of your template ad set, so it gets the same targeting, budget, placements and pixel.
3. Creates **one ad per attached image**. Headline, primary text, CTA and link are identical on every ad, so the image is the only thing that changes.
4. Reads every ad back from Meta and checks that nothing extra was added.
5. Comments a summary on the task and moves it to **In Meta**, or to **Meta failed** with the reason.

Ads are created **paused** so you can review them in Ads Manager. It runs on **GitHub Actions**, so there's no server to rent.

## How a task maps to Meta

| ClickUp | Meta |
|---|---|
| Task title (your generated name) | Ad set name |
| `Campaign` dropdown | Campaign (the option must match the Meta campaign name exactly; the campaign must already exist) |
| `Primary Text 1` (falls back to the task description if empty) | Primary text |
| `Headline` | Headline |
| `CTA` dropdown (Learn More, Shop Now, …, **No button**) | Call-to-action button |
| `URL Destination` | Website URL (the image click and the CTA) |
| Images attached to the task (JPG/PNG/GIF) | One ad each, named `<task title> - <file name>`, created in file-name order |

Other fields (Angle, Batch #, Product, etc.) are ignored. Field names can be changed in the settings.

### Primary text formatting

The text is rebuilt as plain text, with **every line separated by exactly one blank line**:

```
Tired of joint pain?

Most people don't realise this.

Try it today at our site https://example.com/offer
```

- Extra blank lines are collapsed. Stray spaces, non-breaking spaces and invisible characters are removed.
- Markdown (`**bold**`, `# headings`) is stripped so it doesn't appear literally in Meta.
- Meta can't hyperlink words, so `[our site](https://…)` becomes `our site https://…`. Meta then shows the bare URL as a clickable link.
- `Primary Text 1` must be a **Text** (long text) field, not **Short Text**. Short text throws away line breaks.

### "No button"

Set the CTA dropdown to **No button** and the ad is created with Meta's `NO_BUTTON` type. The image still clicks through to `URL Destination`, and URLs in the primary text stay clickable. An empty CTA stops the run, so a missing button is always deliberate.

## What is switched off, and how it's checked

On every creative:

- **Every Advantage+ creative enhancement is opted out individually**, including visual touch-ups, overlays and templates, brightness and contrast, expand image, background generation, animation, **music**, text improvements and translations, auto descriptions, enhanced CTA, relevant comments, **site links, highlights and summaries**, product and catalog extensions, store locations, profile card and adapt to placement. The full list is `OPT_OUT_FEATURES` in [`app/meta.py`](app/meta.py). If your API version doesn't recognise a key, that key is dropped automatically and the run continues.
- **Multi-advertiser ads** are opted out.
- It is a plain single-image link ad. There is no asset feed or dynamic creative, and no catalog, product set or carousel.

Each ad is then **read back from Meta** and checked. The check fails if any enhancement shows `OPT_IN`, if there is an asset feed or catalog link, or if an extra description or cards were added. It also fails if the text, headline, link, image or CTA differ from the task. A failing ad stays **paused**, and the ClickUp comment says what's wrong.

On the ad set, Advantage+ audience is switched off on the copy. You get warnings if the template uses dynamic creative or Advantage+ placements.

## Setup

### 1. ClickUp

- Add three statuses to the list: **Push to Facebook**, **In Meta** and **Meta failed**. The automation moves tasks out of Push to Facebook when it's done. If those statuses don't exist, it retries every run, so they're required.
- Fields: you already have `Campaign`, `CTA`, `Headline`, `Primary Text 1` and `URL Destination`.
  - Make the `Campaign` dropdown options exactly match your Meta campaign names.
  - Add a **No button** option to the `CTA` dropdown.

### 2. Meta

- **Token:** a system user token with `ads_management`, `ads_read`, `business_management`, `pages_read_engagement` and `pages_show_list`. The system user needs the ad account and Page assigned with full control.
- **Template ad set:** one ad set with your targeting, budget, optimisation and pixel, **manual placements** and **Advantage+ audience off**. Leave it paused. Every new ad set is a copy of it.

### 3. GitHub

Under **Settings → Secrets and variables → Actions → New repository secret**, add:

| Secret | Value |
|---|---|
| `CLICKUP_API_TOKEN` | ClickUp API token (`pk_…`) |
| `CLICKUP_TEAM_ID` | The number after `app.clickup.com/` in any ClickUp URL |
| `CLICKUP_LIST_IDS` | Optional: the list ID(s) to watch, comma separated |
| `META_ACCESS_TOKEN` | Meta system user token |
| `META_AD_ACCOUNT_ID` | e.g. `act_123456789` |
| `META_PAGE_ID` | Facebook Page ID |
| `META_TEMPLATE_ADSET_ID` | Template ad set ID |
| `META_INSTAGRAM_USER_ID` | Optional |

Optional **variables** (on the Variables tab):
- `CLICKUP_TRIGGER_STATUS`, `CLICKUP_DONE_STATUS`, `CLICKUP_FAILED_STATUS`: only if your status names differ from the defaults.
- `META_AD_STATUS`: `ACTIVE` makes clean ads go live automatically.
- `META_URL_TAGS`: UTM tags.

**Keep this repository private.** Action logs on a public repo are visible to anyone. A private repo gets 2,000 free Actions minutes a month. The schedule (every 15 minutes, Mon–Fri 07:00–20:00 UTC) uses about 1,150, and you can change it in [`.github/workflows/push-to-meta.yml`](.github/workflows/push-to-meta.yml).

### 4. Test it

Go to **Actions → Push to Meta → Run workflow**, enter a task ID (from the task URL `app.clickup.com/t/<task id>`) and leave **Dry run** ticked. The log shows the ad set name, the images it found and the exact primary text, and creates nothing. Untick Dry run to do it for real.

## Re-running

Re-running a task is safe. If the ad set already exists in the campaign it is reused, and images that already have an ad are skipped. If something failed, fix it and move the task back to **Push to Facebook**; only the missing ads are created.

## Google Drive (optional)

If a task has no images attached but contains a Google Drive folder link, the images are taken from Drive instead. This needs a Google service account with the folder shared to it, and its key JSON in the `GOOGLE_SERVICE_ACCOUNT_JSON` secret. You can skip this if you always attach images to the task.

## Development

```bash
pip install -r requirements-dev.txt
pytest
python -m app.cli run <task_id> --dry-run   # with a filled-in .env
```
