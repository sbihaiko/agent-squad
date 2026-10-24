"""Run spend. Measured spend only; unknown spend is never counted as zero.

`run_spend` is the sum of measured SDK results. A call that ends with no result (a deadline kill,
an adapter error) has unknown spend: its granted cap stays reserved for funding, so the run never
starts an entry that an unmeasured call may have already paid for.

A run may also cap tokens (#780): input plus output, as the record measures them. The token cap follows the same
rules beside the dollar cap; with no token cap, tokens are never counted against funding.
"""
import math
from dataclasses import dataclass


@dataclass
class Reservation:
    amount: float
    settled: bool = False
    tokens: float = 0       # the token grant; 0 when the run has no token cap


def measured_tokens(usage):
    """Input plus output tokens of a result's usage, or None when the usage is unknown."""
    if not usage or usage.get("tokens_in") is None or usage.get("tokens_out") is None:
        return None
    return usage["tokens_in"] + usage["tokens_out"]


class Budget:
    def __init__(self, cap, tokens=None):
        self.cap = cap
        self.measured = 0.0
        self.reserved = 0.0     # granted to entries now running
        self.unmeasured = 0.0   # granted to entries that ended with no measured result
        self.token_cap = tokens                 # None: no token cap
        self.run_tokens = 0
        self.tokens_reserved = 0
        self.tokens_unmeasured = 0

    def available_tokens(self):
        """The tokens the run can still fund; infinite with no token cap."""
        if self.token_cap is None:
            return math.inf
        return self.token_cap - self.run_tokens - self.tokens_reserved - self.tokens_unmeasured

    def _grant_tokens(self, node_tokens):
        """The token grant for one entry, or None when the run cannot fund it in tokens."""
        if self.token_cap is None:
            return 0
        avail = self.available_tokens()
        if avail <= 0:
            return None
        return min(node_tokens if node_tokens is not None else math.inf, avail)

    @property
    def run_spend(self):
        return self.measured

    @property
    def run_spend_remaining(self):
        return self.cap - self.measured

    def available(self):
        """What the run can still fund: the cap minus measured, running, and unmeasured grants."""
        return self.cap - self.measured - self.reserved - self.unmeasured

    def reserve(self, node_cap, node_min, tokens=None):
        """Grant the lesser of the node cap and the remainder, in dollars and in tokens, atomically.
        None when the run cannot fund it. `tokens` is the node's token cap; None means none."""
        avail = self.available()
        tok = self._grant_tokens(tokens)
        if avail <= 0 or avail < node_min or tok is None:
            return None
        grant = min(node_cap, avail)
        self.reserved += grant
        self.tokens_reserved += tok
        return Reservation(grant, tokens=tok)

    def hold(self, amount, tokens=None):
        """Reserve an exact amount, for a spawn acceptance. None when the run cannot fund it, in dollars or in tokens."""
        if amount > self.available() or amount <= 0:
            return None
        tok = self._grant_tokens(tokens)
        if tok is None or (tokens is not None and self.token_cap is not None and tokens > tok):
            return None
        self.reserved += amount
        self.tokens_reserved += tok
        return Reservation(amount, tokens=tok)

    def settle(self, res, measured, tokens=None):
        """End a reservation. measured is None when the call returned no result; tokens is None when they are unknown."""
        if res.settled:
            return
        res.settled = True
        self.reserved -= res.amount
        self.tokens_reserved -= res.tokens
        if measured is None:
            self.unmeasured += res.amount
        else:
            self.measured += measured
        if tokens is None:          # unknown tokens are never counted as zero: the token grant stays reserved
            self.tokens_unmeasured += res.tokens
        else:
            self.run_tokens += tokens

    def release(self, res):
        """Return a grant that never started a call."""
        if not res.settled:
            res.settled = True
            self.reserved -= res.amount
            self.tokens_reserved -= res.tokens
