#!/usr/bin/env python3
"""Generate components/schemas for dfc-ldp.yaml from the DFC v2.0.0 ontology.

One-shot generator. The output it writes is ordinary hand-editable YAML; nothing
in CI runs this, and the repo has no build step. Re-run it only if you want to
re-derive the schemas from scratch, and expect to reconcile the result by hand.

Why this exists rather than using DFC-LinkML
-------------------------------------------
DFC-LinkML's generated schema carries `multivalued` flags, but they are derived
from `owl:inverseOf`, not from cardinality, and they contradict the ontology in
at least three places where the ontology pins `owl:cardinality 1`:

    Organization.hasMainContact, PhysicalPlace.hasAddress, CatalogItem.listedIn

It also carries no `required:` flags at all. So multiplicity is derived here
straight from `DFC_BusinessOntology.rdf`, and ranges come from LinkML (whose
range data is sound) with the ontology's `allValuesFrom` preferred where the two
disagree.

Multiplicity rule, in precedence order
--------------------------------------
1. literal range (string/number/boolean/date/...): scalar. A JSON-LD literal is
   one value whatever the cardinality says.
2. `owl:cardinality 1` on the class or an ancestor: scalar, and `required` on
   request bodies.
3. an example in dfc-ldp.yaml already writes an array: array.
4. an example writes an inline node: scalar, `$ref`'d to that node's schema.
5. otherwise the ontology says *nothing*: oneOf [scalar, array] with a
   description saying so. Absence of an OWL restriction is unspecified, not
   "many", and asserting "many" would be inventing a constraint.

`dfc-b:totalTheoriticalStock` is pinned to cardinality 1 but appears in neither
spelling in the published context, so no example can use it. It is emitted as
optional for that reason.
"""

import collections
import copy
import json
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

import yaml

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SPEC = REPO / "dfc-ldp.yaml"
CACHE = pathlib.Path("/tmp/opencode/dfc-card")

ONTOLOGY = "https://w3id.org/dfc/ontology/v2.0.0/src/DFC_BusinessOntology.rdf"
LINKML = ("https://raw.githubusercontent.com/Food-Data-Collaboration/"
          "DFC-LinkML/main/src/dfc_business_linkml_v2_0.yaml")

RDFNS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
OWLNS = "http://www.w3.org/2002/07/owl#"
RDFSNS = "http://www.w3.org/2000/01/rdf-schema#"

PRIMITIVES = {
    "string": {"type": "string"},
    "uri": {"type": "string", "format": "uri"},
    "float": {"type": "number"},
    "integer": {"type": "integer"},
    "boolean": {"type": "boolean"},
    "date": {"type": "string", "format": "date"},
    "datetime": {"type": "string", "format": "date-time"},
    "Value_RECUR": {"type": "string",
                    "description": "An iCalendar RRULE, e.g. FREQ=WEEKLY;BYDAY=TU."},
}

# LinkML ranges that are object properties but are typed `string` there. The
# ontology's own allValuesFrom wins where the two disagree.
OBJECT_RANGES = {
    "lists": ["CatalogItem", "Offer"],
    "hasStep": ["Step"],
    "hasVariant": ["DefinedProduct"],
    "offeredThrough": ["Offer"],
    "hasPrice": ["Price"],
    "hasQuantity": ["QuantitativeValue"],
    "uses": ["Address", "PaymentMethod", "PhysicalPlace"],
    # LinkML types these `string`; the examples carry resource IRIs and, on
    # Certification, a SKOS concept IRI.
    "hasCertification": ["Certification"],
    "hasTemplateSaleSession": ["TemplateSaleSession"],
    "hasOffer": ["Offer"],
    "manages": ["Catalog"],
    "supplies": ["SuppliedProduct"],
    "referencedBy": ["CatalogItem"],
    "isCertifiedBy": ["Organization"],
}

# Range overrides where the ontology's own range is wrong or absent.
RANGE_OVERRIDES = {
    # DFC_BusinessOntology gives this no allValuesFrom and LinkML guesses float,
    # but every example writes a duration such as "7 days".
    "lifetime": "string",
}

# owl:cardinality 1, yet the value is the link back to the parent that embeds
# this node: hasPart/partOf and offeredThrough/offers. Writing it would make the
# child point back at a parent that already contains it, so it is documented as
# optional. See AGENTS.md.
PARENT_POINTERS = {"partOf", "offers"}

# Not declared anywhere in DFC_BusinessOntology. Kept as free-form strings so
# the examples that already use them stay valid; see AGENTS.md.
UNDECLARED = {
    "image": "Not declared in the DFC v2.0.0 business, technical or full-model "
             "ontologies. Reported as a property of dfc-b:DefinedProduct; left "
             "free-form pending confirmation.",
    "occursAt": "Not declared in the DFC_BusinessOntology. Spelled in the iCalendar "
                "namespace in the examples; the ontology working group has an open "
                "issue on it.",
    "hasCountryCode": "Not modelled in DFC-LinkML and not declared in the "
                      "DFC_BusinessOntology.",
}

# Deliberately not required despite owl:cardinality 1: the term is absent from
# the published context in both spellings, so no example can use it.
NEVER_REQUIRED = {"totalTheoriticalStock"}

JSON_TYPES = ("application/ld+json", "application/json")

IRI = {"type": "string", "format": "uri"}

# Absence of an OWL restriction means unspecified, not "many". Saying so beats
# silently asserting one of the two.
UNCONSTRAINED = ("IRI of the referenced resource. The ontology states no cardinality "
                 "for this property, so a single value and an array are both accepted.")


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "gen_schemas"})
        dest.write_bytes(urllib.request.urlopen(req, timeout=90).read())
    return dest


def load_ontology(path):
    root = ET.parse(path).getroot()
    local = lambda t: t.split("#")[-1].split("}")[-1]

    parents, cardinality = collections.defaultdict(set), {}
    ranges = collections.defaultdict(set)
    class_names = set()
    for cls in root.findall(f"{{{OWLNS}}}Class"):
        cid = local(cls.get(f"{{{RDFNS}}}about", ""))
        class_names.add(cid)
        for node in cls.findall(f"{{{RDFSNS}}}subClassOf"):
            res = node.get(f"{{{RDFNS}}}resource")
            if res and not res.startswith(OWLNS):
                parents[cid].add(local(res))
        for rest in cls.iter(f"{{{OWLNS}}}Restriction"):
            on = rest.find(f"{{{OWLNS}}}onProperty")
            if on is None:
                continue
            prop = local(on.get(f"{{{RDFNS}}}resource", ""))
            av = rest.find(f"{{{OWLNS}}}allValuesFrom")
            if av is not None:
                res = av.get(f"{{{RDFNS}}}resource")
                if res and (av.text or "").strip() == "":
                    ranges[prop].add(local(res))
            for tag in ("cardinality", "qualifiedCardinality"):
                el = rest.find(f"{{{OWLNS}}}{tag}")
                if el is not None and (el.text or "").strip().isdigit():
                    cardinality[f"{cid}|{prop}"] = int(el.text.strip())
    return parents, cardinality, ranges, class_names



def observe(spec):
    """What the examples in dfc-ldp.yaml actually write, per (class, property)."""
    seen = collections.defaultdict(collections.Counter)

    def shape(value):
        if isinstance(value, dict):
            t = value.get("@type")
            if isinstance(t, str) and t.startswith("dfc-b:"):
                return "embedded:" + t[6:]
            return "xsd-value" if t else "inline-object"
        if isinstance(value, list):
            return "array-of-" + "+".join(sorted({shape(v) for v in value})) if value else "empty-array"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        return "string"

    def walk(node, ctx=None):
        if isinstance(node, dict):
            t = node.get("@type")
            cls = t[6:] if isinstance(t, str) and t.startswith("dfc-b:") else ctx
            for key, value in node.items():
                if isinstance(key, str) and key.startswith("dfc-b:") and cls:
                    seen[(cls, key[6:])][shape(value)] += 1
            for key, value in node.items():
                if key not in ("@context", "@id", "@type"):
                    walk(value, cls)
        elif isinstance(node, list):
            for item in node:
                walk(item, ctx)

    for item in spec["paths"].values():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete", "head"):
                continue
            sources = [op.get("requestBody")] + list((op.get("responses") or {}).values())
            for source in sources:
                for ct, media in ((source or {}).get("content") or {}).items():
                    if ct.startswith("application/"):
                        walk((media.get("schema") or {}).get("example"))
    return seen


def classify(model, cls, prop, linkml_slot, shapes):
    """Return (schema_dict, is_required) for one (class, property) pair.

    The value shape is read off the examples, not off the declared range: a link
    property carries an IRI even when its range is a class, and only an inline
    node is a $ref. Reading $ref off the range alone types Organization.hasMainContact
    as a Person object when the examples hold a Person IRI.
    """
    required = model.is_required(cls, prop)
    rng = model.range_of(prop, linkml_slot)

    # 0. terms the ontology does not declare at all
    if prop in UNDECLARED:
        seen_array = any(k.startswith("array-of-string") for k in shapes)
        base = {"type": "string"}
        if seen_array:
            # dict() twice: reusing one object would make safe_dump emit a YAML
            # anchor, and anchors do not belong in this file.
            return {"oneOf": [dict(base), {"type": "array", "items": dict(base)}],
                    "description": UNDECLARED[prop]}, False
        return {**base, "description": UNDECLARED[prop]}, False

    # 1. literals are always a single value, whatever the cardinality says
    if rng in PRIMITIVES:
        return dict(PRIMITIVES[rng]), required

    # Split the observations into the shapes that actually matter here. These
    # flags are independent: an inline observation must not suppress "a bare
    # string was also seen", or a property written both ways collapses to the
    # array form and rejects the scalar.
    inline = {k[len("embedded:"):] for k in shapes if k.startswith("embedded:")}
    inline_arr = {k.split("embedded:", 1)[1] for k in shapes
                  if k.startswith("array-of-embedded:")}
    xsd = "xsd-value" in shapes
    xsd_arr = any(k.startswith("array-of-xsd-value") for k in shapes)
    iri = "string" in shapes
    iri_arr = any(k.startswith("array-of-string") for k in shapes)

    def ref(target):
        return {"$ref": f"#/components/schemas/{target}"}

    # 2. JSON-LD value objects, e.g. {"@type": "...#time", "@value": "08:00:00"}
    if xsd or xsd_arr:
        value = ref("XsdTimeValue")
        if xsd and xsd_arr:
            return {"oneOf": [value, {"type": "array", "items": value}]}, required
        if xsd_arr:
            return {"type": "array", "items": value}, required
        return value, required

    # 3. an inline node is the only case that becomes a $ref
    if inline or inline_arr:
        options = []
        if inline:
            options.append(ref(sorted(inline)[0]) if len(inline) == 1
                           else {"oneOf": [ref(t) for t in sorted(inline)]})
        if inline_arr:
            items = ({"oneOf": [{"$ref": f"#/components/schemas/{t}"} for t in sorted(inline_arr)]}
                     if len(inline_arr) > 1 else ref(sorted(inline_arr)[0]))
            options.append({"type": "array", "items": items})
        if iri:
            options.append(dict(IRI))
        return (options[0] if len(options) == 1 else {"oneOf": options}), required

    # 4. a link. When the ontology is silent about multiplicity, accept either a
    #    bare IRI or an array rather than asserting at-most-one: an example that
    #    happens to show one address is not a statement that only one is allowed.
    if model.is_class(rng):
        if iri_arr and not iri:
            return {"type": "array", "items": dict(IRI)}, required
        return {"oneOf": [dict(IRI), {"type": "array", "items": dict(IRI)}],
                "description": UNCONSTRAINED}, required

    if iri_arr:
        return {"type": "array", "items": {"type": "string"}}, False
    return {"type": "string"}, False


class Model:
    """The ontology facts the classifier needs, plus the set of class names."""

    def __init__(self, parents, cardinality, ranges, class_names):
        self.parents = parents
        self.cardinality = cardinality
        self.ranges = ranges
        self.class_names = class_names

    def is_class(self, name):
        return bool(name) and name in self.class_names

    def ancestors(self, cls):
        seen, stack = set(), [cls]
        while stack:
            for parent in self.parents.get(stack.pop(), ()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen

    def is_required(self, cls, prop):
        if prop in NEVER_REQUIRED or prop in PARENT_POINTERS:
            return False
        return any(self.cardinality.get(f"{a}|{prop}") == 1
                   for a in self.ancestors(cls) | {cls})

    def range_of(self, prop, linkml_slot):
        """The ontology's own allValuesFrom where unambiguous, else LinkML."""
        if prop in RANGE_OVERRIDES:
            return RANGE_OVERRIDES[prop]
        candidates = self.ranges.get(prop, set())
        if len(candidates) == 1:
            return next(iter(candidates))
        if prop in OBJECT_RANGES:
            return OBJECT_RANGES[prop][0]
        return (linkml_slot or {}).get("range")


def closure(out, roots):
    """Names reachable from `roots` by following $ref, in either direction."""
    keep = set(roots) & set(out)
    changed = True
    while changed:
        changed = False
        for name in list(keep):
            for ref in refs_in(out[name]):
                if ref in out and ref not in keep:
                    keep.add(ref)
                    changed = True
    return keep


def refs_in(node):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield ref.rsplit("/", 1)[-1]
        for value in node.values():
            yield from refs_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from refs_in(value)


def relax_refs(node, known):
    """Repoint nested $refs at the *Response variant, where one exists."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[-1]
            if f"{name}Response" in known:
                node["$ref"] = f"#/components/schemas/{name}Response"
        for value in node.values():
            relax_refs(value, known)
    elif isinstance(node, list):
        for value in node:
            relax_refs(value, known)


def body_subjects(doc):
    """(request subjects, response subjects) across all ld+json bodies.

    A container body holds several typed nodes in one @graph, so a body can be
    about more than one class. Request and response subjects are kept apart
    because they resolve to different schemas.
    """
    reqs, ress = set(), set()
    for item in (doc.get("paths") or {}).values():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            body = op.get("requestBody")
            if isinstance(body, dict):
                for ct, media in (body.get("content") or {}).items():
                    if ct.split(";")[0].strip() in JSON_TYPES:
                        reqs |= classes_in((media.get("schema") or {}).get("example"))
            for resp in (op.get("responses") or {}).values():
                if not isinstance(resp, dict):
                    continue
                for ct, media in (resp.get("content") or {}).items():
                    if ct.split(";")[0].strip() in JSON_TYPES:
                        ress |= classes_in((media.get("schema") or {}).get("example"))
    return reqs, ress


def classes_in(example):
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


def main():
    onto = fetch(ONTOLOGY, CACHE / "Business.rdf")
    linkml = fetch(LINKML, CACHE / "linkml.yaml")

    parents, cardinality, ranges, class_names = load_ontology(onto)
    model = Model(parents, cardinality, ranges, class_names)
    slots = yaml.safe_load(linkml.read_text())["slots"]
    by_alias = {a: s for s in slots.values() for a in (s.get("aliases") or [])}

    spec = yaml.safe_load(SPEC.read_text())
    shapes = observe(spec)

    req_subjects, resp_subjects = body_subjects(spec)
    subjects = req_subjects | resp_subjects

    props = collections.defaultdict(dict)
    for cls, prop in sorted(shapes):
        schema, required = classify(model, cls, prop, by_alias.get(prop), shapes[(cls, prop)])
        props[cls][f"dfc-b:{prop}"] = (schema, required)

    out = {
        "XsdTimeValue": {
            "type": "object",
            "description": "A JSON-LD value object carrying a time of day. Used by "
                           "dfc-b:startsAt on Step, which the ontology declares as an "
                           "ObjectProperty with no range.",
            "properties": {
                "@type": {"type": "string"},
                "@value": {"type": "string", "format": "time"},
            },
            "required": ["@value"],
        },
    }

    for cls in sorted(props):
        body = {
            "type": "object",
            "description": f"A dfc-b:{cls} resource.",
            "properties": {
                "@id": {"type": "string", "format": "uri-reference",
                        "description": "IRI of this resource."},
                "@type": {"type": "string", "enum": [f"dfc-b:{cls}"]},
            },
        }
        for pname, (schema, _) in sorted(props[cls].items()):
            body["properties"][pname] = schema
        req = [p for p, (_, is_req) in sorted(props[cls].items()) if is_req]
        if req:
            body["required"] = req
        out[cls] = body

    # A response may legitimately be partial: a container lists items without
    # repeating each one's obligatory links, and a Prefer: return=minimal reply
    # carries no body at all. So each class that has a required list also gets a
    # Response variant: same properties, no required list, and nested refs
    # repointed at the Response variants too. Built from the same data so the
    # pair cannot drift, and deep-copied because a shared dict would make
    # yaml.safe_dump emit an anchor, which does not belong in this file.
    # Two passes: decide which classes get a Response variant first, so that
    # relax_refs can see every target regardless of alphabetical order.
    # Response variants first: a body is wired to the Response variant where one
    # exists, so pruning has to be able to see them. Pruning before they are
    # built would seed on base names and keep request schemas that no response
    # body ever resolves to (CustomerCategory).
    relaxed_names = {f"{cls}Response" for cls, body in out.items()
                     if body.get("required") and cls in subjects
                     and not cls.endswith("Response")}
    for cls, source in list(out.items()):
        if f"{cls}Response" not in relaxed_names:
            continue
        relaxed = copy.deepcopy({k: v for k, v in source.items() if k != "required"})
        relax_refs(relaxed, relaxed_names)
        relaxed["description"] = (
            f"A dfc-b:{cls} resource as returned by the API. Identical to the request "
            "schema except that no property is required and nested nodes point at the "
            "Response variants: a representation may be partial.")
        out[f"{cls}Response"] = relaxed

    # Keep only what is reachable: a class that is the subject of some body, plus
    # everything it $refs (PaymentMethod and QuantitativeValue are only ever
    # nested, but their schemas are needed for those refs to resolve). Anything
    # else is dead weight and Spectral rightly reports it as an unused component.
    # Request bodies point at the request schema; response bodies at the Response
    # variant, so each closure is seeded from its own subjects.
    req_keep = closure(out, {c for c in req_subjects if c in out})
    # A response subject with no required list gets no Response variant, so its
    # base schema is what a response body resolves to and must be kept.
    resp_seeds = {f"{c}Response" for c in subjects if f"{c}Response" in out}
    resp_seeds |= {c for c in resp_subjects if f"{c}Response" not in out and c in out}
    resp_keep = closure(out, resp_seeds)
    out = {k: v for k, v in out.items() if k in req_keep | resp_keep}

    text = yaml.safe_dump({"schemas": out}, sort_keys=False,
                          default_flow_style=False, width=100, allow_unicode=True)
    if "&id" in text or re.search(r": \*\w+$", text, re.M):
        sys.exit("refusing to write: yaml aliases/anchors in the generated block")

    # Two spaces: schemas is a child of components, alongside securitySchemes.
    indented = "\n".join(("  " + line) if line.strip() else line
                         for line in text.rstrip("\n").split("\n"))
    block = ("  # --- generated by scripts/gen_schemas.py from\n"
             "  # --- DFC_BusinessOntology v2.0.0. Hand edits belong in that script,\n"
             "  # --- not here: re-running it will overwrite them.\n"
             + indented + "\n"
             "  # --- end generated ---\n")

    marker_start = "  # --- generated by scripts/gen_schemas.py"
    marker_end = "  # --- end generated ---"
    text_all = SPEC.read_text()
    pattern = re.compile(re.escape(marker_start) + r".*?" + re.escape(marker_end) + r"\n",
                         re.S)
    if pattern.search(text_all):
        text_all = pattern.sub(block, text_all, count=1)
    else:
        anchor = "\n  securitySchemes:"
        at = text_all.index(anchor)
        text_all = text_all[:at] + "\n" + block + text_all[at:]
    SPEC.write_text(text_all)

    total = sum(len(v.get("properties", {})) for v in out.values())
    reqs = sum(len(v.get("required", [])) for v in out.values())
    print(f"# {len(out)} schemas, {total} properties, {reqs} required entries -> {SPEC}",
          file=sys.stderr)


if __name__ == "__main__":
    main()