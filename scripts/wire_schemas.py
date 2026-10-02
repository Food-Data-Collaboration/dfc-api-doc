#!/usr/bin/env python3
"""Point every application/ld+json body in dfc-ldp.yaml at components/schemas.

Each media type currently reads:

    application/ld+json:
      schema:
        example:
          "@context": ...

There is no typing at all -- `schema` holds nothing but an example. This adds the
referenced schema alongside the example, leaving the example where it is:

    application/ld+json:
      schema:
        allOf:
        - $ref: '#/components/schemas/Organization'
        example:
          "@context": ...

`allOf` rather than a bare `$ref` because a `$ref` with siblings is ignored by
OAS 3.0 tooling, and the example has to stay put: moving it up to the media type
would mean re-indenting ~150 example blocks, of which half are YAML block
scalars holding Turtle. The examples are hand-maintained and the file carries
several traps (unquoted CURIEs, commas swallowed into URIs, flow vs block
mapping choices) that a rewrite would flatten.

Only application/ld+json is wired. A text/turtle body is not JSON, so a JSON
Schema does not describe it and pointing one at it would be misleading.

Idempotent: re-running leaves an already-wired entry alone.
"""

import collections
import pathlib
import re
import sys

import yaml

SPEC = pathlib.Path(__file__).resolve().parent.parent / "dfc-ldp.yaml"
JSON_TYPES = ("application/ld+json", "application/json")
TYPE_RE = re.compile(r"^[ \t]*\"@type\"\s*:\s*dfc-b:(\w+)[ \t]*$", re.M)


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def key_of(line):
    """The mapping key a `foo:` line denotes, with any quoting removed.

    Path keys in this file are written '/organizations/{organizationId}': -- a
    double-quoted key inside a single-quoted one -- so stripping only trailing
    quotes leaves the leading one attached and the lookup silently misses.
    """
    text = line.strip()
    if text.startswith("#"):
        return None
    text = text[:-1].strip() if text.endswith(":") else None
    if text is None:
        return None
    while len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    return text


def block_end(lines, start):
    """First line at or after `start` that is not blank and less indented."""
    base = indent_of(lines[start])
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and indent_of(lines[i]) < base:
            return i
    return len(lines)


def main():
    lines = SPEC.read_text().split("\n")
    doc = yaml.safe_load("\n".join(lines))
    store = ((doc.get("components") or {}).get("schemas") or {})
    if not store:
        sys.exit("no components/schemas to wire; run scripts/gen_schemas.py first")

    # Class for each application/ld+json body, per operation, in the order the
    # bodies appear in the text: requestBody first, then each response in
    # document order. PyYAML preserves mapping order, so walking the parsed
    # document and the text in step lines the two up.
    plan = {}
    for path, item in (doc.get("paths") or {}).items():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            order, request_classes = [], []
            body = op.get("requestBody")
            if isinstance(body, dict):
                for ct, media in (body.get("content") or {}).items():
                    if ct.split(";")[0].strip() in JSON_TYPES:
                        found = classes_in((media.get("schema") or {}).get("example"))
                        order.append((found, True))
                        request_classes = request_classes or sorted(found)
            for code, resp in (op.get("responses") or {}).items():
                if not isinstance(resp, dict):
                    continue
                for ct, media in (resp.get("content") or {}).items():
                    if ct.split(";")[0].strip() not in JSON_TYPES:
                        continue
                    found = classes_in((media.get("schema") or {}).get("example"))
                    if not found and method in ("post", "put", "patch"):
                        # A success reply to a write is the class that was just
                        # written, even when the example only echoes the IRI.
                        found = set(request_classes)
                    order.append((found, False))
            if order:
                plan[(path, method)] = iter(order)

    # Walk the text and insert an allOf under each wireable media type's `schema:`.
    #
    # Indentation is the whole trick, and it is not uniform. This file writes
    # application/ld+json at indent 12 under `content:` for most operations but
    # at 10 under some, and a few bodies put `example:` deeper than the rest.
    # Measured on the original file:
    #
    #     65x  media 12, schema 14, example 16
    #     31x  media 10, schema 12, example 14
    #      8x  media 12, schema 14, example 18
    #
    # So nothing is assumed: `allOf:` is inserted at whatever indent the existing
    # `example:` already uses, making the two siblings under the untouched
    # `schema:`, with the list item two deeper. That way not one line of any
    # example moves, and no pattern can orphan the example inside the list item
    # (which Spectral reports as no-$ref-siblings).
    out, i, wired, skipped = [], 0, collections.Counter(), collections.Counter()
    path_key = method_key = None
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        depth = indent_of(line)

        if depth == 2 and stripped.endswith(":"):
            path_key, method_key = key_of(line), None
        elif depth == 4 and stripped.endswith(":"):
            method_key = key_of(line)

        if stripped.endswith(":"):
            name = (key_of(line) or "").split(";")[0].strip()
            if name in JSON_TYPES and i + 2 < len(lines) \
                    and lines[i + 1].strip() == "schema:" \
                    and lines[i + 2].strip() == "example:":
                stream = plan.get((path_key, method_key))
                found, is_request = next(stream) if stream is not None else (set(), True)
                found = sorted(found)
                # A response may legitimately be partial -- a container lists
                # items without repeating each one's obligatory links, and a
                # Prefer: return=minimal reply carries no body -- so responses
                # point at the Response variant, which drops the required list.
                if not is_request:
                    found = [f"{c}Response" if f"{c}Response" in store else c for c in found]
                usable = [c for c in found if c in store]
                if not usable:
                    if found:
                        for c in found:
                            skipped[f"no schema for dfc-b:{c}"] += 1
                    else:
                        skipped["no dfc-b class (container, WebID, or platform payload)"] += 1
                    out.append(line)
                    i += 1
                    continue
                at = indent_of(lines[i + 2])
                out.append(line)
                out.append(lines[i + 1])
                if len(usable) == 1:
                    out.append(" " * at + "allOf:")
                    out.append(" " * (at + 2) + f"- $ref: '#/components/schemas/{usable[0]}'")
                else:
                    # A container body can hold several typed nodes in one
                    # @graph; the schema has to admit each of them.
                    out.append(" " * at + "allOf:")
                    out.append(" " * (at + 2) + "- oneOf:")
                    for cls in usable:
                        out.append(" " * (at + 4) + f"- $ref: '#/components/schemas/{cls}'")
                for cls in usable:
                    wired[cls] += 1
                i += 2
                continue
        out.append(line)
        i += 1

    SPEC.write_text("\n".join(out))
    print(f"wired {sum(wired.values())} application/ld+json bodies across "
          f"{len(wired)} classes")
    for cls, n in sorted(wired.items()):
        print(f"  {n:3}x {cls}")
    if skipped:
        print("\nleft alone:")
        for why, n in skipped.most_common():
            print(f"  {n:3}x {why}")


def classes_in(example):
    """Every dfc-b class appearing as a node in an example's @graph."""
    if not isinstance(example, dict):
        return set()
    nodes = example.get("@graph")
    if not isinstance(nodes, list):
        nodes = [example]
    found = set()
    for node in nodes:
        if isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str) and t.startswith("dfc-b:"):
                found.add(t[6:])
    return found


if __name__ == "__main__":
    main()