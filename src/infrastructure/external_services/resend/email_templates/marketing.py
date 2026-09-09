"""Branded shell for marketing email.

Marketing mail was going out as whatever the operator typed into the template
body — raw text on a white page. That is not a neutral choice: an unstyled
promotional email from a company the merchant half-remembers reads as spam,
and the referral invite is the first thing many merchants ever see from NUMU.

Structure follows the shape promotional mail has settled on because it works
at a glance — brand band, motif, eyebrow, headline, body, one action, fine
print. Colour and type come from the NUMU design system's **editorial**
register: warm, Sienna-led, Arabic-first. That is the register the system
reserves for marketing, and picking `admin` here would make a promotion look
like the internal ops console.

Design-system values are inlined as literal hex on purpose. `styles.css`
resolves them through CSS custom properties, which Outlook does not support at
all and Gmail strips from `<style>` blocks — a token reference in email is a
missing colour. Each literal below names the token it came from so the two can
be diffed by eye when the palette moves.

Not shared with `_base.py`. That module dresses transactional mail in an older
navy/gold palette that predates the design system; re-skinning it would change
every password reset and order receipt, which is a bigger and separate
decision. Flagged rather than folded in here.
"""

from __future__ import annotations

import html
import re

# ── Editorial register, resolved to literals ─────────────────────────────
# tokens/palette.css → tokens/semantic.css [data-numu-register="editorial"]
PAGE_BG = "#f5ede0"  # --surface-app  ← --cream-warm
CARD_BG = "#fff9ee"  # --surface-card ← --paper-hi
BAND_BG = "#ebdfcc"  # --surface-inset ← --cream-warm-2
NAVY = "#001f3f"  # --navy-900, the brand band
INK = "#1a1410"  # --text-body ← --ink-warm
INK_SOFT = "#5c5044"  # --text-muted ← --ink-warm-soft
INK_FAINT = "#8a7c6c"  # --text-faint
SIENNA = "#8b2500"  # --accent / --text-link
TERRACOTTA = "#c14a1c"  # --cta
CTA_ON = "#f5ede0"  # --cta-on
HAIRLINE = "#e0d0b6"  # --border-subtle ← --cream-warm-3
SAFFRON = "#e8a430"  # --saffron, wordmark underline

# tokens/typography.css. Webfonts are declared for the clients that honour
# them and every stack ends in a system fallback, because most do not.
FONT_DISPLAY = "'Reem Kufi','Tajawal',Georgia,serif"
FONT_AR = "'Tajawal','Segoe UI',Tahoma,Arial,sans-serif"
FONT_LATIN = "'Space Grotesk','Segoe UI',Helvetica,Arial,sans-serif"

_FONT_LINK = (
    "https://fonts.googleapis.com/css2?family=Reem+Kufi:wght@500;700"
    "&family=Tajawal:wght@400;500;700&family=Space+Grotesk:wght@400;500;700"
    "&display=swap"
)

# A paragraph break in operator-written copy. Anything that already looks like
# markup is passed through untouched — see `_body_html`.
_MARKUP = re.compile(r"<(p|div|table|ul|ol|h[1-6]|br)\b", re.I)


def _body_html(body: str) -> str:
    """Operator copy as HTML.

    Templates predate this shell and are a mix: some are hand-written HTML,
    most are plain text with blank lines between paragraphs. Escaping the
    former would publish visible tags; wrapping the latter is the only way it
    reads as paragraphs rather than one run-on block. Detect, don't guess
    twice.
    """
    if _MARKUP.search(body):
        return body
    parts = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    return "".join(
        f'<p style="margin:0 0 16px;font-size:16px;line-height:1.75;color:{INK};">'
        f"{html.escape(p).replace(chr(10), '<br>')}</p>"
        for p in parts
    )


def _button(label: str, url: str, is_ar: bool) -> str:
    font = FONT_AR if is_ar else FONT_LATIN
    return f"""
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:8px auto 0;">
        <tr><td align="center" bgcolor="{TERRACOTTA}" style="border-radius:8px;">
          <a href="{html.escape(url, quote=True)}"
             style="display:inline-block;padding:14px 30px;font-family:{font};font-size:16px;
                    font-weight:700;color:{CTA_ON};text-decoration:none;border-radius:8px;">
            {html.escape(label)}
          </a>
        </td></tr>
      </table>"""


def render_marketing_email(
    *,
    body: str,
    language: str = "ar",
    eyebrow: str | None = None,
    headline: str | None = None,
    cta_label: str | None = None,
    cta_url: str | None = None,
    preheader: str | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    """Wrap operator copy in the branded marketing shell.

    Everything except ``body`` is optional: an operator who writes only copy
    still gets the brand, and a template that supplies a headline and a button
    gets the full treatment. Nothing is invented — no headline is rendered
    when none was given, rather than promoting the first line of the body into
    one and hoping it fits.
    """
    is_ar = language == "ar"
    direction = "rtl" if is_ar else "ltr"
    align = "right" if is_ar else "left"
    font = FONT_AR if is_ar else FONT_LATIN

    # The preview line in the inbox list. Without one, clients pull the first
    # words of the HTML — which here is the brand band's alt text.
    pre = (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
        f"{html.escape(preheader)}</div>"
        if preheader
        else ""
    )

    eyebrow_html = (
        f'<div style="font-family:{font};font-size:12px;font-weight:700;color:{SIENNA};'
        f'letter-spacing:1.5px;text-transform:uppercase;margin:0 0 12px;">'
        f"{html.escape(eyebrow)}</div>"
        if eyebrow
        else ""
    )
    headline_html = (
        f'<h1 class="numu-h1" style="margin:0 0 18px;font-family:{FONT_DISPLAY};font-size:30px;'
        f'line-height:1.3;font-weight:700;color:{INK};">{html.escape(headline)}</h1>'
        if headline
        else ""
    )
    cta_html = _button(cta_label, cta_url, is_ar) if cta_label and cta_url else ""

    unsub = (
        f'<a href="{html.escape(unsubscribe_url, quote=True)}" '
        f'style="color:{INK_FAINT};text-decoration:underline;">'
        f"{'إلغاء الاشتراك' if is_ar else 'Unsubscribe'}</a>"
        if unsubscribe_url
        else ""
    )
    footer_note = (
        "وصلتك الرسالة دي لأنك سجّلت اهتمامك بنُمو."
        if is_ar
        else "You are receiving this because you registered your interest in NUMU."
    )

    return f"""<!DOCTYPE html>
<html dir="{direction}" lang="{"ar" if is_ar else "en"}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<link href="{_FONT_LINK}" rel="stylesheet">
<title>NUMU</title>
<style>
  /* A fluid card with a max-width, not a fixed 600px one: `width="600"`
     cannot shrink, so on a 390pt phone the card ran off the screen and the
     copy was clipped mid-line. Outlook ignores max-width, which is what the
     mso ghost table below is for.

     Gmail on iOS strips <style> only for the *sender's* CSS in some legacy
     paths; media queries here are honoured by iOS Mail, Gmail iOS and Apple
     Mail — the clients that matter for a phone-first Egyptian audience. The
     inline styles above stand alone if this block is ever dropped, so the
     email degrades to "readable at 600px" rather than "broken". */
  @media only screen and (max-width: 600px) {{
    .numu-pad {{ padding-left: 20px !important; padding-right: 20px !important; }}
    .numu-h1 {{ font-size: 24px !important; line-height: 1.35 !important; }}
    .numu-body {{ font-size: 15px !important; }}
    .numu-cta a {{ display: block !important; text-align: center !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{PAGE_BG};">
{pre}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{PAGE_BG};padding:24px 12px;">
  <tr><td align="center">
    <!--[if mso]>
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td>
    <![endif]-->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           class="numu-card"
           style="width:100%;max-width:600px;background:{CARD_BG};border:1px solid {HAIRLINE};
                  border-radius:14px;overflow:hidden;">

      <!-- Brand band -->
      <tr><td align="center" bgcolor="{NAVY}" style="padding:22px 24px;">
        <span style="font-family:{FONT_DISPLAY};font-size:32px;font-weight:700;color:#ffffff;
                     line-height:1.2;display:inline-block;">نُمو</span><br>
        <span style="font-family:{FONT_LATIN};font-size:11px;font-weight:700;color:{SAFFRON};
                     letter-spacing:4px;text-transform:uppercase;">NUMU</span>
      </td></tr>

      <!-- Motif band. A flat hairlined strip rather than an image: an email
           whose hero is a remote PNG shows a broken box in every client that
           blocks images by default, which is most of them. -->
      <tr><td bgcolor="{BAND_BG}" style="padding:0;font-size:0;line-height:0;height:8px;">&nbsp;</td></tr>

      <!-- Content -->
      <tr><td dir="{direction}" align="{align}" class="numu-pad"
              style="padding:32px 32px 8px;text-align:{align};">
        {eyebrow_html}
        {headline_html}
        <div class="numu-body" style="font-family:{font};font-size:16px;line-height:1.75;color:{INK};">
          {_body_html(body)}
        </div>
      </td></tr>

      <tr><td align="center" class="numu-pad numu-cta" style="padding:8px 32px 32px;">{cta_html}</td></tr>

      <!-- Fine print -->
      <tr><td class="numu-pad" style="padding:0 32px;">
        <div style="border-top:1px solid {HAIRLINE};font-size:0;line-height:0;">&nbsp;</div>
      </td></tr>
      <tr><td dir="{direction}" align="center" class="numu-pad"
              style="padding:18px 32px 28px;text-align:center;">
        <p style="margin:0 0 6px;font-family:{font};font-size:12px;line-height:1.7;color:{INK_FAINT};">
          {footer_note}
        </p>
        <p style="margin:0;font-family:{font};font-size:12px;color:{INK_FAINT};">
          <a href="https://numueg.app" style="color:{SIENNA};text-decoration:none;">numueg.app</a>
          {"&nbsp;·&nbsp;" + unsub if unsub else ""}
        </p>
      </td></tr>
    </table>
    <!--[if mso]></td></tr></table><![endif]-->
  </td></tr>
</table>
</body>
</html>"""
