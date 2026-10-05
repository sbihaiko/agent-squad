"""M4 — The credit limit is enforced where the charge happens.

`Customer.credit_limit` and `within_credit_limit` already existed and
nothing read them, so a customer with a $150 limit could place a $194.58
order. This Task makes the limit load-bearing: `Customer.available_credit`,
a stricter `Customer.__post_init__`, `CustomerBook.exposure`,
`Order.credit_check`, a guard inside `Order.place`, and
`RevenueReport.over_limit_orders`.

Every figure below is an unrounded-looking number on purpose. The limit is
$150.00 and the order that breaches it totals $194.58 out of a 137.50 +
42.25 cart at 8.25% tax, so an implementation that compares the subtotal
(179.75) instead of the total, or the tax-exclusive figure, or that clamps
an overage at zero, produces a different number rather than the same one.
The at-the-limit case is pinned on $148.84 — a total the pipeline actually
produces — because an inclusive boundary can only be tested by an amount
that sits exactly on it.
"""

import pytest

from billing import api

# 8.25%, so a line's tax is never a round figure and the order total is not
# reachable by ignoring the rate: 137.50 * 0.0825 = 11.34375 -> 11.34 and
# 42.25 * 0.0825 = 3.485625 -> 3.49, both under tax.py's bankers policy.
TAX_RATE = "8.25"

# 137.50 + 42.25 = 179.75 subtotal; 11.34 + 3.49 = 14.83 tax; 194.58 total.
FULL_TOTAL = api.Money("194.58")
# SKU-1 alone: 137.50 + 11.34 = 148.84.
DESK_TOTAL = api.Money("148.84")
# SKU-2 alone: 42.25 + 3.49 = 45.74.
LAMP_TOTAL = api.Money("45.74")

LIMIT = api.Money("150.00")


def catalog():
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("137.50"), "furniture"))
    catalog.add(api.Product("SKU-2", "Lamp", api.Money("42.25"), "furniture"))
    catalog.add(
        api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers", taxable=False)
    )
    return catalog


def pricing():
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", TAX_RATE))
    return api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(table),
    )


def customer(customer_id="C-1", limit=LIMIT):
    return api.Customer(customer_id, "Ada", "US-CA", credit_limit=limit)


def order(order_id, buyer, skus):
    cart = api.Cart(catalog(), buyer)
    for sku in skus:
        cart.add(sku)
    return api.Order(order_id, cart, pricing()), cart


def test_the_credit_limit_refusal_is_its_own_exception():
    """Exported, and not the same class as `CurrencyMismatch`.

    The two failures have different remedies — split the order or raise the
    limit, against fix the configuration — so a caller has to be able to
    catch one without the other. An implementation that reused
    `CurrencyMismatch`, or `ValueError`, would pass every other assertion in
    this file that only checks that *something* was raised.
    """
    assert issubclass(api.CreditLimitExceeded, Exception)
    assert not issubclass(api.CreditLimitExceeded, api.CurrencyMismatch)
    assert not issubclass(api.CurrencyMismatch, api.CreditLimitExceeded)


def test_a_customer_with_no_limit_places_every_order_with_the_pipeline_untouched():
    """`None` still means unlimited, and the guard adds nothing to that path.

    This is the criterion that pins the Task as additive. A guard written as
    `limit or Money("0.00")` would refuse every unlimited customer's order,
    and that implementation passes every over-limit assertion below.
    """
    placed, _cart = order("O-1", customer(limit=None), ["SKU-1", "SKU-2"])
    assert placed.credit_check() is True
    placed.place()
    assert placed.status == "placed"
    assert placed.breakdown() == {
        "subtotal": api.Money("179.75"),
        "discount": api.Money("0.00"),
        "tax": api.Money("14.83"),
        "total": FULL_TOTAL,
    }
    assert [t.event for t in placed.transitions()] == ["placed"]


def test_available_credit_reports_the_headroom_left_against_a_charge():
    """150.00 - 148.84 = 1.16, and the reader is the subtraction's result.

    Not a bool, and not the limit: an implementation that returned the limit
    regardless of the charge satisfies "returns a Money" and nothing else.
    """
    assert customer().available_credit(DESK_TOTAL) == api.Money("1.16")
    assert customer().available_credit(api.Money("0.00")) == LIMIT


def test_available_credit_returns_the_overage_rather_than_clamping_at_zero():
    """150.00 - 194.58 = -44.58, and the sign is the point.

    A clamp would report the same zero for "exactly at the limit" and for
    "$44.58 short", which are two different facts with two different
    remedies. The second assertion is what kills the clamp: it would make
    both of these equal.
    """
    assert customer().available_credit(FULL_TOTAL) == api.Money("-44.58")
    assert customer().available_credit(FULL_TOTAL) != api.Money("0.00")
    assert customer().available_credit(LIMIT) == api.Money("0.00")


def test_available_credit_on_an_unlimited_customer_raises():
    """"Unlimited minus $5" is not a `Money`.

    Every sentinel lies in a different direction — a zero reads as no
    headroom, the charge itself reads as exactly enough — so the refusal is
    the honest answer, and `within_credit_limit` remains the reader that
    does answer for an unlimited customer.
    """
    unlimited = customer(limit=None)
    with pytest.raises(ValueError):
        unlimited.available_credit(api.Money("5.00"))
    assert unlimited.within_credit_limit(api.Money("5.00")) is True


def test_available_credit_across_currencies_raises_moneys_own_mismatch():
    """A charge in another currency is not a credit verdict at all.

    `Money`'s subtraction reports it, so the error a caller sees is the same
    one any other cross-currency arithmetic in the package produces.
    """
    with pytest.raises(api.CurrencyMismatch):
        customer().available_credit(api.Money("10.00", "EUR"))


def test_a_limit_finer_than_its_currencys_minor_unit_is_refused():
    """A limit of 50.005 USD is a boundary no charge can sit on.

    `within_credit_limit` promises an inclusive boundary, and the pipeline
    quantizes to the currency's registered minor unit, so such a limit is
    inclusive of nothing. The JPY case is what makes this the *registered*
    exponent rather than two hardcoded places: 150.5 yen is as unreachable
    as 50.005 dollars, and 150 yen is fine.
    """
    with pytest.raises(ValueError):
        customer(limit=api.Money("50.005"))
    with pytest.raises(ValueError):
        customer(limit=api.Money("150.5", "JPY"))
    assert customer(limit=api.Money("150", "JPY")).credit_limit == api.Money(
        "150", "JPY"
    )
    # The ordinary case is untouched, including a limit of exactly zero,
    # which is a different fact from no limit at all.
    assert customer(limit=api.Money("0.00")).credit_limit == api.Money("0.00")


def test_a_limit_must_carry_a_usable_currency_code():
    """An empty currency code is comparable with nothing.

    The existing type and sign checks stay, asserted here so a rewritten
    `__post_init__` cannot trade one validation for another.
    """
    with pytest.raises(ValueError):
        customer(limit=api.Money("150.00", ""))
    with pytest.raises(TypeError):
        customer(limit="150.00")
    with pytest.raises(ValueError):
        customer(limit=api.Money("-1.00"))


def test_place_refuses_an_order_over_the_limit_and_names_both_figures():
    """194.58 against a 150.00 limit, and the message carries both.

    The figures are asserted because a refusal a caller cannot act on is
    half a feature: "over limit" alone does not say by how much. 179.75
    appears in neither number, which is what kills an implementation that
    checked the subtotal instead of the total.
    """
    over, _cart = order("O-1", customer(), ["SKU-1", "SKU-2"])
    with pytest.raises(api.CreditLimitExceeded) as raised:
        over.place()
    message = str(raised.value)
    assert "194.58" in message
    assert "150.00" in message
    assert "179.75" not in message


def test_place_succeeds_exactly_at_the_limit_because_the_boundary_is_inclusive():
    """148.84 against a 148.84 limit places; a 148.83 limit refuses it.

    The pair is the assertion. An implementation using `<` rather than `<=`
    passes the first half of every other test in this file, since every
    other breach is over by dollars rather than by a cent.
    """
    at_limit, _cart = order("O-2", customer(limit=DESK_TOTAL), ["SKU-1"])
    at_limit.place()
    assert at_limit.status == "placed"

    a_cent_short, _cart = order("O-3", customer(limit=api.Money("148.83")), ["SKU-1"])
    with pytest.raises(api.CreditLimitExceeded):
        a_cent_short.place()


def test_a_refused_place_leaves_a_draft_with_nothing_in_the_audit_trail():
    """A rejected transition is not a transition.

    An implementation that set the status and then validated, or that
    recorded the attempt, would leave the order unplaceable or make
    `transitions()` a log of tries rather than the record of what happened.
    Both are pinned: the order is still a draft, the trail is empty, and a
    later `place` that *does* fit still works.
    """
    refused, cart = order("O-1", customer(), ["SKU-1", "SKU-2"])
    with pytest.raises(api.CreditLimitExceeded):
        refused.place()
    assert refused.status == "draft"
    assert refused.transitions() == ()

    cart.remove("SKU-2")
    refused.place()
    assert refused.status == "placed"
    assert [t.event for t in refused.transitions()] == ["placed"]


def test_an_already_placed_order_reports_the_transition_not_the_credit():
    """The status check comes before the credit check.

    The order is placed while it fits, then pushed over the limit by adding
    to its cart — `Order` reads its lines on demand, so the total moves.
    Placing again has to report `InvalidTransition`: there was no transition
    available to refuse on credit grounds, and reporting the limit would
    send a caller to fix the wrong thing.
    """
    placed, cart = order("O-2", customer(limit=DESK_TOTAL), ["SKU-1"])
    placed.place()
    cart.add("SKU-2")
    assert placed.total == FULL_TOTAL
    with pytest.raises(api.InvalidTransition):
        placed.place()
    assert placed.status == "placed"


def test_credit_check_reports_the_verdict_on_a_draft_without_placing_it():
    """A verdict, not a refusal, and not a side effect.

    `credit_check` is the reader a caller consults *before* deciding to
    place, so it must neither raise nor change status. Both verdicts are
    asserted against the same cart contents, one limit apart, so an
    implementation that returned a constant fails.
    """
    over, _cart = order("O-1", customer(), ["SKU-1", "SKU-2"])
    assert over.credit_check() is False
    assert over.status == "draft"
    assert over.transitions() == ()

    fits, _cart = order("O-4", customer(limit=api.Money("200.00")), ["SKU-1", "SKU-2"])
    assert fits.credit_check() is True
    assert fits.status == "draft"


def test_credit_check_still_answers_for_an_already_placed_order():
    """Which is what lets a report ask who is over the limit *now*.

    The order fitted when it was placed and does not fit after its cart
    grew. An implementation that only checked drafts, or that cached the
    verdict at `place`, would answer `True` here.
    """
    placed, cart = order("O-2", customer(limit=DESK_TOTAL), ["SKU-1"])
    placed.place()
    assert placed.credit_check() is True
    cart.add("SKU-2")
    assert placed.credit_check() is False
    assert placed.status == "placed"


def test_a_cross_currency_limit_raises_currency_mismatch_not_a_credit_error():
    """`Money`'s own comparison reports it, on both readers.

    A limit in EUR against a USD total is a configuration error, not a
    verdict: swallowing it into a `False` would refuse the order and tell
    the caller to raise a limit that is not the problem.
    """
    foreign = customer(limit=api.Money("150.00", "EUR"))
    checked, _cart = order("O-1", foreign, ["SKU-1", "SKU-2"])
    with pytest.raises(api.CurrencyMismatch):
        checked.credit_check()
    with pytest.raises(api.CurrencyMismatch):
        checked.place()
    assert checked.status == "draft"


def test_cancelling_a_line_brings_an_over_limit_order_back_inside():
    """194.58 - 45.74 = 148.84, which fits a 150.00 limit.

    The verdict is read afresh rather than cached, so the order becomes
    placeable. Asserting the total as well as the verdict is what ties the
    verdict to the pipeline: an implementation that ignored cancellations
    would still answer `False` here.
    """
    over, _cart = order("O-1", customer(), ["SKU-1", "SKU-2"])
    assert over.credit_check() is False
    over.cancel_line("SKU-2")
    assert over.total == DESK_TOTAL
    assert over.credit_check() is True
    over.place()
    assert over.status == "placed"
    assert [t.event for t in over.transitions()] == ["line_cancelled", "placed"]


def test_exposure_sums_only_the_named_customers_orders():
    """148.84 + 45.74 = 194.58 for C-1; C-2's order is not in the sum.

    C-2's order is in the same iterable and its total (50.00) is absent
    from the result, which is what kills an implementation that sums
    everything it is handed.
    """
    book = api.CustomerBook()
    ada = customer("C-1")
    bob = customer("C-2")
    book.add(ada)
    book.add(bob)
    mine, _cart = order("O-2", ada, ["SKU-1"])
    also_mine, _cart = order("O-3", ada, ["SKU-2"])
    theirs, _cart = order("O-9", bob, ["SKU-3"])
    orders = [mine, also_mine, theirs]
    assert book.exposure("C-1", orders) == FULL_TOTAL
    assert book.exposure("C-2", orders) == api.Money("50.00")


def test_exposure_skips_a_cancelled_order_and_counts_a_draft():
    """A cancelled order charges nobody; a draft is still owed.

    Pinned as a pair because "skips cancelled" and "counts draft" are the
    two halves of one decision about what exposure means, and an
    implementation that filtered on `status == "placed"` would satisfy the
    first and fail the second.
    """
    book = api.CustomerBook()
    ada = customer("C-1")
    book.add(ada)
    draft, _cart = order("O-2", ada, ["SKU-1"])
    cancelled, _cart = order("O-3", ada, ["SKU-2"])
    cancelled.cancel()
    assert draft.status == "draft"
    assert book.exposure("C-1", [draft, cancelled]) == DESK_TOTAL
    # And the cancelled order on its own is zero, not its 45.74 total.
    assert book.exposure("C-1", [cancelled]) == api.Money("0.00")


def test_exposure_counts_one_charge_per_element_of_the_set():
    """The same order handed over twice is two charges.

    "What counts as one" is a decision, not an accident: this method is
    given a sequence and deduplicating it would disagree with the caller
    about their own data. 148.84 * 2 = 297.68.
    """
    book = api.CustomerBook()
    ada = customer("C-1")
    book.add(ada)
    twice, _cart = order("O-2", ada, ["SKU-1"])
    assert book.exposure("C-1", [twice, twice]) == api.Money("297.68")


def test_exposure_over_an_empty_set_is_zero_in_the_limits_currency():
    """And the currency is the limit's, not a default.

    A zero in the wrong currency fails every later comparison against the
    limit with a `CurrencyMismatch` instead of a number, so the currency is
    asserted rather than assumed — `Money`'s equality reads it.
    """
    book = api.CustomerBook()
    book.add(customer("C-1"))
    book.add(api.Customer("C-2", "Bo", "US-CA", credit_limit=api.Money("90", "JPY")))
    assert book.exposure("C-1", []) == api.Money("0.00")
    assert book.exposure("C-2", []) == api.Money("0", "JPY")
    assert book.exposure("C-2", []) != api.Money("0.00", "USD")


def test_exposure_refuses_an_unknown_customer_and_an_unlimited_one():
    """Two refusals, for two reasons.

    An unknown id gets `UnknownCustomer`, matching `get`, because a zero
    would look like an answer about a customer this book has never seen. An
    unlimited customer gets `ValueError`: there is no limit currency in
    which to express the sum, the same reason `available_credit` refuses.
    """
    book = api.CustomerBook()
    book.add(customer("C-1"))
    book.add(customer("C-2", limit=None))
    with pytest.raises(api.UnknownCustomer):
        book.exposure("C-404", [])
    with pytest.raises(ValueError):
        book.exposure("C-2", [])


def test_over_limit_orders_names_ids_sorted_ascending():
    """Ids, ascending, regardless of construction order.

    The report is built with O-7 before O-2 so construction order and
    ascending order disagree; an unsorted implementation returns them the
    other way round. The ids are asserted rather than the orders because an
    implementation returning `Order` objects would satisfy a length check.
    """
    ada = customer("C-1")
    over_one, _cart = order("O-7", ada, ["SKU-1", "SKU-2"])
    over_two, _cart = order("O-2", ada, ["SKU-1", "SKU-2"])
    fits, _cart = order("O-4", customer("C-2", limit=api.Money("200.00")), ["SKU-1"])
    report = api.RevenueReport([over_one, over_two, fits])
    assert report.over_limit_orders() == ("O-2", "O-7")


def test_over_limit_orders_is_empty_when_every_customer_is_unlimited():
    """The report adds no policy about a missing limit.

    An implementation treating `None` as a zero limit would name both of
    these orders, and the empty tuple is also what distinguishes "nobody is
    over" from "nothing was measured" — the report's other figures confirm
    the orders are present.
    """
    unlimited = customer("C-1", limit=None)
    first, _cart = order("O-1", unlimited, ["SKU-1", "SKU-2"])
    second, _cart = order("O-2", unlimited, ["SKU-3"])
    report = api.RevenueReport([first, second])
    assert report.over_limit_orders() == ()
    assert report.gross == api.Money("229.75")  # 179.75 + 50.00


def test_over_limit_orders_excludes_a_cancelled_order_and_tracks_a_cancelled_line():
    """A cancelled order is out; a cancelled line moves a verdict.

    The wholly cancelled order is excluded at construction like every other
    figure this report produces. The surviving order is over the limit until
    its lamp line goes, and the second read reflects that — which is what
    "would fail the check now" means, as against a verdict fixed when the
    report was built.
    """
    ada = customer("C-1")
    survivor, _cart = order("O-2", ada, ["SKU-1", "SKU-2"])
    scrapped, _cart = order("O-7", ada, ["SKU-1", "SKU-2"])
    scrapped.cancel()
    report = api.RevenueReport([survivor, scrapped])
    assert report.over_limit_orders() == ("O-2",)
    survivor.cancel_line("SKU-2")
    assert report.over_limit_orders() == ()


def test_a_segment_change_can_move_the_credit_limit_in_the_same_call():
    """A promotion and the terms that come with it are one event.

    The reader is `book.get(...).credit_limit`, asserted here because a
    limit the book accepted and stored nowhere would be indistinguishable
    from a limit it ignored. The history still records the segment change
    and nothing about the limit, which `segment_history` pins.
    """
    book = api.CustomerBook()
    book.add(customer("C-1"))
    updated = book.change_segment(
        "C-1", "wholesale", as_of="2026-01-15", credit_limit=api.Money("1250.00")
    )
    assert updated.credit_limit == api.Money("1250.00")
    assert book.get("C-1").credit_limit == api.Money("1250.00")
    assert book.get("C-1").segment == "wholesale"
    history = book.segment_history("C-1")
    assert [(t.from_segment, t.to_segment) for t in history] == [
        ("retail", "wholesale")
    ]


def test_a_segment_change_that_omits_the_limit_leaves_it_alone():
    """Omission is not `None`.

    A default of `None` would silently strip every customer's limit on any
    segment change, which is the expensive bug here: the limit stops binding
    and nothing reports it. The third assertion is the other half — `None`
    passed explicitly *does* clear the limit, so the two calls are
    distinguishable.
    """
    book = api.CustomerBook()
    book.add(customer("C-1"))
    book.change_segment("C-1", "wholesale", as_of="2026-01-15")
    assert book.get("C-1").credit_limit == LIMIT

    book.change_segment("C-1", "retail", as_of="2026-02-01", credit_limit=None)
    assert book.get("C-1").credit_limit is None
    assert book.get("C-1").within_credit_limit(api.Money("99999.00")) is True


def test_a_limit_the_book_would_refuse_leaves_the_stored_customer_alone():
    """The replacement goes through `Customer.__post_init__` like any other.

    A limit finer than the currency's minor unit is refused here for the
    same reason it is refused at construction, and the refusal must not
    half-apply: the segment is unchanged, the old limit is intact, and no
    transition was recorded.
    """
    book = api.CustomerBook()
    book.add(customer("C-1"))
    with pytest.raises(ValueError):
        book.change_segment(
            "C-1", "wholesale", as_of="2026-01-15", credit_limit=api.Money("50.005")
        )
    assert book.get("C-1").segment == "retail"
    assert book.get("C-1").credit_limit == LIMIT
    assert book.segment_history("C-1") == ()
