"""Helper to compute Kashier webhook signature and generate payment URL."""

import hashlib
import hmac
import sys

API_KEY = "212774e4-c0cd-496b-925b-90b72ebf8595"
MID = "MID-44217-177"


def compute_signature(order_id: str, amount: str):
    """Compute HMAC SHA256 signature for a Kashier webhook."""
    qs = (
        f"paymentStatus=SUCCESS"
        f"&cardDataToken="
        f"&maskedCard=****4242"
        f"&merchantOrderId={order_id}"
        f"&orderId=KSH-001"
        f"&cardBrand=Visa"
        f"&orderReference=ref-1"
        f"&transactionId=txn-001"
        f"&amount={amount}"
        f"&currency=EGP"
    )
    sig = hmac.new(API_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return sig


def generate_payment_url(order_id: str, amount: str, hash_value: str):
    """Generate the Kashier Hosted Payment Page URL."""
    return (
        f"https://payments.kashier.io/"
        f"?mid={MID}"
        f"&orderId={order_id}"
        f"&amount={amount}"
        f"&currency=EGP"
        f"&hash={hash_value}"
        f"&mode=test"
        f"&merchantRedirect=http://localhost:8001/api/v1/webhooks/kashier/callback"
        f"&allowedMethods=card,wallet"
    )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python compute_kashier_sig.py <order_id> <amount>")
        print("Example: python compute_kashier_sig.py abc-123 500.00")
        print()
        print("Or with hash (to generate payment URL):")
        print("  python compute_kashier_sig.py <order_id> <amount> <hash>")
        sys.exit(1)

    order_id = sys.argv[1]
    amount = sys.argv[2]

    sig = compute_signature(order_id, amount)
    print(f"Webhook Signature: {sig}")
    print()

    if len(sys.argv) >= 4:
        hash_value = sys.argv[3]
        url = generate_payment_url(order_id, amount, hash_value)
        print(f"Payment URL (open in browser):")
        print(url)
    else:
        print("To generate payment URL, also pass the hash from checkout response:")
        print(f"  python compute_kashier_sig.py {order_id} {amount} <hash_from_payment_data>")
