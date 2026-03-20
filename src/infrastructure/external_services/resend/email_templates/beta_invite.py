"""Beta invite email template.

Sent to waitlist entries when an admin invites them to create a store.
"""


def beta_invite_html(name: str | None, invite_code: str) -> str:
    """Render the beta invite email.

    Args:
        name: Recipient name (optional).
        invite_code: The unique invite code for store creation.

    Returns:
        HTML string for the email body.
    """
    greeting = f"Hi {name}," if name else "Hi there,"

    return f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0; padding:0; font-family:'Segoe UI',Arial,sans-serif; background:#f4f4f5; color:#1a1a2e; line-height:1.6;">
<div style="max-width:600px; margin:0 auto; background:#ffffff;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#D4AF37 0%,#1034A6 100%); padding:36px 24px; text-align:center;">
        <h1 style="color:#fff; margin:0; font-size:26px; font-weight:700; letter-spacing:-0.3px;">
            You're Invited!
        </h1>
        <p style="color:rgba(255,255,255,0.85); margin:8px 0 0; font-size:15px;">
            NUMU Beta — Early Merchant Access
        </p>
    </div>

    <!-- Body -->
    <div style="padding:32px 24px;">
        <p style="margin:0 0 16px; font-size:15px;">{greeting}</p>

        <p style="margin:0 0 16px; font-size:15px;">
            Great news — you've been selected for the <strong>NUMU beta program</strong>.
            You can now create your store and start selling with Egypt's next-generation
            e-commerce platform.
        </p>

        <!-- Invite Code Box -->
        <div style="background:#f8f9fa; border:2px solid #1034A6; border-radius:10px; padding:24px; margin:24px 0; text-align:center;">
            <p style="margin:0 0 6px; font-size:12px; color:#6c757d; text-transform:uppercase; letter-spacing:1px;">
                YOUR BETA INVITE CODE
            </p>
            <p style="margin:0; font-size:28px; font-weight:700; color:#1034A6; letter-spacing:2px; font-family:monospace;">
                {invite_code[:16]}
            </p>
        </div>

        <p style="margin:0 0 16px; font-size:15px;">
            Use this code when creating your store. It's single-use and tied to your account.
        </p>

        <!-- CTA Button -->
        <div style="text-align:center; margin:28px 0;">
            <a href="https://numueg.app/register?invite={invite_code}"
               style="display:inline-block; padding:14px 36px; background:#D4AF37; color:#fff; text-decoration:none; border-radius:6px; font-weight:700; font-size:15px;">
                Create Your Store
            </a>
        </div>

        <div style="height:1px; background:#e9ecef; margin:24px 0;"></div>

        <p style="margin:0 0 12px; font-size:14px; font-weight:600;">What you get as a beta merchant:</p>
        <ul style="margin:0 0 16px; padding-left:20px; font-size:14px; color:#495057;">
            <li style="margin-bottom:6px;">Full platform access — storefront, payments, shipping</li>
            <li style="margin-bottom:6px;">Egyptian payment gateways (Paymob, Fawry, COD)</li>
            <li style="margin-bottom:6px;">ETA e-invoicing compliance built in</li>
            <li style="margin-bottom:6px;">Priority support during beta</li>
            <li style="margin-bottom:6px;">Founding merchant pricing (locked in forever)</li>
        </ul>

        <p style="margin:24px 0 0; font-size:13px; color:#6c757d;">
            This invite expires in 7 days. If you have questions, reply to this email
            and we'll get back to you within 24 hours.
        </p>
    </div>

    <!-- Footer -->
    <div style="padding:24px; text-align:center; background:#f8f9fa;">
        <p style="margin:0; font-size:12px; color:#999;">
            NUMU — E-commerce for Egyptian merchants
        </p>
    </div>

</div>
</body>
</html>
"""
