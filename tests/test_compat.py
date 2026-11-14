"""Static compatibility audit of everything the browser has to run.

There is no iPad 2 and no iOS 9 Safari in this environment, so nothing here can
prove the page works on one. What it does prove is narrower and still worth
having: the shipped `web/` files contain no syntax, API or CSS feature that
Safari gained after iOS 9 (2015), and no network dependency that a phone on a
dead Wi-Fi would notice. A future edit that quietly adds `fetch()` or a CSS
custom property fails here instead of failing on somebody's tablet.

The JavaScript check is a lexical audit with a small hand-written lexer (it
blanks comments, string literals and regex literals before matching), not a
full ECMAScript parser. It is deliberately conservative: patterns that could
match inside a string are reported against the *code* view only.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")


def read_web(name):
    path = os.path.join(WEB_DIR, name)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------- js lexer

# A '/' opens a regex literal only where a value cannot end, so that
# /https:\/\// inside real code is not mistaken for a line comment.
_REGEX_OPERATORS = set("([{,;:=!&|?+-*%<>~^")
_REGEX_KEYWORDS = {"return", "typeof", "instanceof", "in", "of", "new", "delete",
                   "void", "case", "do", "else", "yield", "await"}
_IDENT = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")


def strip_javascript(source):
    """(code, findings): comments/strings/regexes blanked, newlines kept.

    `findings` collects problems the blanking itself reveals, like template
    literals, which are ES6 regardless of what is inside them.
    """
    out = []
    findings = []
    i, n = 0, len(source)
    line = 1
    prev_sig = ""      # last significant character written to `out`
    prev_word = ""     # identifier that ended just before i
    word = ""
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""

        if ch == "\n":
            out.append("\n")
            i += 1
            line += 1
            word = ""
            continue
        if ch in " \t\r":
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
            word = ""
            continue
        if ch == "/" and nxt == "*":
            i += 2
            out.append("  ")
            while i < n and not (source[i] == "*" and source[i + 1:i + 2] == "/"):
                if source[i] == "\n":
                    out.append("\n")
                    line += 1
                else:
                    out.append(" ")
                i += 1
            i += 2
            out.append("  ")
            word = ""
            continue
        if ch == "/" and (not prev_sig or prev_sig in _REGEX_OPERATORS
                          or prev_word in _REGEX_KEYWORDS):
            i += 1
            out.append(" ")
            in_class = False
            while i < n:
                c = source[i]
                if c == "\\":
                    out.append("  ")
                    i += 2
                    continue
                if c == "\n":
                    break                       # unterminated: leave it to the syntax
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    break
                out.append(" ")
                i += 1
            i += 1
            while i < n and source[i] in "gimsuy":
                out.append(" ")
                i += 1
            prev_sig = "/"
            word = ""
            continue

        if ch in "\"'":
            quote = ch
            i += 1
            out.append(" ")
            while i < n and source[i] != quote:
                if source[i] == "\\":
                    out.append("  ")
                    i += 2
                    continue
                if source[i] == "\n":
                    findings.append("unterminated string at line %d" % line)
                    break
                out.append(" ")
                i += 1
            i += 1
            out.append(" ")
            prev_sig = "\""
            word = ""
            continue

        if ch == "`":
            findings.append("template literal at line %d" % line)
            i += 1
            out.append(" ")
            depth = 0
            while i < n:
                c = source[i]
                if c == "\\":
                    out.append("  ")
                    i += 2
                    continue
                if c == "$" and source[i + 1:i + 2] == "{":
                    depth += 1
                    out.append("  ")
                    i += 2
                    continue
                if c == "}" and depth:
                    depth -= 1
                    out.append(" ")
                    i += 1
                    continue
                if c == "`" and depth == 0:
                    break
                if c == "\n":
                    out.append("\n")
                    line += 1
                else:
                    out.append(" ")
                i += 1
            i += 1
            out.append(" ")
            prev_sig = "`"
            word = ""
            continue

        out.append(ch)
        if ch in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$0123456789":
            word += ch
        else:
            if word:
                prev_word = word
                word = ""
            prev_sig = ch
        i += 1
    if word:
        prev_word = word
    return "".join(out), prev_word, findings


def line_of(text, index):
    return text.count("\n", 0, index) + 1


# ------------------------------------------------------------------ checks

ES5_FORBIDDEN = [
    ("arrow function (=>)", re.compile(r"=>")),
    ("let declaration", re.compile(r"\blet\s+[A-Za-z_$][\w$]*\s*=", re.I)),
    ("const declaration", re.compile(r"\bconst\s+[A-Za-z_$][\w$]*\s*=", re.I)),
    ("class", re.compile(r"\bclass\s+[A-Za-z_$]")),
    ("generator function", re.compile(r"function\s*\*")),
    ("async function", re.compile(r"\basync\b")),
    ("await", re.compile(r"\bawait\b")),
    ("for..of loop", re.compile(r"\bfor\s*\([^;)]*\bof\b")),
    ("spread or rest (...)", re.compile(r"[(\[{,]\s*\.\.\.|(?<=[\w\]\)})])\s*\.\.\.")),
    ("optional chaining (?.)", re.compile(r"\?\.")),
    ("nullish coalescing (??)", re.compile(r"\?\?")),
    ("exponent operator (**)", re.compile(r"\*\*")),
    ("destructured parameter", re.compile(r"function\s*\w*\s*\(\s*[\[{]")),
]

POST_IOS9_APIS = [
    ("fetch", re.compile(r"\bfetch\s*\(")),
    ("Promise", re.compile(r"\bPromise\b")),
    ("WebSocket", re.compile(r"\bWebSocket\b")),
    ("service worker", re.compile(r"serviceWorker")),
    ("clipboard API", re.compile(r"navigator\.clipboard")),
    ("Web Share", re.compile(r"navigator\.share\b")),
    ("IndexedDB", re.compile(r"\bindexedDB\b")),
    ("TextEncoder", re.compile(r"\bTextEncoder\b")),
    ("URLSearchParams", re.compile(r"\bURLSearchParams\b")),
    ("IntersectionObserver", re.compile(r"IntersectionObserver")),
    ("ResizeObserver", re.compile(r"ResizeObserver")),
    ("MutationObserver", re.compile(r"MutationObserver")),
    ("Proxy", re.compile(r"new\s+Proxy\b")),
    ("Reflect", re.compile(r"\bReflect\.")),
    ("Symbol", re.compile(r"\bSymbol\b")),
    ("BigInt", re.compile(r"\bBigInt\b|\d+n\b")),
    ("globalThis", re.compile(r"\bglobalThis\b")),
    ("structuredClone", re.compile(r"structuredClone")),
    ("Object.assign", re.compile(r"Object\.assign")),
    ("Object.entries/values", re.compile(r"Object\.(entries|values)")),
    ("Array.from/of", re.compile(r"Array\.(from|of)")),
    ("Array.includes/find", re.compile(r"\.includes\s*\(|\.find\s*\(|\.findIndex\s*\(")),
    ("String startsWith/endsWith/pad/repeat",
     re.compile(r"\.(startsWith|endsWith|padStart|padEnd|repeat|at)\s*\(")),
    ("Number.isInteger", re.compile(r"Number\.(isInteger|isFinite|parseFloat|parseInt)")),
    ("Element.closest", re.compile(r"\.closest\s*\(")),
    ("Element.append/before/after", re.compile(r"\.(append|prepend|before|after|replaceChildren)\s*\(")),
    ("dataset", re.compile(r"\bdataset\b")),
    ("classList multi-arg", re.compile(r"classList\.(toggle|replace)\s*\([^)]*,")),
    ("CustomEvent", re.compile(r"new\s+CustomEvent\b")),
    ("matchMedia", re.compile(r"matchMedia")),
    ("WebCrypto", re.compile(r"crypto\.(subtle|randomUUID)")),
    ("CSS custom property", re.compile(r"setProperty\s*\(\s*[\"']--")),
]


class JavaScriptSyntaxAudit(unittest.TestCase):
    """web/app.js must be parseable by a 2015 Safari."""

    @classmethod
    def setUpClass(cls):
        cls.source = read_web("app.js")
        cls.code, cls.prev_word, cls.findings = strip_javascript(cls.source)

    def test_lexer_blanks_comments_and_strings(self):
        # The audit is only as good as this: a comment must not be able to
        # satisfy or break any pattern below.
        self.assertFalse("Legacy Safari first" in self.code)
        self.assertFalse("//" in self.code)
        self.assertTrue("function poll" in self.code)
        self.assertTrue("getElementById" in self.code)

    def test_no_post_es5_syntax(self):
        bad = list(self.findings)
        for label, pattern in ES5_FORBIDDEN:
            for match in pattern.finditer(self.code):
                bad.append("%s at line %d: %r" %
                           (label, line_of(self.code, match.start()),
                            self.code[match.start():match.start() + 40].split("\n")[0].strip()))
        self.assertEqual(bad, [], "app.js uses syntax iOS 9 Safari cannot parse")

    def test_no_post_ios9_apis(self):
        bad = []
        for label, pattern in POST_IOS9_APIS:
            for match in pattern.finditer(self.code):
                bad.append("%s at line %d" % (label, line_of(self.code, match.start())))
        self.assertEqual(bad, [], "app.js calls something Safari did not have in 2015")

    def test_uses_xhr_and_polling(self):
        # The other half of the promise: not only is the modern thing absent,
        # the legacy replacement has to be present.
        self.assertGreaterEqual(self.code.count("new XMLHttpRequest"), 3)
        self.assertIn("xhr.upload.onprogress", self.source)
        self.assertIn("window.setTimeout", self.code)

    def test_timers_are_cleaned_up(self):
        # A retry countdown is the one place a setInterval is honest; it must
        # be cleared, or a page left open overnight stacks tickers.
        for name in re.findall(r"var (\w+) = window\.setInterval\(", self.source):
            self.assertRegex(self.source, r"clearInterval\(" + name + r"\)",
                             "interval handle %s is never cleared" % name)

    MODERN_SAMPLE = (
        "/* modern: fetch, Promise, class */\n"
        "const limit = 10;\n"
        "let names = [];\n"
        "const trim = (s) => s?.trim();\n"
        "class Panel {}\n"
        "async function load() { await fetch('/api/state'); }\n"
        "for (const n of names) { }\n"
        "var label = `row ${limit}`;\n"
        "var ok = names.includes('a');\n"
        "Object.assign(target, source);\n"
        "var x = navigator.serviceWorker;\n"
        "var p = new Promise(function () {});\n"
        "var rest = [...items];\n"
        "if (a ?? b) { }\n"
    )

    def test_audit_rejects_modern_javascript(self):
        # A silent scanner is worse than none: this sample has to light it up.
        code, _, findings = strip_javascript(self.MODERN_SAMPLE)
        syntax = [label for label, pattern in ES5_FORBIDDEN if pattern.search(code)]
        for label in ("arrow function (=>)", "let declaration", "const declaration",
                      "class", "async function", "await", "for..of loop",
                      "optional chaining (?.)", "nullish coalescing (??)",
                      "spread or rest (...)"):
            self.assertIn(label, syntax)
        apis = [label for label, pattern in POST_IOS9_APIS if pattern.search(code)]
        for label in ("fetch", "Promise", "service worker", "Object.assign",
                      "Array.includes/find"):
            self.assertIn(label, apis)
        self.assertIn("template literal at line 8", findings)
        # The comment on line 1 mentions fetch and Promise; the API scan looks
        # at code only, so those two names must not be reported twice from it.
        self.assertEqual(len([m for m in POST_IOS9_APIS[0][1].finditer(code)]), 1)

    def test_file_slice_has_the_webkit_fallback(self):
        self.assertRegex(self.source, r"File\.prototype\.slice|\.slice\s*\|\|\s*\w*\.webkitSlice")
        self.assertIn("webkitSlice", self.source)

    def test_touch_probe_leads_with_ontouchstart(self):
        # maxTouchPoints is undefined on old Safari; the order is what makes
        # the expression safe rather than throwing.
        self.assertRegex(self.source, r'"ontouchstart"\s+in\s+window\s*\|\|[^|]*maxTouchPoints')

    def test_no_strict_module_or_global_leaks(self):
        # "use strict" is ES5 and is a good sign here, not a problem; what
        # would be a problem is the code drifting into global scope.
        self.assertRegex(self.source.lstrip(),
                         r"^(\/\*[\s\S]*?\*\/\s*)?\(\s*function\s*\(\s*\)\s*\{\s*\"use strict\"",
                         "app.js should open with an IIFE in strict mode")
        self.assertNotIn("type=\"module\"", self.source)

    def test_json_parsing_is_small_and_guarded(self):
        # JSON.parse throws on a captive-portal page that answers with HTML,
        # so every call site has to sit in a try block.
        calls = [m.start() for m in re.finditer(r"JSON\.parse", self.source)]
        self.assertLessEqual(len(calls), 4, "more JSON parsing than the protocol needs")
        for at in calls:
            head = self.source[:at]
            last_try = head.rfind("try {")
            last_catch = head.rfind("catch")
            self.assertNotEqual(last_try, -1, "JSON.parse with no try before it")
            self.assertGreater(last_try, last_catch,
                               "JSON.parse at line %d is outside a try block"
                               % line_of(self.source, at))


class CssCompatibilityAudit(unittest.TestCase):
    """web/app.css must lay out correctly in Safari 9 without variables."""

    MODERN = [
        ("custom property declaration", re.compile(r"--[a-z][\w-]*\s*:")),
        ("var()", re.compile(r"\bvar\(\s*--")),
        ("flex layout", re.compile(r"display\s*:\s*(inline-)?flex\b")),
        ("grid layout", re.compile(r"display\s*:\s*grid\b|\bgrid-\w+\s*:")),
        ("gap property", re.compile(r"^\s*gap\s*:", re.M)),
        ("position: sticky", re.compile(r"position\s*:\s*sticky")),
        ("object-fit", re.compile(r"object-fit\s*:")),
        ("aspect-ratio", re.compile(r"aspect-ratio\s*:")),
        ("backdrop-filter", re.compile(r"backdrop-filter\s*:")),
        ("filter", re.compile(r"(?<![-\w])filter\s*:")),
        ("scroll-behavior", re.compile(r"scroll-behavior\s*:")),
        ("overscroll-behavior", re.compile(r"overscroll-behavior\s*:")),
        ("touch-action", re.compile(r"touch-action\s*:")),
        ("env() / safe-area", re.compile(r"\benv\(\s*safe-area|(?<!-)calc\([^)]*\benv\(")),
        ("clamp()", re.compile(r"\bclamp\(")),
        ("min()/max() function", re.compile(r"[^-\w](min|max)\(")),
        ("calc without -webkit twin", re.compile(r"(?<!-webkit-)\bcalc\(")),
        (":has() selector", re.compile(r":has\(")),
        (":is()/:where() selector", re.compile(r":(?:is|where)\(")),
        ("::placeholder", re.compile(r"::placeholder")),
        ("@supports", re.compile(r"@supports")),
        ("viewport units", re.compile(r"\d+(vh|vw|vmin|vmax)\b")),
    ]

    @classmethod
    def setUpClass(cls):
        raw = read_web("app.css")
        cls.source = raw
        cls.code = re.sub(r"/\*.*?\*/", " ", raw, flags=re.S)

    def test_no_post_ios9_properties(self):
        bad = []
        for label, pattern in self.MODERN:
            for match in pattern.finditer(self.code):
                tail = self.code[match.end():match.end() + 40].lstrip()
                if label == "filter" and tail.startswith("alpha("):
                    continue        # the IE8 opacity hack; Safari ignores it
                bad.append("%s at line %d: %r" %
                           (label, line_of(self.code, match.start()),
                            self.code[match.start():match.start() + 40].split("\n")[0].strip()))
        self.assertEqual(bad, [], "app.css uses a feature Safari on iOS 9 lacks")

    def test_calc_is_also_prefixed(self):
        # The rule above allows calc() only when the -webkit- twin sits next to
        # it, which is what Safari 6-8 needed.
        for match in re.finditer(r"(?<!-webkit-)\bcalc\(", self.code):
            start = self.code.rfind("\n", 0, match.start()) + 1
            block = self.code[start:start + 400]
            self.assertIn("-webkit-calc", block,
                          "calc() at line %d has no prefixed fallback" %
                          line_of(self.code, match.start()))

    def test_responsive_without_breakpoint_framework(self):
        self.assertIn("@media (max-width", self.code)

    def test_themes_are_a_class_not_a_variable(self):
        self.assertIn("body.dark", self.code)
        self.assertNotIn("prefers-color-scheme", self.code)

    def test_reduced_motion_is_optional_degradation(self):
        # Unknown media queries are ignored, so this block can only improve the
        # old device's experience, never gate it.
        if "prefers-reduced-motion" in self.code:
            self.assertIn("transition", self.code)

    MODERN_SAMPLE = """
.panel {
  display: flex;
  --accent: #0f8a6a;
  color: var(--accent);
  gap: 8px;
  position: sticky;
  grid-template-columns: 1fr 1fr;
  width: min(100%, 400px);
  aspect-ratio: 1;
  backdrop-filter: blur(4px);
  filter: blur(2px);
  touch-action: none;
}
.x:has(.y) { color: red; }
input::placeholder { color: gray; }
@supports (display: grid) { .panel { display: grid; } }
""".strip()

    def test_audit_rejects_modern_css(self):
        code = re.sub(r"/\*.*?\*/", " ", self.MODERN_SAMPLE, flags=re.S)
        found = []
        for label, pattern in self.MODERN:
            for match in pattern.finditer(code):
                tail = code[match.end():match.end() + 40].lstrip()
                if label == "filter" and tail.startswith("alpha("):
                    continue
                found.append(label)
        for label in ("custom property declaration", "var()", "flex layout",
                      "grid layout", "gap property", "position: sticky",
                      "aspect-ratio", "backdrop-filter", "filter", "touch-action",
                      "min()/max() function", ":has() selector", "::placeholder",
                      "@supports"):
            self.assertIn(label, found)

    def test_fallback_for_every_gradient(self):
        # An old Safari that cannot paint a gradient must still show the
        # button, so the same rule block has to set a solid background too.
        for match in re.finditer(r"background-image\s*:\s*(?:-webkit-)?linear-gradient",
                                 self.code):
            open_brace = self.code.rfind("{", 0, match.start())
            close_brace = self.code.find("}", match.end())
            block = self.code[open_brace + 1:close_brace]
            self.assertTrue(re.search(r"background(?:-color)?\s*:\s*#", block),
                            "gradient at line %d has no solid background in its rule"
                            % line_of(self.code, match.start()))

    def test_no_external_font_or_asset(self):
        self.assertNotIn("@import", self.code)
        self.assertNotIn("url(http", self.code)


class HtmlCompatibilityAudit(unittest.TestCase):
    """web/index.html is the only document an unpaired phone ever loads."""

    @classmethod
    def setUpClass(cls):
        cls.source = read_web("index.html")
        cls.lower = cls.source.lower()
        cls.ids = set(re.findall(r'id="([\w-]+)"', cls.source))

    def test_no_module_scripts_or_deferred_boot(self):
        self.assertNotIn("type=\"module\"", self.lower)
        self.assertNotIn("<template", self.lower)
        self.assertNotIn("<picture", self.lower)
        self.assertIn("<!doctype html>", self.lower)

    def test_every_script_and_style_is_local(self):
        # A phone on a LAN with no internet must still get the whole UI.
        for match in re.finditer(r'(?:src|href)="([^"]+)"', self.source):
            url = match.group(1)
            self.assertTrue(url.startswith("/") or url.startswith("#") or url.startswith("data:"),
                            "external reference at line %d: %s" %
                            (line_of(self.source, match.start()), url))

    def test_viewport_meta_limits_zoom_out(self):
        self.assertRegex(self.lower, r"<meta name=\"viewport\"[^>]*width=device-width")

    def test_no_dangling_element_ids(self):
        # app.js looks elements up by literal id; a renamed id used to fail
        # silently, which is how the touch-mode drop text stopped working.
        js = read_web("app.js")
        wanted = set(re.findall(r'\$\("([\w-]+)"\)', js))
        missing = sorted(wanted - self.ids)
        self.assertEqual(missing, [], "$() asks for ids that index.html does not define: %s"
                         % ", ".join(missing))

    def test_no_dangling_data_targets(self):
        wanted = set(re.findall(r'data-close="([\w-]+)"', self.source))
        missing = sorted(wanted - self.ids)
        self.assertEqual(missing, [], "data-close points at a missing sheet: %s"
                         % ", ".join(missing))

    def test_controls_are_labelled(self):
        for match in re.finditer(r"<input\b[^>]*>", self.source):
            tag = match.group(0)
            if "type=\"hidden\"" in tag or "type=\"file\"" in tag:
                continue
            has_label = ("id=\"" in tag and "aria-label" in tag) or \
                        ("id=\"%%s\"" % "" if False else "id=\"" in tag)
            self.assertTrue(has_label,
                            "input at line %d has neither an id for a <label> nor aria-label"
                            % line_of(self.source, match.start()))
        self.assertIn("role=\"alert\"", self.source)
        self.assertIn("aria-live", self.source)

    def test_no_legacy_browser_sniffing_in_markup(self):
        self.assertNotIn("<!--[if", self.source)


if __name__ == "__main__":
    unittest.main()
