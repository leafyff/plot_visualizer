"""
plot_visualizer
===============

Plot a real-valued function of one variable.

The user-facing surface is unchanged from the older regex-based version, but
the parsing/analysis backend is now SymPy:

  * Real symbolic parser — handles ``2x``, ``sin x``, ``sin^2(x)``,
    nested calls, fractions, function exponentiation, ...
  * Titles are rendered via ``sympy.latex(expr)`` — correct for any expression.
  * Visible domain comes from ``sympy.calculus.util.continuous_domain``,
    so ``sqrt(x-30)`` auto-frames ``x >= 30`` and ``arcsin(x)`` ``[-1, 1]``.
  * The function is compiled to NumPy with ``sympy.lambdify`` — no ``eval``.
  * Numerical probes remain as a fallback when symbolic analysis can't decide.
"""

from __future__ import annotations

import re
import sys

import matplotlib

# When running inside Pyodide (browser / WebAssembly), force the headless Agg
# backend so importing pyplot doesn't try to attach to a GUI toolkit.
if sys.platform == 'emscripten':
    matplotlib.use('Agg')

import matplotlib.pyplot as plt
import mpmath
import numpy as np
import sympy as sp
from sympy.calculus.util import continuous_domain
from sympy.parsing.sympy_parser import (
    convert_xor,
    function_exponentiation,
    implicit_application,
    implicit_multiplication,
    parse_expr,
    standard_transformations,
)

FUNC_QUALITY = 10000   # samples used to draw the curve
PROBE_QUALITY = 2000   # samples used by the numerical fallback probe
DEFAULT_X_BORDERS = (-12.0, 12.0)
DEFAULT_Y_BORDERS = (-12.0, 12.0)
INFINITY_RANGE = (-1000.0, 1000.0)

_X = sp.Symbol('x', real=True)

# Map math-style names to SymPy primitives. Anything not in here that the
# parser encounters becomes a free Symbol, which we reject — that's how we
# catch typos like ``xsin(x)`` (meant ``x*sin(x)``).
_LOCAL_DICT = {
    'x': _X,
    # --- trigonometric ---
    'sin': sp.sin, 'cos': sp.cos,
    'tan': sp.tan, 'tg': sp.tan,
    'cot': sp.cot, 'ctg': sp.cot,
    'sec': sp.sec, 'csc': sp.csc, 'cosec': sp.csc,
    # --- inverse trigonometric ---
    'asin': sp.asin, 'arcsin': sp.asin,
    'acos': sp.acos, 'arccos': sp.acos,
    'atan': sp.atan, 'arctan': sp.atan, 'arctg': sp.atan,
    'acot': sp.acot, 'arccot': sp.acot, 'arcctg': sp.acot,
    # --- hyperbolic ---
    'sinh': sp.sinh, 'cosh': sp.cosh, 'tanh': sp.tanh,
    'coth': sp.coth, 'sech': sp.sech, 'csch': sp.csch,
    # --- inverse hyperbolic ---
    'asinh': sp.asinh, 'arcsinh': sp.asinh,
    'acosh': sp.acosh, 'arccosh': sp.acosh,
    'atanh': sp.atanh, 'arctanh': sp.atanh,
    # --- exponential / logarithmic / roots ---
    'exp': sp.exp, 'ln': sp.log, 'log': sp.log,
    'sqrt': sp.sqrt, 'cbrt': sp.cbrt,
    # --- rounding / misc ---
    'abs': sp.Abs, 'sign': sp.sign, 'sgn': sp.sign,
    'floor': sp.floor, 'ceil': sp.ceiling, 'ceiling': sp.ceiling,
    # --- special functions ---
    'gamma': sp.gamma, 'factorial': sp.factorial,
    'loggamma': sp.loggamma, 'lgamma': sp.loggamma,
    'digamma': sp.digamma, 'polygamma': sp.polygamma, 'beta': sp.beta,
    'erf': sp.erf, 'erfc': sp.erfc,
    'zeta': sp.zeta,
    'Si': sp.Si, 'si': sp.Si, 'Ci': sp.Ci, 'ci': sp.Ci,
    'Ei': sp.Ei, 'ei': sp.Ei, 'li': sp.li,
    'besselj': sp.besselj, 'bessely': sp.bessely,
    'besseli': sp.besseli, 'besselk': sp.besselk,
    'LambertW': sp.LambertW, 'lambertw': sp.LambertW, 'W': sp.LambertW,
    # --- constants ---
    'pi': sp.pi, 'e': sp.E,
}

_TRANSFORMATIONS = standard_transformations + (
    convert_xor,                # ^ -> **
    implicit_multiplication,    # 2x -> 2*x, x(y+1) -> x*(y+1)
    implicit_application,       # sin x -> sin(x)
    function_exponentiation,    # sin^2(x) -> sin(x)**2
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _rewrite_calls(text: str, name: str, rewrite) -> str:
    """Replace every balanced ``name(...)`` call in ``text`` with ``rewrite(inner)``.

    The lookbehind skips already-namespaced occurrences (``np.log(``,
    ``sp.log(``), so the helper is safe to apply iteratively for nested calls.
    """
    pattern = re.compile(r'(?<![a-zA-Z0-9_.])' + re.escape(name) + r'\(')
    for _ in range(16):
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
                break
            spans.append((m.start(), j + 1, text[m.end():j]))
            pos = j + 1
        if not spans:
            return text
        parts, cursor = [], 0
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


_LOG_SENTINEL = '__SPLOG__'  # uppercase so the case-sensitive regex won't re-match


def _swap_log_args(text: str) -> str:
    """Convert Wolfram-style ``log(base, arg)`` to SymPy's ``log(arg, base)``.
    Single-argument ``log(x)`` passes through. Handles nesting via a sentinel
    so each call site is rewritten exactly once."""
    def swap(inner: str) -> str:
        depth, comma_at = 0, -1
        for i, c in enumerate(inner):
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            elif c == ',' and depth == 0:
                comma_at = i
                break
        if comma_at < 0:
            return f'{_LOG_SENTINEL}({inner})'
        base = inner[:comma_at].strip()
        arg = inner[comma_at + 1:].strip()
        return f'{_LOG_SENTINEL}({arg}, {base})'
    return _rewrite_calls(text, 'log', swap).replace(_LOG_SENTINEL, 'log')


def _parse(text: str) -> sp.Expr:
    """Parse a math expression into a SymPy expression in the single variable ``x``."""
    text = _swap_log_args(text.strip())
    if not text:
        raise ValueError("empty expression")
    expr = parse_expr(text, local_dict=_LOCAL_DICT, transformations=_TRANSFORMATIONS)
    extras = expr.free_symbols - {_X}
    if extras:
        names = ', '.join(sorted(str(s) for s in extras))
        raise ValueError(
            f"unknown symbol(s) in expression: {names}. "
            f"Did you forget a '*'? (e.g. write x*sin(x), not xsin(x))"
        )
    return expr


def _vectorize_mpmath(fn):
    """Wrap a scalar mpmath function as a NumPy-broadcasting ufunc.

    Special functions (gamma, erf, Bessel, …) have no NumPy implementation, so
    we evaluate them element-wise via mpmath. Complex / undefined results
    (e.g. gamma at a pole) collapse to NaN, which the sampler then drops.
    """
    def scalar(*args):
        try:
            value = complex(fn(*args))
        except (ValueError, TypeError, ZeroDivisionError, ArithmeticError):
            return float('nan')
        return value.real if abs(value.imag) < 1e-12 else float('nan')
    return np.vectorize(scalar, otypes=[float])


# Names emitted by SymPy's NumPy printer that NumPy itself can't evaluate,
# mapped to mpmath-backed vectorized implementations. Providing these in the
# lambdify module list also tells the printer to emit the bare name.
_SPECIAL_FUNCS = {
    'gamma': _vectorize_mpmath(mpmath.gamma),
    'loggamma': _vectorize_mpmath(mpmath.loggamma),
    'polygamma': _vectorize_mpmath(mpmath.polygamma),
    'factorial': _vectorize_mpmath(mpmath.factorial),
    'beta': _vectorize_mpmath(mpmath.beta),
    'erf': _vectorize_mpmath(mpmath.erf),
    'erfc': _vectorize_mpmath(mpmath.erfc),
    'zeta': _vectorize_mpmath(mpmath.zeta),
    'Si': _vectorize_mpmath(mpmath.si),
    'Ci': _vectorize_mpmath(mpmath.ci),
    'Ei': _vectorize_mpmath(mpmath.ei),
    'li': _vectorize_mpmath(mpmath.li),
    'besselj': _vectorize_mpmath(mpmath.besselj),
    'bessely': _vectorize_mpmath(mpmath.bessely),
    'besseli': _vectorize_mpmath(mpmath.besseli),
    'besselk': _vectorize_mpmath(mpmath.besselk),
    'LambertW': _vectorize_mpmath(mpmath.lambertw),
}


def _compile(expr: sp.Expr):
    """Compile a SymPy expression into a fast function of x.

    Common functions use NumPy directly; special functions fall back to the
    mpmath-backed implementations in ``_SPECIAL_FUNCS``.
    """
    return sp.lambdify(_X, expr, modules=[_SPECIAL_FUNCS, 'numpy'])


def _to_latex(expr: sp.Expr) -> str:
    """Render a SymPy expression to a LaTeX string for matplotlib titles."""
    return sp.latex(expr, inv_trig_style='full')


# ---------------------------------------------------------------------------
# Domain analysis
# ---------------------------------------------------------------------------

def _domain(expr: sp.Expr):
    """Continuous real domain of ``expr``, or ``None`` if symbolic analysis fails."""
    try:
        return continuous_domain(expr, _X, sp.S.Reals)
    except Exception:
        return None


def _interval_endpoints(iv: sp.Interval) -> tuple[float | None, float | None]:
    """(inf, sup) of a SymPy Interval, with ``None`` for unbounded ends."""
    lo = float(iv.start) if iv.start.is_finite else None
    hi = float(iv.end) if iv.end.is_finite else None
    return lo, hi


def _domain_to_x_borders(domain) -> tuple[float, float] | None:
    """Convert a SymPy domain set into plot borders. Returns ``None`` when the
    set is too complex to summarize (caller falls back to the numerical probe)."""
    default_iv = sp.Interval(*DEFAULT_X_BORDERS)
    dx_lo, dx_hi = DEFAULT_X_BORDERS
    default_width = dx_hi - dx_lo

    try:
        if default_iv.is_subset(domain):
            return DEFAULT_X_BORDERS
        active = domain.intersect(default_iv)
    except Exception:
        return None

    if active is sp.S.EmptySet or active.is_empty:
        # Function lives outside the default window — anchor on its real edge.
        try:
            d_inf = float(domain.inf) if domain.inf.is_finite else None
            d_sup = float(domain.sup) if domain.sup.is_finite else None
        except Exception:
            return None
        if d_inf is not None and d_sup is not None:
            span = d_sup - d_inf
            pad = 0.05 * span if span > 0 else 0.5
            return (d_inf - pad, d_sup + pad)
        if d_inf is not None:
            return (d_inf - 0.05 * default_width, d_inf + default_width)
        if d_sup is not None:
            return (d_sup - default_width, d_sup + 0.05 * default_width)
        return None

    # Function has some presence in the default window.
    if isinstance(active, sp.Interval):
        a_lo, a_hi = _interval_endpoints(active)
        if a_lo is None or a_hi is None:
            return DEFAULT_X_BORDERS
        span = a_hi - a_lo
        # Domain effectively fills the default window — keep the default.
        if span >= 0.95 * default_width:
            return DEFAULT_X_BORDERS
        pad = 0.05 * span if span > 0 else 0.5
        return (a_lo - pad, a_hi + pad)

    # Multi-interval (``1/x``, ``tan(x)``, ``sqrt(x^2 - 25)``, ...) — use the
    # default window and let asymptote-breaking handle the gaps.
    return DEFAULT_X_BORDERS


# ---------------------------------------------------------------------------
# Numerical fallback probe (when symbolic analysis cannot decide)
# ---------------------------------------------------------------------------

def _probe(func, x_range: tuple[float, float]):
    """Sample ``func`` over ``x_range``. Returns (fx_lo, fx_hi, fy_lo, fy_hi, ok)."""
    x = np.linspace(x_range[0], x_range[1], PROBE_QUALITY)
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        y = func(x)
    y = np.broadcast_to(np.asarray(y, dtype=float), x.shape)
    finite = np.isfinite(y)
    if not finite.any():
        return x_range[0], x_range[1], DEFAULT_Y_BORDERS[0], DEFAULT_Y_BORDERS[1], False
    fx, fy = x[finite], y[finite]
    return float(fx.min()), float(fx.max()), float(fy.min()), float(fy.max()), True


def _probe_until_finite(func):
    """Try wider and wider probe windows until a finite sample appears."""
    for scale in (1, 10, 100, 1000):
        probe_range = (DEFAULT_X_BORDERS[0] * scale, DEFAULT_X_BORDERS[1] * scale)
        fx_lo, fx_hi, _, _, found = _probe(func, probe_range)
        if found:
            return probe_range, fx_lo, fx_hi
    return None


def _adapt_x_from_probe(fx_lo, fx_hi, probe_range):
    """X borders from a numerical probe (mirrors the symbolic-path logic)."""
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

    if not touches_lo and not touches_hi:
        span = fx_hi - fx_lo
        if span <= 0:
            return DEFAULT_X_BORDERS
        pad = 0.05 * span
        return (fx_lo - pad, fx_hi + pad)
    if touches_hi and not touches_lo:
        return (fx_lo - 0.05 * default_width, fx_lo + default_width)
    if touches_lo and not touches_hi:
        return (fx_hi - default_width, fx_hi + 0.05 * default_width)
    return DEFAULT_X_BORDERS


def _adapt_y(fy_lo: float, fy_hi: float) -> tuple[float, float]:
    """Fit Y to the observed range, capped by the default Y window. Functions
    that live outside default (e.g. ``x^2 + 1000``) get their own window;
    effectively-constant functions (e.g. ``sin^2(x) + cos^2(x)``) center on the
    value rather than zooming into floating-point noise."""
    dy_lo, dy_hi = DEFAULT_Y_BORDERS
    default_span = dy_hi - dy_lo
    span = fy_hi - fy_lo

    if span < 1e-6 * default_span:
        value = 0.5 * (fy_lo + fy_hi)
        if dy_lo <= value <= dy_hi:
            return DEFAULT_Y_BORDERS
        return (value - 1.0, value + 1.0)

    if fy_lo > dy_hi or fy_hi < dy_lo:
        pad = 0.1 * span
        return (fy_lo - pad, fy_hi + pad)

    y_lo = max(fy_lo, dy_lo)
    y_hi = min(fy_hi, dy_hi)
    capped = y_hi - y_lo
    if capped >= 0.9 * default_span:
        return DEFAULT_Y_BORDERS
    pad = 0.1 * capped
    return (y_lo - pad, y_hi + pad)


def _resolve_x_borders(expr: sp.Expr, func) -> tuple[float, float]:
    """Pick adaptive X borders for a single curve. Symbolic domain first,
    numerical probe as fallback."""
    domain = _domain(expr)
    if domain is not None:
        borders = _domain_to_x_borders(domain)
        if borders is not None:
            return borders
    probe_result = _probe_until_finite(func)
    if probe_result is None:
        return DEFAULT_X_BORDERS
    probe_range, fx_lo, fx_hi = probe_result
    return _adapt_x_from_probe(fx_lo, fx_hi, probe_range)


def _resolve_borders(expr: sp.Expr, func, x_borders, y_borders):
    """Fill in unspecified borders for a single curve."""
    if x_borders is None:
        x_borders = _resolve_x_borders(expr, func)
    if y_borders is None:
        _, _, fy_lo, fy_hi, found = _probe(func, x_borders)
        y_borders = _adapt_y(fy_lo, fy_hi) if found else DEFAULT_Y_BORDERS
    return x_borders, y_borders


def _resolve_borders_multi(parsed, x_borders, y_borders):
    """Pick borders covering several curves. Each curve gets its own adaptive
    X (or the user's, if given); we then take the bounding box and re-probe Y
    across all curves on the final X window."""
    if x_borders is None:
        spans = [_resolve_x_borders(p['expr'], p['func']) for p in parsed]
        x_borders = (min(s[0] for s in spans), max(s[1] for s in spans))
    else:
        x_borders = (float(x_borders[0]), float(x_borders[1]))

    if y_borders is None:
        y_los, y_his = [], []
        for p in parsed:
            _, _, fy_lo, fy_hi, found = _probe(p['func'], x_borders)
            if found:
                y_los.append(fy_lo)
                y_his.append(fy_hi)
        y_borders = _adapt_y(min(y_los), max(y_his)) if y_los else DEFAULT_Y_BORDERS
    else:
        y_borders = (float(y_borders[0]), float(y_borders[1]))

    return x_borders, y_borders


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def _get_interval(text: str) -> tuple[float, float] | None:
    """Parse a borders string. ``""`` -> None (caller picks adaptively);
    ``"R"`` -> INFINITY_RANGE; otherwise two comma- or space-separated floats."""
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
    func_input = input("y = ").strip()
    domain_x = input("X borders (2 comma-separated values, blank = auto): ").strip()
    range_y = input("Y borders (2 comma-separated values, blank = auto): ").strip()
    func_name = input("Write LaTeX name of func (skip for default): ").strip()
    return (
        func_input,
        _get_interval(domain_x),
        _get_interval(range_y),
        func_name,
    )


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def _sample(func, x1: float, x2: float, y_span: float):
    """Sample ``func`` over [x1, x2] for plotting.

    Non-finite samples are dropped. Vertical asymptotes (``tan(x)``, ``1/x``)
    are detected and broken with NaN so matplotlib doesn't draw a straight
    line across the discontinuity.
    """
    x = np.linspace(x1, x2, FUNC_QUALITY)
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        y = func(x)
    y = np.broadcast_to(np.asarray(y, dtype=float), x.shape).copy()

    finite = np.isfinite(y)
    x, y = x[finite], y[finite]

    if y.size >= 2 and y_span > 0:
        # Discontinuity: adjacent samples of opposite sign whose magnitudes
        # both massively exceed the visible Y window.
        big = np.minimum(np.abs(y[:-1]), np.abs(y[1:])) > 10 * y_span
        flip = np.sign(y[:-1]) * np.sign(y[1:]) < 0
        breaks = big & flip
        if breaks.any():
            y[1:][breaks] = np.nan
    return x, y


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DEFAULT_CURVE_COLOR = '#e41a1c'


def render_plot(curves,
                x_borders=None,
                y_borders=None,
                title: str = '',
                dpi: int = 110,
                figsize: tuple[float, float] = (8.0, 5.0)) -> dict:
    """Render one or more curves to PNG — entry point for embedding (web / scripts).

    Parameters
    ----------
    curves
        Iterable of curve specs. Each spec may be:

        * a string expression (the default color is used), or
        * a 2-element sequence ``(expression, color)`` where ``color`` is any
          matplotlib-compatible color (hex like ``"#e41a1c"`` or a name).
    x_borders, y_borders
        Optional 2-tuples/lists; ``None`` means adaptive.
    title
        Optional explicit LaTeX title. With a single curve, displayed as
        ``y = <title>``; with several curves, displayed as-is (and curves are
        also labelled in a legend).
    dpi, figsize
        Matplotlib rendering options.

    Returns
    -------
    dict with keys: ``png`` (bytes), ``x1, x2, y1, y2`` (the borders that were
    actually used), and ``errors`` (per-curve parse errors as strings — the
    plot still renders for the curves that parsed successfully).
    """
    from io import BytesIO
    from matplotlib.figure import Figure

    if x_borders is not None:
        x_borders = (float(x_borders[0]), float(x_borders[1]))
    if y_borders is not None:
        y_borders = (float(y_borders[0]), float(y_borders[1]))

    parsed: list[dict] = []
    errors: list[str] = []
    for i, item in enumerate(curves):
        if isinstance(item, str):
            text, color = item.strip(), DEFAULT_CURVE_COLOR
        else:
            seq = list(item)
            text = str(seq[0]).strip() if seq else ''
            color = str(seq[1]) if len(seq) > 1 and seq[1] else DEFAULT_CURVE_COLOR
        if not text:
            continue
        try:
            expr = _parse(text)
            func = _compile(expr)
        except Exception as e:
            errors.append(f"curve {i + 1} ({text!r}): {e}")
            continue
        parsed.append({'expr': expr, 'func': func, 'color': color, 'text': text})

    if not parsed:
        msg = "no valid curves"
        if errors:
            msg += ":\n" + "\n".join(errors)
        raise ValueError(msg)

    (x1, x2), (y1, y2) = _resolve_borders_multi(parsed, x_borders, y_borders)

    fig = Figure(figsize=figsize, dpi=dpi)
    ax = fig.add_subplot(111)

    for p in parsed:
        x, y = _sample(p['func'], x1, x2, y2 - y1)
        ax.plot(x, y, color=p['color'], linewidth=1.6,
                label=f"$y = {_to_latex(p['expr'])}$")

    explicit_title = title.strip() if title else ''
    if explicit_title:
        if len(parsed) == 1:
            ax.set_title(f"$y = {explicit_title}$")
        else:
            ax.set_title(f"${explicit_title}$")
    elif len(parsed) == 1:
        ax.set_title(f"$y = {_to_latex(parsed[0]['expr'])}$")

    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.grid(True)
    ax.set_xlim(x1, x2)
    ax.set_ylim(y1, y2)

    if len(parsed) > 1:
        ax.legend(loc='best', fontsize='small', framealpha=0.9)

    buf = BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    return {
        'png': buf.getvalue(),
        'x1': x1, 'x2': x2,
        'y1': y1, 'y2': y2,
        'errors': errors,
    }


def main():
    func_input, x_borders, y_borders, func_name = _input_func()
    expr = _parse(func_input)
    func = _compile(expr)

    (x1, x2), (y1, y2) = _resolve_borders(expr, func, x_borders, y_borders)
    x, y = _sample(func, x1, x2, y2 - y1)

    title = func_name if func_name else _to_latex(expr)

    plt.plot(x, y, color='red')
    plt.title(f"Graph of $y = {title}$")
    plt.xlabel("x")
    plt.ylabel("y")
    plt.grid(True)
    plt.xlim(x1, x2)
    plt.ylim(y1, y2)
    plt.show()
    # plt.savefig("func_plot.png", dpi=300)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Program aborted")
    except (sp.SympifyError, ValueError, SyntaxError, TypeError,
            NameError, ZeroDivisionError) as e:
        print(f"Error: {e}")
