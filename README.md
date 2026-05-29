# plot_visualizer

### 🔗 Live app: **https://leafyff.github.io/plot_visualizer/**

Plot math functions right in your browser — no install, no server. Type an
expression, get an instant graph.

---

A small command-line tool for plotting mathematical functions of one variable.
You type an expression in a Wolfram-like syntax (e.g. `x*sin(x)`,
`2*ln(x)*sin(x^2)/x + 2*arctg(x)`), the script parses it with
[SymPy](https://www.sympy.org/), figures out the visible domain automatically,
and renders the graph with matplotlib — including a LaTeX title produced by
`sympy.latex(expr)`.

## Examples

| Input | Result |
| --- | --- |
| `y = x*sin(x)` with X borders `-5, 5` and Y borders `-5, 5` | ![xsin(x)](ex1.jpg) |
| `y = 2*ln(x)*sin(x^2)/x + 2*arctg(x)` with X borders `0, 20` and Y borders `-2, 5` | ![ln·sin example](ex2.jpg) |

## Requirements

- Python 3.10+ (uses PEP 604-style type hints, e.g. `tuple[int, int]`)
- See [requirements.txt](requirements.txt) for Python packages

## Installation

```bash
git clone https://github.com/<your-user>/plot_visualizer.git
cd plot_visualizer
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python plot_visualizer.py
```

## Browser version (GitHub Pages)

The same Python module is also runnable in the browser via
[Pyodide](https://pyodide.org/) — no server, no install, just a static page.

### Files involved

- [index.html](index.html) — UI markup
- [style.css](style.css) — styling (dark mode included)
- [app.js](app.js) — loads Pyodide, the math packages, and `plot_visualizer.py`,
  then wires the inputs to `plot_visualizer.render_plot(...)` with a 250 ms debounce
- [plot_visualizer.py](plot_visualizer.py) — same file as for CLI; the
  `render_plot()` function returns PNG bytes plus the borders that were used

### Run locally

```bash
python -m http.server 8000
# open http://localhost:8000
```

### Deploy to GitHub Pages

1. Push the repo to GitHub.
2. **Settings → Pages → Source: Deploy from a branch → Branch: `main` / `(root)` → Save**.
3. After ~1 minute the site is live at `https://<user>.github.io/<repo>/`.

### Caveats

- First load downloads ~20–30 MB (Pyodide runtime + numpy + matplotlib + sympy).
  Cached after that — subsequent visits start in ~1 second.
- Computation happens on the main JS thread, so very heavy expressions can
  briefly stutter the UI. Switching to a Web Worker would fix this; not needed
  for typical inputs.

You will be prompted for four inputs:

1. **`y = `** — the function expression.
2. **`X borders`** — two comma- or space-separated numbers (e.g. `-5, 5`).
   - Leave empty for **adaptive** borders: the script samples the function and trims X to where it's defined. For `sqrt(x)` you'll get roughly `(-0.6, 12.6)`; for `arcsin(x)`, `(-1.1, 1.1)`. Functions defined everywhere fall back to the default range `(-12, 12)`.
   - Type `R` to use a very wide range `(-1000, 1000)` — useful when you want to see global behavior of a quickly-shrinking function.
3. **`Y borders`** — same format as X borders. Controls the visible vertical window via `plt.ylim`.
   - Leave empty for **adaptive** borders: Y is fit to the observed range of the function, capped by the default `(-12, 12)` window. `sqrt(x)` gives `(-0.3, 3.9)`; `e^(-x^2)` gives `(-0.1, 1.1)`; unbounded functions like `1/x` or `tan(x)` fall back to the default.
4. **LaTeX name** — an optional LaTeX-formatted title (inside `$...$`).
   - Leave empty to auto-generate a LaTeX title from the expression.

Close the matplotlib window to exit, or press `Ctrl+C` in the terminal.

### Expression syntax

The expression is parsed by SymPy with implicit-multiplication,
implicit-application, function-exponentiation, and `^`-as-power transformations
enabled. That means a wide, relaxed syntax works out of the box:

**Syntax sugar**

| You can write | Notes |
| --- | --- |
| `^` | exponentiation |
| `2x`, `3sin(x)`, `(x+1)(x+2)` | implicit multiplication (no `*` needed) |
| `sin x`, `cos x`, `tan x` | implicit function application (no parens needed) |
| `sin^2(x)`, `cos^3 x` | function exponentiation |

**Functions**

| Category | Available |
| --- | --- |
| Trigonometric | `sin`, `cos`, `tan`/`tg`, `cot`/`ctg`, `sec`, `csc` |
| Inverse trig | `asin`/`arcsin`, `acos`/`arccos`, `atan`/`arctan`/`arctg`, `acot` |
| Hyperbolic | `sinh`, `cosh`, `tanh`, `coth`, `sech`, `csch`, `asinh`, `acosh`, `atanh` |
| Exp / log / roots | `exp(x)`/`e^x`, `ln`/`log`, `log(base, x)`, `sqrt`, `cbrt` |
| Rounding / sign | `abs`, `sign`/`sgn`, `floor`, `ceil`/`ceiling` |
| Special | `gamma`, `factorial`, `loggamma`, `digamma`, `polygamma(n, x)`, `beta(x, a)`, `erf`, `erfc`, `zeta`, `Si`, `Ci`, `Ei`, `li`, `besselj(n, x)`, `bessely(n, x)`, `besseli(n, x)`, `besselk(n, x)`, `LambertW`/`W` |
| Constants | `pi`, `e` |

Common functions evaluate through NumPy; the special functions fall back to
`mpmath` (so e.g. `gamma(x)` is the continuous Γ, valid for non-integers, and
`factorial(x)` is `gamma(x+1)`). The web UI shows the same list in its
collapsible **Function reference** panel.

Examples:

```
x^2 - 3x + 1
sin x / x
e^(-x^2)
log(2, x)
sin^2(x) + cos^2(x)
2 ln(x) sin(x^2) / x + 2 arctg(x)
```

If you write something the parser doesn't recognize as a function (e.g.
`xsin(x)` — meant to be `x*sin(x)`), the script tells you which symbol is
unknown rather than producing a nonsensical plot.

### Notes on the LaTeX auto-title

The title is produced by `sympy.latex(expr, inv_trig_style='full')`, so it's
correct LaTeX for any expression SymPy understands — fractions render as
`\frac`, roots as `\sqrt{...}`, inverse trig as `\arcsin`, and so on. If you'd
rather supply your own, type it at the fourth prompt (without the surrounding
`$...$`).

## Configuration

A few constants at the top of [plot_visualizer.py](plot_visualizer.py) can be
tweaked:

- `FUNC_QUALITY` — number of sample points used to draw the curve (default `10000`).
- `DEFAULT_X_BORDERS`, `DEFAULT_Y_BORDERS` — used when the user leaves the borders prompt empty.
- `INFINITY_RANGE` — the range used when the user types `R` at the borders prompt.

## How it works

1. **Input** — read the expression, viewport, and optional LaTeX title from stdin.
2. **Parse** — `_parse` swaps Wolfram-style `log(b, x)` to SymPy's `log(x, b)`,
   then hands the string to `sympy.parsing.sympy_parser.parse_expr` with the
   implicit-multiplication / implicit-application / `^`-as-power
   transformations enabled. Free symbols other than `x` are rejected.
3. **Compile** — `_compile` calls `sympy.lambdify(x, expr, modules='numpy')`,
   producing a fast NumPy function. No `eval` is involved.
4. **Adaptive borders** — when borders are left blank, `_domain` runs
   `sympy.calculus.util.continuous_domain` to get the function's symbolic
   domain (e.g. `Interval(30, oo)` for `sqrt(x-30)`) and turns it into a
   sensible plot window. A numerical probe is the fallback when SymPy can't
   decide.
5. **Sample & break asymptotes** — `_sample` evaluates the compiled function
   on `FUNC_QUALITY` points, drops non-finite samples, and inserts NaN at
   vertical asymptotes (opposite signs + magnitudes far past the visible Y
   window) so matplotlib doesn't draw a straight line across the discontinuity.
6. **Title** — `sympy.latex(expr, inv_trig_style='full')` for the default
   title, or the user's own LaTeX string when supplied.
7. **Plot** — matplotlib (`grid=True`, fixed X & Y limits, red curve).
