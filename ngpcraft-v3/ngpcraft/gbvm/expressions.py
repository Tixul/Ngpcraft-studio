"""GB Studio v4.3.2 shared/lib/rpn tokenizer and shuntingYard (MIT).

No Python eval: the released token precedence, associativity and validation
are kept, including left-associative unary negation.
"""
import math
import re

from ..compiler import CompileError

OPERATORS = {
    "/": "div", "*": "mul", "+": "add", "-": "sub", "%": "mod",
    "!": "not", "&": "bAND", "|": "bOR", "^": "bXOR", "~": "bNOT",
    "==": "eq", "!=": "ne", "<": "lt", "<=": "lte", ">": "gt", ">=": "gte",
    "&&": "and", "||": "or", "<<": "shl", ">>": "shr", "neg": "neg",
}
FUNCTION_ARITY = {"min": 2, "max": 2, "abs": 1, "atan2": 2, "isqrt": 1, "rnd": 1, "neg": 1}
UNARY = {"~", "!", "neg"}
PRECEDENCE = {"~": 14, "!": 14, "neg": 14, "*": 12, "/": 12, "%": 12,
              "+": 11, "-": 11, "<<": 10, ">>": 10, "<": 9, ">": 9, ">=": 9, "<=": 9,
              "==": 8, "!=": 8, "&": 7, "^": 6, "|": 5, "&&": 4, "||": 3}
SPLITTER = re.compile(r"(@[a-f0-9-]{36}@|@engine::[^@]+@|<<|>>|==|!=|>=|>|<=|<|&&|\|\||[+\-*/^%&|~!@(),])")
JS_WHITESPACE = re.compile(r"[\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+")


def tokenize(expression):
    if not isinstance(expression, str):
        raise CompileError(f"expression must be text, got {expression!r}")
    tokens = []
    for raw in filter(None, SPLITTER.split(JS_WHITESPACE.sub("", expression))):
        number = None
        if raw.lower() in ("true", "false"):
            number = int(raw.lower() == "true")
        else:
            try:
                if not re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][0-9]+)?)", raw):
                    raise ValueError("not a JavaScript numeric token")
                number = int(raw, 0) if re.fullmatch(r"0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+", raw) else float(raw)
                if not math.isfinite(number):
                    number = None
            except ValueError:
                pass
        if number is not None:
            tokens.append(("VAL", number))
        elif raw in FUNCTION_ARITY:
            tokens.append(("FUN", raw))
        elif raw in ("(", ")", ","):
            tokens.append(({"(": "LBRACE", ")": "RBRACE", ",": "SEPERATOR"}[raw], raw))
        elif raw in OPERATORS:
            tokens.append(("OP", raw))
        elif re.fullmatch(r"[$A-Z_][0-9A-Z_$]*", raw, re.I | re.ASCII):
            tokens.append(("VAR", raw))
        elif re.fullmatch(r"@([a-z0-9-]{36}|engine::[^@]+)@", raw, re.I | re.ASCII):
            tokens.append(("CONST", raw.replace("@", "")))
        else:
            raise CompileError(f"Unexpected token {raw}")
    original = tokens[:]
    for i, token in enumerate(original):
        if token == ("OP", "-") and (i == 0 or original[i - 1][0] in ("LBRACE", "SEPERATOR", "OP")):
            tokens[i] = ("OP", "neg")
    return tokens


def expression_rpn(expression):
    tokens = tokenize(expression)
    if not tokens:
        return [("VAL", 0)]
    output, operators, functions = [], [], []
    previous = None
    def precedence(token):
        return 4 if token[0] == "FUN" else PRECEDENCE[token[1]] if token[0] == "OP" else -1
    def pop():
        token = operators.pop()
        if token[0] in ("VAL", "VAR", "FUN", "OP"):
            output.append(token)
        return token
    for token in tokens:
        kind, value = token
        if kind in ("VAL", "VAR", "CONST"):
            output.append(token)
        elif kind == "FUN":
            operators.append(token)
            functions.append(token)
        elif kind == "SEPERATOR":
            while operators and operators[-1][0] != "LBRACE":
                pop()
            functions.append(token)
        elif kind == "OP":
            right = value in ("!", "~")
            while operators and (precedence(token) < precedence(operators[-1]) if right
                                 else precedence(token) <= precedence(operators[-1])):
                pop()
            operators.append(token)
        elif kind == "LBRACE":
            if previous and previous[0] in ("VAR", "VAL", "CONST"):
                raise CompileError(f"{previous[1]}() is not a function")
            operators.append(token)
        elif kind == "RBRACE":
            while operators and operators[-1][0] != "LBRACE":
                pop()
            if not operators or operators.pop()[0] != "LBRACE":
                raise CompileError("Mismatched parenthesis.")
            if operators and operators[-1][0] == "FUN":
                function = pop()[1]
                count = 1
                while functions and functions[-1][0] != "FUN":
                    functions.pop()
                    count += 1
                if count != FUNCTION_ARITY[function]:
                    raise CompileError(f'Unexpected number of args passed to function "{function}". Found {count} but expected {FUNCTION_ARITY[function]}')
                if functions:
                    functions.pop()
        previous = token
    while operators:
        if operators[-1][0] in ("LBRACE", "RBRACE"):
            raise CompileError("Mismatched parenthesis.")
        pop()
    count = 0
    for kind, value in output:
        if kind in ("VAL", "VAR", "CONST"):
            count += 1
        else:
            count -= (FUNCTION_ARITY[value] if kind == "FUN" else 1 if value in UNARY else 2) - 1
        if count <= 0:
            raise CompileError(f"Not enough operands for {value}.")
    if count > 1:
        raise CompileError("Invalid expression.")
    return output


def expression_to_script_value(expression):
    """helpers.ts expressionToScriptValue, with invalid input explicitly refused.

    The released helper catches parse errors and substitutes zero; the V3
    contract forbids silently changing an author's expression.
    """
    stack = []
    for kind, value in expression_rpn(expression):
        if kind == "VAL":
            stack.append({"type": "number", "value": value})
        elif kind == "VAR":
            stack.append({"type": "variable", "value": re.sub(r"^0", "", value.replace("$", ""))})
        elif kind == "CONST":
            stack.append({"type": "constant", "value": value})
        else:
            op = OPERATORS[value] if kind == "OP" else value
            unary = value in UNARY if kind == "OP" else FUNCTION_ARITY[value] == 1
            if kind == "FUN" and value == "neg":
                stack.append({"type": "sub", "valueA": {"type": "number", "value": 0}, "valueB": stack.pop()})
            elif unary:
                stack.append({"type": op, "value": stack.pop()})
            else:
                b, a = stack.pop(), stack.pop()
                stack.append({"type": op, "valueA": a, "valueB": b})
    return stack[0]
