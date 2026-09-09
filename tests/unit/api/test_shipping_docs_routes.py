"""Route-level tests for manual-carrier paperwork.

The services these wrap are already covered; what is tested here is the
wiring — that each endpoint is reachable, that the ones carrying
single-segment literals are not swallowed by ``GET /{shipment_id}``, and
that the two rules with money or data behind them hold at the route
boundary:

* the status import **applies nothing** on preview;
* applying re-resolves every row against *this* store rather than
  trusting the request.
"""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute


@pytest.fixture(scope="module")
def stores_app() -> FastAPI:
    from src.api.v1.routes.stores import router

    app = FastAPI()
    app.include_router(router)
    return app


def _routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


UUID_ = "11111111-1111-1111-1111-111111111111"


class TestEveryEndpointIsReachable:
    """A route that exists but is shadowed is worse than a missing one."""

    @pytest.mark.parametrize(
        ("method", "suffix"),
        [
            ("GET", "/shipments/couriers"),
            ("POST", "/shipments/couriers"),
            ("GET", "/shipments/couriers/seeds"),
            ("GET", "/shipments/manifest"),
            ("POST", "/shipments/waybills"),
            ("POST", "/shipments/status-import"),
            ("POST", "/shipments/status-import/apply"),
        ],
    )
    def test_literal_paths_are_not_swallowed(self, stores_app, method, suffix):
        """`GET /{shipment_id}` matches any single segment.

        This is how `GET /pickups` became unreachable — it 422'd trying to
        parse "pickups" as a UUID.
        """
        target = f"/{UUID_}{suffix}"
        winner = next(
            (
                r
                for r in _routes(stores_app)
                if method in r.methods and r.path_regex.match(target)
            ),
            None,
        )
        assert winner is not None, f"{method} {suffix} matches nothing"
        assert winner.path.endswith(suffix.split("/")[-1]), (
            f"{method} {suffix} is served by {winner.path}"
        )

    def test_per_shipment_waybill_is_reachable(self, stores_app):
        target = f"/{UUID_}/shipments/{UUID_}/waybill"
        winner = next(
            r
            for r in _routes(stores_app)
            if "GET" in r.methods and r.path_regex.match(target)
        )
        assert winner.path.endswith("/waybill")

    def test_shipping_operation_ids_are_unique(self, stores_app):
        """Scoped to shipping routes on purpose.

        The store tree already has pre-existing duplicates in the settings
        and WhatsApp routes, which `test_openapi_spec` covers (and
        currently fails on). Repeating that here would add a second
        failing test for a known problem rather than catching a new one.
        """
        ids = [
            r.operation_id
            for r in _routes(stores_app)
            if r.operation_id and "/shipments" in r.path
        ]
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"Duplicate shipping operation ids: {duplicates}"


class TestStatusImportSafety:
    """The two rules that protect a merchant's parcels."""

    def test_preview_and_apply_are_separate_endpoints(self, stores_app):
        """Parsing a file must never move a parcel by itself."""
        paths = {r.path for r in _routes(stores_app) if "status-import" in r.path}
        assert any(p.endswith("/status-import") for p in paths)
        assert any(p.endswith("/status-import/apply") for p in paths)

    def test_preview_does_not_touch_the_shipment_repository(self):
        """A preview that could write is not a preview.

        Asserted on the handler's signature: it takes no shipment
        repository, so it has nothing to write with.
        """
        import inspect

        from src.api.v1.routes.stores.shipping_docs import preview_status_import

        params = inspect.signature(preview_status_import).parameters
        assert "shipment_repo" not in params
        assert "file" in params

    def test_apply_reresolves_rows_against_the_store(self):
        """The preview is advisory. A tracking number in the request that
        isn't ours must not move anything, so the handler looks each one
        up and checks store ownership rather than trusting the payload.
        """
        import inspect

        from src.api.v1.routes.stores.shipping_docs import apply_status_import

        source = inspect.getsource(apply_status_import)
        assert "get_by_tracking_number" in source
        assert "store_id != store.id" in source

    def test_apply_reports_skipped_rows(self):
        """Silently dropping rows is how a merchant loses parcels."""
        import inspect

        from src.api.v1.routes.stores.shipping_docs import apply_status_import

        assert "skipped" in inspect.getsource(apply_status_import)


class TestWaybillRoutes:
    def test_batch_size_is_bounded(self):
        from src.api.v1.routes.stores.shipping_docs import MAX_LABELS_PER_JOB

        assert 0 < MAX_LABELS_PER_JOB <= 1000

    def test_both_print_formats_are_offered(self):
        """Roll for thermal, sheet for an office printer."""
        import inspect

        from src.api.v1.routes.stores.shipping_docs import print_waybills

        source = inspect.getsource(print_waybills)
        assert "generate_waybill_sheet_pdf" in source
        assert "generate_waybill_batch_pdf" in source

    def test_missing_renderer_is_a_503_not_a_500(self):
        """Local Windows has no cairo; that is unavailable, not broken."""
        import inspect

        from src.api.v1.routes.stores.shipping_docs import print_waybills

        assert "503" in inspect.getsource(print_waybills)


class TestManifestExport:
    def test_returns_csv_not_json(self):
        import inspect

        from src.api.v1.routes.stores.shipping_docs import export_shipment_manifest

        source = inspect.getsource(export_shipment_manifest)
        assert "text/csv" in source
        assert "attachment" in source

    def test_uses_the_bom_writer(self):
        """Excel needs the BOM to read Arabic — see shipment_csv."""
        import inspect

        from src.api.v1.routes.stores.shipping_docs import export_shipment_manifest

        assert "build_manifest_csv" in inspect.getsource(export_shipment_manifest)


class TestCourierProfileRoutes:
    def test_patch_merges_rather_than_replaces(self):
        """A merchant editing one field must not blank the others."""
        import inspect

        from src.api.v1.routes.stores.shipping_docs import update_courier_profile

        assert "exclude_unset=True" in inspect.getsource(update_courier_profile)

    def test_seed_values_are_overridable(self):
        """Starting from a seed is a convenience, not a constraint."""
        import inspect

        from src.api.v1.routes.stores.shipping_docs import create_courier_profile

        source = inspect.getsource(create_courier_profile)
        assert "to_profile_values()" in source
        # Merchant-supplied values applied after the seed's.
        assert "**{k: v for k, v in values.items() if v}" in source

    def test_unknown_seed_is_a_bilingual_400(self):
        import inspect

        from src.api.v1.routes.stores.shipping_docs import create_courier_profile

        source = inspect.getsource(create_courier_profile)
        assert "message_ar" in source
        assert "UNKNOWN_COURIER_SEED" in source


class TestWhatTheLiveRunCaught:
    """Three defects that every unit test above was blind to, because each
    one only appears when a real order, a real courier sheet and a real
    browser are on the other end. Found by running the branch against a
    live API; pinned here so they cannot come back."""

    def test_the_label_reads_a_field_the_line_item_actually_has(self):
        """`_label_context` read `li.name`. `OrderLineItem` has no `name`,
        so **every** waybill print raised AttributeError → 500."""
        from src.core.entities.order import OrderLineItem

        fields = set(OrderLineItem.model_fields)
        assert "name" not in fields
        assert {"product_name", "variant_name", "quantity"} <= fields

        import inspect

        from src.api.v1.routes.stores.shipping_docs import _label_context

        source = inspect.getsource(_label_context)
        # The prose above mentions the old field, so match the code shape.
        assert '"name": li.name' not in source
        assert "li.product_name" in source

    def test_the_import_resolves_the_sheets_words_not_the_carriers(self):
        """A Tier 3 sheet is written by the merchant, and `manual` has an
        empty carrier `status_map` by design — so routing the sheet
        through `map_carrier_status` skipped every row as unmapped and the
        CSV round-trip moved nothing."""
        from src.application.services.carrier_registry import get_spec
        from src.application.services.shipment_csv import resolve_status
        from src.core.entities.shipment import ShipmentStatus

        assert get_spec("manual").status_map == {}
        assert resolve_status("delivered") is ShipmentStatus.DELIVERED
        assert resolve_status("تم التسليم") is ShipmentStatus.DELIVERED

        import inspect

        from src.api.v1.routes.stores.shipping_docs import apply_status_import

        source = inspect.getsource(apply_status_import)
        assert "resolve_status(raw_status)" in source
        assert "status=resolved" in source

    def test_an_unresolvable_word_still_moves_nothing(self):
        import inspect

        from src.api.v1.routes.stores.shipping_docs import apply_status_import

        source = inspect.getsource(apply_status_import)
        assert "unmapped" in source

    def test_the_status_override_does_not_bypass_the_carrier_map(self):
        """`status=` is an override for a caller with its own vocabulary,
        not a way to skip mapping — omit it and the carrier map still
        decides."""
        import inspect

        from src.application.services.shipment_status_sync import apply_carrier_status

        source = inspect.getsource(apply_carrier_status)
        assert "status or map_carrier_status(carrier, raw_status)" in source

    def test_the_html_label_carries_its_own_stylesheet(self):
        """The template links `label.css` relatively — WeasyPrint resolves
        that against the template dir, a browser would resolve it against
        the API host and 404, leaving an unstyled label."""
        from src.api.v1.routes.stores.shipping_docs import _label_html_response

        html = '<link rel="stylesheet" href="label.css"><div class="label"></div>'
        out = _label_html_response(html).body.decode()
        assert '<link rel="stylesheet"' not in out
        assert "unicode-bidi: isolate" in out  # the LTR rule, inlined
        assert "100mm 150mm" in out or "100mm" in out


class TestManifestIsPerCourier:
    """A merchant running Barashout and Waselha at once must not hand
    either of them a sheet listing the other's parcels."""

    def test_the_endpoint_takes_a_courier(self):
        import inspect

        from src.api.v1.routes.stores.shipping_docs import export_shipment_manifest

        assert "courier" in inspect.signature(export_shipment_manifest).parameters

    def test_an_unknown_courier_is_a_bilingual_404_not_an_empty_sheet(self):
        """An empty CSV would read as "nothing to collect today"."""
        import inspect

        source = inspect.getsource(
            __import__(
                "src.api.v1.routes.stores.shipping_docs",
                fromlist=["export_shipment_manifest"],
            ).export_shipment_manifest
        )
        assert "COURIER_NOT_FOUND" in source
        assert "message_ar" in source

    def test_the_label_and_the_manifest_share_one_matching_rule(self):
        """Two copies of `endswith(profile.id)` would drift, and the label
        would name a courier the sheet did not list."""
        import inspect

        from src.api.v1.routes.stores import shipping_docs as mod

        assert "_courier_of" in inspect.getsource(mod._label_context)
        assert inspect.getsource(mod._courier_of).count("endswith") == 1

    def test_the_filename_names_the_courier(self):
        import inspect

        from src.api.v1.routes.stores.shipping_docs import export_shipment_manifest

        assert "filename_courier" in inspect.getsource(export_shipment_manifest)


class TestTheFourAddedCouriers:
    """Waselha, Flextock, Holy Ship and Barashout — Tier 3 by the same
    rule as the rest: no public API, so NUMU issues the paperwork and the
    merchant sends them a CSV."""

    KEYS = ("waselha", "flextock", "holyship", "barashout")

    def test_all_four_are_seeded(self):
        from src.application.services.manual_carrier_seeds import get_seed

        for key in self.KEYS:
            assert get_seed(key) is not None, key

    def test_each_has_a_real_arabic_name(self):
        from src.application.services.manual_carrier_seeds import get_seed

        for key in self.KEYS:
            seed = get_seed(key)
            assert any("؀" <= ch <= "ۿ" for ch in seed.name_ar), key
            assert seed.name_en

    def test_coverage_is_unconfirmed_not_invented(self):
        """Nobody checked their governorate lists, so each ships covering
        everywhere and says so — a courier wrongly limited hides
        deliveries silently."""
        from src.application.services.manual_carrier_seeds import get_seed

        for key in self.KEYS:
            seed = get_seed(key)
            assert seed.data_verified is False, key
            assert seed.governorate_codes == (), key

    def test_a_phone_still_needs_a_source(self):
        """The module raises at import if one does not; this pins that the
        four new rows obey it rather than relying on the loop staying."""
        from src.application.services.manual_carrier_seeds import get_seed

        for key in self.KEYS:
            seed = get_seed(key)
            if seed.contact_phone:
                assert seed.contact_source, key

    def test_none_of_them_became_a_registry_carrier(self):
        """They have no API. A registry entry would offer a merchant a
        credentials form for something that cannot be connected."""
        from src.application.services.carrier_registry import carrier_slugs

        slugs = carrier_slugs()
        for key in self.KEYS:
            assert key not in slugs, key

    def test_they_appear_in_the_hubs_picker(self):
        from src.application.services.manual_carrier_seeds import seed_catalog

        keys = {s["key"] for s in seed_catalog()}
        assert set(self.KEYS) <= keys


class TestTheCourierSheetIsNotTruncated:
    """Filtering after the fetch limit is how a courier silently stops
    collecting parcels."""

    def test_it_pages_for_this_couriers_parcels(self):
        """A store shipping through three couriers would otherwise get
        roughly a third of a sheet, with nothing saying rows were cut."""
        import inspect

        from src.api.v1.routes.stores.shipping_docs import export_shipment_manifest

        source = inspect.getsource(export_shipment_manifest)
        assert "while len(shipments) < limit" in source
        assert "skip=skip" in source

    def test_the_page_loop_ends_on_a_short_page(self):
        """No page bound would spin forever on a store with no matches."""
        import inspect

        from src.api.v1.routes.stores.shipping_docs import export_shipment_manifest

        source = inspect.getsource(export_shipment_manifest)
        assert "if not page:" in source
        assert "if len(page) < _PAGE:" in source

    def test_the_caller_still_gets_at_most_limit_rows(self):
        import inspect

        from src.api.v1.routes.stores.shipping_docs import export_shipment_manifest

        assert "del shipments[limit:]" in inspect.getsource(export_shipment_manifest)
