# Phase E9 — Three ready-made looks and a custom one

Status: implemented locally (owner request, 2026-09-27). Nothing is deployed.

## The looks

*Settings › Brand › Look*: four cards, each drawn as a small preview.

| Look | Character | Colours | Type | Shapes | Header | Footer |
|---|---|---|---|---|---|---|
| **Signature** | Modern and confident (the original design) | Royal blue, lime | Plus Jakarta Sans | Crisp 8–10 px corners | Bright bar, blue underline on the current page | Deep blue |
| **Heritage** | Classic and calm | Deep green, warm gold on ivory | Fraunces (serif) headings, Plus Jakarta body | Square-ish 3–6 px corners, fine hairlines | Ivory bar with a gold line on top | Deep green with a gold rule |
| **Aura** | Soft and refined | Plum, rose on lavender | Outfit throughout | Large 20–30 px corners, pill buttons, soft shadows | A floating rounded bar, the current page as a pill | Aubergine with rounded top corners |
| **Custom** | Your own | Any two colours (contrast-checked) | Type, corners and header of the look you choose | | | The deep tone is taken from your colour |

Each look has its own light and dark theme. Choosing one changes the store, the client area, the staff console, sign-in, emails and PDFs (they read the same colours). Out of the box the brand stays "Custom" with the colours already saved, so nothing changes until you choose.

## How it works

`BrandSettings.theme` (signature, heritage, aura, custom) and `custom_look`. A ready-made look brings its own colours (`services.LOOKS`); "custom" keeps the typed ones. The page carries `<html data-look="…">`; `static/css/looks.css` holds what each look changes (tokens for colours, corners, shadows and type, and the header and footer shapes). The deep tone of the hero band and the footer is derived from the primary colour for every look. Two fonts were added (Fraunces, Outfit; SIL Open Font Licence, in `static/vendor/fonts/`).

## A bug found on the way

The brand colours (`/brand.css`) were linked *before* the default colours in `theme.css`, so they lost: any colour other than the default blue was silently ignored. They now come last and win.

## Verified

`apps/branding/tests/test_branding.py` (10 new looks tests). Browser (Chromium, 1440 light and dark, 390): every look on the client dashboard, store, sign-in and forms; the Brand page picks and saves each look including a custom red-and-gold on Heritage; no overflow, no JS errors.
