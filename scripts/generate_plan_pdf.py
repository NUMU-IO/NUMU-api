from fpdf import FPDF


class PlanPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 18)
        self.cell(
            0,
            12,
            "NUMU API - 2-Week Execution Plan",
            new_x="LMARGIN",
            new_y="NEXT",
            align="C",
        )
        self.set_font("Helvetica", "", 10)
        self.set_text_color(100, 100, 100)
        self.cell(
            0, 6, "Days 1-14: Make It Work", new_x="LMARGIN", new_y="NEXT", align="C"
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
            f"NUMU API Execution Plan  |  Page {self.page_no()}/{{nb}}",
            align="C",
        )

    def section_title(self, title, r=30, g=100, b=180):
        self.set_font("Helvetica", "B", 14)
        self.set_fill_color(r, g, b)
        self.set_text_color(255, 255, 255)
        self.cell(0, 10, f"  {title}", new_x="LMARGIN", new_y="NEXT", fill=True)
        self.set_text_color(0, 0, 0)
        self.ln(3)

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
        self.set_fill_color(60, 60, 60)
        self.set_text_color(255, 255, 255)
        self.cell(8, 7, "#", border=1, align="C", fill=True)
        self.cell(80, 7, "Task", border=1, align="C", fill=True)
        self.cell(102, 7, "Files to Create / Modify", border=1, align="C", fill=True)
        self.ln()
        self.set_text_color(0, 0, 0)

    def task_row(self, num, task, files, shade=False):
        self.set_font("Helvetica", "", 7.5)
        if shade:
            self.set_fill_color(248, 248, 255)
        else:
            self.set_fill_color(255, 255, 255)

        task_lines = self._wrap_text(task, 78)
        file_lines = self._wrap_text(files, 100)
        max_lines = max(len(task_lines), len(file_lines))
        row_h = max(max_lines * 4.5, 6)

        # Check page break
        if self.get_y() + row_h > 270:
            self.add_page()
            self.task_table_header()

        y_start = self.get_y()
        self.get_x()

        # Number cell
        self.cell(8, row_h, str(num), border=1, align="C", fill=shade)

        # Task cell
        x = self.get_x()
        self.multi_cell(80, 4.5, "\n".join(task_lines), border=1, fill=shade)
        y_after_task = self.get_y()

        # Files cell
        self.set_xy(x + 80, y_start)
        self.multi_cell(102, 4.5, "\n".join(file_lines), border=1, fill=shade)
        y_after_files = self.get_y()

        final_y = max(y_after_task, y_after_files)
        self.set_y(final_y)

    def _wrap_text(self, text, width_mm):
        self.set_font("Helvetica", "", 7.5)
        words = text.split(" ")
        lines = []
        current = ""
        for w in words:
            test = f"{current} {w}".strip() if current else w
            if self.get_string_width(test) < width_mm - 2:
                current = test
            else:
                if current:
                    lines.append(current)
                current = w
        if current:
            lines.append(current)
        return lines if lines else [""]

    def status_box(self, items):
        self.set_font("Helvetica", "", 8)
        for label, status, color in items:
            r, g, b = color
            self.set_fill_color(r, g, b)
            self.cell(4, 5, "", fill=True)
            self.cell(1, 5, "")
            self.set_font("Helvetica", "", 7.5)
            self.cell(55, 5, f"{label}: {status}")
            if self.get_x() > 160:
                self.ln()
        self.ln(4)


def build_pdf():
    pdf = PlanPDF("P", "mm", "A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # --- Team Info ---
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Team:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(
        0,
        5,
        "  Yousef  -  Core business logic, payment integration & security",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(
        0,
        5,
        "  Yahia   -  Models, API endpoints, infrastructure & CI/CD",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)

    # ============================
    # WEEK 1
    # ============================
    pdf.section_title("WEEK 1: Checkout Flow + Dashboard CRUD", 30, 100, 180)

    # Current state
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(
        0,
        4,
        "Current State: Cart NOT implemented | Customer checkout NOT implemented | Paymob service IMPLEMENTED (webhook incomplete) | Product CRUD IMPLEMENTED (no image upload) | Order routes IMPLEMENTED | Dashboard & Analytics IMPLEMENTED",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    # --- Yousef Week 1 ---
    pdf.person_header("Yousef", "Core Logic & Payments")
    pdf.task_table_header()

    yousef_w1 = [
        (
            "1",
            "Create Cart entity with add/remove/update/clear methods + CartItem value object",
            "src/core/entities/cart.py, src/core/value_objects/cart_item.py",
        ),
        (
            "2",
            "Create ICartRepository interface + RedisCartRepository (session-based, TTL 7 days)",
            "src/core/interfaces/repositories/cart_repository.py, src/infrastructure/repositories/cart_repository.py",
        ),
        (
            "3",
            "Create cart use cases: AddToCart, RemoveFromCart, UpdateCartItem, GetCart, ClearCart + DTOs",
            "src/application/use_cases/cart/, src/application/dto/cart.py",
        ),
        (
            "4",
            "Implement CheckoutUseCase - validate cart, check stock, create order, initiate Paymob payment (return iframe URL)",
            "src/application/use_cases/orders/checkout.py",
        ),
        (
            "5",
            "Create UploadProductImageUseCase - validate image type/size, upload to R2, return URL",
            "src/application/use_cases/products/upload_image.py",
        ),
        (
            "6",
            "Create image upload endpoint (multipart) + image delete endpoint",
            "src/api/v1/routes/stores/products.py (modify)",
        ),
        (
            "7",
            "Create OrderTimelineUseCase + add order status transition validation (prevent invalid transitions)",
            "src/application/use_cases/orders/get_order_timeline.py, src/core/entities/order.py",
        ),
        (
            "8",
            "Add product validation in CreateProductUseCase (required fields, price > 0, category exists)",
            "src/application/use_cases/products/ (modify existing)",
        ),
    ]

    for i, (num, task, files) in enumerate(yousef_w1):
        pdf.task_row(num, task, files, shade=(i % 2 == 1))

    pdf.ln(4)

    # --- Yahia Week 1 ---
    pdf.person_header("Yahia", "Routes, Schemas & Tests")
    pdf.task_table_header()

    yahia_w1 = [
        (
            "1",
            "Create cart + checkout API schemas (request/response)",
            "src/api/v1/schemas/storefront/cart.py, src/api/v1/schemas/storefront/checkout.py",
        ),
        (
            "2",
            "Create storefront cart routes: GET /cart, POST /cart/items, PATCH /cart/items/{id}, DELETE /cart/items/{id}, DELETE /cart",
            "src/api/v1/routes/storefront/cart.py",
        ),
        (
            "3",
            "Create storefront checkout route: POST /storefront/store/{store_id}/checkout",
            "src/api/v1/routes/storefront/checkout.py",
        ),
        (
            "4",
            "Complete Paymob webhook handler - implement 4 TODO blocks (mark_paid, mark_failed, mark_refunded, update order)",
            "src/api/v1/routes/webhooks/paymob.py",
        ),
        (
            "5",
            "Create customer order routes: GET /storefront/me/orders, GET /storefront/me/orders/{id} + register all new routes",
            "src/api/v1/routes/storefront/customer.py, src/main.py",
        ),
        (
            "6",
            "Create order detail enriched schema + GET /orders/{id}/timeline endpoint + bulk status update endpoint",
            "src/api/v1/routes/stores/orders.py (modify), src/api/v1/schemas/tenant/orders.py",
        ),
        (
            "7",
            "Add order filtering (date range, payment_status, fulfillment_status) + product search improvements (SKU, price range, sort)",
            "src/api/v1/routes/stores/orders.py, src/api/v1/routes/stores/products.py",
        ),
        (
            "8",
            "Write integration tests for checkout flow + unit tests for product CRUD and order status transitions",
            "tests/integration/test_checkout_flow.py, tests/unit/test_products.py, tests/unit/test_orders.py",
        ),
    ]

    for i, (num, task, files) in enumerate(yahia_w1):
        pdf.task_row(num, task, files, shade=(i % 2 == 1))

    pdf.ln(6)

    # ============================
    # WEEK 2
    # ============================
    pdf.section_title("WEEK 2: Currency Fix + Coupons + Infrastructure", 40, 160, 80)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(
        0,
        4,
        "Current State: Currency defaults to USD (should be EGP) | Coupon system NOT implemented | JWT uses HS256 (need RS256) | CI/CD partial (Slack only) | No DB backups | No CSV import/export",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    # --- Yousef Week 2 ---
    pdf.person_header("Yousef", "Coupons, Currency & Security")
    pdf.task_table_header()

    yousef_w2 = [
        (
            "1",
            "Fix currency defaults: change product price_currency from USD to EGP + add currency validation (match store default) + Alembic migration",
            "src/api/v1/schemas/tenant/products.py, src/infrastructure/database/models/tenant/product.py, alembic/versions/",
        ),
        (
            "2",
            "Create Coupon entity (code, type: percentage/fixed/free_shipping, value, min_order, max_discount, usage_limit, validity dates)",
            "src/core/entities/coupon.py",
        ),
        (
            "3",
            "Create ICouponRepository interface + CouponRepository SQLAlchemy implementation",
            "src/core/interfaces/repositories/coupon_repository.py, src/infrastructure/repositories/coupon_repository.py",
        ),
        (
            "4",
            "Create coupon use cases: CreateCoupon, ValidateCoupon, ApplyCoupon, ListCoupons, UpdateCoupon, DeleteCoupon",
            "src/application/use_cases/coupons/",
        ),
        (
            "5",
            "Integrate coupon validation into CheckoutUseCase - apply discount if valid coupon code provided",
            "src/application/use_cases/orders/checkout.py (modify)",
        ),
        (
            "6",
            "Migrate JWT from HS256 to RS256: generate RSA key pair, update settings + token service + key generation script",
            "src/config/settings.py, src/infrastructure/external_services/token_service.py, scripts/generate_jwt_keys.py",
        ),
        (
            "7",
            "Update .env.example with RS256 key paths + update all JWT tests for RS256",
            ".env.example, tests/security/test_jwt_validation.py",
        ),
        (
            "8",
            "Create DB backup script (pg_dump + upload to R2) + restore script + Celery periodic backup task",
            "scripts/backup_db.py, scripts/restore_db.py, src/infrastructure/messaging/tasks/backup_task.py",
        ),
    ]

    for i, (num, task, files) in enumerate(yousef_w2):
        pdf.task_row(num, task, files, shade=(i % 2 == 1))

    pdf.ln(4)

    # --- Yahia Week 2 ---
    pdf.person_header("Yahia", "Coupon API, CI/CD & CSV")
    pdf.task_table_header()

    yahia_w2 = [
        (
            "1",
            "Create CouponModel (tenant-scoped) + Alembic migration for coupon table",
            "src/infrastructure/database/models/tenant/coupon.py, alembic/versions/",
        ),
        (
            "2",
            "Create coupon API schemas (CreateCouponRequest, CouponResponse, ApplyCouponRequest/Response)",
            "src/api/v1/schemas/tenant/coupons.py",
        ),
        (
            "3",
            "Create store coupon management routes: POST, GET, GET/{id}, PATCH/{id}, DELETE/{id} at /stores/{store_id}/coupons/",
            "src/api/v1/routes/stores/coupons.py",
        ),
        (
            "4",
            "Create storefront coupon apply route + add coupon_code/coupon_id fields to Order model + migration",
            "src/api/v1/routes/storefront/cart.py, src/infrastructure/database/models/tenant/order.py, alembic/versions/",
        ),
        (
            "5",
            "Create GitHub Actions CI pipeline (ruff + mypy + pytest + Docker build) + CD pipeline (deploy on push/tag)",
            ".github/workflows/ci.yml, .github/workflows/cd.yml",
        ),
        (
            "6",
            "Create CSV product import use case + import endpoint (multipart upload) + export endpoint + template download",
            "src/application/use_cases/products/import_products.py, src/api/v1/routes/stores/products.py",
        ),
        (
            "7",
            "Write tests: coupon CRUD + validation edge cases (expired, usage limit, min order) + CSV import tests",
            "tests/unit/test_coupons.py, tests/integration/test_csv_import.py",
        ),
    ]

    for i, (num, task, files) in enumerate(yahia_w2):
        pdf.task_row(num, task, files, shade=(i % 2 == 1))

    pdf.ln(6)

    # ============================
    # VERIFICATION
    # ============================
    pdf.section_title("Verification Plan", 100, 100, 100)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Week 1 - End of Week Checks:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    checks_w1 = [
        "Add items to cart -> view cart -> checkout with Paymob -> webhook updates order to PAID",
        "Upload product image -> create product -> verify image URL in response",
        "Create order -> update status -> view timeline -> test invalid transition is blocked",
        "Run: pytest tests/integration/test_checkout_flow.py tests/unit/test_products.py tests/unit/test_orders.py",
    ]
    for c in checks_w1:
        pdf.cell(5, 5, "")
        pdf.cell(3, 5, "-")
        pdf.cell(0, 5, c, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Week 2 - End of Week Checks:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    checks_w2 = [
        "Verify new products default to EGP -> create coupon -> apply to cart -> checkout with discount applied",
        "Login with RS256 JWT -> access protected route -> verify token validation works",
        "Run CI pipeline on test branch -> verify lint + type check + tests + Docker build pass",
        "Import products via CSV -> verify in DB -> export CSV -> compare with original",
        "Run backup script -> verify .sql file uploaded to R2 -> test restore script",
        "Run: pytest tests/unit/test_coupons.py tests/security/test_jwt_validation.py tests/integration/test_csv_import.py",
    ]
    for c in checks_w2:
        pdf.cell(5, 5, "")
        pdf.cell(3, 5, "-")
        pdf.cell(0, 5, c, new_x="LMARGIN", new_y="NEXT")

    # Output
    output_path = r"c:\Users\PC\Desktop\NUMU\NUMU-api\NUMU_2_Week_Plan.pdf"
    pdf.output(output_path)
    print(f"PDF generated: {output_path}")


if __name__ == "__main__":
    build_pdf()
