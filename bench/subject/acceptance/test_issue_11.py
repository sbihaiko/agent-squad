"""R1 — One row parser for every bulk load.

`Catalog._parse_row` and `CustomerBook._parse_row` were the same function
twice. One `RowSpec` in `catalog/products.py` now owns the field order, the
arity bounds, the dispatch and the message; `sales/customers.py` imports it
(`sales -> catalog` is allowed); `catalog/coupons.py` gains the
`CouponBook.load` it never had, on the same spec. `core/tax.py`'s
`TaxTable._parse_row` is deliberately left alone — `core` may import
nothing from this package.

## Why `CouponBook.load` is in almost every test below

A restructuring states a shape, and a suite can see only behaviour. The
honest difficulty here is that the two hand-written messages the base ships
*already* coincide in shape, word for word except the noun, the range and
the field list — so "the two modules' messages now have the same shape" is
true of the base tree too, and a test asserting only that would pass on the
base and assert nothing about this Task.

What the base cannot do is produce a *third* one. `CouponBook` had no bulk
load at all, and the spec gives it an arity message, a dispatch, a
bare-string guard and a type-error message naming its own noun, its own
required count and its own field order, with nobody writing any of them.
That third instance is the consolidation's visible consequence, so every
criterion about a shared message or a shared dispatch is asserted across all
three holders at once: the products and customers legs pin the unchanged
figures (which the base suite staying green is necessary but not sufficient
for, since the base suite does not read these messages at all), and the
coupon leg is what makes the claim falsifiable.

The other base-failing surface is the spec itself — `api.RowSpec` and the
three `*_ROW_SPEC` constants do not exist on the base — which is what lets
the reconciliation between a spec's hand-written load order and its record
type's declared fields be tested directly rather than inferred.

## On fixtures that discriminate

Every arity figure below is one element off a boundary, never two: a
3-element product row and an 8-element product row, not a 1-element and a
99-element one, because an off-by-one in either bound is the error a
consolidated checker can actually introduce and a wildly wrong row is
refused by every implementation anyone would write. The customer and coupon
rows both require 3 while the three holders permit different maxima, so the
three holders disagree on both bounds — an implementation that hardcoded
either bound, or shared one holder's bound with another, fails here rather
than passing by coincidence.
"""

from dataclasses import MISSING, dataclass, fields
import re

import pytest

from billing import api

#: The one shape all three arity messages must take. Anchored at both ends
#: and capturing the noun, the two bounds and the field list separately, so a
#: message that merely *contains* the right words in the wrong order does not
#: match.
ARITY = re.compile(
    r"^a (?P<noun>\w+) row tuple must have between (?P<low>\d+) and "
    r"(?P<high>\d+) elements: \((?P<fields>.*)\)$"
)


def declared_row(record_type, values):
    """The mapping and the positional row for one record, built from the
    record type's own declared fields (11-*.md: the spec reconciles against
    them). `values` names the fields this Task fills; a declared field it
    does not name takes its own default, so the row is complete on a tree
    where a later issue added a field and on a tree where none did.
    """
    mapping = {}
    for f in fields(record_type):
        if f.name in values:
            mapping[f.name] = values[f.name]
        elif f.default is not MISSING:
            mapping[f.name] = f.default
        else:
            raise AssertionError(f"{f.name} has no value in this fixture")
    return mapping, tuple(mapping.values())


def product_row_shapes():
    """One product, written both ways, filling every field the record type
    declares. Deliberately includes `taxable=False` and a `parent_sku`, so
    the positional row reaches the trailing positions — a spec that `zip`ped
    a short row correctly and a full row onto shifted names would pass a
    4-element fixture.
    """
    return declared_row(api.Product, {
        "sku": "SKU-1-RED",
        "name": "Desk (red)",
        "unit_price": api.Money("260.00"),
        "category": "furniture",
        "taxable": False,
        "parent_sku": "SKU-1",
    })


def customer_row_shapes():
    """One customer, written both ways, filling every optional field.

    `addresses` carries a primary address whose jurisdiction agrees with the
    `jurisdiction` field, because `Customer.__post_init__` rejects a
    disagreement — a positional row that landed `addresses` on the
    `tax_exempt` position, or the reverse, would therefore fail loudly here
    rather than quietly producing a different customer.
    """
    address = api.Address("US-CA", "1 Main St", True, "billing")
    mapping = {
        "customer_id": "C-9",
        "name": "Grace",
        "jurisdiction": "US-CA",
        "segment": "wholesale",
        "tax_exempt": True,
        "addresses": (address,),
        "credit_limit": api.Money("500.00"),
    }
    positional = ("C-9", "Grace", "US-CA", "wholesale", True, (address,),
                  api.Money("500.00"))
    return mapping, positional


def coupon_row_shapes():
    """One coupon, written both ways, filling every field the record type
    declares."""
    return declared_row(api.Coupon, {
        "code": "SAVE10",
        "kind": "percentage",
        "value": "10",
        "minimum_subtotal": api.Money("50.00"),
        "uses_remaining": 3,
        "expires_on": "2026-12-31",
        "customer_id": "C-1",
        "stackable": False,
    })


def holders():
    """The three (spec, load-callable, shortest valid row) triples.

    A list rather than three copies of each test, because the claim under
    test in most of what follows is precisely that the three behave
    identically — and writing it once per holder invites the drift the Task
    exists to remove.
    """
    return [
        (api.PRODUCT_ROW_SPEC, api.Catalog().load,
         ("SKU-1", "Desk", api.Money("250.00"), "furniture")),
        (api.CUSTOMER_ROW_SPEC, api.CustomerBook().load,
         ("C-1", "Ada", "US-CA")),
        (api.COUPON_ROW_SPEC, api.CouponBook().load,
         ("SAVE10", "percentage", "10")),
    ]


def test_the_shared_spec_and_the_three_holders_specs_are_exported():
    """One `RowSpec` class, three instances, each bound to its own record
    type. The binding is asserted because a spec pointing at the wrong
    dataclass would still parse a row of the right arity into a record of
    the wrong kind.
    """
    assert api.RowSpec is not None
    assert api.PRODUCT_ROW_SPEC.record_type is api.Product
    assert api.CUSTOMER_ROW_SPEC.record_type is api.Customer
    assert api.COUPON_ROW_SPEC.record_type is api.Coupon
    assert isinstance(api.PRODUCT_ROW_SPEC, api.RowSpec)
    assert isinstance(api.CUSTOMER_ROW_SPEC, api.RowSpec)
    assert isinstance(api.COUPON_ROW_SPEC, api.RowSpec)


def test_a_dict_row_and_the_equivalent_positional_row_build_the_same_product():
    """Through `load`, and through the spec directly, which must agree.

    Two routes to the same record is the point: `Catalog.load` no longer
    owns a parser, so if the spec and the load path could disagree the
    consolidation would have moved the duplication rather than removed it.
    """
    mapping, positional = product_row_shapes()
    catalog = api.Catalog()
    catalog.load([mapping])
    from_dict = catalog.get("SKU-1-RED")
    other = api.Catalog()
    other.load([positional])
    assert other.get("SKU-1-RED") == from_dict
    assert api.PRODUCT_ROW_SPEC.parse(positional) == from_dict
    assert api.PRODUCT_ROW_SPEC.parse(mapping) == from_dict
    # And the figures the row carried are the ones that landed, not
    # defaults that happen to compare equal.
    assert from_dict.taxable is False
    assert from_dict.parent_sku == "SKU-1"


def test_a_dict_row_and_the_equivalent_positional_row_build_the_same_customer():
    mapping, positional = customer_row_shapes()
    book = api.CustomerBook()
    book.load([mapping])
    from_dict = book.get("C-9")
    other = api.CustomerBook()
    other.load([positional])
    assert other.get("C-9") == from_dict
    assert api.CUSTOMER_ROW_SPEC.parse(positional) == from_dict
    assert api.CUSTOMER_ROW_SPEC.parse(mapping) == from_dict
    assert from_dict.segment == "wholesale"
    assert from_dict.tax_exempt is True
    assert from_dict.credit_limit == api.Money("500.00")
    assert from_dict.primary_address().jurisdiction == "US-CA"


def test_the_spec_reports_each_holders_own_load_order_and_required_count():
    """Each spec's field list and required count come from its own record
    type, and the three do not share either.

    The expected names and counts are read off each dataclass's declared
    fields, so a field later added to a record type shows up here as a
    mismatch rather than moving a number the test pinned. An implementation
    that gave one holder another's names, or a required count not its own,
    fails on the type it borrowed the wrong list from.
    """
    specs = (api.PRODUCT_ROW_SPEC, api.CUSTOMER_ROW_SPEC, api.COUPON_ROW_SPEC)
    for spec in specs:
        declared = fields(spec.record_type)
        names = tuple(f.name for f in declared)
        required = tuple(f.name for f in declared if f.default is MISSING)
        # Exactly the record type's own fields, no extras and none dropped.
        assert sorted(spec.field_names()) == sorted(names)
        # The required count is the record type's own, and the required
        # fields occupy the leading positions the arity bound relies on.
        assert spec.required_count == len(required)
        assert set(spec.field_names()[:spec.required_count]) == set(required)
    # The three field lists are pairwise different, so no holder is wearing
    # another's names.
    lists = [frozenset(spec.field_names()) for spec in specs]
    assert len(set(lists)) == len(specs)


def test_a_positional_row_one_element_short_of_the_required_count_is_refused():
    """One short, in all three holders. Not two short: an off-by-one in the
    lower bound is the error a shared checker can introduce, and it is
    invisible to a fixture that is wildly too short.
    """
    cases = [
        (api.Catalog().load, ("SKU-1", "Desk", api.Money("250.00"))),
        (api.CustomerBook().load, ("C-1", "Ada")),
        (api.CouponBook().load, ("SAVE10", "percentage")),
    ]
    for load, row in cases:
        with pytest.raises(ValueError):
            load([row])
    # And the row one element *longer* than the required count, which is the
    # shortest legal row, is accepted — so the bound is the required count
    # and not the required count plus one.
    for spec, load, row in holders():
        assert len(row) == spec.required_count
        load([row])


def test_a_positional_row_one_element_longer_than_the_field_list_is_refused():
    """One too long, in all three holders, the three field lists differing
    in length — so a shared upper bound cannot satisfy all three.
    """
    mapping, product = product_row_shapes()
    _, customer = customer_row_shapes()
    _, coupon = coupon_row_shapes()
    cases = [
        (api.Catalog().load, product + (None,)),
        (api.CustomerBook().load, customer + (None,)),
        (api.CouponBook().load, coupon + (None,)),
    ]
    for load, row in cases:
        with pytest.raises(ValueError):
            load([row])
    # The full-length rows themselves are accepted, which is what makes the
    # assertions above about the bound rather than about those rows.
    for spec, load, row in zip(
            (api.PRODUCT_ROW_SPEC, api.CUSTOMER_ROW_SPEC, api.COUPON_ROW_SPEC),
            (api.Catalog().load, api.CustomerBook().load, api.CouponBook().load),
            (product, customer, coupon)):
        assert len(row) == len(spec.field_names())
        load([row])


def test_every_arity_message_names_the_range_and_the_field_order_in_one_shape():
    """The consolidation's visible consequence, asserted across three holders.

    The base tree's two hand-written messages already matched this template
    by coincidence of authorship; a third one, for a holder that had no bulk
    load, cannot. Each message's captured bounds and field list are checked
    against the spec's own `required_count` and `field_names`, so a template
    that matched while printing the wrong numbers fails.
    """
    seen = {}
    for spec in (api.PRODUCT_ROW_SPEC, api.CUSTOMER_ROW_SPEC,
                 api.COUPON_ROW_SPEC):
        match = ARITY.match(spec.arity_message())
        assert match is not None, spec.arity_message()
        assert int(match["low"]) == spec.required_count
        assert int(match["high"]) == len(spec.field_names())
        names = re.findall(r"\w+", match["fields"])
        assert tuple(names) == spec.field_names()
        seen[match["noun"]] = match["fields"]
    assert sorted(seen) == ["coupon", "customer", "product"]
    # Optional positions are bracketed, one bracket opened per optional
    # field and all of them closed at the end — the notation the two
    # hand-written messages used, now produced rather than typed. The count
    # is read off each spec, not pinned, so a later optional field does not
    # make this false.
    optional = {
        name: len(spec.field_names()) - spec.required_count
        for name, spec in (("coupon", api.COUPON_ROW_SPEC),
                           ("product", api.PRODUCT_ROW_SPEC))
    }
    assert seen["coupon"].count("[") == optional["coupon"]
    assert seen["coupon"].endswith("]" * optional["coupon"])
    assert seen["product"].count("[") == optional["product"]


def test_the_message_a_wrongly_sized_row_raises_is_the_specs_own():
    """Not merely the same shape: the same string.

    Asserted separately from the template above because a `load` that
    recomputed the message itself would satisfy the template and reintroduce
    exactly the duplication this Task removes.
    """
    cases = [
        (api.PRODUCT_ROW_SPEC, api.Catalog().load, ("SKU-1", "Desk")),
        (api.CUSTOMER_ROW_SPEC, api.CustomerBook().load, ("C-1",)),
        (api.COUPON_ROW_SPEC, api.CouponBook().load, ("SAVE10",)),
    ]
    for spec, load, row in cases:
        with pytest.raises(ValueError) as raised:
            load([row])
        assert str(raised.value) == spec.arity_message()


def test_load_of_a_bare_string_still_raises_type_error_in_every_holder():
    """A string is iterable; iterating it yields characters. The message is
    identical across the three holders, which it has to be — it says nothing
    about the record type, so three different wordings would be three
    authors rather than three contracts.
    """
    messages = set()
    for _spec, load, _row in holders():
        with pytest.raises(TypeError) as raised:
            load("SKU-1")
        messages.add(str(raised.value))
    assert messages == {
        "rows must be an iterable of row tuples or mappings, not a string"
    }


def test_a_row_of_an_unsupported_type_raises_type_error_naming_the_type():
    """And names the holder's own noun, so the caller learns which feed was
    wrong as well as what was in it.
    """
    nouns = {"product", "customer", "coupon"}
    for _spec, load, _row in holders():
        with pytest.raises(TypeError) as raised:
            load([object()])
        message = str(raised.value)
        assert "object" in message
        assert any(noun in message for noun in nouns)
    # A set, not a sequence: iterable but neither a mapping nor a positional
    # row, so it must be refused rather than silently `zip`ped in whatever
    # order the set happens to iterate.
    with pytest.raises(TypeError):
        api.Catalog().load([{"SKU-1", "Desk"}])


def test_an_unknown_keyword_in_a_dict_row_raises_from_the_dataclass_not_the_spec():
    """The boundary, stated: the spec validates arity and dispatch, never
    field names.

    Two copies of "what fields does `Product` accept" would be two places
    that could disagree, and the constructor is the one that cannot be
    wrong. The assertion is on the constructor's own wording, which is what
    distinguishes this from a spec that grew a name check of its own — and
    on a *long* dict row being accepted, which shows that the arity bounds
    are not applied to the mapping shape at all.
    """
    mapping, _ = product_row_shapes()
    with pytest.raises(TypeError) as raised:
        api.PRODUCT_ROW_SPEC.parse(dict(mapping, colour="red"))
    assert "unexpected keyword argument" in str(raised.value)
    assert "colour" in str(raised.value)
    # One more key than the field list is long, and it is the unknown name
    # that is refused rather than the count.
    assert len(dict(mapping, colour="red")) == len(
        api.PRODUCT_ROW_SPEC.field_names()) + 1
    # A dict row shorter than `required_count` likewise reaches the constructor,
    # which reports the missing argument by name.
    with pytest.raises(TypeError) as raised:
        api.PRODUCT_ROW_SPEC.parse({"sku": "SKU-2", "name": "Lamp"})
    assert "unit_price" in str(raised.value)


def test_the_load_order_is_reconciled_against_the_record_types_declared_fields():
    """A spec whose field list disagrees with its dataclass raises where it
    is built, rather than `zip`ping rows onto the wrong names forever after.

    This is the criterion that makes a hand-written load order safe. Three
    disagreements, because they fail differently: a name dropped, a name
    invented, and a name repeated — the last of which has the right length
    and the right-looking contents and would otherwise overwrite a position.
    """
    declared = api.PRODUCT_ROW_SPEC.field_names()
    with pytest.raises(ValueError):
        api.RowSpec(api.Product, declared[:-1])
    with pytest.raises(ValueError):
        api.RowSpec(api.Product, declared + ("colour",))
    with pytest.raises(ValueError):
        api.RowSpec(api.Product, declared[:-1] + ("taxable",))
    # Reordering within the optional tail is allowed: a load order that
    # legitimately differs from the declaration order is the reason the
    # tuple is written out at all, so the reconciliation must not forbid it.
    # The order swaps two optional names of the spec's own field list, so
    # the assertion is about the reorder and not about a list length.
    swapped = list(declared)
    first, second = swapped.index("taxable"), swapped.index("parent_sku")
    swapped[first], swapped[second] = swapped[second], swapped[first]
    reordered = api.RowSpec(api.Product, tuple(swapped))
    declared_fields = {f.name: f for f in fields(api.Product)}
    given = {
        "sku": "SKU-5",
        "name": "Stool",
        "unit_price": api.Money("30.00"),
        "category": "furniture",
        "taxable": False,
        "parent_sku": "SKU-1",
    }
    built = reordered.parse(
        tuple(given.get(name, declared_fields[name].default) for name in swapped))
    assert built.parent_sku == "SKU-1"
    assert built.taxable is False


def test_a_field_added_without_updating_the_spec_raises_rather_than_zipping():
    """The failure mode the reconciliation exists for, reproduced directly.

    Two dataclasses standing in for one record type before and after a field
    is added. The stale load order is refused on the second, which is the
    whole claim; a spec that trusted its tuple would build a record with the
    new field left at its default and nobody any the wiser.
    """

    @dataclass(frozen=True)
    class Before:
        code: str
        label: str = ""

    @dataclass(frozen=True)
    class After:
        code: str
        label: str = ""
        note: str = ""

    order = ("code", "label")
    assert api.RowSpec(Before, order).parse(("X", "y")).label == "y"
    with pytest.raises(ValueError) as raised:
        api.RowSpec(After, order)
    assert "note" in str(raised.value)
    # And a required field may not be pushed out of the leading positions,
    # which would put an optional field inside the range a caller is obliged
    # to fill and make the arity bounds describe a row the constructor
    # refuses.
    with pytest.raises(ValueError):
        api.RowSpec(After, ("label", "code", "note"))


def test_the_spec_refuses_a_record_type_that_is_not_a_dataclass():
    """Because the reconciliation and the required count are both read off
    the declared fields, a non-dataclass has neither — and a spec built on
    one would have no bounds to enforce at all.
    """
    class NotADataclass:
        pass

    with pytest.raises(TypeError):
        api.RowSpec(NotADataclass, ("code",))
    with pytest.raises(TypeError):
        api.RowSpec(dict, ("code",))


def test_catalog_load_is_still_mutate_in_place_and_returns_none():
    """Pinned for all three holders. The base suite does not assert it, so
    "the base suite stays green" does not cover it — and a consolidation
    that made `load` return the parsed records would be a silent change of
    contract for every existing caller.
    """
    catalog = api.Catalog()
    assert catalog.load([("SKU-1", "Desk", api.Money("250.00"), "furniture")]) is None
    assert len(catalog) == 1
    book = api.CustomerBook()
    assert book.load([("C-1", "Ada", "US-CA")]) is None
    assert book.get("C-1").name == "Ada"
    coupons = api.CouponBook()
    assert coupons.load([("SAVE10", "percentage", "10")]) is None
    assert coupons.get("SAVE10").value == "10"


def test_a_load_that_fails_partway_leaves_the_rows_before_the_failure_registered():
    """Rows are registered as they are read, not staged and committed.

    Pinned rather than assumed: a caller loading a long feed may depend on
    it to re-read the remainder, and a consolidation that validated every
    row before registering any — a defensible design, just not this one —
    would change it for three holders at once. The third row in each case is
    valid, and is asserted *absent*, which is what makes this about the
    ordering rather than about the failing row.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("250.00"), "furniture"))
    with pytest.raises(api.DuplicateProduct):
        catalog.load([
            ("SKU-8", "Lamp", api.Money("40.00"), "lighting"),
            ("SKU-1", "Other desk", api.Money("10.00"), "furniture"),
            ("SKU-9", "Rug", api.Money("60.00"), "furniture"),
        ])
    assert catalog.get("SKU-8").name == "Lamp"
    with pytest.raises(api.UnknownProduct):
        catalog.get("SKU-9")

    book = api.CustomerBook()
    with pytest.raises(ValueError):
        book.load([("C-1", "Ada", "US-CA"), ("C-2",), ("C-3", "Alan", "US-OR")])
    assert book.get("C-1").name == "Ada"
    with pytest.raises(api.UnknownCustomer):
        book.get("C-3")

    coupons = api.CouponBook()
    with pytest.raises(ValueError):
        coupons.load([
            ("SAVE10", "percentage", "10"),
            ("BAD",),
            ("SAVE20", "percentage", "20"),
        ])
    assert coupons.get("SAVE10").value == "10"
    with pytest.raises(api.UnknownCoupon):
        coupons.get("SAVE20")


def test_coupon_book_load_registers_coupons_from_both_row_shapes():
    """The capability this Task adds, and the record it builds is the one a
    hand-written `Coupon(...)` would be — every optional field filled, so a
    positional row landing `expires_on` on `uses_remaining` or
    `customer_id` on `stackable` is caught rather than defaulted past.
    """
    mapping, positional = coupon_row_shapes()
    book = api.CouponBook()
    book.load([mapping])
    from_dict = book.get("SAVE10")
    other = api.CouponBook()
    other.load([positional])
    assert other.get("SAVE10") == from_dict
    assert from_dict == api.Coupon(**mapping)
    assert from_dict.minimum_subtotal == api.Money("50.00")
    assert from_dict.uses_remaining == 3
    assert from_dict.expires_on == "2026-12-31"
    assert from_dict.customer_id == "C-1"
    assert from_dict.stackable is False
    # And it is a real coupon, not just a stored tuple: the redemption
    # accounting the row described is in force.
    assert book.applicable(["SAVE10"]) == ("SAVE10",)
    assert from_dict.to_discount().name == "SAVE10"


def test_coupon_book_load_of_a_duplicate_code_replaces_it_without_raising():
    """`CouponBook.add`'s documented behaviour, not `Catalog.add`'s.

    The two registries differ on what a repeated key means — re-issuing a
    code is how `uses_remaining` is reset after a relaunch, while a repeated
    sku is a data error — and the shared spec must not have flattened that
    difference. The same list, loaded by a `Catalog`, raises; loaded by a
    `CouponBook`, it does not. Both halves are asserted, because either one
    alone is satisfied by a spec that happened to pick that holder's rule
    for both.
    """
    book = api.CouponBook()
    book.load([
        ("SAVE10", "percentage", "10", api.Money("0.00"), 1),
        ("SAVE10", "percentage", "15", api.Money("0.00"), 5),
    ])
    assert book.get("SAVE10").value == "15"
    assert book.get("SAVE10").uses_remaining == 5

    catalog = api.Catalog()
    with pytest.raises(api.DuplicateProduct):
        catalog.load([
            ("SKU-1", "Desk", api.Money("250.00"), "furniture"),
            ("SKU-1", "Other desk", api.Money("10.00"), "furniture"),
        ])


def test_coupon_book_load_leaves_the_redemption_count_untouched():
    """`add` is not a redemption, so neither is `load`.

    A bulk relaunch of a hundred codes must not erase a hundred histories,
    and the count is asserted both before and after the reload so a `load`
    that reset it to zero — the plausible alternative — is visible.
    """
    book = api.CouponBook()
    book.load([("SAVE10", "percentage", "10", api.Money("0.00"), 2)])
    book.redeem("SAVE10")
    book.redeem("SAVE10")
    assert book.redemption_count("SAVE10") == 2
    assert book.get("SAVE10").uses_remaining == 0
    # Relaunch the code with fresh uses.
    book.load([("SAVE10", "percentage", "10", api.Money("0.00"), 4)])
    assert book.get("SAVE10").uses_remaining == 4
    assert book.redemption_count("SAVE10") == 2
    # A code loaded and never redeemed reports zero, not one.
    book.load([("NEW5", "fixed", api.Money("5.00"))])
    assert book.redemption_count("NEW5") == 0


def test_the_tax_table_loads_both_row_shapes_without_a_spec_of_its_own():
    """`core` may import nothing from this package, so `TaxTable` cannot
    reach the spec.

    Asserted rather than left implicit because the obvious over-reach of
    this Task is to "finish the job" by either moving the spec into `core`
    or giving `core/tax.py` an illegal import. `TaxTable.load` therefore
    still parses both row shapes to the values it always did, and nothing
    teaches it a spec of its own. Issue 16 later consolidates the tax
    table's own parser into `core`, which this Task's text anticipates and
    does not forbid; what it forbids is the spec travelling with it.
    The first assertion is what fails on the base tree.
    """
    assert api.RowSpec is not None
    assert not hasattr(api, "TAX_ROW_SPEC")
    table = api.TaxTable()
    table.load([("US-CA", "10"), {"jurisdiction": "US-OR", "rate": "0"}])
    assert table.rate_for("US-CA", "furniture") == api.TaxRate("US-CA", "10").rate
    with pytest.raises(ValueError):
        api.TaxTable().load([("US-CA",)])
