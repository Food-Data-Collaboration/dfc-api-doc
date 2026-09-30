# AGENTS.md

Four hand-maintained OpenAPI 3 documents describing the Data Food Consortium standard. No code, no build, no tests, no CI. Everything below was verified against the files on `v2.0.0` (= `origin/main`).

## Files

| File | `openapi` | Ops | Shape | Purpose |
|---|---|---|---|---|
| `dfc.yaml` | 3.0.1 | 36 | plain REST under `/api/dfc/...` | Primary DFC 2.0.0 API |
| `dfc-ldp.yaml` | 3.0.1 | 32 | LDP/Solid discovery, `ldp:Container` | Discoverability endpoints. Consumers fetch this from `raw.githubusercontent.com/.../main/dfc-ldp.yaml` — breaking it breaks downstream tools |
| `INRAE-dfc.yaml` | 3.0.0 | 1 | single nested-tree example | Research endpoint, nested shape |
| `INRAE-dfc-graph.yaml` | 3.0.0 | 1 | same endpoint, flat `@graph` | Same endpoint as above in `@graph` form |

`INRAE-dfc.yaml` and `INRAE-dfc-graph.yaml` are **alternatives**, not duplicates — both must stay in sync when the INRAE payload changes.

Not real: `.swagger-codegen/VERSION` (`3.0.58`) and `.swagger-codegen-ignore` are unmaintained swagger-codegen 2.x leftovers. There is no generator; edit YAML by hand.

## Validation

The only executable check is Spectral, and it is a **global** binary (v6.15.x at `/usr/local/bin/spectral`) — there is no `package.json`, so `npm ci` does nothing.

```bash
spectral lint *.yaml                # all four; exit 1 if any error
spectral lint dfc-ldp.yaml          # one file
spectral lint *.yaml --fail-severity=error   # errors only, ignore warnings
spectral lint dfc.yaml --format stylish
```

- `.spectral.yaml` extends `spectral:oas` + `spectral:asyncapi` + `spectral:arazzo`.
- **Bar: zero errors.** The 179 warnings are the accepted baseline — mainly `operation-operationId`, `operation-description`, and `operation-tag-defined`. There is deliberately no global `tags:` section and the specs carry no `operationId`, so do not add them just to quiet Spectral.
- `--fail-severity=error` is the right pre-commit gate: it still *prints* warnings but only errors affect the exit code. It currently passes.

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

- **Branch model**: long-lived release branches named after the DFC spec version — `v1.16.0` (frozen at DFC 1.16.0), `v2.0.0` (DFC 2.0.0, identical content to `main`). `main` is the merge trunk; work lands via PR (`#7` swaggerhub→v1.16.0→main, `#8` v2.0.0→main). `swaggerhub`, `swaggerAPI`, `INRAE-date` are dead older branches. No git tags.
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

`dfc-ldp.yaml` is gaining write operations (POST/PUT/DELETE). Once those land,
that tool synthesises a write contract *from the ontology* while a real one is
published in the spec it reads. Treat its read-only reasoning as **out of date**,
not authoritative. Don't edit that repo from here.

## Publishing

GitHub is the source of truth; SwaggerHub syncs automatically. Nothing to do by hand after a merge. `README.md` links the published page.
