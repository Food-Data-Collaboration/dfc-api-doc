# AGENTS.md

Four hand-maintained OpenAPI 3 documents describing the Data Food Consortium standard. No build, no test suite, no `package.json`. Everything below was verified against `main`.

## Files

| File | `openapi` | Paths | Ops | Shape | Purpose |
|---|---|---|---|---|---|
| `dfc.yaml` | 3.0.1 | 20 | 36 | plain REST under `/api/dfc/...` | Primary DFC 2.0.0 API. Write-capable (18 GET, 8 POST, 7 PUT, 3 DELETE) |
| `dfc-ldp.yaml` | 3.0.1 | 33 | 74 | LDP/Solid, `ldp:Container` | Discoverability **and** write endpoints. 20 of 33 paths writable. Published to Scalar (see "Publishing") |
| `INRAE-dfc.yaml` | 3.0.0 | 1 | 1 | single nested-tree example | Research endpoint, nested shape |
| `INRAE-dfc-graph.yaml` | 3.0.0 | 1 | 1 | same endpoint, flat `@graph` | Same endpoint as above in `@graph` form |

112 operations total. `dfc-ldp.yaml` is by far the largest at ~5,700 lines; the
rest are small.

`INRAE-dfc.yaml` and `INRAE-dfc-graph.yaml` are **alternatives**, not duplicates — both must stay in sync when the INRAE payload changes.

Consumers fetch `dfc-ldp.yaml` from
`raw.githubusercontent.com/.../main/dfc-ldp.yaml`, so breaking it breaks
downstream tools.

Not real: `.swagger-codegen/VERSION` (`3.0.58`) and `.swagger-codegen-ignore` are unmaintained swagger-codegen 2.x leftovers.

## Scripts

`scripts/` holds three one-shot helpers. None is run by CI and none is a build
step; the YAML they emit is meant to be hand-edited afterwards.

| Script | Does |
|---|---|
| `gen_schemas.py` | Regenerates the marked `components/schemas` block in `dfc-ldp.yaml` from the ontology + LinkML |
| `wire_schemas.py` | Points every `application/ld+json` body at a schema |
| `validate_examples.py` | Validates every example against its schema; the gate to run before committing a spec change |

`gen_schemas.py` and `wire_schemas.py` fetch the ontology and the LinkML schema
over HTTPS and cache them under `/tmp/opencode/dfc-card/`. Nothing else in the
repo has a network dependency, so do not make anything else depend on that path.

## Validation

The only executable check is Spectral, and it is a **global** binary (v6.15.x at `/usr/local/bin/spectral`) — there is no `package.json`, so `npm ci` does nothing.

```bash
spectral lint *.yaml                # all four; exit 1 if any error
spectral lint dfc-ldp.yaml          # one file
spectral lint *.yaml --fail-severity=error   # errors only, ignore warnings
spectral lint dfc.yaml --format stylish
```

- `.spectral.yaml` extends `spectral:oas` + `spectral:asyncapi` + `spectral:arazzo`.
- **Bar: zero errors.** Warnings are the accepted baseline — mainly
  `operation-operationId`, `operation-operationId`, `operation-tags` and
  `operation-tag-defined`. There is deliberately no global `tags:` section and
  the specs carry no `operationId`, so do not add them just to quiet Spectral.
  Both counts scale with the operation count; compare like for like rather
  than against a fixed number.
- `--fail-severity=error` is the right pre-commit gate: it still *prints*
  warnings but only errors affect the exit code. It currently passes.

Spectral is **not** run in CI, so it is only as available as the person running
it has it installed. CI validates `dfc-ldp.yaml` with Scalar's own validator
instead — see "Publishing".

### Example validation

Spectral never looks at an example, so an example can contradict its own
schema and nothing notices. `scripts/validate_examples.py` does that check: it
walks every `application/ld+json` example and validates each `@graph` entry
against its schema.

```bash
python3 scripts/validate_examples.py     # exit 1 on any failure
```

Needs PyYAML only — no npm — so unlike Spectral it *could* run in CI unchanged.
It is not wired up yet; see "Publishing".

### Typed schemas

`dfc-ldp.yaml` has a real `components/schemas`. The block is **generated** and
sits between two marker comments; anything you hand-edit inside it will be lost
the next time the generator runs.

```bash
python3 scripts/gen_schemas.py     # regenerate components/schemas
python3 scripts/wire_schemas.py    # point every ld+json body at a schema
```

Both are idempotent, and both are one-shot: the output is ordinary YAML that
you are expected to hand-edit afterwards. Edit the script, not the block, if a
rule itself is wrong.

The generator derives multiplicity from `DFC_BusinessOntology.rdf` directly,
and takes ranges from DFC-LinkML. It does **not** use LinkML's `multivalued`
flag — see item 14. Rules, in precedence order:

1. **Literal range → scalar.** A JSON-LD literal is one value whatever the
   cardinality says. Getting this wrong makes every `dfc-b:name` an array.
2. **`owl:cardinality 1` on the class or an ancestor → scalar**, and `required`
   on a request body.
3. **An example already writes an array → array.**
4. **An example writes an inline node → scalar `$ref`** to that node's schema.
   Only this case is a `$ref`: a link property holds an IRI even when its range
   is a class, so reading `$ref` off the range types
   `Organization.hasMainContact` as a `Person` object when the examples hold a
   `Person` IRI.
5. **Otherwise the ontology says nothing → `oneOf: [IRI, array of IRI]`**,
   with a description saying so. Absence of an OWL restriction is *unspecified*,
   not "many", and asserting either would invent a constraint.

Seven properties land in case 5: `Address.basedAt`, `Person.affiliates`,
`Person.hasAddress`, `TemplateSaleSession.coordinatedBy`, `hasOption`,
`hostedAt`, `objectOf`.

Responses get a `<Class>Response` twin — same properties, no `required`, nested
refs repointed at the Response variants — because a representation may be
partial: a container lists items without repeating each one's obligatory links,
and a `Prefer: return=minimal` reply carries no body at all.

Two cardinality-1 properties are deliberately **not** `required`, because they
are the link back to the parent that embeds the node and writing them would make
a child point at a parent that already contains it:

- `OrderLine.partOf` — inverse of `Order.hasPart`
- `Offer.offers` — inverse of `CatalogItem.offeredThrough`

Both serialisations must be kept in step. Every example appears twice, as
`application/ld+json` and `text/turtle`, and they must agree:

- JSON-LD writes a predicate as `dfc-b:term: value`
- Turtle writes `dfc-b:term value ;` — **no colon**

A rename applied to only one of the two leaves the file silently
inconsistent. Check both.

### Multi-valued properties must be YAML sequences

Repeated keys in a mapping are **not** a way to express multiple values — YAML
keeps only the last one, so the graph silently loses relations. This was a real
bug here: `dfc-b:supplies` was three repeated keys and resolved to one value.
The fix is a sequence, in whichever style the enclosing node requires:

- Inside a **flow mapping** (`{`, e.g. `dfc.yaml:505`, `INRAE-dfc-graph.yaml:192`)
  a block sequence is illegal and makes the file unparseable — use flow style
  `key: [a, b] ,`. This is the style at `dfc-b:supplies` and `dfc-b:hasPart`.
- Inside a **block mapping** use a block sequence (as at `dfc-b:hasVariant`).

Match the enclosing context, not the neighbouring sites.

## Things that break silently

Spectral does not check any of these.

1. **Example bodies are not valid JSON-LD CURIEs, and that is intentional.** `dfc.yaml` and both INRAE files write unquoted `"@type": dfc-b:Organization` — a bare `dfc-b:Organization` key, not a CURIEs string. `dfc-ldp.yaml` quotes them correctly (`"dfc-b:Organization"`). This is house style, not a typo. Do not "fix" the unquoted form.
2. **Unquoted scalars swallow trailing commas.** 7 `@id` values in `dfc.yaml` (e.g. L506, L559) and 8 in `INRAE-dfc-graph.yaml` end with `,` *inside* the URI, because a plain YAML scalar only terminates at a flow delimiter. Compare the 5 quoted cases in `dfc.yaml` (e.g. L132, `"@id": "#" ,`) where the comma is a correct YAML separator. If you reformat, keep the comma outside the scalar.
3. **Duplicate content types.** `dfc.yaml` has two overlapping inline `@context` blocks (`dfc.yaml:186-242` and `dfc.yaml:1168+`) with *different* prefix maps. Response examples mostly use a bare `@context` URL; request bodies use the inline maps.
4. **`dfc-ldp.yaml` uses prefixes the official context never defines.** The published `https://w3id.org/dfc/ontology/context/context_2.0.0.json` defines only `rdfs skos dfc dc dfc-b dfc-t dfc-m dfc-pt dfc-f dfc-v ontosec`. The file also uses `ldp:`, `foaf:`, `geojson:`, `cal:`, `solid:`, `xsd:` — all undeclared. It is technically valid JSON-LD 1.1 (relative IRIs resolve against the vocab map) but nothing renders as intended. Any prefix you add needs an inline `@context` entry.
5. **`x-dfc-version` is inconsistently applied.** It is a required header on every operation (commit `bfd3f34` "Add x-dfc-version header to all API specs") but 8 ops in `dfc.yaml` are missing it: `Organizations/{id}` GET/POST/PUT/DELETE (L484/567/601/630), `customerCategories/{customerCategory_id}/Offers/{id}` GET (L726), `PhoneNumbers/{id}` GET (L842), `SuppliedProducts/{id}` GET (L1108), `Orders/{id}` GET (L1520). Add it when touching an operation.
6. **The INRAE specs still declare `application/json`** while everything else uses `application/ld+json` (commit `9c049c2` did not reach them).
7. **Two dead URI sets remain in `dfc.yaml` inline contexts.** `dfc.yaml:189-196` points at `http://static.datafoodconsortium.org/...` — the host no longer resolves in DNS. `dfc.yaml:1174` points at `DFC_ProductGlossary.owl` — 404; ontology v2.0.0 ships only `DFC_BusinessOntology.owl`, `DFC_FullModel.owl`, `DFC_TechnicalOntology.owl`. There is no `DFC_Product*` ontology in v2.0.0 and the official context has no `dfc-p` prefix; the `dfc-p:hasUnit` / `dfc-p:hasType` term defs are dead but harmless.
8. **`openIdConnectUrl` is wrong in all four files.** They all use `https://login.fooddatacollaboration.org.uk/auth/realm/dev`, which 404s. The live discovery document is:
   `https://login.fooddatacollaboration.org.uk/realms/dev/.well-known/openid-configuration` (issuer `.../realms/dev` — no `/auth`, Keycloak `realms` plural).
9. **`INRAE-dfc-graph.yaml:105,112` say `dfc-b-supplies:`** (hyphen instead of colon), making those two supplied-product links untyped and invisible to the ontology.
10. **Mixed schemes in examples**: `http://test.host` (dominant) vs `https://test.host`; `dfc-ldp.yaml` uses `https://platform.ex`. Example hosts are placeholders — keep them stable rather than tidying.
11. **`dfc-b:Route` steps carry neither a location nor an ordering.**
    `dfc-b:sequence` and `dfc-b:locatedAt` were removed: neither is declared in
    any published ontology version, and the v2.0.0 `Route` / `Step` /
    `PickUpStep` / `DeliveryStep` classes have **no restrictions at all**. A step
    now carries only `dfc-b:startsAt`. Both properties were authored into the
    examples by commit `02655c5` and never taken to the ontology working group;
    they are being raised with its author. Do not reintroduce them.
12. **`dfc-b:Certification` is not the ontology's spelling.** The ontology
    declares the class as `dfc-b:Certfication` (missing the `i`); this file uses
    the correct spelling and always has. The ontology is being corrected.
    Note the contrast: `dfc-b:certiferReference` *is* the ontology's own
    misspelling and is therefore correct as written here.
13. **`dfc-b:image` is unverified.** Reported as a property of
    `dfc-b:DefinedProduct` inherited by `SuppliedProduct`, but not found
    declared in the business, technical or full-model ontologies. Left in place
    pending confirmation. Schema-wise it is a free-form string with that caveat
    recorded in its `description`.
14. **`dfc-b:occursAt` is not in the business ontology either.** Same situation
    as `image`, and stronger: searching the class declarations finds no
    `occursAt` at all. In the examples it is spelled in the iCalendar namespace
    and carries a fragment IRI (`.../weekly-delivery/index#schedule`). There is
    an open issue with the ontology working group; do not "fix" the spelling.
15. **`dfc-b:startsAt` is a JSON-LD value object, not a timestamp string.** The
    ontology declares it an `ObjectProperty` with no range, but every example
    writes `{"@type": "...#time", "@value": "08:00:00"}`. It is typed against a
    generated `XsdTimeValue` schema. The same property name means different
    things on different classes, so type from the examples, not the range.
16. **DFC-LinkML's `multivalued` is not cardinality.** It is derived from
    `owl:inverseOf`, and it flags 42 slots of which **39 are properties the
    ontology does not constrain at all**, while the 42 `owl:cardinality 1`
    axioms in `DFC_BusinessOntology.rdf` overlap with it in only **three** —
    `Organization.hasMainContact`, `PhysicalPlace.hasAddress`,
    `CatalogItem.listedIn` — all three of which it wrongly marks multi-valued.
    It also carries **no `required:` flags**, and
    `tests/test_model_reference.py::test_no_unmodelled_cardinality_is_claimed`
    actively fails if anything claims cardinality the schema does not carry. So
    it cannot drive array-vs-scalar or `required`; its `range` data is sound and
    is used. Prefer it not at all for multiplicity.
17. **`dfc-b:totalTheoriticalStock` is pinned but unusable.** `owl:cardinality
    1` on `SuppliedProduct`, yet the term appears in neither spelling in the
    published context, so no example can use it without a dangling term. The
    schema declares it optional, with the reason in its `description`. Making it
    `required` would invalidate every `SuppliedProduct` example.

Checking a new term against the ontology is worth the effort — a scan of the
examples for undeclared terms is what turned up items 11 to 15. Two useful
facts: the ontology's `.owl` and `.rdf` serialisations are **not** equivalent
(`.owl` carries no `owl:Restriction`, no cardinality axioms and no inverse
pairs; `.rdf` carries 370 restrictions, 42 cardinality axioms, 68 inverse pairs
and 104 ranges), and `dfc-b:totalTheoriticalStock` — the ontology's own
misspelling, pinned to `cardinality 1` — appears in **neither** spelling in the
published context, so no example can use it without a dangling term.

## Version coupling

DFC version is duplicated in many places and **nothing validates it**. A version bump must change all of:

- `info.version: v2.0.0` — 1 per file, 4 files
- Every `@context` URL `.../context_2.0.0.json` — 32 in `dfc.yaml`, 27 in `dfc-ldp.yaml`, 1 in each INRAE file (61 total)
- Every `example: "2.0.0"` on the `x-dfc-version` parameters — 37 / 33 / 2 / 2
- `dfc-ldp.yaml:66-67` — `dfc-t:supportedProtocolVersion` and `dfc-t:supportedOntologyVersion` in the platform WebID example
- `dfc-ldp.yaml:13` — the version in the `info.description` prose

Verify (occurrence counts, not line counts):

```bash
grep -o 'context_2\.0\.0\.json' *.yaml | cut -d: -f1 | uniq -c
grep -oE 'example: "2\.0\.0"' *.yaml | cut -d: -f1 | uniq -c
```

## Ontology & taxonomy sources

The ontology defines the model; SKOS taxonomies hold reference/enumerated values. Pin to a release with a `v<version>` segment (verified: `https://w3id.org/dfc/ontology/v2.0.0/src/DFC_BusinessOntology.owl` → 200):

```
https://w3id.org/dfc/ontology/v{version}/src/DFC_BusinessOntology.owl
https://w3id.org/dfc/ontology/src/DFC_FullModel.owl
https://w3id.org/dfc/ontology/src/DFC_TechnicalOntology.owl
https://w3id.org/dfc/taxonomies/{facets|productTypes|vocabulary|measures|scopes}.rdf
```

`https://w3id.org/dfc/ontology/src/...` (no `v<version>`) also resolves but via jsDelivr on the **default branch**, so it is unpinned. `dfc.yaml:1171-1178` uses the unpinned form. The published `context_2.0.0.json` itself uses `http://w3id.org/dfc/ontology/v2.0.0/src/...`.

## Naming drift between spec generations

DFC v2 renamed `Enterprise` → `Organization`, but path templates were never updated:

- Path params are still `{enterprise_id}` / `{id}` while the body type is `dfc-b:Organization`.
- `dfc-ldp.yaml` uses `{organizationId}` and kebab-case segments (`/supplied-products`, `/template-sale-sessions`) while `dfc.yaml` uses PascalCase (`/SuppliedProducts`, `/SaleSessions`).
- Typo carried from v1: `AffilateSalesData` (missing `i`) in both INRAE files.

Do not "correct" these in isolation — they are the published contract. Change all sides of a rename together.

## Git

- **Branch model**: long-lived release branches named after the DFC spec version — `v1.16.0` (frozen at DFC 1.16.0), `v2.0.0` (DFC 2.0.0, identical content to `main`). `main` is the merge trunk; work lands via PR (`#7` swaggerhub→v1.16.0→main, `#8` v2.0.0→main, `#9`–`#13`). `swaggerhub`, `swaggerAPI`, `INRAE-date` are dead older branches. No git tags.
- The local `main` ref is often **stale** — `git fetch` before branching off it.
- Commit to a working branch; if already on one, check whether to continue or branch fresh off the request subject.
- **Only push when the user asks.**
- Any new scripts go in a `scripts/` subfolder (does not exist yet).

## Known downstream consumer (will be stale)

`../fdc-onboarding-mapping` fetches `dfc-ldp.yaml` from `main` and hardcodes the
assumption that it is **read-only** — 31 `GET` endpoints, no request bodies. That
premise lives in six places there, most visibly `README.md:5-9` ("Why the
ontology, not `dfc-ldp.yaml`") and `src/spec_generator.py:161`, which writes the
literal header `"The DFC LDP spec is read-only"` into its output.

`dfc-ldp.yaml` now has 20 writable paths, so that tool synthesises a write
contract *from the ontology* while a real one is published in the spec it reads.
Treat its read-only reasoning as **out of date**, not authoritative. It will not
fail loudly — it will just emit output that contradicts its own input. Don't
edit that repo from here; it's tracked there.

## Publishing

Two destinations, both downstream of GitHub. Nothing to do by hand after a merge.

**SwaggerHub** — `food-data-collab/dfc-sample_api`, syncs automatically. Linked
from `README.md`.

**Scalar** — namespace and slug `@siol-data`, `dfc-ldp.yaml` only (the other
three specs are stale or superseded). Published by
`.github/workflows/publish-dfc-ldp-to-scalar.yml`, which fires on a push to
`main` that changes `dfc-ldp.yaml`, plus `workflow_dispatch` for a manual
re-publish.

Things to know before editing that workflow:

- `@scalar/cli` is pinned to **2.8.0** and needs **Node >=24** (`node-version: 24`
  is set for this reason). The vendor docs use a bare `npx @scalar/cli`, which
  would resolve to whatever is latest at run time.
- The API key comes from the repo secret `SCALAR_API_KEY`, passed via `env:`
  rather than `--token` so it stays out of argv.
- Namespace and slug are both set at the top of the workflow to `@siol-data`, so
  they cannot drift apart. Move them to repo variables if they ever need changing
  without a code edit.
- There is **no** post-publish verification step: the endpoint for fetching a
  published document could not be confirmed, and a guessed URL would fail every
  publish. The CLI's own exit code is the signal.
- CI validates with Scalar's validator, **not** Spectral — see "Validation".
  `scripts/validate_examples.py` is the obvious candidate to add to that
  workflow: it needs only PyYAML, so it would sidestep the Spectral-is-a-global-
  binary problem entirely. Not done yet.

Local equivalent, if you want to check before pushing:

```bash
export PATH="$HOME/.nodenv/versions/24.14.1/bin:$PATH"   # Scalar CLI needs Node >=24
npx @scalar/cli@2.8.0 document validate dfc-ldp.yaml
```
