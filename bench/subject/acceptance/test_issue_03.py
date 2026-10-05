"""M3 — Product lifecycle gates the sale.

`Product.state` with `"draft"`/`"active"`/`"retired"` in
`catalog/products.py`, `Catalog.retire`/`activate`/`active()` and a
`Catalog.get(sku, include_retired=True)` that keeps resolving a withdrawn
sku, then `Cart.add` refusing a non-active sku, `Cart.retired_skus()`, and
`RevenueReport.retired_revenue()`.

Three wrong implementations this file exists to kill, all of them plausible
and all of them passing a thinner suite:

1. **"Retired means gone."** Dropping the record, or defaulting
   `include_retired` to `False`, breaks every order that already charged for
   the product. Asserted by pricing a placed order *after* the retirement.
2. **"The gate is `state != "retired"`."** A `"draft"` product then sells.
   Every gate below is asserted against a draft product as well as a retired
   one, which is the only way to tell an equality from an exclusion.
3. **"Revenue is the order's."** `retired_revenue` summing whole orders, or
   ignoring quantity, coincides with the right answer on a one-line cart of
   one unit. Every cart here carries a line that is not retired and a
   quantity that is not 1, at a price that is not round.
"""

from datetime import date

import pytest

from billing import api

# Prices chosen so no figure below can be reached by accident: 137.45 * 3 is
# 412.35, which is not 137.45 (an implementation ignoring quantity), not
# 502.34 (the whole cart), and not any product's own price.
DESK_PRICE = api.Money("137.45")
LAMP_PRICE = api.Money("89.99")
RUG_PRICE = api.Money("24.75")
DESK_QUANTITY = 3
# 137.45 * 3 = 412.35
DESK_REVENUE = api.Money("412.35")
# 412.35 + 89.99 = 502.34
CART_SUBTOTAL = api.Money("502.34")


def build_catalog():
    """A catalog with one variant, one non-taxable product and one draft.

    The variant and the `taxable=False` product are here so that a
    transition which rebuilds a `Product` from a subset of its fields is
    visible: it would lose the parent link or start charging tax.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", DESK_PRICE, "furniture"))
    catalog.add(api.Product("SKU-2", "Lamp", LAMP_PRICE, "furniture"))
    catalog.add(
        api.Product("SKU-3", "Gift card", RUG_PRICE, "vouchers", taxable=False)
    )
    catalog.add(
        api.Product(
            "SKU-1-RED", "Desk (red)", DESK_PRICE, "furniture", parent_sku="SKU-1"
        )
    )
    catalog.add(
        api.Product("SKU-9", "Prototype", RUG_PRICE, "furniture", state="draft")
    )
    return catalog


def build_cart(catalog):
    """Three desks and one lamp, so quantity and line identity both matter."""
    customer = api.Customer("C-1", "Ada", "US-CA")
    cart = api.Cart(catalog, customer)
    cart.add("SKU-1", DESK_QUANTITY)
    cart.add("SKU-2")
    return cart


def build_pricing():
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    return api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(table),
    )


def build_placed_order(catalog, order_id="O-1"):
    order = api.Order(order_id, build_cart(catalog), build_pricing())
    order.place()
    return order


def test_the_lifecycle_vocabulary_is_exported_and_closed():
    """The three states and both exceptions cross the facade.

    The vocabulary is exported as a value rather than left implicit in
    validation, because `Cart.add` gates on equality with one of these
    strings: a caller that has to guess the spelling writes a product nothing
    can sell. Asserted as an exact tuple, so a fourth state cannot be added
    without a test saying so.
    """
    assert api.PRODUCT_STATES == ("draft", "active", "retired")
    assert issubclass(api.ProductNotActive, Exception)
    assert issubclass(api.InvalidProductTransition, Exception)
    # Not collapsed into UnknownProduct: a retired product exists and prices.
    assert not issubclass(api.ProductNotActive, api.UnknownProduct)


def test_the_default_state_is_active_so_every_existing_product_sells():
    """A product written before the lifecycle existed is sellable.

    The default is what keeps the base behavior intact. A default of
    `"draft"` would be defensible in the abstract and would make every
    existing construction in the package unsellable, so it is pinned here
    rather than left to the reader.
    """
    product = api.Product("SKU-5", "Chair", api.Money("61.20"), "furniture")
    assert product.state == "active"
    catalog = build_catalog()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1")
    assert [line.product.sku for line in cart.lines] == ["SKU-1"]


def test_an_unknown_state_is_rejected_at_construction():
    """Including a near miss of a real one.

    `"Active"` is the interesting case: validation that lowercases, or that
    only checks truthiness, would accept it, and the product would then
    price, report and never sell. The gate downstream is an equality against
    `"active"` exactly.
    """
    with pytest.raises(ValueError):
        api.Product("SKU-6", "Desk", DESK_PRICE, "furniture", state="withdrawn")
    with pytest.raises(ValueError):
        api.Product("SKU-6", "Desk", DESK_PRICE, "furniture", state="Active")
    with pytest.raises(ValueError):
        api.Product("SKU-6", "Desk", DESK_PRICE, "furniture", state="")
    with pytest.raises(TypeError):
        api.Product("SKU-6", "Desk", DESK_PRICE, "furniture", state=1)
    # Every valid state does construct, so the check is a vocabulary and not
    # a blanket refusal of anything but the default.
    for state in api.PRODUCT_STATES:
        assert api.Product("SKU-6", "Desk", DESK_PRICE, "x", state=state).state == state


def test_retiring_keeps_the_sku_resolvable_so_a_placed_order_keeps_pricing():
    """Retirement is a sales gate, not a deletion.

    This is the expensive mistake: a `get` that refused a retired sku, or a
    `retire` that popped the record, would make every order placed before the
    withdrawal raise instead of reporting what it charged. The order is priced
    *after* the retirement, and its price history is read after it too.
    """
    catalog = build_catalog()
    catalog.record_price("SKU-1", date(2026, 1, 1), api.Money("150.00"))
    order = build_placed_order(catalog)
    catalog.retire("SKU-1")
    assert catalog.get("SKU-1").sku == "SKU-1"
    assert catalog.get("SKU-1").state == "retired"
    assert order.subtotal == CART_SUBTOTAL
    # Tax is 10% per taxable line, rounded per line: the desk line is
    # 412.35 * 0.10 = 41.235, half up 41.24; the lamp line is
    # 89.99 * 0.10 = 8.999, half up 9.00. So 502.34 + 50.24 = 552.58.
    assert order.tax_total == api.Money("50.24")
    assert order.total == api.Money("552.58")
    assert len(catalog) == 5
    # The recorded history is keyed by sku and survives the replacement.
    assert catalog.price_as_of("SKU-1", date(2026, 6, 1)) == api.Money("150.00")


def test_a_transition_replaces_the_record_and_carries_every_other_field():
    """`retire` returns the new record, and loses nothing on the way.

    A transition that rebuilt a `Product` from sku, name, price and category
    would silently drop `taxable` and `parent_sku` — a gift card would start
    being taxed and a variant would stop being a variant. Both are asserted
    through their readers rather than off the field.
    """
    catalog = build_catalog()
    retired = catalog.retire("SKU-3")
    assert retired is catalog.get("SKU-3")
    assert retired.state == "retired"
    assert retired.taxable is False
    assert retired.unit_price == RUG_PRICE
    assert retired.name == "Gift card"
    assert retired.category == "vouchers"
    catalog.retire("SKU-1-RED")
    assert [p.sku for p in catalog.variants_of("SKU-1")] == ["SKU-1-RED"]
    assert catalog.get("SKU-1-RED").parent_sku == "SKU-1"


def test_cart_add_refuses_a_retired_sku_and_names_the_sku_and_its_state():
    """The message has to carry both, because the caller has to answer a
    human: "SKU-2 was discontinued" and "SKU-2 is not released yet" are
    different sentences, and a bare refusal supports neither.
    """
    catalog = build_catalog()
    catalog.retire("SKU-2")
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    with pytest.raises(api.ProductNotActive) as excinfo:
        cart.add("SKU-2")
    assert "SKU-2" in str(excinfo.value)
    assert "retired" in str(excinfo.value)
    # The refusal left nothing behind: no line, no zero-quantity ghost.
    assert cart.lines == []
    assert cart.quantity == 0


def test_a_draft_product_is_refused_too_because_the_gate_is_is_it_active():
    """Kills `state != "retired"`.

    A draft product is the whole reason the gate is an equality. An
    exclusion-based gate sells an unreleased product at whatever placeholder
    price it was staged with, and every other assertion in this file passes
    while it does.
    """
    catalog = build_catalog()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    with pytest.raises(api.ProductNotActive) as excinfo:
        cart.add("SKU-9")
    assert "SKU-9" in str(excinfo.value)
    assert "draft" in str(excinfo.value)
    # And an unknown sku is still a different failure from an unsellable one.
    with pytest.raises(api.UnknownProduct):
        cart.add("SKU-NOPE")


def test_activate_makes_a_sku_sellable_again_from_either_other_state():
    """Retired then active sells; draft then active sells.

    Both directions, because an `activate` implemented as "undo a retirement"
    would leave a draft product permanently unsellable, and a staged catalog
    is exactly what the draft state is for.
    """
    catalog = build_catalog()
    catalog.retire("SKU-1")
    catalog.activate("SKU-1")
    assert catalog.get("SKU-1").state == "active"
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1", DESK_QUANTITY)
    assert cart.subtotal == DESK_REVENUE
    catalog.activate("SKU-9")
    cart.add("SKU-9")
    assert [line.product.sku for line in cart.lines] == ["SKU-1", "SKU-9"]


def test_a_transition_that_would_change_nothing_raises():
    """A silent no-op lets a caller believe it withdrew a product still on
    sale, which is the expensive direction of the mistake. Both transitions
    refuse, and the state is unchanged by the refusal.
    """
    catalog = build_catalog()
    catalog.retire("SKU-1")
    with pytest.raises(api.InvalidProductTransition):
        catalog.retire("SKU-1")
    assert catalog.get("SKU-1").state == "retired"
    with pytest.raises(api.InvalidProductTransition):
        catalog.activate("SKU-2")
    assert catalog.get("SKU-2").state == "active"
    # A draft product is not active, so activating it is not a no-op.
    assert catalog.activate("SKU-9").state == "active"
    # And an unregistered sku is an unknown product, not a bad transition.
    with pytest.raises(api.UnknownProduct):
        catalog.retire("SKU-NOPE")


def test_active_excludes_draft_and_retired_and_is_sorted_by_sku():
    """One order for every collection this class returns.

    Sorted by sku like `by_category`, `search` and `variants_of`, and
    asserted as an exact list: insertion order would put SKU-1-RED last, so a
    `list(self._products.values())` implementation is visible here.
    """
    catalog = build_catalog()
    assert [p.sku for p in catalog.active()] == [
        "SKU-1", "SKU-1-RED", "SKU-2", "SKU-3"
    ]
    catalog.retire("SKU-2")
    assert [p.sku for p in catalog.active()] == ["SKU-1", "SKU-1-RED", "SKU-3"]
    # Retirement removed nothing: the registry still holds all five.
    assert len(catalog) == 5
    # `by_category` is unchanged by the lifecycle and still reports every
    # furniture product, retired and draft alike: `active()` is the sellable
    # view, and narrowing the older collections would change what an existing
    # caller asking "what furniture do we have records for" gets back.
    assert [p.sku for p in catalog.by_category("furniture")] == [
        "SKU-1", "SKU-1-RED", "SKU-2", "SKU-9"
    ]


def test_get_with_include_retired_false_hides_a_retired_sku_and_only_that():
    """The flag names retired, and deliberately not "not active".

    A draft product still resolves under `False`: it is unreleased, not
    withdrawn, and a caller staging a catalog has to read it back. An
    implementation that read the flag as "active only" would make staging
    impossible, and nothing else in this file would notice.
    """
    catalog = build_catalog()
    catalog.retire("SKU-2")
    assert catalog.get("SKU-2").state == "retired"          # default is True
    assert catalog.get("SKU-2", include_retired=True).state == "retired"
    with pytest.raises(api.UnknownProduct):
        catalog.get("SKU-2", include_retired=False)
    assert catalog.get("SKU-9", include_retired=False).state == "draft"
    assert catalog.get("SKU-1", include_retired=False).state == "active"


def test_a_line_added_before_retirement_keeps_its_total_and_its_place():
    """Retirement is not a removal, at the cart as well as the catalog.

    A cart that dropped the line on retirement would change a subtotal a
    customer was already shown. The line's own `line_total` is asserted, not
    just the cart's subtotal, so an implementation that zeroed the line and
    compensated elsewhere still fails.
    """
    catalog = build_catalog()
    cart = build_cart(catalog)
    assert cart.subtotal == CART_SUBTOTAL
    catalog.retire("SKU-1")
    assert [line.product.sku for line in cart.lines] == ["SKU-1", "SKU-2"]
    desk = cart.lines[0]
    assert desk.quantity == DESK_QUANTITY
    assert desk.line_total == DESK_REVENUE       # 137.45 * 3 = 412.35
    assert cart.subtotal == CART_SUBTOTAL        # 412.35 + 89.99 = 502.34
    assert cart.quantity == 4                    # 3 desks + 1 lamp


def test_retired_skus_reports_only_this_carts_retired_lines_sorted_by_sku():
    """Lines, not the catalog; and an order, not a set.

    Two retired products are put into the cart in descending sku order, so an
    implementation reporting insertion order fails. A third product is
    retired in the catalog but never added, which an implementation reading
    the catalog rather than the lines would wrongly include.
    """
    catalog = build_catalog()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-3")
    cart.add("SKU-2")
    cart.add("SKU-1", DESK_QUANTITY)
    assert cart.retired_skus() == ()
    catalog.retire("SKU-3")
    catalog.retire("SKU-1")
    catalog.retire("SKU-1-RED")          # retired, but never added to the cart
    assert cart.retired_skus() == ("SKU-1", "SKU-3")
    # The active line is absent, and the report is a tuple a human can read
    # in order rather than a set.
    assert isinstance(cart.retired_skus(), tuple)
    assert "SKU-2" not in cart.retired_skus()


def test_retired_revenue_counts_the_retired_lines_and_not_the_whole_order():
    """412.35, not 502.34, and not 137.45.

    Three wrong implementations are separated by this one assertion: summing
    the order's subtotal gives 502.34, ignoring quantity gives 137.45, and
    the right answer is the retired line's own total. That is why the cart
    carries a second, still-active line and a quantity above one.
    """
    catalog = build_catalog()
    order = build_placed_order(catalog)
    catalog.retire("SKU-1")
    report = api.RevenueReport([order])
    assert report.retired_revenue() == DESK_REVENUE      # 137.45 * 3 = 412.35
    assert report.retired_revenue() != order.subtotal
    assert report.retired_revenue() != DESK_PRICE


def test_retired_revenue_is_recomputed_on_each_read_not_cached():
    """The same report object answers differently after a retirement.

    This is the question the method exists for — how much of what we booked
    comes from things we no longer sell — and a figure computed at
    construction answers it only for products retired before the report was
    built, which is the uninteresting case.
    """
    catalog = build_catalog()
    report = api.RevenueReport([build_placed_order(catalog)])
    assert report.retired_revenue() == api.Money("0.00")
    catalog.retire("SKU-2")
    assert report.retired_revenue() == LAMP_PRICE         # 89.99
    catalog.retire("SKU-1")
    # Both lines retired: 412.35 + 89.99 = 502.34
    assert report.retired_revenue() == CART_SUBTOTAL
    catalog.activate("SKU-1")
    assert report.retired_revenue() == LAMP_PRICE


def test_retired_revenue_excludes_a_cancelled_line_like_every_other_figure():
    """Cancelled first, retired second.

    A retired line is revenue that was charged; a cancelled one is not. A
    line that is both contributes nothing, because `_active_lines` is the one
    definition of "counts" in the report and this figure routes through it
    like `gross` and `by_category` do. An implementation reading
    `order.cart.lines` directly passes every other test here.
    """
    catalog = build_catalog()
    order = build_placed_order(catalog)
    order.cancel_line("SKU-1")
    catalog.retire("SKU-1")
    catalog.retire("SKU-2")
    report = api.RevenueReport([order])
    # Only the lamp: the cancelled desk line is not revenue at all.
    assert report.retired_revenue() == LAMP_PRICE
    assert report.retired_revenue() != CART_SUBTOTAL
    # And the figure still agrees with the report's own active-line view:
    # by_category sees the same lamp line and no desk.
    assert report.by_category() == {"furniture": LAMP_PRICE}


def test_retired_revenue_excludes_a_wholly_cancelled_order():
    """Excluded at construction, like every other figure in the class.

    Asserted separately from the cancelled-*line* case because the two are
    different filters in the implementation: one is `_active_lines`, the
    other is the status filter in `__init__`.
    """
    catalog = build_catalog()
    kept = build_placed_order(catalog, order_id="O-1")
    dropped = build_placed_order(catalog, order_id="O-2")
    dropped.cancel()
    catalog.retire("SKU-1")
    report = api.RevenueReport([kept, dropped])
    assert report.retired_revenue() == DESK_REVENUE       # one order's worth
    assert report.retired_revenue() != api.Money("824.70")  # 412.35 * 2


def test_retired_revenue_is_zero_when_nothing_is_retired():
    """Zero rather than an error or an empty mapping.

    A report over an all-active catalog is the normal case, and it has to
    answer the question with a figure a caller can add to another figure.
    """
    catalog = build_catalog()
    report = api.RevenueReport([build_placed_order(catalog)])
    assert report.retired_revenue() == api.Money("0.00")
    assert api.RevenueReport([]).retired_revenue() == api.Money("0.00")
    # A draft product is not retired: nothing is attributable to it.
    catalog.activate("SKU-9")
    catalog.retire("SKU-9")
    assert report.retired_revenue() == api.Money("0.00")


def test_load_carries_state_as_a_seventh_positional_field():
    """The criterion's `load` half, which no other test here reaches.

    Two shapes and both matter: a seven-element row naming the state, and a
    shorter row that stops before it and must still mean `"active"`. The
    state is the *seventh* field, after `parent_sku`, so an implementation
    that inserted it anywhere earlier builds a product whose category or
    taxability is the word `"retired"` — and `RUG` is the row that shows it,
    since it is the non-taxable one.
    """
    catalog = api.Catalog()
    catalog.load(
        [
            ("DSK-1", "Desk, oak", DESK_PRICE, "furniture", True, None,
             "retired"),
            ("LMP-1", "Lamp", LAMP_PRICE, "furniture"),
            ("RUG-1", "Rug", RUG_PRICE, "furniture", False, None, "draft"),
        ]
    )
    assert catalog.get("DSK-1").state == "retired"
    assert catalog.get("LMP-1").state == "active"
    assert catalog.get("RUG-1").state == "draft"
    # The shorter row is unchanged in every other respect, and the state did
    # not displace a field: the rug is still the non-taxable one.
    assert catalog.get("LMP-1").unit_price == LAMP_PRICE
    assert catalog.get("LMP-1").taxable is True
    assert catalog.get("RUG-1").taxable is False
    assert catalog.get("RUG-1").category == "furniture"
    # And `active()` reads the loaded states rather than the default.
    assert [product.sku for product in catalog.active()] == ["LMP-1"]


def test_load_accepts_state_in_a_mapping_row_too():
    """The mapping shape names its fields, so the position cannot be wrong —
    which is what makes it the control for the tuple test above.
    """
    catalog = api.Catalog()
    catalog.load(
        [
            {"sku": "DSK-2", "name": "Desk, ash", "unit_price": DESK_PRICE,
             "category": "furniture", "state": "retired"},
        ]
    )
    assert catalog.get("DSK-2").state == "retired"
    with pytest.raises(api.ProductNotActive):
        api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA")).add("DSK-2")


def test_the_row_length_moved_by_exactly_one_field_and_not_more():
    """Seven is now the whole row; eight is not.

    Both halves are needed and neither alone says it. The base refuses a
    seventh element, so accepting one is the change. An eighth is a schema
    this loader does not know, and dropping the extra silently would load
    the other seven wrong — an implementation that widened the ceiling to
    "however many you like" passes the test above and fails this one.
    """
    catalog = api.Catalog()
    catalog.load([("DSK-3", "Desk", DESK_PRICE, "furniture", True, None,
                   "draft")])
    assert catalog.get("DSK-3").state == "draft"
    with pytest.raises(ValueError):
        catalog.load([("DSK-4", "Desk", DESK_PRICE, "furniture", True, None,
                       "active", "extra")])
    # And too short is still refused at four, the existing floor.
    with pytest.raises(ValueError):
        catalog.load([("DSK-4", "Desk", DESK_PRICE)])
