"""R2 — "Active" has one definition.

`Order.active_lines()` and `Order.active_quantity()` become public, the two
private originals and `reporting/reports.py`'s two module-level copies go,
and the line-filtering loop moves down to `Cart.lines_excluding(skus)`.

A restructuring states a shape and a suite can only see behaviour, so every
test below is either a **new surface** the consolidation exposes or an **old
figure pinned against the new surface**. A pin alone would pass on the base
tree and assert nothing about this Task; pinning `subtotal` *and* asserting
that the same number is the sum of `active_lines()`' line totals is what
makes the pin a statement about one definition rather than two. The two
exceptions are the removal assertions, which are structural on purpose and
say so.

Every figure here is pinned to a literal read off the base tree, not
recomputed in the test: a test that re-derives the expected number the same
way the implementation does cannot catch the implementation being wrong.

The fixture is built to discriminate, not to be tidy:

- Three lines with distinct line totals (200.00, 150.00, 120.00) and
  distinct quantities (1, 2, 3), so "excluded the wrong sku", "counted
  lines instead of units" and "read the cart instead of the order" all give
  different answers. No two subsets of {200, 150, 120} sum alike, so a
  subtotal identifies exactly which lines were included.
- One non-taxable line (the gift card), so the tax figures move when the
  taxable set changes and stay put when it does not.
- A tax rate of 8.25% on a discounted base of 315.00, which is 25.9875 —
  a figure that distinguishes rounding from truncation rather than landing
  on a clean cent.
- A percentage discount gated on `quantity >= 5` against a cart of 6 units.
  Cancelling the 2-unit line drops the active quantity to 4 and the
  discount to zero, so an implementation that feeds the engine
  `cart.quantity` prices the order 47.00 cheaper. Unconditional 10% would
  have left the two implementations indistinguishable.
"""

import sys

import pytest

from billing import api

# The cart, before anything is cancelled.
SUBTOTAL = api.Money("470.00")       # 200.00 + 2 x 75.00 + 3 x 40.00
DISCOUNT = api.Money("47.00")        # 10% of 470.00, gated on quantity >= 5
TAXABLE_BASE = api.Money("315.00")   # the two furniture lines, less their share
TAX = api.Money("25.99")             # 8.25% of 315.00 is 25.9875
TOTAL = api.Money("448.99")

# After `cancel_line("SKU-2")`: 6 units becomes 4, so the bulk rule stops
# matching and the discount becomes zero. That is today's behaviour and the
# consolidation must not change it.
SUBTOTAL_AFTER = api.Money("320.00")
DISCOUNT_AFTER = api.Money("0.00")
TAXABLE_BASE_AFTER = api.Money("200.00")
TAX_AFTER = api.Money("16.50")
TOTAL_AFTER = api.Money("336.50")


def build_catalog():
    """Four products, only three of which ever reach a cart.

    `SKU-4` exists so a test can exclude a sku that the catalog knows and
    the cart does not, which is a different case from excluding a sku
    nothing has ever heard of.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(api.Product("SKU-2", "Chair", api.Money("75.00"), "furniture"))
    catalog.add(
        api.Product("SKU-3", "Gift card", api.Money("40.00"), "vouchers", taxable=False)
    )
    catalog.add(api.Product("SKU-4", "Lamp", api.Money("60.00"), "furniture"))
    return catalog


def build_cart(tax_exempt=False):
    catalog = build_catalog()
    customer = api.Customer("C-1", "Ada", "US-CA", tax_exempt=tax_exempt)
    cart = api.Cart(catalog, customer)
    cart.add("SKU-1", 1)
    cart.add("SKU-2", 2)
    cart.add("SKU-3", 3)
    return cart


def bulk_percentage():
    """10% off, but only once the order is charging for 5 units or more.

    The gate is the point: it reads `context["quantity"]`, which `Order`
    fills from its active-line unit count. A discount with no rule would
    make every quantity figure in this file unobservable.
    """
    return api.PercentageDiscount(
        "spring", "10", rule=api.Rule("bulk", lambda ctx: ctx["quantity"] >= 5)
    )


def build_order(order_id="O-1", discounts=None, tax_exempt=False):
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "8.25"))
    if discounts is None:
        discounts = [bulk_percentage()]
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine(list(discounts)),
        tax_calculator=api.TaxCalculator(table),
    )
    return api.Order(order_id, build_cart(tax_exempt=tax_exempt), pricing)


def skus_and_quantities(lines):
    return [(line.product.sku, line.quantity) for line in lines]


def test_active_lines_excludes_every_cancelled_sku_and_nothing_else():
    """The filtered view, read directly, is the behavioural gain.

    Asserted as `(sku, quantity)` pairs rather than as a count: a count of
    2 would also be produced by dropping the wrong line, and the quantity
    is what distinguishes `SKU-2` (2 units) from `SKU-3` (3 units). Order
    is asserted too, because `Cart.lines` is sku-sorted and a filter that
    rebuilt the list from a set would be free to scramble it.
    """
    order = build_order()
    assert skus_and_quantities(order.active_lines()) == [
        ("SKU-1", 1),
        ("SKU-2", 2),
        ("SKU-3", 3),
    ]
    order.cancel_line("SKU-2")
    assert skus_and_quantities(order.active_lines()) == [("SKU-1", 1), ("SKU-3", 3)]
    order.cancel_line("SKU-1")
    assert skus_and_quantities(order.active_lines()) == [("SKU-3", 3)]


def test_the_private_active_filters_are_gone_from_the_order():
    """One of the two structural assertions in this file, and deliberate.

    The Task is a removal, and a removal that leaves the originals in
    place as dead code satisfies every behavioural criterion here while
    leaving two definitions of "active" in the package — which is the one
    thing this Task exists to fix. No arrangement of inputs and outputs
    can see that, so it is asserted directly.

    `_active_lines_with_discount_shares` is *not* asserted gone: it is the
    discount-allocation helper, it is nobody's duplicate, and this Task
    does not touch its existence.
    """
    order = build_order()
    assert not hasattr(order, "_active_lines")
    assert not hasattr(order, "_active_quantity")
    assert callable(order.active_lines)
    assert callable(order.active_quantity)
    assert hasattr(order, "_active_lines_with_discount_shares")


def test_the_reporting_module_no_longer_carries_its_own_active_filters():
    """The second structural assertion, for the other half of the removal.

    Reached through `RevenueReport.__module__` rather than by importing a
    module path, because this suite is injected into the Subject's own
    acceptance directory and sees the package only through the facade. The
    reporting module is not exported and has no behaviour of its own to
    interrogate here: its two helpers are module-private functions whose
    results are, by construction, identical to the order's own. Their
    absence is the only observable difference, so this is how it is
    observed.
    """
    reporting = sys.modules[api.RevenueReport.__module__]
    assert not hasattr(reporting, "_active_lines")
    assert not hasattr(reporting, "_active_quantity")


def test_active_quantity_is_the_summed_quantity_of_the_active_lines():
    """Units, not lines, and the order's units rather than the cart's.

    The fixture makes all three readings distinct: 6 active units, 3
    active lines, and — once the 2-unit line is cancelled — 4 active units
    against a cart that still reports 6. A reading of 2 would mean lines
    were counted; a reading of 6 after the cancellation would mean the
    cart was read instead of the order.
    """
    order = build_order()
    assert order.active_quantity() == 6
    assert order.active_quantity() == sum(
        line.quantity for line in order.active_lines()
    )
    order.cancel_line("SKU-2")
    assert order.active_quantity() == 4
    assert order.cart.quantity == 6
    assert order.active_quantity() == sum(
        line.quantity for line in order.active_lines()
    )


def test_the_order_filter_is_the_cart_filter_applied_to_the_cancelled_skus():
    """The two new surfaces are one mechanism, not two that agree.

    This is the assertion that the filter actually moved: if `Order` kept
    its own loop and `Cart.lines_excluding` were a second, parallel
    implementation, the two could still agree on this fixture — so the
    equality is asserted on three different cancellation sets, including
    the empty one and a two-sku one.
    """
    order = build_order()
    cart = order.cart
    assert order.active_lines() == cart.lines_excluding(order.cancelled_skus())
    order.cancel_line("SKU-3")
    assert order.active_lines() == cart.lines_excluding(order.cancelled_skus())
    assert order.active_lines() == cart.lines_excluding(("SKU-3",))
    order.cancel_line("SKU-1")
    assert order.active_lines() == cart.lines_excluding(order.cancelled_skus())
    assert order.active_lines() == cart.lines_excluding({"SKU-1", "SKU-3"})


def test_excluding_nothing_gives_back_exactly_the_cart_lines():
    """`lines_excluding(())` equals `lines`, as a value and as a type.

    The equality is asserted against `cart.lines` itself, so it also pins
    the return type to a `list`: a tuple of the same `LineItem`s compares
    unequal to a list, and `snapshot()` is already this package's way of
    freezing a point-in-time tuple. Three empty iterables are passed,
    because an implementation that special-cased a falsy argument and one
    that filtered against an empty set are the same answer here and should
    stay that way.
    """
    cart = build_cart()
    assert cart.lines_excluding(()) == cart.lines
    assert cart.lines_excluding([]) == cart.lines
    assert cart.lines_excluding(set()) == cart.lines
    assert len(cart.lines_excluding(())) == 3


def test_excluding_lines_does_not_mutate_the_cart():
    """A filtered read is a read.

    `Cart` keeps its state in sku-keyed maps and rebuilds every `LineItem`
    on access, so an implementation that filtered `_quantities` in place
    would pass the previous test and then quietly lose the line. Asserted
    on the cart's lines, its unit count, its subtotal, and on a second
    `lines_excluding` call with a different sku, which must not see the
    first call's exclusion.
    """
    cart = build_cart()
    before = cart.lines
    assert len(cart.lines_excluding(("SKU-2",))) == 2
    assert cart.lines == before
    assert cart.quantity == 6
    assert cart.subtotal == SUBTOTAL
    assert skus_and_quantities(cart.lines_excluding(("SKU-1",))) == [
        ("SKU-2", 2),
        ("SKU-3", 3),
    ]


def test_excluding_a_sku_the_cart_never_held_is_not_an_error():
    """Two cases, both answers rather than exceptions.

    `SKU-4` is in the catalog and not in the cart; `SKU-404` is in neither.
    Both return every line. `Order` cancels by sku, and "this cart has no
    line for that sku" and "this cart's line for that sku is excluded" are
    the same result from the caller's side, so raising would make every
    caller pre-check a condition that changes no answer. This is also the
    one place the method deliberately does *not* validate against the
    catalog, unlike `set_note` and `override_price`: those record state
    against a sku for later, where this one selects over lines that
    already exist.
    """
    cart = build_cart()
    assert cart.lines_excluding(("SKU-4",)) == cart.lines
    assert cart.lines_excluding(("SKU-404",)) == cart.lines
    assert cart.lines_excluding(("SKU-4", "SKU-404", "SKU-2")) == cart.lines_excluding(
        ("SKU-2",)
    )


def test_excluding_refuses_a_bare_sku_string_rather_than_iterating_it():
    """A single sku is not an iterable of skus, and silently reads as five.

    `lines_excluding("SKU-1")` would iterate `S`, `K`, `U`, `-`, `1`, match
    no line, and return the whole cart as though nothing had been
    excluded — a wrong answer wearing the shape of a right one, which is
    the class of surprise `Cart.add` already refuses for a fractional
    quantity. The message has to point at the fix, so it is asserted to
    name the tuple form rather than merely to exist.
    """
    cart = build_cart()
    with pytest.raises(TypeError) as caught:
        cart.lines_excluding("SKU-1")
    assert "SKU-1" in str(caught.value)
    assert cart.lines_excluding(("SKU-1",)) != cart.lines


def test_a_filtered_line_still_carries_its_note_and_its_price_override():
    """The filter selects lines; it does not rebuild them.

    An implementation that materialized `LineItem(product, quantity)` from
    the sku-keyed quantities, instead of filtering `self.lines`, would drop
    the note and the override — and every total in this file would still
    be right, because nothing else in the fixture overrides a price. So
    the override is checked through both new surfaces and through the
    order's `subtotal`, which must charge the overridden price.
    """
    cart = build_cart()
    cart.set_note("SKU-1", "gift wrap requested")
    cart.override_price("SKU-1", api.Money("180.00"), "manager approved")
    kept = cart.lines_excluding(("SKU-3",))
    assert [line.note for line in kept] == ["gift wrap requested", None]
    assert kept[0].override_price == api.Money("180.00")
    assert kept[0].overridden_by == "manager approved"
    assert kept[0].line_total == api.Money("180.00")
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "8.25"))
    order = api.Order(
        "O-1",
        cart,
        api.PricingContext(
            discount_engine=api.DiscountEngine([]),
            tax_calculator=api.TaxCalculator(table),
        ),
    )
    assert order.active_lines()[0].override_price == api.Money("180.00")
    assert order.subtotal == api.Money("450.00")


def test_every_pipeline_figure_is_unchanged_and_agrees_with_the_active_view():
    """The five pipeline figures, pinned to the base tree's own numbers and
    tied to the promoted filter.

    The pins alone would pass on the base tree; what makes them this
    Task's invariants is that `subtotal` is also asserted to be the sum of
    `active_lines()`' line totals, before and after the cancellation. That
    is the claim the consolidation makes: the number the pipeline reports
    and the lines a caller can now read are the same filter, not two that
    agree today.
    """
    order = build_order()
    assert order.subtotal == SUBTOTAL
    assert order.discount_total == DISCOUNT
    assert order.taxable_base == TAXABLE_BASE
    assert order.tax_total == TAX
    assert order.total == TOTAL
    assert sum(
        (line.line_total for line in order.active_lines()), api.Money("0.00")
    ) == SUBTOTAL

    order.cancel_line("SKU-2")
    assert order.subtotal == SUBTOTAL_AFTER
    assert order.discount_total == DISCOUNT_AFTER
    assert order.taxable_base == TAXABLE_BASE_AFTER
    assert order.tax_total == TAX_AFTER
    assert order.total == TOTAL_AFTER
    assert sum(
        (line.line_total for line in order.active_lines()), api.Money("0.00")
    ) == SUBTOTAL_AFTER
    assert order.breakdown() == {
        "subtotal": SUBTOTAL_AFTER,
        "discount": DISCOUNT_AFTER,
        "tax": TAX_AFTER,
        "total": TOTAL_AFTER,
    }


def test_the_discount_gate_reads_the_active_quantity_and_not_the_carts():
    """The one figure where the two candidate filters disagree by money.

    6 cart units clear the `quantity >= 5` gate and 4 active units do not,
    so an order that fed the engine `cart.quantity` would keep charging
    47.00 less after the cancellation. Both the discount and the
    quantities it is derived from are asserted, so a failure says which of
    the two went wrong.
    """
    order = build_order()
    assert order.discount_total == DISCOUNT
    order.cancel_line("SKU-2")
    assert order.active_quantity() == 4
    assert order.cart.quantity == 6
    assert order.discount_total == api.Money("0.00")
    order_without_cancellation = build_order()
    assert order_without_cancellation.discount_total == DISCOUNT


def test_by_category_is_unchanged_with_one_cancelled_line():
    """The figure the removed `reports._active_lines` existed for.

    Pinned in both directions: the cancelled line's 150.00 leaves
    `furniture`, and the untouched `vouchers` line stays at 120.00, so a
    filter that excluded too much fails as loudly as one that excluded too
    little. The same dict is then rebuilt from `order.active_lines()`,
    which is what ties the pin to this Task.
    """
    clean = api.RevenueReport([build_order()])
    assert clean.by_category() == {
        "furniture": api.Money("350.00"),
        "vouchers": api.Money("120.00"),
    }

    order = build_order()
    order.cancel_line("SKU-2")
    report = api.RevenueReport([order])
    assert report.by_category() == {
        "furniture": api.Money("200.00"),
        "vouchers": api.Money("120.00"),
    }
    from_active_view = {}
    for line in order.active_lines():
        category = line.product.category
        from_active_view[category] = (
            from_active_view.get(category, api.Money("0.00")) + line.line_total
        )
    assert report.by_category() == from_active_view


def test_discount_attribution_is_unchanged_with_one_cancelled_line():
    """The figure the removed `reports._active_quantity` existed for.

    The gated discount makes this sharp: attribution on the clean order
    names `spring` at 47.00, and on the cancelled order it names nothing
    at all, because the engine's own rule no longer matches 4 units. A
    report that built its context from `cart.quantity` would still report
    `spring`. The sum is then checked against `order.discount_total`,
    which is the invariant the module's docstring claims and which can
    only hold while both sides count the same units.
    """
    clean_order = build_order()
    assert api.RevenueReport([clean_order]).discount_attribution() == {
        "spring": api.Money("47.00")
    }

    order = build_order()
    order.cancel_line("SKU-2")
    report = api.RevenueReport([order])
    assert report.discount_attribution() == {}
    assert sum(
        report.discount_attribution().values(), api.Money("0.00")
    ) == order.discount_total
    assert order.active_quantity() == 4


def test_top_products_is_unchanged_with_one_cancelled_line():
    """Ranking, revenue and units, pinned as whole rows.

    A cancelled line neither contributes revenue nor occupies a rank slot,
    and the surviving rows keep both their figures and their order. Rows
    are asserted whole rather than by sku, because a correct set of skus
    with a cancelled line's quantity still folded in is the error a
    sku-only assertion would miss.
    """
    clean = api.RevenueReport([build_order()])
    assert clean.top_products() == (
        ("SKU-1", "Desk", api.Money("200.00"), 1),
        ("SKU-2", "Chair", api.Money("150.00"), 2),
        ("SKU-3", "Gift card", api.Money("120.00"), 3),
    )

    order = build_order()
    order.cancel_line("SKU-2")
    report = api.RevenueReport([order])
    assert report.top_products() == (
        ("SKU-1", "Desk", api.Money("200.00"), 1),
        ("SKU-3", "Gift card", api.Money("120.00"), 3),
    )
    assert [row[0] for row in report.top_products()] == [
        line.product.sku for line in order.active_lines()
    ]


def test_a_wholly_cancelled_order_is_excluded_while_its_lines_stay_active():
    """Two filters, one of which this Task does not touch.

    A cancelled *order* is dropped at report construction; a cancelled
    *line* is dropped by the active-line view. The second has been
    consolidated and the first has not, so the cancelled order's own
    `active_lines()` still reports all three lines — `cancel()` is not a
    line cancellation and must not be folded into the filter that just
    moved. The report's figures are pinned to the surviving order alone.
    """
    cancelled = build_order("O-0")
    cancelled.place()
    cancelled.cancel()
    surviving = build_order("O-1")
    report = api.RevenueReport([cancelled, surviving])

    assert report.gross == SUBTOTAL
    assert report.net == SUBTOTAL - DISCOUNT
    assert report.tax_collected == TAX
    assert report.to_rows() == (("O-1", SUBTOTAL, DISCOUNT, TAX, TOTAL),)
    assert report.by_category() == {
        "furniture": api.Money("350.00"),
        "vouchers": api.Money("120.00"),
    }

    assert len(cancelled.active_lines()) == 3
    assert cancelled.active_quantity() == 6
    assert cancelled.cancelled_skus() == frozenset()


def test_a_tax_exempt_customer_yields_no_tax_while_its_lines_stay_active():
    """Exemption is not cancellation, and the two must not merge.

    `_lines_for_tax` answers exemption once and yields nothing for an
    exempt customer, which is observable as a zero `taxable_base` and a
    zero `tax_total`. The part this Task could break is the other half:
    the lines are still active, still in the subtotal, and still carry the
    discount, so `active_lines()` must report all three and the total must
    be the discounted subtotal with no tax at all.
    """
    order = build_order(tax_exempt=True)
    assert order.taxable_base == api.Money("0.00")
    assert order.tax_total == api.Money("0.00")
    assert order.subtotal == SUBTOTAL
    assert order.discount_total == DISCOUNT
    assert order.total == api.Money("423.00")
    assert len(order.active_lines()) == 3
    assert order.active_quantity() == 6

    order.cancel_line("SKU-1")
    assert order.subtotal == api.Money("270.00")
    assert order.tax_total == api.Money("0.00")
    assert skus_and_quantities(order.active_lines()) == [("SKU-2", 2), ("SKU-3", 3)]


def test_refund_line_reads_the_stashed_total_drop_and_not_the_filter():
    """A `CreditNote` that differs from the line's own total, on purpose.

    Under a flat 30.00 discount, cancelling the 200.00 desk pushes the
    whole discount onto the two survivors, so the total drops by 216.04 —
    not 200.00, and not 200.00 plus a line's tax. That gap is what makes
    this test discriminate: a refund recomputed from the active-line view
    after the fact would land on a different number, and a fixture with no
    discount would have hidden the difference entirely.

    The figures are base-tree literals; the active view is asserted
    alongside them so the test belongs to this Task.
    """
    flat = api.FixedDiscount("flat", api.Money("30.00"))
    order = build_order(discounts=[flat])
    order.place()
    assert order.total == api.Money("467.04")

    order.cancel_line("SKU-1")
    assert order.total == api.Money("251.00")
    assert skus_and_quantities(order.active_lines()) == [("SKU-2", 2), ("SKU-3", 3)]

    note = order.refund_line("SKU-1", reason="damaged in transit")
    assert note.amount == api.Money("216.04")
    assert note.amount != api.Money("200.00")
    assert note.sku == "SKU-1"
    assert note.reason == "damaged in transit"
    assert order.total == api.Money("251.00")


def test_reprice_carries_over_no_cancellations():
    """A repriced order starts with every line active.

    `reprice` shares the cart and carries over nothing else, so the new
    order's active view is the whole cart and its quantity clears the bulk
    gate again — which is visible as the discount coming back. The
    original keeps its cancellation, so this also pins that the two orders
    do not share the set the filter reads.
    """
    order = build_order()
    order.cancel_line("SKU-2")
    assert order.active_quantity() == 4

    repriced = order.reprice("O-2", order.pricing)
    assert repriced.cancelled_skus() == frozenset()
    assert skus_and_quantities(repriced.active_lines()) == [
        ("SKU-1", 1),
        ("SKU-2", 2),
        ("SKU-3", 3),
    ]
    assert repriced.active_quantity() == 6
    assert repriced.subtotal == SUBTOTAL
    assert repriced.discount_total == DISCOUNT
    assert repriced.total == TOTAL

    assert order.cancelled_skus() == frozenset({"SKU-2"})
    assert skus_and_quantities(order.active_lines()) == [("SKU-1", 1), ("SKU-3", 3)]
