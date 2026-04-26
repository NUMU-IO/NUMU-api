"""Resend email service implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import resend

from src.application.dto.email_template import RenderedEmailDTO
from src.config import settings
from src.config.logging_config import get_logger
from src.core.entities.email_log import EmailLog
from src.core.exceptions import ExternalServiceError
from src.core.interfaces.services.email_service import EmailMessage, IEmailService

if TYPE_CHECKING:
    from src.application.services.email_template_renderer import EmailTemplateRenderer
    from src.core.interfaces.repositories.email_log_repository import (
        IEmailLogRepository,
    )

logger = get_logger(__name__)

# Resend errors that indicate a permanent configuration problem (no point retrying).
_PERMANENT_ERROR_PHRASES = [
    "domain with your API key is not verified",
    "api key is invalid",
    "api_key is required",
    "missing api key",
]


class EmailConfigurationError(ExternalServiceError):
    """Raised for permanent email configuration problems (non-retryable)."""


class ResendEmailService(IEmailService):
    """Email service implementation using Resend.

    Optionally accepts an :class:`EmailTemplateRenderer` and an
    :class:`IEmailLogRepository`. When both are provided AND a caller
    passes ``store_id`` to one of the per-event send methods, the service
    routes through the renderer (giving merchants the chance to override
    the default template) and writes a row to the email-log audit trail.

    When either dependency is missing OR the caller doesn't pass a
    ``store_id`` (e.g. a pre-tenant signup flow), behavior is identical
    to the legacy string-interpolation path — guaranteeing backwards
    compatibility for the FastAPI startup, Celery workers that build the
    service ad-hoc per task, and tests.
    """

    def __init__(
        self,
        api_key: str | None = None,
        from_address: str | None = None,
        *,
        renderer: EmailTemplateRenderer | None = None,
        email_log_repo: IEmailLogRepository | None = None,
    ) -> None:
        self.api_key = api_key or settings.resend_api_key
        if self.api_key:
            resend.api_key = self.api_key
        self.from_email = from_address or settings.email_from_address
        self.from_name = settings.email_from_name
        # Optional dependencies. When None, _render_or_legacy() and
        # _log_send() fall through to the legacy code path.
        self.renderer = renderer
        self.email_log_repo = email_log_repo

    # ------------------------------------------------------------------
    # Internal helpers — render-or-legacy + audit log
    # ------------------------------------------------------------------

    async def _render_or_legacy(
        self,
        *,
        event_type: str,
        language: str,
        store_id: UUID | None,
        variables: dict,
        legacy_subject: str,
        legacy_html: str,
        legacy_from_name: str | None = None,
    ) -> RenderedEmailDTO:
        """Route through the renderer when possible; otherwise wrap the
        legacy values in a :class:`RenderedEmailDTO` so the rest of the
        send path stays uniform.
        """
        if self.renderer is not None and store_id is not None:
            try:
                return await self.renderer.render(
                    store_id=store_id,
                    event_type=event_type,
                    language=language,
                    variables=variables,
                )
            except Exception:
                # The renderer has its own internal fallbacks; if it
                # somehow still raises we fall back to legacy so the
                # customer gets *something* rather than nothing.
                logger.exception(
                    "email_template_renderer_unhandled_error",
                    event_type=event_type,
                    language=language,
                    store_id=str(store_id),
                )
        return RenderedEmailDTO(
            subject=legacy_subject,
            html=legacy_html,
            from_name=legacy_from_name,
            reply_to=None,
            used_custom=False,
            template_id=None,
        )

    async def _log_send(
        self,
        *,
        store_id: UUID | None,
        tenant_id: UUID | None,
        recipient: str,
        event_type: str,
        language: str,
        subject: str,
        status: str,
        used_custom_template: bool,
        template_id: UUID | None,
        message_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Write an :class:`EmailLog` audit row.

        No-op when ``email_log_repo`` is None (legacy path), or when no
        ``store_id`` is supplied — global / pre-tenant emails (signup
        verification, password reset before tenant exists, beta invites,
        etc.) don't have a store to attribute the row to.
        """
        if self.email_log_repo is None or store_id is None:
            return
        try:
            await self.email_log_repo.create(
                EmailLog(
                    store_id=store_id,
                    tenant_id=tenant_id,
                    recipient=recipient,
                    event_type=event_type,
                    language=language,
                    subject=subject,
                    status=status,  # type: ignore[arg-type]
                    used_custom_template=used_custom_template,
                    template_id=template_id,
                    message_id=message_id,
                    error_code=error_code,
                    extra_data={},
                )
            )
        except Exception:
            # Audit-log failures must NEVER break customer notifications.
            logger.exception("email_log_create_failed", event_type=event_type)

    # ------------------------------------------------------------------
    # IEmailService implementation
    # ------------------------------------------------------------------

    async def send_email(self, message: EmailMessage) -> bool:
        """Send an email using Resend."""
        to_list = message.to if isinstance(message.to, list) else [message.to]

        # ── Dev / missing-key guard ─────────────────────────────────
        if not self.api_key:
            logger.warning(
                "email_skipped_no_api_key",
                to=to_list,
                subject=message.subject,
                hint="Set RESEND_API_KEY in .env to enable email sending.",
            )
            return True  # Don't block the caller in dev

        try:
            from_address = f"{message.from_name or self.from_name} <{message.from_email or self.from_email}>"

            params = {
                "from": from_address,
                "to": to_list,
                "subject": message.subject,
            }

            # Template-based email
            if message.template_id:
                params["template"] = {
                    "id": message.template_id,
                    "props": message.context or {},
                }
            else:
                params["html"] = message.html_content

            if message.text_content:
                params["text"] = message.text_content
            if message.reply_to:
                params["reply_to"] = message.reply_to
            if message.attachments:
                params["attachments"] = message.attachments

            resend.Emails.send(params)
        except Exception as e:
            error_msg = str(e).lower()
            if any(phrase in error_msg for phrase in _PERMANENT_ERROR_PHRASES):
                raise EmailConfigurationError(
                    "Resend",
                    f"{e}  -- This is a configuration issue; fix RESEND_API_KEY / "
                    f"EMAIL_FROM_ADDRESS or verify the domain in Resend dashboard.",
                )
            raise ExternalServiceError("Resend", str(e))

        # Log success OUTSIDE the try/except — structlog can crash on
        # non-ASCII subjects (Arabic) when the Windows console uses cp1252.
        # That encoding error must NOT be mis-reported as an email failure.
        try:
            logger.info("email_sent", to=to_list, subject=message.subject)
        except UnicodeEncodeError:
            logger.info("email_sent", to=to_list, subject="(non-ascii subject)")
        return True

    async def send_verification_email(
        self,
        email: str,
        token: str,
        code: str | None = None,
        *,
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
        user_name: str | None = None,
        language: str = "ar",
    ) -> bool:
        """Send email verification email with a 6-digit code and a verification link.

        Egyptian Arabic by default — matches NUMU brand identity.

        ``store_id`` is optional and almost never supplied — this email
        usually fires before a tenant/store even exists. When supplied,
        the merchant's custom template (if any) is used.
        """
        from src.infrastructure.external_services.resend.email_templates._base import (
            header,
            wrap,
        )

        verify_url = f"{settings.cors_origins[0]}/verify-email?token={token}"
        code_display = code or "------"

        body = f"""
        {header("تأكيد البريد الإلكتروني", "خطوة واحدة وخلصت", language="ar")}
        <div class="body">
            <p class="lead">أهلاً بيك في <span class="brand">نُمو</span>،</p>
            <p>دخّل الكود ده في لوحة التحكم عشان تأكّد إيميلك:</p>

            <div class="code-box">
                <p class="digits">{code_display}</p>
                <p class="hint">الكود ده صلاحيته ٢٤ ساعة</p>
            </div>

            <hr class="divider">

            <p>أو اضغط الزرار ده عشان تأكّد على طول:</p>
            <p class="center" style="margin:20px 0;">
                <a href="{verify_url}" class="btn">تأكيد الإيميل</a>
            </p>

            <p class="muted" style="margin-top:24px;">
                لو ماعملتش حساب على نُمو، تجاهل الإيميل ده ببساطة.
            </p>
        </div>"""

        legacy_html = wrap(body, language="ar", preheader="كود تأكيد إيميلك على نُمو")
        legacy_subject = "تأكيد إيميلك على نُمو"

        rendered = await self._render_or_legacy(
            event_type="email_verification",
            language=language,
            store_id=store_id,
            variables={
                "code": code_display,
                "user_name": user_name or "",
                "expires_in_minutes": 1440,
            },
            legacy_subject=legacy_subject,
            legacy_html=legacy_html,
        )

        message = EmailMessage(
            to=email,
            subject=rendered.subject,
            html_content=rendered.html,
            from_name=rendered.from_name,
            reply_to=rendered.reply_to,
        )
        try:
            ok = await self.send_email(message)
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="email_verification",
                language=language,
                subject=rendered.subject,
                status="sent" if ok else "failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
            )
            return ok
        except Exception as exc:
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="email_verification",
                language=language,
                subject=rendered.subject,
                status="failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
                error_code=str(exc)[:100],
            )
            raise

    async def send_staff_invitation_email(
        self,
        email: str,
        invite_url: str,
        tenant_name: str,
        inviter_name: str | None = None,
        personal_message: str | None = None,
        *,
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
        role: str | None = None,
        language: str = "ar",
    ) -> bool:
        """Send a staff invitation email with acceptance link."""
        from src.infrastructure.external_services.resend.email_templates._base import (
            header,
            wrap,
        )

        inviter_line = (
            f"<strong>{inviter_name}</strong> دعاك" if inviter_name else "اتدعيت"
        )

        personal_html = ""
        if personal_message:
            personal_html = (
                '<div class="panel" style="margin:16px 0;">'
                f'<p style="margin:0;">{personal_message}</p>'
                "</div>"
            )

        body = f"""
        {header("دعوة لنضم للفريق", tenant_name, language="ar")}
        <div class="body">
            <p class="lead">أهلاً بيك،</p>
            <p>
                {inviter_line} تنضم لفريق <strong>{tenant_name}</strong>
                على <span class="brand">نُمو</span>.
            </p>
            {personal_html}
            <p>اضغط الزرار ده عشان تقبل الدعوة وتبدأ:</p>
            <p class="center" style="margin:28px 0;">
                <a href="{invite_url}" class="btn">قبول الدعوة</a>
            </p>

            <hr class="divider">

            <p class="muted">الدعوة دي صلاحيتها ٧ أيام.</p>
            <p class="muted">
                لو الزرار مش شغال، افتح اللينك ده:<br>
                <a href="{invite_url}">{invite_url}</a>
            </p>
        </div>"""

        legacy_html = wrap(
            body,
            language="ar",
            preheader=f"دعوة للانضمام لـ {tenant_name} على نُمو",
        )
        legacy_subject = f"دعوة للانضمام لـ {tenant_name} — نُمو"

        rendered = await self._render_or_legacy(
            event_type="staff_invitation",
            language=language,
            store_id=store_id,
            variables={
                "inviter_name": inviter_name or "",
                "store_name": tenant_name,
                "invite_link": invite_url,
                "role": role or "staff",
            },
            legacy_subject=legacy_subject,
            legacy_html=legacy_html,
        )

        message = EmailMessage(
            to=email,
            subject=rendered.subject,
            html_content=rendered.html,
            from_name=rendered.from_name,
            reply_to=rendered.reply_to,
        )
        try:
            ok = await self.send_email(message)
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="staff_invitation",
                language=language,
                subject=rendered.subject,
                status="sent" if ok else "failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
            )
            return ok
        except Exception as exc:
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="staff_invitation",
                language=language,
                subject=rendered.subject,
                status="failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
                error_code=str(exc)[:100],
            )
            raise

    async def send_password_reset_email(
        self,
        email: str,
        token: str,
        *,
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
        user_name: str | None = None,
        language: str = "ar",
    ) -> bool:
        """Send password reset email — Egyptian Arabic, NUMU brand."""
        from src.infrastructure.external_services.resend.email_templates._base import (
            header,
            wrap,
        )

        reset_url = f"{settings.cors_origins[0]}/reset-password?token={token}"

        body = f"""
        {header("إعادة تعيين كلمة المرور", "طلبنا تغيير الباسورد", language="ar")}
        <div class="body">
            <p class="lead">أهلاً بيك،</p>
            <p>وصلنا طلب لإعادة تعيين كلمة المرور بتاعتك على <span class="brand">نُمو</span>. اضغط على الزرار ده عشان تظبط باسورد جديد:</p>

            <p class="center" style="margin:28px 0;">
                <a href="{reset_url}" class="btn">إعادة تعيين كلمة المرور</a>
            </p>

            <hr class="divider">

            <p class="muted">اللينك ده صلاحيته ساعة واحدة بس.</p>
            <p class="muted">لو ماطلبتش إعادة تعيين كلمة المرور، تجاهل الإيميل ده وحسابك في أمان.</p>
        </div>"""

        legacy_html = wrap(
            body, language="ar", preheader="إعادة تعيين كلمة المرور بتاعتك على نُمو"
        )
        legacy_subject = "إعادة تعيين كلمة المرور — نُمو"

        rendered = await self._render_or_legacy(
            event_type="password_reset",
            language=language,
            store_id=store_id,
            variables={
                "reset_link": reset_url,
                "user_name": user_name or "",
                "expires_in_minutes": 60,
            },
            legacy_subject=legacy_subject,
            legacy_html=legacy_html,
        )

        message = EmailMessage(
            to=email,
            subject=rendered.subject,
            html_content=rendered.html,
            from_name=rendered.from_name,
            reply_to=rendered.reply_to,
        )
        try:
            ok = await self.send_email(message)
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="password_reset",
                language=language,
                subject=rendered.subject,
                status="sent" if ok else "failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
            )
            return ok
        except Exception as exc:
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="password_reset",
                language=language,
                subject=rendered.subject,
                status="failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
                error_code=str(exc)[:100],
            )
            raise

    async def send_order_confirmation(
        self,
        email: str,
        order_number: str,
        order_details: dict,
        language: str = "ar",
        *,
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> bool:
        """Send order confirmation email."""
        from src.infrastructure.external_services.resend.email_templates.notifications import (
            ORDER_CONFIRMATION_TEMPLATE,
        )

        items = order_details.get("items", [])
        total = order_details.get("total", 0)
        currency = order_details.get("currency", "EGP")
        store_name = order_details.get("store_name", "NUMU")
        customer_name = order_details.get("customer_name")
        # Persistent tracking page on the storefront; reflects real-time
        # status changes made by the merchant. Caller constructs the full
        # absolute URL (e.g. https://{subdomain}.numueg.app/track/{order_id}).
        tracking_url = order_details.get("tracking_url")
        # Optional InstaPay block — when the order was placed via the
        # manual IPA flow, we inline the IPA / ref / amount / expiry so
        # a customer who closed the tab can still pay from this email.
        instapay = order_details.get("instapay")

        legacy_html = ORDER_CONFIRMATION_TEMPLATE["html_fn"](
            order_number=order_number,
            items=items,
            total=total,
            currency=currency,
            store_name=store_name,
            customer_name=customer_name,
            language=language,
            tracking_url=tracking_url,
            instapay=instapay,
        )
        legacy_subject = ORDER_CONFIRMATION_TEMPLATE["subject_fn"](
            order_number, store_name, language
        )

        rendered = await self._render_or_legacy(
            event_type="order_confirmation",
            language=language,
            store_id=store_id,
            variables={
                "customer_name": customer_name or "",
                "order_number": order_number,
                "order_total": total,
                "currency": currency,
                "store_name": store_name,
                "items": items,
                "track_url": tracking_url or "#",
            },
            legacy_subject=legacy_subject,
            legacy_html=legacy_html,
        )

        message = EmailMessage(
            to=email,
            subject=rendered.subject,
            html_content=rendered.html,
            from_name=rendered.from_name,
            reply_to=rendered.reply_to,
        )
        try:
            ok = await self.send_email(message)
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="order_confirmation",
                language=language,
                subject=rendered.subject,
                status="sent" if ok else "failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
            )
            return ok
        except Exception as exc:
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="order_confirmation",
                language=language,
                subject=rendered.subject,
                status="failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
                error_code=str(exc)[:100],
            )
            raise

    async def send_invoice_email(
        self,
        email: str,
        order_number: str,
        invoice_number: str,
        pdf_bytes: bytes,
        store_name: str = "NUMU",
        language: str = "ar",
    ) -> bool:
        """Send invoice email with PDF attachment to customer."""
        from src.infrastructure.external_services.resend.email_templates._base import (
            header,
            wrap,
        )

        is_ar = language == "ar"
        subject = (
            f"فاتورتك من {store_name} — طلب #{order_number}"
            if is_ar
            else f"Your Invoice from {store_name} — Order #{order_number}"
        )

        if is_ar:
            body = f"""
            {header("فاتورة ضريبية", store_name, badge=f"#{invoice_number}", language="ar")}
            <div class="body">
                <p class="lead">أهلاً بيك،</p>
                <p>
                  شكراً لطلبك رقم <strong>#{order_number}</strong>.
                  مرفق فاتورتك الضريبية رقم <strong>{invoice_number}</strong>.
                </p>

                <div class="panel">
                    <p style="margin:0; font-size:14px; color:#1A1A2E;">
                        📎 الفاتورة مرفقة كملف PDF. تقدر تحمّلها وتحتفظ بيها لسجلاتك.
                    </p>
                </div>

                <p class="muted" style="margin-top:24px;">
                    دي فاتورة إلكترونية صادرة وفقاً لمتطلبات مصلحة الضرايب المصرية.
                </p>
            </div>"""
        else:
            body = f"""
            {header("Tax Invoice", store_name, badge=f"#{invoice_number}", language="en")}
            <div class="body">
                <p class="lead">Hello,</p>
                <p>
                  Thank you for your order <strong>#{order_number}</strong>.
                  Please find attached your tax invoice <strong>{invoice_number}</strong>.
                </p>

                <div class="panel">
                    <p style="margin:0; font-size:14px; color:#1A1A2E;">
                        📎 Your invoice is attached as a PDF file. You may download it for your records.
                    </p>
                </div>

                <p class="muted" style="margin-top:24px;">
                    This is an electronic invoice issued per Egyptian Tax Authority requirements.
                </p>
            </div>"""

        html_content = wrap(
            body,
            language=language,
            preheader=(
                f"فاتورتك رقم {invoice_number}"
                if is_ar
                else f"Your invoice {invoice_number}"
            ),
        )

        import base64

        safe_filename = invoice_number.replace("/", "-")
        message = EmailMessage(
            to=email,
            subject=subject,
            html_content=html_content,
            attachments=[
                {
                    "filename": f"{safe_filename}.pdf",
                    "content": base64.b64encode(pdf_bytes).decode("utf-8"),
                    "content_type": "application/pdf",
                }
            ],
        )
        return await self.send_email(message)

    async def send_instapay_payment_confirmed(
        self,
        *,
        email: str,
        order_number: str,
        reference_code: str,
        amount_cents: int,
        currency: str = "EGP",
        store_name: str = "NUMU",
        customer_name: str | None = None,
        language: str = "ar",
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> bool:
        """Send a short "payment received" email after proof approval.

        Fires in parallel with (and independently of) the invoice email,
        so the customer learns about approval even when PDF generation
        fails or no invoice is issued.
        """
        from src.infrastructure.external_services.resend.email_templates.instapay import (
            payment_confirmed_html,
            payment_confirmed_subject,
        )

        legacy_html = payment_confirmed_html(
            order_number=order_number,
            reference_code=reference_code,
            amount_cents=amount_cents,
            currency=currency,
            store_name=store_name,
            customer_name=customer_name,
            language=language,
        )
        legacy_subject = payment_confirmed_subject(
            order_number, store_name=store_name, language=language
        )

        rendered = await self._render_or_legacy(
            event_type="instapay_payment_confirmed",
            language=language,
            store_id=store_id,
            variables={
                "customer_name": customer_name or "",
                "order_number": order_number,
                "amount": amount_cents / 100,
                "currency": currency,
                "store_name": store_name,
            },
            legacy_subject=legacy_subject,
            legacy_html=legacy_html,
        )

        message = EmailMessage(
            to=email,
            subject=rendered.subject,
            html_content=rendered.html,
            from_name=rendered.from_name,
            reply_to=rendered.reply_to,
        )
        try:
            ok = await self.send_email(message)
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="instapay_payment_confirmed",
                language=language,
                subject=rendered.subject,
                status="sent" if ok else "failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
            )
            return ok
        except Exception as exc:
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="instapay_payment_confirmed",
                language=language,
                subject=rendered.subject,
                status="failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
                error_code=str(exc)[:100],
            )
            raise

    async def send_instapay_payment_rejected(
        self,
        *,
        email: str,
        order_number: str,
        reason: str,
        can_retry: bool = True,
        retry_url: str | None = None,
        store_name: str = "NUMU",
        customer_name: str | None = None,
        language: str = "ar",
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
        amount_cents: int | None = None,
        currency: str = "EGP",
    ) -> bool:
        """Notify the customer that the merchant rejected their proof."""
        from src.infrastructure.external_services.resend.email_templates.instapay import (
            payment_rejected_html,
            payment_rejected_subject,
        )

        legacy_html = payment_rejected_html(
            order_number=order_number,
            reason=reason,
            can_retry=can_retry,
            retry_url=retry_url,
            store_name=store_name,
            customer_name=customer_name,
            language=language,
        )
        legacy_subject = payment_rejected_subject(
            order_number, store_name=store_name, language=language
        )

        rendered = await self._render_or_legacy(
            event_type="instapay_payment_rejected",
            language=language,
            store_id=store_id,
            variables={
                "customer_name": customer_name or "",
                "order_number": order_number,
                "amount": (amount_cents or 0) / 100,
                "currency": currency,
                "reason": reason,
                "store_name": store_name,
            },
            legacy_subject=legacy_subject,
            legacy_html=legacy_html,
        )

        message = EmailMessage(
            to=email,
            subject=rendered.subject,
            html_content=rendered.html,
            from_name=rendered.from_name,
            reply_to=rendered.reply_to,
        )
        try:
            ok = await self.send_email(message)
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="instapay_payment_rejected",
                language=language,
                subject=rendered.subject,
                status="sent" if ok else "failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
            )
            return ok
        except Exception as exc:
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="instapay_payment_rejected",
                language=language,
                subject=rendered.subject,
                status="failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
                error_code=str(exc)[:100],
            )
            raise

    async def send_shipping_notification(
        self,
        email: str,
        order_number: str,
        tracking_number: str | None,
        carrier: str | None,
        language: str = "ar",
        *,
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
        store_name: str = "NUMU",
        customer_name: str | None = None,
    ) -> bool:
        """Send shipping notification email."""
        from src.infrastructure.external_services.resend.email_templates.notifications import (
            SHIPPING_NOTIFICATION_TEMPLATE,
        )

        legacy_html = SHIPPING_NOTIFICATION_TEMPLATE["html_fn"](
            order_number=order_number,
            tracking_number=tracking_number,
            carrier=carrier,
            language=language,
        )
        legacy_subject = SHIPPING_NOTIFICATION_TEMPLATE["subject_fn"](
            order_number, language=language
        )

        rendered = await self._render_or_legacy(
            event_type="shipping_notification",
            language=language,
            store_id=store_id,
            variables={
                "customer_name": customer_name or "",
                "order_number": order_number,
                "tracking_number": tracking_number or "",
                "carrier": carrier or "",
                "store_name": store_name,
            },
            legacy_subject=legacy_subject,
            legacy_html=legacy_html,
        )

        message = EmailMessage(
            to=email,
            subject=rendered.subject,
            html_content=rendered.html,
            from_name=rendered.from_name,
            reply_to=rendered.reply_to,
        )
        try:
            ok = await self.send_email(message)
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="shipping_notification",
                language=language,
                subject=rendered.subject,
                status="sent" if ok else "failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
            )
            return ok
        except Exception as exc:
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type="shipping_notification",
                language=language,
                subject=rendered.subject,
                status="failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
                error_code=str(exc)[:100],
            )
            raise

    # ------------------------------------------------------------------
    # Order-status events that don't have dedicated public methods today
    # but still benefit from per-merchant overrides + the audit log.
    # Called from `handle_email_notification` once `store_id` is known.
    # ------------------------------------------------------------------

    async def send_order_status_email(
        self,
        *,
        email: str,
        status: str,
        order_number: str,
        store_name: str = "NUMU",
        customer_name: str | None = None,
        tracking_number: str | None = None,
        carrier: str | None = None,
        reason: str | None = None,
        language: str = "ar",
        store_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> bool:
        """Send an arbitrary order-status email.

        Maps ``status`` to one of the registry event types
        (``order_confirmed`` / ``order_processing`` /
        ``shipping_notification`` / ``delivery_confirmation`` /
        ``order_cancelled`` / ``order_refunded``) and routes through the
        renderer so merchant overrides apply. Falls back to the legacy
        ``order_status_email`` helper for the body when no override
        exists.
        """
        from src.infrastructure.external_services.resend.email_templates.notifications import (
            order_status_email,
        )

        legacy = order_status_email(
            status=status,
            order_number=order_number,
            store_name=store_name,
            customer_name=customer_name,
            tracking_number=tracking_number,
            carrier=carrier,
            reason=reason,
            language=language,
        )
        if not legacy:
            return False

        # Map order-status -> registry event_type. Both 'order_confirmed'
        # and the legacy 'confirmed' status string round-trip cleanly.
        status_to_event = {
            "confirmed": "order_confirmed",
            "processing": "order_processing",
            "shipped": "shipping_notification",
            "delivered": "delivery_confirmation",
            "cancelled": "order_cancelled",
            "refunded": "order_refunded",
        }
        event_type = status_to_event.get(status, status)

        variables: dict = {
            "customer_name": customer_name or "",
            "order_number": order_number,
            "store_name": store_name,
        }
        if event_type == "shipping_notification":
            variables["tracking_number"] = tracking_number or ""
            variables["carrier"] = carrier or ""
        if event_type in {"order_cancelled", "order_refunded"}:
            variables["reason"] = reason or ""

        rendered = await self._render_or_legacy(
            event_type=event_type,
            language=language,
            store_id=store_id,
            variables=variables,
            legacy_subject=legacy["subject"],
            legacy_html=legacy["html"],
        )

        message = EmailMessage(
            to=email,
            subject=rendered.subject,
            html_content=rendered.html,
            from_name=rendered.from_name,
            reply_to=rendered.reply_to,
        )
        try:
            ok = await self.send_email(message)
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type=event_type,
                language=language,
                subject=rendered.subject,
                status="sent" if ok else "failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
            )
            return ok
        except Exception as exc:
            await self._log_send(
                store_id=store_id,
                tenant_id=tenant_id,
                recipient=email,
                event_type=event_type,
                language=language,
                subject=rendered.subject,
                status="failed",
                used_custom_template=rendered.used_custom,
                template_id=rendered.template_id,
                error_code=str(exc)[:100],
            )
            raise
