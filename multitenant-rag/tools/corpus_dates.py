"""Corpus date -> epoch conversion.

DECISION 2: the application post date must reflect the CORPUS date, not seed
execution time. A corpus `YYYY-MM-DD` becomes the epoch second for
`YYYY-MM-DDT00:00:00Z` — fixed at 00:00:00 **UTC** so the conversion is
deterministic and independent of the machine running the seed.
"""
import datetime as _dt
import re

DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def corpus_date_to_epoch(date_str: str) -> int:
    m = DATE_RE.match((date_str or "").strip())
    if not m:
        raise ValueError(f"corpus date {date_str!r} is not YYYY-MM-DD")
    y, mo, d = (int(x) for x in m.groups())
    return int(_dt.datetime(y, mo, d, 0, 0, 0,
                            tzinfo=_dt.timezone.utc).timestamp())


def epoch_to_corpus_date(epoch: int) -> str:
    return _dt.datetime.fromtimestamp(int(epoch), _dt.timezone.utc).strftime("%Y-%m-%d")
