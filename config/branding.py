"""
Sculpt's look outside the website: the emails it sends and the few pages the backend serves
itself (email verification, password reset). Colours, type and shapes follow the website's
light "Studio" theme (dashboard/src/index.css).

Emails use tables and inline styles, since email clients ignore most modern CSS. The pages
use one <style> block; their Content-Security-Policy (page_csp) allows exactly that, the
Google font, the logo, and at most one script carrying a per-request nonce.
"""
import html
import os
from typing import Optional

PAPER = "#F3F6FB"       # page background
CARD = "#FFFFFF"
RULE = "#E7ECF4"        # soft borders and dividers
RULE_HARD = "#D6DEEA"
INK = "#0B1533"         # headings
INK_2 = "#374463"       # body text
MUTED = "#66728C"
ACCENT = "#0A6CF0"      # Sculpt blue: buttons, links, kicker
ACCENT_SOFT = "#E6F0FE"
CYAN = "#21C8E8"
DANGER = "#C2410C"
FONT = "'Plus Jakarta Sans', 'Segoe UI', Helvetica, Arial, sans-serif"
FONT_LINK = "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap"


def site_url() -> str:
    """The website's address: links back to Sculpt and the logo image."""
    return os.getenv("DASHBOARD_URL", "https://resumesculpt.app").rstrip("/")


def logo_url() -> str:
    return f"{site_url()}/sculpt-mark.png"


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


# ============================== emails ==============================

def email_html(*, preheader: str, kicker: str, heading: str, paragraphs: list,
               button_label: str, button_url: str, note: str, footer_reason: str) -> str:
    """One branded email. Every argument is plain text and is escaped here."""
    site = site_url()
    paras = "".join(
        f'<p style="margin:0 0 16px;font-family:{FONT};font-size:15px;line-height:1.65;color:{INK_2};">{esc(p)}</p>'
        for p in paragraphs
    )
    url = esc(button_url)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{esc(heading)}</title>
<link href="{FONT_LINK}" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:{PAPER};-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{PAPER};">{esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAPER};">
  <tr><td align="center" style="padding:36px 14px 28px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;">

      <tr><td style="padding:0 6px 18px;">
        <a href="{esc(site)}" style="text-decoration:none;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td style="vertical-align:middle;padding-right:9px;"><img src="{esc(logo_url())}" width="30" height="30" alt="" style="display:block;border:0;"></td>
            <td style="vertical-align:middle;font-family:{FONT};font-size:21px;font-weight:800;letter-spacing:-0.02em;color:{INK};">Sculpt</td>
          </tr></table>
        </a>
      </td></tr>

      <tr><td style="background:{CARD};border:1px solid {RULE};border-top:4px solid {ACCENT};border-radius:20px;padding:34px 34px 30px;">
        <p style="margin:0 0 12px;font-family:{FONT};font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:{ACCENT};">{esc(kicker)}</p>
        <h1 style="margin:0 0 18px;font-family:{FONT};font-size:26px;line-height:1.25;font-weight:800;letter-spacing:-0.02em;color:{INK};">{esc(heading)}</h1>
        {paras}
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 22px;"><tr>
          <td align="center" bgcolor="{ACCENT}" style="border-radius:12px;">
            <a href="{url}" style="display:inline-block;padding:14px 26px;font-family:{FONT};font-size:15px;font-weight:700;color:#FFFFFF;text-decoration:none;border-radius:12px;">{esc(button_label)} &rarr;</a>
          </td>
        </tr></table>
        <p style="margin:0 0 22px;font-family:{FONT};font-size:13px;line-height:1.6;color:{MUTED};">{esc(note)}</p>
        <div style="height:1px;background:{RULE};line-height:1px;font-size:1px;">&nbsp;</div>
        <p style="margin:18px 0 6px;font-family:{FONT};font-size:12px;line-height:1.6;color:{MUTED};">Button not working? Paste this link into your browser:</p>
        <p style="margin:0;font-family:{FONT};font-size:12px;line-height:1.6;word-break:break-all;"><a href="{url}" style="color:{ACCENT};text-decoration:underline;">{url}</a></p>
      </td></tr>

      <tr><td style="padding:20px 8px 0;font-family:{FONT};font-size:12px;line-height:1.7;color:{MUTED};text-align:center;">
        {esc(footer_reason)}<br>
        <a href="{esc(site)}" style="color:{MUTED};text-decoration:underline;">Sculpt</a> &middot; AI resume tailoring &middot; New Delhi, India
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""


def email_text(*, heading: str, paragraphs: list, button_label: str, button_url: str,
               note: str, footer_reason: str) -> str:
    """The plain-text twin of email_html, for clients that don't show HTML (and for spam filters)."""
    body = "\n\n".join(paragraphs)
    return (
        f"{heading}\n\n{body}\n\n{button_label}:\n{button_url}\n\n{note}\n\n"
        f"-- \nSculpt, AI resume tailoring\n{site_url()}\n{footer_reason}\n"
    )


# ============================== pages ==============================

def page_csp(nonce: Optional[str] = None) -> str:
    """CSP for a branded page: its <style> block, the Google font, the logo, and (with a nonce)
    one inline script that may call this API."""
    parts = [
        "default-src 'none'",
        "style-src 'unsafe-inline' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        f"img-src {site_url()}",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
    if nonce:
        parts += [f"script-src 'nonce-{nonce}'", "connect-src 'self'"]
    return "; ".join(parts)


PAGE_CSS = f"""
*,*::before,*::after{{box-sizing:border-box}}
html,body{{margin:0}}
body{{min-height:100vh;display:grid;place-items:center;padding:32px 16px;font-family:{FONT};color:{INK_2};
  background:{PAPER} radial-gradient(rgba(11,21,51,0.09) 1px,transparent 1.2px) 0 0/22px 22px;}}
.wrap{{width:100%;max-width:440px}}
.brand{{display:inline-flex;align-items:center;gap:9px;margin:0 0 18px 4px;color:{INK};text-decoration:none;font-weight:800;font-size:21px;letter-spacing:-0.02em}}
.brand img{{width:30px;height:30px}}
.card{{background:{CARD};border:1px solid {RULE};border-top:4px solid {ACCENT};border-radius:20px;padding:32px 30px 28px;
  box-shadow:0 24px 48px -28px rgba(11,21,51,0.35)}}
.badge{{width:46px;height:46px;border-radius:14px;display:grid;place-items:center;margin:0 0 18px;background:{ACCENT_SOFT};color:{ACCENT}}}
.badge svg{{width:24px;height:24px}}
.badge--warn{{background:#FDECE3;color:{DANGER}}}
.kicker{{margin:0 0 10px;font-size:11px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:{ACCENT}}}
h1{{margin:0 0 12px;font-size:26px;line-height:1.25;font-weight:800;letter-spacing:-0.02em;color:{INK}}}
p{{margin:0 0 14px;font-size:15px;line-height:1.65}}
.muted{{color:{MUTED};font-size:13px}}
.btn{{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;margin-top:8px;padding:13px 18px;border:0;border-radius:12px;
  background:{ACCENT};color:#fff;font:700 15px/1 {FONT};text-decoration:none;cursor:pointer;transition:background .2s}}
.btn:hover{{background:#0757C7}}
.btn[disabled]{{opacity:.6;cursor:progress}}
label{{display:block;margin:14px 0 6px;font-size:13px;font-weight:700;color:{INK}}}
input{{width:100%;padding:12px 14px;border:1px solid {RULE_HARD};border-radius:12px;background:#fff;color:{INK};font:500 15px/1.2 {FONT}}}
input:focus{{outline:2px solid {ACCENT};outline-offset:1px;border-color:{ACCENT}}}
.msg{{min-height:22px;margin:14px 0 0;font-size:14px;font-weight:600}}
.msg--err{{color:{DANGER}}}
.foot{{margin:18px 0 0;text-align:center;font-size:12px;color:{MUTED}}}
.foot a{{color:{MUTED}}}
[hidden]{{display:none!important}}
"""

ICON_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'
ICON_ALERT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8v5M12 16.5v.5"/><circle cx="12" cy="12" r="9"/></svg>'
ICON_KEY = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="15" r="4"/><path d="M10.8 12.2L19 4M16 7l2.5 2.5M14 9l2 2"/></svg>'


def page_html(*, title: str, body: str, script: str = "", nonce: Optional[str] = None) -> str:
    """A branded page. `body` is trusted HTML built by the caller (escape any user data)."""
    site = esc(site_url())
    script_tag = f'<script nonce="{esc(nonce)}">{script}</script>' if script and nonce else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{esc(title)} · Sculpt</title>
<link rel="stylesheet" href="{FONT_LINK}">
<style>{PAGE_CSS}</style>
</head>
<body>
<main class="wrap">
  <a class="brand" href="{site}"><img src="{esc(logo_url())}" alt="">Sculpt</a>
  <div class="card">{body}</div>
  <p class="foot"><a href="{site}">resumesculpt.app</a> &middot; <a href="{site}/contact.html">Help</a></p>
</main>
{script_tag}
</body>
</html>"""


def message_page(*, icon: str, kicker: str, heading: str, text: str, note: str = "",
                 button_label: str = "Open Sculpt", warn: bool = False) -> str:
    """A page that tells the reader one thing and offers a way back to Sculpt."""
    badge = "badge badge--warn" if warn else "badge"
    note_html = f'<p class="muted">{esc(note)}</p>' if note else ""
    body = (
        f'<div class="{badge}">{icon}</div>'
        f'<p class="kicker">{esc(kicker)}</p><h1>{esc(heading)}</h1><p>{esc(text)}</p>{note_html}'
        f'<a class="btn" href="{esc(site_url())}">{esc(button_label)} &rarr;</a>'
    )
    return page_html(title=heading, body=body)
