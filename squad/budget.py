"""Run spend. Measured spend only; unknown spend is never counted as zero.

`run_spend` is the sum of measured SDK results. A call that ends with no result (a deadline kill,
an adapter error) has unknown spend: its granted cap stays reserved for funding, so the run never
starts an entry that an unmeasured call may have already paid for.
"""
from dataclasses import dataclass


@dataclass
class Reservation:
    amount: float
    settled: bool = False


class Budget:
    def __init__(self, cap):
        self.cap = cap
        self.measured = 0.0
        self.reserved = 0.0     # granted to entries now running
        self.unmeasured = 0.0   # granted to entries that ended with no measured result

    @property
    def run_spend(self):
        return self.measured

    @property
    def run_spend_remaining(self):
        return self.cap - self.measured

    def available(self):
        """What the run can still fund: the cap minus measured, running, and unmeasured grants."""
        return self.cap - self.measured - self.reserved - self.unmeasured

    def reserve(self, node_cap, node_min):
        """Grant the lesser of the node cap and the remainder, atomically. None when the run cannot fund it."""
        avail = self.available()
        if avail <= 0 or avail < node_min:
            return None
        grant = min(node_cap, avail)
        self.reserved += grant
        return Reservation(grant)

    def hold(self, amount):
        """Reserve an exact amount, for a spawn acceptance. None when the run cannot fund it."""
        if amount > self.available() or amount <= 0:
            return None
        self.reserved += amount
        return Reservation(amount)

    def settle(self, res, measured):
        """End a reservation. measured is None when the call returned no result."""
        if res.settled:
            return
        res.settled = True
        self.reserved -= res.amount
        if measured is None:
            self.unmeasured += res.amount
        else:
            self.measured += measured

    def release(self, res):
        """Return a grant that never started a call."""
        if not res.settled:
            res.settled = True
            self.reserved -= res.amount
