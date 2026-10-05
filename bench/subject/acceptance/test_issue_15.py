"""R5 — One audit stamp, and the override reason becomes readable.

One `AuditStamp(as_of, reason)` in `sales/orders.py` replaces the audit pair
spelled three ways across `_Transition`, `SegmentTransition` and a
`_PriceOverride` that is removed outright, and `Cart.override_reason(sku)`
becomes the reader an override's reason never had.

A restructuring has no shape a suite can see, so every test below names a
behavioural consequence instead:

- The stamp is reachable and owns one rule about reasons — `None` accepted,
  blank rejected, kept reasons stripped — and that rule is observed at all
  three call sites that now route through it (`override_price`,
  `refund_line`, `change_segment`). Three sites answering the same way is
  the only evidence a suite can have that they share one definition.
- The reason of a price override is readable before the sku is in the cart.
  That is unobservable on the base tree at any price, and it is the Task.
- Every public shape the restructuring moves storage behind is pinned field
  by field: `transitions()`, `LineItem`, `segment_history`.

**Why the pinning tests also touch something new.** A test that passes on
the base tree asserts nothing this Task adds — 15 of 24 `v4` suites failed
exactly that way. So each pin here asserts the unchanged figures *first*,
in the order a reader would check them, and then one new fact that makes
the test fail on the base tree: usually that the recorded pair is exactly
what `api.AuditStamp` normalizes it to. A Reference Implementation that
moved `as_of` off `transitions()` fails on the pin, before reaching the new
assertion, which is the failure order that tells an author what broke.

**Why these fixture values.** Reasons are distinct strings per call site, so
a reader that returns *an* override's reason rather than *this* sku's fails.
The stripped reason carries an interior space (`"manager override"`), so an
implementation that strips all whitespace rather than surrounding whitespace
fails where `"clearance"` would have passed either way. The override price is
`199.99` against a catalog price of `250.00` at quantity 3, giving `599.97`:
quantity 1 would leave "uses the override price" and "uses the override
price once" indistinguishable, and a round override price would leave a
truncating multiply passing.
"""

import dataclasses

import pytest

from billing import api

# Whitespace-only in three different ways. A rule written as `not reason`
# catches the first and misses the other two, which is the mistake that
# makes a reason that says nothing pass for one that was never given.
BLANK_REASONS = ["", "   ", "\t\n "]

CATALOG_PRICE = api.Money("250.00")
OVERRIDE_PRICE = api.Money("199.99")
OVERRIDE_REASON = "manager override"
OTHER_REASON = "clearance"


def build_cart():
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", CATALOG_PRICE, "furniture"))
    catalog.add(api.Product("SKU-2", "Chair", api.Money("125.50"), "furniture"))
    customer = api.Customer("C-1", "Ada", "US-CA")
    return api.Cart(catalog, customer)


def build_order():
    """A two-line order, one taxable line and one not, matching the Subject's
    own fixture so the pipeline figures a cancellation moves are the figures
    the base suite already pins.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(
        api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers", taxable=False)
    )
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1")
    cart.add("SKU-3")
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(table),
    )
    return api.Order("O-1", cart, pricing)


def build_book():
    book = api.CustomerBook()
    book.add(api.Customer("C-1", "Ada", "US-CA"))
    return book


# --- The stamp itself -----------------------------------------------------


def test_the_audit_stamp_is_exported_and_carries_the_pair_in_that_order():
    """`AuditStamp(as_of, reason)`, positionally, and both optional.

    The argument order is asserted positionally on purpose: the three
    records it replaces all wrote `as_of` before `reason`, and a stamp that
    took them the other way round would make every keyword-free call site
    silently record the date as the reason.
    """
    stamp = api.AuditStamp("2026-01-15", OVERRIDE_REASON)
    assert stamp.as_of == "2026-01-15"
    assert stamp.reason == OVERRIDE_REASON
    empty = api.AuditStamp()
    assert empty.as_of is None
    assert empty.reason is None


def test_a_stamp_accepts_no_reason_and_refuses_one_that_says_nothing():
    """"No reason given" and "a reason that says nothing" are different
    facts, and only the second is an error.

    `None` has to be accepted: `place()`, `cancel()` and `cancel_line()`
    take no reason at all, so a stamp that required one would have no way to
    record them.
    """
    assert api.AuditStamp("2026-01-15").reason is None
    assert api.AuditStamp("2026-01-15", None).reason is None
    for blank in BLANK_REASONS:
        with pytest.raises(ValueError):
            api.AuditStamp("2026-01-15", blank)


def test_a_stamp_strips_the_reason_around_it_and_not_inside_it():
    """Stored stripped, matching what `override_price` has always stored.

    The fixture reason contains an interior space, so an implementation that
    removed all whitespace rather than the surrounding whitespace fails here
    instead of passing on a single-word reason.
    """
    stamp = api.AuditStamp("2026-01-15", f"  {OVERRIDE_REASON}\t")
    assert stamp.reason == OVERRIDE_REASON


def test_a_stamp_refuses_a_non_string_reason_the_way_the_cart_always_did():
    """`ValueError`, not `TypeError`.

    `Cart.override_price` has always raised `ValueError` for a non-string
    reason, and callers may catch it. Consolidating three sites into one is
    not a licence to change an exception type a caller already handles, so
    the stamp inherits the cart's answer rather than the more idiomatic one.
    """
    with pytest.raises(ValueError):
        api.AuditStamp("2026-01-15", 42)


def test_a_stamp_cannot_be_rewritten_through_the_record_that_carries_it():
    """The stamp is frozen, so an audit trail cannot be edited in place.

    `AttributeError` rather than `dataclasses.FrozenInstanceError` exactly:
    the entry says "frozen record" and does not prescribe the mechanism, and
    every mechanism that is actually frozen — a frozen dataclass, a
    `NamedTuple`, `__slots__` with no setter — raises `AttributeError` or a
    subclass of it. `FrozenInstanceError` is one such subclass, so an
    implementation using it still passes.
    """
    stamp = api.AuditStamp("2026-01-15", OVERRIDE_REASON)
    with pytest.raises(AttributeError):
        stamp.reason = OTHER_REASON
    assert stamp.reason == OVERRIDE_REASON
    assert issubclass(dataclasses.FrozenInstanceError, AttributeError)


# --- The order's audit trail: pinned, then read for its reason ------------


def test_the_trail_still_reports_event_status_as_of_and_sku_per_entry():
    """Pinned field by field, in the order a reader checks them.

    The trail is public and the restructuring moves its storage behind a
    stamp, so the four field names a caller reads may not move. The figures
    come first; the last assertion is what fails on the base tree, and it
    says the recorded pair is exactly what the stamp normalizes it to.
    """
    order = build_order()
    order.place(as_of="t1")
    order.cancel_line("SKU-3", as_of="t2")
    order.cancel(as_of="t3")
    trail = order.transitions()
    assert isinstance(trail, tuple)
    assert [entry.event for entry in trail] == [
        "placed",
        "line_cancelled",
        "cancelled",
    ]
    assert [entry.status for entry in trail] == ["placed", "placed", "cancelled"]
    assert [entry.as_of for entry in trail] == ["t1", "t2", "t3"]
    assert [entry.sku for entry in trail] == [None, "SKU-3", None]
    assert [entry.reason for entry in trail] == [None, None, None]


def test_place_and_cancel_with_no_as_of_still_record_an_undated_entry():
    """Zero-argument `place()`/`cancel()` are why the stamp accepts a
    missing date. An entry is still recorded, and its date is `None` rather
    than a clock read or a sentinel.
    """
    order = build_order()
    order.place()
    order.cancel()
    trail = order.transitions()
    assert [entry.event for entry in trail] == ["placed", "cancelled"]
    assert all(entry.as_of is None for entry in trail)
    assert all(entry.reason is None for entry in trail)


def test_a_refund_reason_is_readable_on_the_trail_and_not_only_on_the_note():
    """The second reader the consolidation buys.

    Today a refund's reason reaches the returned `CreditNote` and nothing
    else; the stamp on the trail entry carries it too, so an order holds the
    reason for its own refund without the caller having kept the note. The
    note's own fields are pinned first — nothing that reads a note changes.
    """
    order = build_order()
    order.place()
    order.cancel_line("SKU-3")
    note = order.refund_line("SKU-3", reason="customer return", as_of="t9")
    assert note.sku == "SKU-3"
    assert note.reason == "customer return"
    assert note.as_of == "t9"
    entry = order.transitions()[-1]
    assert entry.event == "line_refunded"
    assert entry.sku == "SKU-3"
    assert entry.as_of == "t9"
    assert entry.reason == "customer return"


def test_a_refund_reason_reaches_the_note_and_the_trail_identically():
    """One stamp, so the two readers cannot disagree.

    Both get the stripped text, which is what every other reason in `sales`
    is stored as. Asserting the two are equal as well as equal to the
    expected string catches an implementation that stamps the trail and
    passes the raw argument to the note.
    """
    order = build_order()
    order.place()
    order.cancel_line("SKU-3")
    note = order.refund_line("SKU-3", reason=f"  {OTHER_REASON}  ")
    entry = order.transitions()[-1]
    assert note.reason == OTHER_REASON
    assert entry.reason == OTHER_REASON
    assert note.reason == entry.reason


def test_a_refund_reason_that_says_nothing_is_refused_and_spends_nothing():
    """The refusal must not half-complete the refund.

    A line may be refunded once. If the reason were checked after
    `_refunded_skus` was updated, this call would raise *and* burn the
    single refund that sku is allowed, so the retry below would be rejected
    as a double refund. The retry is the assertion that matters: the
    rejection left the order exactly as it was.

    The sku is already cancelled here, so only one rule can fire. Which
    rejection wins when a blank reason meets a state error is deliberately
    not asserted — the entry does not settle it, and pinning an order it
    does not name would fail a defensible implementation.
    """
    order = build_order()
    order.place()
    order.cancel_line("SKU-3")
    with pytest.raises(ValueError):
        order.refund_line("SKU-3", reason="   ")
    assert [entry.event for entry in order.transitions()] == [
        "placed",
        "line_cancelled",
    ]
    note = order.refund_line("SKU-3", reason=OTHER_REASON)
    assert note.reason == OTHER_REASON
    assert order.transitions()[-1].event == "line_refunded"


# --- The cart: the reason becomes readable --------------------------------


def test_the_reason_for_an_override_is_readable_before_the_sku_is_added():
    """The Task, in one test.

    `override_price` accepts a sku that is not in the cart yet — the module
    docstring says so explicitly — and until now the only reader was
    `overridden_by` on a materialized `LineItem`, which does not exist for
    such a sku. So the cart validated a reason and then put it somewhere
    nothing could reach. `override_reason` answers for the sku either way,
    and the assertions bracket the `add` to show the answer does not depend
    on it.
    """
    cart = build_cart()
    cart.override_price("SKU-1", OVERRIDE_PRICE, OVERRIDE_REASON)
    assert cart.lines == []
    assert cart.override_reason("SKU-1") == OVERRIDE_REASON
    cart.add("SKU-1")
    assert cart.override_reason("SKU-1") == OVERRIDE_REASON


def test_override_reason_is_none_for_a_sku_with_no_override():
    """`None`, for a sku in the cart without an override and for a sku the
    catalog has never heard of.

    An unknown sku has no override, so `None` is the truthful answer and
    `UnknownProduct` would be a reader inventing a validation its writer
    already performs. `note_for` set that precedent for notes; a reader that
    raised here would make the two halves of the same idea disagree.
    """
    cart = build_cart()
    cart.add("SKU-1")
    cart.add("SKU-2")
    cart.override_price("SKU-2", api.Money("100.00"), OTHER_REASON)
    assert cart.override_reason("SKU-1") is None
    assert cart.override_reason("SKU-404") is None
    assert cart.override_reason("SKU-2") == OTHER_REASON


def test_a_materialized_line_still_prices_and_explains_the_override():
    """`LineItem` pinned: `override_price`, `overridden_by`, `line_total`.

    Nothing that reads a line changes. 199.99 at quantity 3 is 599.97, which
    a line still pricing from the 250.00 catalog price (750.00), or applying
    the override once (199.99), or truncating the multiply, all miss. The
    final assertion is the new one: the line and the reader return the same
    reason, because both read one stamp.
    """
    cart = build_cart()
    cart.add("SKU-1", 3)
    cart.override_price("SKU-1", OVERRIDE_PRICE, OVERRIDE_REASON)
    line = cart.lines[0]
    assert line.override_price == OVERRIDE_PRICE
    assert line.overridden_by == OVERRIDE_REASON
    assert line.line_total == api.Money("599.97")
    assert cart.override_reason("SKU-1") == line.overridden_by


def test_the_reason_is_stored_stripped_for_both_readers():
    """Stripping is the behaviour `override_price` already had; the point
    here is that the new reader reports the same text as the old one rather
    than the raw argument.
    """
    cart = build_cart()
    cart.add("SKU-1")
    cart.override_price("SKU-1", OVERRIDE_PRICE, f"   {OVERRIDE_REASON}   ")
    assert cart.override_reason("SKU-1") == OVERRIDE_REASON
    assert cart.lines[0].overridden_by == OVERRIDE_REASON


@pytest.mark.parametrize("blank", BLANK_REASONS)
def test_override_price_still_refuses_a_blank_reason_and_stores_nothing(blank):
    """Today's exception type, `ValueError`, and no override behind it.

    The second assertion is only askable because the reader exists: before
    it, "rejected the call" and "rejected the call but kept the price" were
    indistinguishable for a sku not yet in the cart.
    """
    cart = build_cart()
    with pytest.raises(ValueError):
        cart.override_price("SKU-1", OVERRIDE_PRICE, blank)
    assert cart.override_reason("SKU-1") is None


def test_override_price_still_refuses_a_non_money_price_with_type_error():
    """`TypeError` on the price, where the reason earns `ValueError`.

    The two exception types are the one place this method distinguishes a
    wrong type from a wrong value, and the consolidation must not flatten
    them: a caller catching `TypeError` around a price conversion would stop
    seeing it.
    """
    cart = build_cart()
    with pytest.raises(TypeError):
        cart.override_price("SKU-1", "199.99", OVERRIDE_REASON)
    assert cart.override_reason("SKU-1") is None


def test_override_price_still_refuses_a_currency_mismatch():
    """`CurrencyMismatch`, the package-wide answer, and nothing stored.

    The reason here is valid, so this test separates "the reason was
    accepted" from "the override was recorded": a valid reason stamped
    before the currency check must not survive the currency check failing.
    """
    cart = build_cart()
    cart.add("SKU-1")
    with pytest.raises(api.CurrencyMismatch):
        cart.override_price("SKU-1", api.Money("199.99", "EUR"), OVERRIDE_REASON)
    assert cart.override_reason("SKU-1") is None
    assert cart.lines[0].overridden_by is None
    assert cart.lines[0].line_total == api.Money("250.00")


def test_removing_a_line_drops_its_override_and_its_reason():
    """`remove` is a full removal, so a later `add` of the same sku must not
    resurrect a manager's price or the reason given for it. The reader makes
    the second half of that checkable.
    """
    cart = build_cart()
    cart.add("SKU-1")
    cart.override_price("SKU-1", OVERRIDE_PRICE, OVERRIDE_REASON)
    cart.remove("SKU-1")
    assert cart.override_reason("SKU-1") is None
    cart.add("SKU-1")
    assert cart.override_reason("SKU-1") is None
    assert cart.lines[0].override_price is None
    assert cart.lines[0].line_total == CATALOG_PRICE


def test_a_merged_override_brings_the_other_carts_price_and_reason_together():
    """`other` wins for a shared sku, and wins as a pair.

    Both carts override the same sku with different prices *and* different
    reasons, which is what makes the test discriminate: a merge that took
    `other`'s price while leaving this cart's reason in place would pass a
    fixture where only one of the two differed.
    """
    cart = build_cart()
    cart.add("SKU-1")
    cart.override_price("SKU-1", api.Money("240.00"), OVERRIDE_REASON)
    incoming = build_cart()
    incoming.add("SKU-1")
    incoming.override_price("SKU-1", OVERRIDE_PRICE, OTHER_REASON)
    cart.merge(incoming)
    assert cart.override_reason("SKU-1") == OTHER_REASON
    line = cart.lines[0]
    assert line.override_price == OVERRIDE_PRICE
    assert line.overridden_by == OTHER_REASON


# --- Segment history: same rule, its own field names ----------------------


def test_segment_history_keeps_its_own_four_field_names():
    """`SegmentTransition` does not become a stamp carrier.

    `segment_history` is public and callers read `.from_segment`,
    `.to_segment`, `.as_of` and `.reason`, so the names stay where they are
    and only the rule about what a reason may be moved. The last assertion
    is the one that fails on the base tree: the recorded pair is exactly
    what the stamp normalizes it to.
    """
    book = build_book()
    book.change_segment("C-1", "wholesale", as_of="2026-01-15", reason="volume deal")
    history = book.segment_history("C-1")
    assert isinstance(history, tuple)
    assert len(history) == 1
    assert history[0].from_segment == "retail"
    assert history[0].to_segment == "wholesale"
    assert history[0].as_of == "2026-01-15"
    assert history[0].reason == "volume deal"
    stamp = api.AuditStamp("2026-01-15", "volume deal")
    assert (history[0].as_of, history[0].reason) == (stamp.as_of, stamp.reason)


def test_change_segment_still_requires_a_date_and_still_refuses_a_no_op():
    """Two refusals the consolidation must not absorb.

    A stamp accepts `as_of=None` on purpose, because `place()` has
    zero-argument call sites; whether a date is mandatory is a property of
    the call site. So `change_segment` keeps its own `TypeError`, and a
    no-op transition keeps its `ValueError`. Neither refusal records
    anything, and the surviving entry's pair is the stamp's.
    """
    book = build_book()
    with pytest.raises(TypeError):
        book.change_segment("C-1", "wholesale", as_of=None)
    book.change_segment("C-1", "wholesale", as_of="2026-01-15")
    with pytest.raises(ValueError):
        book.change_segment("C-1", "wholesale", as_of="2026-06-01")
    history = book.segment_history("C-1")
    assert len(history) == 1
    assert (history[0].as_of, history[0].reason) == (
        api.AuditStamp("2026-01-15").as_of,
        api.AuditStamp("2026-01-15").reason,
    )


@pytest.mark.parametrize("blank", BLANK_REASONS)
def test_change_segment_refuses_a_reason_that_says_nothing(blank):
    """The third call site answering the same way as the other two.

    This is the behavioural evidence that the rule has one definition:
    before the stamp, `override_price` refused `"   "` and `change_segment`
    recorded it, for no reason other than that each site wrote the rule out
    for itself. The refusal must also leave the book untouched — the
    customer's segment and the history both — since a check after the
    `dataclasses.replace` would change a segment on a call that raised.
    """
    book = build_book()
    with pytest.raises(ValueError):
        book.change_segment("C-1", "wholesale", as_of="2026-01-15", reason=blank)
    assert book.get("C-1").segment == "retail"
    assert book.segment_history("C-1") == ()


def test_change_segment_records_the_reason_stripped():
    """Stripping was `override_price`'s behaviour and is now every site's.

    The base tree records the raw argument here, so this is a change, and it
    is the change that makes one rule worth having: a reason read back from
    the history can be compared against a literal without each reader
    re-stripping it.
    """
    book = build_book()
    book.change_segment(
        "C-1", "wholesale", as_of="2026-01-15", reason=f"\t{OTHER_REASON}  "
    )
    assert book.segment_history("C-1")[0].reason == OTHER_REASON
