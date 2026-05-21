"""QR code generator for ETA e-invoices.

Egyptian e-invoices require a QR code containing:
- Seller name (Arabic)
- VAT registration number
- Invoice date/time
- Invoice total with VAT
- VAT amount

The QR code uses TLV (Tag-Length-Value) encoding as per
ZATCA specifications (similar to Saudi Arabia).
"""

import base64
from datetime import datetime
from io import BytesIO

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_L

    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


def _encode_tlv(tag: int, value: str) -> bytes:
    """Encode a value in TLV format.

    Args:
        tag: Tag number (1-5)
        value: Value to encode

    Returns:
        TLV encoded bytes
    """
    value_bytes = value.encode("utf-8")
    return bytes([tag, len(value_bytes)]) + value_bytes


def generate_eta_qr_data(
    seller_name: str,
    tax_number: str,
    invoice_date: datetime,
    total_with_vat: float,
    vat_amount: float,
) -> str:
    """Generate QR code data string for ETA e-invoice.

    The QR code contains TLV-encoded data with:
    - Tag 1: Seller name (Arabic preferred)
    - Tag 2: VAT registration number
    - Tag 3: Invoice timestamp (ISO format)
    - Tag 4: Invoice total including VAT
    - Tag 5: VAT amount

    Args:
        seller_name: Seller/issuer name (Arabic)
        tax_number: VAT registration number
        invoice_date: Invoice date and time
        total_with_vat: Total amount including VAT
        vat_amount: VAT amount

    Returns:
        Base64 encoded TLV data for QR code
    """
    # Format date as ISO string
    date_str = invoice_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Encode each field in TLV format
    tlv_data = b""
    tlv_data += _encode_tlv(1, seller_name)
    tlv_data += _encode_tlv(2, tax_number)
    tlv_data += _encode_tlv(3, date_str)
    tlv_data += _encode_tlv(4, f"{total_with_vat:.2f}")
    tlv_data += _encode_tlv(5, f"{vat_amount:.2f}")

    # Return base64 encoded string
    return base64.b64encode(tlv_data).decode("utf-8")


def generate_eta_qr_code(
    seller_name: str,
    tax_number: str,
    invoice_date: datetime,
    total_with_vat: float,
    vat_amount: float,
    size: int = 200,
) -> tuple[str, str | None]:
    """Generate QR code for ETA e-invoice.

    Args:
        seller_name: Seller name (Arabic)
        tax_number: VAT registration number
        invoice_date: Invoice date and time
        total_with_vat: Total including VAT
        vat_amount: VAT amount
        size: QR code image size in pixels

    Returns:
        Tuple of (qr_data_string, base64_image or None)
    """
    # Generate the data string
    qr_data = generate_eta_qr_data(
        seller_name=seller_name,
        tax_number=tax_number,
        invoice_date=invoice_date,
        total_with_vat=total_with_vat,
        vat_amount=vat_amount,
    )

    # Generate QR code image if library is available
    qr_image_b64 = None
    if HAS_QRCODE:
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(qr_data)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white")

            # Resize if needed
            if hasattr(img, "resize"):
                img = img.resize((size, size))

            # Convert to base64
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            qr_image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        except Exception:
            # If QR generation fails, just return the data
            pass

    return qr_data, qr_image_b64


def decode_eta_qr_data(qr_data: str) -> dict[str, str]:
    """Decode ETA QR code data.

    Args:
        qr_data: Base64 encoded TLV data

    Returns:
        Dictionary with decoded fields
    """
    try:
        data = base64.b64decode(qr_data)
        result = {}

        tag_names = {
            1: "seller_name",
            2: "tax_number",
            3: "invoice_date",
            4: "total_with_vat",
            5: "vat_amount",
        }

        i = 0
        while i < len(data):
            tag = data[i]
            length = data[i + 1]
            value = data[i + 2 : i + 2 + length].decode("utf-8")

            if tag in tag_names:
                result[tag_names[tag]] = value

            i += 2 + length

        return result

    except Exception:
        return {}
