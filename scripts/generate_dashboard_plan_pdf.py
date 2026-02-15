from fpdf import FPDF


class DashboardPDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            self.set_font("Helvetica", "B", 18)
            self.cell(0, 12, "NUMU Dashboard - Frontend 2-Week Sprint", new_x="LMARGIN", new_y="NEXT", align="C")
            self.set_font("Helvetica", "", 10)
            self.set_text_color(100, 100, 100)
            self.cell(0, 6, "Phase 1: Fix All Dead-End Buttons  |  Yousef & Yahia", new_x="LMARGIN", new_y="NEXT", align="C")
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
        self.cell(0, 10, f"NUMU Dashboard Sprint  |  Page {self.page_no()}/{{nb}}", align="C")

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
        self.cell(0, 8, f"  {name} - {role}", new_x="LMARGIN", new_y="NEXT", fill=True, border=1)
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
        max_lines = max(len(name_lines), len(dep_lines), 1)
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
        color = (30, 100, 180) if owner == "Yousef" else (40, 160, 80) if owner == "Yahia" else (120, 120, 120)
        self.set_text_color(*color)
        self.set_font("Helvetica", "B", 7.5)
        self.cell(20, row_h, owner, border=1, align="C", fill=shade)
        self.set_text_color(0, 0, 0)
        self.set_font("Helvetica", "", 6.5)
        self.cell(28, row_h, scope, border=1, align="C", fill=shade)
        self.set_y(max(y0 + row_h, y1, y2))

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
    pdf = DashboardPDF("P", "mm", "A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Tech stack
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Tech Stack: React 18 + MUI 5 + Redux Toolkit / RTK Query + React Router v5 + Vite", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    # Team
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Team:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "  Yousef  -  Shared UI systems (Snackbar, ConfirmDialog, Export) + Product CRUD forms", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "  Yahia   -  Order detail + Customer detail + Routes + Dashboard cleanup", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ========== WEEK 1 ==========
    pdf.section_title("WEEK 1: Product CRUD Form + Global Feedback System", 30, 100, 180)
    pdf.sub_section(
        "Current State: Products list EXISTS (table, search, pagination) | No create/edit form | No global snackbar | "
        "5x window.alert() calls | Delete via menu (no confirm dialog) | Export buttons are stubs | "
        "RTK Query hooks exist: useCreateProductMutation, useUpdateProductMutation, useDeleteProductMutation"
    )

    # Yousef W1
    pdf.person_header("Yousef", "Shared UI + Product Forms")
    pdf.task_table_header()
    yousef_w1 = [
        ("1", "Create SnackbarProvider context: queue-based, auto-dismiss (5s), position bottom-left, success/error/warning/info variants, useSnackbar() hook", "src/context/SnackbarContext.js, src/components/GlobalSnackbar/index.js"),
        ("2", "Wrap App with SnackbarProvider + replace all 5 window.alert() calls with useSnackbar() across sign-up, products, profile pages", "src/App.js (modify), src/layouts/authentication/sign-up/ (modify), src/layouts/products/ (modify), src/layouts/profile/ (modify)"),
        ("3", "Create reusable ConfirmDialog component: MUI Dialog with title, message, cancel/confirm buttons, loading state, destructive variant (red button)", "src/components/ConfirmDialog/index.js"),
        ("4", "Create reusable CSV export utility: exportToCSV(data, columns, filename) + integrate into Products page export button", "src/utils/exportCSV.js, src/layouts/products/index.js (modify)"),
        ("5", "Create ProductForm component: full-page form with sections (Basic Info, Pricing, Inventory, Media, Organization). Fields: name, description, SKU, price, compare-at-price, cost, quantity, category select, tags, status (draft/active/archived)", "src/layouts/products/components/ProductForm/index.js"),
        ("6", "Create ProductCreate page: uses ProductForm, wires to useCreateProductMutation, snackbar on success/error, redirect to /products on save", "src/layouts/products/create/index.js"),
        ("7", "Create ProductEdit page: same ProductForm pre-filled via useGetProductQuery, wires to useUpdateProductMutation, snackbar feedback", "src/layouts/products/edit/index.js"),
        ("8", "Create ProductDuplicate: same form pre-filled but no ID (creates new), triggered from products list menu action 'Duplicate'", "src/layouts/products/duplicate/index.js, src/layouts/products/index.js (modify menu)"),
    ]
    for i, (n, t, f) in enumerate(yousef_w1):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(4)

    # Yahia W1
    pdf.person_header("Yahia", "Order Detail + Customer Detail")
    pdf.task_table_header()
    yahia_w1 = [
        ("1", "Create OrderDetail page/side-panel: header (order #, date, status badge), customer info with link, action buttons (update status, cancel, refund)", "src/layouts/orders/detail/index.js"),
        ("2", "OrderDetail: line items table (product image, name, SKU, qty, unit price, line total) + pricing summary (subtotal, shipping, 14% VAT, discount, total EGP)", "src/layouts/orders/detail/components/LineItems.js, src/layouts/orders/detail/components/PricingSummary.js"),
        ("3", "OrderDetail: shipping address with governorate, payment info (method COD/Paymob/Card, status, transaction ID), internal notes section", "src/layouts/orders/detail/components/ShippingInfo.js, src/layouts/orders/detail/components/PaymentInfo.js, src/layouts/orders/detail/components/OrderNotes.js"),
        ("4", "OrderDetail: status update dropdown (enforce valid transitions: PENDING->CONFIRMED->PROCESSING->SHIPPED->DELIVERED), wire to useUpdateOrderStatusMutation", "src/layouts/orders/detail/components/StatusUpdater.js"),
        ("5", "Create CustomerDetail page/side-panel: customer info (name, email, phone, addresses), order history list filtered by customer_id", "src/layouts/customers/detail/index.js"),
        ("6", "CustomerDetail: stats cards (total spent, total orders, average order value, member since), wire to useGetCustomerQuery", "src/layouts/customers/detail/components/CustomerStats.js"),
        ("7", "Create CustomerCreate form: fields (first name, last name, email, phone, address), wire to useCreateCustomerMutation, snackbar on success", "src/layouts/customers/create/index.js"),
        ("8", "Wire order list 'View' button to OrderDetail + wire customer list click to CustomerDetail + add navigation links between them", "src/layouts/orders/index.js (modify), src/layouts/customers/index.js (modify)"),
    ]
    for i, (n, t, f) in enumerate(yahia_w1):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(5)

    # ========== WEEK 2 ==========
    pdf.section_title("WEEK 2: Routes + Export + Dashboard Cleanup", 40, 160, 80)
    pdf.sub_section(
        "Current State: Sign-up component EXISTS but not in routes.js | Billing component EXISTS but not in routes.js | "
        "Export buttons are stubs on Orders + Customers | Dashboard has 2 hardcoded widgets (SatisfactionRate 95%, ReferralTracking) | "
        "barChartDataDashboard has hardcoded sales data (not used, real data used instead)"
    )

    # Yousef W2
    pdf.person_header("Yousef", "Image Upload + Form Validation + Polish")
    pdf.task_table_header()
    yousef_w2 = [
        ("1", "Add image upload section to ProductForm: drag-and-drop zone, preview thumbnails, reorder images, delete image, max 5 images, max 5MB each", "src/layouts/products/components/ProductForm/ImageUpload.js, src/layouts/products/components/ProductForm/index.js (modify)"),
        ("2", "Add form validation to ProductForm: required fields (name, price, status), price > 0, SKU format, MUI error states + helper text", "src/layouts/products/components/ProductForm/validation.js, src/layouts/products/components/ProductForm/index.js (modify)"),
        ("3", "Add ConfirmDialog to product delete + order cancel: replace browser confirm() with ConfirmDialog in products list and order detail", "src/layouts/products/index.js (modify), src/layouts/orders/detail/index.js (modify)"),
        ("4", "Integrate CSV export into Orders page: exportToCSV with columns (order#, customer, date, total, status, payment_method)", "src/layouts/orders/index.js (modify)"),
        ("5", "Integrate CSV export into Customers page: exportToCSV with columns (name, email, phone, orders_count, total_spent, status)", "src/layouts/customers/index.js (modify)"),
        ("6", "Add product routes to routes.js: /products/new -> ProductCreate, /products/:id/edit -> ProductEdit, /products/:id/duplicate -> ProductDuplicate", "src/routes.js (modify)"),
        ("7", "Add loading skeletons to ProductForm, OrderDetail, CustomerDetail for better UX during data fetch", "src/layouts/products/components/ProductForm/Skeleton.js, src/layouts/orders/detail/Skeleton.js, src/layouts/customers/detail/Skeleton.js"),
        ("8", "Write integration smoke tests: product CRUD flow (create -> edit -> duplicate -> delete), snackbar appears, confirm dialog works", "src/__tests__/products.test.js, src/__tests__/snackbar.test.js"),
    ]
    for i, (n, t, f) in enumerate(yousef_w2):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(4)

    # Yahia W2
    pdf.person_header("Yahia", "Routes + Dashboard Cleanup")
    pdf.task_table_header()
    yahia_w2 = [
        ("1", "Fix Sign-Up route: add /authentication/sign-up to routes.js (component exists at src/layouts/authentication/sign-up/)", "src/routes.js (modify)"),
        ("2", "Fix Billing route: add /billing to routes.js + add to sidenav (component exists in src/layouts/billing/)", "src/routes.js (modify), src/examples/Sidenav/index.js (modify)"),
        ("3", "Add order/customer detail routes: /orders/:id -> OrderDetail, /customers/:id -> CustomerDetail, /customers/new -> CustomerCreate", "src/routes.js (modify)"),
        ("4", "Replace SatisfactionRate hardcoded 95% with real data: compute from order ratings or customer feedback API (or hide if no data)", "src/layouts/dashboard/components/SatisfactionRate/index.js (modify)"),
        ("5", "Replace ReferralTracking hardcoded data with real data: use actual merchant invite count from API or replace with useful widget (e.g. conversion rate)", "src/layouts/dashboard/components/ReferralTracking/index.js (modify)"),
        ("6", "Clean up barChartDataDashboard mock: remove hardcoded file, audit all dashboard imports to confirm real API data is used everywhere", "src/layouts/dashboard/data/barChartData.js (delete), src/layouts/dashboard/index.js (audit)"),
        ("7", "Add empty states for all list pages: Products (no products yet), Orders (no orders), Customers (no customers) with call-to-action buttons", "src/layouts/products/components/EmptyState.js, src/layouts/orders/components/EmptyState.js, src/layouts/customers/components/EmptyState.js"),
        ("8", "Write integration tests: route navigation (sign-up, billing, order detail), dashboard renders without mock data, empty states render", "src/__tests__/routes.test.js, src/__tests__/dashboard.test.js"),
    ]
    for i, (n, t, f) in enumerate(yahia_w2):
        pdf.task_row(n, t, f, shade=(i % 2 == 1))
    pdf.ln(5)

    # ========== PR ORDER ==========
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "PR Dependency Order (No Conflicts)", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(2)

    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, "Frontend PRs use FE- prefix. File scope shows which layout/component each PR touches - no overlap between Yousef & Yahia.", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    # Week 1 PRs
    pdf.section_title("Week 1 PRs: Product CRUD + Global Feedback", 30, 100, 180)
    pdf.pr_table_header()

    w1_prs = [
        ("FE-1", "feat: SnackbarProvider context + GlobalSnackbar component", "None (foundation)", "Yousef", "context/, components/"),
        ("FE-2", "refactor: replace all window.alert() with useSnackbar()", "PR #FE-1", "Yousef", "sign-up, products, profile"),
        ("FE-3", "feat: reusable ConfirmDialog component", "None (independent)", "Yousef", "components/"),
        ("FE-4", "feat: CSV export utility (exportToCSV)", "None (independent)", "Yousef", "utils/"),
        ("FE-5", "feat: ProductForm component (full form with all sections)", "PR #FE-1", "Yousef", "layouts/products/comp/"),
        ("FE-6", "feat: ProductCreate + ProductEdit + ProductDuplicate pages", "PR #FE-5", "Yousef", "layouts/products/"),
        ("FE-7", "feat: OrderDetail page (header, line items, pricing, shipping, payment, notes, status updater)", "PR #FE-1", "Yahia", "layouts/orders/detail/"),
        ("FE-8", "feat: CustomerDetail page (info, stats, order history)", "PR #FE-1", "Yahia", "layouts/customers/detail/"),
        ("FE-9", "feat: CustomerCreate form", "PR #FE-1", "Yahia", "layouts/customers/create/"),
        ("FE-10", "feat: wire Order 'View' + Customer click to detail pages", "PR #FE-7, #FE-8", "Yahia", "layouts/orders/, customers/"),
    ]
    for i, row in enumerate(w1_prs):
        pdf.pr_row(*row, shade=(i % 2 == 1))

    pdf.ln(4)

    # Week 2 PRs
    pdf.section_title("Week 2 PRs: Routes + Export + Dashboard Cleanup", 40, 160, 80)
    pdf.pr_table_header()

    w2_prs = [
        ("FE-11", "feat: image upload + drag-and-drop in ProductForm", "PR #FE-5", "Yousef", "products/comp/ImageUpload"),
        ("FE-12", "feat: form validation + MUI error states in ProductForm", "PR #FE-5", "Yousef", "products/comp/validation"),
        ("FE-13", "feat: ConfirmDialog in product delete + order cancel", "PR #FE-3, #FE-6, #FE-7", "Yousef", "products/, orders/detail"),
        ("FE-14", "feat: CSV export on Orders + Customers pages", "PR #FE-4", "Yousef", "orders/, customers/"),
        ("FE-15", "feat: add product routes (/new, /:id/edit, /:id/duplicate)", "PR #FE-6", "Yousef", "routes.js"),
        ("FE-16", "fix: add Sign-Up + Billing routes + sidenav entry", "None (independent)", "Yahia", "routes.js, Sidenav/"),
        ("FE-17", "fix: replace SatisfactionRate + ReferralTracking mock data", "None (independent)", "Yahia", "dashboard/components/"),
        ("FE-18", "chore: delete barChartDataDashboard mock + audit dashboard", "None (independent)", "Yahia", "dashboard/data/, dashboard/"),
        ("FE-19", "feat: add order/customer detail + create routes", "PR #FE-7, #FE-8, #FE-9", "Yahia", "routes.js"),
        ("FE-20", "feat: empty states for Products, Orders, Customers lists", "None (independent)", "Yahia", "layouts/*/components/"),
        ("FE-21", "feat: loading skeletons for ProductForm, OrderDetail, CustomerDetail", "PR #FE-5, #FE-7, #FE-8", "Yousef", "layouts/*/Skeleton.js"),
        ("FE-22", "test: product CRUD + snackbar + routes + dashboard + empty states", "PR #FE-6, #FE-19, #FE-20", "Both", "__tests__/"),
    ]
    for i, row in enumerate(w2_prs):
        pdf.pr_row(*row, shade=(i % 2 == 1))

    pdf.ln(5)

    # ========== TIMELINE ==========
    pdf.section_title("Parallel Work Timeline", 100, 100, 100)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Week 1:", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.timeline_line("Yousef:", [
        "[FE-1 Snackbar] --> [FE-2 Replace alerts] --> [FE-3 ConfirmDialog] --> [FE-4 CSV] --> [FE-5 ProductForm] --> [FE-6 CRUD pages]",
        " Day 1               Day 1                    Day 2                   Day 2         Day 3-4                Day 5",
    ], (30, 100, 180))

    pdf.timeline_line("Yahia:", [
        "     wait FE-1...   --> [FE-7 OrderDetail (3 days)] -------> [FE-8 CustomerDetail] --> [FE-9 CustCreate] --> [FE-10 Wiring]",
        "                        Day 1-3                               Day 3-4                  Day 4                 Day 5",
    ], (40, 160, 80))

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "Week 2:", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.timeline_line("Yousef:", [
        "[FE-11 ImgUpload] --> [FE-12 Validation] --> [FE-13 ConfirmDlg] --> [FE-14 CSV Export] --> [FE-15 Routes] --> [FE-21 Skeletons]",
        " Day 1                 Day 2                  Day 2                  Day 3                 Day 3             Day 4-5",
    ], (30, 100, 180))

    pdf.timeline_line("Yahia:", [
        "[FE-16 SignUp+Billing] --> [FE-17 SatisfRate] --> [FE-18 MockClean] --> [FE-19 Routes] --> [FE-20 EmptyStates] --> [FE-22 Tests]",
        " Day 1                     Day 1                  Day 2                 Day 3             Day 3-4                Day 5",
    ], (40, 160, 80))

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
        ("src/context/SnackbarContext.js", "src/layouts/orders/detail/ (entire dir)"),
        ("src/components/GlobalSnackbar/", "src/layouts/orders/detail/components/"),
        ("src/components/ConfirmDialog/", "src/layouts/customers/detail/ (entire dir)"),
        ("src/utils/exportCSV.js", "src/layouts/customers/create/"),
        ("src/layouts/products/components/ProductForm/", "src/layouts/customers/detail/components/"),
        ("src/layouts/products/create/", "src/layouts/dashboard/components/SatisfactionRate/"),
        ("src/layouts/products/edit/", "src/layouts/dashboard/components/ReferralTracking/"),
        ("src/layouts/products/duplicate/", "src/layouts/dashboard/data/barChartData.js"),
        ("src/__tests__/products.test.js", "src/layouts/orders/components/EmptyState.js"),
        ("src/__tests__/snackbar.test.js", "src/layouts/customers/components/EmptyState.js"),
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
        "src/App.js - Yousef wraps with SnackbarProvider (FE-1). Done first, Yahia never touches it.",
        "src/routes.js - Yousef adds product routes (FE-15, Day 3). Yahia adds sign-up/billing/detail routes (FE-16 Day 1, FE-19 Day 3). Merge FE-16 first, then FE-15 rebases, then FE-19 rebases.",
        "src/layouts/products/index.js - Yousef modifies (FE-2 alerts, FE-4 export, FE-13 confirm). All Yousef's PRs. No conflict.",
        "src/layouts/orders/index.js - Yahia modifies 'View' button (FE-10). Yousef adds CSV export (FE-14). FE-10 merges first, FE-14 rebases.",
        "src/layouts/customers/index.js - Yahia modifies click handler (FE-10). Yousef adds CSV export (FE-14). FE-10 merges first, FE-14 rebases.",
        "src/examples/Sidenav/ - Only Yahia touches this (FE-16). No conflict.",
    ]
    for s in shared:
        pdf.cell(3, 4.5, "")
        pdf.cell(2, 4.5, "-")
        pdf.cell(0, 4.5, f" {s}", new_x="LMARGIN", new_y="NEXT")

    # Output
    output_path = r"c:\Users\PC\Desktop\NUMU\NUMU-api\NUMU_Dashboard_Frontend_Plan.pdf"
    pdf.output(output_path)
    print(f"PDF generated: {output_path}")


if __name__ == "__main__":
    build_pdf()
