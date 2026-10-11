# ClickUp → Meta ads automation

Move a ClickUp task to **Push to Facebook** and this service:

1. Reads the task through the ClickUp API.
2. Finds the Meta **campaign** named in the task's `Campaign` field.
3. Creates an **ad set** named exactly like the task title. It is a copy of your template ad set, so it gets the same targeting, budget, placements and pixel.
4. Downloads every image from the **Google Drive link** in the task.
5. Creates **one ad per image**. Primary text, headline, CTA and link are identical on every ad, so the image is the only thing that changes.
6. Reads every ad back from Meta and checks that nothing extra was added. Then it comments a summary on the task and, if you configure it, moves the task to a done or failed status.

Ads are created **paused** by default so you can review them in Ads Manager.

## How a task maps to Meta

| ClickUp | Meta |
|---|---|
| Task title | Ad set name |
| `Campaign` field | Campaign the ad set goes into (exact name; the campaign must already exist) |
| Task description (or `Primary Text` field if filled) | Primary text |
| `Headline` field | Headline |
| `CTA` field (dropdown: Learn More, Shop Now, Sign Up, …, **No button**) | Call-to-action button |
| `Link` field | Website URL (the image click and the CTA) |
| `Drive Link` field, or the first Drive link in the description | Images; one ad each, named `<task title> - <file name>` |

Field names can be changed in `.env`. Lines in the description that contain the Drive link are removed from the primary text.

### Primary text formatting

The description is rebuilt as plain text, with **every line separated by exactly one blank line**:

```
Tired of bloating?

Most people don't realise this.

Try it today at our site https://example.com/offer
```

- Extra blank lines are collapsed. Stray spaces, non-breaking spaces and invisible characters are removed.
- Markdown (`**bold**`, `# headings`, `> quotes`, ClickUp's `\!` escapes) is stripped so it doesn't appear literally in Meta.
- Meta can't hyperlink words, so `[our site](https://…)` becomes `our site https://…`. Meta then shows the bare URL as a clickable link.
- A sentence is never split; only the lines you wrote become paragraphs.

Run `python -m app.cli run <task_id> --dry-run` to print exactly what will be pasted.

### "No button"

Set the CTA dropdown to **No button** and the ad is created with Meta's `NO_BUTTON` type. The image still clicks through to the `Link`, and any URLs in the primary text stay clickable. An empty CTA field is treated as a mistake and stops the run, so a missing button is always a deliberate choice.

## What is switched off, and how it's checked

On every creative:

- **Every Advantage+ creative enhancement is opted out individually**, including visual touch-ups, image templates and overlays, brightness and contrast, expand image, background generation, image animation, **music**, text improvements and translations, auto descriptions, enhanced CTA, relevant comments, **site links, highlights and summaries**, product and catalog extensions, store locations, profile card and adapt to placement. The full list is `OPT_OUT_FEATURES` in [`app/meta.py`](app/meta.py). If your API version doesn't recognise a key, that key is dropped automatically and the run continues.
- **Multi-advertiser ads** are opted out.
- It is a plain single-image link ad. There is no asset feed or dynamic creative, and no catalog, product set or carousel.

Each ad is then **read back from Meta** and checked. The check fails if any enhancement shows `OPT_IN`, if there is an asset feed or catalog link, or if an extra description or carousel cards were added. It also fails if the primary text, headline, link, image or CTA differ from the brief. A failing ad is **left paused**, and the ClickUp comment lists what's wrong.

On the ad set, Advantage+ audience is switched off on the copy. You get warnings if the template has dynamic creative switched on or uses Advantage+ placements.

## One-time setup

### 1. Meta

- Create a **system user** in Business Settings with `ads_management` access to the ad account and Page, and generate a token for it (`META_ACCESS_TOKEN`).
- Create the campaigns you'll reference. Use a manual campaign, not Advantage+ shopping.
- Build **one template ad set** with the targeting, budget, optimisation, pixel and conversion event you want. Use **manual placements** and switch **Advantage+ audience off**. Put its ID in `META_TEMPLATE_ADSET_ID`. Every new ad set is a copy of it. The template can sit paused in any campaign.

### 2. Google Drive

Use one of these:

- **Service account (recommended):** create one in Google Cloud, enable the Drive API, and share your ads folder (or shared drive) with the service account's email as Viewer. Set `GOOGLE_SERVICE_ACCOUNT_FILE` (path) or `GOOGLE_SERVICE_ACCOUNT_JSON` (contents).
- **API key:** only works for folders shared as "Anyone with the link".

Images are taken in name order (`1, 2, … 10`). JPG, PNG, GIF, BMP and TIFF are used. Anything else is skipped and listed in the ClickUp comment.

### 3. ClickUp

- Add the statuses `Push to Facebook` and, optionally, `In Meta` and `Meta failed` to the list.
- Add custom fields `Campaign` (text or dropdown), `Headline` (text), `CTA` (dropdown), `Link` (URL) and, optionally, `Drive Link` (URL).
- Set `CLICKUP_API_TOKEN` (Settings → Apps → API token).

### 4. Deploy and connect

```bash
cp .env.example .env          # fill it in
pip install -r requirements.txt

# test on a real task without touching Meta
python -m app.cli run <task_id> --dry-run

# run the service (or: docker build -t ads-bot . && docker run --env-file .env -p 8000:8000 ads-bot)
gunicorn -w 1 --threads 4 -b 0.0.0.0:8000 app.server:app

# point ClickUp at it (team id = the number in app.clickup.com/<team id>/...)
python -m app.cli register-webhook <team_id> https://your-public-url --list-id <list_id>
# → put the printed secret in CLICKUP_WEBHOOK_SECRET and restart
```

Any host that runs a container or a Python process works, such as Render, Railway, Fly.io or a small VM. Keep it at one gunicorn worker.

## Re-running

Re-running a task is safe. If the ad set already exists in the campaign it is reused, and images that already have an ad (matched by ad name) are skipped. If one image fails, fix the problem and move the task back to **Push to Facebook**; only the missing ads are created.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```
