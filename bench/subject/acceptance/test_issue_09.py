"""M9 — A customer's addresses decide the tax scopes.

`TaxTable.rate_for` has always summed a compound jurisdiction — a tuple of
scopes, one per taxing authority — and nothing in the pipeline ever handed
it one: `Order.tax_total` passed `cart.customer.jurisdiction`, a single
string, and `Customer.addresses` carried a `kind` that played no part in
resolution. This suite pins the connection: `Customer.tax_scopes()`,
`Customer.resolved_jurisdiction`, `Address.__post_init__`'s scope-shape
validation, `TaxCalculator.scopes_for`, `core.tax.render_jurisdiction`,
`Order.tax_scopes()`/`Order.tax_total`, and `tax_by_jurisdiction`'s key.

Every rate here is non-round and every figure is shown as the arithmetic
that produces it, so an implementation that charges the primary rate only,
or sorts the scopes, or rounds each scope's tax separately, fails rather
than coinciding with the right answer.
"""

import pytest

from billing import api

# 6.25% and 2.75%. Neither is round, and they sum to a rate whose tax on
# the fixture's desk lands on a half-cent tie (below), which is what makes
# "sum the rates, then round once" distinguishable from "round each scope's
# tax, then sum".
CA_RATE = "6.25"
OR_RATE = "2.75"
# Registered, and deliberately not reachable from any fixture below: a
# billing address in New York must contribute nothing, and an
# implementation that let it contribute would change a figure rather than
# raise, which is the failure a *registered* third rate can catch and an
# unregistered one cannot.
NY_RATE = "4.5"
# Never registered. A shipping address here must raise, not tax at the
# primary rate and not contribute a silent zero.
UNREGISTERED_SCOPE = "US-WA"

DESK = api.Money("137.50")


def table():
    rates = api.TaxTable()
    rates.load([("US-CA", CA_RATE), ("US-OR", OR_RATE), ("US-NY", NY_RATE)])
    return rates


def catalog():
    products = api.Catalog()
    products.add(api.Product("SKU-1", "Desk", DESK, "furniture"))
    # A non-taxable line, so "taxed under these scopes" is not confusable
    # with "every line is taxed": the gift card is in the cart, in the
    # subtotal, and owes nothing under any scope.
    products.add(
        api.Product("SKU-2", "Gift card", api.Money("40.00"), "vouchers", taxable=False)
    )
    return products


def order_for(customer, order_id="O-1", calculator=None):
    cart = api.Cart(catalog(), customer)
    cart.add("SKU-1")
    cart.add("SKU-2")
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=calculator or api.TaxCalculator(table()),
    )
    return api.Order(order_id, cart, pricing)


def plain_customer(customer_id="C-1"):
    """A customer with no addresses at all: the path this Task must leave
    charging exactly what it charged before."""
    return api.Customer(customer_id, "Ada", "US-CA")


def shipping_elsewhere(customer_id="C-2", scope="US-OR"):
    return api.Customer(
        customer_id,
        "Grace",
        "US-CA",
        addresses=(
            api.Address("US-CA", is_primary=True, kind=api.ADDRESS_KIND_BILLING),
            api.Address(scope, kind=api.ADDRESS_KIND_SHIPPING),
        ),
    )


def test_the_rendering_helper_is_exported_and_is_the_identity_on_one_scope():
    """A one-scope jurisdiction renders to the name it already had.

    This is the property every existing report key depends on: a compound
    of one scope renders the same as the bare string, so a customer taxed
    by one authority groups exactly where it always grouped. An
    implementation that always joined with the separator, or that wrapped a
    single scope in brackets, fails here.
    """
    assert api.render_jurisdiction("US-CA") == "US-CA"
    assert api.render_jurisdiction(("US-CA",)) == "US-CA"
    assert api.render_jurisdiction(["US-CA"]) == "US-CA"


def test_the_compound_rendering_joins_scopes_in_the_given_order_and_never_sorts():
    """The key reports the tuple it was handed, in that order.

    Sorting would render a key that does not correspond to the argument
    `rate_for` was actually given, and would make two different orders of
    the same scopes indistinguishable. `("US-OR", "US-CA")` is therefore a
    different key from `("US-CA", "US-OR")`, which is what kills a sorting
    implementation — every other assertion in this file uses a tuple that
    happens to be in sorted order already.
    """
    assert api.render_jurisdiction(("US-CA", "US-OR")) == "US-CA" + api.SCOPE_SEPARATOR + "US-OR"
    assert api.render_jurisdiction(("US-CA", "US-OR")) == "US-CA+US-OR"
    assert api.render_jurisdiction(("US-OR", "US-CA")) == "US-OR+US-CA"
    with pytest.raises(ValueError):
        api.render_jurisdiction(())


def test_a_customer_with_no_addresses_resolves_to_one_scope_equal_to_its_jurisdiction():
    """And resolves to a plain *string*, not a one-element tuple.

    Both halves are asserted because they are different contracts:
    `tax_scopes()` is always a tuple (the lookup argument), and
    `resolved_jurisdiction()` is the key a report groups on, which stays
    the string it has always been for this customer.
    """
    customer = plain_customer()
    assert customer.tax_scopes() == ("US-CA",)
    assert customer.resolved_jurisdiction() == "US-CA"
    assert not isinstance(customer.resolved_jurisdiction(), tuple)


def test_the_no_address_tax_total_is_the_single_rate_figure_unchanged():
    """The whole no-address path is pinned, figure by figure.

    137.50 * 6.25% = 8.59375, half to even over two places = 8.59. The
    subtotal and total are pinned with it, so an implementation that
    reached the compound path by taxing something else as well — or that
    changed the rounding by summing a one-element tuple differently —
    fails here rather than in a report three modules away.
    """
    order = order_for(plain_customer())
    assert order.subtotal == api.Money("177.50")       # 137.50 + 40.00
    assert order.tax_total == api.Money("8.59")        # 137.50 * 0.0625 = 8.59375
    assert order.total == api.Money("186.09")          # 177.50 - 0.00 + 8.59
    assert order.tax_scopes() == ("US-CA",)


def test_one_primary_address_yields_one_scope_and_the_same_tax_as_no_address():
    """An address is not a second authority. Filing the primary address a
    customer was already taxed under must not double its tax, which is what
    a `tax_scopes` that appended the primary to the `jurisdiction` field
    would do: `("US-CA", "US-CA")` is a duplicate `rate_for` rejects
    outright, so that implementation cannot price this order at all.
    """
    customer = api.Customer(
        "C-1", "Ada", "US-CA", addresses=(api.Address("US-CA", is_primary=True),)
    )
    assert customer.tax_scopes() == ("US-CA",)
    assert customer.resolved_jurisdiction() == "US-CA"
    assert order_for(customer).tax_total == order_for(plain_customer()).tax_total


def test_a_shipping_address_elsewhere_adds_a_second_scope_with_the_primary_first():
    """Two authorities, in a stated order: primary, then shipping.

    The addresses are declared shipping-first so declaration order cannot
    be what produces the right answer — `is_primary` is, and the primary
    address is not even the first element of the tuple it came from.
    Order is load-bearing rather than cosmetic: `rate_for` rejects a
    duplicate scope, so the tuple is what must be deduplicated, and a set
    would make the position of a rejected duplicate depend on hash order.
    """
    customer = api.Customer(
        "C-2",
        "Grace",
        "US-CA",
        addresses=(
            api.Address("US-OR", kind=api.ADDRESS_KIND_SHIPPING),
            api.Address("US-CA", is_primary=True, kind=api.ADDRESS_KIND_BILLING),
        ),
    )
    assert customer.tax_scopes() == ("US-CA", "US-OR")
    assert customer.tax_scopes() != ("US-OR", "US-CA")
    assert customer.resolved_jurisdiction() == ("US-CA", "US-OR")


def test_the_compound_tax_sums_both_rates_before_rounding():
    """One rounding over the summed rate, not one rounding per scope.

    137.50 * (6.25% + 2.75%) = 137.50 * 0.09 = 12.375, which half to even
    over two places is 12.38. Rounding each scope on its own gives
    8.59375 -> 8.59 plus 3.78125 -> 3.78, summing to 12.37 — one cent
    apart, and that cent is the assertion. It is also 3.79 more than the
    primary-rate-only figure of 8.59, so an implementation that ignores
    the shipping scope fails the same test.
    """
    order = order_for(shipping_elsewhere())
    assert order.tax_total == api.Money("12.38")
    assert order.tax_total != api.Money("12.37")       # 8.59 + 3.78, rounded twice
    assert order.tax_total != api.Money("8.59")        # the primary rate alone
    assert order.total == api.Money("189.88")          # 177.50 - 0.00 + 12.38


def test_a_billing_address_elsewhere_contributes_no_scope():
    """`kind` is consulted for exactly one value: `"shipping"`.

    A billing address is where the invoice goes, not where the goods
    arrive. New York's 4.5% is registered, so an implementation that
    collected every address's jurisdiction would price this order at
    137.50 * 10.75% = 14.78 and be caught by the figure rather than by a
    lookup failure. An `"other"` address, and an invented kind, are
    asserted in the same test because they are the same rule.
    """
    customer = api.Customer(
        "C-5",
        "Hopper",
        "US-CA",
        addresses=(
            api.Address("US-CA", is_primary=True),
            api.Address("US-NY", kind=api.ADDRESS_KIND_BILLING),
            api.Address("US-OR", kind=api.ADDRESS_KIND_OTHER),
            api.Address("US-NY", kind="warehouse"),
        ),
    )
    assert customer.tax_scopes() == ("US-CA",)
    assert order_for(customer).tax_total == api.Money("8.59")
    assert order_for(customer).tax_total != api.Money("14.78")


def test_two_shipping_addresses_in_one_jurisdiction_are_one_scope():
    """What counts as one scope: the jurisdiction, not the address.

    Two warehouses in Oregon are one taxing authority. This is not a
    nicety — `rate_for` raises `ValueError` on a repeated scope, so an
    implementation that appended per address cannot price the order at
    all, and the tax figure is asserted alongside the tuple to prove the
    rate was counted once rather than twice (12.38, not 137.50 * 11.75%).
    """
    customer = api.Customer(
        "C-6",
        "Grace",
        "US-CA",
        addresses=(
            api.Address("US-CA", is_primary=True),
            api.Address("US-OR", line1="dock 1", kind=api.ADDRESS_KIND_SHIPPING),
            api.Address("US-OR", line1="dock 2", kind=api.ADDRESS_KIND_SHIPPING),
        ),
    )
    assert customer.tax_scopes() == ("US-CA", "US-OR")
    assert order_for(customer).tax_total == api.Money("12.38")


def test_a_scope_that_is_both_primary_and_shipping_is_not_repeated():
    """The primary address may itself be the shipping address.

    Same rule as two shipping addresses in one state, different route into
    it: the scope is already present when the shipping pass reaches it. An
    implementation that deduplicated only within the shipping addresses
    yields `("US-CA", "US-CA")`, which `rate_for` refuses.
    """
    customer = api.Customer(
        "C-7",
        "Ada",
        "US-CA",
        addresses=(
            api.Address("US-CA", is_primary=True, kind=api.ADDRESS_KIND_SHIPPING),
        ),
    )
    assert customer.tax_scopes() == ("US-CA",)
    assert customer.resolved_jurisdiction() == "US-CA"
    assert order_for(customer).tax_total == api.Money("8.59")


def test_rate_for_rejects_a_repeated_scope_which_is_why_dedup_is_not_cosmetic():
    """The reason the three deduplication tests above are about pricing and
    not about tidiness. Asserted directly on `rate_for` so the consequence
    is visible rather than inferred: a duplicate scope is a `ValueError`,
    not a doubled rate.
    """
    rates = table()
    with pytest.raises(ValueError):
        rates.rate_for(("US-CA", "US-CA"), None)
    # And the compound path itself is unchanged in meaning: two distinct
    # scopes sum, 6.25 + 2.75 = 9.00.
    assert rates.rate_for(("US-CA", "US-OR"), None) == rates.rate_for(
        ("US-OR", "US-CA"), None
    )
    assert str(rates.rate_for(("US-CA", "US-OR"), "furniture")) == "9.00"


def test_resolved_jurisdiction_is_a_string_for_one_scope_and_a_tuple_for_many():
    """Two shapes from one reader, and both are contracts.

    A reader that always returned a tuple would have changed every
    existing grouping key for every customer on the day scopes arrived;
    one that always returned a string would have hidden the second
    authority from the report entirely. The types are asserted, not just
    the values.
    """
    single = plain_customer().resolved_jurisdiction()
    compound = shipping_elsewhere().resolved_jurisdiction()
    assert isinstance(single, str)
    assert isinstance(compound, tuple)
    assert compound == ("US-CA", "US-OR")
    # And each is accepted by the lookup it is a key for, at its own rate.
    assert str(table().rate_for(single, "furniture")) == "6.25"
    assert str(table().rate_for(compound, "furniture")) == "9.00"


def test_tax_by_jurisdiction_groups_single_scope_orders_under_the_plain_name():
    """The existing grouping is pinned unchanged, including the summing.

    Two single-scope orders share one key, `"US-CA"` — not `("US-CA",)`
    and not `"US-CA+"` — and their tax adds: 8.59 + 8.59 = 17.18. A key
    that silently became a tuple would change every report nobody edited,
    and would raise `TypeError` in the method's own `sorted` as soon as
    one customer filed a shipping address.
    """
    first = order_for(plain_customer("C-1"), "O-1")
    second = order_for(plain_customer("C-8"), "O-2")
    first.place()
    second.place()
    report = api.RevenueReport([first, second])
    assert report.tax_by_jurisdiction() == {"US-CA": api.Money("17.18")}
    assert list(report.tax_by_jurisdiction()) == ["US-CA"]


def test_tax_by_jurisdiction_groups_a_multi_scope_order_under_the_rendered_compound():
    """A compound key is one stable string, and a separate bucket.

    `"US-CA+US-OR"`, rendered by `core/tax.py` rather than assembled here,
    holds the 12.38 the two authorities charged; the single-scope order's
    8.59 stays under `"US-CA"`. Both halves matter: an implementation that
    grouped the compound order under its primary scope would report 20.97
    against `"US-CA"` and lose the distinction the Task exists to make.
    Keys are asserted in order, because the method sorts and a compound
    key that was a tuple could not be sorted against a string at all.
    """
    single = order_for(plain_customer("C-1"), "O-1")
    compound = order_for(shipping_elsewhere("C-2"), "O-2")
    single.place()
    compound.place()
    report = api.RevenueReport([single, compound])
    assert report.tax_by_jurisdiction() == {
        "US-CA": api.Money("8.59"),             # 137.50 * 6.25%
        "US-CA+US-OR": api.Money("12.38"),      # 137.50 * (6.25% + 2.75%)
    }
    assert list(report.tax_by_jurisdiction()) == ["US-CA", "US-CA+US-OR"]
    # The grouping never disagrees with the figures it groups.
    assert report.tax_collected == api.Money("20.97")


def test_an_unregistered_shipping_scope_raises_rather_than_taxing_at_the_primary_rate():
    """A typo in a shipping address is a configuration error, not a zero.

    `UnknownJurisdiction`, never a silent fall back to the primary scope's
    8.59 and never a zero contribution — `rate_for` already guarantees
    this for a compound jurisdiction, and reaching it from the pipeline
    must not weaken it. The scopes themselves still resolve: `scopes_for`
    reads no rate, so an order can report what it *would* be taxed under
    even when one of those authorities is unregistered.
    """
    customer = shipping_elsewhere("C-9", scope=UNREGISTERED_SCOPE)
    order = order_for(customer)
    assert order.tax_scopes() == ("US-CA", UNREGISTERED_SCOPE)
    with pytest.raises(api.UnknownJurisdiction):
        order.tax_total
    with pytest.raises(api.UnknownJurisdiction):
        order.total


def test_an_address_jurisdiction_must_be_a_single_scope_string():
    """`Address.__post_init__` validates the *shape* of a scope.

    Each rejection is a value that would otherwise survive to the rate
    lookup or to the rendering: a tuple, because one address sits in one
    authority's territory and a compound is assembled from several
    addresses; surrounding whitespace, because `" US-CA"` resolves to no
    registered rate while reading as `"US-CA"` to a human; and a scope
    containing the separator, because it makes the rendering ambiguous —
    `("US-CA+US", "OR")` and `("US-CA", "US-OR")` would render to the same
    report key while owing different rates.
    """
    with pytest.raises(TypeError):
        api.Address(("US-CA", "US-OR"))
    with pytest.raises(ValueError):
        api.Address("")
    with pytest.raises(ValueError):
        api.Address(" US-CA")
    with pytest.raises(ValueError):
        api.Address("US-CA ")
    with pytest.raises(ValueError):
        api.Address("US-CA" + api.SCOPE_SEPARATOR + "US-OR")
    # The valid case still constructs, and keeps its scope verbatim.
    assert api.Address("US-CA", line1="1 Main St").jurisdiction == "US-CA"


def test_order_tax_scopes_is_the_argument_tax_total_handed_the_calculator():
    """Not a re-derivation: the same value, observed at the call.

    A recording calculator captures every `jurisdiction` argument
    `tax_total` passes. Both lines — the taxable desk and the non-taxable
    gift card — are taxed under the same scope tuple, and that tuple is
    what `tax_scopes()` reports. An implementation that reported the
    compound from `tax_scopes()` while still passing
    `cart.customer.jurisdiction` to the calculator would produce the right
    tuple and the wrong tax, and is killed here by the recorded argument
    rather than by a figure.
    """
    class RecordingCalculator(api.TaxCalculator):
        def __init__(self, rates):
            super().__init__(rates)
            self.seen = []

        def tax_for(self, amount, jurisdiction, category, taxable=True):
            self.seen.append(jurisdiction)
            return super().tax_for(amount, jurisdiction, category, taxable=taxable)

    calculator = RecordingCalculator(table())
    order = order_for(shipping_elsewhere(), calculator=calculator)
    assert order.tax_total == api.Money("12.38")
    assert calculator.seen == [("US-CA", "US-OR"), ("US-CA", "US-OR")]
    assert calculator.seen[0] == order.tax_scopes()
    assert not isinstance(calculator.seen[0], str)
    # And the calculator's own reading of the customer is that same tuple,
    # always a tuple, for the single-scope case too.
    assert calculator.scopes_for(shipping_elsewhere()) == ("US-CA", "US-OR")
    assert calculator.scopes_for(plain_customer()) == ("US-CA",)


def test_a_tax_exempt_customer_reports_scopes_and_owes_zero():
    """Two separate facts: where the customer is, and what they owe.

    Exemption is answered by `_lines_for_tax` before any rate is read, so
    the tax is zero while the scopes stay exactly what the addresses say.
    An implementation that returned no scopes for an exempt customer would
    lose the record of which authorities the exemption was claimed
    against, and one that let exemption short-circuit the scopes would
    report the bare field instead of the compound.
    """
    customer = api.Customer(
        "C-3",
        "Alan",
        "US-CA",
        tax_exempt=True,
        addresses=(
            api.Address("US-CA", is_primary=True),
            api.Address("US-OR", kind=api.ADDRESS_KIND_SHIPPING),
        ),
    )
    order = order_for(customer)
    assert order.tax_scopes() == ("US-CA", "US-OR")
    assert order.tax_total == api.Money("0.00")
    assert order.total == api.Money("177.50")          # the subtotal, untaxed
    order.place()
    report = api.RevenueReport([order])
    assert report.tax_by_jurisdiction() == {"US-CA+US-OR": api.Money("0.00")}


def test_tax_scopes_on_a_draft_order_answers_without_placing_it():
    """A read-only reader, like every other figure on this class.

    A draft order reports the scopes it *would* be taxed under: no
    `InvalidTransition`, no status change, and no entry in the audit
    trail. An implementation that required a placed order would make the
    reporting of scopes conditional on a transition that has nothing to do
    with where the customer is.
    """
    order = order_for(shipping_elsewhere())
    assert order.status == "draft"
    assert order.tax_scopes() == ("US-CA", "US-OR")
    assert order.status == "draft"
    assert order.transitions() == ()


def test_scopes_for_refuses_an_empty_or_non_string_scope_tuple():
    """The guard at the one place a customer becomes a lookup argument.

    `rate_for` would fail on both of these several frames further down,
    naming a type rather than the customer. Checked here instead, so the
    failure names the boundary it crossed. A stub customer is used rather
    than a real one precisely because a real `Customer` cannot produce
    either shape — which is the point of `scopes_for` carrying the
    guarantee rather than trusting its caller.
    """
    class StubCustomer:
        def __init__(self, scopes):
            self.customer_id = "C-STUB"
            self._scopes = scopes

        def tax_scopes(self):
            return self._scopes

    calculator = api.TaxCalculator(table())
    with pytest.raises(ValueError):
        calculator.scopes_for(StubCustomer(()))
    with pytest.raises(TypeError):
        calculator.scopes_for(StubCustomer(("US-CA", 42)))
    assert calculator.scopes_for(StubCustomer(["US-CA", "US-OR"])) == (
        "US-CA",
        "US-OR",
    )
