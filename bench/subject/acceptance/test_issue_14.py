"""R4 — Price history folds into the catalog.

`_PriceHistory` goes; the sorted insert and the `bisect_right` lookup become
`Catalog` internals; the fold is spent on the reader the history never had,
`Catalog.price_changes(sku)`, threaded up through `Cart.catalog_price(sku,
as_of)` and `RevenueReport.price_changes_seen()`.

A restructuring states a shape, and a suite can only see behaviour. So what
is asserted below is the behaviour the fold is supposed to leave intact and
the behaviour it is supposed to add, never the shape:

- Every figure `price_as_of` answered before the fold, it answers after,
  pinned across a before/on/between/after fixture. A pinned figure alone
  would pass on the base and assert nothing about this Task, so each pinned
  run travels inside a test that also reads the new surface — the pin is
  what makes the test a regression test, the new name is what makes it an
  invariant of *this* Task. The base suite pins the same figures
  independently and is the other half of that guarantee.
- The two invariants the old representation existed for: a change recorded
  out of chronological order still resolves for dates after it, and a second
  change recorded for a date that already has one wins. The first is the
  reason the original sorted on insert instead of appending; the second is
  true on the base and was never stated anywhere, which is the `C11` failure
  mode, so this Task states it and asserts it.
- What a public reader newly makes possible, and therefore newly makes
  breakable: a returned history that is a copy rather than the catalog's own
  growing list.
- The predecessor's reader survives. This Task is ordered after `M8`, and its
  Reference Implementation is cut from `M8`'s, so the tree this suite runs on
  already carries `Catalog.price_history(sku)` — `M8`'s public
  `(effective_date, price)` reader over the very class this Task deletes.
  After the fold it must answer the same pairs, read now from the
  `PriceChange` records. That cannot be asserted by reading `price_history`
  alone: `price_history` is green on this Task's base by construction, so a
  test that touched nothing else would measure `M8` rather than this Task.
  Each assertion below pins the two readers *against each other*, which only
  a tree carrying both can satisfy.

Fixture choice. The recorded prices are 275.00, 300.00, 310.00 and 320.00
against a `unit_price` of 250.00 — five distinct figures, so a lookup that is
off by one entry, that falls back to `unit_price` when it should not, or that
resolves a same-date pair to the first recording rather than the last is
visible rather than coincidentally right. The two changes sharing
2026-09-01 are 310.00 and 320.00 for exactly that reason: a `bisect_left`
insert files the correction before the mistake, and with equal prices the two
implementations would be indistinguishable.
"""

from datetime import date

import pytest

from billing import api

MAR = date(2026, 3, 1)
JUN = date(2026, 6, 1)
SEP = date(2026, 9, 1)

DESK_BASE = api.Money("250.00")
DESK_MAR = api.Money("275.00")
DESK_JUN = api.Money("300.00")
DESK_SEP_FIRST = api.Money("310.00")
DESK_SEP_LAST = api.Money("320.00")
LAMP_BASE = api.Money("125.50")
LAMP_JUN = api.Money("140.00")


def build_catalog():
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", DESK_BASE, "furniture"))
    catalog.add(api.Product("SKU-2", "Lamp", LAMP_BASE, "furniture"))
    catalog.add(
        api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers",
                    taxable=False)
    )
    return catalog


def with_desk_history(catalog=None):
    """A catalog whose SKU-1 carries four changes, recorded out of order.

    June is recorded before March deliberately: the sorted insert is the
    whole reason the history was never a plain append list, and a fixture
    that records chronologically cannot tell the two apart. The two
    September recordings go in ascending price so that "the last recorded
    wins" and "the highest price wins" are different answers.
    """
    catalog = catalog or build_catalog()
    catalog.record_price("SKU-1", JUN, DESK_JUN)
    catalog.record_price("SKU-1", MAR, DESK_MAR)
    catalog.record_price("SKU-1", SEP, DESK_SEP_FIRST)
    catalog.record_price("SKU-1", SEP, DESK_SEP_LAST)
    return catalog


def build_order(catalog, order_id="O-1", skus=("SKU-1", "SKU-3")):
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    customer = api.Customer("C-1", "Ada", "US-CA")
    cart = api.Cart(catalog, customer)
    for sku in skus:
        cart.add(sku)
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(table),
    )
    return api.Order(order_id, cart, pricing)


# --- The reader the history never had ---------------------------------


def test_a_recorded_change_can_be_read_back_as_a_date_and_a_price():
    """The gain the fold is spent on: the changes are observable at all.

    Before this, a recorded change could only be seen one date at a time
    through `price_as_of`, so "show me this sku's price history" meant
    guessing candidate dates. `PriceChange` is asserted by value rather
    than by attribute probing, so an implementation that returned bare
    `(date, price)` pairs — readable, but unnameable by a caller, and the
    thing `api.py`'s own rule refuses — does not pass.
    """
    catalog = build_catalog()
    catalog.record_price("SKU-1", MAR, DESK_MAR)
    assert catalog.price_changes("SKU-1") == (api.PriceChange(MAR, DESK_MAR),)
    change = catalog.price_changes("SKU-1")[0]
    assert change.effective_date == MAR
    assert change.price == DESK_MAR


def test_a_price_change_record_is_frozen():
    """A caller holding the catalog's record cannot rewrite the past.

    The old private representation made this unreachable by being
    unreadable. A public reader makes the defect possible, so immutability
    is part of the contract rather than an implementation detail.
    """
    change = with_desk_history().price_changes("SKU-1")[0]
    # `AttributeError` rather than `FrozenInstanceError`: the contract is
    # "a caller cannot write to it", and a frozen dataclass, a named tuple
    # and a property without a setter all satisfy it. Naming the dataclass
    # exception would assert the shape, which is what this suite cannot do.
    with pytest.raises(AttributeError):
        change.price = api.Money("1.00")
    with pytest.raises(AttributeError):
        change.effective_date = JUN
    assert change.price == DESK_MAR


def test_price_as_of_resolves_before_on_between_and_after_every_change():
    """Every figure the base answered, pinned, beside the new reader.

    The `MAR` and `JUN` assertions are on-the-boundary cases: a change takes
    effect *on* its own effective date, which is the `bisect_right` minus one
    invariant and the single most likely thing a rewrite gets wrong by one.
    """
    catalog = with_desk_history()
    assert catalog.price_as_of("SKU-1", date(2026, 1, 1)) == DESK_BASE
    assert catalog.price_as_of("SKU-1", date(2026, 2, 28)) == DESK_BASE
    assert catalog.price_as_of("SKU-1", MAR) == DESK_MAR
    assert catalog.price_as_of("SKU-1", date(2026, 4, 15)) == DESK_MAR
    assert catalog.price_as_of("SKU-1", JUN) == DESK_JUN
    assert catalog.price_as_of("SKU-1", date(2026, 8, 31)) == DESK_JUN
    assert catalog.price_as_of("SKU-1", date(2026, 12, 1)) == DESK_SEP_LAST
    # The new reader sees the same four recordings the lookup resolved
    # against: four entries, not three and not five.
    assert len(catalog.price_changes("SKU-1")) == 4


def test_a_change_recorded_out_of_chronological_order_still_resolves():
    """The invariant the sorted insert exists for.

    JUN was recorded before MAR. A plain append would leave the list
    unsorted, and a lookup scanning it would answer JUN's price for a date
    in April. The reader is asserted to be sorted by effective date
    regardless of recording order, which is the same fact seen from the
    other side.
    """
    catalog = with_desk_history()
    assert catalog.price_as_of("SKU-1", date(2026, 4, 15)) == DESK_MAR
    assert catalog.price_as_of("SKU-1", date(2026, 7, 1)) == DESK_JUN
    dates = [change.effective_date for change in catalog.price_changes("SKU-1")]
    assert dates == [MAR, JUN, SEP, SEP]


def test_two_changes_on_the_same_date_both_persist_and_the_last_wins():
    """True on the base and never stated, so this Task states it.

    A correction recorded for a date that already has a change does not
    replace it — both are kept — and the later recording is the one in
    effect. The reader reports them in recording order within the date,
    which is the same tie-break seen directly: a `bisect_left` insert would
    report (320, 310) here and resolve SEP to 310.00.
    """
    catalog = with_desk_history()
    assert catalog.price_as_of("SKU-1", SEP) == DESK_SEP_LAST
    assert catalog.price_as_of("SKU-1", date(2026, 10, 1)) == DESK_SEP_LAST
    same_date = [change.price for change in catalog.price_changes("SKU-1")
                 if change.effective_date == SEP]
    assert same_date == [DESK_SEP_FIRST, DESK_SEP_LAST]


def test_a_sku_with_no_recorded_change_has_an_empty_history_not_a_base_entry():
    """`price_changes` invents nothing.

    A synthetic entry for the product's own `unit_price` would be the
    tempting shape — it would make the history "complete" — and it is
    refused: a `unit_price` is not a change anybody recorded, and it has no
    effective date this module could know. `price_as_of` is where the
    `unit_price` fallback lives, and it still answers it.
    """
    catalog = with_desk_history()
    assert catalog.price_changes("SKU-2") == ()
    assert catalog.price_as_of("SKU-2", date(2026, 12, 1)) == LAMP_BASE
    # And a catalog with nothing recorded at all behaves the same way.
    assert build_catalog().price_changes("SKU-1") == ()


def test_price_changes_of_one_sku_are_not_visible_on_another():
    """Per-sku isolation, stated because a fold is where it gets lost.

    The removed class held one sku's changes per instance, so sharing was
    structurally impossible. A dict of lists on `Catalog` makes one shared
    list a one-character mistake, and every figure derived from it would be
    wrong for every sku at once.
    """
    catalog = with_desk_history()
    catalog.record_price("SKU-2", JUN, LAMP_JUN)
    assert catalog.price_changes("SKU-2") == (api.PriceChange(JUN, LAMP_JUN),)
    assert len(catalog.price_changes("SKU-1")) == 4
    assert catalog.price_as_of("SKU-2", MAR) == LAMP_BASE
    assert catalog.price_as_of("SKU-2", date(2026, 12, 1)) == LAMP_JUN


def test_price_changes_for_an_unknown_sku_raises_rather_than_reading_empty():
    """"No price changes" and "no such product" are different facts.

    `UnknownProduct`, matching `get` and `price_as_of`. An empty tuple here
    would let a typo'd sku read as a product that simply never moved price.
    """
    catalog = with_desk_history()
    with pytest.raises(api.UnknownProduct):
        catalog.price_changes("SKU-404")


def test_price_changes_is_a_copy_that_a_later_recording_does_not_grow():
    """The defect a public reader newly makes possible.

    Returning the internal list would hand a caller a sequence that mutates
    under it on the next `record_price`, and would let the caller reorder the
    catalog's own history. Asserted by recording a change *after* the read:
    an implementation handing back its own list fails on length alone.
    """
    catalog = with_desk_history()
    held = catalog.price_changes("SKU-1")
    catalog.record_price("SKU-1", date(2026, 11, 1), api.Money("330.00"))
    assert len(held) == 4
    assert len(catalog.price_changes("SKU-1")) == 5
    # Two reads are equal but the caller's copy is not the catalog's store:
    # nothing done to one is visible in the other.
    assert catalog.price_changes("SKU-1") == catalog.price_changes("SKU-1")


def test_a_rejected_recording_leaves_the_history_untouched():
    """The three refusals still refuse, and record nothing on the way out.

    Pinned against the reader, which is the only way to see it: before this
    Task, a recording that raised after partially mutating one of the two
    parallel lists would have left them out of step, and no caller could
    have observed that at all.
    """
    catalog = build_catalog()
    with pytest.raises(TypeError):
        catalog.record_price("SKU-1", None, DESK_MAR)
    with pytest.raises(TypeError):
        catalog.record_price("SKU-1", MAR, "275.00")
    with pytest.raises(api.UnknownProduct):
        catalog.record_price("SKU-404", MAR, DESK_MAR)
    assert catalog.price_changes("SKU-1") == ()
    assert catalog.price_as_of("SKU-1", date(2026, 12, 1)) == DESK_BASE
    with pytest.raises(TypeError):
        catalog.price_as_of("SKU-1", None)


def test_a_price_in_the_wrong_currency_still_raises_naming_both():
    """The currency guard survives the fold, message included.

    Both currencies are named, so a caller reading the error learns which
    way round the mismatch is; and the rejected change is not recorded.
    """
    catalog = build_catalog()
    with pytest.raises(api.CurrencyMismatch) as caught:
        catalog.record_price("SKU-1", MAR, api.Money("275.00", "EUR"))
    message = str(caught.value)
    assert "EUR" in message and "USD" in message and "SKU-1" in message
    assert catalog.price_changes("SKU-1") == ()


# --- Threaded through the cart ----------------------------------------


def test_cart_catalog_price_agrees_with_the_catalog_for_every_date():
    """A read-through, not a second lookup.

    Agreement is asserted across the whole fixture rather than at one date:
    a cart-side copy of the search could agree at a midpoint and disagree on
    a boundary, which is exactly where a re-implementation goes wrong.
    """
    catalog = with_desk_history()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1")
    for as_of in (date(2026, 1, 1), date(2026, 2, 28), MAR, date(2026, 4, 15),
                  JUN, date(2026, 8, 31), SEP, date(2026, 12, 1)):
        assert cart.catalog_price("SKU-1", as_of) == catalog.price_as_of(
            "SKU-1", as_of)
    assert cart.catalog_price("SKU-1", date(2026, 4, 15)) == DESK_MAR
    assert cart.catalog_price("SKU-1", SEP) == DESK_SEP_LAST


def test_cart_catalog_price_changes_no_line_total_and_no_subtotal():
    """Pricing a cart as of a date is a different operation, deliberately.

    Every figure on the cart still prices from `unit_price`, before and after
    the call, even though a change to 320.00 is on record. An
    implementation that let the history reach `line_total` would read 320.00
    here and fail — which is the whole point of pinning it.
    """
    catalog = with_desk_history()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1", 2)
    before = cart.subtotal
    assert before == api.Money("500.00")
    assert cart.catalog_price("SKU-1", date(2026, 12, 1)) == DESK_SEP_LAST
    assert cart.subtotal == before
    assert cart.lines[0].line_total == api.Money("500.00")
    assert cart.lines[0].product.unit_price == DESK_BASE


def test_cart_catalog_price_accepts_a_sku_the_cart_does_not_hold():
    """Matching `set_note` and `override_price`.

    This is a question about the catalog, and the cart is only the route to
    it, so requiring the sku to be in the cart would refuse something the
    catalog accepts. An unknown sku is still refused, by the catalog.
    """
    catalog = with_desk_history()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    assert cart.lines == []
    assert cart.catalog_price("SKU-1", date(2026, 12, 1)) == DESK_SEP_LAST
    assert cart.catalog_price("SKU-2", date(2026, 12, 1)) == LAMP_BASE
    with pytest.raises(api.UnknownProduct):
        cart.catalog_price("SKU-404", date(2026, 12, 1))


def test_cart_catalog_price_refuses_a_missing_date():
    """No clock, so no default date, and no silent one either.

    `TypeError` straight from the catalog: the cart re-validates nothing, so
    it cannot drift into accepting what the catalog refuses.
    """
    catalog = with_desk_history()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1")
    with pytest.raises(TypeError):
        cart.catalog_price("SKU-1", None)
    with pytest.raises(TypeError):
        cart.catalog_price("SKU-1")


def test_cart_pricing_catalog_is_the_catalog_the_lines_price_from():
    """Identity, not equality.

    The reason this is exposed at all is that a consumer handed a catalog
    separately could be handed a different one and would report prices these
    lines were never resolved through. Identity is the only assertion that
    rules that out.
    """
    catalog = with_desk_history()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1")
    assert cart.pricing_catalog is catalog
    assert cart.pricing_catalog.price_changes("SKU-1") == catalog.price_changes(
        "SKU-1"
    )
    # A merged-in cart does not replace it: `merge` resolves every line
    # through `self._catalog`, and the accessor reports that same one.
    other = api.Cart(build_catalog(), api.Customer("C-2", "Bo", "US-CA"))
    other.add("SKU-2")
    cart.merge(other)
    assert cart.pricing_catalog is catalog


# --- Surfaced on the report -------------------------------------------


def test_price_changes_seen_reports_the_changes_of_every_sku_sold():
    """One entry per sku sold that has changes, keyed by sku, sorted by sku.

    The value is the sku's whole history, asserted by value against
    `Catalog.price_changes`, so a report answering a count or a latest price
    does not pass. Sorting is asserted on the key order, not on membership:
    two orders are built so that the insertion order (O-2's SKU-2 seen
    first) differs from the sorted order.
    """
    catalog = with_desk_history()
    catalog.record_price("SKU-2", JUN, LAMP_JUN)
    second = build_order(catalog, "O-2", skus=("SKU-2",))
    first = build_order(catalog, "O-1", skus=("SKU-1", "SKU-3"))
    report = api.RevenueReport([second, first])
    seen = report.price_changes_seen()
    assert list(seen) == ["SKU-1", "SKU-2"]
    assert seen["SKU-1"] == catalog.price_changes("SKU-1")
    assert seen["SKU-2"] == (api.PriceChange(JUN, LAMP_JUN),)


def test_price_changes_seen_omits_a_sold_sku_with_no_recorded_change():
    """Absent, not present with an empty tuple.

    The question is "which of the things we sold were repriced". SKU-3 was
    sold and never repriced, so it is not an answer; a caller wanting every
    sku sold has `top_products`.
    """
    catalog = with_desk_history()
    report = api.RevenueReport([build_order(catalog)])
    seen = report.price_changes_seen()
    assert list(seen) == ["SKU-1"]
    assert "SKU-3" not in seen
    # SKU-2 was never sold by this report's orders, changes or not.
    catalog.record_price("SKU-2", JUN, LAMP_JUN)
    assert "SKU-2" not in api.RevenueReport(
        [build_order(catalog)]).price_changes_seen()


def test_price_changes_seen_excludes_a_cancelled_line_and_a_cancelled_order():
    """The module's one definition of "sold", applied here too.

    A cancelled line contributes no revenue, so its price history is not part
    of explaining this report's revenue either; a wholly cancelled order was
    already excluded at construction. An implementation reading
    `order.cart.lines` directly instead of the active-line filter reports
    SKU-1 in both cases and fails.
    """
    catalog = with_desk_history()
    partly = build_order(catalog, "O-1")
    partly.cancel_line("SKU-1")
    assert api.RevenueReport([partly]).price_changes_seen() == {}
    whole = build_order(catalog, "O-2")
    whole.cancel()
    assert api.RevenueReport([whole]).price_changes_seen() == {}
    # And the same sku sold on a surviving order is still reported, so the
    # exclusion is per line rather than per sku across the report.
    alive = build_order(catalog, "O-3")
    assert list(api.RevenueReport([partly, whole, alive])
                .price_changes_seen()) == ["SKU-1"]


def test_price_changes_seen_on_an_empty_report_is_an_empty_mapping():
    """Empty, not a raise and not `None`.

    A report with no orders has sold nothing, which is a complete answer to
    "which skus were repriced": there is no currency or catalog to be
    missing, unlike the money figures that have to invent a zero.
    """
    assert api.RevenueReport([]).price_changes_seen() == {}
    only_cancelled = build_order(with_desk_history(), "O-1")
    only_cancelled.cancel()
    assert api.RevenueReport([only_cancelled]).price_changes_seen() == {}


def test_price_changes_seen_reprices_nothing_it_reports_on():
    """Reports read orders and never reprice them, history or no history.

    The fixture has a change to 320.00 on record for a desk charged at
    250.00. Every figure is asserted against the charged price, before and
    after the history is read, so an implementation that folded the history
    into the pipeline while threading it up to the report fails here even
    though the base suite — which records no change on this fixture — stays
    green.
    """
    catalog = with_desk_history()
    order = build_order(catalog, "O-1")
    report = api.RevenueReport([order])
    assert report.gross == api.Money("300.00")
    assert report.tax_collected == api.Money("25.00")
    seen = report.price_changes_seen()
    assert seen["SKU-1"][-1].price == DESK_SEP_LAST
    assert report.gross == api.Money("300.00")
    assert report.tax_collected == api.Money("25.00")
    assert report.by_category() == {
        "furniture": api.Money("250.00"),
        "vouchers": api.Money("50.00"),
    }
    assert order.total == api.Money("325.00")


# --- The predecessor's reader survives the fold -----------------------


def test_m8_price_history_still_answers_the_same_pairs_after_the_fold():
    """`Catalog.price_history` answers what it answered on `M8`'s tree.

    The hardest thing this Task asks: `M8`'s public reader was written over
    `_PriceHistory.changes()`, and this Task deletes the class. The pairs are
    pinned by value — all four changes, ascending, with the two September
    recordings in the order they were recorded — so an implementation that
    kept the name alive over a resorted, deduplicated or reversed reading of
    the new records fails here.

    The same pairs are then derived from `price_changes` and required to
    match. That is what makes this a test of *this* Task rather than of
    `M8`: on this Task's base `price_history` already answers correctly and
    `price_changes` does not exist at all, so the two readers can only agree
    on a tree where the fold has happened and carried the old one through.
    """
    catalog = with_desk_history()
    assert catalog.price_history("SKU-1") == (
        (MAR, DESK_MAR),
        (JUN, DESK_JUN),
        (SEP, DESK_SEP_FIRST),
        (SEP, DESK_SEP_LAST),
    )
    assert catalog.price_history("SKU-1") == tuple(
        (change.effective_date, change.price)
        for change in catalog.price_changes("SKU-1")
    )


def test_price_history_and_price_changes_agree_on_the_empty_and_unknown_cases():
    """The edges agree too, not only the populated reading.

    `M8` settled both: a sku with no recorded change answers an empty
    sequence rather than `None` or an invented `unit_price` entry, and an
    unknown sku raises `UnknownProduct` rather than reading as a product that
    never changed price. The fold re-decided both for `price_changes`, and a
    fold that answered one way through the new reader and another way through
    the carried-through one would be two histories wearing one catalog.
    """
    catalog = with_desk_history()
    assert catalog.price_history("SKU-2") == ()
    assert catalog.price_changes("SKU-2") == ()
    with pytest.raises(api.UnknownProduct):
        catalog.price_history("SKU-404")
    with pytest.raises(api.UnknownProduct):
        catalog.price_changes("SKU-404")


def test_a_change_recorded_after_price_history_was_read_does_not_grow_it():
    """`price_history` is a copy, for the reason `price_changes` is.

    Both readers hand out the catalog's recorded history, and the fold moved
    that history onto `Catalog` itself. A reader returning the live list — or
    a view over it — would let a caller watch the past change under it. The
    two are checked in one test because the copy property is the one a shared
    internal list would break for both at once.
    """
    catalog = build_catalog()
    catalog.record_price("SKU-1", MAR, DESK_MAR)
    history = catalog.price_history("SKU-1")
    changes = catalog.price_changes("SKU-1")
    catalog.record_price("SKU-1", JUN, DESK_JUN)
    assert history == ((MAR, DESK_MAR),)
    assert changes == (api.PriceChange(MAR, DESK_MAR),)
    assert len(catalog.price_history("SKU-1")) == 2
