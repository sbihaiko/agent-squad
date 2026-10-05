"""M10 — Variants roll up into one sellable family.

`Catalog.variant_tree` and `Catalog.rollup_price` in `catalog/products.py`,
the one-level depth rule (`Product.__post_init__` plus `Cart.add`, sharing
the named `VariantDepthError`), `Cart.add_variant`, and on the reporting
side `RevenueReport.by_parent_sku` plus `top_products(by="family")`.

The fixture catalog is built so that three plausible shortcuts fail rather
than coincide:

- The parent's sku sorts *after* two of its variants (`DSK-Z9` against
  `DSK-A1`), so "sort the whole family by sku" is not the same answer as
  "parent first, then variants sorted".
- The parent carries the family's *highest* price, so a `rollup_price`
  built on `variants_of` (which excludes the parent) reports a different
  maximum.
- The best-selling single sku (`LMP-7`) and the best-selling family
  (`DSK-Z9`) are different rows, so a `top_products` that ignores `by`
  ranks the wrong thing first.

Every price is an odd number of cents for the same reason: an
implementation that drops a line, double-counts one, or attributes a
variant to itself lands on a different total rather than on the same round
figure.
"""

import pytest

from billing import api

# Prices, all unround, and chosen so the parent is the family maximum and a
# variant is the family minimum:
#   parent  DSK-Z9 169.40  <- the maximum; variants-only rollup misses it
#   variant DSK-A1 139.75
#   variant DSK-B2 119.25  <- the minimum
#   variant DSK-C3 159.95
# 61.05 is deliberately shared by AAA-V and MMM-V so the tie-break has
# something to break.


def build_catalog():
    catalog = api.Catalog()
    catalog.add(api.Product("DSK-Z9", "Desk", api.Money("169.40"), "furniture"))
    catalog.add(
        api.Product(
            "DSK-A1", "Desk, oak", api.Money("139.75"), "furniture", parent_sku="DSK-Z9"
        )
    )
    catalog.add(
        api.Product(
            "DSK-B2", "Desk, ash", api.Money("119.25"), "furniture", parent_sku="DSK-Z9"
        )
    )
    catalog.add(
        api.Product(
            "DSK-C3",
            "Desk, walnut",
            api.Money("159.95"),
            "furniture",
            parent_sku="DSK-Z9",
        )
    )
    catalog.add(api.Product("LMP-7", "Lamp", api.Money("42.30"), "lighting"))
    catalog.add(api.Product("ZZZ-P", "Shelf", api.Money("75.10"), "furniture"))
    catalog.add(
        api.Product(
            "AAA-V",
            "Shelf, short",
            api.Money("61.05"),
            "furniture",
            parent_sku="ZZZ-P",
        )
    )
    catalog.add(api.Product("MMM-P", "Stool", api.Money("80.00"), "furniture"))
    catalog.add(
        api.Product(
            "MMM-V", "Stool, tall", api.Money("61.05"), "furniture", parent_sku="MMM-P"
        )
    )
    return catalog


def build_cart(catalog, additions):
    customer = api.Customer("C-1", "Ada", "US-CA")
    cart = api.Cart(catalog, customer)
    for sku, quantity in additions:
        cart.add(sku, quantity)
    return cart


def build_report(catalog, additions):
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(table),
    )
    order = api.Order("O-1", build_cart(catalog, additions), pricing)
    order.place()
    return api.RevenueReport([order])


def test_the_depth_error_is_exported_and_stays_catchable_as_a_value_error():
    """The new name is reachable, and the old habit still works.

    The self-parent rule has always raised `ValueError`, so callers wrap
    `Product(...)` in `except ValueError`. A depth rejection raised as a
    bare `Exception` would slip past every one of them, which is why the
    subclass relationship is asserted rather than assumed.
    """
    assert api.VariantDepthError is not None
    assert issubclass(api.VariantDepthError, ValueError)


def test_variant_tree_puts_the_parent_first_then_variants_sorted_by_sku():
    """Position is the contract, not a by-product of the sort.

    `DSK-Z9` sorts after `DSK-A1`, `DSK-B2` and `DSK-C3`, so an
    implementation that sorts the whole family by sku returns the parent
    last and fails here. The second family makes the same claim from the
    opposite end: `ZZZ-P` sorts after its only variant `AAA-V`.
    """
    catalog = build_catalog()
    assert [p.sku for p in catalog.variant_tree("DSK-Z9")] == [
        "DSK-Z9",
        "DSK-A1",
        "DSK-B2",
        "DSK-C3",
    ]
    assert [p.sku for p in catalog.variant_tree("ZZZ-P")] == ["ZZZ-P", "AAA-V"]
    # The parent itself is not one of its own variants, which is
    # `variants_of`'s existing contract and is what `variant_tree` adds to
    # rather than redefines.
    assert [p.sku for p in catalog.variants_of("DSK-Z9")] == [
        "DSK-A1",
        "DSK-B2",
        "DSK-C3",
    ]


def test_a_product_with_no_variants_is_a_family_of_one():
    """Not an empty sequence: a standalone lamp is a family of one.

    An empty answer would make "the family of LMP-7" and "LMP-7 does not
    exist" the same result, and the caller could not tell them apart
    without a second lookup.
    """
    catalog = build_catalog()
    tree = catalog.variant_tree("LMP-7")
    assert [p.sku for p in tree] == ["LMP-7"]
    assert tree[0] is catalog.get("LMP-7")


def test_variant_tree_of_an_unknown_sku_raises_unknown_product():
    """`get`'s answer, so a miss reads the same wherever it happens — and a
    `Product` is accepted in place of its sku, as `variants_of` accepts one.
    """
    catalog = build_catalog()
    with pytest.raises(api.UnknownProduct):
        catalog.variant_tree("NO-SUCH")
    with pytest.raises(api.UnknownProduct):
        catalog.rollup_price("NO-SUCH")
    parent = catalog.get("DSK-Z9")
    assert catalog.variant_tree(parent) == catalog.variant_tree("DSK-Z9")


def test_a_variant_of_a_variant_is_rejected_at_construction():
    """One level deep, enforced where the parent record is in hand.

    `DSK-A1` is already a variant, so nothing may vary from it. Without
    this rule `variant_tree` would need a recursion whose termination
    nothing guarantees. A `Product` passed as the parent is the only form
    that carries the parent's own parent, and it is also normalized to the
    parent's sku — asserted here, because a field that kept a `Product`
    would silently break `variants_of`'s string comparison.
    """
    catalog = build_catalog()
    oak = catalog.get("DSK-A1")
    desk = catalog.get("DSK-Z9")
    with pytest.raises(api.VariantDepthError):
        api.Product("DSK-A1-X", "Desk, oak, tall", api.Money("149.95"), "furniture",
                    parent_sku=oak)
    # The same declaration against a genuine parent is accepted, and the
    # record collapses to that parent's sku.
    accepted = api.Product(
        "DSK-D4", "Desk, teak", api.Money("149.95"), "furniture", parent_sku=desk
    )
    assert accepted.parent_sku == "DSK-Z9"
    catalog.add(accepted)
    assert [p.sku for p in catalog.variants_of("DSK-Z9")] == [
        "DSK-A1",
        "DSK-B2",
        "DSK-C3",
        "DSK-D4",
    ]


def test_the_self_parent_rejection_is_unchanged():
    """Still `ValueError`, still at construction, for both spellings of the
    same mistake — the sku, and the record that resolves to it.
    """
    with pytest.raises(ValueError):
        api.Product("DSK-Z9", "Desk", api.Money("169.40"), "furniture",
                    parent_sku="DSK-Z9")
    desk = api.Product("DSK-Z9", "Desk", api.Money("169.40"), "furniture")
    with pytest.raises(ValueError):
        api.Product("DSK-Z9", "Desk", api.Money("169.40"), "furniture",
                    parent_sku=desk)


def test_rollup_price_spans_the_whole_family_including_the_parent():
    """The parent's own price is part of the span.

    The parent carries the family maximum (169.40 against the variants'
    top of 159.95), so a rollup computed over `variants_of` instead of
    `variant_tree` reports 159.95 and fails. The pair is ordered lowest
    first, so a caller rendering "from 119.25" reads index 0 without
    knowing which end is which.
    """
    catalog = build_catalog()
    assert catalog.rollup_price("DSK-Z9") == (
        api.Money("119.25"),  # DSK-B2, the family minimum
        api.Money("169.40"),  # DSK-Z9 itself, the family maximum
    )
    # Explicitly not the variants-only span, which is the likeliest wrong
    # answer rather than a hypothetical one.
    assert catalog.rollup_price("DSK-Z9") != (
        api.Money("119.25"),
        api.Money("159.95"),
    )


def test_rollup_price_of_a_family_of_one_returns_that_price_as_both_bounds():
    """The span of one price is that price. A `None` upper bound would make
    every caller special-case the commonest catalog shape.
    """
    catalog = build_catalog()
    assert catalog.rollup_price("LMP-7") == (api.Money("42.30"), api.Money("42.30"))


def test_rollup_price_refuses_to_compare_prices_in_different_currencies():
    """A family priced in two currencies has no span.

    `CurrencyMismatch` is the exception this package already raises
    wherever two currencies meet, so the rollup does not invent a
    catalog-specific error for the same fact. The parent is the odd one
    out here, which also proves the parent is in the comparison at all.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("MIX-P", "Rug", api.Money("90.00", "EUR"), "furniture"))
    catalog.add(
        api.Product(
            "MIX-V", "Rug, large", api.Money("120.50"), "furniture", parent_sku="MIX-P"
        )
    )
    with pytest.raises(api.CurrencyMismatch):
        catalog.rollup_price("MIX-P")
    # Each single-currency family in the same catalog still rolls up.
    assert catalog.rollup_price("MIX-V") == (
        api.Money("120.50"),
        api.Money("120.50"),
    )


def test_add_variant_verifies_the_family_rather_than_assuming_it():
    """The stated family is checked against the catalog.

    `MMM-V` is a real product and `DSK-Z9` is a real parent, so an
    implementation that merely resolved both skus and added the second
    would accept this call and put a stool line under a desk family. The
    rejection is what the method is *for*: `add` already adds a sku.
    """
    catalog = build_catalog()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    with pytest.raises(ValueError):
        cart.add_variant("DSK-Z9", "MMM-V", 1)
    # A standalone product declares no parent, so it is nobody's variant.
    with pytest.raises(ValueError):
        cart.add_variant("DSK-Z9", "LMP-7", 1)
    # Nothing was added by either refusal.
    assert cart.lines == []
    # An unknown sku on either side is a catalog miss, not a family error.
    with pytest.raises(api.UnknownProduct):
        cart.add_variant("NO-SUCH", "DSK-A1", 1)
    with pytest.raises(api.UnknownProduct):
        cart.add_variant("DSK-Z9", "NO-SUCH", 1)
    # And the genuine relationship is accepted.
    cart.add_variant("DSK-Z9", "DSK-A1", 2)
    assert [(line.product.sku, line.quantity) for line in cart.lines] == [
        ("DSK-A1", 2)
    ]


def test_add_variant_merges_into_an_existing_line_exactly_as_add_does():
    """One line for a sku, whichever door the units came through.

    Two lines for `DSK-A1` would double its rank slot in `top_products`
    and its row in `by_parent_sku`. Quantity validation is delegated too,
    so a fractional count is the same `InvalidQuantity` `add` raises
    rather than a second rule that could drift from it.
    """
    catalog = build_catalog()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("DSK-A1", 2)
    cart.add_variant("DSK-Z9", "DSK-A1", 3)
    assert [(line.product.sku, line.quantity) for line in cart.lines] == [
        ("DSK-A1", 5)  # 2 + 3, one line
    ]
    # And `quantity` defaults to one, as `add`'s does. A different default
    # would charge a caller who named no count for units it never asked
    # for, and the merge above would hide it behind an explicit 3.
    cart.add_variant("DSK-Z9", "DSK-A1")
    assert [line.quantity for line in cart.lines] == [6]
    # 139.75 * 6 = 838.50
    assert cart.subtotal == api.Money("838.50")
    with pytest.raises(api.InvalidQuantity):
        cart.add_variant("DSK-Z9", "DSK-A1", 1.5)
    with pytest.raises(api.InvalidQuantity):
        cart.add_variant("DSK-Z9", "DSK-A1", 0)
    # A rejected quantity changed nothing.
    assert cart.subtotal == api.Money("838.50")


def test_a_cart_refuses_a_line_whose_parent_is_itself_a_variant():
    """The depth rule again, for the sku form that needs a catalog.

    A `parent_sku` given as a bare string cannot see whether the parent is
    a variant, so a grandchild can reach the catalog. The cart is the
    first place holding both the product and the catalog, and it refuses
    the line — because `by_parent_sku` attributes every line to its parent
    exactly once, and a grandchild's parent is not a family root, so the
    same revenue would sit under a key that is itself somebody's variant.
    """
    catalog = build_catalog()
    catalog.add(
        api.Product(
            "DSK-A1-T",
            "Desk, oak, tall",
            api.Money("149.95"),
            "furniture",
            parent_sku="DSK-A1",  # DSK-A1 is a variant of DSK-Z9
        )
    )
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    with pytest.raises(api.VariantDepthError):
        cart.add("DSK-A1-T")
    with pytest.raises(api.VariantDepthError):
        cart.add_variant("DSK-A1", "DSK-A1-T")
    assert cart.lines == []
    # A one-level variant and a standalone product are both still fine, so
    # the check refuses grandchildren rather than variants.
    cart.add("DSK-A1")
    cart.add("LMP-7")
    assert [line.product.sku for line in cart.lines] == ["DSK-A1", "LMP-7"]


def test_a_variant_whose_parent_is_not_registered_is_still_addable():
    """The cart does not newly require a parent to exist.

    `add` has never demanded that a variant's parent be in the catalog,
    and a depth check that resolved the parent unconditionally would
    reject carts the base has always accepted. The rule is "a *known*
    parent must not itself be a variant".
    """
    catalog = api.Catalog()
    catalog.add(
        api.Product(
            "ORP-1", "Orphan, oak", api.Money("55.45"), "furniture",
            parent_sku="ABSENT-PARENT",
        )
    )
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("ORP-1", 2)
    # 55.45 * 2 = 110.90
    assert cart.subtotal == api.Money("110.90")


def test_by_parent_sku_attributes_every_line_exactly_once():
    """Each active line lands in one family, and the families partition the
    report's revenue.

    The cross-check against `by_category` is the point: both are
    partitions of the same active lines, so their totals must agree. An
    implementation that attributed a variant to *both* its parent and
    itself, or that skipped a standalone product for having no parent,
    breaks the equality rather than merely reporting a different shape.
    """
    catalog = build_catalog()
    report = build_report(
        catalog,
        [("AAA-V", 1), ("DSK-A1", 1), ("DSK-B2", 1), ("LMP-7", 4)],
    )
    assert report.by_parent_sku() == {
        "DSK-Z9": api.Money("259.00"),  # 139.75 + 119.25, neither sku is a key
        "LMP-7": api.Money("169.20"),  # 42.30 * 4, a standalone is its own family
        "ZZZ-P": api.Money("61.05"),  # the variant's revenue, under its parent
    }
    # The same lines, partitioned by category instead: 61.05 + 139.75 +
    # 119.25 = 320.05 furniture, 169.20 lighting.
    assert report.by_category() == {
        "furniture": api.Money("320.05"),
        "lighting": api.Money("169.20"),
    }
    families = sum(
        report.by_parent_sku().values(), api.Money("0.00")
    )
    categories = sum(report.by_category().values(), api.Money("0.00"))
    assert families == categories == api.Money("489.25")
    # A parent sold directly is attributed to itself, not dropped for
    # having no parent_sku of its own.
    direct = build_report(catalog, [("DSK-Z9", 1), ("DSK-A1", 1)])
    # 169.40 + 139.75 = 309.15, one family, one key.
    assert direct.by_parent_sku() == {"DSK-Z9": api.Money("309.15")}


def test_by_parent_sku_is_sorted_by_parent_sku():
    """Sorted by key, matching the report's other mappings.

    The fixture makes this falsifiable: `AAA-V` is the first line by sku
    and its family key is `ZZZ-P`, the last key, so a mapping built in
    line order comes back `ZZZ-P` first. A report that returned insertion
    order would pass a test built from a catalog whose skus happened to
    agree with its parents'.
    """
    catalog = build_catalog()
    report = build_report(
        catalog, [("AAA-V", 1), ("DSK-A1", 1), ("LMP-7", 1)]
    )
    assert list(report.by_parent_sku()) == ["DSK-Z9", "LMP-7", "ZZZ-P"]


def test_top_products_by_family_ranks_families_rather_than_variants():
    """A desk sold in two colors competes as a desk.

    Per sku the lamp wins on 169.20; per family the desk wins on 259.00,
    which no single desk variant reaches. So an implementation that
    ignores `by` ranks the lamp first and fails. Quantities fold too —
    2 desk units against the lamp's 4 — and `n` still truncates the
    ranking it was given.
    """
    catalog = build_catalog()
    report = build_report(
        catalog,
        [("AAA-V", 1), ("DSK-A1", 1), ("DSK-B2", 1), ("LMP-7", 4)],
    )
    assert [row[0] for row in report.top_products()] == [
        "LMP-7",  # 169.20
        "DSK-A1",  # 139.75
        "DSK-B2",  # 119.25
        "AAA-V",  # 61.05
    ]
    assert report.top_products(by="family") == (
        ("DSK-Z9", None, api.Money("259.00"), 2),  # 139.75 + 119.25, 1 + 1 units
        ("LMP-7", "Lamp", api.Money("169.20"), 4),  # 42.30 * 4
        ("ZZZ-P", None, api.Money("61.05"), 1),
    )
    assert report.top_products(1, by="family") == (
        ("DSK-Z9", None, api.Money("259.00"), 2),
    )


def test_a_family_row_is_named_only_when_the_parent_itself_was_sold():
    """The report never consults the catalog, so it cannot name a parent it
    has no line for.

    `None` rather than the parent sku, so a caller can tell "this report
    holds no name for the family" from "the family is named after its
    sku". When the parent *is* sold, its own name is the row's — and that
    holds regardless of which line the aggregation met first, which is why
    the variant is added before the parent here.
    """
    catalog = build_catalog()
    unsold_parent = build_report(catalog, [("DSK-A1", 1)])
    assert unsold_parent.top_products(by="family") == (
        ("DSK-Z9", None, api.Money("139.75"), 1),
    )
    sold_parent = build_report(catalog, [("DSK-A1", 1), ("DSK-Z9", 1)])
    assert sold_parent.top_products(by="family") == (
        ("DSK-Z9", "Desk", api.Money("309.15"), 2),  # 139.75 + 169.40
    )
    # Per sku, both rows are named by their own product as they always were.
    assert [row[1] for row in sold_parent.top_products()] == ["Desk", "Desk, oak"]


def test_a_family_tie_breaks_on_parent_sku_and_a_sku_tie_on_sku():
    """Two tie-breaks, two assertions, because they key on different things.

    `AAA-V` and `MMM-V` sell the identical 61.05. Per sku the order is
    `AAA-V`, `MMM-V`; per family it is `MMM-P`, `ZZZ-P` — the reverse,
    because a family ranks on the parent's sku and `AAA-V`'s parent is
    `ZZZ-P`. An implementation that broke a family tie on the variant sku
    it happened to aggregate first returns them the other way round.
    """
    catalog = build_catalog()
    report = build_report(catalog, [("AAA-V", 1), ("MMM-V", 1)])
    assert [row[0] for row in report.top_products()] == ["AAA-V", "MMM-V"]
    assert [row[0] for row in report.top_products(by="family")] == ["MMM-P", "ZZZ-P"]
    assert [row[2] for row in report.top_products(by="family")] == [
        api.Money("61.05"),
        api.Money("61.05"),
    ]


def test_top_products_is_unchanged_by_default():
    """The existing signature and every existing behavior still hold.

    `by` is a new keyword with the old behavior as its default, so a
    caller that never heard of families sees no change: positional `n`,
    `n=None` meaning "all of them, ranked", an `n` past the end without
    padding, and a negative `n` rejected.
    """
    catalog = build_catalog()
    report = build_report(catalog, [("DSK-A1", 1), ("DSK-B2", 1), ("LMP-7", 4)])
    assert [row[0] for row in report.top_products()] == ["LMP-7", "DSK-A1", "DSK-B2"]
    assert [row[0] for row in report.top_products(2)] == ["LMP-7", "DSK-A1"]
    # Three distinct products, so an n of 9 returns three rows, not nine.
    assert len(report.top_products(9)) == 3
    with pytest.raises(ValueError):
        report.top_products(-1)
    assert report.top_products()[0] == (
        "LMP-7",
        "Lamp",
        api.Money("169.20"),  # 42.30 * 4
        4,
    )


def test_an_unrecognized_by_is_rejected_rather_than_falling_back():
    """A silent fallback to per-sku ranking would answer a question the
    caller did not ask and look like an answer — the same reasoning that
    makes a negative `n` an error instead of an empty tuple.
    """
    catalog = build_catalog()
    report = build_report(catalog, [("DSK-A1", 1)])
    with pytest.raises(ValueError):
        report.top_products(by="parent")
    with pytest.raises(ValueError):
        report.top_products(by=None)
