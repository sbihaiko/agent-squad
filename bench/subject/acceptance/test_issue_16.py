"""R6 — Tax unpacking has one arity checker.

`TaxTable._parse_row` and `TaxCalculator._parse_line` were the same
function twice: branch on `tuple`/`list`, switch on length, raise a
hand-written complaint naming the accepted arities. They are replaced by
one checker driven by shape descriptions, and the consolidation is spent
upward: `Order.tax_lines()` exposes the `(line, taxed share)` pairs the
pipeline actually ran over, and `RevenueReport.tax_lines()` aggregates
them.

## How a suite sees a restructuring

It cannot see that two definitions became one. Two consequences are
visible and both are asserted here:

1. **The four refusals now read the same way.** Two malformed rate rows
   (wrong length, wrong type) and two malformed tax lines were phrased by
   two independent authors; they are now one sentence built from the
   accepted shapes. The exact strings are pinned, because a suite that
   only asserted the exception *types* would pass on a tree where nothing
   had been consolidated at all — those types were already ValueError and
   TypeError before.
2. **The pairs behind a tax figure are readable.** That is new surface,
   so it fails on the base for the ordinary reason.

Every unchanged figure is pinned too, but never in a test of its own: a
test that passes on the base asserts nothing this Task adds. So each
"still parses as today" claim is asserted in the same test as the new
consequence it travels with, which is why the refusal messages appear
beside the figures they did not change.

## Fixture choices

The rate is 8.5%, not 10%: a 10% rate makes a share and its tax differ by
a factor a reader can check by eye, and makes `per_line` and `per_order`
rounding agree on this cart, which would leave the two modes
indistinguishable.

The discount is a flat `Money("40.00")` over lines of 200.00, 99.99 and
50.00, not a percentage: a percentage discount allocates to shares that a
plain per-line "subtract 10%" also produces, so the allocation could not
be distinguished from a prorate. Flat 40.00 allocates to 22.86/11.43/5.71
— a largest-remainder split that does not fall out of any per-line
arithmetic — and the shares it leaves (177.14/88.56/44.29) sum to exactly
`subtotal - discount`.

The rounding-drift fixture is ten lines of 0.10 at 8.5%, chosen because
0.0085 is a bankers-rounding boundary: per line it is 0.01 ten times for
0.10, and summed first it is 0.085, which rounds *down* to 0.08. Any
other figure leaves the two modes agreeing and the pin worthless.
"""

import re

import pytest

from billing import api

# The one skeleton, as a pattern per call site. The fixed words are written
# out: one format for all four refusals is what this Task owns, and a plain
# substring assertion would pass on the base tree, whose two hand-written
# sentences already carry "rate[, category]" and "category[, taxable]". The
# field list of the mapping shape is not written out as a fixed string: the
# skeleton renders the shapes `_unpack` is given, and issues 02 and 07 add
# `inclusive` and `rounding` to `TaxRate` by the mapping row shape only. So
# the tuple shape stays `(jurisdiction, rate[, category])`, which a 4-element
# row is refused against, and the mapping shape admits those two later keys
# after `category`. A tree that lists other fields there fails.
RATE_ROW = (
    r"a tax rate row must be "
    r"\(jurisdiction, rate\[, category\]\) "
    r"or \{jurisdiction, rate\[, category(\[, inclusive(\[, rounding)?)?\]*\]?\}; got "
)
TAX_LINE = (
    r"a tax line must be "
    r"\(amount, jurisdiction, category\[, taxable\]\); got "
)


def refusal(skeleton, value, arrived):
    """True when a refusal is that skeleton rendered for a row of this type."""
    return re.fullmatch(skeleton + re.escape(arrived), str(value)) is not None


def rate_table():
    """A table registering 8.5% in US-CA and a zero-rated category.

    Loaded through `add`, not `load`: the tests about `load` want a table
    they can watch being loaded, and the pricing tests want one whose
    contents owe nothing to the row parsing under test.
    """
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "8.5"))
    table.add(api.TaxRate("US-CA", "0", "groceries"))
    return table


def catalog():
    products = api.Catalog()
    products.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    products.add(api.Product("SKU-2", "Chair", api.Money("99.99"), "furniture"))
    products.add(
        api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers", taxable=False)
    )
    products.add(api.Product("SKU-4", "Rug", api.Money("120.00"), "furniture"))
    return products


def build_order(order_id="O-1", skus=("SKU-1", "SKU-2", "SKU-3"),
                discounts=(api.FixedDiscount("promo", api.Money("40.00")),),
                tax_exempt=False):
    products = catalog()
    customer = api.Customer("C-1", "Ada", "US-CA", tax_exempt=tax_exempt)
    cart = api.Cart(products, customer)
    for sku in skus:
        cart.add(sku)
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine(list(discounts)),
        tax_calculator=api.TaxCalculator(rate_table()),
    )
    return api.Order(order_id, cart, pricing)


def test_both_readers_exist_and_report_the_pipelines_own_pairs():
    """The two new readers, and the shape they return.

    A pair, not a sku and not a bare amount: a share is uninterpretable
    without the line it was allocated to, and `LineItem` is already on the
    facade so nothing new is exposed to expose it.
    """
    order = build_order()
    pairs = order.tax_lines()
    assert [line.product.sku for line, _share in pairs] == ["SKU-1", "SKU-2", "SKU-3"]
    assert [share for _line, share in pairs] == [
        api.Money("177.14"),
        api.Money("88.56"),
        api.Money("44.29"),
    ]
    # A tuple, not a generator: read twice, answer twice. A generator here
    # would satisfy every other assertion in this file and then yield
    # nothing to the second caller of a figure it is meant to explain.
    assert order.tax_lines() == pairs
    assert isinstance(pairs, tuple)


def test_a_rate_row_parses_at_two_and_three_elements_and_is_refused_otherwise():
    """The arities `load` took before, and the new wording of a refusal.

    The figures are the unchanged half: a 2-element row registers a
    jurisdiction default and a 3-element row a category override, which is
    what `load` did before this Task. The messages are the changed half,
    and they are what makes this test fail on a tree with two parsers: the
    old one said "a tax rate row tuple must have 2 or 3 elements".
    """
    table = api.TaxTable()
    table.load([("US-CA", "8.5"), ("US-CA", "0", "groceries")])
    calculator = api.TaxCalculator(table)
    assert calculator.tax_for(api.Money("100.00"), "US-CA", "furniture") == api.Money(
        "8.50"
    )
    assert calculator.tax_for(api.Money("100.00"), "US-CA", "groceries") == api.Money(
        "0.00"
    )
    with pytest.raises(ValueError) as short:
        api.TaxTable().load([("US-CA",)])
    assert refusal(RATE_ROW, short.value, "a 1-element tuple")
    with pytest.raises(ValueError) as long:
        api.TaxTable().load([("US-CA", "8.5", "groceries", True)])
    assert refusal(RATE_ROW, long.value, "a 4-element tuple")


def test_a_list_rate_row_is_as_good_as_a_tuple_and_reports_its_own_type():
    """A list row has always been accepted, and a refusal names what it got.

    The type in the tail is not decoration: "a 1-element list" versus
    "a 1-element tuple" is the evidence that the checker describes the row
    in front of it rather than printing a fixed noun, and a consolidation
    that hard-coded "tuple" would read wrong for half its callers.
    """
    table = api.TaxTable()
    table.load([["US-CA", "8.5"]])
    assert api.TaxCalculator(table).tax_for(
        api.Money("100.00"), "US-CA", "furniture"
    ) == api.Money("8.50")
    with pytest.raises(ValueError) as refused:
        api.TaxTable().load([["US-CA"]])
    assert refusal(RATE_ROW, refused.value, "a 1-element list")


def test_a_tax_line_parses_at_three_and_four_elements_and_is_refused_otherwise():
    """`tax_for_lines`' arities, and its new wording.

    Same division as the rate row: the two figures are what the method
    already computed, the two messages are the consolidation's visible
    consequence. The old wording was "a tax line must be a 3- or 4-tuple
    of (amount, jurisdiction, category[, taxable])" — the same facts in a
    different sentence, which is exactly what one checker removes.
    """
    calculator = api.TaxCalculator(rate_table())
    assert calculator.tax_for_lines(
        [
            (api.Money("100.00"), "US-CA", "furniture"),
            (api.Money("100.00"), "US-CA", "furniture", False),
        ]
    ) == [api.Money("8.50"), api.Money("0.00")]
    with pytest.raises(ValueError) as short:
        calculator.tax_for_lines([(api.Money("100.00"), "US-CA")])
    assert refusal(TAX_LINE, short.value, "a 2-element tuple")
    with pytest.raises(ValueError) as long:
        calculator.tax_for_lines(
            [(api.Money("100.00"), "US-CA", "furniture", True, "extra")]
        )
    assert refusal(TAX_LINE, long.value, "a 5-element tuple")


def test_the_four_refusals_share_one_format():
    """The consolidation's whole visible point, asserted as a format.

    Not four strings again — this asserts the property they now have in
    common: one subject, one " must be ", one "; got ", and a body that
    lists every shape that would have been accepted. On the base tree the
    four messages have four different skeletons: one says "must have 2 or
    3 elements", one says "unsupported tax rate row type:", one says "must
    be a 3- or 4-tuple of", one says "must be a tuple or list, got".
    """
    calculator = api.TaxCalculator(rate_table())
    messages = []
    for raising in (
        lambda: api.TaxTable().load([("US-CA",)]),
        lambda: api.TaxTable().load([object()]),
        lambda: calculator.tax_for_lines([(api.Money("1.00"), "US-CA")]),
        lambda: calculator.tax_for_lines([{"amount": api.Money("1.00")}]),
    ):
        with pytest.raises((TypeError, ValueError)) as refused:
            raising()
        messages.append(str(refused.value))
    for message in messages:
        subject, _, rest = message.partition(" must be ")
        assert subject in ("a tax rate row", "a tax line"), message
        shapes, separator, got = rest.partition("; got ")
        assert separator == "; got ", message
        assert got and not got.endswith("."), message
        assert shapes.startswith("("), message
    # The rate row's accepted shapes include the mapping form; the tax
    # line's do not, because `tax_for_lines` never took one. Same format,
    # different data — which is what distinguishes a shared checker from a
    # shared message.
    assert messages[0].count(" or ") == 1
    assert "{jurisdiction" in messages[0] and "{jurisdiction" in messages[1]
    assert " or " not in messages[2] and "{" not in messages[3]


def test_a_dict_rate_row_still_parses_and_a_dict_tax_line_still_raises():
    """Asymmetric on purpose, and the asymmetry is now written down.

    `load` has always accepted a mapping of `TaxRate`'s keywords and
    `tax_for_lines` has always refused one. This Task does not add it: a
    dict tax line is what a caller who confused the two APIs would pass,
    and accepting it would price a row whose field order nobody checked.
    The refusal now names the positional shape to use instead, which is
    the part that fails on the base.
    """
    table = api.TaxTable()
    table.load(
        [
            {"jurisdiction": "US-CA", "rate": "8.5"},
            {"jurisdiction": "US-CA", "rate": "0", "category": "groceries"},
        ]
    )
    calculator = api.TaxCalculator(table)
    assert calculator.tax_for(api.Money("100.00"), "US-CA", "furniture") == api.Money(
        "8.50"
    )
    assert calculator.tax_for(api.Money("100.00"), "US-CA", "groceries") == api.Money(
        "0.00"
    )
    with pytest.raises(TypeError) as refused:
        calculator.tax_for_lines([{"amount": api.Money("1.00"), "jurisdiction": "US-CA"}])
    assert refusal(TAX_LINE, refused.value, "dict")


def test_a_rate_row_mapping_is_refused_by_key_as_a_type_error():
    """A mapping's keys are its contract, so a wrong key is a TypeError.

    `TaxRate(**row)` reported both a missing and an unknown key as
    `TypeError` before this Task, and that is preserved rather than
    regularised into the `ValueError` a wrong *count* gets: for a sequence
    the values are right and the number is wrong, for a mapping the names
    themselves are wrong. The message is the new part — the old one came
    from Python's own argument binding and named `__init__`, not the row.
    """
    with pytest.raises(TypeError) as missing:
        api.TaxTable().load([{"jurisdiction": "US-CA"}])
    assert refusal(RATE_ROW, missing.value, "dict")
    with pytest.raises(TypeError) as unknown:
        api.TaxTable().load([{"jurisdiction": "US-CA", "rate": "8.5", "zone": "x"}])
    assert refusal(RATE_ROW, unknown.value, "dict")
    # And a row of the right keys still registers, so the key check did not
    # become a blanket refusal of mappings.
    table = api.TaxTable()
    table.load([{"jurisdiction": "US-CA", "rate": "8.5"}])
    assert api.TaxCalculator(table).tax_for(
        api.Money("100.00"), "US-CA", "furniture"
    ) == api.Money("8.50")


def test_an_unsupported_row_type_is_refused_with_the_accepted_shapes():
    """Neither a sequence nor a mapping, for either caller.

    A string is the interesting case and is included for it: it is
    iterable and has a length, so a checker that asked for `len(row)`
    before asking what `row` was would refuse `"US-CA"` for its arity and
    accept a five-character jurisdiction as a five-element row.
    """
    with pytest.raises(TypeError) as row:
        api.TaxTable().load([object()])
    assert refusal(RATE_ROW, row.value, "object")
    with pytest.raises(TypeError) as text:
        api.TaxTable().load(["US-CA"])
    assert refusal(RATE_ROW, text.value, "str")
    calculator = api.TaxCalculator(rate_table())
    with pytest.raises(TypeError) as line:
        calculator.tax_for_lines(["abc"])
    assert refusal(TAX_LINE, line.value, "str")


def test_taxable_defaults_to_true_and_the_message_says_which_field_defaults():
    """The default, and where it is now declared.

    A 3-element line is taxed exactly as the same line with an explicit
    `True`, which is what `tax_for_lines` did before. What is new is that
    the default belongs to the field: the accepted shape renders `taxable`
    inside brackets, marking it as the one field a shorter row may omit,
    and `category` is *not* bracketed in a tax line even though it is
    bracketed in a rate row. A checker carrying the default in a branch
    could not say either thing.

    The skeleton's fixed words are asserted whole rather than searched for
    the bracketed field: the base tree's two hand-written sentences happen
    to contain `category[, taxable]` and `rate[, category]` as well, so a
    substring assertion here would pass on a tree with two parsers and test
    nothing. The field list inside the shapes is matched, not pinned, for
    the reason recorded at `RATE_ROW`.
    """
    calculator = api.TaxCalculator(rate_table())
    assert calculator.tax_for_lines(
        [(api.Money("100.00"), "US-CA", "furniture")]
    ) == calculator.tax_for_lines(
        [(api.Money("100.00"), "US-CA", "furniture", True)]
    )
    with pytest.raises(ValueError) as refused:
        calculator.tax_for_lines([(api.Money("100.00"),)])
    assert refusal(TAX_LINE, refused.value, "a 1-element tuple")
    with pytest.raises(ValueError) as rate_row:
        api.TaxTable().load([("US-CA", "8.5", "groceries", True, "extra")])
    assert refusal(RATE_ROW, rate_row.value, "a 5-element tuple")


def test_a_non_bool_taxable_still_raises_rather_than_being_read_truthily():
    """The value check stayed with the calculator, not the unpacker.

    A shape says how many values a row has and what to call them; it never
    says what a value may be. So `"yes"` is still refused by
    `TaxCalculator` itself, with its own message, while a row of the wrong
    length is refused by the shared checker with the shared one. The two
    messages being different is the assertion: a consolidation that had
    swallowed the value checks would report both in one voice and lose the
    distinction between "wrong shape" and "wrong value".
    """
    calculator = api.TaxCalculator(rate_table())
    with pytest.raises(TypeError) as value:
        calculator.tax_for_lines([(api.Money("100.00"), "US-CA", "furniture", "yes")])
    assert str(value.value) == "taxable must be a bool, got str"
    with pytest.raises(ValueError) as shape:
        calculator.tax_for_lines([(api.Money("100.00"), "US-CA")])
    assert refusal(TAX_LINE, shape.value, "a 2-element tuple")


def test_tax_for_lines_figures_are_pinned_in_both_rounding_modes():
    """Fed from the new reader, so the pin is of the real pipeline inputs.

    `tax_for_lines` in `per_line` mode over `order.tax_lines()` reproduces
    the order's own `tax_total` line for line — the check that the reader
    reports the inputs the pipeline used and not a plausible
    reconstruction. `per_order` over the same rows gives 22.58, one cent
    less, because summing before rounding is not rounding before summing;
    both figures are pinned so that a tree collapsing the two modes fails
    rather than passing whichever one it kept.
    """
    order = build_order()
    calculator = api.TaxCalculator(rate_table())
    rows = [
        (share, "US-CA", line.product.category, line.product.taxable)
        for line, share in order.tax_lines()
    ]
    per_line = calculator.tax_for_lines(rows, mode="per_line")
    assert per_line == [api.Money("15.06"), api.Money("7.53"), api.Money("0.00")]
    assert sum(per_line, api.Money("0.00")) == order.tax_total == api.Money("22.59")
    assert calculator.tax_for_lines(rows, mode="per_order") == api.Money("22.58")
    # Each line also matches `tax_for` called on its own, which is what
    # `per_line` mode means.
    assert [
        calculator.tax_for(*row) for row in rows
    ] == per_line


def test_per_line_and_per_order_still_differ_on_a_bankers_rounding_boundary():
    """Ten lines of 0.10 at 8.5%: 0.10 per line, 0.08 summed first.

    0.0085 is exactly half a hundredth of a cent above 0.008, so bankers
    rounding takes 0.085 down to 0.08 while each 0.0085 goes up to 0.01.
    Any other rate or amount leaves the modes agreeing, and the pin of
    both figures then proves nothing about either.

    The rows come from a real order's `tax_lines()` rather than being
    written out, which is what makes this a statement about the pipeline
    and not about the calculator in isolation: the order's own `tax_total`
    is the per-line figure, 0.10, and the same pipeline rows totalled once
    come to 0.08. The drift is a property of the order the pipeline
    priced, visible only because the pairs it taxed can now be read.
    """
    products = api.Catalog()
    skus = [f"CENT-{index}" for index in range(10)]
    for sku in skus:
        products.add(api.Product(sku, sku, api.Money("0.10"), "furniture"))
    customer = api.Customer("C-1", "Ada", "US-CA")
    cart = api.Cart(products, customer)
    for sku in skus:
        cart.add(sku)
    calculator = api.TaxCalculator(rate_table())
    order = api.Order(
        "O-1",
        cart,
        api.PricingContext(
            discount_engine=api.DiscountEngine([]), tax_calculator=calculator
        ),
    )
    rows = [
        (share, "US-CA", line.product.category) for line, share in order.tax_lines()
    ]
    assert [share for _line, share in order.tax_lines()] == [api.Money("0.10")] * 10
    assert calculator.tax_for_lines(rows) == [api.Money("0.01")] * 10
    assert sum(calculator.tax_for_lines(rows), api.Money("0.00")) == api.Money("0.10")
    assert order.tax_total == api.Money("0.10")
    assert calculator.tax_for_lines(rows, mode="per_order") == api.Money("0.08")


def test_per_order_mode_over_an_empty_line_set_still_raises():
    """And the empty set the new reader can hand it is a real one.

    A tax-exempt customer's `tax_lines()` is empty, so composing the two
    additions of this Task produces exactly the input `per_order` mode has
    always refused: it cannot name a currency to total in. `per_line` over
    the same empty set still returns an empty list, which is the
    asymmetry that makes the refusal a decision rather than an oversight.
    """
    calculator = api.TaxCalculator(rate_table())
    exempt = build_order(tax_exempt=True)
    assert exempt.tax_lines() == ()
    with pytest.raises(ValueError):
        calculator.tax_for_lines(exempt.tax_lines(), mode="per_order")
    assert calculator.tax_for_lines(exempt.tax_lines(), mode="per_line") == []


def test_order_tax_lines_is_empty_for_a_tax_exempt_customer():
    """Asserted, not assumed, because the alternative is plausible.

    A reader built from the cart rather than from the tax pass would
    report three pairs here and look perfectly reasonable doing it — the
    lines exist, they have shares, and only `tax_total` knows the customer
    owes nothing. Exemption is answered once, where the pipeline answers
    it, and the reader inherits that answer rather than re-deriving it.
    """
    order = build_order(tax_exempt=True)
    assert order.tax_lines() == ()
    assert order.tax_total == api.Money("0.00")
    assert order.taxable_base == api.Money("0.00")
    # The order is still priced: an exempt customer pays the discounted
    # subtotal, so an empty reader does not mean an empty order.
    assert order.total == api.Money("309.99")


def test_order_tax_lines_excludes_a_cancelled_line_and_reallocates_the_rest():
    """A cancelled line is gone, and the survivors' shares move.

    Not merely absent: the flat 40.00 discount reallocates over the two
    remaining lines, so SKU-1's share rises from 177.14 to 168.00. A
    reader that filtered a cached list of pairs would drop SKU-2 and leave
    SKU-1 at 177.14, which is a figure this order never charged tax on.
    """
    order = build_order()
    order.cancel_line("SKU-2")
    pairs = order.tax_lines()
    assert [line.product.sku for line, _ in pairs] == ["SKU-1", "SKU-3"]
    assert [share for _, share in pairs] == [api.Money("168.00"), api.Money("42.00")]
    assert order.tax_total == api.Money("14.28")
    # The cart itself is untouched, which is why the reader has to ask the
    # order and not the cart.
    assert [line.product.sku for line in order.cart.lines] == [
        "SKU-1",
        "SKU-2",
        "SKU-3",
    ]


def test_order_tax_lines_shares_are_post_discount_and_sum_to_what_was_charged():
    """The shares conserve, which is what makes the reader trustworthy.

    For an all-taxable order the shares sum to `taxable_base` exactly —
    the property the entry asks for, and the one a per-line prorate would
    miss by a cent, since 40.00 allocated over 200.00 and 99.99 is
    26.67/13.33 by largest remainder. The catalog prices are asserted as
    *not* the shares so that a reader returning `line.line_total` fails
    here instead of passing the ordering assertions above.
    """
    order = build_order(skus=("SKU-1", "SKU-2"))
    pairs = order.tax_lines()
    assert [share for _, share in pairs] == [api.Money("173.33"), api.Money("86.66")]
    assert sum((share for _, share in pairs), api.Money("0.00")) == order.taxable_base
    assert order.taxable_base == api.Money("259.99")
    assert order.subtotal - order.discount_total == api.Money("259.99")
    assert [line.line_total for line, _ in pairs] != [share for _, share in pairs]


def test_order_tax_lines_includes_a_non_taxable_line_the_pass_looked_at():
    """Which is why a mixed order's shares do not sum to `taxable_base`.

    The gift card was allocated a discount share and then taxed at zero.
    It belongs in a reader that answers "what did the tax pass look at",
    and not in `taxable_base`, which answers "what was taxed". Stated as
    two different sums rather than left as a surprise: 309.99 over every
    pair, 265.70 over the taxable ones.
    """
    order = build_order()
    pairs = order.tax_lines()
    every = sum((share for _, share in pairs), api.Money("0.00"))
    taxed = sum(
        (share for line, share in pairs if line.product.taxable), api.Money("0.00")
    )
    assert every == api.Money("309.99") == order.subtotal - order.discount_total
    assert taxed == api.Money("265.70") == order.taxable_base
    assert not any(
        line.product.taxable for line, _ in pairs if line.product.sku == "SKU-3"
    )


def test_report_tax_lines_covers_every_non_cancelled_order_and_no_cancelled_one():
    """Per sku, across the report, with a cancelled order contributing none.

    The two orders hold disjoint skus so that "excluded" is visible rather
    than masked by a sku the surviving order also sells. The cancelled
    order's sku is absent entirely, not present with a zero — a zero would
    claim a line was looked at and taxed at nothing, which is what an
    exempt line looks like.
    """
    placed = build_order("O-1")
    placed.place()
    cancelled = build_order("O-2", skus=("SKU-4",), discounts=())
    cancelled.place()
    cancelled.cancel()
    report = api.RevenueReport([placed, cancelled])
    assert report.tax_lines() == {
        "SKU-1": api.Money("177.14"),
        "SKU-2": api.Money("88.56"),
        "SKU-3": api.Money("44.29"),
    }
    assert "SKU-4" not in report.tax_lines()


def test_report_tax_lines_sums_across_orders_and_back_to_net():
    """Two orders sharing a sku roll up, and the total is the report's net.

    `net` is summed `subtotal - discount_total`, and a non-exempt order's
    shares are exactly that subtotal less that discount — the discount
    allocation conserves — so the two figures must agree. That is the
    strongest available statement that this method covers every order the
    report covers, and it would fail for a method that dropped an order,
    double-counted one, or summed raw line totals instead of shares.
    """
    first = build_order("O-1", skus=("SKU-1", "SKU-2"))
    second = build_order("O-2", skus=("SKU-1", "SKU-4"))
    for order in (first, second):
        order.place()
    report = api.RevenueReport([first, second])
    lines = report.tax_lines()
    assert sorted(lines) == ["SKU-1", "SKU-2", "SKU-4"]
    assert lines["SKU-1"] == api.Money("173.33") + api.Money("175.00")
    assert sum(lines.values(), api.Money("0.00")) == report.net
    assert report.net == api.Money("539.99")


def test_report_tax_lines_is_not_by_category_in_another_shape():
    """Post-allocation shares versus raw line totals, deliberately apart.

    `by_category` sums `line_total` before any discount; this sums what
    the tax pass saw after allocation. On a discounted order the two must
    differ, and a method implemented as a re-keyed `by_category` would
    report 299.99 of furniture where 261.89 was taxed.
    """
    order = build_order(skus=("SKU-1", "SKU-2"))
    order.place()
    report = api.RevenueReport([order])
    assert report.by_category() == {"furniture": api.Money("299.99")}
    assert sum(report.tax_lines().values(), api.Money("0.00")) == api.Money("259.99")
    assert report.tax_lines()["SKU-1"] != api.Money("200.00")


def test_report_tax_lines_omits_an_exempt_order_whose_net_still_counts():
    """An exempt order is in the report and absent from this figure.

    The divergence from `net` is the point: `tax_lines()` reports what the
    tax pass looked at, and for an exempt customer that is nothing, while
    the order's revenue is still real. A method that reconstructed shares
    from the cart would report three pairs for an order that owes no tax
    at all.
    """
    exempt = build_order("O-1", tax_exempt=True)
    exempt.place()
    report = api.RevenueReport([exempt])
    assert report.tax_lines() == {}
    assert report.net == api.Money("309.99")
    assert report.tax_collected == api.Money("0.00")
