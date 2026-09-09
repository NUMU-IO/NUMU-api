"""Marketing outreach to leads, and a referral programme with milestones.

Three things the platform could not do before:

  1. REACH a lead. `merchant_leads` records everyone who reached for NUMU
     and their whole journey, and there was no way to send any of them a
     message. Every follow-up happened in someone's personal WhatsApp, so
     nothing was repeatable and nothing was recorded.
  2. SAY the same thing twice. `marketing_templates` holds the copy — a
     follow-up, a promotion, a referral invite — for both channels, editable
     from the backoffice rather than pasted from a document.
  3. PAY for a referral. `referral_rewards` is the ledger; the milestones a
     referral can hit are declared in code, because each one is a CONDITION
     that code has to evaluate. Only their AMOUNTS live in a table
     (`referral_milestone_settings`), which is the half a human actually
     changes. A table of milestones nobody evaluates would be a promise the
     platform silently fails to keep.

The two columns on `merchant_leads` are what makes a referral attributable:
a lead's own `referral_code` to share, and `referred_by_lead_id` recording
who brought them. Plain UUID with no FK, exactly like `tenant_id` and
`user_id` on that table — this table exists to outlive deletions, and a
cascade would reintroduce the loss it was built to prevent.

Revision ID: marketing_referrals_20260909
Revises: admin_push_devices_20260909
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "marketing_referrals_20260909"
down_revision: str | Sequence[str] | None = "admin_push_devices_20260909"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Defaults for the reward amounts, in minor units (piastres). Seeded here so
# the programme is live the moment it ships; every one is editable afterwards.
_MILESTONE_DEFAULTS = [
    ("referred_registered", 0, False),
    ("referred_store_created", 2500, True),
    ("referred_first_product", 0, False),
    ("referred_first_order", 10000, True),
    ("referred_first_commission", 5000, True),
]


def upgrade() -> None:
    # ── Attribution on the lead ─────────────────────────────────────────
    op.add_column(
        "merchant_leads",
        sa.Column("referral_code", sa.String(16), nullable=True),
        schema="public",
    )
    op.add_column(
        "merchant_leads",
        sa.Column("referred_by_lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="public",
    )
    op.create_index(
        "uq_merchant_leads_referral_code",
        "merchant_leads",
        ["referral_code"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("referral_code IS NOT NULL"),
    )
    op.create_index(
        "ix_merchant_leads_referred_by",
        "merchant_leads",
        ["referred_by_lead_id"],
        schema="public",
        postgresql_where=sa.text("referred_by_lead_id IS NOT NULL"),
    )

    # ── Templates ───────────────────────────────────────────────────────
    op.create_table(
        "marketing_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("language", sa.String(5), nullable=False, server_default="en"),
        sa.Column("name", sa.String(160), nullable=False),
        # Email only. A WhatsApp message has no subject line.
        sa.Column("subject", sa.String(300), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('email', 'whatsapp')", name="ck_marketing_templates_channel"
        ),
        # An email with no subject is not a sendable email. Enforced here
        # rather than in the route so a direct INSERT cannot create one.
        sa.CheckConstraint(
            "channel <> 'email' OR (subject IS NOT NULL AND subject <> '')",
            name="ck_marketing_templates_email_subject",
        ),
        sa.UniqueConstraint(
            "key", "channel", "language", name="uq_marketing_templates_key"
        ),
        schema="public",
    )

    # ── Outreach log ────────────────────────────────────────────────────
    op.create_table(
        "marketing_outreach",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # No FK, for the same reason merchant_leads has none: this record of
        # "we contacted this person" must survive the lead being removed.
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        # An email address or an E.164 phone, depending on channel.
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("template_key", sa.String(64), nullable=True),
        sa.Column("subject", sa.String(300), nullable=True),
        # The RENDERED body, not the template. What we actually said is the
        # only version worth keeping — a template edited next month would
        # otherwise rewrite history.
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('email', 'whatsapp')", name="ck_marketing_outreach_channel"
        ),
        sa.CheckConstraint(
            "status IN ('sent', 'failed', 'skipped')",
            name="ck_marketing_outreach_status",
        ),
        schema="public",
    )
    op.create_index(
        "ix_marketing_outreach_lead",
        "marketing_outreach",
        ["lead_id", "created_at"],
        schema="public",
    )
    op.create_index(
        "ix_marketing_outreach_created",
        "marketing_outreach",
        ["created_at"],
        schema="public",
    )

    # ── Referral reward ledger ──────────────────────────────────────────
    op.create_table(
        "referral_rewards",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("referrer_lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("referred_lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("milestone", sa.String(48), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "earned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'paid', 'void')",
            name="ck_referral_rewards_status",
        ),
        sa.CheckConstraint("amount_cents >= 0", name="ck_referral_rewards_amount"),
        # THE idempotency guarantee. Accrual runs from several places — a
        # lead milestone handler, a lead update, an admin recalculate — and
        # without this a merchant's first order would pay their referrer
        # once per code path that noticed it.
        sa.UniqueConstraint(
            "referred_lead_id", "milestone", name="uq_referral_rewards_earned_once"
        ),
        schema="public",
    )
    op.create_index(
        "ix_referral_rewards_referrer",
        "referral_rewards",
        ["referrer_lead_id", "status"],
        schema="public",
    )

    # ── Editable reward amounts ─────────────────────────────────────────
    # The CONDITIONS live in code (referral_service.MILESTONES) because each
    # one is a rule something has to evaluate. Only the money is data.
    op.create_table(
        "referral_milestone_settings",
        sa.Column("milestone", sa.String(48), primary_key=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount_cents >= 0", name="ck_referral_milestone_settings_amount"
        ),
        schema="public",
    )

    conn = op.get_bind()
    for milestone, amount, active in _MILESTONE_DEFAULTS:
        conn.execute(
            sa.text(
                "INSERT INTO public.referral_milestone_settings "
                "(milestone, amount_cents, is_active) "
                "VALUES (:m, :a, :active) ON CONFLICT (milestone) DO NOTHING"
            ),
            {"m": milestone, "a": amount, "active": active},
        )

    # ── Default copy ────────────────────────────────────────────────────
    # Seeded so the page is usable on first open rather than showing an
    # empty list and an invitation to write marketing copy from scratch.
    # `{{name}}`, `{{email}}`, `{{referral_code}}` and `{{referral_link}}`
    # are substituted at send time; an unknown placeholder is left alone.
    #
    # Every template exists in English and in EGYPTIAN ARABIC — the spoken
    # dialect, not Modern Standard. A merchant selling on Instagram in Cairo
    # does not write to their customers in MSA and does not want to be written
    # to in it; formal Arabic reads like a government letter and is exactly
    # the register that gets ignored.
    for key, channel, language, name, subject, body in _DEFAULT_TEMPLATES:
        conn.execute(
            sa.text(
                "INSERT INTO public.marketing_templates "
                "(key, channel, language, name, subject, body) "
                "VALUES (:k, :c, :lang, :n, :s, :b) "
                "ON CONFLICT (key, channel, language) DO NOTHING"
            ),
            {
                "k": key,
                "c": channel,
                "lang": language,
                "n": name,
                "s": subject,
                "b": body,
            },
        )


_DEFAULT_TEMPLATES = [
    # ── Follow-up ───────────────────────────────────────────────────────
    (
        "follow_up",
        "email",
        "en",
        "Follow-up",
        "Still thinking about your store, {{name}}?",
        (
            "<p>Hi {{name}},</p>"
            "<p>You started setting up a store on NUMU and did not finish. "
            "If something got in the way, reply to this email and a human "
            "will help you through it — no forms, no queue.</p>"
            "<p>Your setup is exactly where you left it.</p>"
            "<p>— The NUMU team</p>"
        ),
    ),
    (
        "follow_up",
        "email",
        "ar",
        "متابعة",
        "لسه فاكر متجرك يا {{name}}؟",
        (
            "<p>أهلاً {{name}},</p>"
            "<p>بدأت تجهّز متجرك على نُمو وماكمّلتش. لو حاجة وقفتك، ردّ على "
            "الإيميل ده وحد من الفريق هيساعدك خطوة بخطوة — من غير فورمات "
            "ولا انتظار.</p>"
            "<p>كل اللي عملته لسه موجود زي ما سيبته.</p>"
            "<p>— فريق نُمو</p>"
        ),
    ),
    (
        "follow_up",
        "whatsapp",
        "en",
        "Follow-up",
        None,
        (
            "Hi {{name}}, this is NUMU. You started setting up your store and "
            "did not finish. Reply here and we will help you through it — "
            "your setup is exactly where you left it."
        ),
    ),
    (
        "follow_up",
        "whatsapp",
        "ar",
        "متابعة",
        None,
        (
            "أهلاً {{name}}، أنا من نُمو. بدأت تجهّز متجرك وماكمّلتش. ردّ عليّا "
            "هنا وهساعدك تخلّصه — كل اللي عملته لسه موجود زي ما سيبته."
        ),
    ),
    # ── Promotion ───────────────────────────────────────────────────────
    (
        "promotion",
        "email",
        "en",
        "Promotion",
        "A month on us, {{name}}",
        (
            "<p>Hi {{name}},</p>"
            "<p>We are giving new merchants their first month free — every "
            "feature, no card needed. Launch your store this week and it "
            "applies automatically.</p>"
            "<p>— The NUMU team</p>"
        ),
    ),
    (
        "promotion",
        "email",
        "ar",
        "عرض",
        "شهر علينا يا {{name}}",
        (
            "<p>أهلاً {{name}},</p>"
            "<p>التجار الجدد بياخدوا أول شهر مجاناً — كل المميزات، ومن غير "
            "كارت. افتح متجرك الأسبوع ده والعرض هيتطبّق لوحده.</p>"
            "<p>— فريق نُمو</p>"
        ),
    ),
    (
        "promotion",
        "whatsapp",
        "en",
        "Promotion",
        None,
        (
            "Hi {{name}}, NUMU here. New merchants get their first month free "
            "— every feature, no card needed. Launch this week and it applies "
            "automatically."
        ),
    ),
    (
        "promotion",
        "whatsapp",
        "ar",
        "عرض",
        None,
        (
            "أهلاً {{name}}، أنا من نُمو. التجار الجدد بياخدوا أول شهر مجاناً "
            "— كل المميزات ومن غير كارت. افتح متجرك الأسبوع ده والعرض هيتطبّق "
            "لوحده."
        ),
    ),
    # ── Referral invite ─────────────────────────────────────────────────
    (
        "referral_invite",
        "email",
        "en",
        "Referral invite",
        "Earn EGP 100 for every merchant you bring, {{name}}",
        (
            "<p>Hi {{name}},</p>"
            "<p>Know someone selling on Instagram or WhatsApp who needs a real "
            "store? Send them your link and you earn as they grow:</p>"
            "<p><strong>{{referral_link}}</strong></p>"
            "<ul>"
            "<li>EGP 25 when they open their store</li>"
            "<li>EGP 100 on their first order</li>"
            "<li>EGP 50 when they start selling for real</li>"
            "</ul>"
            "<p>— The NUMU team</p>"
        ),
    ),
    (
        "referral_invite",
        "email",
        "ar",
        "دعوة صاحب",
        "اكسب ١٠٠ جنيه عن كل تاجر تجيبه يا {{name}}",
        (
            "<p>أهلاً {{name}},</p>"
            "<p>تعرف حد بيبيع على إنستجرام أو واتساب ومحتاج متجر حقيقي؟ ابعتله "
            "اللينك بتاعك وانت بتكسب معاه وهو بيكبر:</p>"
            "<p><strong>{{referral_link}}</strong></p>"
            "<ul>"
            "<li>٢٥ جنيه أول ما يفتح متجره</li>"
            "<li>١٠٠ جنيه على أول أوردر ليه</li>"
            "<li>٥٠ جنيه لما يبدأ يبيع بجد</li>"
            "</ul>"
            "<p>— فريق نُمو</p>"
        ),
    ),
    (
        "referral_invite",
        "whatsapp",
        "en",
        "Referral invite",
        None,
        (
            "Hi {{name}}, NUMU here. Know someone who needs a real store? Send "
            "them your link {{referral_link}} — you earn EGP 25 when they open "
            "their store, EGP 100 on their first order, and EGP 50 when they "
            "start selling for real."
        ),
    ),
    (
        "referral_invite",
        "whatsapp",
        "ar",
        "دعوة صاحب",
        None,
        (
            "أهلاً {{name}}، أنا من نُمو. تعرف حد محتاج متجر حقيقي؟ ابعتله "
            "اللينك ده {{referral_link}} — وانت تاخد ٢٥ جنيه أول ما يفتح "
            "متجره، ١٠٠ جنيه على أول أوردر، و٥٠ جنيه لما يبدأ يبيع بجد."
        ),
    ),
    # ── Win-back ────────────────────────────────────────────────────────
    (
        "win_back",
        "email",
        "en",
        "Win-back",
        "Your NUMU store is still here, {{name}}",
        (
            "<p>Hi {{name}},</p>"
            "<p>It has been a while. Your store, your products and your "
            "customers are all still where you left them.</p>"
            "<p>If something stopped working for you, tell us what — we would "
            "rather fix it than lose you.</p>"
            "<p>— The NUMU team</p>"
        ),
    ),
    (
        "win_back",
        "email",
        "ar",
        "رجوع",
        "متجرك على نُمو لسه مستنيك يا {{name}}",
        (
            "<p>أهلاً {{name}},</p>"
            "<p>بقالك فترة. متجرك ومنتجاتك وعملاؤك كلهم لسه زي ما سيبتهم.</p>"
            "<p>لو في حاجة وقفت معاك، قولنا عليها — نصلّحها أحسن ما نخسرك.</p>"
            "<p>— فريق نُمو</p>"
        ),
    ),
    (
        "win_back",
        "whatsapp",
        "en",
        "Win-back",
        None,
        (
            "Hi {{name}}, NUMU here. Your store, products and customers are all "
            "still where you left them. If something stopped working for you, "
            "tell us what — we would rather fix it than lose you."
        ),
    ),
    (
        "win_back",
        "whatsapp",
        "ar",
        "رجوع",
        None,
        (
            "أهلاً {{name}}، أنا من نُمو. متجرك ومنتجاتك وعملاؤك كلهم لسه زي ما "
            "سيبتهم. لو في حاجة وقفت معاك قولنا عليها — نصلّحها أحسن ما نخسرك."
        ),
    ),
]


def downgrade() -> None:
    op.drop_table("referral_milestone_settings", schema="public")
    op.drop_index(
        "ix_referral_rewards_referrer", table_name="referral_rewards", schema="public"
    )
    op.drop_table("referral_rewards", schema="public")
    op.drop_index(
        "ix_marketing_outreach_created",
        table_name="marketing_outreach",
        schema="public",
    )
    op.drop_index(
        "ix_marketing_outreach_lead", table_name="marketing_outreach", schema="public"
    )
    op.drop_table("marketing_outreach", schema="public")
    op.drop_table("marketing_templates", schema="public")
    op.drop_index(
        "ix_merchant_leads_referred_by", table_name="merchant_leads", schema="public"
    )
    op.drop_index(
        "uq_merchant_leads_referral_code", table_name="merchant_leads", schema="public"
    )
    op.drop_column("merchant_leads", "referred_by_lead_id", schema="public")
    op.drop_column("merchant_leads", "referral_code", schema="public")
