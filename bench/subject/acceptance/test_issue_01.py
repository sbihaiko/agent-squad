"""M1 — Foreign-currency order totals.

`ExchangeRate(base, quote, rate)` and a `RateTable` holding them in
`core/money.py`, `Money.convert_to(currency, rates)`, then
`Order.total_in`/`Order.breakdown_in` and `RevenueReport.net_in`.

Every conversion below uses a rate that is neither 1 nor a round number, so
an implementation that ignores the rate, inverts it, or defaults a missing
pair to parity fails rather than passing by coincidence.
"""

from decimal import Decimal

import pytest

from billing import api

# 0.9135 is chosen so that a two-place target rounds a third decimal of
# exactly 5: 270.00 * 0.9135 is 246.645, which is 246.65 half up and 246.64
# truncated. A rate of 0.91 would leave those two implementations
# indistinguishable.
USD_EUR = Decimal("0.9135")
# Exponent 0 and exponent 3 respectively, so a conversion quantized to two
# places is wrong in both directions rather than only in one.
USD_JPY = Decimal("150.275")
USD_BHD = Decimal("0.37688")


def rates():
    return api.RateTable(
        [
            api.ExchangeRate("USD", "EUR", USD_EUR),
            api.ExchangeRate("USD", "JPY", USD_JPY),
            api.ExchangeRate("USD", "BHD", USD_BHD),
        ]
    )


def build_order(discounts=()):
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(
        api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers", taxable=False)
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
    )
    return api.Order("O-1", cart, pricing)


def test_the_rate_types_are_exported():
    assert api.ExchangeRate is not None
    assert api.RateTable is not None
    assert api.UnknownExchangeRate is not None


def test_rate_for_returns_the_registered_rate_as_a_decimal():
    assert rates().rate_for("USD", "EUR") == USD_EUR
    assert isinstance(rates().rate_for("USD", "JPY"), Decimal)


def test_an_unregistered_pair_raises_rather_than_defaulting_to_parity():
    """A missing rate is a missing configuration, not a rate of 1.

    Parity is the expensive default: it converts at exactly the wrong
    price while looking like an answer, and every figure downstream carries
    it. The assertion is on the exception *and* on the absence of a
    returned value, because a table that answered `Decimal(1)` here would
    otherwise satisfy every other test in this file that never asks for
    GBP.
    """
    with pytest.raises(api.UnknownExchangeRate):
        rates().rate_for("USD", "GBP")
    with pytest.raises(api.UnknownExchangeRate):
        api.Money("100.00").convert_to("GBP", rates())


def test_a_rate_is_directed_and_the_reverse_pair_is_not_derived():
    """`(USD, EUR)` says nothing about `(EUR, USD)`.

    A bid and an ask are different numbers. A table that inverted one to
    answer for the other would convert at a price nobody quoted, and the
    inverse of 0.9135 is not a rate any market published.
    """
    table = rates()
    with pytest.raises(api.UnknownExchangeRate):
        table.rate_for("EUR", "USD")
    table.add(api.ExchangeRate("EUR", "USD", Decimal("1.07")))
    assert table.rate_for("EUR", "USD") == Decimal("1.07")
    # Registering the reverse did not disturb the forward rate, and the two
    # are not reciprocals of each other.
    assert table.rate_for("USD", "EUR") == USD_EUR
    assert table.rate_for("EUR", "USD") != 1 / USD_EUR


def test_a_zero_or_negative_rate_is_rejected_at_construction():
    with pytest.raises(ValueError):
        api.ExchangeRate("USD", "EUR", "0")
    with pytest.raises(ValueError):
        api.ExchangeRate("USD", "EUR", "-0.5")
    # Rejected at construction, so a table never holds one either.
    with pytest.raises(ValueError):
        api.RateTable([api.ExchangeRate("USD", "EUR", 0)])


def test_a_rate_is_held_as_a_decimal_and_is_immutable():
    """A string rate parses exactly; the held value is not a float.

    `0.9135` as a binary float is not 0.9135, and a rate multiplies into
    every figure it touches, so the error would not stay in one place.
    """
    rate = api.ExchangeRate("USD", "EUR", "0.9135")
    assert rate.rate == Decimal("0.9135")
    assert isinstance(rate.rate, Decimal)
    with pytest.raises(AttributeError):
        rate.rate = Decimal("2")


def test_registering_a_pair_twice_raises_rather_than_replacing():
    """A table that held the last write would convert at a price no caller
    chose, and the caller that registered first would never find out.
    """
    table = rates()
    with pytest.raises(ValueError):
        table.add(api.ExchangeRate("USD", "EUR", Decimal("1.5")))
    assert table.rate_for("USD", "EUR") == USD_EUR


def test_converting_into_the_amounts_own_currency_consults_no_rate():
    """A self-pair is not a price, so no table is required to hold one.

    `None` stands in for the table to make the claim falsifiable: an
    implementation that looks the rate up before checking the currency
    raises `AttributeError` here, and one that converts through a
    registered `(USD, USD)` rate cannot be written at all.
    """
    amount = api.Money("12.34")
    assert amount.convert_to("USD", None) == amount
    assert amount.convert_to("USD", api.RateTable()) == api.Money("12.34")


def test_conversion_rounds_to_the_target_currencys_registered_exponent():
    """Whole yen, three places of dinar, two of euro.

    Not two places everywhere: JPY has no minor unit and BHD prices in
    thousandths, the same registered exponent `Money.round`, `from_cents`
    and `to_cents` already read.
    """
    amount = api.Money("270.00")
    table = rates()
    # 270.00 * 150.275 = 40574.25, quantized to a whole yen.
    assert amount.convert_to("JPY", table) == api.Money("40574", "JPY")
    # 270.00 * 0.37688 = 101.7576, quantized to three places.
    assert amount.convert_to("BHD", table) == api.Money("101.758", "BHD")
    # 270.00 * 0.9135 = 246.645, half up to two places.
    assert amount.convert_to("EUR", table) == api.Money("246.65", "EUR")


def test_conversion_rounds_half_up_rather_than_truncating():
    """Pinned on its own, because every other conversion in this file is
    also satisfied by a truncating implementation off by one minor unit in
    the one direction the other assertions happen not to exercise.
    """
    assert api.Money("270.00").convert_to("EUR", rates()) == api.Money("246.65", "EUR")
    assert api.Money("270.00").convert_to("EUR", rates()) != api.Money("246.64", "EUR")


def test_conversion_never_becomes_implicit_in_arithmetic():
    """Adding two currencies still raises. Conversion is a call, not a
    coercion: an implicit one inside `+` would pick a table out of the air
    and a direction out of whichever operand came first.
    """
    with pytest.raises(api.CurrencyMismatch):
        api.Money("10.00", "USD") + api.Money("10.00", "EUR")


def test_order_total_in_equals_the_converted_total():
    order = build_order()
    table = rates()
    assert order.total == api.Money("270.00")
    assert order.total_in("EUR", table) == order.total.convert_to("EUR", table)
    assert order.total_in("EUR", table) == api.Money("246.65", "EUR")
    assert order.total_in("JPY", table) == api.Money("40574", "JPY")


def test_breakdown_in_carries_the_same_four_keys_in_the_target_currency():
    order = build_order()
    converted = order.breakdown_in("EUR", rates())
    assert sorted(converted) == sorted(order.breakdown())
    assert [value.currency for value in converted.values()] == ["EUR"] * 4
    assert converted == {
        "subtotal": api.Money("228.38", "EUR"),   # 250.00 * 0.9135 = 228.375
        "discount": api.Money("0.00", "EUR"),
        "tax": api.Money("18.27", "EUR"),         # 20.00 * 0.9135 = 18.27
        "total": api.Money("246.65", "EUR"),      # 270.00 * 0.9135 = 246.645
    }


def test_each_figure_is_converted_independently_so_the_sum_may_differ_by_one_unit():
    """Four roundings, not one. Stated rather than hidden.

    Under a 50% discount the four converted figures do not add back up to
    the converted total: 228.38 - 114.19 + 9.14 is 123.33 and the converted
    total is 123.32. Conserving across a conversion needs a residual line,
    and inventing one would report a figure nobody charged. `breakdown()`
    in the order's own currency conserves exactly and stays the authority.
    """
    order = build_order([api.PercentageDiscount("half", "50")])
    assert order.breakdown() == {
        "subtotal": api.Money("250.00"),
        "discount": api.Money("125.00"),
        "tax": api.Money("10.00"),
        "total": api.Money("135.00"),
    }
    converted = order.breakdown_in("EUR", rates())
    assert converted == {
        "subtotal": api.Money("228.38", "EUR"),   # 228.375
        "discount": api.Money("114.19", "EUR"),   # 114.1875
        "tax": api.Money("9.14", "EUR"),          # 9.135
        "total": api.Money("123.32", "EUR"),      # 123.3225
    }
    arithmetic = converted["subtotal"] - converted["discount"] + converted["tax"]
    assert arithmetic == api.Money("123.33", "EUR")
    assert arithmetic - converted["total"] == api.Money("0.01", "EUR")


def test_report_net_in_equals_the_converted_net():
    order = build_order([api.PercentageDiscount("half", "50")])
    order.place()
    report = api.RevenueReport([order])
    table = rates()
    assert report.net == api.Money("125.00")
    assert report.net_in("EUR", table) == report.net.convert_to("EUR", table)
    # 125.00 * 0.9135 = 114.1875, half up to two places.
    assert report.net_in("EUR", table) == api.Money("114.19", "EUR")
    assert report.net_in("BHD", table) == api.Money("47.110", "BHD")


def test_an_empty_reports_net_in_is_zero_in_the_currency_that_was_asked_for():
    """And it asks for no rate at all.

    An empty report's `net` is a zero in this module's default currency,
    which is an artifact of there being no order to read a currency off.
    Converting it would demand a USD rate a report of no orders has no
    reason to need. The empty `RateTable` is what makes that falsifiable.
    """
    report = api.RevenueReport([])
    assert report.net_in("EUR", api.RateTable()) == api.Money("0.00", "EUR")
    assert report.net_in("JPY", api.RateTable()) == api.Money("0", "JPY")
    assert report.net_in("EUR", api.RateTable()) != api.Money("0.00", "USD")
