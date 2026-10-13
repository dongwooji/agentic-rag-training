"""Calendar bounds grounded only in explicit question text; no current year."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
import re


class DateExpressionError(ValueError):
    def __init__(self, check_id):
        self.check_id = check_id
        super().__init__(check_id)


@dataclass(frozen=True)
class ExplicitPeriod:
    start_date: str
    end_date: str
    source_text: str


_TOKEN = re.compile(
    r"(?<!\d)(?P<iso>\d{4}-\d{2}-\d{2})(?!\d)"
    r"|(?P<ko>\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)"
    r"|(?P<month>\d{4}\s*년\s*\d{1,2}\s*월)"
    r"|(?P<year>\d{4}\s*년)"
    r"|(?P<short_month>(?<!\d)\d{1,2}\s*월)"
)
_CONNECTOR = re.compile(r"\s*(?:부터|[~～〜–—]|-)\s*")


def explicit_period(question: str) -> ExplicitPeriod | None:
    tokens = list(_TOKEN.finditer(question))
    if not tokens:
        return None
    # Two separate full dates retain the previous membership validation; they
    # are not silently interpreted as one continuous requested period.
    if len(tokens) == 2 and not _CONNECTOR.fullmatch(question[tokens[0].end():tokens[1].start()]):
        if all(x.lastgroup in {"iso", "ko"} for x in tokens):
            return None
        raise DateExpressionError("ambiguous_date_range")
    if len(tokens) > 2:
        raise DateExpressionError("ambiguous_date_range")
    bounds = []
    inherited_year = None
    try:
        for index, token in enumerate(tokens):
            numbers = [int(x) for x in re.findall(r"\d+", token.group())]
            if token.lastgroup in {"iso", "ko"}:
                first = last = date(*numbers)
            elif token.lastgroup == "year":
                first, last = date(numbers[0], 1, 1), date(numbers[0], 12, 31)
            else:
                if token.lastgroup == "short_month":
                    if index == 0 or inherited_year is None:
                        raise DateExpressionError("missing_date_year")
                    year, month = inherited_year, numbers[0]
                else:
                    year, month = numbers
                first = date(year, month, 1)
                last = date(year, month, monthrange(year, month)[1])
            inherited_year = first.year
            bounds.append((first, last))
    except DateExpressionError:
        raise
    except ValueError as error:
        raise DateExpressionError("invalid_calendar_date") from error
    first, last = bounds[0][0], bounds[-1][1]
    if first > last:
        raise DateExpressionError("reversed_date_range")
    end = tokens[-1].end()
    suffix = re.match(r"\s*까지", question[end:])
    if suffix:
        end += suffix.end()
    return ExplicitPeriod(first.isoformat(), last.isoformat(), question[tokens[0].start():end])


def full_dates(question: str) -> list[str]:
    values = []
    for token in _TOKEN.finditer(question):
        if token.lastgroup in {"iso", "ko"}:
            try:
                values.append(date(*[int(x) for x in re.findall(r"\d+", token.group())]).isoformat())
            except ValueError as error:
                raise DateExpressionError("invalid_calendar_date") from error
    return list(dict.fromkeys(values))
