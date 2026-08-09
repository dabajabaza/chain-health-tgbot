"""Storage bounds that have to be checked on the way in.

Not part of the ORM schema — a validation bound. Python ints are unbounded, so
a value that does not fit a 64-bit INTEGER is rejected by the driver at bind
time (OverflowError deep inside the query) rather than by any check of ours.
Values arriving from outside — a forged callback_data, a hand-typed id — must
therefore be bounded before they reach a query.
"""

SQLITE_INT_MAX = 2**63 - 1
