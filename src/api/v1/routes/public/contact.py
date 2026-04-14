"""Public contact form endpoint — no auth required.

URL: POST /api/v1/public/contact
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

from src.api.dependencies.services import get_email_service
from src.api.responses import SuccessResponse
from src.config import settings
from src.core.interfaces.services.email_service import EmailMessage, IEmailService

logger = logging.getLogger(__name__)

router = APIRouter()


class ContactRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    country: str
    city: str
    message: str


@router.post(
    "/contact",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit contact form",
    operation_id="submit_contact_form",
)
async def submit_contact(
    request: ContactRequest,
    email_service: Annotated[IEmailService, Depends(get_email_service)],
):
    """Receive a contact form submission and forward it via email."""
    try:
        html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px">
          <h2 style="color:#1e3a8a;margin-bottom:16px">New Contact Form Submission</h2>
          <table style="width:100%;border-collapse:collapse">
            <tr><td style="padding:8px 12px;font-weight:bold;color:#374151;border-bottom:1px solid #e5e7eb">Name</td><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">{request.name}</td></tr>
            <tr><td style="padding:8px 12px;font-weight:bold;color:#374151;border-bottom:1px solid #e5e7eb">Email</td><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">{request.email}</td></tr>
            <tr><td style="padding:8px 12px;font-weight:bold;color:#374151;border-bottom:1px solid #e5e7eb">Phone</td><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb" dir="ltr">{request.phone}</td></tr>
            <tr><td style="padding:8px 12px;font-weight:bold;color:#374151;border-bottom:1px solid #e5e7eb">Country</td><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">{request.country}</td></tr>
            <tr><td style="padding:8px 12px;font-weight:bold;color:#374151;border-bottom:1px solid #e5e7eb">City</td><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">{request.city}</td></tr>
          </table>
          <div style="margin-top:16px;padding:16px;background:#f9fafb;border-radius:12px">
            <p style="font-weight:bold;color:#374151;margin-bottom:8px">Message:</p>
            <p style="color:#4b5563;white-space:pre-wrap">{request.message}</p>
          </div>
        </div>
        """

        await email_service.send_email(
            EmailMessage(
                to=settings.resend_forward_to,
                subject=f"[NUMU Contact] {request.name} — {request.country}, {request.city}",
                html_content=html,
                reply_to=request.email,
            )
        )

        return SuccessResponse(data={"sent": True}, message="Message sent successfully")

    except Exception:
        logger.exception("Failed to send contact form email")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send message. Please try again later.",
        )
