"""R3 — The rule combinators share one component reader.

`_predicate_of`, `_label_of` and `_required_keys_of` become one reader of
"is this a `Rule` or a bare predicate", used by `all_of`, `any_of` and
`negate`; the consolidation is then spent twice — `Discount.applies` accepts
a bare callable as its `rule`, and `Coupon._minimum_rule` builds its gate
through a combinator.

## Why almost every test here carries a bare-callable arm

A restructuring states a shape and a suite can only see behaviour, so most
of this Task's criteria are *pins*: the combinators must behave exactly as
they did. A pin asserted on its own passes on the base tree, and a test that
passes on the base asserts nothing this Task adds — the `base-fail`
invariant the pool requires, and the failure mode behind 15 of `v4`'s 24
thin suites.

So each pin is asserted together with the one consequence that makes it an
invariant *of R3*: the same component, handed over bare, is read the same
way. That pairing is not glue. It is the consolidation's whole claim — one
reader means one answer, whichever shape the caller happened to have and
whichever reader asks. On the base tree every bare arm raises
`AttributeError` from `Discount.applies`, which is why each test below is
red there, and the pinned half of each test is what stops the RI from
buying the new shape by changing the old answers.

`bare_gate` and `rule_gate` are the two spellings, kept as two helpers so
each test reads as the same question asked twice rather than as a loop.

## Fixtures chosen to discriminate

- The positional labels use bare predicates at arguments 1 and 3, so an
  implementation that numbers bare components 0, 1 in order of appearance
  rather than by argument index gives `predicate0,predicate1` and fails.
- The required-key union uses `region`, `account`, `quantity` in that
  argument order, whose sorted union reorders: an implementation that
  returns insertion order, or a `set`, fails rather than passing by luck.
- The coupon gate is tested at exactly its minimum and one cent below, so
  `>` and `>=` are distinguishable; `12.5%` of `73.30` is `9.1625`, which
  rounds to `9.16` rather than landing on a figure a truncating or
  half-even implementation would also produce for a rounder percentage.
- `Money("0.00", "EUR")` rather than a USD zero throughout the coupon
  tests, because the gate's missing-subtotal default is currency-bearing
  and a USD fixture would let a `Money.ZERO` default pass.
"""

import pytest

from billing import api


def bare_gate(predicate, context):
    """Whether a `Discount` carrying a *bare* predicate applies to `context`.

    The one new behaviour R3 buys, and the reason every test that calls this
    is red on the base tree: there `Discount.applies` reaches for
    `rule.predicate` on a plain function and raises `AttributeError` from
    inside the pricing fold.
    """
    return api.FixedDiscount("GATE", api.Money("1.00"), rule=predicate).applies(context)


def rule_gate(rule, context):
    """Whether a `Discount` carrying a `Rule` applies to `context`.

    The shape that already worked. Paired with `bare_gate` in test after
    test because the claim is the equality of the two answers, not either
    one alone.
    """
    return api.FixedDiscount("GATE", api.Money("1.00"), rule=rule).applies(context)


def vip_rule():
    return api.Rule("vip", lambda context: context.get("segment") == "vip")


def bulk_rule():
    return api.Rule("bulk", lambda context: context.get("quantity", 0) >= 10)


def vip_predicate(context):
    return context.get("segment") == "vip"


def bulk_predicate(context):
    return context.get("quantity", 0) >= 10


TRUTH_TABLE_CONTEXTS = (
    {"segment": "vip", "quantity": 10},
    {"segment": "vip", "quantity": 1},
    {"segment": "retail", "quantity": 10},
    {"segment": "retail", "quantity": 1},
)


def test_a_bare_callable_is_accepted_as_a_discount_rule():
    """The behavioural consequence R3 exists to deliver.

    Both halves are asserted: the lambda a caller already has carries no
    `.predicate` attribute — which is exactly why the old
    `self.rule.predicate` reached past it — and the discount now gates on it
    correctly in both directions, rather than merely not raising.
    """
    predicate = vip_predicate
    assert not hasattr(predicate, "predicate")
    discount = api.FixedDiscount("SAVE", api.Money("7.50"), rule=predicate)
    subtotal = api.Money("40.00")
    assert discount.applies({"segment": "vip"}) is True
    assert discount.apply(subtotal, {"segment": "vip"}) == api.Money("7.50")
    assert discount.applies({"segment": "retail"}) is False
    assert discount.apply(subtotal, {"segment": "retail"}) == api.Money("0.00")


def test_a_rule_and_the_identical_bare_predicate_price_the_same():
    """One reader, so the two spellings cannot disagree about an amount.

    A percentage discount rather than a fixed one, so the assertion is on a
    computed figure and not only on a boolean: 12.5% of 73.30 is 9.1625,
    which is 9.16 to two places. A discount that silently declined in the
    bare case would return a zero and be caught here by the amount, not just
    by `applies`.
    """
    subtotal = api.Money("73.30")
    as_rule = api.PercentageDiscount("PCT", "12.5", rule=vip_rule())
    as_bare = api.PercentageDiscount("PCT", "12.5", rule=vip_predicate)
    context = {"segment": "vip"}
    assert as_rule.apply(subtotal, context) == api.Money("9.16")
    assert as_bare.apply(subtotal, context) == as_rule.apply(subtotal, context)
    declined = {"segment": "retail"}
    assert as_bare.apply(subtotal, declined) == as_rule.apply(subtotal, declined)
    assert as_bare.apply(subtotal, declined) == api.Money("0.00")


def test_a_bare_predicate_declares_no_required_keys_so_no_declaration_is_invented():
    """Undeclared is not the same as "requires nothing", and neither raises.

    A bare callable never wrote a `required_keys` contract, so the component
    reader must not invent one by inspecting the callable: a `Discount`
    holding a bare predicate that reads a key defensively returns a verdict
    for an empty context rather than raising `MissingContextKeys`. The
    positive case is asserted too, so a reader that simply swallowed the
    context would fail.
    """
    # No `pytest.raises` wrapper on purpose: a `MissingContextKeys` raised
    # from here would propagate and fail the test, which *is* the assertion.
    # The positive case is asserted too, so a reader that quietly declined
    # every bare predicate could not pass by never raising.
    predicate = bulk_predicate
    assert bare_gate(predicate, {}) is False
    assert bare_gate(predicate, {"quantity": 10}) is True
    declared = api.Rule("bulk", predicate, required_keys=("quantity",))
    assert declared.required_keys == ("quantity",)
    assert api.all_of(predicate, name="bare_only").required_keys == ()


def test_a_declaration_is_still_enforced_by_the_rule_set_and_a_bare_callable_declares_nothing():
    """The rule set enforces a declaration a bare callable never makes.

    `RuleSet._evaluate` promised the check and still makes it: a declaring
    `Rule` registered on a set raises `MissingContextKeys` naming the rule.
    The component reader this Task introduces gives a bare callable no
    `required_keys`, so a `Discount` holding one never raises
    `MissingContextKeys` — nothing was declared, so there is nothing to
    enforce — and the bare arm completes.

    Whether a `Discount` enforces a declared `Rule`'s keys is a later
    behaviour change that this Task does not own, so this test does not pin
    it. Only the rule set's enforcement and the bare callable's silence are
    asserted here.
    """
    declaring = api.Rule(
        "needs_quantity",
        lambda context: context["quantity"] >= 10,
        required_keys=("quantity",),
    )
    rules = api.RuleSet()
    rules.add(declaring)
    with pytest.raises(api.MissingContextKeys) as missing:
        rules.matching({})
    assert missing.value.rule_name == "needs_quantity"
    assert bare_gate(lambda context: context.get("quantity", 0) >= 10, {}) is False


def test_no_rule_still_always_applies_and_is_not_a_falsey_rule():
    """`rule=None` is "no gate", which a bare callable must not collapse into.

    The default is unchanged and the new shape does not shadow it: a
    discount with no rule applies to an empty context, and a discount whose
    rule is a bare predicate returning `False` does not. An implementation
    that treated any non-`Rule` as absent would pass the first assertion and
    fail the second.
    """
    assert api.FixedDiscount("A", api.Money("5.00")).applies({}) is True
    assert api.FixedDiscount("A", api.Money("5.00"), rule=None).applies({}) is True
    assert bare_gate(lambda context: False, {}) is False
    assert bare_gate(lambda context: True, {}) is True


def test_all_of_gives_the_same_truth_table_in_all_three_spellings():
    """`all_of` is unchanged, and the three spellings agree cell by cell.

    Two `Rule`s, a `Rule` mixed with a bare predicate, and the combined
    rule's own predicate handed to a discount bare. The full four-cell truth
    table rather than one matching context, so an implementation that lost a
    component — the `zip`-style mistake a consolidation invites — fails on
    the cells where exactly one side is true.
    """
    both_rules = api.all_of(vip_rule(), bulk_rule(), name="vip_bulk")
    mixed = api.all_of(vip_rule(), bulk_predicate, name="vip_bulk_mixed")
    expected = (True, False, False, False)
    for context, want in zip(TRUTH_TABLE_CONTEXTS, expected):
        assert rule_gate(both_rules, context) is want
        assert rule_gate(mixed, context) is want
        assert bare_gate(both_rules.predicate, context) is want


def test_any_of_gives_the_same_truth_table_in_all_three_spellings():
    """`any_of` is unchanged, and differs from `all_of` on three cells.

    Asserted against the same four contexts as `all_of`, so a reader can see
    the two folds are distinguished by the fixture and not merely named
    differently: only the last cell is shared.
    """
    both_rules = api.any_of(vip_rule(), bulk_rule(), name="vip_or_bulk")
    mixed = api.any_of(vip_predicate, bulk_rule(), name="vip_or_bulk_mixed")
    expected = (True, True, True, False)
    for context, want in zip(TRUTH_TABLE_CONTEXTS, expected):
        assert rule_gate(both_rules, context) is want
        assert rule_gate(mixed, context) is want
        assert bare_gate(both_rules.predicate, context) is want


def test_negate_inverts_both_shapes_and_keeps_its_unnumbered_default_name():
    """`not_<name>` for a `Rule`, `not_predicate` for a bare one.

    The second default is the one a consolidation is likely to break: if
    `negate` wrapped its single argument with a position the way the
    variadic combinators do, the name would come out `not_predicate0`. The
    test pins the absence of that digit, and the inversion itself in both
    shapes.
    """
    assert api.negate(vip_rule()).name == "not_vip"
    assert api.negate(vip_predicate).name == "not_predicate"
    assert api.negate(vip_predicate, name="plain").name == "plain"
    inverted_rule = api.negate(vip_rule())
    inverted_bare = api.negate(vip_predicate)
    for context in TRUTH_TABLE_CONTEXTS:
        want = context["segment"] != "vip"
        assert rule_gate(inverted_rule, context) is want
        assert bare_gate(inverted_bare.predicate, context) is want


def test_auto_generated_names_label_bare_predicates_by_argument_position():
    """The label is the argument index, not a count of bare components.

    Bare predicates sit at arguments 1 and 3 of four, so the expected name
    carries `predicate1` and `predicate3`. An implementation that enumerated
    only the bare components would produce `predicate0` and `predicate1` and
    pass a fixture where every component was bare — which is why this
    fixture interleaves. `Rule` components contribute their own names, which
    is what a reader looks for in a `RuleEvaluationError`.
    """
    combined = api.all_of(vip_rule(), vip_predicate, bulk_rule(), bulk_predicate)
    assert combined.name == "all_of(vip,predicate1,bulk,predicate3)"
    either = api.any_of(vip_predicate, bulk_rule())
    assert either.name == "any_of(predicate0,bulk)"
    assert api.all_of(vip_rule(), name="chosen").name == "chosen"
    assert bare_gate(combined.predicate, {"segment": "vip", "quantity": 10}) is True


def test_required_keys_is_the_sorted_union_and_empty_when_every_component_is_bare():
    """The union is sorted, and a bare component contributes nothing to it.

    The argument order `region, account, quantity` sorts to
    `account, quantity, region`, so an implementation returning insertion
    order or an unsorted `set` fails. The all-bare case returns `()` — the
    defect the removed `_required_keys_of` docstring described must stay
    fixed — and the mixed case shows a bare component neither contributes a
    key nor erases the declared ones.
    """
    region = api.Rule("region", lambda context: True, required_keys=("region",))
    account = api.Rule("account", lambda context: True, required_keys=("account",))
    quantity = api.Rule("quantity", lambda context: True, required_keys=("quantity",))
    combined = api.all_of(region, account, quantity, name="three")
    assert combined.required_keys == ("account", "quantity", "region")
    assert api.any_of(region, account, name="two").required_keys == ("account", "region")
    assert api.negate(region).required_keys == ("region",)
    assert api.all_of(vip_predicate, bulk_predicate, name="bare").required_keys == ()
    assert api.all_of(region, vip_predicate, name="mixed").required_keys == ("region",)
    assert bare_gate(combined.predicate, {}) is True


def test_a_nested_combinator_keeps_its_keys_and_still_gates():
    """Combinators compose, and the union survives one level of nesting.

    `all_of(any_of(...), negate(...))` is the shape a caller reaches for
    second, and it is where a reader that read `required_keys` off the
    argument rather than off the component would drop a declaration: the
    inner combinator is itself a `Rule`, and its keys are the union it
    already computed.
    """
    region = api.Rule("region", lambda context: context.get("region") == "eu",
                      required_keys=("region",))
    account = api.Rule("account", lambda context: context.get("account") == "A1",
                       required_keys=("account",))
    quantity = api.Rule("quantity", lambda context: context.get("quantity", 0) >= 10,
                        required_keys=("quantity",))
    nested = api.all_of(api.any_of(region, account), api.negate(quantity),
                        name="nested")
    assert nested.required_keys == ("account", "quantity", "region")
    context = {"region": "eu", "account": "none", "quantity": 1}
    assert rule_gate(nested, context) is True
    assert bare_gate(nested.predicate, context) is True
    assert bare_gate(nested.predicate, dict(context, quantity=10)) is False


def test_short_circuit_order_is_unchanged_left_to_right():
    """`all_of` stops at the first false, `any_of` at the first true.

    Asserted by a component that raises if it is reached, which is the only
    way to see an order from outside. The same fold is then observed through
    the bare path, because a consolidation that materialized its components
    into a list of results before folding them would still return the right
    answer and would evaluate the exploding component.
    """
    def boom(context):
        raise AssertionError("a short-circuited component was evaluated")

    stops_early = api.all_of(lambda context: False, boom, name="all_stops")
    assert rule_gate(stops_early, {}) is False
    assert bare_gate(stops_early.predicate, {}) is False
    first_true = api.any_of(lambda context: True, boom, name="any_stops")
    assert rule_gate(first_true, {}) is True
    assert bare_gate(first_true.predicate, {}) is True
    reaches_it = api.all_of(lambda context: True, boom, name="all_reaches")
    # Issue 6 wraps a predicate failure in `RuleEvaluationError`, so reaching
    # the exploding component shows as either type; what is pinned is that it is reached.
    with pytest.raises((AssertionError, api.RuleEvaluationError)):
        bare_gate(reaches_it.predicate, {})


def test_a_combinator_with_no_components_still_raises_value_error():
    """The arity guard is unchanged, and one bare component is a whole rule.

    `ValueError` rather than a `Rule` that vacuously matches everything: an
    `all_of()` that returned a rule would be a gate a caller believed they
    had configured. The floor of one component is now reachable in the bare
    shape anywhere a rule is taken, which is the half of this test that is
    new.
    """
    with pytest.raises(ValueError):
        api.all_of(name="empty")
    with pytest.raises(ValueError):
        api.any_of(name="empty")
    assert api.all_of(vip_predicate, name="one").name == "one"
    assert bare_gate(api.all_of(vip_predicate, name="one").predicate,
                     {"segment": "vip"}) is True


def test_a_component_that_could_never_be_called_is_refused_where_it_is_written():
    """One reader means one place to refuse, and the refusal names the value.

    `Rule.__post_init__` already refused a non-callable predicate at
    construction for the reason its docstring gives; a bare component had no
    such guard because no single reader owned it. Now it does, so a
    combinator and a discount both fail at the mistake rather than inside a
    later fold — and the discount's failure is a `TypeError` about the
    component, not an `AttributeError` about a missing attribute on a string.
    """
    with pytest.raises(TypeError) as refused:
        api.all_of(vip_rule(), "nope")
    assert "nope" in str(refused.value)
    with pytest.raises(TypeError):
        api.negate(None)
    with pytest.raises(TypeError):
        api.FixedDiscount("BAD", api.Money("5.00"), rule="nope").applies({})


def test_a_zero_minimum_subtotal_still_builds_no_rule_at_all():
    """No gate, rather than a gate that always passes.

    An unconditional rule would appear in every explanation of why the
    discount applied, which `_minimum_rule` refuses by returning `None`. The
    second half shows the absence is doing work and is not merely invisible:
    the same coupon with a real minimum builds a gate that declines the very
    context the zero-minimum coupon is discounted on, and that gate's own
    predicate declines it in the bare shape too.
    """
    free = api.Coupon("FREE-EUR", "fixed", api.Money("5.00", "EUR"),
                      minimum_subtotal=api.Money("0.00", "EUR"))
    assert free.to_discount().rule is None
    assert free.to_discount().apply(api.Money("10.00", "EUR"), {}) == \
        api.Money("5.00", "EUR")
    gated = api.Coupon("BIG-EUR", "fixed", api.Money("5.00", "EUR"),
                       minimum_subtotal=api.Money("100.00", "EUR"))
    assert gated.to_discount().rule is not None
    assert bare_gate(gated.to_discount().rule.predicate, {}) is False


def test_the_coupon_gate_is_named_for_the_code_and_declares_no_keys():
    """The gate is assembled by a combinator and is still the same gate.

    `<code>-minimum` rather than the `all_of(predicate0)` an
    auto-generated name would give: the identifier a support agent reads in
    an explanation names the gate, not the plumbing. And it declares no
    `required_keys`, which is what keeps a context with no subtotal a
    decline rather than a `MissingContextKeys` — the gate reads the key
    defensively by design.
    """
    coupon = api.Coupon("BIG-EUR", "fixed", api.Money("5.00", "EUR"),
                        minimum_subtotal=api.Money("100.00", "EUR"))
    rule = coupon.to_discount().rule
    assert rule.name == "BIG-EUR-minimum"
    assert rule.required_keys == ()
    assert bare_gate(rule.predicate, {"subtotal": api.Money("100.00", "EUR")}) is True


def test_the_coupon_gate_defaults_a_missing_subtotal_to_zero_in_its_own_currency():
    """A context with no subtotal declines; it does not raise.

    The minimum is in EUR, so a `Money.ZERO`-shaped default in USD would
    raise `CurrencyMismatch` on the comparison instead of declining. The
    boundary is asserted at exactly the minimum and one cent below it, which
    is what separates `>=` from `>` — a consolidation that rebuilt this gate
    with the wrong comparison would otherwise pass every other assertion
    here. Both shapes are asked, since the gate's predicate is now something
    a discount can hold bare.
    """
    coupon = api.Coupon("BIG-EUR", "fixed", api.Money("5.00", "EUR"),
                        minimum_subtotal=api.Money("100.00", "EUR"))
    discount = coupon.to_discount()
    subtotal = api.Money("150.00", "EUR")
    assert discount.apply(subtotal, {}) == api.Money("0.00", "EUR")
    at_minimum = {"subtotal": api.Money("100.00", "EUR")}
    below = {"subtotal": api.Money("99.99", "EUR")}
    assert discount.apply(subtotal, at_minimum) == api.Money("5.00", "EUR")
    assert discount.apply(subtotal, below) == api.Money("0.00", "EUR")
    assert bare_gate(discount.rule.predicate, at_minimum) is True
    assert bare_gate(discount.rule.predicate, below) is False
    assert bare_gate(discount.rule.predicate, {}) is False
