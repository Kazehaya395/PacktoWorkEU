"""Loss-aware, company-neutral presentation of saved/fetched job descriptions."""
from html import unescape
from html.parser import HTMLParser
import re

_MARKUP = re.compile(r'</?(?:p|div|br|ul|ol|li|h[1-6]|section|article|strong|em|b|i|a|span|table|tr|td|pre|code|script|style|nav|footer|head)\b[^>]*>', re.I)


class _ReadableHTML(HTMLParser):
    skip = {'script', 'style', 'noscript', 'svg', 'template', 'head', 'nav', 'footer', 'iframe', 'form', 'button'}
    blocks = {'p', 'div', 'br', 'ul', 'ol', 'li', 'section', 'article', 'table', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.skip:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in self.blocks:
            self.parts.append('\n')
        if tag == 'li':
            self.parts.append('• ')

    def handle_startendtag(self, tag, attrs):
        if tag not in self.skip:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in self.blocks:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def normalize_jd_text(value):
    """Strip real HTML, not ordinary comparisons/C++ templates; never truncate.

    Decode up to two envelope escapes only when they reveal recognized markup.
    Already-readable text (including literal entities) is otherwise unchanged.
    HTMLParser decodes text-node entities without reparsing them as markup.
    """
    original = str(value or '')
    markup = original
    for _ in range(3):
        if _MARKUP.search(markup):
            parser = _ReadableHTML()
            parser.feed(markup)
            parser.close()
            lines = [re.sub(r'[\t \xa0]+', ' ', line).strip()
                     for line in ''.join(parser.parts).splitlines()]
            return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        decoded = unescape(markup)
        if decoded == markup:
            break
        markup = decoded
    return original.strip()


_BENEFIT_HEAD = re.compile(r'^(?:benefits?|perks?|compensation(?: and benefits)?|what we offer|what you get|our offer|cosa offriamo|i nostri benefit|vantaggi|benefici|retribuzione e benefit)\s*:?\s*$', re.I)
_ROLE_HEAD = re.compile(r'^(?:responsibilities|requirements|qualifications|your role|what you(?: will|\x27ll) do|what you bring|skills|about (?:you|the role)|how to apply|requisiti|responsabilità|il tuo ruolo|cosa farai|competenze)\s*:?\s*$', re.I)
_BENEFIT_SENTENCE = re.compile(r'\b(?:benefits?|perks?|stock options|equity awards|pension|health insurance|we offer|we provide you|you (?:will )?receive|(?:available|provided|offered) (?:to|for) (?:all |our |every )?(?:employees?|staff|team members?)|offriamo|benefici|vantaggi|disponibil\w* per (?:tutti |tutte |i |le |nostri |nostre )*(?:dipendenti|collaboratori|collaboratrici))\b', re.I)
_ROLE_SENTENCE = re.compile(r'\b(?:patients?|pazienti|clinico|clinica|terapia|clients?|clinical|therap(?:y|ist)|trading|pricing|valuation|hedging|requirements?|qualifications?|experience (?:in|with)|proficien\w*)\b', re.I)


def competency_text(value):
    """Keep role evidence, excluding explicit benefit sections/sentences.

    Low-level exact-regex APIs remain unchanged. This high-level boundary
    prevents benefits from being labelled skills while retaining real clinical
    or financial duties. No source description is modified in storage.
    """
    out = []
    benefits = False
    for line in normalize_jd_text(value).splitlines():
        heading = line.strip().lstrip('•*#- ').strip()
        if _BENEFIT_HEAD.fullmatch(heading):
            benefits = True
            continue
        if _ROLE_HEAD.fullmatch(heading):
            benefits = False
        if benefits:
            continue
        for sentence in re.split(r'(?<=[.!?;])\s+', line):
            if _BENEFIT_SENTENCE.search(sentence) and not _ROLE_SENTENCE.search(sentence):
                continue
            out.append(sentence)
    return '\n'.join(out)
