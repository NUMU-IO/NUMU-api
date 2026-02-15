from fpdf import FPDF


class Phase3PDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            self.set_font("Helvetica", "B", 18)
            self.cell(
                0,
                12,
                "NUMU API - Days 61-90: Make It Launchable",
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
            f"NUMU API Phase 3 - Days 61-90  |  Page {self.page_no()}/{{nb}}",
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
    pdf = Phase3PDF("P", "mm", "A4")
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
        "  Yousef  -  Performance, Egyptian specifics (ETA, COD, Arabic PDF) & security audit",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(
        0,
        5,
        "  Yahia   -  Beta launch infrastructure, waitlist, landing page & merchant feedback",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(3)

    # ========== WEEK 1 ==========
    pdf.section_title("WEEK 1: SEO + Performance + Egyptian Specifics", 30, 100, 180)
    pdf.sub_section(
        "Current State: Gzip compression EXISTS (nginx) | No Brotli | Cache headers exist (no-store default) | "
        "ETA e-invoicing FULLY IMPLEMENTED | QR codes IMPLEMENTED | COD workflow IMPLEMENTED (webhook TODO) | "
        "Invoice PDF generation MISSING | No Arabic PDF support"
    )

    # Yousef W1
    pdf.person_header("Yousef", "Performance & Egyptian Specifics")
    pdf.task_table_header()
    yousef_w1 = [
        (
            "1",
            "Add Brotli compression to nginx config + conditional caching headers for public endpoints (products, categories) with ETag support",
            "docker/nginx/nginx.conf (modify), src/api/middleware/cache_headers.py",
        ),
        (
            "2",
            "Create response compression middleware for FastAPI (gzip fallback when nginx bypassed in dev)",
            "src/api/middleware/compression.py",
        ),
        (
            "3",
            "Add performance indexes to frequently queried columns (product.store_id, order.customer_id, order.created_at) + query optimization",
            "alembic/versions/xxx_add_performance_indexes.py",
        ),
        (
            "4",
            "Install weasyprint + Arabic font (Noto Sans Arabic) + create InvoicePDFGenerator service with Arabic RTL support",
            "pyproject.toml (modify), src/infrastructure/external_services/invoice/pdf_generator.py, docker/fonts/",
        ),
        (
            "5",
            "Create Arabic invoice PDF template: company logo, bilingual headers (AR/EN), line items table, QR code, ETA compliance footer",
            "src/infrastructure/external_services/invoice/templates/invoice_ar.html",
        ),
        (
            "6",
            "Create PDF download endpoint: GET /stores/{store_id}/invoices/{invoice_id}/pdf + store generated PDFs in R2",
            "src/api/v1/routes/stores/invoices.py (modify)",
        ),
        (
            "7",
            "Complete COD confirmation in Bosta webhook: implement TODO blocks, update order.payment_status on DELIVERED, handle RETURNED/FAILED",
            "src/api/v1/routes/webhooks/bosta.py (modify), src/application/use_cases/orders/confirm_cod_payment.py",
        ),
        (
            "8",
            "Add ETA QR code to invoice entity on submission acceptance + auto-generate QR image and store in R2",
            "src/application/use_cases/invoices/submit_to_eta.py (modify)",
        ),
    ]
    for i, (n, t, f) in enumerate(yousef_w1):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(4)

    # Yahia W1
    pdf.person_header("Yahia", "Performance Testing & 3G Optimization")
    pdf.task_table_header()
    yahia_w1 = [
        (
            "1",
            "Create API response size analyzer: log payload sizes, identify large responses, add pagination where missing",
            "scripts/analyze_response_sizes.py, src/api/v1/routes/ (modify multiple)",
        ),
        (
            "2",
            "Add sparse fieldsets support: allow clients to request specific fields via ?fields=id,name,price query param",
            "src/api/dependencies/fieldsets.py, src/api/v1/routes/stores/products.py (modify)",
        ),
        (
            "3",
            "Implement cursor-based pagination for large lists (products, orders, customers) - better for mobile/3G",
            "src/api/dependencies/pagination.py (modify), src/api/v1/schemas/common.py",
        ),
        (
            "4",
            "Add Redis caching layer for product listings and category trees (TTL 5 min) with cache invalidation on update",
            "src/infrastructure/cache/product_cache.py, src/application/use_cases/products/ (modify)",
        ),
        (
            "5",
            "Create Lighthouse CI config for API performance audits + add to CI pipeline",
            ".lighthouserc.js, .github/workflows/ci.yml (modify)",
        ),
        (
            "6",
            "Create 3G network simulation tests using throttled requests (500kbps) - verify acceptable response times",
            "tests/performance/test_3g_simulation.py",
        ),
        (
            "7",
            "Add response time tracking middleware: log slow queries (>500ms), add X-Response-Time header",
            "src/api/middleware/timing.py, src/main.py (modify)",
        ),
        (
            "8",
            "Write performance tests: cache hit/miss verification, pagination correctness, sparse fieldsets, compression",
            "tests/performance/test_caching.py, tests/performance/test_pagination.py",
        ),
    ]
    for i, (n, t, f) in enumerate(yahia_w1):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(5)

    # ========== WEEK 2 ==========
    pdf.section_title("WEEK 2: Security Audit + Beta Launch", 40, 160, 80)
    pdf.sub_section(
        "Current State: Bandit + Safety pre-commit hooks EXIST | Security headers IMPLEMENTED | "
        "JWT RS256 IMPLEMENTED | 2FA IMPLEMENTED | Rate limiting IMPLEMENTED | RLS IMPLEMENTED | "
        "Waitlist/beta signup MISSING | Landing page MISSING | No OWASP ZAP integration"
    )

    # Yousef W2
    pdf.person_header("Yousef", "Security Audit & OWASP Fixes")
    pdf.task_table_header()
    yousef_w2 = [
        (
            "1",
            "Run OWASP ZAP automated scan against staging API + export findings report",
            "scripts/run_owasp_scan.py, docs/security/owasp_scan_report.md",
        ),
        (
            "2",
            "Fix OWASP findings - typically: CORS misconfig, missing headers, information disclosure, verbose errors",
            "src/api/middleware/ (modify as needed), src/config/settings.py",
        ),
        (
            "3",
            "Run Bandit deep scan with all plugins enabled + fix any HIGH/MEDIUM severity findings",
            "scripts/run_bandit_full.py, src/ (fix findings)",
        ),
        (
            "4",
            "Run Safety dependency check + upgrade vulnerable packages + document accepted risks",
            "scripts/run_safety_check.py, pyproject.toml (modify), docs/security/dependency_audit.md",
        ),
        (
            "5",
            "Create penetration test checklist: auth bypass, IDOR, SQL injection, XSS, CSRF, rate limit bypass + manual testing",
            "docs/security/pentest_checklist.md, tests/security/test_pentest_scenarios.py",
        ),
        (
            "6",
            "Add input sanitization for all user-provided strings: strip HTML tags, validate lengths, prevent injection",
            "src/api/dependencies/sanitization.py, src/api/v1/schemas/ (modify)",
        ),
        (
            "7",
            "Create security audit summary document with all findings, fixes applied, and residual risks",
            "docs/security/SECURITY_AUDIT_REPORT.md",
        ),
        (
            "8",
            "Add automated security scan to CI: OWASP ZAP baseline scan on every PR to staging",
            ".github/workflows/security.yml",
        ),
    ]
    for i, (n, t, f) in enumerate(yousef_w2):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(4)

    # Yahia W2
    pdf.person_header("Yahia", "Beta Launch & Merchant Onboarding")
    pdf.task_table_header()
    yahia_w2 = [
        (
            "1",
            "Create Waitlist entity + WaitlistRepository: email, signup_date, referral_code, status (pending/invited/converted), priority_score",
            "src/core/entities/waitlist.py, src/infrastructure/repositories/waitlist_repository.py, alembic/versions/xxx_create_waitlist.py",
        ),
        (
            "2",
            "Create public waitlist endpoint: POST /public/waitlist (no auth) with email validation, duplicate check, welcome email trigger",
            "src/api/v1/routes/public/waitlist.py, src/api/v1/schemas/public/waitlist.py",
        ),
        (
            "3",
            "Create admin waitlist management: GET /admin/waitlist (list), POST /admin/waitlist/invite (send beta invite), PATCH /admin/waitlist/{id}/priority",
            "src/api/v1/routes/admin/waitlist.py",
        ),
        (
            "4",
            "Create beta invite email template + invite code generation + code validation in store registration flow",
            "src/infrastructure/external_services/resend/templates/beta_invite.html, src/application/use_cases/stores/create_store.py (modify)",
        ),
        (
            "5",
            "Create landing page API endpoints: GET /public/stats (merchant count, order count), GET /public/features (feature list for marketing)",
            "src/api/v1/routes/public/landing.py",
        ),
        (
            "6",
            "Create beta merchant feedback system: POST /stores/{store_id}/feedback, GET /admin/feedback (aggregate complaints)",
            "src/api/v1/routes/stores/feedback.py, src/core/entities/feedback.py, alembic/versions/xxx_create_feedback.py",
        ),
        (
            "7",
            "Set up 5-10 beta merchant test accounts with seeded data (products, orders, customers) for QA testing",
            "scripts/seed_beta_merchants.py, tests/fixtures/beta_merchants.json",
        ),
        (
            "8",
            "Create beta launch runbook: pre-launch checklist, rollback plan, monitoring alerts, support escalation",
            "docs/operations/BETA_LAUNCH_RUNBOOK.md",
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

    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(
        0,
        5,
        "PR numbering continues from Phase 2 (#40+). File scope shows which directories each PR touches.",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    # Week 1 PRs
    pdf.section_title("Week 1 PRs: Performance + Egyptian Specifics", 30, 100, 180)
    pdf.pr_table_header()

    w1_prs = [
        (
            "40",
            "perf: Brotli + conditional cache headers + ETag support",
            "None (independent)",
            "Yousef",
            "nginx/, middleware/",
        ),
        (
            "41",
            "perf: response compression middleware (FastAPI)",
            "None (independent)",
            "Yousef",
            "middleware/",
        ),
        (
            "42",
            "perf: database performance indexes migration",
            "None (independent)",
            "Yousef",
            "alembic/",
        ),
        (
            "43",
            "perf: sparse fieldsets support (?fields=)",
            "None (independent)",
            "Yahia",
            "dependencies/, routes/",
        ),
        (
            "44",
            "perf: cursor-based pagination for large lists",
            "None (independent)",
            "Yahia",
            "dependencies/, schemas/",
        ),
        (
            "45",
            "perf: Redis caching for products + categories",
            "None (independent)",
            "Yahia",
            "cache/, use_cases/",
        ),
        (
            "46",
            "feat: Arabic invoice PDF generator (weasyprint + Noto font)",
            "None (independent)",
            "Yousef",
            "ext_svc/invoice/",
        ),
        (
            "47",
            "feat: PDF download endpoint + R2 storage",
            "PR #46",
            "Yousef",
            "routes/invoices.py",
        ),
        (
            "48",
            "feat: complete COD webhook + confirm_cod_payment use case",
            "None (independent)",
            "Yousef",
            "webhooks/, use_cases/",
        ),
        (
            "49",
            "feat: ETA QR code auto-generation on submission",
            "PR #46",
            "Yousef",
            "use_cases/invoices/",
        ),
        (
            "50",
            "perf: response time tracking middleware + slow query logging",
            "None (independent)",
            "Yahia",
            "middleware/timing",
        ),
        (
            "51",
            "test: 3G simulation + caching + pagination + performance tests",
            "PR #44, #45, #50",
            "Yahia",
            "tests/performance/",
        ),
    ]
    for i, row in enumerate(w1_prs):
        pdf.pr_row(*row, shade=(i % 2 == 1))

    pdf.ln(4)

    # Week 2 PRs
    pdf.section_title("Week 2 PRs: Security Audit + Beta Launch", 40, 160, 80)
    pdf.pr_table_header()

    w2_prs = [
        (
            "52",
            "sec: OWASP ZAP scan script + findings report",
            "None (independent)",
            "Yousef",
            "scripts/, docs/security/",
        ),
        (
            "53",
            "sec: fix OWASP findings (headers, CORS, errors)",
            "PR #52",
            "Yousef",
            "middleware/, config/",
        ),
        (
            "54",
            "sec: Bandit deep scan + fix findings",
            "None (independent)",
            "Yousef",
            "src/ (various)",
        ),
        (
            "55",
            "sec: Safety dependency audit + upgrades",
            "None (independent)",
            "Yousef",
            "pyproject.toml, docs/",
        ),
        (
            "56",
            "sec: input sanitization layer + schema updates",
            "None (independent)",
            "Yousef",
            "dependencies/, schemas/",
        ),
        (
            "57",
            "feat: Waitlist entity + repository + migration",
            "None (independent)",
            "Yahia",
            "core/, repos/, alembic/",
        ),
        (
            "58",
            "feat: public waitlist endpoint + admin management",
            "PR #57",
            "Yahia",
            "routes/public/, admin/",
        ),
        (
            "59",
            "feat: beta invite email + code validation in store creation",
            "PR #57, #58",
            "Yahia",
            "resend/, use_cases/",
        ),
        (
            "60",
            "feat: landing page API endpoints (stats, features)",
            "None (independent)",
            "Yahia",
            "routes/public/",
        ),
        (
            "61",
            "feat: beta merchant feedback system + admin view",
            "None (independent)",
            "Yahia",
            "routes/, entities/",
        ),
        (
            "62",
            "ci: automated OWASP ZAP scan in CI pipeline",
            "PR #52",
            "Yousef",
            ".github/workflows/",
        ),
        (
            "63",
            "docs: security audit report + beta launch runbook",
            "PR #53, #54, #55",
            "Both",
            "docs/",
        ),
        (
            "64",
            "data: seed beta merchants + test fixtures",
            "PR #59",
            "Yahia",
            "scripts/, fixtures/",
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
            "[PR#40 Brotli] --> [PR#41 Compress] --> [PR#42 Indexes] --> [PR#46 PDF Gen] --> [PR#47 PDF Route] --> [PR#48 COD] --> [PR#49 QR]",
            " Day 1              Day 1              Day 2               Day 2-3             Day 3               Day 4           Day 5",
        ],
        (30, 100, 180),
    )

    pdf.timeline_line(
        "Yahia:",
        [
            "[PR#43 Fieldsets] --> [PR#44 Pagination] --> [PR#45 Cache] --> [PR#50 Timing] --> [PR#51 Tests]",
            " Day 1                 Day 2                  Day 2-3          Day 4              Day 5",
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
            "[PR#52 OWASP] --> [PR#53 Fixes] --> [PR#54 Bandit] --> [PR#55 Safety] --> [PR#56 Sanitize] --> [PR#62 CI] --> [PR#63 Docs]",
            " Day 1             Day 2            Day 2             Day 3             Day 3-4            Day 4          Day 5",
        ],
        (30, 100, 180),
    )

    pdf.timeline_line(
        "Yahia:",
        [
            "[PR#57 Waitlist] --> [PR#58 Endpoints] --> [PR#59 Invite] --> [PR#60 Landing] --> [PR#61 Feedback] --> [PR#64 Seed]",
            " Day 1               Day 2                 Day 3             Day 3              Day 4               Day 5",
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
        ("docker/nginx/nginx.conf", "src/api/dependencies/fieldsets.py"),
        ("src/api/middleware/compression.py", "src/api/dependencies/pagination.py"),
        (
            "src/api/middleware/cache_headers.py",
            "src/infrastructure/cache/product_cache.py",
        ),
        ("alembic/ (performance indexes)", "alembic/ (waitlist, feedback tables)"),
        (
            "src/infrastructure/external_services/invoice/",
            "src/core/entities/waitlist.py",
        ),
        ("src/api/v1/routes/webhooks/bosta.py (COD)", "src/core/entities/feedback.py"),
        (
            "src/application/use_cases/orders/confirm_cod_payment.py",
            "src/api/v1/routes/public/waitlist.py",
        ),
        ("src/application/use_cases/invoices/", "src/api/v1/routes/public/landing.py"),
        (
            "scripts/run_owasp_scan.py, run_bandit_full.py",
            "src/api/v1/routes/admin/waitlist.py",
        ),
        (
            "src/api/dependencies/sanitization.py",
            "src/api/v1/routes/stores/feedback.py",
        ),
        ("docs/security/", "scripts/seed_beta_merchants.py"),
        (".github/workflows/security.yml", "docs/operations/BETA_LAUNCH_RUNBOOK.md"),
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
    pdf.cell(0, 5, "Shared Files (merge sequentially):", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 7.5)
    shared = [
        "pyproject.toml - Yousef adds weasyprint (PR#46), then Safety updates (PR#55). Sequential, no conflict.",
        "src/main.py - Yousef adds compression/timing middleware. Yahia doesn't touch main.py this phase.",
        "alembic/ - Yousef: indexes (PR#42). Yahia: waitlist+feedback (PR#57, #61). Different tables, no conflict.",
        "src/api/v1/routes/ - Yousef: invoices.py, webhooks/. Yahia: public/, admin/, stores/feedback.py. No overlap.",
        "docs/ - Yousef: security/. Yahia: operations/. Different subdirs.",
        ".github/workflows/ - Yousef: security.yml (PR#62). Yahia: doesn't touch workflows this phase.",
    ]
    for s in shared:
        pdf.cell(3, 4.5, "")
        pdf.cell(2, 4.5, "-")
        pdf.cell(0, 4.5, f" {s}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)

    # Beta launch checklist
    pdf.section_title("Beta Launch Checklist (End of Day 90)", 80, 40, 120)
    pdf.set_font("Helvetica", "", 8)
    checklist = [
        "[ ] All OWASP ZAP findings resolved or documented as accepted risk",
        "[ ] Bandit + Safety scans pass with no HIGH severity issues",
        "[ ] Security audit report completed and reviewed",
        "[ ] 5-10 beta merchants onboarded with seeded data",
        "[ ] Waitlist collecting signups, invite flow tested",
        "[ ] Arabic invoice PDF generation working with QR codes",
        "[ ] COD payment confirmation via Bosta webhook working",
        "[ ] Performance: <500ms p95 response time on 3G simulation",
        "[ ] Monitoring alerts configured for errors, slow queries",
        "[ ] Beta launch runbook reviewed by team",
    ]
    for item in checklist:
        pdf.cell(5, 5, "")
        pdf.cell(0, 5, item, new_x="LMARGIN", new_y="NEXT")

    # Output
    output_path = r"c:\Users\PC\Desktop\NUMU\NUMU-api\NUMU_Phase3_Days61_90.pdf"
    pdf.output(output_path)
    print(f"PDF generated: {output_path}")


if __name__ == "__main__":
    build_pdf()
