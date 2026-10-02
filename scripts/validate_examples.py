#!/usr/bin/env python3
"""Validate every JSON-LD example in dfc-ldp.yaml against components/schemas.

Spectral checks the document's structure but never looks at an example, so an
example can contradict its own schema and nothing notices. This does that check.

Request bodies are validated against the request schema (<Class>); responses
against <Class>Response, falling back to <Class> where no required list was
generated. Only the application/* examples are checked -- a text/turtle example
is a string and has no schema to validate against.

Needs PyYAML only. Exit 1 if any example fails.
"""

import collections
import json
import pathlib
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
SPEC = REPO / "dfc-ldp.yaml"
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def collect(doc):
    """Yield (origin, example, use_request_schema) for every JSON-LD example."""
    for path, item in (doc.get("paths") or {}).items():
        for method, op in item.items():
            if method not in HTTP_METHODS:
                continue
            body = op.get("requestBody")
            if body:
                for ct, media in (body.get("content") or {}).items():
                    if ct.split(";")[0].strip() == "application/ld+json":
                        ex = (media.get("schema") or {}).get("example")
                        if ex is not None:
                            yield f"{method.upper()} {path}", ex, True
            for code, resp in (op.get("responses") or {}).items():
                for ct, media in (resp.get("content") or {}).items():
                    if ct.split(";")[0].strip() == "application/ld+json":
                        ex = (media.get("schema") or {}).get("example")
                        if ex is not None:
                            yield f"{method.upper()} {path} [{code}]", ex, False


def entries(example):
    if isinstance(example, dict) and "@graph" in example:
        return [e for e in example["@graph"] if isinstance(e, dict)]
    return [example] if isinstance(example, dict) else []


def resolve(schema, name, store, seen=()):
    """Follow $ref one level, as OpenAPI 3.0 tooling does."""
    ref = schema.get("$ref")
    if not ref:
        return schema, []
    key = ref.rsplit("/", 1)[-1]
    if key in seen or key not in store:
        return {}, {ref}
    return store[key], set()


def check(node, schema, store, path, errors, missing_refs):
    schema, refs = resolve(schema, None, store)
    missing_refs.update(refs)
    if not schema:
        return

    for comb in ("oneOf", "anyOf", "allOf"):
        if comb in schema:
            subs = schema[comb]
            if comb in ("oneOf", "anyOf"):
                # A oneOf branch that does not match must not contribute errors.
                matched = False
                for sub in subs:
                    trial = set()
                    check(node, sub, store, path, trial, missing_refs)
                    if not trial:
                        errors.update(trial)
                        matched = True
                        break
                if not matched:
                    errors.add(f"{path}: matches none of the {comb} branches")
                return
            for sub in subs:
                check(node, sub, store, path, errors, missing_refs)
            return

    kind = schema.get("type")
    if kind == "object" or ("properties" in schema and kind is None):
        if not isinstance(node, dict):
            return
        for req in schema.get("required") or []:
            if req not in node:
                errors.add(f"{path}: missing required {req!r}")
        for prop, sub in (schema.get("properties") or {}).items():
            if prop in node:
                check(node[prop], sub, store, f"{path}.{prop}", errors, missing_refs)
    elif kind == "array":
        if not isinstance(node, list):
            if node is not None:
                errors.add(f"{path}: expected array, got {type(node).__name__}")
            return
        for i, item in enumerate(node):
            check(item, schema.get("items") or {}, store, f"{path}[{i}]", errors, missing_refs)
    elif kind == "string":
        if node is not None and not isinstance(node, str):
            errors.add(f"{path}: expected string, got {type(node).__name__} {node!r}")
    elif kind == "number":
        if node is not None and (isinstance(node, bool) or not isinstance(node, (int, float))):
            errors.add(f"{path}: expected number, got {node!r}")
    elif kind == "integer":
        if node is not None and (isinstance(node, bool) or not isinstance(node, int)):
            errors.add(f"{path}: expected integer, got {node!r}")
    elif kind == "boolean":
        if node is not None and not isinstance(node, bool):
            errors.add(f"{path}: expected boolean, got {node!r}")


def main():
    doc = yaml.safe_load(SPEC.read_text())
    store = ((doc.get("components") or {}).get("schemas") or {})
    if not store:
        print("no components/schemas in dfc-ldp.yaml - nothing to check")
        return 0

    total = nodes = 0
    problems = collections.Counter()
    detail, dangling = [], set()

    for origin, example, is_request in collect(doc):
        total += 1
        for entry in entries(example):
            t = entry.get("@type")
            if not (isinstance(t, str) and t.startswith("dfc-b:")):
                continue
            cls = t[6:]
            name = cls if is_request else f"{cls}Response"
            if name not in store:
                name = cls
            if name not in store:
                problems[f"no schema for dfc-b:{cls}"] += 1
                continue
            nodes += 1
            errors = set()
            check(entry, {"$ref": f"#/components/schemas/{name}"}, store,
                  f"{origin}[{cls}]", errors, dangling)
            for e in errors:
                problems[e.split(":", 1)[1].strip()] += 1
            if errors:
                detail.append((origin, cls, errors))

    print(f"examples: {total}   graph entries checked: {nodes}   schemas: {len(store)}")
    if dangling:
        print(f"\nDANGLING $ref targets: {sorted(dangling)}")

    if not problems:
        print("all examples validate")
        return 0

    print(f"\n{sum(problems.values())} failures, {len(problems)} distinct:")
    for msg, n in problems.most_common():
        print(f"  {n:4}x  {msg}")

    print("\nfirst occurrence of each:")
    for origin, cls, errors in detail[:12]:
        print(f"  {origin}  dfc-b:{cls}")
        for e in sorted(errors)[:4]:
            print(f"      {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())