import re

import matplotlib.pyplot as plt
import numpy as np

FUNC_QUALITY = 10000  # Number of dots in function
DEFAULT_X_BORDERS = (-12.0, 12.0)
DEFAULT_Y_BORDERS = (-12.0, 12.0)
INFINITY_RANGE = (-1000.0, 1000.0)

FUNC_REPLACEMENTS = {
    r'\btg\b': 'np.tan',
    r'\btan\b': 'np.tan',
    r'\bctg\b': '1/np.tan',
    r'\bcot\b': '1/np.tan',
    r'\bsin\b': 'np.sin',
    r'\bcos\b': 'np.cos',
    r'\barcsin\b': 'np.arcsin',
    r'\barccos\b': 'np.arccos',
    r'\barctan\b': 'np.arctan',
    r'\barctg\b': 'np.arctan',
    r'\bln\b': 'np.log',
    r'\bsqrt\b': 'np.sqrt',
    r'\bpi\b': 'np.pi',
    r'\babs\b': 'np.abs',
}

LATEX_REPLACEMENTS = {
    r'\btg\b': r'\\tan',
    r'\btan\b': r'\\tan',
    r'\bctg\b': r'\\cot',
    r'\bcot\b': r'\\cot',
    r'\bsin\b': r'\\sin',
    r'\bcos\b': r'\\cos',
    r'\barcsin\b': r'\\arcsin',
    r'\barccos\b': r'\\arccos',
    r'\barctan\b': r'\\arctan',
    r'\barctg\b': r'\\arctan',
    r'\bln\b': r'\\ln',
    r'\blog\b': r'\\ln',
    r'\bpi\b': r'\\pi',
}


def _rewrite_calls(text: str, name: str, rewrite) -> str:
    """
    Replace every balanced `name(...)` call in `text` using `rewrite(inner)`,
    where `inner` is the raw text between the matching parentheses.

    The lookbehind skips occurrences already namespaced (e.g. `np.log(`), so the
    helper is safe to apply repeatedly; that lets it handle nested calls like
    `log(log(x))` or `sqrt(sqrt(x))`.
    """
    pattern = re.compile(r'(?<![a-zA-Z0-9_.])' + re.escape(name) + r'\(')
    for _ in range(16):  # bounded by realistic nesting depth
        spans = []
        pos = 0
        while True:
            m = pattern.search(text, pos)
            if not m:
                break
            depth = 1
            j = m.end()
            while j < len(text) and depth > 0:
                c = text[j]
                if c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth != 0:
                break  # unbalanced — leave the rest of the string alone
            spans.append((m.start(), j + 1, text[m.end():j]))
            pos = j + 1
        if not spans:
            return text
        parts = []
        cursor = 0
        for start, end, inner in spans:
            parts.append(text[cursor:start])
            parts.append(rewrite(inner))
            cursor = end
        parts.append(text[cursor:])
        new_text = ''.join(parts)
        if new_text == text:
            return text
        text = new_text
    return text


def _rewrite_log(inner: str) -> str:
    """log(x) -> np.log(x); log(base, x) -> change-of-base via np.log."""
    depth = 0
    comma_at = -1
    for i, c in enumerate(inner):
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif c == ',' and depth == 0:
            comma_at = i
            break
    if comma_at < 0:
        return f'np.log({inner})'
    base = inner[:comma_at].strip()
    arg = inner[comma_at + 1:].strip()
    return f'np.log({arg})/np.log({base})'


def _get_interval(text: str) -> tuple[float, float] | None:
    """
    Parse a borders string. Returns None for empty input (caller picks
    adaptively); "R" expands to INFINITY_RANGE.
    """
    text = text.strip()
    if not text:
        return None
    if text == "R":
        return INFINITY_RANGE
    parts = re.split(r'[,\s]+', text)
    if len(parts) != 2:
        raise ValueError(f"expected two numbers, got {text!r}")
    return float(parts[0]), float(parts[1])


def _input_func():
    """Gets input: function, domain, range and LaTeX name."""
    func_input = input("y = ").strip()
    domain_x = input("X borders (2 comma-separated values, blank = auto): ").strip()
    range_y = input("Y borders (2 comma-separated values, blank = auto): ").strip()
    func_name = input("Write LaTeX name of func (skip for default): ").strip()

    x_borders = _get_interval(domain_x)
    y_borders = _get_interval(range_y)
    return func_input, x_borders, y_borders, func_name


def _probe(func_expression: str, x_range: tuple[float, float]):
    """Sample the expression over `x_range` and return
    (fx_lo, fx_hi, fy_lo, fy_hi, found_finite). When no sample is finite, the
    range / Y default are echoed back and found_finite is False."""
    x = np.linspace(x_range[0], x_range[1], 2000)
    with np.errstate(divide='ignore', invalid='ignore'):
        y = eval(func_expression, {"x": x, "np": np})
    y = np.broadcast_to(np.asarray(y, dtype=float), x.shape)
    finite = np.isfinite(y)
    if not finite.any():
        return x_range[0], x_range[1], DEFAULT_Y_BORDERS[0], DEFAULT_Y_BORDERS[1], False
    fx = x[finite]
    fy = y[finite]
    return float(fx.min()), float(fx.max()), float(fy.min()), float(fy.max()), True


def _probe_until_finite(func_expression: str):
    """Probe at progressively wider x ranges until something is finite. Returns
    (probe_range, fx_lo, fx_hi, fy_lo, fy_hi) or None if nothing was found in
    any range up to ~1000x the default window (e.g. sqrt(x-1e6))."""
    for scale in (1, 10, 100, 1000):
        probe_range = (DEFAULT_X_BORDERS[0] * scale, DEFAULT_X_BORDERS[1] * scale)
        fx_lo, fx_hi, fy_lo, fy_hi, found = _probe(func_expression, probe_range)
        if found:
            return probe_range, fx_lo, fx_hi, fy_lo, fy_hi
    return None


def _adapt_x(fx_lo: float, fx_hi: float, probe_range: tuple[float, float]) -> tuple[float, float]:
    """Pick x borders from the probe's finite domain.

    Two regimes:
      * Default-range probe found samples: narrow only when the domain is a
        strict subset of the default window (sqrt(x), arcsin(x), ...).
      * Expanded probe — the function lives outside the default window
        (sqrt(x-30)): anchor on the edge of the finite domain and extend by a
        default-width slice so the curve has room to develop.
    """
    probe_lo, probe_hi = probe_range
    eps = 0.005 * (probe_hi - probe_lo)
    touches_lo = fx_lo <= probe_lo + eps
    touches_hi = fx_hi >= probe_hi - eps

    dx_lo, dx_hi = DEFAULT_X_BORDERS
    default_width = dx_hi - dx_lo

    if probe_range == DEFAULT_X_BORDERS:
        margin = 0.02 * default_width
        if fx_lo <= dx_lo + margin and fx_hi >= dx_hi - margin:
            return DEFAULT_X_BORDERS
        span = fx_hi - fx_lo
        if span <= 0:
            return DEFAULT_X_BORDERS
        pad = 0.05 * span
        return (fx_lo - pad, fx_hi + pad)

    # Expanded probe.
    if not touches_lo and not touches_hi:
        span = fx_hi - fx_lo
        if span <= 0:
            return DEFAULT_X_BORDERS
        pad = 0.05 * span
        return (fx_lo - pad, fx_hi + pad)
    if touches_hi and not touches_lo:
        # Function extends to the right past the probe — anchor lo edge.
        pad = 0.05 * default_width
        return (fx_lo - pad, fx_lo + default_width)
    if touches_lo and not touches_hi:
        pad = 0.05 * default_width
        return (fx_hi - default_width, fx_hi + pad)
    return DEFAULT_X_BORDERS


def _adapt_y(fy_lo: float, fy_hi: float) -> tuple[float, float]:
    """Fit y borders to the observed range, capped by the default window so
    unbounded functions (tan, 1/x) don't blow the view up. When the function
    lives entirely outside the default window (e.g. x^2 + 1000), use its own
    range — capping would produce a blank plot."""
    dy_lo, dy_hi = DEFAULT_Y_BORDERS
    default_span = dy_hi - dy_lo

    if fy_lo > dy_hi or fy_hi < dy_lo:
        span = fy_hi - fy_lo
        if span <= 0:
            return (fy_lo - 1.0, fy_lo + 1.0)
        pad = 0.1 * span
        return (fy_lo - pad, fy_hi + pad)

    y_lo = max(fy_lo, dy_lo)
    y_hi = min(fy_hi, dy_hi)
    if y_hi <= y_lo:
        return DEFAULT_Y_BORDERS
    span = y_hi - y_lo
    if span >= 0.9 * default_span:
        return DEFAULT_Y_BORDERS
    pad = 0.1 * span
    return (y_lo - pad, y_hi + pad)


def _resolve_borders(func_expression: str, x_borders, y_borders):
    """Fill in any unspecified (None) borders by probing the function."""
    if x_borders is not None and y_borders is not None:
        return x_borders, y_borders

    if x_borders is not None:
        # User pinned X — probe within it, only Y may need adapting.
        _, _, fy_lo, fy_hi, _ = _probe(func_expression, x_borders)
        if y_borders is None:
            y_borders = _adapt_y(fy_lo, fy_hi)
        return x_borders, y_borders

    # X is None: expand the probe until we find finite samples.
    result = _probe_until_finite(func_expression)
    if result is None:
        return DEFAULT_X_BORDERS, (y_borders if y_borders is not None else DEFAULT_Y_BORDERS)

    probe_range, fx_lo, fx_hi, fy_lo, fy_hi = result
    x_borders = _adapt_x(fx_lo, fx_hi, probe_range)
    if y_borders is None:
        # Re-probe inside the chosen window for a clean y fit.
        _, _, fy_lo, fy_hi, _ = _probe(func_expression, x_borders)
        y_borders = _adapt_y(fy_lo, fy_hi)
    return x_borders, y_borders


def _process(expression: str) -> str:
    """Convert a Wolfram-style expression into a NumPy-friendly Python expression."""
    expression = expression.replace('^', '**')

    # Implicit multiplication: 2x -> 2*x, 3sin(x) -> 3*sin(x).
    # The lookahead preserves scientific notation like 1e5 or 2E-3.
    expression = re.sub(r'(\d)(?![eE][+\-]?\d)([a-zA-Z(])', r'\1*\2', expression)

    # log(base, arg) / log(arg) with balanced parens so nested calls work.
    expression = _rewrite_calls(expression, 'log', _rewrite_log)

    for pattern, replacement in FUNC_REPLACEMENTS.items():
        expression = re.sub(pattern, replacement, expression)

    # Standalone `e` -> np.e (leaves identifiers and 1e5 alone).
    expression = re.sub(r'(?<![a-zA-Z0-9_.])e(?![a-zA-Z0-9_])', 'np.e', expression)
    return expression


def _filter_function(func_expression: str, x1: float, x2: float, y_span: float):
    """
    Sample the expression and prepare the curve for plotting.

      * non-finite samples are dropped (log of negative, division by zero, ...),
      * vertical asymptotes (tan, 1/x, ...) are detected and broken with NaN so
        matplotlib does not draw a straight line across the discontinuity.
    """
    x = np.linspace(x1, x2, FUNC_QUALITY)

    with np.errstate(divide='ignore', invalid='ignore'):
        y = eval(func_expression, {"x": x, "np": np})

    y = np.broadcast_to(np.asarray(y, dtype=float), x.shape).copy()

    finite = np.isfinite(y)
    x, y = x[finite], y[finite]

    if y.size >= 2 and y_span > 0:
        # A discontinuity needs two adjacent samples of opposite sign whose
        # magnitudes both vastly exceed the visible y window.
        big = np.minimum(np.abs(y[:-1]), np.abs(y[1:])) > 10 * y_span
        flip = np.sign(y[:-1]) * np.sign(y[1:]) < 0
        breaks = big & flip
        if breaks.any():
            y[1:][breaks] = np.nan
    return x, y


def _latex_parse(expression: str) -> str:
    """
    Best-effort LaTeX rendering of the original input. Not a complete parser;
    pass an explicit LaTeX name at the prompt for anything elaborate.
    """
    expression = expression.replace('**', '^')

    # sqrt(x) -> \sqrt{x}, with balanced parens so nested args render correctly.
    expression = _rewrite_calls(expression, 'sqrt', lambda inner: r'\sqrt{' + inner + '}')

    for pattern, replacement in LATEX_REPLACEMENTS.items():
        expression = re.sub(pattern, replacement, expression)

    expression = expression.replace('*', '')

    expression = re.sub(r'e\^\(([^)]+)\)', r'e^{\1}', expression)
    expression = re.sub(r'e\^([a-zA-Z0-9_*/+.\-]+)', r'e^{\1}', expression)

    expression = re.sub(r'(\d)([a-zA-Z(])', r'\1 \2', expression)  # 2x -> 2 x
    expression = re.sub(r'([a-zA-Z)])(\()', r'\1 \2', expression)  # x(y+1) -> x (y+1)
    return expression


def main():
    func_input, x_borders, y_borders, func_name = _input_func()
    func_expression = _process(func_input)

    (x1, x2), (y1, y2) = _resolve_borders(func_expression, x_borders, y_borders)

    x, y = _filter_function(func_expression, x1, x2, y2 - y1)

    if func_name == "":
        title = f"Graph of $y = {_latex_parse(func_input)}$"
    else:
        title = f"Graph of $y = {func_name}$"

    plt.plot(x, y, color='red')
    plt.title(title)
    plt.xlabel("x")
    plt.ylabel("y")
    plt.grid(True)
    plt.xlim(x1, x2)
    plt.ylim(y1, y2)
    plt.show()
    # plt.savefig(f"func_plot.png", dpi=300)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Program aborted")
    except (ValueError, SyntaxError, NameError, TypeError, ZeroDivisionError) as e:
        print(f"Error: {e}")
