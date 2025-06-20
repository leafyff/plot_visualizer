"""
TO BE DONE:
- Input of multiple functions
- Complete LaTeX parser (Tree-sitter)
- Automatic domain recogniser (use wolfram alpha?)
- App creation (Textual)
- Implement gamma function and others
- Add readable ReadMe.md
"""

import numpy as np
import matplotlib.pyplot as plt
import re

FUNC_QUALITY = 10000 # Number of dots in function
DEFAULT_X_BORDERS = (-12, 12)
DEFAULT_Y_BORDERS = (-12, 12)
INFINITY_RANGE = (-1000, 1000)
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
    r'\babs\b': 'np.abs'
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
    r'\bsqrt\b': r'\\sqrt',
    r'\bpi\b': r'\\pi',
}

def _get_interval(input_borders: str, default_borders: tuple[int, int]) -> tuple[int, int]:
    """
    Gets input borders and processes it, giving final borders as a result
    """
    if input_borders == "":
        x1, x2 = default_borders
    elif input_borders == "R":
        x1, x2 = INFINITY_RANGE
    else:
        x1, x2 = input_borders.replace(",", " ").split()
        x1, x2 = float(x1), float(x2)
    return x1, x2

def _input_func():
    """
    Gets input: function, domain, range and LaTeX name
    """
    func_input = input("y = ").strip()
    domain_x = input("X borders (2 comma-separated values): ").strip()
    range_y = input("Y borders (2 comma-separated values): ").strip()
    func_name = input("Write LaTeX name of func (skip for default): ").strip()

    x1, x2 = _get_interval(domain_x, DEFAULT_X_BORDERS)
    y1, y2 = _get_interval(range_y, DEFAULT_Y_BORDERS)

    return func_input, x1, x2, y1, y2, func_name

def _process(expression: str) -> str:
    """
    Corrects the expression from Wolfram Format to Python3 format.
    Uses FUNC_REPLACEMENTS
    """
    expression = expression.replace('^', '**')
    for pattern, replacement in FUNC_REPLACEMENTS.items():
        expression = re.sub(pattern, replacement, expression)

    expression = re.sub(r'log\(([^,]+),\s*([^)]+)\)', r'np.log(\2)/np.log(\1)', expression)
    expression = re.sub(r'e\^\(([^)]+)\)', r'np.exp(\1)', expression)
    expression = re.sub(r'e\^([a-zA-Z0-9_*/+.\-]+)', r'np.exp(\1)', expression)
    expression = re.sub(r'(?<![a-zA-Z0-9_.])e(?![a-zA-Z0-9_])', 'np.e', expression)
    expression = expression.replace('np.np.', 'np.')

    return expression

def _filter_function(func_expression, x1, x2):
    """
    Filters domain of function in order to not get errors while drawing
    """
    x = np.linspace(x1, x2, FUNC_QUALITY)

    with np.errstate(divide='ignore', invalid='ignore'):
        y = eval(func_expression, {"x": x, "np": np})
        if not hasattr(y, '__len__') or (hasattr(y, 'shape') and len(y.shape) == 0):
            y = np.full_like(x, y)

    valid = np.isfinite(y)
    x = x[valid]
    y = y[valid]

    return x, y

def _latex_parse(expression: str) -> str:
    """
    Creates LaTeX name from the expression
    NOTE: It's not complete LaTeX patser and may not work in many cases.
        Use custom function name in that case
    """
    expression = expression.replace('**', '^')  # ** -> ^

    for pattern, replacement in LATEX_REPLACEMENTS.items():
        expression = re.sub(pattern, replacement, expression)

    expression = expression.replace('*', r'')

    expression = re.sub(r'e\^\(([^)]+)\)', r'e^{\1}', expression)
    expression = re.sub(r'e\^([a-zA-Z0-9_*/+.\-]+)', r'e^{\1}', expression)

    expression = re.sub(r'(\d)([a-zA-Z(])', r'\1 \2', expression)  # 2x -> 2 x
    expression = re.sub(r'([a-zA-Z)])(\()', r'\1 \2', expression)  # x(y+1) -> x (y+1)

    return expression


def main():
    func_input, x1, x2, y1, y2, func_name = _input_func()
    func_expression = _process(func_input)

    x, y = _filter_function(func_expression, x1, x2)

    if func_name == "":
        latex_name = _latex_parse(func_input)
        plt.title(f'Graph of $y = {latex_name}$')
    else:
        title = "Graph of $y = " + func_name + "$"
        plt.title(title)

    plt.plot(x, y, color='red')
    plt.xlabel("x")
    plt.ylabel("y")
    plt.grid(True)
    plt.ylim(y1, y2)
    plt.show()
    # plt.savefig(f"func_plot.png", dpi=300)

if __name__ == "__main__":
    try:
        main()
    #except Exception as e:
    #    print("Error in calculations:", e)
    except KeyboardInterrupt:
        print("Program aborted")