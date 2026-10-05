"""M7 — Rounding policy is a property of the jurisdiction.

`Money.round_to(unit, policy)` and an optional `unit` on `Money.round` in
`core/money.py`; `Rounding` and `TaxRate.rounding` carried through
`TaxTable.add`/`_parse_row` and read back by `TaxTable.rounding_for` in
`core/tax.py`; `tax_for` and `tax_for_lines` reading it instead of the
hardcoded `"bankers"`; and `RevenueReport.rounding_by_jurisdiction` as the
reporting surface.

Every figure below is computed from one rate, 6.25%, on one amount, 19.60,
chosen together because their product is 1.225 — a tax that lands exactly on
a half at the third decimal *and* exactly on a half-multiple of five cents.
That single fixture separates four implementations that a round rate or a
round amount would leave indistinguishable:

    19.60 * 6.25 / 100            = 1.225
    bankers to 0.01              -> 1.22   (2 is even, so it rounds down)
    half_up to 0.01              -> 1.23
    half_up to 0.05              -> 1.225 / 0.05 = 24.5 -> 25 -> 1.25
    bankers to 0.05              -> 24.5 -> 24           -> 1.20

Four distinct answers from one amount. An implementation that ignores the
declared policy, ignores the declared unit, applies the unit under the wrong
policy, or truncates instead of rounding lands on the wrong one of the four
rather than coinciding with the right one.
"""

import dataclasses
from decimal import Decimal

import pytest

from billing import api

# The rate and the amount whose product is exactly 1.225. 6.25 is not round,
# so an implementation that ignores the rate cannot coincide; 19.60 is not
# round, so one that ignores the amount cannot either.
RATE = "6.25"
AMOUNT = api.Money("19.60")

BANKERS_CENTS = api.Money("1.22")
HALF_UP_CENTS = api.Money("1.23")
HALF_UP_NICKEL = api.Money("1.25")
BANKERS_NICKEL = api.Money("1.20")


def build_table():
    """One rate, four jurisdictions, four roundings.

    Every jurisdiction charges the same 6.25% on the same amount, so the
    only thing that can make their figures differ is the rounding each
    declares. That is the whole claim of this Task, arranged so a test can
    see nothing else.
    """
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", RATE))
    table.add(api.TaxRate("US-NV", RATE, rounding=api.Rounding("half_up")))
    table.add(api.TaxRate("US-TX", RATE, rounding=api.Rounding("half_up", "0.05")))
    table.add(api.TaxRate("US-AZ", RATE, rounding=api.Rounding("bankers", "0.05")))
    return table


def build_order(order_id, jurisdiction, quantity=1):
    """An order whose one line costs 19.60 per unit, in `jurisdiction`.

    No discount, so `Order.tax_total` taxes the line total itself and the
    arithmetic in the module docstring is the whole of the order's tax.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Clamp", api.Money("19.60"), "hardware"))
    customer = api.Customer(f"C-{order_id}", "Ada", jurisdiction)
    cart = api.Cart(catalog, customer)
    cart.add("SKU-1", quantity)
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(build_table()),
    )
    return api.Order(order_id, cart, pricing)


def test_the_rounding_type_is_exported_and_its_default_is_todays_behavior():
    """`Rounding()` is the policy and unit every figure already used.

    Asserted on the defaults rather than on the type alone, because the
    default is load-bearing: it is what a jurisdiction declaring nothing
    resolves to, and a default of `half_up` would silently re-round every
    existing figure in the package.
    """
    assert api.Rounding is not None
    assert api.Rounding().policy == "bankers"
    assert api.Rounding().unit is None
    # Compared by value, which `tax_for_lines` depends on when it decides
    # whether two lines' jurisdictions agree. Identity would make two
    # equally-declared lines disagree.
    assert api.Rounding("half_up", "0.05") == api.Rounding("half_up", "0.05")
    assert api.Rounding("half_up", "0.05") != api.Rounding("bankers", "0.05")
    assert api.Rounding("half_up", "0.05") != api.Rounding("half_up")


def test_a_rounding_is_frozen_so_a_registered_instruction_cannot_be_edited():
    """An authority's instruction is a value, not a mutable record.

    A table holds the `TaxRate` a row registered and hands its `rounding`
    to whoever asks, so a mutable one could be edited through any reader
    and every figure computed afterwards would round under an instruction
    no row declares. The `unit` coercion in `__post_init__` goes through
    `object.__setattr__` precisely because this is frozen.
    """
    rounding = api.Rounding("half_up", "0.05")
    with pytest.raises(dataclasses.FrozenInstanceError):
        rounding.policy = "bankers"
    with pytest.raises(dataclasses.FrozenInstanceError):
        rounding.unit = Decimal("0.01")
    assert rounding == api.Rounding("half_up", "0.05")


def test_a_rate_with_no_rounding_taxes_exactly_as_it_did_before():
    """Pinned against the figure the base tree produces, not merely "some
    bankers answer".

    1.225 under bankers is 1.22, and under the half-up default of
    `Money.round` it would be 1.23. So this single assertion is what catches
    an implementation that resolved the default rounding to `Rounding()`
    with `Money.round`'s own default policy instead of tax's.
    """
    calculator = api.TaxCalculator(build_table())
    assert calculator.tax_for(AMOUNT, "US-CA", "hardware") == BANKERS_CENTS
    # The figure the base suite itself pins, recomputed here so a change to
    # the rounding path cannot move it unnoticed: 10.05 * 10% = 1.005, which
    # is 1.00 under bankers.
    table = api.TaxTable()
    table.add(api.TaxRate("US-OH", "10"))
    assert api.TaxCalculator(table).tax_for(
        api.Money("10.05"), "US-OH", "hardware") == api.Money("1.00")


def test_round_to_rounds_to_a_unit_coarser_than_the_currencys_minor_unit():
    """And the policy still decides the direction at the coarse unit.

    1.225 is 24.5 five-cent units: exactly a half, so half up and bankers
    disagree on it. A coarse-unit implementation that hardcoded one policy
    passes one of these two assertions and fails the other.
    """
    amount = api.Money("1.225")
    assert amount.round_to("0.05", "half_up") == HALF_UP_NICKEL
    assert amount.round_to("0.05", "bankers") == BANKERS_NICKEL
    # A whole-dollar authority, to show the unit is not special-cased at
    # five cents: 1.225 / 1 = 1.225, which is 1 either way.
    assert amount.round_to("1", "half_up") == api.Money("1.00")
    # And the result keeps the currency's own scale rather than the unit's
    # arithmetic scale, so it compares and prints as money.
    assert amount.round_to("0.05", "half_up").amount == Decimal("1.25")
    assert amount.round_to("0.05", "half_up").currency == "USD"


def test_round_to_refuses_a_unit_finer_than_the_currencys_minor_unit():
    """A currency has no representation below its minor unit.

    Read off the registered exponent, not a hardcoded two places, which the
    JPY and BHD assertions are what establish: 0.05 is coarser than a cent
    and *finer* than a whole yen, so an implementation comparing against
    0.01 everywhere accepts it for JPY and is wrong.
    """
    with pytest.raises(ValueError):
        api.Money("1.2255").round_to("0.001")
    with pytest.raises(ValueError):
        api.Money("150", "JPY").round_to("0.05")
    # BHD prices in thousandths, so 0.001 is its minor unit and is allowed
    # where USD refuses it — the same registered exponent, read twice.
    assert api.Money("1.2255", "BHD").round_to("0.001", "half_up") == api.Money(
        "1.226", "BHD")
    assert api.Money("1234", "JPY").round_to("5", "half_up") == api.Money(
        "1235", "JPY")


def test_round_to_refuses_a_non_positive_unit():
    """Rounding to the nearest zero is not an instruction, and a unit of
    zero would otherwise surface as a division by zero inside the rounding.
    """
    with pytest.raises(ValueError):
        api.Money("1.225").round_to("0")
    with pytest.raises(ValueError):
        api.Money("1.225").round_to("-0.05")
    with pytest.raises(ValueError):
        api.Money("1.225").round(unit=0)


def test_round_to_refuses_a_unit_that_is_not_a_whole_number_of_minor_units():
    """0.015 is coarser than a cent and still unpayable.

    Its multiples land half a cent off the grid USD settles on, so accepting
    it would break the only reason the other two refusals exist: that what
    this method returns can be charged.
    """
    with pytest.raises(ValueError):
        api.Money("1.225").round_to("0.015")
    # 0.03 is not a "nice" unit either, but every multiple of it is a whole
    # number of cents, so it is accepted: the rule is divisibility, not a
    # list of blessed units. 1.225 / 0.03 = 40.83..., half up to 41 -> 1.23.
    assert api.Money("1.225").round_to("0.03", "half_up") == api.Money("1.23")


def test_rounds_existing_one_argument_calls_are_unchanged():
    """Every caller in the package calls `round()` or `round("bankers")`.

    The unit is the *second* positional parameter precisely so those calls
    keep their meaning; the last assertion is what fails if a would-be
    implementation put the unit first and reinterpreted "bankers" as a unit.
    """
    assert api.Money("1.005").round() == api.Money("1.01")
    assert api.Money("1.005").round("bankers") == api.Money("1.00")
    assert api.Money("1.015").round("bankers") == api.Money("1.02")
    assert api.Money("1.225").round("half_up", "0.05") == HALF_UP_NICKEL
    assert api.Money("1.225").round("bankers", "0.05") == BANKERS_NICKEL
    assert api.Money("1.225").round(unit="0.05") == HALF_UP_NICKEL


def test_an_unknown_policy_raises_value_error_naming_it_on_every_path():
    """Three entry points, one registry of policy names.

    The `Rounding` assertion is the one that matters most: a policy name is
    a data-entry value, and a `Rounding` that accepted `"half-up"` (hyphen
    where the registry has an underscore) would turn a typo in one table row
    into a failure on the first order priced, hours later.
    """
    with pytest.raises(ValueError) as one:
        api.Money("1.225").round("nope")
    assert "nope" in str(one.value)
    with pytest.raises(ValueError) as two:
        api.Money("1.225").round_to("0.05", "nope")
    assert "nope" in str(two.value)
    with pytest.raises(ValueError) as three:
        api.Rounding("half-up")
    assert "half-up" in str(three.value)


def test_tax_for_honours_a_per_jurisdiction_half_up_policy():
    """Same rate, same amount, different declared policy, different figure.

    The assertion that the two differ is not decoration: it is what kills an
    implementation that reads the declaration, stores it, and rounds bankers
    anyway. Both jurisdictions charge 6.25% of 19.60 = 1.225.
    """
    calculator = api.TaxCalculator(build_table())
    assert calculator.tax_for(AMOUNT, "US-NV", "hardware") == HALF_UP_CENTS
    assert calculator.tax_for(AMOUNT, "US-CA", "hardware") == BANKERS_CENTS
    assert calculator.tax_for(AMOUNT, "US-NV", "hardware") != calculator.tax_for(
        AMOUNT, "US-CA", "hardware")


def test_tax_for_honours_a_coarse_unit_under_the_declared_policy():
    """The unit and the policy are two facts and both are read.

    US-TX and US-AZ declare the same five-cent unit and opposite policies,
    so an implementation that honours the unit and hardcodes the policy
    gives both 1.25 or both 1.20 and fails one of these.
    """
    calculator = api.TaxCalculator(build_table())
    assert calculator.tax_for(AMOUNT, "US-TX", "hardware") == HALF_UP_NICKEL
    assert calculator.tax_for(AMOUNT, "US-AZ", "hardware") == BANKERS_NICKEL
    # And neither coincides with the cent-unit answer under either policy,
    # so an implementation that ignores the unit fails too.
    assert calculator.tax_for(AMOUNT, "US-TX", "hardware") != HALF_UP_CENTS
    assert calculator.tax_for(AMOUNT, "US-AZ", "hardware") != BANKERS_CENTS


def test_the_rounding_is_read_back_under_the_same_key_as_the_rate():
    """`rounding_for` is the reader that makes `TaxRate.rounding` a contract.

    A table that accepted a `rounding` and dropped it would be
    indistinguishable from one that honoured it if nothing could ask what it
    held. And the key resolution has to match `rate_for`'s: a category
    override's own rounding wins over the jurisdiction default's, because
    the rate came from that same row.
    """
    table = build_table()
    assert table.rounding_for("US-CA") == api.Rounding("bankers", None)
    assert table.rounding_for("US-TX") == api.Rounding("half_up", Decimal("0.05"))
    # A category row carrying its own rounding: the figure must be rounded
    # under the row its rate came from, not under the jurisdiction default.
    table.add(api.TaxRate("US-TX", RATE, category="groceries",
                          rounding=api.Rounding("bankers")))
    assert table.rounding_for("US-TX", "groceries") == api.Rounding("bankers")
    assert table.rounding_for("US-TX", "hardware") == api.Rounding(
        "half_up", Decimal("0.05"))
    calculator = api.TaxCalculator(table)
    assert calculator.tax_for(AMOUNT, "US-TX", "groceries") == BANKERS_CENTS
    assert calculator.tax_for(AMOUNT, "US-TX", "hardware") == HALF_UP_NICKEL
    # An unregistered jurisdiction has no rounding any more than it has a
    # rate. Defaulting here would answer a rule for an authority owed
    # nothing.
    with pytest.raises(api.UnknownJurisdiction):
        table.rounding_for("US-ZZ")


def test_re_registering_a_scope_without_a_rounding_clears_the_previous_one():
    """A policy that outlived the row it arrived with would round figures
    under an instruction no current row carries, and the table would hold a
    rate and a rounding that were never filed together.
    """
    table = build_table()
    table.add(api.TaxRate("US-TX", RATE))
    assert table.rounding_for("US-TX") == api.Rounding()
    assert api.TaxCalculator(table).tax_for(
        AMOUNT, "US-TX", "hardware") == BANKERS_CENTS


def test_an_exempt_line_is_answered_before_the_rounding_is_looked_up():
    """Exemption already short-circuits the rate lookup, and the rounding
    lookup must not reintroduce the failure the short-circuit exists to
    avoid: an exempt line in an unregistered jurisdiction owes zero, not an
    `UnknownJurisdiction`.
    """
    calculator = api.TaxCalculator(build_table())
    assert calculator.tax_for(
        api.Money("10.00"), "US-ZZ", "vouchers", taxable=False) == api.Money("0.00")
    assert calculator.tax_for(
        api.Money("10.00", "EUR"), "US-ZZ", "vouchers", taxable=False
    ) == api.Money("0.00", "EUR")


def test_per_line_mode_rounds_each_line_under_its_own_jurisdiction():
    """A basket taxed by two authorities gets each line its own rule.

    Two lines, identical amounts, different jurisdictions, and the two
    results differ — which an implementation that resolved one rounding for
    the whole call cannot produce.
    """
    calculator = api.TaxCalculator(build_table())
    lines = [
        (AMOUNT, "US-TX", "hardware"),
        (AMOUNT, "US-CA", "hardware"),
        (AMOUNT, "US-AZ", "hardware"),
    ]
    assert calculator.tax_for_lines(lines) == [
        HALF_UP_NICKEL, BANKERS_CENTS, BANKERS_NICKEL]


def test_per_order_mode_rounds_once_under_the_jurisdictions_policy():
    """Once, not per line, and the difference is visible.

    Three lines, deliberately, because two 1.225 lines sum to 2.45 — already
    a whole number of five-cent units, so the coarse unit would be invisible.
    Three sum to 3.675 unrounded, which is 73.5 five-cent units and rounds
    half up to 3.70; rounding each line first gives 3 * 1.25 = 3.75, and
    rounding the same 3.675 to cents gives 3.68. Three distinct answers, so
    this figure is reachable only by summing unrounded and applying the
    jurisdiction's own unit once.
    """
    calculator = api.TaxCalculator(build_table())
    lines = [(AMOUNT, "US-TX", "hardware")] * 3
    assert calculator.tax_for_lines(lines, mode="per_order") == api.Money("3.70")
    assert sum(calculator.tax_for_lines(lines), api.Money("0.00")) == api.Money("3.75")
    # The same three lines under the cent-unit jurisdiction: 3.675 rounds
    # bankers to 3.68 (the 7 before the 5 is odd), where the per-line sum is
    # 3 * 1.22 = 3.66 — the drift per-order mode exists to avoid.
    cents = [(AMOUNT, "US-CA", "hardware")] * 3
    assert calculator.tax_for_lines(cents, mode="per_order") == api.Money("3.68")
    assert sum(calculator.tax_for_lines(cents), api.Money("0.00")) == api.Money("3.66")


def test_per_order_mode_raises_when_the_lines_disagree_on_rounding():
    """One rounding is applied, so the lines have to agree on which.

    Picking one would round one authority's share under another's
    instruction while reading as the more accurate figure. `per_line` is the
    mode for lines that genuinely disagree, and it still answers.
    """
    calculator = api.TaxCalculator(build_table())
    mixed = [(AMOUNT, "US-TX", "hardware"), (AMOUNT, "US-CA", "hardware")]
    with pytest.raises(ValueError):
        calculator.tax_for_lines(mixed, mode="per_order")
    assert calculator.tax_for_lines(mixed) == [HALF_UP_NICKEL, BANKERS_CENTS]
    # Same declared rounding, different jurisdictions: agreement is on the
    # rule, not on the authority, so this is not a disagreement. US-TX and
    # a second five-cent half-up authority sum and round once.
    table = build_table()
    table.add(api.TaxRate("US-UT", RATE, rounding=api.Rounding("half_up", "0.05")))
    agreeing = [(AMOUNT, "US-TX", "hardware"), (AMOUNT, "US-UT", "hardware")]
    assert api.TaxCalculator(table).tax_for_lines(
        agreeing, mode="per_order") == api.Money("2.45")


def test_an_exempt_line_does_not_veto_per_order_rounding():
    """An exempt line owes nothing, its jurisdiction need not be registered
    at all, and letting it cast a vote on the rounding would fail an order
    that owes exactly what it always did.
    """
    calculator = api.TaxCalculator(build_table())
    lines = [
        (AMOUNT, "US-TX", "hardware"),
        (AMOUNT, "US-TX", "hardware"),
        (api.Money("50.00"), "US-ZZ", "vouchers", False),
    ]
    assert calculator.tax_for_lines(lines, mode="per_order") == api.Money("2.45")
    # All-exempt: there is no declared rounding to find, and the zero rounds
    # under the module default rather than raising.
    exempt = [(api.Money("50.00"), "US-ZZ", "vouchers", False)]
    assert calculator.tax_for_lines(exempt, mode="per_order") == api.Money("0.00")


def test_a_compound_jurisdiction_whose_scopes_disagree_on_rounding_raises():
    """Rates sum across scopes; a rounding cannot.

    Each authority is owed its own share, so the rates add. There is no
    corresponding way to add two rounding rules, and choosing one scope's
    would round the city's share under the state's instruction with nothing
    recording the choice. Scopes that agree resolve to the rule they share.
    """
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "5", rounding=api.Rounding("half_up", "0.05")))
    table.add(api.TaxRate("US-CA-SF", "1.25"))
    calculator = api.TaxCalculator(table)
    # The summed rate is reachable and unchanged — 5 + 1.25 = 6.25 — so this
    # raise is about the rounding and not about the compound path breaking.
    assert table.rate_for(("US-CA", "US-CA-SF"), "hardware") == Decimal("6.25")
    with pytest.raises(ValueError):
        table.rounding_for(("US-CA", "US-CA-SF"))
    with pytest.raises(ValueError):
        calculator.tax_for(AMOUNT, ("US-CA", "US-CA-SF"), "hardware")
    # Declared on both scopes, the compound resolves and the figure is the
    # five-cent half-up one for the summed 6.25%.
    table.add(api.TaxRate("US-CA-SF", "1.25",
                          rounding=api.Rounding("half_up", "0.05")))
    assert table.rounding_for(("US-CA", "US-CA-SF")) == api.Rounding(
        "half_up", Decimal("0.05"))
    assert calculator.tax_for(
        AMOUNT, ("US-CA", "US-CA-SF"), "hardware") == HALF_UP_NICKEL


def test_a_rounding_naming_an_unknown_policy_is_refused_at_registration():
    """At the row, not at the first order priced under it.

    A table that accepted the row would fail on whichever order happened to
    be priced first, naming a policy the caller would then have to trace
    back to a row loaded much earlier.
    """
    with pytest.raises(ValueError):
        api.TaxRate("US-CA", RATE, rounding=api.Rounding("nope"))
    table = api.TaxTable()
    with pytest.raises(ValueError):
        table.load([{"jurisdiction": "US-CA", "rate": RATE,
                     "rounding": api.Rounding("nope")}])
    # And nothing was registered, so the refusal is not partial.
    with pytest.raises(api.UnknownJurisdiction):
        table.rate_for("US-CA", "hardware")
    # A non-positive unit is refused at the same boundary, for the same
    # reason: rounding to the nearest zero is not an instruction.
    with pytest.raises(ValueError):
        api.Rounding("half_up", "0")


def test_rounding_is_absent_able_in_both_load_row_shapes():
    """Absent is the common case and must stay free.

    A dict row omits the key, a tuple row stops at 2 or 3 elements, and
    either way the rate rounds exactly as it did before jurisdictions could
    declare anything — pinned on the figure, not on the absence.
    """
    table = api.TaxTable()
    table.load([
        {"jurisdiction": "US-CA", "rate": RATE},
        ("US-OR", RATE),
        ("US-WA", RATE, "hardware"),
        {"jurisdiction": "US-TX", "rate": RATE,
         "rounding": api.Rounding("half_up", "0.05")},
        {"jurisdiction": "US-AZ", "rate": RATE,
         "rounding": api.Rounding("bankers", "0.05")},
    ])
    calculator = api.TaxCalculator(table)
    assert calculator.tax_for(AMOUNT, "US-CA", "hardware") == BANKERS_CENTS
    assert calculator.tax_for(AMOUNT, "US-OR", "hardware") == BANKERS_CENTS
    assert calculator.tax_for(AMOUNT, "US-WA", "hardware") == BANKERS_CENTS
    assert calculator.tax_for(AMOUNT, "US-TX", "hardware") == HALF_UP_NICKEL
    assert calculator.tax_for(AMOUNT, "US-AZ", "hardware") == BANKERS_NICKEL
    # A five-element row is still malformed, and the message says so rather
    # than silently dropping the extra.
    with pytest.raises(ValueError):
        table.load([("US-ID", RATE, None, None, "extra")])


def test_an_order_charges_tax_under_its_customers_jurisdiction_rounding():
    """The policy reaches the pipeline, not only the calculator.

    `Order.tax_total` rounds per line through `tax_for`, so the order in the
    five-cent half-up jurisdiction owes 1.25 where the cent jurisdiction
    owes 1.22, and `total` carries the difference.
    """
    texan = build_order("O-1", "US-TX")
    californian = build_order("O-2", "US-CA")
    assert texan.subtotal == api.Money("19.60")
    assert texan.tax_total == HALF_UP_NICKEL
    assert californian.tax_total == BANKERS_CENTS
    assert texan.total == api.Money("20.85")        # 19.60 - 0.00 + 1.25
    assert californian.total == api.Money("20.82")  # 19.60 - 0.00 + 1.22


def test_tax_by_jurisdiction_sums_already_rounded_figures_and_does_not_re_round():
    """The report cannot disagree with the orders about tax owed.

    Two US-TX orders each charged 1.25, rounded from 1.225 under the
    jurisdiction's five-cent unit. Their sum is 2.50. A report that summed
    the raw 2.45 and applied the unit once would publish 2.45 — a figure no
    order charged and nothing accounts for the 0.05 difference. So the
    assertion is on 2.50 *and* against 2.45, because only the pair
    distinguishes summing from recomputing.
    """
    orders = [build_order("O-1", "US-TX"), build_order("O-2", "US-TX")]
    for order in orders:
        order.place()
    report = api.RevenueReport(orders)
    assert report.tax_by_jurisdiction() == {"US-TX": api.Money("2.50")}
    assert report.tax_by_jurisdiction()["US-TX"] != api.Money("2.45")
    assert report.tax_by_jurisdiction()["US-TX"] == sum(
        (order.tax_total for order in orders), api.Money("0.00"))
    assert report.tax_collected == api.Money("2.50")


def test_rounding_by_jurisdiction_reports_the_rule_each_figure_was_rounded_under():
    """The remittance reader: what was collected, and under which rule.

    Keyed exactly as `tax_by_jurisdiction` is — same orders, same
    cancellation filter — so the two can be read side by side. The cancelled
    order is in a jurisdiction the table does not hold, which makes the
    exclusion falsifiable: an implementation that forgot the filter raises
    `UnknownJurisdiction` here rather than returning a third key.
    """
    table = build_table()
    texan = build_order("O-1", "US-TX")
    californian = build_order("O-2", "US-CA")
    ghost = build_order("O-3", "US-ZZ")
    for order in (texan, californian, ghost):
        order.place()
    ghost.cancel()
    report = api.RevenueReport([texan, californian, ghost])
    assert report.rounding_by_jurisdiction(table) == {
        "US-CA": api.Rounding("bankers", None),
        "US-TX": api.Rounding("half_up", Decimal("0.05")),
    }
    assert list(report.rounding_by_jurisdiction(table)) == list(
        report.tax_by_jurisdiction())
    # A jurisdiction the table does not hold is not reported under a
    # default: the orders were charged under some rule, and inventing one
    # would report a rule nobody filed under. Still placed, this time, so the
    # raise is about the missing jurisdiction and not about the filter.
    stranger = build_order("O-4", "US-ZZ")
    stranger.place()
    with pytest.raises(api.UnknownJurisdiction):
        api.RevenueReport([texan, stranger]).rounding_by_jurisdiction(table)


def test_an_empty_report_has_no_jurisdictions_and_asks_the_table_nothing():
    """An empty mapping, not a mapping of defaults.

    `api.TaxTable()` holds nothing at all, so an implementation that
    probed it for anything raises here instead of answering.
    """
    report = api.RevenueReport([])
    assert report.rounding_by_jurisdiction(api.TaxTable()) == {}
    assert report.tax_by_jurisdiction() == {}
