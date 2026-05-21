from fpdf import FPDF


class Phase2PDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            self.set_font("Helvetica", "B", 18)
            self.cell(
                0,
                12,
                "NUMU API - Days 31-60: Make It Reliable",
                new_x="LMARGIN",
                new_y="NEXT",
                align="C",
            )
            self.set_font("Helvetica", "", 10)
            self.set_text_color(100, 100, 100)
            self.cell(
                0,
                6,
                "2-Week Sprint  |  Yousef & Yahia",
                new_x="LMARGIN",
                new_y="NEXT",
                align="C",
            )
            self.set_text_color(0, 0, 0)
            self.ln(2)
            self.set_draw_color(50, 50, 50)
            self.set_line_width(0.5)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(
            0,
            10,
            f"NUMU API Phase 2 - Days 31-60  |  Page {self.page_no()}/{{nb}}",
            align="C",
        )

    def section_title(self, title, r=30, g=100, b=180):
        self.set_font("Helvetica", "B", 13)
        self.set_fill_color(r, g, b)
        self.set_text_color(255, 255, 255)
        self.cell(0, 9, f"  {title}", new_x="LMARGIN", new_y="NEXT", fill=True)
        self.set_text_color(0, 0, 0)
        self.ln(3)

    def sub_section(self, title):
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(80, 80, 80)
        self.multi_cell(0, 4, title)
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def person_header(self, name, role):
        self.set_font("Helvetica", "B", 11)
        self.set_fill_color(240, 240, 240)
        self.set_draw_color(180, 180, 180)
        self.cell(
            0,
            8,
            f"  {name} - {role}",
            new_x="LMARGIN",
            new_y="NEXT",
            fill=True,
            border=1,
        )
        self.ln(2)

    def task_table_header(self):
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(50, 50, 50)
        self.set_text_color(255, 255, 255)
        self.cell(8, 7, "#", border=1, align="C", fill=True)
        self.cell(82, 7, "Task", border=1, align="C", fill=True)
        self.cell(100, 7, "Files to Create / Modify", border=1, align="C", fill=True)
        self.ln()
        self.set_text_color(0, 0, 0)

    def task_row(self, num, task, files, shade=False):
        self.set_font("Helvetica", "", 7.5)
        if shade:
            self.set_fill_color(245, 245, 255)
        else:
            self.set_fill_color(255, 255, 255)

        task_lines = self._wrap(task, 80)
        file_lines = self._wrap(files, 98)
        max_lines = max(len(task_lines), len(file_lines))
        row_h = max(max_lines * 4.5, 6)

        if self.get_y() + row_h > 270:
            self.add_page()
            self.task_table_header()

        y0 = self.get_y()
        self.cell(8, row_h, str(num), border=1, align="C", fill=shade)
        x = self.get_x()
        self.multi_cell(82, 4.5, "\n".join(task_lines), border=1, fill=shade)
        y1 = self.get_y()
        self.set_xy(x + 82, y0)
        self.multi_cell(100, 4.5, "\n".join(file_lines), border=1, fill=shade)
        y2 = self.get_y()
        self.set_y(max(y1, y2))

    def pr_table_header(self):
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(50, 50, 50)
        self.set_text_color(255, 255, 255)
        self.cell(12, 7, "PR", border=1, align="C", fill=True)
        self.cell(82, 7, "PR Name", border=1, align="C", fill=True)
        self.cell(48, 7, "Depends On", border=1, align="C", fill=True)
        self.cell(20, 7, "Owner", border=1, align="C", fill=True)
        self.cell(28, 7, "Files Touched", border=1, align="C", fill=True)
        self.ln()
        self.set_text_color(0, 0, 0)

    def pr_row(self, num, name, depends, owner, scope, shade=False):
        self.set_font("Helvetica", "", 7.5)
        if shade:
            self.set_fill_color(245, 245, 255)
        else:
            self.set_fill_color(255, 255, 255)

        name_lines = self._wrap(name, 80)
        dep_lines = self._wrap(depends, 46)
        scope_lines = self._wrap(scope, 26)
        max_lines = max(len(name_lines), len(dep_lines), len(scope_lines), 1)
        row_h = max(max_lines * 4.5, 7)

        if self.get_y() + row_h > 270:
            self.add_page()
            self.pr_table_header()

        y0 = self.get_y()

        self.set_font("Helvetica", "B", 8)
        self.cell(12, row_h, f"#{num}", border=1, align="C", fill=shade)

        self.set_font("Helvetica", "", 7.5)
        x = self.get_x()
        self.multi_cell(82, 4.5, "\n".join(name_lines), border=1, fill=shade)
        y1 = self.get_y()

        self.set_xy(x + 82, y0)
        self.multi_cell(48, 4.5, "\n".join(dep_lines), border=1, fill=shade)
        y2 = self.get_y()

        self.set_xy(x + 82 + 48, y0)
        color = (30, 100, 180) if owner == "Yousef" else (40, 160, 80)
        self.set_text_color(*color)
        self.set_font("Helvetica", "B", 7.5)
        self.cell(20, row_h, owner, border=1, align="C", fill=shade)
        self.set_text_color(0, 0, 0)

        self.set_font("Helvetica", "", 6.5)
        self.cell(28, row_h, scope, border=1, align="C", fill=shade)

        final_y = max(y0 + row_h, y1, y2)
        self.set_y(final_y)

    def timeline_line(self, person, lines, color):
        self.set_font("Helvetica", "B", 9)
        r, g, b = color
        self.set_text_color(r, g, b)
        self.cell(0, 6, person, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.set_font("Courier", "", 7)
        for line in lines:
            self.cell(5, 4.5, "")
            self.cell(0, 4.5, line, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def _wrap(self, text, w):
        self.set_font("Helvetica", "", 7.5)
        words = text.split(" ")
        lines, cur = [], ""
        for word in words:
            test = f"{cur} {word}".strip() if cur else word
            if self.get_string_width(test) < w - 2:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines if lines else [""]


def build_pdf():
    pdf = Phase2PDF("P", "mm", "A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Team
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Team:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(
        0,
        5,
        "  Yousef  -  Security, image pipeline & onboarding logic",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(
        0,
        5,
        "  Yahia   -  Observability, notifications & load testing",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(3)

    # ========== WEEK 1 ==========
    pdf.section_title("WEEK 1: Security + Observability", 30, 100, 180)
    pdf.sub_section(
        "Current State: No PostgreSQL RLS | No 2FA/MFA | No security headers | "
        "No Sentry | Basic string logging (no JSON) | No staging docker-compose | "
        "Audit service interface defined but incomplete"
    )

    # Yousef W1
    pdf.person_header("Yousef", "Security & Hardening")
    pdf.task_table_header()
    yousef_w1 = [
        (
            "1",
            "Create PostgreSQL RLS policies for tenant-scoped tables (stores, products, orders, customers, categories, invoices, addresses, coupons)",
            "alembic/versions/xxx_add_rls_policies.py",
        ),
        (
            "2",
            "Create RLS helper function: set_tenant_context() called before each query + update DB connection to SET app.current_tenant",
            "src/infrastructure/database/connection.py (modify), src/infrastructure/tenancy/rls.py",
        ),
        (
            "3",
            "Add RLS integration tests - verify cross-tenant data isolation at DB level",
            "tests/security/test_rls_isolation.py",
        ),
        (
            "4",
            "Create 2FA entity + TOTP service using pyotp (generate secret, generate QR URI, verify code)",
            "src/core/entities/two_factor.py, src/infrastructure/external_services/totp_service.py",
        ),
        (
            "5",
            "Create 2FA use cases: Enable2FA (return QR + backup codes), Verify2FA, Disable2FA + generate 10 backup codes on enable",
            "src/application/use_cases/auth/two_factor/",
        ),
        (
            "6",
            "Create 2FA API routes: POST /auth/2fa/enable, POST /auth/2fa/verify, DELETE /auth/2fa/disable + add 2FA check in login flow",
            "src/api/v1/routes/auth.py (modify), src/api/v1/schemas/auth.py (modify)",
        ),
        (
            "7",
            "Create security headers middleware: X-Frame-Options, X-Content-Type-Options, Strict-Transport-Security, CSP, X-XSS-Protection, Referrer-Policy, Permissions-Policy",
            "src/api/middleware/security_headers.py",
        ),
        (
            "8",
            "Register security headers middleware in app + add security tests (verify headers present, 2FA flow, RLS bypass attempts)",
            "src/main.py (modify), tests/security/test_security_headers.py, tests/security/test_2fa.py",
        ),
    ]
    for i, (n, t, f) in enumerate(yousef_w1):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(4)

    # Yahia W1
    pdf.person_header("Yahia", "Observability & Staging")
    pdf.task_table_header()
    yahia_w1 = [
        (
            "1",
            "Install sentry-sdk + initialize Sentry in app startup with environment, release, traces_sample_rate + capture unhandled exceptions",
            "pyproject.toml (modify), src/main.py (modify), src/config/settings.py (modify)",
        ),
        (
            "2",
            "Add Sentry middleware: capture request context (user_id, tenant_id, path) as tags + performance transaction tracking",
            "src/api/middleware/sentry_middleware.py",
        ),
        (
            "3",
            "Replace string logging with structlog: JSON formatter, bound loggers with request_id, tenant_id, user_id context propagation",
            "src/api/middleware/logging.py (rewrite), src/config/logging_config.py",
        ),
        (
            "4",
            "Add structured log calls across key flows: auth (login/register/2fa), orders (create/status), payments (webhook), errors",
            "src/application/use_cases/ (modify multiple), src/api/v1/routes/webhooks/ (modify)",
        ),
        (
            "5",
            "Enhance health check: add DB connectivity, Redis ping, Sentry DSN status, disk space + return detailed JSON status",
            "src/api/v1/routes/health.py (modify)",
        ),
        (
            "6",
            "Create docker-compose.staging.yml: Nginx reverse proxy + SSL termination, PostgreSQL with WAL archiving, Redis sentinel, API with staging env vars",
            "docker/docker-compose.staging.yml, docker/nginx/nginx.conf, docker/nginx/ssl/",
        ),
        (
            "7",
            "Create .env.staging template + staging deployment script + update Makefile with staging commands",
            ".env.staging, scripts/deploy_staging.sh, Makefile (modify)",
        ),
        (
            "8",
            "Write observability tests: verify Sentry captures errors, structured logs contain required fields, health endpoint returns all checks",
            "tests/integration/test_observability.py",
        ),
    ]
    for i, (n, t, f) in enumerate(yahia_w1):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(5)

    # ========== WEEK 2 ==========
    pdf.section_title("WEEK 2: Media + Notifications + Onboarding", 40, 160, 80)
    pdf.sub_section(
        "Current State: Image upload exists (R2) but NO optimization/resize | "
        "WhatsApp service IMPLEMENTED (template-based) but NOT wired to order events | "
        "Resend email service IMPLEMENTED | No onboarding wizard | No load tests"
    )

    # Yousef W2
    pdf.person_header("Yousef", "Image Pipeline & Onboarding")
    pdf.task_table_header()
    yousef_w2 = [
        (
            "1",
            "Create ImageProcessor service using Pillow: resize (thumbnail 150px, medium 600px, large 1200px), compress quality 85%, convert to WebP, strip EXIF metadata",
            "src/infrastructure/external_services/image/image_processor.py",
        ),
        (
            "2",
            "Create image optimization pipeline: on upload -> optimize original + generate 3 size variants + upload all to R2 + return URLs dict",
            "src/infrastructure/external_services/image/image_pipeline.py",
        ),
        (
            "3",
            "Integrate image pipeline into UploadProductImageUseCase: replace direct upload with pipeline, store variant URLs in product.media_urls",
            "src/application/use_cases/products/upload_image.py (modify)",
        ),
        (
            "4",
            "Create Celery task for async image processing (background optimization for bulk uploads)",
            "src/infrastructure/messaging/tasks/image_tasks.py",
        ),
        (
            "5",
            "Create OnboardingStep entity + MerchantOnboardingUseCase: track steps (create_store, add_product, configure_payment, add_shipping, first_order) with completion %",
            "src/core/entities/onboarding.py, src/application/use_cases/stores/onboarding.py",
        ),
        (
            "6",
            "Create onboarding progress routes: GET /stores/{store_id}/onboarding (get progress), POST /stores/{store_id}/onboarding/skip/{step}",
            "src/api/v1/routes/stores/onboarding.py, src/api/v1/schemas/tenant/onboarding.py",
        ),
        (
            "7",
            "Auto-trigger onboarding step completion: hook into create_store, create_product, configure_payment use cases to mark steps done",
            "src/application/use_cases/stores/ (modify), src/application/use_cases/products/ (modify)",
        ),
        (
            "8",
            "Write tests for image pipeline (resize dimensions, WebP output, EXIF stripped) + onboarding flow tests",
            "tests/unit/test_image_pipeline.py, tests/unit/test_onboarding.py",
        ),
    ]
    for i, (n, t, f) in enumerate(yousef_w2):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(4)

    # Yahia W2
    pdf.person_header("Yahia", "Notifications & Load Testing")
    pdf.task_table_header()
    yahia_w2 = [
        (
            "1",
            "Wire WhatsApp to order events: send order_confirmation on checkout, shipping_update on ship(), delivery_confirmation on deliver()",
            "src/application/use_cases/orders/checkout.py (modify), src/application/use_cases/orders/update_order_status.py (modify)",
        ),
        (
            "2",
            "Wire email notifications to order events: order confirmation email, shipping notification email, delivery email via Resend service",
            "src/application/use_cases/orders/checkout.py (modify), src/application/use_cases/orders/update_order_status.py (modify)",
        ),
        (
            "3",
            "Create Celery tasks for async notification dispatch (don't block order flow): send_order_email_task, send_whatsapp_task",
            "src/infrastructure/messaging/tasks/notification_tasks.py",
        ),
        (
            "4",
            "Create notification preferences: allow customers to opt-in/out of WhatsApp + email per event type + store in customer profile",
            "src/core/entities/customer.py (modify), src/api/v1/routes/storefront/customer.py (modify), alembic/versions/xxx_add_notification_prefs.py",
        ),
        (
            "5",
            "Create welcome email + onboarding email sequence: send on merchant registration, 1st product added, 1st order received (Celery scheduled)",
            "src/infrastructure/messaging/tasks/onboarding_email_tasks.py, src/infrastructure/external_services/resend/email_templates/ (new templates)",
        ),
        (
            "6",
            "Create Locust load test suite: auth flow, browse products, add to cart, checkout, order status update - target 100 concurrent merchants",
            "tests/load/locustfile.py, tests/load/README.md",
        ),
        (
            "7",
            "Create Locust config with different profiles: smoke (10 users), load (100 users), stress (500 users) + Makefile targets",
            "tests/load/locust.conf, Makefile (modify)",
        ),
        (
            "8",
            "Write notification tests: verify WhatsApp called on order events, email sent, notification preferences respected, load test smoke run",
            "tests/integration/test_notifications.py, tests/integration/test_notification_prefs.py",
        ),
    ]
    for i, (n, t, f) in enumerate(yahia_w2):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(5)

    # ========== PR ORDER ==========
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(
        0,
        10,
        "PR Dependency Order (No Conflicts)",
        new_x="LMARGIN",
        new_y="NEXT",
        align="C",
    )
    pdf.ln(2)

    # File scope legend
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(
        0,
        5,
        "File scope column shows which directory each PR touches - PRs touching different directories cannot conflict.",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    # Week 1 PRs
    pdf.section_title("Week 1 PRs: Security + Observability", 30, 100, 180)
    pdf.pr_table_header()

    w1_prs = [
        (
            "20",
            "feat: PostgreSQL RLS policies + tenant context helper",
            "None (independent)",
            "Yousef",
            "alembic/, tenancy/",
        ),
        (
            "21",
            "feat: security headers middleware",
            "None (independent)",
            "Yousef",
            "middleware/",
        ),
        (
            "22",
            "feat: Sentry SDK integration + middleware",
            "None (independent)",
            "Yahia",
            "main.py, config/",
        ),
        (
            "23",
            "feat: structured logging with structlog (JSON)",
            "None (independent)",
            "Yahia",
            "middleware/logging",
        ),
        (
            "24",
            "feat: 2FA entity + TOTP service + backup codes",
            "None (independent)",
            "Yousef",
            "core/entities/, ext_svc/",
        ),
        (
            "25",
            "feat: 2FA use cases + API routes (enable/verify/disable)",
            "PR #24",
            "Yousef",
            "use_cases/auth/, routes/",
        ),
        (
            "26",
            "feat: enhanced health check (DB, Redis, Sentry status)",
            "PR #22",
            "Yahia",
            "routes/health.py",
        ),
        (
            "27",
            "feat: structured logs in key flows (auth, orders, payments)",
            "PR #23",
            "Yahia",
            "use_cases/, webhooks/",
        ),
        (
            "28",
            "infra: docker-compose.staging + Nginx + .env.staging + deploy script",
            "None (independent)",
            "Yahia",
            "docker/, scripts/",
        ),
        (
            "29",
            "test: RLS isolation + security headers + 2FA + observability",
            "PR #20, #21, #25, #27",
            "Yousef",
            "tests/",
        ),
    ]
    for i, row in enumerate(w1_prs):
        pdf.pr_row(*row, shade=(i % 2 == 1))

    pdf.ln(4)

    # Week 2 PRs
    pdf.section_title("Week 2 PRs: Media + Notifications + Onboarding", 40, 160, 80)
    pdf.pr_table_header()

    w2_prs = [
        (
            "30",
            "feat: ImageProcessor service (resize, WebP, compress, strip EXIF)",
            "None (independent)",
            "Yousef",
            "ext_svc/image/",
        ),
        (
            "31",
            "feat: image optimization pipeline + Celery async task",
            "PR #30",
            "Yousef",
            "ext_svc/image/, tasks/",
        ),
        (
            "32",
            "feat: integrate pipeline into product image upload",
            "PR #31",
            "Yousef",
            "use_cases/products/",
        ),
        (
            "33",
            "feat: wire WhatsApp + email to order events via Celery tasks",
            "None (independent)",
            "Yahia",
            "use_cases/orders/, tasks/",
        ),
        (
            "34",
            "feat: notification preferences (opt-in/out) + migration",
            "None (independent)",
            "Yahia",
            "entities/customer, alembic/",
        ),
        (
            "35",
            "feat: onboarding entity + wizard use case + progress tracking",
            "None (independent)",
            "Yousef",
            "core/, use_cases/stores/",
        ),
        (
            "36",
            "feat: onboarding routes + auto-trigger step completion",
            "PR #35",
            "Yousef",
            "routes/stores/, schemas/",
        ),
        (
            "37",
            "feat: welcome + onboarding email sequence (Celery scheduled)",
            "PR #34",
            "Yahia",
            "tasks/, resend/templates/",
        ),
        (
            "38",
            "feat: Locust load test suite (smoke/load/stress profiles)",
            "None (independent)",
            "Yahia",
            "tests/load/",
        ),
        (
            "39",
            "test: image pipeline + onboarding + notifications + load smoke",
            "PR #32, #36, #33, #38",
            "Both",
            "tests/",
        ),
    ]
    for i, row in enumerate(w2_prs):
        pdf.pr_row(*row, shade=(i % 2 == 1))

    pdf.ln(5)

    # ========== TIMELINE ==========
    pdf.section_title("Parallel Work Timeline", 100, 100, 100)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Week 1:", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.timeline_line(
        "Yousef:",
        [
            "[PR#20 RLS] --> [PR#21 Sec Headers] --> [PR#24 2FA entity] --> [PR#25 2FA routes] --> [PR#29 Tests]",
            " Day 1-2         Day 2                  Day 3                  Day 4                  Day 5",
        ],
        (30, 100, 180),
    )

    pdf.timeline_line(
        "Yahia:",
        [
            "[PR#22 Sentry] --> [PR#23 Structlog] --> [PR#26 Health] --> [PR#27 Log flows] --> [PR#28 Staging]",
            " Day 1              Day 2                 Day 3              Day 4                Day 5",
        ],
        (40, 160, 80),
    )

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Week 2:", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.timeline_line(
        "Yousef:",
        [
            "[PR#30 ImgProcessor] --> [PR#31 Pipeline] --> [PR#32 Upload] --> [PR#35 Onboarding] --> [PR#36 Routes]",
            " Day 1                    Day 2               Day 3              Day 3-4                Day 4-5",
        ],
        (30, 100, 180),
    )

    pdf.timeline_line(
        "Yahia:",
        [
            "[PR#33 Wire notifs] --> [PR#34 Prefs] --> [PR#37 Onboard emails] --> [PR#38 Locust] --> [PR#39 Tests]",
            " Day 1-2                Day 2              Day 3                     Day 4              Day 5",
        ],
        (40, 160, 80),
    )

    pdf.ln(4)

    # ========== NO CONFLICT MAP ==========
    pdf.section_title("Conflict-Free File Ownership Map", 140, 70, 70)

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(50, 50, 50)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(95, 7, "Yousef Owns (exclusive)", border=1, align="C", fill=True)
    pdf.cell(95, 7, "Yahia Owns (exclusive)", border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)

    ownership = [
        ("alembic/ (RLS migration only)", "src/config/settings.py, logging_config.py"),
        (
            "src/infrastructure/tenancy/rls.py",
            "src/api/middleware/logging.py (rewrite)",
        ),
        (
            "src/api/middleware/security_headers.py",
            "src/api/middleware/sentry_middleware.py",
        ),
        ("src/core/entities/two_factor.py", "src/api/v1/routes/health.py"),
        (
            "src/core/entities/onboarding.py",
            "docker/docker-compose.staging.yml, nginx/",
        ),
        (
            "src/infrastructure/external_services/totp_service.py",
            "src/infrastructure/messaging/tasks/notification_tasks.py",
        ),
        (
            "src/infrastructure/external_services/image/",
            "src/infrastructure/messaging/tasks/onboarding_email_tasks.py",
        ),
        ("src/application/use_cases/auth/two_factor/", "tests/load/"),
        (
            "src/application/use_cases/stores/onboarding.py",
            ".env.staging, scripts/deploy_staging.sh",
        ),
        (
            "src/api/v1/routes/stores/onboarding.py",
            "src/core/entities/customer.py (notif prefs)",
        ),
    ]

    pdf.set_font("Helvetica", "", 7)
    for i, (y_file, ya_file) in enumerate(ownership):
        shade = i % 2 == 1
        if shade:
            pdf.set_fill_color(248, 248, 255)
        else:
            pdf.set_fill_color(255, 255, 255)
        pdf.cell(95, 5, f"  {y_file}", border=1, fill=shade)
        pdf.cell(95, 5, f"  {ya_file}", border=1, fill=shade)
        pdf.ln()

    pdf.ln(3)

    # Shared files note
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(180, 50, 50)
    pdf.cell(
        0,
        5,
        "Shared Files (merge sequentially - never in parallel):",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 7.5)
    shared = [
        "src/main.py - Yousef adds security_headers middleware (PR#21), then Yahia adds Sentry init (PR#22). PR#22 rebases on PR#21.",
        "src/application/use_cases/orders/ - Yahia modifies checkout.py & update_status.py for notifications (PR#33). Yousef does NOT touch these.",
        "Makefile - Yahia adds staging commands (PR#28), then load test targets (PR#38). Both are Yahia's so no conflict.",
        "pyproject.toml - Yahia adds sentry-sdk + structlog (PR#22,#23). Yousef adds pyotp (PR#24). Merged sequentially, different lines.",
        "tests/ - PR#29 (Yousef) and PR#39 (Both) touch different test files. No overlap.",
    ]
    for s in shared:
        pdf.cell(3, 4.5, "")
        pdf.cell(2, 4.5, "-")
        pdf.cell(0, 4.5, f" {s}", new_x="LMARGIN", new_y="NEXT")

    # Output
    output_path = r"c:\Users\PC\Desktop\NUMU\NUMU-api\NUMU_Phase2_Days31_60.pdf"
    pdf.output(output_path)
    print(f"PDF generated: {output_path}")


if __name__ == "__main__":
    build_pdf()
