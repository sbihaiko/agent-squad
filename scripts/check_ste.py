"""Warn-only ASD-STE100 check of an issue body (#1972): prints findings, exits 0. Usage: check_ste.py [--title TEXT] [FILE]

Passive voice and noun stacks come from word lists, not a tagger: a finding is a prompt to read the sentence, not a verdict.
"""
import argparse
import re
import sys
from typing import NamedTuple

STRICT_HEADINGS = ("acceptance", "step", "observed", "expected")
INSTRUCTION_LIMIT = 20
DESCRIPTION_LIMIT = 25
TITLE_LIMIT = 12

WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_'’-]*")
PASSIVE = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being)\s+"
    r"(?:\w+ly\s+)?(?:\w{2,}ed|written|given|shown|sent|made|done|built|kept|taken|lost|found|read|known|held|left|"
    r"put|set|led|paid|told|bought|brought|caught|thought)\b",
    re.IGNORECASE,
)
IMPERATIVES = {
    "run", "set", "write", "add", "click", "open", "check", "read", "send", "create", "delete", "use", "make", "put",
    "remove", "update", "verify", "start", "stop", "call", "pass", "return", "print", "exit", "keep", "do", "don't",
    "never", "always", "show", "save", "load", "fetch", "retry", "move", "select", "enter", "press", "find", "copy",
    "build", "reset", "skip", "include", "exclude", "label", "assert", "commit", "post", "rerun", "restart", "choose",
    "list", "name", "mark", "split", "apply", "avoid", "ensure", "give", "take", "go",
}
FUNCTION_WORDS = {
    "a", "an", "the", "of", "to", "in", "on", "at", "by", "for", "with", "from", "into", "onto", "over", "under",
    "about", "as", "and", "or", "but", "nor", "so", "if", "then", "than", "that", "which", "who", "when", "while",
    "where", "how", "what", "this", "these", "those", "it", "its", "they", "them", "their", "there", "here", "each",
    "every", "all", "any", "some", "no", "not", "none", "both", "either", "only", "also", "just", "before", "after",
    "until", "unless", "because", "since", "although", "though", "whether", "via", "per", "within", "without",
    "between", "among", "through", "during", "against", "upon", "once", "more", "most", "less", "very", "such",
    "same", "other", "another", "do", "does", "did", "has", "have", "had", "can", "could", "may", "might", "must",
    "shall", "should", "will", "would", "is", "are", "was", "were", "be", "been", "being", "am", "i", "we", "you",
    "he", "she", "me", "us", "our", "your", "my", "his", "her", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "zero",
}
VERBS = {
    "changes", "writes", "reads", "moves", "sends", "sets", "uses", "returns", "exits", "prints", "starts", "stops",
    "fails", "passes", "creates", "makes", "gets", "needs", "names", "shows", "holds", "keeps", "takes", "goes",
    "means", "checks", "matches", "lands", "updates", "adds", "removes", "deletes", "runs", "reports", "waits",
    "stays", "becomes", "comes", "lists", "saves", "loads", "links", "marks", "gives", "finds", "shares", "states",
}


class Finding(NamedTuple):
    section: str
    kind: str
    detail: str
    sentence: str


def sections(body):
    name, lines, fence, out = "(preamble)", [], None, []
    for raw in body.splitlines():
        stripped = raw.strip()
        if fence:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            continue
        heading = re.match(r"#{1,6}\s+(.*)", stripped)
        if heading:
            out.append((name, lines))
            name, lines = heading.group(1).strip(), []
        else:
            lines.append(stripped)
    out.append((name, lines))
    return out


def units(lines):
    out = []
    for line in lines:
        line = re.sub(r"`[^`]*`", "", line)
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"^(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?", "", line).strip()
        if line:
            out.append(line)
    return out


def is_strict(heading):
    return any(key in heading.lower() for key in STRICT_HEADINGS)


def is_instruction(words):
    return words[0].lower() in IMPERATIVES


def noun_stacks(words):
    stacks, run = [], []
    for i, word in enumerate(words):
        low = word.lower()
        breaker = low in FUNCTION_WORDS or low in VERBS or low.isdigit() or (i == 0 and low in IMPERATIVES)
        if breaker:
            if len(run) > 3:
                stacks.append(" ".join(run))
            run = []
        else:
            run.append(word)
    if len(run) > 3:
        stacks.append(" ".join(run))
    return stacks


def check(body, title=None):
    findings = []
    if title is not None:
        n = len(WORD.findall(title))
        if n > TITLE_LIMIT:
            findings.append(Finding("Title", "title-length", f"{n} words, limit {TITLE_LIMIT}", title.strip()))
    for heading, lines in sections(body):
        strict = is_strict(heading)
        for unit in units(lines):
            for sentence in re.split(r"(?<=[.!?])\s+", unit):
                words = WORD.findall(sentence)
                if not words:
                    continue
                if strict:
                    instruction = is_instruction(words)
                    limit = INSTRUCTION_LIMIT if instruction else DESCRIPTION_LIMIT
                    if len(words) > limit:
                        kind = "instruction-length" if instruction else "description-length"
                        findings.append(Finding(heading, kind, f"{len(words)} words, limit {limit}", sentence))
                passive = PASSIVE.search(sentence)
                if passive:
                    findings.append(Finding(heading, "passive-voice", passive.group(0), sentence))
                for stack in noun_stacks(words):
                    findings.append(Finding(heading, "noun-stack", stack, sentence))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Warn-only ASD-STE100 check of an issue body.")
    parser.add_argument("file", nargs="?", help="issue body file; stdin when absent")
    parser.add_argument("--title", help="issue title, checked for length")
    args = parser.parse_args(argv)
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as fh:
                body = fh.read()
        except OSError as exc:
            print(f"check_ste: {exc}", file=sys.stderr)
            return 2
    else:
        body = sys.stdin.read()
    for f in check(body, args.title):
        print(f"{f.section} | {f.kind} | {f.detail} | {f.sentence}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
