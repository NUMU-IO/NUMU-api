"""Egyptian Tax Authority (ETA) e-invoicing service.

Integrates with the ETA e-invoicing portal for submitting
electronic invoices as required by Egyptian tax law.

API Documentation: https://sdk.invoicing.eta.gov.eg/
Portal: https://invoicing.eta.gov.eg/

Requirements:
- ETA portal registration
- Digital certificate (optional for testing)
- OAuth2 client credentials
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from src.config import settings
from src.core.entities.invoice import Invoice, InvoiceStatus
from src.infrastructure.external_services.eta.qr_generator import generate_eta_qr_code

logger = logging.getLogger(__name__)


class ETAInvoiceService:
    """ETA e-invoicing API service.

    Handles:
    - OAuth2 authentication with ETA
    - Invoice submission
    - Status checking
    - Document retrieval
    - QR code generation

    Workflow:
    1. Authenticate with client credentials
    2. Submit invoice document
    3. Receive submission UUID
    4. Check submission status
    5. Get final document with QR code
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        token_url: str | None = None,
    ) -> None:
        self.client_id = client_id or settings.eta_client_id
        self.client_secret = client_secret or settings.eta_client_secret
        self.base_url = base_url or settings.eta_base_url
        self.token_url = token_url or settings.eta_token_url
        self.enabled = settings.eta_enabled

        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    async def _get_access_token(self) -> str:
        """Get OAuth2 access token from ETA.

        Returns:
            Access token string

        Raises:
            ValueError: If authentication fails
        """
        # Check if we have a valid cached token
        if (
            self._access_token
            and self._token_expires_at
            and datetime.utcnow() < self._token_expires_at
        ):
            return self._access_token

        if not self.client_id or not self.client_secret:
            raise ValueError("ETA client credentials not configured")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "InvoicingAPI",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )

            if response.status_code != 200:
                logger.error(f"ETA auth failed: {response.text}")
                raise ValueError("Failed to authenticate with ETA")

            data = response.json()
            self._access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            self._token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 60)

            return self._access_token

    def _get_headers(self, token: str) -> dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _calculate_document_hash(self, document: dict) -> str:
        """Calculate SHA-256 hash of document.

        ETA requires a hash of the canonicalized document.

        Args:
            document: Invoice document dict

        Returns:
            Hex-encoded SHA-256 hash
        """
        # Canonicalize JSON (sorted keys, no whitespace)
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def submit_invoice(
        self,
        invoice: Invoice,
    ) -> dict[str, Any]:
        """Submit an invoice to ETA.

        Args:
            invoice: Invoice entity to submit

        Returns:
            Submission response with UUID and status

        Raises:
            ValueError: If submission fails
        """
        if not self.enabled:
            logger.warning("ETA e-invoicing is disabled")
            return {
                "success": False,
                "error": "ETA e-invoicing is disabled",
            }

        token = await self._get_access_token()

        # Convert invoice to ETA format
        document = invoice.to_eta_format()

        # Calculate document hash
        doc_hash = self._calculate_document_hash(document)

        # Prepare submission request
        submission = {
            "documents": [document],
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/documentsubmissions",
                headers=self._get_headers(token),
                json=submission,
                timeout=60.0,
            )

            if response.status_code not in (200, 201, 202):
                logger.error(f"ETA submission failed: {response.text}")
                error_data = response.json() if response.text else {}
                return {
                    "success": False,
                    "error": error_data.get("error", "Submission failed"),
                    "details": error_data,
                }

            data = response.json()
            submission_id = data.get("submissionId")

            # Get the accepted/rejected documents
            accepted = data.get("acceptedDocuments", [])
            rejected = data.get("rejectedDocuments", [])

            if accepted:
                doc = accepted[0]
                return {
                    "success": True,
                    "submission_id": submission_id,
                    "uuid": doc.get("uuid"),
                    "long_id": doc.get("longId"),
                    "internal_id": doc.get("internalId"),
                    "hash": doc_hash,
                    "status": "accepted",
                }
            elif rejected:
                doc = rejected[0]
                return {
                    "success": False,
                    "submission_id": submission_id,
                    "internal_id": doc.get("internalId"),
                    "error": doc.get("error", {}).get("message", "Document rejected"),
                    "error_details": doc.get("error", {}),
                    "status": "rejected",
                }
            else:
                return {
                    "success": True,
                    "submission_id": submission_id,
                    "status": "pending",
                }

    async def get_submission_status(
        self,
        submission_id: str,
    ) -> dict[str, Any]:
        """Get the status of a submission.

        Args:
            submission_id: ETA submission ID

        Returns:
            Submission status details
        """
        token = await self._get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/documentsubmissions/{submission_id}",
                headers=self._get_headers(token),
                timeout=30.0,
            )

            if response.status_code != 200:
                logger.error(f"ETA status check failed: {response.text}")
                return {"error": "Failed to get submission status"}

            return response.json()

    async def get_document(
        self,
        uuid: str,
    ) -> dict[str, Any]:
        """Get a submitted document by UUID.

        Args:
            uuid: ETA document UUID

        Returns:
            Full document details
        """
        token = await self._get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/documents/{uuid}/raw",
                headers=self._get_headers(token),
                timeout=30.0,
            )

            if response.status_code != 200:
                logger.error(f"ETA document fetch failed: {response.text}")
                return {"error": "Failed to get document"}

            return response.json()

    async def get_document_printable(
        self,
        uuid: str,
        long_id: str,
    ) -> str | None:
        """Get the printable URL for a document.

        Args:
            uuid: ETA document UUID
            long_id: ETA long ID

        Returns:
            URL for printable version
        """
        return f"https://invoicing.eta.gov.eg/print/{uuid}/{long_id}"

    async def cancel_document(
        self,
        uuid: str,
        reason: str,
    ) -> dict[str, Any]:
        """Request cancellation of a submitted document.

        Note: Cancellation requires ETA approval and may not be immediate.

        Args:
            uuid: ETA document UUID
            reason: Reason for cancellation

        Returns:
            Cancellation request result
        """
        token = await self._get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{self.base_url}/documents/state/{uuid}/state",
                headers=self._get_headers(token),
                json={
                    "status": "cancelled",
                    "reason": reason,
                },
                timeout=30.0,
            )

            if response.status_code not in (200, 202):
                logger.error(f"ETA cancel failed: {response.text}")
                return {"success": False, "error": "Cancellation failed"}

            return {"success": True, "data": response.json()}

    async def process_invoice_submission(
        self,
        invoice: Invoice,
    ) -> Invoice:
        """Submit invoice and update with ETA response.

        This is a convenience method that:
        1. Submits the invoice to ETA
        2. Updates the invoice with ETA response
        3. Generates QR code

        Args:
            invoice: Invoice to submit

        Returns:
            Updated Invoice with ETA data
        """
        if not self.enabled:
            logger.warning("ETA disabled, skipping submission")
            invoice.status = InvoiceStatus.DRAFT
            return invoice

        # Mark as pending
        invoice.status = InvoiceStatus.PENDING
        invoice.touch()

        # Submit to ETA
        result = await self.submit_invoice(invoice)

        if result.get("success"):
            # Update invoice with ETA data
            invoice.eta_uuid = result.get("uuid")
            invoice.eta_long_id = result.get("long_id")
            invoice.eta_submission_id = result.get("submission_id")
            invoice.eta_internal_id = result.get("internal_id")
            invoice.eta_hash = result.get("hash")

            if result.get("status") == "accepted":
                invoice.status = InvoiceStatus.ACCEPTED
                invoice.eta_status_code = "accepted"

                # Generate QR code
                if invoice.seller and invoice.eta_uuid:
                    qr_data, qr_image = generate_eta_qr_code(
                        seller_name=invoice.seller.name_ar or invoice.seller.name,
                        tax_number=invoice.seller.tax_id,
                        invoice_date=invoice.date_issued,
                        total_with_vat=invoice.total / 100,
                        vat_amount=invoice.total_taxes / 100,
                    )
                    invoice.qr_code_data = qr_data
                    invoice.qr_code_image = qr_image

            elif result.get("status") == "pending":
                invoice.status = InvoiceStatus.SUBMITTED
                invoice.eta_status_code = "pending"

        else:
            # Submission failed or rejected
            invoice.status = InvoiceStatus.REJECTED
            invoice.eta_status_code = result.get("status", "error")
            invoice.eta_status_message = result.get("error")

        invoice.touch()
        return invoice

    async def validate_document(
        self,
        document: dict,
    ) -> dict[str, Any]:
        """Validate a document without submitting.

        Useful for checking document format before actual submission.

        Args:
            document: Invoice document in ETA format

        Returns:
            Validation result
        """
        token = await self._get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/documentsubmissions/validate",
                headers=self._get_headers(token),
                json={"documents": [document]},
                timeout=30.0,
            )

            if response.status_code != 200:
                return {"valid": False, "error": response.text}

            data = response.json()
            return {
                "valid": len(data.get("rejectedDocuments", [])) == 0,
                "accepted": data.get("acceptedDocuments", []),
                "rejected": data.get("rejectedDocuments", []),
            }

    async def search_documents(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Search submitted documents.

        Args:
            date_from: Start date filter
            date_to: End date filter
            status: Status filter
            page: Page number
            page_size: Results per page

        Returns:
            Search results
        """
        token = await self._get_access_token()

        params = {
            "pageNo": page,
            "pageSize": page_size,
        }

        if date_from:
            params["issueDateFrom"] = date_from.strftime("%Y-%m-%d")
        if date_to:
            params["issueDateTo"] = date_to.strftime("%Y-%m-%d")
        if status:
            params["status"] = status

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/documents/recent",
                headers=self._get_headers(token),
                params=params,
                timeout=30.0,
            )

            if response.status_code != 200:
                logger.error(f"ETA search failed: {response.text}")
                return {"error": "Search failed"}

            return response.json()
