"""K1 — A restock can be requested.

`RestockOrder(sku, quantity, requested_on)` in `catalog/products.py`, with
`Catalog.request_restock` registering one and `Catalog.restock_requests`
reading them back; `Cart.backorder` recording what a cart could not fill;
and `RevenueReport.unfilled_restocks` reporting the requests nothing has
closed.

The fixture below is built so that three plausible orderings give three
different answers. Its three requests are registered in an order that is
neither date order nor sku order, two of them share a date, and one sku
carries two requests on different dates. So an implementation that returns
insertion order, one that sorts by sku alone, and one that collapses a
second request for a sku into the first all fail here rather than passing by
coincidence — the `C11` failure mode, where "count of orders" admitted two
opposite implementations.

Quantities are 7, 4, 2 and 5 rather than 1 for the same reason: a quantity
of one cannot distinguish "recorded the quantity" from "recorded that
something was wanted".

Every date arrives from the test. The package reads no clock, and a
`requested_on` this file did not choose would make the ordering assertions
depend on when the suite ran.
"""

from datetime import date
from decimal import Decimal

import pytest

from billing import api

# Two requests share 2026-03-05, so the `(requested_on, sku)` tie-break is
# exercised; SKU-2's earlier date is registered last, so insertion order and
# date order disagree; SKU-1 carries a second, later request, so "a second
# request is a correction" and "a second request sums into the first" both
# fail.
MARCH = date(2026, 3, 5)
JANUARY = date(2026, 1, 9)
APRIL = date(2026, 4, 1)


def build_catalog():
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(api.Product("SKU-2", "Lamp", api.Money("40.00"), "furniture"))
    catalog.add(api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers"))
    return catalog


def stocked_catalog():
    """A catalog with three requests, registered out of both orders."""
    catalog = build_catalog()
    catalog.request_restock("SKU-3", 4, MARCH)
    catalog.request_restock("SKU-1", 7, MARCH)
    catalog.request_restock("SKU-2", 2, JANUARY)
    return catalog


def build_cart(catalog=None):
    catalog = catalog or build_catalog()
    return api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))


def build_report(catalog):
    """A report over one real placed order from `catalog`.

    `unfilled_restocks` must not depend on the orders, and the only way to
    assert that is to read the same catalog through a report that has
    orders and through one that has none.
    """
    cart = build_cart(catalog)
    cart.add("SKU-1", 2)
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(table),
    )
    order = api.Order("O-1", cart, pricing)
    order.place()
    return api.RevenueReport([order])


def test_the_restock_record_is_exported_and_carries_its_three_fields():
    """The record crosses the facade, because a caller holding a request
    (to compare it against a later receipt, say) needs its type by name.
    """
    request = api.RestockOrder("SKU-1", 7, MARCH)
    assert request.sku == "SKU-1"
    assert request.quantity == 7
    assert request.requested_on == MARCH


def test_a_restock_order_is_frozen():
    """A request is a record of what was asked for on a date. Editing one
    in place would rewrite a history `restock_requests` has already handed
    out, which is why `Product` is frozen too.
    """
    request = api.RestockOrder("SKU-1", 7, MARCH)
    with pytest.raises(AttributeError):
        request.quantity = 99
    with pytest.raises(AttributeError):
        request.requested_on = APRIL


def test_a_restock_order_validates_its_fields_at_construction():
    """Not at first use, matching `Product`.

    A request validated only when a report reads it would surface the bad
    value far from the call that made it, and the report could not say
    which caller was at fault.
    """
    with pytest.raises((TypeError, ValueError)):
        api.RestockOrder("", 7, MARCH)
    with pytest.raises((TypeError, ValueError)):
        api.RestockOrder(None, 7, MARCH)
    with pytest.raises((TypeError, ValueError)):
        api.RestockOrder("SKU-1", 0, MARCH)
    with pytest.raises((TypeError, ValueError)):
        api.RestockOrder("SKU-1", 7, None)


def test_a_restock_for_an_unknown_sku_raises_unknown_product():
    """The same refusal `Catalog.get` gives, and for a reason beyond
    consistency: a request against a sku this catalog never carried can
    never be received against anything.
    """
    catalog = build_catalog()
    with pytest.raises(api.UnknownProduct):
        catalog.request_restock("SKU-404", 7, MARCH)
    # The refusal recorded nothing on the way out.
    assert catalog.restock_requests() == []


def test_a_non_positive_quantity_is_not_a_restock():
    """Zero units ordered is the absence of an order, and a negative one is
    a return, which this record cannot express.

    The exception type is deliberately left open between `TypeError` and
    `ValueError`: `InvalidQuantity` lives in `sales/cart.py` and `catalog/`
    may import only `core/`, so the catalog cannot raise the
    name `Cart.add` raises. Only the rejection is the contract.
    """
    catalog = build_catalog()
    for quantity in (0, -3):
        with pytest.raises((TypeError, ValueError)):
            catalog.request_restock("SKU-1", quantity, MARCH)
    assert catalog.restock_requests() == []
    # One unit is the smallest restock there is, and it is a restock. The
    # boundary is stated because a guard written `<= 1` refuses the
    # commonest reorder of all and every other criterion here still holds.
    catalog.request_restock("SKU-1", 1, MARCH)
    assert [(r.sku, r.quantity) for r in catalog.restock_requests()] == [
        ("SKU-1", 1)]


def test_a_fractional_quantity_is_refused_rather_than_rounded():
    """1.5 of a desk has no meaning, exactly as in `Cart.add`. Both a
    `float` and an exact `Decimal` are refused: the objection is to a
    fraction of a physical unit, not to binary floating point.
    """
    catalog = build_catalog()
    for quantity in (1.5, Decimal("1.5"), 2.0):
        with pytest.raises((TypeError, ValueError)):
            catalog.request_restock("SKU-1", quantity, MARCH)
    assert catalog.restock_requests() == []


def test_a_bool_quantity_is_refused_because_true_would_mean_one():
    """`bool` is an `int` subclass, so a truthy flag passed where a count
    belongs would be accepted as a restock of one unit and never reported
    as a mistake. `Product.taxable` and `Cart.add` both refuse the same
    trap from the other direction.
    """
    catalog = build_catalog()
    for quantity in (True, False):
        with pytest.raises((TypeError, ValueError)):
            catalog.request_restock("SKU-1", quantity, MARCH)
    assert catalog.restock_requests() == []


def test_the_request_date_is_required_and_never_read_from_a_clock():
    """Matching `Catalog.record_price` and `Coupon.redeem`.

    Two claims, because they fail differently: omitting the argument must
    not be accepted at all (no `requested_on=None` default standing in for
    "now"), and an explicit `None` must be refused rather than stored.
    A clock-read default would silently date a request on the day the code
    ran, and every lead time computed from it would be wrong by however
    long the request sat unrecorded.
    """
    catalog = build_catalog()
    with pytest.raises(TypeError):
        catalog.request_restock("SKU-1", 7)
    with pytest.raises(TypeError):
        catalog.request_restock("SKU-1", 7, None)
    assert catalog.restock_requests() == []


def test_requests_read_back_ordered_by_date_then_sku():
    """Oldest first, ties broken by sku ascending.

    The fixture registers SKU-3, then SKU-1, then SKU-2, with SKU-2 holding
    the earliest date and SKU-1 and SKU-3 sharing a later one. So insertion
    order gives (3, 1, 2), sku order gives (1, 2, 3), and only
    `(requested_on, sku)` gives the sequence asserted here.
    """
    requests = stocked_catalog().restock_requests()
    assert [(r.sku, r.quantity, r.requested_on) for r in requests] == [
        ("SKU-2", 2, JANUARY),
        ("SKU-1", 7, MARCH),
        ("SKU-3", 4, MARCH),
    ]


def test_two_requests_for_one_sku_both_persist():
    """A second request is not a correction of the first, unlike a
    re-recorded price.

    A price is a fact about a sku on a date, of which there is one; a
    request is an event, of which there can be many. The two quantities are
    7 and 5 and the assertion names both, so an implementation that kept
    only the last (5) or summed them (12) fails rather than merely
    reporting a different shape.
    """
    catalog = stocked_catalog()
    catalog.request_restock("SKU-1", 5, APRIL)
    requests = catalog.restock_requests()
    assert len(requests) == 4
    sku_1 = [(r.quantity, r.requested_on) for r in requests if r.sku == "SKU-1"]
    assert sku_1 == [(7, MARCH), (5, APRIL)]


def test_restock_requests_returns_a_copy():
    """Mutating the result cannot corrupt the catalog.

    The mutation is attempted and its absence tolerated: an implementation
    returning an immutable sequence is a copy by construction and satisfies
    the contract, while one returning its own internal list does not. What
    is asserted either way is that the catalog still holds its three
    requests afterwards.
    """
    catalog = stocked_catalog()
    requests = catalog.restock_requests()
    try:
        requests.clear()
    except AttributeError:
        pass
    else:
        assert requests == []
    assert len(catalog.restock_requests()) == 3


def test_a_backorder_is_recorded_against_the_sku_and_read_back():
    """A recorded value with no reader is not a contract: without
    `backorders()`, a cart that validated a backorder and discarded it
    would be indistinguishable from one that kept it.
    """
    cart = build_cart()
    cart.backorder("SKU-2", 5)
    assert cart.backorders() == {"SKU-2": 5}


def test_a_backorder_records_against_a_sku_the_catalog_knows():
    """And refuses otherwise, with `UnknownProduct` — the refusal `add`,
    `set_note` and `override_price` already give. A backorder for a sku no
    catalog carries could never be filled by restocking it.
    """
    cart = build_cart()
    with pytest.raises(api.UnknownProduct):
        cart.backorder("SKU-404", 5)
    assert cart.backorders() == {}


def test_a_backorder_quantity_is_validated_as_a_cart_line_is():
    """`InvalidQuantity`, the name `Cart.add` already raises — this one is
    in `sales/cart.py`, so unlike the catalog's refusal there is no
    layering reason to reach for a different exception.

    Zero, a fraction and `True` are all refused, and nothing is recorded.
    """
    cart = build_cart()
    for quantity in (0, -2, 1.5, True):
        with pytest.raises(api.InvalidQuantity):
            cart.backorder("SKU-2", quantity)
    assert cart.backorders() == {}
    # And one unit is accepted, for the reason the catalog's own boundary
    # is asserted: a guard written `<= 1` turns the commonest shortfall of
    # all into an error.
    cart.backorder("SKU-2", 1)
    assert cart.backorders() == {"SKU-2": 1}


def test_a_backorder_changes_neither_the_lines_nor_the_subtotal():
    """A backorder is a request, not a line.

    Nothing about it has been priced, so putting it into `.lines` would
    charge a customer for units that do not exist. The subtotal is checked
    against a figure, not against itself, so an implementation that added
    the backorder as a line at the catalog price (400.00 + 5 * 40.00 =
    600.00) fails here.
    """
    cart = build_cart()
    cart.add("SKU-1", 2)
    cart.backorder("SKU-2", 5)
    assert cart.subtotal == api.Money("400.00")
    assert [(line.product.sku, line.quantity) for line in cart.lines] == [("SKU-1", 2)]
    assert cart.quantity == 2
    assert cart.backorders() == {"SKU-2": 5}


def test_a_backorder_for_a_sku_already_in_the_cart_leaves_that_line_alone():
    """The line is what the customer is buying now; the backorder is what
    they could not get. A cart holding 2 units and wanting 5 more is
    ordinary, so the two figures are held apart rather than summed into a
    line of 7 that nobody can be charged for.
    """
    cart = build_cart()
    cart.add("SKU-1", 2)
    cart.backorder("SKU-1", 5)
    assert [(line.product.sku, line.quantity) for line in cart.lines] == [("SKU-1", 2)]
    assert cart.subtotal == api.Money("400.00")
    assert cart.backorders() == {"SKU-1": 5}


def test_unfilled_restocks_reports_every_request_sorted_by_sku():
    """Sorted by sku, which is not the order the catalog reads them in.

    The same three requests come back from `Catalog.restock_requests()` as
    (SKU-2, SKU-1, SKU-3) by date and from the report as (SKU-1, SKU-2,
    SKU-3) by sku, so an implementation that passes the catalog's ordering
    straight through fails. Nothing closes a request at this point, so
    every request is unfilled.

    The same answer comes back from a report over a placed order and from a
    report over none: this figure reads the catalog it is handed, and the
    report's orders play no part in it.
    """
    catalog = stocked_catalog()
    reported = build_report(catalog).unfilled_restocks(catalog)
    assert [(r.sku, r.quantity) for r in reported] == [
        ("SKU-1", 7),
        ("SKU-2", 2),
        ("SKU-3", 4),
    ]
    assert api.RevenueReport([]).unfilled_restocks(catalog) == reported


def test_unfilled_restocks_is_empty_for_a_catalog_with_no_requests():
    """No shortfall is an answer, not an error — and the empty result is
    asserted to be empty rather than falsy, so a report that raised or
    returned `None` does not pass here.
    """
    reported = api.RevenueReport([]).unfilled_restocks(build_catalog())
    assert reported is not None
    assert len(reported) == 0
