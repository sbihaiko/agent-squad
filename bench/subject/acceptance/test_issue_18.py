"""R8 — The report's five folds become one.

`by_category`, `discount_attribution`, `by_period` and `tax_by_jurisdiction`
are the same seven lines four times, and `RevenueReport._sum` is the same
fold without a key. They consolidate into `RevenueReport._grouped(key_fn,
amount_fn)`, with `_sum` expressed as the keyless case and then *removed*,
backed by `Money.total_of(amounts, currency)` that both the report and
`Order.subtotal` fold through.

## How a suite tests a restructuring

A restructuring states a shape, and a suite can only see behaviour. Two
devices carry the whole file:

1. **The behavioural consequence.** One fold means one place where a sum can
   discover it is mixing currencies. Before, each grouping seeded each bucket
   from whatever amount landed in it first, so a report holding a USD order
   and a EUR order answered `by_category()` with a dict of dollars *and*
   euros — while `gross` on the same report raised `CurrencyMismatch`. After,
   every bucket is seeded in the report's own currency and all four groupings
   refuse the report exactly as `gross` always did. That is the one figure
   this Task changes, and `test_all_four_groupings_refuse_a_two_currency_
   report_at_one_place` is the test the Task exists for.

2. **Pinned figures, each with an anchor.** Everything else must come back
   identical, and "identical" passes on the base by definition. A test that
   passes on the base asserts nothing about this Task, so every pin below
   also asserts something only the consolidated tree satisfies: the pinned
   figure is re-derived through `Money.total_of` (absent on the base, so the
   test errors there), or the pin sits beside `_sum`'s absence. The pins are
   not decoration either way — they are what makes this a move rather than a
   rewrite, and `by_period`'s `KeyError`, `top_products`' rank order and
   `to_rows`' shape are each a thing a plausible consolidation breaks.

## Why these fixtures

`build_report()` gives every figure a **distinct** value: gross 690, net 585,
tax 50.40, three categories at 600/40/50, two discounts at 80/25, three
periods at 225/200/160, two jurisdictions at 36/14.40. Nothing ties, so a
fold that paired a key with the wrong bucket's total cannot pass, and no two
methods return the same number by coincidence. It also carries a wholly
cancelled order (O-4, excluded at construction) and an order with one
cancelled line (O-3, excluded by the active filter), because a consolidation
that folded over `order.cart.lines` instead of the active lines would
otherwise agree with every other assertion here.

The one tie is kept in its own fixture, `build_tie_report()`, so it cannot
blunt the pins above: two products at 100.00 each, added in the order
`SKU-B` then `SKU-A` across two orders, so sku-ascending, name-ascending and
first-seen orderings are three different answers and only the first is
`top_products`' documented tie-break.
"""

from datetime import date

import pytest

from billing import api

# Dates for `by_period`. December 2025 sorts before January 2026 only if the
# default bucket's `(year, month)` tuple is compared as a tuple of ints; a
# bucket keyed on the month alone, or on a string, gets this pair backwards.
DATES = {
    "O-1": date(2026, 1, 15),
    "O-2": date(2025, 12, 2),
    "O-3": date(2026, 2, 9),
    "O-4": date(2026, 3, 1),
}


def build_report():
    """Four orders, three placed and one cancelled, all in USD.

    O-1  Ada / US-CA  desk 200 + lamp 40      subtotal 240, fixed promo 40
    O-2  Bo  / US-NY  2 chairs 200 + card 50  subtotal 250, 10% loyalty
    O-3  Ada / US-CA  desk 200 (+ lamp cancelled)
    O-4  Bo  / US-NY  cancelled outright

    Two pricing contexts, so `discount_attribution` has two names to keep
    apart and `tax_by_jurisdiction` two jurisdictions. The gift card is
    non-taxable, so the tax figures are not a constant multiple of the
    revenue ones and a fold that swapped `tax_total` for `subtotal`
    somewhere fails rather than rounding to the same answer.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(api.Product("SKU-2", "Chair", api.Money("100.00"), "furniture"))
    catalog.add(api.Product("SKU-4", "Lamp", api.Money("40.00"), "lighting"))
    catalog.add(
        api.Product("SKU-9", "Gift card", api.Money("50.00"), "vouchers", taxable=False)
    )

    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    table.add(api.TaxRate("US-NY", "8"))
    calculator = api.TaxCalculator(table)

    ca_pricing = api.PricingContext(
        discount_engine=api.DiscountEngine(
            [api.FixedDiscount("promo", api.Money("40.00"))]
        ),
        tax_calculator=calculator,
    )
    ny_pricing = api.PricingContext(
        discount_engine=api.DiscountEngine([api.PercentageDiscount("loyalty", "10")]),
        tax_calculator=calculator,
    )
    ada = api.Customer("C-1", "Ada", "US-CA")
    bo = api.Customer("C-2", "Bo", "US-NY")

    first = api.Cart(catalog, ada)
    first.add("SKU-1")
    first.add("SKU-4")
    o1 = api.Order("O-1", first, ca_pricing)
    o1.place()

    second = api.Cart(catalog, bo)
    second.add("SKU-2", 2)
    second.add("SKU-9")
    o2 = api.Order("O-2", second, ny_pricing)
    o2.place()

    third = api.Cart(catalog, ada)
    third.add("SKU-1")
    third.add("SKU-4")
    o3 = api.Order("O-3", third, ca_pricing)
    o3.place()
    o3.cancel_line("SKU-4")

    fourth = api.Cart(catalog, bo)
    fourth.add("SKU-1")
    o4 = api.Order("O-4", fourth, ny_pricing)
    o4.place()
    o4.cancel()

    return api.RevenueReport([o1, o2, o3, o4]), [o1, o2, o3, o4]


def build_tie_report():
    """Two products tying on revenue at 100.00, seen SKU-B first.

    `SKU-B` ("Alpha chair", 2 x 50.00) is added to the first order and
    `SKU-A` ("Zebra desk", 1 x 100.00) to the second, so iteration meets B
    before A while the sku tie-break demands A first and a name-ascending
    tie-break would also say "Alpha" first. Three candidate orderings, one
    correct answer.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-A", "Zebra desk", api.Money("100.00"), "furniture"))
    catalog.add(api.Product("SKU-B", "Alpha chair", api.Money("50.00"), "furniture"))
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(table),
    )
    ada = api.Customer("C-1", "Ada", "US-CA")

    cheap = api.Cart(catalog, ada)
    cheap.add("SKU-B", 2)
    first = api.Order("T-1", cheap, pricing)
    first.place()

    dear = api.Cart(catalog, ada)
    dear.add("SKU-A")
    second = api.Order("T-2", dear, pricing)
    second.place()

    return api.RevenueReport([first, second])


def build_two_currency_orders():
    """One USD order and one EUR order, in different categories, different
    periods, different jurisdictions and under differently-named discounts.

    Every dimension is kept distinct deliberately: two amounts that disagree
    on currency *inside one bucket* already raised before this Task, because
    the bucket's accumulator was seeded from the first of them. The mixture
    this Task is about is the one *across* buckets, which the four groupings
    used to report as a dict in two currencies.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(
        api.Product("SKU-E", "Monitor", api.Money("300.00", "EUR"), "electronics")
    )
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    table.add(api.TaxRate("EU-DE", "19"))
    calculator = api.TaxCalculator(table)

    usd_pricing = api.PricingContext(
        discount_engine=api.DiscountEngine(
            [api.FixedDiscount("promo-us", api.Money("40.00"))]
        ),
        tax_calculator=calculator,
    )
    eur_pricing = api.PricingContext(
        discount_engine=api.DiscountEngine(
            [api.FixedDiscount("promo-eu", api.Money("30.00", "EUR"))]
        ),
        tax_calculator=calculator,
    )
    ada = api.Customer("C-1", "Ada", "US-CA")
    cleo = api.Customer("C-3", "Cleo", "EU-DE")

    dollars = api.Cart(catalog, ada)
    dollars.add("SKU-1")
    usd_order = api.Order("M-1", dollars, usd_pricing)
    usd_order.place()

    euros = api.Cart(catalog, cleo)
    euros.add("SKU-E")
    eur_order = api.Order("M-2", euros, eur_pricing)
    eur_order.place()

    return usd_order, eur_order


# --- the new fold primitive ----------------------------------------------


def test_total_of_is_exported_on_money_and_sums_in_the_currency_it_was_given():
    """The fold is a `Money` classmethod, reachable through the facade.

    Asserted on a sequence whose members are not all equal and whose total
    is not a round number, so a `total_of` that returned its first argument,
    its last, or a count would not coincide with the answer.
    """
    amounts = [api.Money("10.01"), api.Money("0.40"), api.Money("7.59")]
    assert api.Money.total_of(amounts, "USD") == api.Money("18.00")
    assert isinstance(api.Money.total_of(amounts, "USD"), api.Money)
    # A generator, not only a list: the report hands it comprehensions.
    assert api.Money.total_of((a for a in amounts), "USD") == api.Money("18.00")
    # And it does not round: a sub-cent sum survives as a sub-cent sum,
    # because the callers have already decided their own rounding policy.
    assert api.Money.total_of(
        [api.Money("0.005"), api.Money("0.004")], "USD"
    ) == api.Money("0.009")


def test_total_of_nothing_is_a_zero_in_the_currency_that_was_asked_for():
    """The empty case is why `currency` is a parameter at all.

    An empty sequence has no operand to read a currency off, and the
    package's hardcoded `Money.ZERO` is USD — so a fold that inferred its
    currency would answer a report of no euro orders in dollars, and raise
    on the next addition far from here. `!=` on the USD zero is the part
    that makes the claim falsifiable.
    """
    assert api.Money.total_of([], "EUR") == api.Money("0.00", "EUR")
    assert api.Money.total_of([], "EUR") != api.Money("0.00", "USD")
    # A zero-exponent currency, to pin that nothing here assumes two places.
    assert api.Money.total_of([], "JPY") == api.Money("0", "JPY")
    assert api.Money.total_of([], "USD") == api.Money("0.00")


def test_total_of_refuses_a_mixed_sum_through_moneys_own_currency_check():
    """One place in the package now decides what mixing currencies means.

    The second assertion is the discriminating one: the two amounts agree
    with *each other* and disagree only with the currency the caller named.
    A fold that seeded its accumulator from the first amount it was handed
    would answer 20.00 EUR here and never notice, which is exactly the
    seeding the four groupings used to do.
    """
    with pytest.raises(api.CurrencyMismatch):
        api.Money.total_of([api.Money("10.00"), api.Money("10.00", "EUR")], "USD")
    with pytest.raises(api.CurrencyMismatch):
        api.Money.total_of(
            [api.Money("10.00", "EUR"), api.Money("10.00", "EUR")], "USD"
        )


# --- the removal ---------------------------------------------------------


def test_the_keyless_fold_is_gone_rather_than_kept_as_a_one_line_alias():
    """`RevenueReport._sum` is removed, not renamed-and-forwarded.

    Two spellings of one fold is how the copies drifted in the first place,
    so an alias left behind would leave the Task's point undone. Nothing
    outside the module ever referenced it — the facade exports no such name
    and the three whole-order figures below are its only callers, all of
    which still answer — so the removal costs no caller anything.
    """
    report, _ = build_report()
    assert not hasattr(api.RevenueReport, "_sum")
    assert not hasattr(report, "_sum")
    assert "_sum" not in api.__all__
    assert not hasattr(api, "_sum")
    assert report.gross == api.Money("690.00")
    assert report.net == api.Money("585.00")
    assert report.tax_collected == api.Money("50.40")


# --- the figures that must not move -------------------------------------


def test_the_three_whole_order_figures_are_identical_and_fold_through_total_of():
    """Pinned method by method, and re-derived through the new primitive.

    Re-deriving is not a tautology here: the right-hand side lists the
    three surviving orders explicitly, so a fold that kept the cancelled
    O-4 in (or dropped O-3's surviving line with its cancelled one) breaks
    the equality even though it might still produce *a* number.
    """
    report, orders = build_report()
    placed = [order for order in orders if order.status != "cancelled"]
    assert report.gross == api.Money("690.00")
    assert report.net == api.Money("585.00")
    assert report.tax_collected == api.Money("50.40")
    assert report.gross == api.Money.total_of(
        [order.subtotal for order in placed], "USD"
    )
    assert report.net == api.Money.total_of(
        [order.subtotal - order.discount_total for order in placed], "USD"
    )
    assert report.tax_collected == api.Money.total_of(
        [order.tax_total for order in placed], "USD"
    )


def test_an_empty_report_still_answers_a_usd_zero_and_four_empty_groupings():
    """The case the old `_sum` handled with `Money.ZERO`.

    There is no first order to read a currency from, so the consolidated
    fold falls back to the module's default currency — which has to be USD
    to the cent, because that is what the base suite already pins. Stated
    as an equality against `total_of([], "USD")` so the fallback and the
    primitive are pinned to the same answer rather than separately.
    """
    report = api.RevenueReport([])
    assert report.gross == api.Money("0.00")
    assert report.net == api.Money("0.00")
    assert report.tax_collected == api.Money("0.00")
    assert report.gross == api.Money.total_of([], "USD")
    assert report.by_category() == {}
    assert report.discount_attribution() == {}
    assert report.by_period({}) == {}
    assert report.tax_by_jurisdiction() == {}


def test_by_category_is_identical_on_a_multi_order_multi_category_fixture():
    """Three categories, three distinct totals, keys in ascending order.

    `furniture` spans two orders and two skus, which is what makes this a
    fold rather than a per-order lookup; `lighting` is 40 rather than 80
    because O-3's lamp line is cancelled.
    """
    report, _ = build_report()
    assert report.by_category() == {
        "furniture": api.Money("600.00"),
        "lighting": api.Money("40.00"),
        "vouchers": api.Money("50.00"),
    }
    assert report.by_category()["furniture"] == api.Money.total_of(
        [api.Money("200.00"), api.Money("200.00"), api.Money("200.00")], "USD"
    )


def test_discount_attribution_is_identical_and_still_agrees_with_discount_total():
    """Two named discounts, and the attribution still sums to the pipeline.

    `promo` is 80 because the same fixed discount is charged on two orders,
    so the name is a bucket across orders and not a per-order label. The
    second assertion is the invariant the module's own docstring claims:
    the attribution can say where a discount went, never disagree about how
    much was discounted.
    """
    report, orders = build_report()
    assert report.discount_attribution() == {
        "loyalty": api.Money("25.00"),
        "promo": api.Money("80.00"),
    }
    placed = [order for order in orders if order.status != "cancelled"]
    assert api.Money.total_of(
        list(report.discount_attribution().values()), "USD"
    ) == api.Money.total_of([order.discount_total for order in placed], "USD")


def test_by_period_is_identical_and_the_default_bucket_still_sorts_chronologically():
    """2025-12 first, then 2026-01, then 2026-02 — and the dates are not
    supplied in that order, so the ordering is the fold's and not the
    caller's.
    """
    report, _ = build_report()
    grouped = report.by_period(DATES)
    assert grouped == {
        (2025, 12): api.Money("225.00"),
        (2026, 1): api.Money("200.00"),
        (2026, 2): api.Money("160.00"),
    }
    assert list(grouped) == [(2025, 12), (2026, 1), (2026, 2)]
    assert api.Money.total_of(list(grouped.values()), "USD") == report.net


def test_all_four_groupings_still_sort_by_key_ascending():
    """One sort, in one place, and every grouping still uses it.

    Sorting moved into the shared fold, which is precisely where a
    consolidation can lose it for three of four methods while the fourth
    still looks right. Each key list is checked against its own `sorted`,
    so the assertion does not restate the expected order a second time and
    cannot agree with a wrong one.
    """
    report, _ = build_report()
    for grouped in (
        report.by_category(),
        report.discount_attribution(),
        report.by_period(DATES),
        report.tax_by_jurisdiction(),
    ):
        assert len(grouped) > 1
        assert list(grouped) == sorted(grouped)
    assert api.Money.total_of(
        list(report.tax_by_jurisdiction().values()), "USD"
    ) == report.tax_collected


def test_a_custom_bucket_returning_an_arbitrary_hashable_sorts_by_its_own_ordering():
    """Quarters as plain strings: `Q1` before `Q4`, lexicographically.

    Not a special case for anything — the fold sorts with Python's own
    `sorted`, so a caller's hashable brings its own ordering and a scheme
    that wants `Q10` after `Q9` has to return something that compares that
    way. Pinned because a consolidation that normalized keys (stringified
    them, or coerced them to tuples to sort safely) would pass the default
    bucket's test and silently change this one.
    """
    report, _ = build_report()
    quarterly = report.by_period(
        DATES, bucket=lambda moment: f"Q{(moment.month - 1) // 3 + 1}"
    )
    assert quarterly == {"Q1": api.Money("360.00"), "Q4": api.Money("225.00")}
    assert list(quarterly) == ["Q1", "Q4"]
    assert api.Money.total_of(list(quarterly.values()), "USD") == report.net


def test_a_bucket_returning_none_groups_under_none_rather_than_dropping_the_order():
    """A line the report cannot key is still a line it must account for.

    `None` is a hashable like any other here, and the fold must not treat it
    as "no bucket": dropping those orders would make
    `sum(by_period(...).values())` disagree with `net` for a reason no
    caller could see from the outside. The same `None` passed as the
    `bucket` *argument* still means "use the default monthly bucket", which
    the second half pins — the two readings sit one call apart and a
    consolidation that conflated them would answer one of these two wrong.
    """
    report, _ = build_report()
    unkeyed = report.by_period(DATES, bucket=lambda moment: None)
    assert unkeyed == {None: api.Money("585.00")}
    assert api.Money.total_of(list(unkeyed.values()), "USD") == report.net
    assert report.by_period(DATES, bucket=None) == report.by_period(DATES)
    assert list(report.by_period(DATES, bucket=None)) == [
        (2025, 12),
        (2026, 1),
        (2026, 2),
    ]


def test_by_period_still_raises_key_error_naming_the_order_for_a_missing_date():
    """The date lookup stayed inside the key function, so the miss still
    surfaces as the `KeyError` it always did, naming the order it is about.

    A consolidation that pre-built its keys with `dates.get(...)` would
    turn this into a `None` bucket instead — which is why the test above
    exists as well: `None` is a legitimate key here, so the two behaviours
    are indistinguishable unless both are pinned.
    """
    report, _ = build_report()
    with pytest.raises(KeyError) as raised:
        report.by_period({"O-1": date(2026, 1, 15)})
    assert "O-2" in str(raised.value)
    assert report.by_period(DATES)[(2025, 12)] == api.Money.total_of(
        [api.Money("225.00")], "USD"
    )


def test_tax_by_jurisdiction_is_identical_and_reads_the_jurisdiction_off_the_order():
    """Two jurisdictions, and the cancelled order's US-NY tax is not in the
    US-NY figure: 14.40 is O-2's alone.

    The jurisdiction is still read off `order.cart.customer` rather than
    supplied, so it is the same jurisdiction `tax_total` taxed with.
    """
    report, orders = build_report()
    assert report.tax_by_jurisdiction() == {
        "US-CA": api.Money("36.00"),
        "US-NY": api.Money("14.40"),
    }
    by_order = {order.order_id: order for order in orders}
    assert report.tax_by_jurisdiction()["US-CA"] == api.Money.total_of(
        [by_order["O-1"].tax_total, by_order["O-3"].tax_total], "USD"
    )


def test_top_products_is_not_folded_into_a_dict_and_keeps_its_sku_tie_break():
    """"The report's folds" could be read to include `top_products`. It does
    not: rank order is the whole point of this one, and a dict keyed by sku
    would make every caller re-sort what this method already ranked.

    So the shape is pinned — a tuple of four-field rows — along with the
    two-pass tie-break, on a fixture where sku-ascending, name-ascending
    and first-seen are three different answers.
    """
    ranked = build_tie_report().top_products()
    assert isinstance(ranked, tuple)
    assert ranked == (
        ("SKU-A", "Zebra desk", api.Money("100.00"), 1),
        ("SKU-B", "Alpha chair", api.Money("100.00"), 2),
    )
    assert build_tie_report().top_products(1) == (
        ("SKU-A", "Zebra desk", api.Money("100.00"), 1),
    )
    report, _ = build_report()
    assert [row[0] for row in report.top_products()] == [
        "SKU-1",
        "SKU-2",
        "SKU-9",
        "SKU-4",
    ]
    assert report.top_products()[0][2] == api.Money.total_of(
        [api.Money("200.00"), api.Money("200.00")], "USD"
    )


def test_to_rows_is_untouched_and_still_sorted_by_order_id():
    """Five fields per row, one row per surviving order, sorted by id.

    `to_rows` reads the orders' own figures and was never one of the folds,
    so it is pinned to catch a consolidation that routed it through the
    shared helper and changed its shape on the way.
    """
    report, _ = build_report()
    assert report.to_rows() == (
        (
            "O-1",
            api.Money("240.00"),
            api.Money("40.00"),
            api.Money("20.00"),
            api.Money("220.00"),
        ),
        (
            "O-2",
            api.Money("250.00"),
            api.Money("25.00"),
            api.Money("14.40"),
            api.Money("239.40"),
        ),
        (
            "O-3",
            api.Money("200.00"),
            api.Money("40.00"),
            api.Money("16.00"),
            api.Money("176.00"),
        ),
    )
    assert api.Money.total_of([row[1] for row in report.to_rows()], "USD") == report.gross


def test_a_cancelled_order_and_a_cancelled_line_are_still_excluded():
    """Both exclusions, at their two different places.

    The wholly cancelled O-4 never enters the report (excluded at
    construction, so it is absent from `to_rows` as well as from every
    figure), and O-3's cancelled lamp is excluded by the active-lines
    filter while O-3 itself stays. Cancelling the lamp a second time still
    refuses, so the active set is the order's own record and not something
    the report rebuilt.
    """
    report, orders = build_report()
    by_order = {order.order_id: order for order in orders}
    assert [row[0] for row in report.to_rows()] == ["O-1", "O-2", "O-3"]
    # O-4 is a US-NY order whose tax_total is 14.40 in its own right, so a
    # US-NY figure of 28.80 is exactly what failing to exclude it looks like.
    assert by_order["O-4"].tax_total == api.Money("14.40")
    assert report.tax_by_jurisdiction()["US-NY"] == api.Money.total_of(
        [by_order["O-2"].tax_total], "USD"
    )
    assert by_order["O-3"].cancelled_skus() == frozenset({"SKU-4"})
    assert by_order["O-3"].subtotal == api.Money.total_of([api.Money("200.00")], "USD")
    assert report.by_category()["lighting"] == api.Money("40.00")
    with pytest.raises(api.InvalidTransition):
        by_order["O-3"].cancel_line("SKU-4")


def test_order_subtotal_folds_through_total_of_and_breakdown_is_unchanged():
    """`Order.subtotal` is the other caller of the new primitive.

    Its two pre-existing edge cases are preserved deliberately and pinned
    here: the currency comes from the cart's *first* line, and an order
    whose every line is cancelled reports a zero in that currency rather
    than the USD zero a `Money.ZERO`-seeded fold would have produced. The
    four `breakdown()` keys are pinned alongside, since `subtotal` feeds
    three of them.
    """
    report, orders = build_report()
    by_order = {order.order_id: order for order in orders}
    order = by_order["O-1"]
    assert order.breakdown() == {
        "subtotal": api.Money("240.00"),
        "discount": api.Money("40.00"),
        "tax": api.Money("20.00"),
        "total": api.Money("220.00"),
    }
    assert order.subtotal == api.Money.total_of(
        [line.line_total for line in order.cart.lines], "USD"
    )
    assert report.gross == api.Money("690.00")

    _, eur_order = build_two_currency_orders()
    assert eur_order.subtotal == api.Money("300.00", "EUR")
    eur_order.cancel_line("SKU-E")
    assert eur_order.subtotal == api.Money("0.00", "EUR")
    assert eur_order.subtotal != api.Money("0.00", "USD")


# --- the behavioural consequence ----------------------------------------


def test_all_four_groupings_refuse_a_two_currency_report_at_one_place():
    """The figure this Task changes, and the only one.

    Five folds meant five seeding rules. `gross` seeded from the first
    order's currency and therefore raised on a two-currency report; the four
    groupings seeded each bucket from the first amount to land in it, and so
    answered with a dict holding dollars under one key and euros under
    another — a mapping that cannot be summed, compared or serialized
    without each caller rediscovering the mixture for itself, one figure at
    a time. One fold means one seed, the report's own currency, and
    `Money.__add__`'s check refuses the rest.

    Asserted through each of the four groupings, and in both list orders, so
    the refusal is a property of the report rather than of whichever order
    happens to be first. `gross` is asserted too, as the figure that always
    behaved this way and is now the rule rather than the exception.
    """
    usd_order, eur_order = build_two_currency_orders()
    for orders in ([usd_order, eur_order], [eur_order, usd_order]):
        report = api.RevenueReport(orders)
        with pytest.raises(api.CurrencyMismatch):
            report.by_category()
        with pytest.raises(api.CurrencyMismatch):
            report.discount_attribution()
        with pytest.raises(api.CurrencyMismatch):
            report.by_period({"M-1": date(2026, 1, 1), "M-2": date(2026, 2, 1)})
        with pytest.raises(api.CurrencyMismatch):
            report.tax_by_jurisdiction()
        with pytest.raises(api.CurrencyMismatch):
            report.gross


def test_a_single_currency_report_in_euros_is_unaffected_by_that_refusal():
    """The refusal is about mixing, not about being non-USD.

    A report of euro orders alone folds in euros throughout — which is the
    half of the claim a fold that hardcoded USD anywhere would fail, and the
    reason `total_of` takes a currency rather than assuming one. The last
    assertion re-derives one of the figures through the primitive, since
    everything above this line is also true of the four separate folds and
    would otherwise assert nothing about this Task.
    """
    _, eur_order = build_two_currency_orders()
    report = api.RevenueReport([eur_order])
    assert report.gross == api.Money("300.00", "EUR")
    assert report.net == api.Money("270.00", "EUR")
    assert report.tax_collected == api.Money("51.30", "EUR")
    assert report.by_category() == {"electronics": api.Money("300.00", "EUR")}
    assert report.discount_attribution() == {"promo-eu": api.Money("30.00", "EUR")}
    assert report.tax_by_jurisdiction() == {"EU-DE": api.Money("51.30", "EUR")}
    assert report.by_period({"M-2": date(2026, 2, 1)}) == {
        (2026, 2): api.Money("270.00", "EUR")
    }
    assert report.gross == api.Money.total_of([eur_order.subtotal], "EUR")
