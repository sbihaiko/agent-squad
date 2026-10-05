"""M6 — Why a discount applied is auditable end to end.

`RuleTrace`, `RuleSet.trace` and `evaluate_rule`/`trace_rule` in
`core/rules.py`; `Discount.applies` routed through a single-rule evaluation
plus `Discount.rule_trace` and `DiscountEngine.audit` in `core/discounts.py`;
`RevenueReport.rule_audit()` in `reporting/reports.py`.

The Task's point is that a verdict nobody reached is reported as exactly
that. So every test below that touches a failure asserts the *third* state —
an unset verdict — against the two an implementation is tempted to collapse
it into: `False`, or an exception escaping a method whose job is to report
the failure.

Amounts are 87.00 and 43.50 a unit and the percentages are 12.5 and 7.5, so
the fold lands on 25.625 and 13.45275 — figures a truncating or
half-even implementation gets wrong, and figures that differ from what the
same discounts would take off the *original* subtotal, which is how a row's
amount being read off the fold is falsifiable at all.
"""

import dataclasses

import pytest

from billing import api

# One discount rank apiece and one name apiece, chosen so that (rank, name)
# order differs from name order and from insertion order: by name alone the
# order is alumni, autumn, volume, welcome; by rank it is the two fixed
# discounts, then the percentage, then the tiered one.
SUBTOTAL = api.Money("217.50")     # 2 x 87.00 + 1 x 43.50
QUANTITY = 3                       # two desks and one rug


def retail_only():
    """A rule that declares the key it reads, which is what makes the
    `required_keys` contract observable through a `Discount` at all.
    """
    return api.Rule(
        "retail_only",
        lambda context: context["segment"] == "retail",
        required_keys=("segment",),
    )


def vip_only():
    return api.Rule(
        "vip_only",
        lambda context: context["segment"] == "vip",
        required_keys=("segment",),
    )


def needs_loyalty():
    """Declares a key no pricing context in this file carries, so a context
    assembled by the package itself cannot decide it."""
    return api.Rule(
        "needs_loyalty",
        lambda context: context["loyalty_years"] >= 3,
        required_keys=("loyalty_years",),
    )


def context(segment="retail"):
    return {"subtotal": SUBTOTAL, "quantity": QUANTITY, "segment": segment}


def four_discounts():
    """One applying and one rejected fixed discount, a percentage, a tiered."""
    return [
        api.FixedDiscount("welcome", api.Money("12.50"), rule=retail_only()),
        api.FixedDiscount("alumni", api.Money("30.00"), rule=vip_only()),
        api.PercentageDiscount("autumn", "12.5", rule=retail_only()),
        api.TieredDiscount("volume", [(3, "7.5")]),
    ]


def build_order(order_id, discounts):
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("87.00"), "furniture"))
    catalog.add(api.Product("SKU-2", "Rug", api.Money("43.50"), "rugs"))
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1", 2)
    cart.add("SKU-2")
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine(discounts),
        tax_calculator=api.TaxCalculator(table),
    )
    order = api.Order(order_id, cart, pricing)
    order.place()
    return order


# -- core/rules.py: the trace ------------------------------------------


def test_the_trace_types_are_exported():
    """A capability reachable only through an internal module is not
    surfaced, and every suite here imports the facade and nothing else."""
    assert api.RuleTrace is not None
    assert api.evaluate_rule is not None
    assert api.trace_rule is not None


def test_trace_reports_one_entry_per_rule_in_priority_then_name_order():
    """The order is `explain`'s, not insertion order and not name order.

    `beta` and `gamma` share priority 10 and `alpha` carries 90, so the
    evaluation order is beta, gamma, alpha — which name order (alpha, beta,
    gamma) and insertion order (beta, alpha, gamma) both get wrong. Pinned
    against `explain`'s own keys as well, because the two orderings agreeing
    is the contract rather than a coincidence of this fixture.
    """
    rules = api.RuleSet()
    rules.add(api.Rule("beta", lambda ctx: True, priority=10))
    rules.add(api.Rule("alpha", lambda ctx: False, priority=90))
    rules.add(api.Rule("gamma", lambda ctx: True, priority=10))
    names = [entry.name for entry in rules.trace({})]
    assert names == ["beta", "gamma", "alpha"]
    assert names == list(rules.explain({}))
    assert [entry.verdict for entry in rules.trace({})] == [True, True, False]


def test_a_rule_whose_predicate_raises_is_traced_unset_rather_than_false():
    """A predicate that raised has not decided "no".

    The assertion is on `is None`, not on falsiness: `None` and `False` are
    both falsy, so an implementation that recorded `False` for a raising
    predicate would satisfy every `if not entry.verdict` written against it
    and would tell a reader the rule rejected the context, which it never
    did.
    """
    rules = api.RuleSet()
    rules.add(api.Rule("boom", lambda ctx: 1 / 0))
    entry = rules.trace({})[0]
    assert entry.verdict is None
    assert entry.verdict is not False
    assert entry.missing_keys == ()


def test_trace_does_not_raise_where_explain_and_matching_do():
    """The asymmetry is the reason `trace` exists, in both directions.

    `explain` answers what a context means and must not answer `False` for a
    rule that failed, so it propagates. `trace` answers what happened, and a
    trace that stopped at the first broken rule would hide every rule behind
    it — which is the position a caller reaches for a trace from. So the
    third assertion is the load-bearing one: the rules after `boom` are
    still reported.
    """
    rules = api.RuleSet()
    rules.add(api.Rule("boom", lambda ctx: 1 / 0, priority=10))
    rules.add(api.Rule("calm", lambda ctx: True, priority=20))
    with pytest.raises(api.RuleEvaluationError):
        rules.explain({})
    with pytest.raises(api.RuleEvaluationError):
        rules.matching({})
    entries = rules.trace({})
    assert [entry.name for entry in entries] == ["boom", "calm"]
    assert [entry.verdict for entry in entries] == [None, True]


def test_a_rule_missing_a_declared_key_reports_those_keys_and_is_not_evaluated():
    """The keys come back, and the predicate never runs.

    The spy is what makes "is not evaluated" falsifiable: an implementation
    that called the predicate and caught its `KeyError` would produce the
    same unset verdict while running code the declaration exists to keep
    from running.
    """
    calls = []

    def predicate(ctx):
        calls.append(ctx)
        return True

    rules = api.RuleSet()
    rules.add(api.Rule("needs_two", predicate, required_keys=("x", "y")))
    entry = rules.trace({"x": 1})[0]
    assert entry.verdict is None
    assert entry.missing_keys == ("y",)
    assert calls == []


def test_a_raising_predicate_and_a_missing_key_are_told_apart_by_missing_keys():
    """Two different failures, one unset verdict, and `missing_keys` is the
    only field that separates them — so a trace that left it empty for a
    missing-key failure would report a broken predicate where the caller's
    own context was the problem, and the remedies are different people's.
    """
    rules = api.RuleSet()
    rules.add(api.Rule("boom", lambda ctx: 1 / 0, priority=10))
    rules.add(api.Rule("needs_x", lambda ctx: ctx["x"], priority=20,
                       required_keys=("x",)))
    boom, needs_x = rules.trace({})
    assert (boom.verdict, boom.missing_keys) == (None, ())
    assert (needs_x.verdict, needs_x.missing_keys) == (None, ("x",))


def test_a_rule_trace_is_frozen_and_its_missing_keys_is_a_tuple():
    """An audit record a reader can rewrite records nothing.

    Both halves matter: a frozen record whose `missing_keys` was a list
    would still let the reader of an audit edit the keys in place and pass
    the record on as though it had always said that.
    """
    rules = api.RuleSet()
    rules.add(api.Rule("needs_x", lambda ctx: True, required_keys=("x",)))
    collected = rules.trace({})
    entry = collected[0]
    assert isinstance(entry.missing_keys, tuple)
    assert isinstance(collected, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.verdict = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.missing_keys = ()


def test_evaluate_rule_applies_both_layers_to_a_rule_in_no_set():
    """The single-rule evaluation is the one `RuleSet` uses, not a copy.

    Required keys first, before the predicate is ever called; then the
    predicate, with its own failure wrapped as `RuleEvaluationError` and a
    truthy non-bool return coerced — `is True` rather than `== True`, since
    a rule returning `"yes"` must not leak that string into a verdict field
    a reader compares with `is False`.
    """
    calls = []

    def predicate(ctx):
        calls.append(ctx)
        return "yes"

    declared = api.Rule("needs_x", predicate, required_keys=("x",))
    with pytest.raises(api.MissingContextKeys) as missing:
        api.evaluate_rule(declared, {})
    assert missing.value.missing_keys == ("x",)
    assert calls == []
    assert api.evaluate_rule(declared, {"x": 1}) is True
    with pytest.raises(api.RuleEvaluationError):
        api.evaluate_rule(api.Rule("boom", lambda ctx: 1 / 0), {})


def test_trace_rule_records_what_evaluate_rule_raises():
    """`trace_rule` is the swallowing half of the same pair, so a caller
    wanting a record never has to write its own `except` — and the two
    halves stay one definition of what a failure means.
    """
    record = api.trace_rule(api.Rule("boom", lambda ctx: 1 / 0), {})
    assert isinstance(record, api.RuleTrace)
    assert (record.name, record.verdict) == ("boom", None)
    decided = api.trace_rule(api.Rule("calm", lambda ctx: 0), {})
    assert (decided.verdict, decided.missing_keys) == (False, ())


# -- core/discounts.py: the rule a discount holds -----------------------


def test_a_discounts_rule_now_honours_its_required_keys():
    """A deliberate behaviour change, and the new exception type is the
    assertion.

    `Discount.applies` called `self.rule.predicate(context)` directly, so a
    discount's rule was the one rule in the package whose declaration was
    never enforced: an incomplete context raised whatever the predicate made
    of the missing key. `MissingContextKeys` is not a `KeyError`, which is
    asserted rather than assumed — the two are caught by different callers
    for different remedies.
    """
    discount = api.FixedDiscount("loyal", api.Money("10.00"), rule=needs_loyalty())
    with pytest.raises(api.MissingContextKeys) as caught:
        discount.applies(context())
    assert caught.value.missing_keys == ("loyalty_years",)
    assert not issubclass(api.MissingContextKeys, KeyError)
    # Through the whole pricing path too, not only the predicate check: a
    # price must not be produced from a context that cannot decide the rule.
    with pytest.raises(api.MissingContextKeys):
        discount.apply(SUBTOTAL, context())
    with pytest.raises(api.MissingContextKeys):
        api.DiscountEngine([discount]).total(SUBTOTAL, context())
    # And a satisfied context still prices exactly as before.
    full = dict(context(), loyalty_years=4)
    assert discount.apply(SUBTOTAL, full) == api.Money("10.00")


def test_a_discount_with_no_rule_still_applies_and_has_no_trace():
    """Unchanged, and `None` rather than an unset trace: "there was no rule"
    and "the rule did not decide" are different facts, and a trace carries a
    rule name a ruleless discount cannot fill.
    """
    discount = api.FixedDiscount("flat", api.Money("5.00"))
    assert discount.applies({}) is True
    assert discount.rule_trace({}) is None
    assert discount.apply(SUBTOTAL, {}) == api.Money("5.00")


def test_rule_trace_reads_back_the_verdict_applies_acts_on():
    """The reader for the contract `applies` enforces.

    Asserted in all three states against the same discount shape, because a
    `rule_trace` that only ever reported `True` would satisfy any test that
    never registered a failing rule.
    """
    applying = api.FixedDiscount("welcome", api.Money("12.50"), rule=retail_only())
    rejected = api.FixedDiscount("alumni", api.Money("30.00"), rule=vip_only())
    undecided = api.FixedDiscount("loyal", api.Money("10.00"), rule=needs_loyalty())
    assert applying.rule_trace(context()).verdict is True
    assert rejected.rule_trace(context()).verdict is False
    trace = undecided.rule_trace(context())
    assert (trace.name, trace.verdict, trace.missing_keys) == (
        "needs_loyalty", None, ("loyalty_years",))


def test_the_rule_is_consulted_before_any_subclass_gate():
    """`C6`'s ratified ordering, now visible through the exception.

    Both subclasses carry a gate of their own that would answer "does not
    apply" without consulting anything: the tiered discount's quantity
    clears no bracket and the bundle's category count earns no free unit. A
    rule that cannot be evaluated still raises, because the subclass gate
    never gets to speak first.
    """
    tiered = api.TieredDiscount("volume", [(10, "5")], rule=needs_loyalty())
    with pytest.raises(api.MissingContextKeys):
        tiered.applies({"quantity": 1})
    bundle = api.BundleDiscount("bundle", category="furniture", buy_quantity=3,
                                unit_price=api.Money("10.00"), rule=needs_loyalty())
    with pytest.raises(api.MissingContextKeys):
        bundle.applies({"category_counts": {"furniture": 1}})
    # And the gate still decides once the rule can be evaluated.
    assert tiered.applies({"quantity": 1, "loyalty_years": 4}) is False


# -- core/discounts.py: the audit --------------------------------------


def test_audit_returns_a_row_per_held_discount_including_the_ones_that_did_not_apply():
    """This is what distinguishes an audit from `explain`.

    `explain` reports what was charged, so a discount that took nothing off
    is simply absent from it; the question an audit answers is "why did this
    discount not apply", and a missing row answers nothing. `alumni` is the
    row `explain` does not have.
    """
    engine = api.DiscountEngine(four_discounts())
    rows = engine.audit(SUBTOTAL, context())
    assert [name for name, _amount, _trace in rows] == [
        "alumni", "welcome", "autumn", "volume"]
    assert [name for name, _amount in engine.explain(SUBTOTAL, context())] == [
        "welcome", "autumn", "volume"]
    alumni = rows[0]
    assert alumni[1] == api.Money("0.00")
    assert alumni[2].verdict is False


def test_audit_rows_are_ordered_by_rank_then_name():
    """Application order, not name order and not insertion order.

    By name the order would be alumni, autumn, volume, welcome; by insertion
    welcome, alumni, autumn, volume. The fixed discounts share a rank, so
    their relative order is the name tie-break and nothing else.
    """
    rows = api.DiscountEngine(four_discounts()).audit(SUBTOTAL, context())
    names = [name for name, _amount, _trace in rows]
    assert names == ["alumni", "welcome", "autumn", "volume"]
    assert names != sorted(names)
    assert names[:2] == ["alumni", "welcome"]


def test_an_audit_row_carries_the_amount_explain_reports_for_it():
    """Read off the fold, not recomputed against the original subtotal.

    The distinction is the whole assertion. `autumn` takes 12.5% of what
    `welcome` left — 12.5% of 205.00 is 25.625, half up to 25.63 — not
    12.5% of 217.50, which is 27.19. `volume` then takes 7.5% of 179.37,
    which is 13.45275 and rounds to 13.45, not 7.5% of 217.50. An
    implementation that applied each discount to `subtotal` to fill the
    column passes every test that only ever holds one discount.
    """
    engine = api.DiscountEngine(four_discounts())
    amounts = {name: amount for name, amount, _trace in
               engine.audit(SUBTOTAL, context())}
    assert amounts == {
        "alumni": api.Money("0.00"),
        "welcome": api.Money("12.50"),
        "autumn": api.Money("25.63"),
        "volume": api.Money("13.45"),
    }
    assert dict(engine.explain(SUBTOTAL, context())) == {
        "welcome": api.Money("12.50"),
        "autumn": api.Money("25.63"),
        "volume": api.Money("13.45"),
    }
    # Against the original subtotal these would read 27.19 and 16.31, so the
    # fold is what produced the column.
    assert amounts["autumn"] != api.Money("27.19")
    assert amounts["volume"] != api.Money("16.31")


def test_two_discounts_sharing_a_name_are_two_audit_rows():
    """What counts as one row: one discount the engine holds, not one name.

    The engine folds both, so the audit reports both, and their amounts
    differ — an implementation keyed by name would report 8.25 twice and
    lose the 5.00 the first one actually took off.
    """
    engine = api.DiscountEngine([
        api.FixedDiscount("dup", api.Money("5.00")),
        api.FixedDiscount("dup", api.Money("8.25")),
    ])
    rows = engine.audit(SUBTOTAL, {})
    assert [(name, amount) for name, amount, _trace in rows] == [
        ("dup", api.Money("5.00")),
        ("dup", api.Money("8.25")),
    ]
    assert engine.total(SUBTOTAL, {}) == api.Money("13.25")


def test_audit_reports_a_rule_it_cannot_evaluate_instead_of_raising():
    """An audit that raised would become the failure it exists to name.

    `explain` still refuses to price the same context, which is the pair of
    assertions: pricing must not proceed on a rule it could not decide, and
    the audit must not be silent about which rule that was. The undecided
    discount is charged nothing — reading "did not decide" as "applied"
    would invent a price.
    """
    discounts = four_discounts() + [
        api.FixedDiscount("loyal", api.Money("10.00"), rule=needs_loyalty()),
        api.FixedDiscount("boom", api.Money("9.00"),
                          rule=api.Rule("explodes", lambda ctx: 1 / 0)),
    ]
    engine = api.DiscountEngine(discounts)
    # `boom` precedes `loyal` in the engine's own (rank, name) order, so the
    # predicate failure is the one that reaches the caller first; either
    # exception escaping `explain` is the contract being pinned here.
    with pytest.raises(api.RuleEvaluationError):
        engine.explain(SUBTOTAL, context())
    rows = {name: (amount, trace) for name, amount, trace in
            engine.audit(SUBTOTAL, context())}
    assert rows["loyal"][0] == api.Money("0.00")
    assert rows["loyal"][1].verdict is None
    assert rows["loyal"][1].missing_keys == ("loyalty_years",)
    assert rows["boom"][0] == api.Money("0.00")
    assert rows["boom"][1].verdict is None
    assert rows["boom"][1].missing_keys == ()
    # The discounts that could be decided are still folded normally, so one
    # undecided rule does not void the rest of the audit's amounts.
    assert rows["welcome"][0] == api.Money("12.50")
    assert rows["autumn"][0] == api.Money("25.63")


def test_a_group_rival_that_lost_is_audited_with_a_zero_amount():
    """`explain` drops a group loser entirely; the audit keeps its row.

    And the row's amount is what it actually took off — nothing — rather
    than the amount it was *compared* by, which is 12.50 against the
    original subtotal. Its trace still says its rule matched, which is the
    point: the discount qualified and was outbid, two different facts the
    audit reports separately.
    """
    engine = api.DiscountEngine([
        api.FixedDiscount("welcome", api.Money("12.50"), rule=retail_only(),
                          group="signup"),
        api.FixedDiscount("bonus", api.Money("30.00"), rule=retail_only(),
                          group="signup"),
    ])
    assert dict(engine.explain(SUBTOTAL, context())) == {"bonus": api.Money("30.00")}
    rows = {name: (amount, trace) for name, amount, trace in
            engine.audit(SUBTOTAL, context())}
    assert sorted(rows) == ["bonus", "welcome"]
    assert rows["welcome"][0] == api.Money("0.00")
    assert rows["welcome"][1].verdict is True
    assert rows["bonus"][0] == api.Money("30.00")


def test_an_audit_row_carries_no_trace_for_a_discount_with_no_rule():
    """`volume` holds no rule, so its trace is `None` and not a record
    naming a rule that does not exist."""
    rows = {name: trace for name, _amount, trace in
            api.DiscountEngine(four_discounts()).audit(SUBTOTAL, context())}
    assert rows["volume"] is None
    assert rows["welcome"].name == "retail_only"


# -- reporting/reports.py: the report surface ---------------------------


def test_rule_audit_counts_verdicts_per_rule_name_sorted_by_name():
    """Counts per rule name across orders, not per discount and not per
    order.

    Two discounts on the first order share `retail_only`, so one order
    contributes two verdicts to it — "what counts as one" is one audited
    discount row, because the question is how often the rule decided and
    which way. Sorted by name, which `vip_only` after `retail_only` pins
    against the insertion order the engines were built in.
    """
    orders = [build_order("O-1", four_discounts()),
              build_order("O-2", four_discounts())]
    audit = api.RevenueReport(orders).rule_audit()
    assert list(audit) == ["retail_only", "vip_only"]
    assert audit == {
        "retail_only": {"true": 4, "false": 0, "unset": 0},
        "vip_only": {"true": 0, "false": 2, "unset": 0},
    }


def test_rule_audit_counts_a_rule_once_per_discount_row_per_order():
    """One order, two discounts, one shared rule name: two verdicts.

    Separated from the aggregate above so the per-order and per-discount
    halves of "what counts as one" each have their own assertion. An
    implementation counting one verdict per rule name per order would
    report 1, and one counting per distinct rule name across the report
    would report 1 as well.
    """
    audit = api.RevenueReport([build_order("O-1", four_discounts())]).rule_audit()
    assert audit["retail_only"] == {"true": 2, "false": 0, "unset": 0}


def test_rule_audit_reports_a_verdict_no_rule_reached_as_unset():
    """And never folds it into `false`.

    The report's own context carries `subtotal`, `quantity` and `segment`
    and nothing else, so `needs_loyalty` cannot be decided from it. The
    order's pricing pipeline says so by raising, which this test asserts
    alongside: the audit is readable for exactly the order whose total is
    not, which is the state an auditor is called in for.
    """
    order = build_order("O-1", four_discounts() + [
        api.FixedDiscount("loyal", api.Money("10.00"), rule=needs_loyalty())])
    with pytest.raises(api.MissingContextKeys):
        order.discount_total
    audit = api.RevenueReport([order]).rule_audit()
    assert audit["needs_loyalty"] == {"true": 0, "false": 0, "unset": 1}
    assert audit["retail_only"] == {"true": 2, "false": 0, "unset": 0}


def test_rule_audit_counts_no_rule_for_a_ruleless_discount():
    """`volume` applies to every order in this file and contributes nothing
    to the audit: it has no rule name to count under, and inventing one
    would put a row in the audit no rule authored. The assertion names the
    discount, because an implementation keying on the discount's name would
    produce a `volume` key that looks plausible.
    """
    audit = api.RevenueReport([build_order("O-1", four_discounts())]).rule_audit()
    assert "volume" not in audit
    assert set(audit) == {"retail_only", "vip_only"}


def test_rule_audit_on_an_empty_report_is_an_empty_mapping_not_zeros():
    """With no orders there is no engine to ask which rules exist, so a key
    here would be a claim about a rule set this report never saw. A
    cancelled order is the same case, since the report excludes it at
    construction — that is the version an implementation reading the
    constructor argument rather than the kept orders gets wrong.
    """
    assert api.RevenueReport([]).rule_audit() == {}
    cancelled = build_order("O-1", four_discounts())
    cancelled.cancel()
    assert api.RevenueReport([cancelled]).rule_audit() == {}


def test_rule_audit_does_not_disturb_discount_attribution():
    """The two readers share one context and the attribution is unchanged.

    `welcome` takes 12.50 and `autumn` 12.5% of the 205.00 left, half up
    from 25.625; `volume` then takes 7.5% of 179.37. They sum to 51.58,
    which is the order's own `discount_total` — the audit names where a
    discount went without moving what was charged.
    """
    order = build_order("O-1", four_discounts())
    report = api.RevenueReport([order])
    assert report.discount_attribution() == {
        "autumn": api.Money("25.63"),
        "volume": api.Money("13.45"),
        "welcome": api.Money("12.50"),
    }
    assert order.discount_total == api.Money("51.58")
    assert report.rule_audit()["vip_only"]["false"] == 1
