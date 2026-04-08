"""Realistic poorly-annotated code that benefits from assignment dataflow inference.

This module simulates real-world code where developers skip type annotations
on parameters and locals but assign from typed literals — exactly the case
where assignment-based dataflow inference adds value over annotation-only enrichment.
"""

from __future__ import annotations


# --- String operations via local variables ---


def build_greeting(name, suffix):
    """Assemble a greeting string using local variables."""
    label = "Hello"
    separator = ", "
    result = label + separator + name + suffix
    return result


def slugify(text, replacement):
    """Replace spaces with a given replacement character."""
    slug = text
    dash = replacement
    return slug + dash


def format_tag(key, value):
    """Format a key=value tag string."""
    prefix = "tag:"
    equals = "="
    tag = prefix + key + equals + value
    return tag


def repeat_separator(sep, times):
    """Build a separator string repeated N times."""
    base = "-"
    full_sep = base + sep
    return full_sep * times


def join_parts(a, b, c):
    """Join three string parts with a fixed connector."""
    connector = " | "
    first_join = a + connector + b
    result = first_join + connector + c
    return result


# --- Integer math via local variables ---


def compute_area(width, height):
    """Compute area using locally-assigned integers then overridden by params."""
    w = 0
    h = 0
    w = width
    h = height
    area = w * h
    return area


def accumulate_score(base_points, multiplier, bonus):
    """Compute a score with accumulation."""
    score = 0
    score += base_points
    score += bonus
    total = score * multiplier
    return total


def count_items(items, batch_size):
    """Count batches needed to process items."""
    count = 0
    count += items
    batches = count // batch_size
    return batches


def compute_discount(price, discount_pct):
    """Compute discounted price from integer percent."""
    rate = 100
    discount = price * discount_pct
    net = discount // rate
    result = price - net
    return result


def bounded_increment(current, maximum, step):
    """Increment *current* by *step*, clamped to *maximum*."""
    return min(current + step, maximum)


def sum_range(start, end):
    """Sum integers in a range via local accumulation."""
    total = 0
    count = end - start
    total += count * (start + end)
    total = total // 2
    return total


# --- Mixed: untyped params, some locals from literals ---


def build_report_line(label, count, total):
    """Format a report line with label, percentage count/total, and a % sign."""
    sep = ": "
    pct_label = "%"
    ratio = count * 100
    pct = ratio // total
    line = label + sep + str(pct) + pct_label
    return line


def score_attempt(correct, wrong, skipped):
    """Score a quiz attempt with penalty for wrong answers."""
    penalty = 2
    raw = correct - wrong * penalty
    bonus = 0
    bonus += skipped
    final = raw + bonus
    return final


def classify_score(score):
    """Return grade letter based on score."""
    threshold_a = 90
    threshold_b = 80
    threshold_c = 70
    if score >= threshold_a:
        return "A"
    if score >= threshold_b:
        return "B"
    if score >= threshold_c:
        return "C"
    return "F"


def compute_fibonacci_step(a, b):
    """One step of Fibonacci, no type annotations."""
    next_val = 0
    next_val += a
    next_val += b
    return next_val


def pack_rgb(r, g, b):
    """Pack three channel values into a single integer."""
    shift_r = 16
    shift_g = 8
    packed = r * shift_r + g * shift_g + b
    return packed
