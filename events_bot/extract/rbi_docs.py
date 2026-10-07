"""Structure of the RBI MPC-day documents, verbatim, for the detailed review e-mail.

Nothing here rewrites text: each function only splits the official document into the sections
RBI itself uses, so the e-mail can present them in order with every number still the source's.

- resolution_sections(): the MPC resolution page, split at its headings ("Monetary Policy
  Decisions", "Global Outlook", "Domestic Outlook", "Rationale for Monetary Policy Decisions").
- sdrp_measures(): the Statement on Developmental and Regulatory Policies -> [(area, title, text)].
- governor_sections(): the Governor's Statement PDF (it is not in the HTML page) split at its bold
  headings. Superscript footnote markers and the footnotes themselves are dropped by font size, so
  "July-August 2026" never turns into "July-August 2026.9".
"""
from __future__ import annotations

import re

RES_HEADINGS = ("Monetary Policy Decisions", "Growth and Inflation Outlook", "Global Outlook", "Domestic Outlook",
                "Rationale for Monetary Policy Decisions")
_NUM = re.compile(r"^\d+\.\s+")


def _strip_num(p: str) -> str:
    return _NUM.sub("", p).strip()


def resolution_sections(text: str) -> list[tuple[str, list[str]]]:
    """[(heading, [paragraph, ...])] in document order; paragraph numbers removed."""
    out: list[tuple[str, list[str]]] = []
    cur: tuple[str, list[str]] | None = None
    for line in text.split("\n"):
        s = line.strip()
        if s in RES_HEADINGS:
            cur = (s, [])
            out.append(cur)
        elif cur is not None and _NUM.match(s):
            cur[1].append(_strip_num(s))
        elif cur is not None and re.match(r"\(.+\) (Chief )?General Manager", s):
            break
    return [(h, ps) for h, ps in out if ps]


def resolution_closing(text: str) -> list[str]:
    """The 'minutes will be published on ...' / 'next meeting ... scheduled for ...' lines, verbatim."""
    return [_strip_num(l) for l in text.split("\n")
            if re.search(r"minutes of the MPC.s meeting will be published|next meeting of the MPC is scheduled", l)]


def split_growth_inflation(paras: list[str]) -> tuple[list[str], list[str]]:
    """Domestic-outlook paragraphs: those about prices go to inflation, the rest to growth."""
    infl = [p for p in paras if re.search(r"\binflation\b|\bCPI\b|\bprices\b", p, re.I)
            and not re.search(r"real GDP growth for", p)]
    return [p for p in paras if p not in infl], infl


def sdrp_measures(text: str) -> list[tuple[str, str, str]]:
    """[(area, measure title, description)] from the SDRP page text."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    out: list[tuple[str, str, str]] = []
    area = ""
    i = 0
    while i < len(lines):
        l = lines[i]
        if re.match(r"\(.+\) (Chief )?General Manager", l):
            break
        m = re.match(r"^(\d+)\.\s+(.+)$", l)
        if m and len(l) < 180 and not l.endswith("."):
            body = []
            j = i + 1
            while j < len(lines) and not re.match(r"^\d+\.\s+.{0,170}[^.]$", lines[j]) \
                    and not re.match(r"\(.+\) (Chief )?General Manager", lines[j]) and len(lines[j]) > 60:
                body.append(lines[j])
                j += 1
            out.append((area, m.group(2), " ".join(body)))
            i = j
            continue
        if len(l) < 60 and not l.endswith(".") and not l.startswith(("Date", "(", "Press Release")) \
                and "Statement on Developmental" not in l:
            area = l
        i += 1
    return out


def governor_sections(pdf: bytes) -> list[tuple[str, list[str]]]:
    """[(heading, [paragraph, ...])] from the Governor's Statement PDF (body text only)."""
    import pymupdf

    doc = pymupdf.open(stream=pdf, filetype="pdf")
    sections: list[tuple[str, list[str]]] = []
    cur_head, cur_paras, buf = "", [], []

    def flush_para() -> None:
        if buf:
            t = re.sub(r"\s+", " ", " ".join(buf)).strip()
            # sign-off lines: "Thank you. Namaskar.", "Press Release: 2026-2027/1266", "(Brij Raj) Chief General Manager"
            t = re.split(r"\s*(?:Press Release:|\(\w[\w .]+\)\s*(?:Chief )?General Manager)", t)[0].strip()
            if t and not (t.startswith("Thank you") and len(t) < 60):
                cur_paras.append(t)
            buf.clear()

    def flush_section() -> None:
        flush_para()
        if cur_head and cur_paras:
            sections.append((cur_head, list(cur_paras)))
        cur_paras.clear()

    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["size"] >= 10.5]      # drops superscripts + footnotes
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if not text or not re.search(r"[A-Za-z0-9]", text):
                    continue
                if all(s["flags"] & 16 for s in spans if s["text"].strip()):  # bold line = heading
                    if re.match(r"^[A-Z][A-Za-z ,&’'-]{2,60}$", text) and "Statement" not in text:
                        flush_section()
                        cur_head = text
                    continue
                if re.fullmatch(r"\d+\.", text):                               # paragraph number span
                    flush_para()
                    continue
                if re.fullmatch(r"\d+", text):                                 # page number
                    continue
                if cur_head:
                    buf.append(text)
    flush_section()
    return sections
