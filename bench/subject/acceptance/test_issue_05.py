"""M5 — Coupon stacking reaches the engine.

`Coupon.stack_group` names a mutual-exclusion group, `Coupon.to_discount`
carries it down, `Discount.__init__` accepts it keyword-only after `group`,
`DiscountEngine._resolve_groups` resolves stack groups alongside exclusivity
groups, `DiscountEngine.stacked(subtotal, context, limit)` applies at most
`limit` discounts, `CouponBook.stack_plan` reports the plan, and
`PricingContext.stack_limit` reaches `Order.discount_total`.

Amounts here are deliberately not round — 17.35 against 23.40, a coupon worth
12.45, a 9.5% rate — so an implementation that keeps the wrong member of a
group, or ignores the limit, or resolves in the wrong order, reports a
different figure rather than coinciding with the right one. The two figures
this Task must not move (a 50% discount on the standard order, and the
two-discount `explain` of `test_discounts.py`) are pinned against the
existing suite's own numbers, not against a recomputation.
"""

import pytest

from billing import api

M = api.Money


def engine(discounts):
    return api.DiscountEngine(list(discounts))


def build_order(discounts=(), stack_limit=None):
    """The standard two-line order of `tests/acceptance/test_orders.py`.

    Desk 200.00 taxable plus a 50.00 gift card that is not, under a 10%
    US-CA rate: subtotal 250.00, tax 20.00 undiscounted. Copied rather than
    invented so the undiscounted figures this file pins are the figures the
    Subject's own suite already asserts.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", M("200.00"), "furniture"))
    catalog.add(
        api.Product("SKU-3", "Gift card", M("50.00"), "vouchers", taxable=False)
    )
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    customer = api.Customer("C-1", "Ada", "US-CA")
    cart = api.Cart(catalog, customer)
    cart.add("SKU-1")
    cart.add("SKU-3")
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine(list(discounts)),
        tax_calculator=api.TaxCalculator(table),
        stack_limit=stack_limit,
    )
    return api.Order("O-1", cart, pricing)


def test_the_default_stack_group_reproduces_todays_explain_exactly():
    """The figures pinned here are `test_discounts.py`'s own.

    `test_a_discount_with_no_group_behaves_exactly_as_before` asserts this
    engine explains as `[("voucher", 100.00), ("spring", 10.00)]` — the
    voucher first at rank 10, then 10% of the 100.00 it left behind. A
    `stack_group` that defaulted to anything other than "no group" would
    make these two discounts compete and drop one of them.
    """
    both = engine(
        [api.PercentageDiscount("spring", "10"), api.FixedDiscount("voucher", M("100.00"))]
    )
    assert both.explain(M("200.00"), {}) == [
        ("voucher", M("100.00")),
        ("spring", M("10.00")),
    ]
    assert both.total(M("200.00"), {}) == M("110.00")
    # And the default is readable as the absence of a group, not as a
    # sentinel that happens to behave like one.
    assert api.FixedDiscount("voucher", M("100.00")).stack_group is None
    assert api.Coupon("PLAIN", "fixed", M("5.00")).stack_group is None


def test_a_coupons_stack_group_reaches_the_discount_it_becomes():
    """The gap this Task closes, asserted at the seam where it was open.

    `to_discount` built a `Discount` with no trace of the stacking verdict,
    so the engine could not enforce what the book decided. The group has to
    arrive on the discount for anything downstream to resolve it, and a
    coupon that validated a group and discarded it would otherwise be
    indistinguishable from one that carried it.
    """
    fixed = api.Coupon("WELCOME25", "fixed", M("12.45"), stack_group="welcome")
    percentage = api.Coupon("WELCOME10", "percentage", "10", stack_group="welcome")
    assert fixed.to_discount().stack_group == "welcome"
    assert percentage.to_discount().stack_group == "welcome"
    # Both coupon kinds, because only one of the two branches of
    # `to_discount` would be exercised otherwise.
    assert isinstance(fixed.to_discount(), api.FixedDiscount)
    assert isinstance(percentage.to_discount(), api.PercentageDiscount)


def test_two_discounts_sharing_a_stack_group_yield_one_the_larger_by_amount():
    """Larger by amount, not first by name.

    `beta` is worth 23.40 and `alpha` 17.35, and `alpha` sorts first at the
    same rank. An implementation that kept the first member it saw — the
    shape a naive "one per group" dict gets — would report `alpha`, and an
    implementation that kept the last would report `beta` for the wrong
    reason, which the tie-break test below rules out separately.
    """
    competing = engine(
        [
            api.FixedDiscount("alpha", M("17.35"), stack_group="welcome"),
            api.FixedDiscount("beta", M("23.40"), stack_group="welcome"),
        ]
    )
    assert competing.explain(M("200.00"), {}) == [("beta", M("23.40"))]
    assert competing.total(M("200.00"), {}) == M("23.40")
    # Dropped entirely, not kept as a zero: a group's loser never appears.
    assert [name for name, _amount in competing.explain(M("200.00"), {})] == ["beta"]


def test_equal_amounts_in_a_stack_group_break_by_rank_before_name():
    """`(rank, name)`, which is the order `applicable` already sorts by.

    9.5% of 200.00 is exactly 19.00, the same amount as the flat `zeta`, so
    the tie-break alone decides. `zeta` survives despite sorting last by
    name, because a fixed discount ranks 10 and a percentage one ranks 20.
    An implementation that broke the tie on name only would report `alpha`,
    and one that kept whichever came last in the constructor list would too.
    """
    tied = engine(
        [
            api.PercentageDiscount("alpha", "9.5", stack_group="welcome"),
            api.FixedDiscount("zeta", M("19.00"), stack_group="welcome"),
        ]
    )
    assert tied.explain(M("200.00"), {}) == [("zeta", M("19.00"))]


def test_a_stack_group_and_an_exclusivity_group_are_resolved_independently():
    """Both, not either. Winning one does not exempt a discount from the other.

    `a1` is in `promo` with `a2` and in `welcome` with `a3`, and loses both:
    40.00 beats its 30.00 in `promo`, 50.00 beats it in `welcome`. What
    survives is `a2` (won `promo`, no stack group) and `a3` (won `welcome`,
    no exclusivity group). An implementation that resolved only one of the
    two fields, or that short-circuited on the first grouping a discount
    belongs to, would leave `a1` in.
    """
    mixed = engine(
        [
            api.FixedDiscount("a1", M("30.00"), group="promo", stack_group="welcome"),
            api.FixedDiscount("a2", M("40.00"), group="promo"),
            api.FixedDiscount("a3", M("50.00"), stack_group="welcome"),
        ]
    )
    assert mixed.explain(M("200.00"), {}) == [("a2", M("40.00")), ("a3", M("50.00"))]


def test_winning_a_stack_group_does_not_rescue_a_lost_exclusivity_group():
    """The converse direction, which the previous test cannot see.

    `b1` is the only member of `lonely`, so it wins that group outright —
    and it still loses `promo` to `b2`'s 40.00. An implementation that
    treated "won a stack group" as "survives resolution" would keep it, and
    every assertion above would still pass.
    """
    mixed = engine(
        [
            api.FixedDiscount("b1", M("30.00"), group="promo", stack_group="lonely"),
            api.FixedDiscount("b2", M("40.00"), group="promo"),
        ]
    )
    assert mixed.explain(M("200.00"), {}) == [("b2", M("40.00"))]


def test_stacked_takes_the_first_discounts_in_fold_order_not_the_largest():
    """"At most two discounts" has two readings; this is the implemented one.

    In fold order the three are `aardvark` (fixed, 1.00), `big` (50% of the
    199.00 left, 99.50) and `zz` (25% of the 99.50 left, 24.88 half up).
    The first two are `aardvark` and `big`; the two *largest* are `big` and
    `zz`. The 1.00 discount is deliberately the least valuable and first, so
    the two readings cannot agree.
    """
    three = engine(
        [
            api.FixedDiscount("aardvark", M("1.00")),
            api.PercentageDiscount("big", "50"),
            api.PercentageDiscount("zz", "25"),
        ]
    )
    assert three.explain(M("200.00"), {}) == [
        ("aardvark", M("1.00")),
        ("big", M("99.50")),
        ("zz", M("24.88")),
    ]
    assert three.stacked(M("200.00"), {}, 2) == [
        ("aardvark", M("1.00")),
        ("big", M("99.50")),
    ]
    assert [name for name, _amount in three.stacked(M("200.00"), {}, 1)] == ["aardvark"]


def test_stacked_amounts_are_the_folds_own_so_the_limit_truncates_before_folding():
    """`stacked(..., n)` is `explain(...)[:n]`, value for value.

    The amounts matter as much as the names: truncating *after* the fold
    would report `big` as 99.50 of a subtotal `zz` had already reduced in a
    run where `zz` was dropped, and truncating before it is the only way the
    figures a limited order charges are figures some unlimited order would
    also have charged.
    """
    three = engine(
        [
            api.FixedDiscount("aardvark", M("1.00")),
            api.PercentageDiscount("big", "50"),
            api.PercentageDiscount("zz", "25"),
        ]
    )
    full = three.explain(M("200.00"), {})
    for limit in (0, 1, 2, 3):
        assert three.stacked(M("200.00"), {}, limit) == full[:limit]


def test_a_limit_of_zero_returns_no_discounts_rather_than_every_discount():
    """Zero means none. The falsy-limit bug this kills is a one-liner:
    `if limit:` treats 0 as "no limit configured" and charges the customer
    every discount the policy just forbade.
    """
    three = engine(
        [
            api.FixedDiscount("aardvark", M("1.00")),
            api.PercentageDiscount("big", "50"),
            api.PercentageDiscount("zz", "25"),
        ]
    )
    assert three.stacked(M("200.00"), {}, 0) == []


def test_a_limit_above_the_applicable_count_returns_them_all_without_padding():
    three = engine(
        [
            api.FixedDiscount("aardvark", M("1.00")),
            api.PercentageDiscount("big", "50"),
            api.PercentageDiscount("zz", "25"),
        ]
    )
    assert len(three.stacked(M("200.00"), {}, 99)) == 3
    assert three.stacked(M("200.00"), {}, 99) == three.explain(M("200.00"), {})
    # A discount whose rule refuses is not applicable and is not padded in
    # either: the limit counts discounts applied, not discounts registered.
    gated = engine(
        [
            api.FixedDiscount("aardvark", M("1.00")),
            api.FixedDiscount(
                "refused", M("9.99"), rule=api.Rule("never", lambda context: False)
            ),
        ]
    )
    assert gated.stacked(M("200.00"), {}, 99) == [("aardvark", M("1.00"))]


def test_a_negative_limit_raises_rather_than_slicing_from_the_other_end():
    """`matched[:-1]` is a valid slice and a silent wrong answer: it drops
    the last discount instead of refusing the policy that asked for minus
    one. There is no reading of "apply at most -1 discounts".
    """
    three = engine(
        [
            api.FixedDiscount("aardvark", M("1.00")),
            api.PercentageDiscount("big", "50"),
        ]
    )
    with pytest.raises(ValueError):
        three.stacked(M("200.00"), {}, -1)


def test_stacked_resolves_groups_before_applying_the_limit():
    """A group's loser must not consume one of the limit's slots.

    `alpha` (17.35) loses `welcome` to `beta` (23.40), so the two discounts
    a limit of 2 may apply are `beta` and `ship`. An implementation that
    truncated first would hand the resolver `[alpha, beta]` and charge only
    `beta` — one discount for a limit of two, with `ship` never considered.
    """
    competing = engine(
        [
            api.FixedDiscount("alpha", M("17.35"), stack_group="welcome"),
            api.FixedDiscount("beta", M("23.40"), stack_group="welcome"),
            api.FixedDiscount("ship", M("4.99")),
        ]
    )
    assert competing.stacked(M("200.00"), {}, 2) == [
        ("beta", M("23.40")),
        ("ship", M("4.99")),
    ]


def test_a_blank_or_unhashable_stack_group_is_refused_at_construction():
    """Both constructors, because a rule enforced at one is no rule.

    `""` is falsy and would read as "no group" wherever the code asks
    truthily and as a real group wherever it asks `is not None`; a list
    cannot key the resolver's dict and would raise from inside the fold,
    at whatever later pricing call first reached that coupon.
    """
    with pytest.raises(ValueError):
        api.FixedDiscount("d", M("1.00"), stack_group="")
    with pytest.raises(ValueError):
        api.FixedDiscount("d", M("1.00"), stack_group="   ")
    with pytest.raises(ValueError):
        api.Coupon("BLANK", "fixed", M("1.00"), stack_group="")
    with pytest.raises(TypeError):
        api.Coupon("LIST", "fixed", M("1.00"), stack_group=["welcome"])
    with pytest.raises(TypeError):
        api.PercentageDiscount("d", "10", stack_group=["welcome"])


def test_stack_group_is_keyword_only_after_group():
    """Every subclass calls `super().__init__(name, rule, group=group)`
    positionally, so a third positional parameter would land under `group`
    and silently turn a stack group into an exclusivity group.
    """
    with pytest.raises(TypeError):
        api.Discount("d", None, None, "welcome")
    with pytest.raises(TypeError):
        api.FixedDiscount("d", M("1.00"), None, None, "welcome")
    # Positional `group` still reaches `group`, unchanged.
    assert api.FixedDiscount("d", M("1.00"), None, "promo").group == "promo"
    assert api.FixedDiscount("d", M("1.00"), None, "promo").stack_group is None


def test_stack_plan_reports_each_applicable_code_with_its_group():
    """Order preserved, group attached, and the group is the part of the
    plan the engine could not otherwise know.

    A plan that reported bare codes would be `applicable` under a second
    name, and the caller assembling the engine would still have to reach
    back into the book for every group.
    """
    book = api.CouponBook()
    book.add(api.Coupon("WELCOME10", "percentage", "10", stack_group="welcome"))
    book.add(api.Coupon("WELCOME25", "fixed", M("12.45"), stack_group="welcome"))
    book.add(api.Coupon("SHIP", "fixed", M("4.99")))
    assert book.stack_plan(["WELCOME10", "WELCOME25", "SHIP"]) == (
        ("WELCOME10", "welcome"),
        ("WELCOME25", "welcome"),
        ("SHIP", None),
    )
    # The offered order is kept, not sorted and not the book's insertion
    # order, so a caller can line the plan up against what it asked for.
    assert book.stack_plan(["SHIP", "WELCOME10"]) == (
        ("SHIP", None),
        ("WELCOME10", "welcome"),
    )


def test_stack_plan_follows_applicables_own_rules_for_unknown_and_solo_codes():
    """Unknown raises, a lone non-stackable code is planned, and a
    non-stackable code beside any other is excluded entirely — the three
    rules `CouponBook.applicable` documents with worked examples. A
    `stack_plan` that re-derived them would be a second place for them to
    drift from.
    """
    book = api.CouponBook()
    book.add(api.Coupon("SOLO", "fixed", M("1.00"), stackable=False))
    book.add(api.Coupon("SHIP", "fixed", M("4.99")))
    with pytest.raises(api.UnknownCoupon):
        book.stack_plan(["NOPE", "SHIP"])
    assert book.stack_plan(["SOLO"]) == (("SOLO", None),)
    assert book.stack_plan(["SOLO", "SHIP"]) == (("SHIP", None),)
    assert book.applicable(["SOLO", "SHIP"]) == ("SHIP",)


def test_a_books_stack_plan_priced_through_the_engine_applies_one_per_group():
    """End to end: the book's verdict now decides what the engine charges.

    The whole plan is handed to the engine, competing codes included, and
    the engine resolves `welcome`: the 12.45 flat `WELCOME25` beats
    `WELCOME10`'s 10% of 100.00, and `SHIP` is ungrouped and survives. Before
    this Task the same plan charged all three, because the discounts carried
    no trace of the group.
    """
    book = api.CouponBook()
    book.add(api.Coupon("WELCOME10", "percentage", "10", stack_group="welcome"))
    book.add(api.Coupon("WELCOME25", "fixed", M("12.45"), stack_group="welcome"))
    book.add(api.Coupon("SHIP", "fixed", M("4.99")))
    plan = book.stack_plan(["WELCOME10", "WELCOME25", "SHIP"])
    priced = engine([book.get(code).to_discount() for code, _group in plan])
    # SHIP and WELCOME25 are both rank 10, so name order puts SHIP first;
    # WELCOME25 then takes its flat 12.45 off the 95.01 remaining.
    assert priced.explain(M("100.00"), {"subtotal": M("100.00")}) == [
        ("SHIP", M("4.99")),
        ("WELCOME25", M("12.45")),
    ]
    assert priced.total(M("100.00"), {"subtotal": M("100.00")}) == M("17.44")


def test_an_order_with_no_stack_limit_charges_todays_discount():
    """The pinned figure is `test_orders.py`'s own.

    `test_discount_reduces_the_taxable_base_proportionally` asserts 125.00
    discount, 10.00 tax and 135.00 total for a 50% discount on this order.
    The limit defaults to absent and the unlimited path is the call that was
    always made, so all three are unchanged.
    """
    order = build_order([api.PercentageDiscount("half", "50")])
    assert order.stack_limit is None
    assert order.discount_total == M("125.00")
    assert order.tax_total == M("10.00")
    assert order.total == M("135.00")


def test_an_orders_stack_limit_reaches_the_discount_and_the_tax_behind_it():
    """A limit is not cosmetic: it moves the taxable base too.

    Unlimited, this order charges the 30.00 voucher and then 50% of the
    220.00 remaining — 140.00 of discount, leaving the desk's 80% share and
    8.80 of tax. Limited to one discount it charges the voucher alone:
    30.00, of which the desk bears 24.00, so 176.00 is taxable and tax is
    17.60. Every one of those six figures moves, so an implementation that
    read the limit and then summed the unlimited breakdown fails here.
    """
    discounts = [api.FixedDiscount("voucher", M("30.00")), api.PercentageDiscount("half", "50")]
    unlimited = build_order(discounts)
    assert unlimited.discount_total == M("140.00")
    assert unlimited.tax_total == M("8.80")
    assert unlimited.total == M("118.80")

    limited = build_order(discounts, stack_limit=1)
    assert limited.stack_limit == 1
    assert limited.discount_total == M("30.00")
    assert limited.tax_total == M("17.60")
    assert limited.total == M("237.60")


def test_an_order_limited_to_zero_discounts_charges_the_undiscounted_total():
    """And a limit above the number of discounts charges all of them.

    Zero is the case a truthiness test gets wrong in the customer's favour
    and the merchant's loss; the undiscounted figures are `test_orders.py`'s
    `test_totals_without_discounts` — 250.00, 20.00, 270.00 — reached with
    two discounts registered and none permitted.
    """
    discounts = [api.FixedDiscount("voucher", M("30.00")), api.PercentageDiscount("half", "50")]
    none_allowed = build_order(discounts, stack_limit=0)
    assert none_allowed.discount_total == M("0.00")
    assert none_allowed.tax_total == M("20.00")
    assert none_allowed.total == M("270.00")
    generous = build_order(discounts, stack_limit=5)
    assert generous.discount_total == M("140.00")
    assert generous.breakdown() == {
        "subtotal": M("250.00"),
        "discount": M("140.00"),
        "tax": M("8.80"),
        "total": M("118.80"),
    }


def test_the_limit_travels_with_the_pricing_context_through_reprice():
    """`reprice` exists to price the same cart under a different policy, so
    the limit has to be part of the policy rather than state on the order.
    A limit held on the `Order` would be lost here, and the repriced order
    would quietly charge every discount again.
    """
    discounts = [api.FixedDiscount("voucher", M("30.00")), api.PercentageDiscount("half", "50")]
    unlimited = build_order(discounts)
    limited_pricing = api.PricingContext(
        discount_engine=unlimited.pricing.discount_engine,
        tax_calculator=unlimited.pricing.tax_calculator,
        stack_limit=1,
    )
    repriced = unlimited.reprice("O-2", limited_pricing)
    assert repriced.stack_limit == 1
    assert repriced.discount_total == M("30.00")
    # The original order is untouched by the repricing.
    assert unlimited.stack_limit is None
    assert unlimited.discount_total == M("140.00")


def test_all_four_subclasses_carry_a_stack_group_not_only_the_two_a_coupon_builds():
    """An asymmetric constructor is a trap for a later Task.

    `Coupon.to_discount` builds only `FixedDiscount` and
    `PercentageDiscount`, so those two are the ones the rest of this file
    exercises. The tiered and bundle constructors take the keyword through
    their own `__init__` and hand it to `super()`, and a subclass that
    accepted it and dropped it would report `None` here while passing every
    other criterion.
    """
    tiered = api.TieredDiscount("tiered", [(0, "5")], stack_group="welcome")
    bundle = api.BundleDiscount("bundle", "furniture", 2, M("10.00"),
                                stack_group="welcome")
    assert tiered.stack_group == "welcome"
    assert bundle.stack_group == "welcome"
    # And the base's other fields still arrive, which is what distinguishes
    # "the keyword is wired" from "the super() call happens at all".
    assert (tiered.name, tiered.group) == ("tiered", None)
    assert (bundle.name, bundle.group) == ("bundle", None)
    assert api.TieredDiscount("g", [(0, "5")], group="promo").group == "promo"


def test_a_tiered_discount_is_resolved_by_its_stack_group_like_any_other():
    """The group a subclass carries has to reach `_resolve_groups`, not just
    the attribute. At a quantity of 2 the tiered discount takes 5% of
    250.00, which is 12.50, against the fixed 17.35 sharing its group — so
    the fixed one wins and the tiered one does not appear at all.
    """
    breakdown = engine(
        [
            api.FixedDiscount("alpha", M("17.35"), stack_group="welcome"),
            api.TieredDiscount("tiered", [(0, "5")], stack_group="welcome"),
        ]
    ).explain(M("250.00"), {"quantity": 2})
    assert breakdown == [("alpha", M("17.35"))]
