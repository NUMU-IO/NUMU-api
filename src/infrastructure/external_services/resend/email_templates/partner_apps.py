"""Partner App status emails, sent to the partner's owner and admins.

One email carries both languages: Egyptian Arabic first, then English, so a
partner team that reads either gets the same message.
"""

from __future__ import annotations

from html import escape

from src.infrastructure.external_services.resend.email_templates._base import (
    header,
    wrap,
)

_STATUS = {
    "submitted": (
        "استلمنا طلب المراجعة",
        "هنراجعه خلال ٣ أيام عمل.",
        "Submitted for review",
        "We will review it within 3 business days.",
    ),
    "in_review": (
        "المراجعة بدأت",
        "حد من فريق نُمو بيراجع طلبك دلوقتي.",
        "Review started",
        "A NUMU reviewer is looking at it now.",
    ),
    "changes_requested": (
        "مطلوب تعديلات",
        "عدّل النقط اللي تحت وابعته تاني للمراجعة.",
        "Changes requested",
        "Address the notes below, then resubmit.",
    ),
    "approved": (
        "اتقبل",
        "تقدر تنشره وقت ما تحب.",
        "Approved",
        "You can publish it whenever you are ready.",
    ),
    "rejected": (
        "اترفض",
        "شوف الملاحظات اللي تحت.",
        "Rejected",
        "See the notes below.",
    ),
    "published": (
        "اتنشر",
        "بقى هو النسخة الحالية على نُمو.",
        "Published",
        "It is now the live version on NUMU.",
    ),
    "suspended": (
        "التطبيق اتوقف",
        "نُمو وقفت التطبيق. التطبيق خرج من متجر التطبيقات ومفيش توكن شغال لحد ما يرجع.",
        "App suspended",
        "NUMU suspended the app. It is out of the App Store and its tokens are refused until it is reinstated.",
    ),
}


def partner_app_status_subject(status: str, app_name: str) -> str:
    ar, _, en, _ = _STATUS[status]
    return f"{app_name}: {ar} / {en} — NUMU Partners"


def partner_app_status_html(
    *,
    status: str,
    app_name: str,
    subject_label: tuple[str, str],
    notes: dict | None,
    url: str,
) -> str:
    ar_title, ar_line, en_title, en_line = _STATUS[status]
    name = escape(app_name)
    what_ar, what_en = subject_label

    def _notes(lang: str) -> str:
        text = (notes or {}).get(lang)
        if not text:
            return ""
        return (
            '<div class="panel" style="margin:16px 0;">'
            f'<p style="margin:0;white-space:pre-line;">{escape(text)}</p></div>'
        )

    body = f"""
    {header(ar_title, name, language="ar")}
    <div class="body">
        <p><strong>{name}</strong> — {escape(what_ar)}: {ar_line}</p>
        {_notes("ar")}
        <p class="center" style="margin:24px 0;">
            <a href="{url}" class="btn">افتح بوابة الشركاء</a>
        </p>
        <hr class="divider">
        <div dir="ltr" style="text-align:left;">
            <p><strong>{en_title}</strong></p>
            <p><strong>{name}</strong> — {escape(what_en)}: {en_line}</p>
            {_notes("en")}
            <p><a href="{url}">Open the partner portal</a></p>
        </div>
    </div>"""
    return wrap(body, language="ar", preheader=f"{name}: {ar_title} / {en_title}")
