import re
from typing import List


class StreamingTextNormalizer:
    """Stateful text normalizer that processes stream chunks, mapping LaTeX
    and ASCII arrows to clean Unicode characters ONLY when outside code blocks
    (triple backticks or inline single backticks).
    """

    def __init__(self):
        self.buffer = ""
        self.in_code_block = False  # True when inside ``` ... ```
        self.in_inline_code = False  # True when inside ` ... `

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self.buffer += chunk

        # Smart cut-off to handle potential split tokens across chunks
        cut_off = len(self.buffer)

        # 1. LaTeX command cut-off: if buffer ends with \ followed by letters
        # e.g., "\rightar", "\sq" or just "\"
        match_latex = re.search(r"\\[a-zA-Z]*$", self.buffer)
        if match_latex:
            cut_off = match_latex.start()

        # 2. HTML entity cut-off: if buffer ends with & followed by alphanumeric chars (no semicolon)
        # e.g., "&g", "&gt" or just "&"
        match_html = re.search(r"&[a-zA-Z0-9#]*$", self.buffer)
        if match_html:
            cut_off = min(cut_off, match_html.start())

        # 3. Arrow or code block delimiter cut-off
        for pref in ["-", "=", "`", "``"]:
            if self.buffer.endswith(pref):
                cut_off = min(cut_off, len(self.buffer) - len(pref))
                break

        to_process = self.buffer[:cut_off]
        self.buffer = self.buffer[cut_off:]

        return self._process_text(to_process)

    def flush(self) -> str:
        """Process any remaining text in the buffer."""
        res = self.buffer
        self.buffer = ""
        return self._process_text(res)

    def _process_text(self, text: str) -> str:
        output: List[str] = []
        i = 0
        n = len(text)

        while i < n:
            # 1. Check for backticks (to toggle code block state)
            if text[i] == '`':
                count = 0
                while i + count < n and text[i + count] == '`':
                    count += 1

                backticks = text[i:i+count]
                i += count

                if count >= 3:
                    self.in_code_block = not self.in_code_block
                    self.in_inline_code = False
                elif count == 1:
                    if not self.in_code_block:
                        self.in_inline_code = not self.in_inline_code

                output.append(backticks)
                continue

            # If inside code blocks, copy characters as-is
            if self.in_code_block or self.in_inline_code:
                output.append(text[i])
                i += 1
                continue

            # 2. Check for LaTeX commands starting with \
            if text[i] == '\\':
                # Handle \sqrt{...}
                if text.startswith("\\sqrt{", i):
                    depth = 1
                    j = i + 6
                    found = False
                    while j < n:
                        if text[j] == '{':
                            depth += 1
                        elif text[j] == '}':
                            depth -= 1
                            if depth == 0:
                                found = True
                                break
                        j += 1
                    if found:
                        inside = text[i+6:j]
                        # Recursively normalize the contents of the square root
                        normalized_inside = self._process_text(inside)
                        output.append(f"√{normalized_inside}")
                        i = j + 1
                        continue
                    else:
                        # Incomplete matching brace, buffer and wait
                        break

                # Handle \frac{...}{...}
                if text.startswith("\\frac{", i):
                    depth = 1
                    j = i + 6
                    found1 = False
                    while j < n:
                        if text[j] == '{':
                            depth += 1
                        elif text[j] == '}':
                            depth -= 1
                            if depth == 0:
                                found1 = True
                                break
                        j += 1
                    if found1:
                        if j + 1 < n and text[j+1] == '{':
                            depth = 1
                            k = j + 2
                            found2 = False
                            while k < n:
                                if text[k] == '{':
                                    depth += 1
                                elif text[k] == '}':
                                    depth -= 1
                                    if depth == 0:
                                        found2 = True
                                        break
                                k += 1
                            if found2:
                                arg1 = text[i+6:j]
                                arg2 = text[j+2:k]
                                normalized_arg1 = self._process_text(arg1)
                                normalized_arg2 = self._process_text(arg2)
                                output.append(f"({normalized_arg1})/({normalized_arg2})")
                                i = k + 1
                                continue
                            else:
                                break
                        else:
                            if j + 1 >= n:
                                break
                            else:
                                pass

                # Map common LaTeX commands to clean Unicode symbols
                matched = False
                latex_symbols = [
                    # Greek letters (lowercase)
                    ("\\alpha", "α"), ("\\beta", "β"), ("\\gamma", "γ"), ("\\delta", "δ"),
                    ("\\epsilon", "ε"), ("\\zeta", "ζ"), ("\\eta", "η"), ("\\theta", "θ"),
                    ("\\iota", "ι"), ("\\kappa", "κ"), ("\\lambda", "λ"), ("\\mu", "μ"),
                    ("\\nu", "ν"), ("\\xi", "ξ"), ("\\pi", "π"), ("\\rho", "ρ"),
                    ("\\sigma", "σ"), ("\\tau", "τ"), ("\\upsilon", "υ"), ("\\phi", "φ"),
                    ("\\chi", "χ"), ("\\psi", "ψ"), ("\\omega", "ω"),
                    # Greek letters (uppercase)
                    ("\\Delta", "Δ"), ("\\Omega", "Ω"), ("\\Sigma", "Σ"), ("\\Pi", "Π"),
                    ("\\Gamma", "Γ"), ("\\Phi", "Φ"), ("\\Psi", "Ψ"), ("\\Xi", "Ξ"),
                    ("\\Theta", "Θ"), ("\\Lambda", "Λ"),
                    # Operators & Relations
                    ("\\times", "×"), ("\\div", "÷"), ("\\pm", "±"),
                    ("\\leq", "≤"), ("\\geq", "≥"), ("\\leq", "≤"), ("\\geq", "≥"),
                    ("\\le", "≤"), ("\\ge", "≥"), ("\\neq", "≠"), ("\\neq", "≠"),
                    ("\\ne", "≠"), ("\\approx", "≈"), ("\\in", "∈"), ("\\notin", "∉"),
                    ("\\infty", "∞"), ("\\cdot", "·"),
                    # Arrows
                    ("\\rightarrow", "→"), ("\\leftarrow", "←"), ("\\to", "→"),
                    ("\\Rightarrow", "⇒"), ("\\implies", "⇒"), ("\\leftrightarrow", "↔"),
                    ("\\Leftrightarrow", "⇔"), ("\\Leftarrow", "⇐")
                ]
                for lat, uni in latex_symbols:
                    if text.startswith(lat, i):
                        output.append(uni)
                        i += len(lat)
                        matched = True
                        break
                if matched:
                    continue

            # 3. Check for text arrows: `->` and `=>`
            if text.startswith("->", i):
                output.append("→")
                i += 2
                continue
            if text.startswith("=>", i):
                output.append("⇒")
                i += 2
                continue

            # 4. Check for html entities
            if text[i] == '&':
                matched = False
                for ent, val in [
                    ("&gt;", ">"),
                    ("&lt;", "<"),
                    ("&amp;", "&"),
                    ("&quot;", '"'),
                    ("&#39;", "'"),
                    ("&rarr;", "→"),
                    ("&larr;", "←"),
                ]:
                    if text.startswith(ent, i):
                        output.append(val)
                        i += len(ent)
                        matched = True
                        break
                if matched:
                    continue

            # 5. Check for LaTeX math delimiters ($ and $$) and strip them
            if text[i] == '$':
                if text.startswith("$$", i):
                    i += 2
                    continue
                # Keep $ if it looks like a currency symbol (followed by digit)
                j = i + 1
                while j < n and text[j] == ' ':
                    j += 1
                if j < n and text[j].isdigit():
                    output.append('$')
                    i += 1
                    continue
                else:
                    i += 1
                    continue

            # Default: copy character
            output.append(text[i])
            i += 1

        return "".join(output)


def normalize_text(text: str) -> str:
    """Helper to normalize a static block of text using StreamingTextNormalizer."""
    if not text:
        return text
    normalizer = StreamingTextNormalizer()
    res = normalizer.feed(text)
    res += normalizer.flush()
    return res
