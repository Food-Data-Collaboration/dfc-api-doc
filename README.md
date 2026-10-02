# dfc-api-doc

OpenAPI descriptions for the [Data Food Consortium](https://www.dfc-standard.org/) (DFC) standard.

## Published API references

| Spec | Published at |
|---|---|
| `dfc.yaml` — DFC REST API | [SwaggerHub](https://app.swaggerhub.com/apis-docs/food-data-collab/dfc-sample_api/) |
| `dfc-ldp.yaml` — LDP / Solid discovery and write API | Scalar, namespace and slug `@siol-data` |

`dfc-ldp.yaml` is published automatically by
[`.github/workflows/publish-dfc-ldp-to-scalar.yml`](.github/workflows/publish-dfc-ldp-to-scalar.yml)
whenever it changes on `main`.

## Specifications

All four describe DFC v2.0.0.

| File | Description |
|---|---|
| `dfc.yaml` | The primary DFC API. Plain REST, 20 paths. |
| `dfc-ldp.yaml` | LDP containers and resource write endpoints. 33 paths, of which 20 are writable. |
| `INRAE-dfc.yaml` | Research endpoint exposing anonymised sales data, nested form. |
| `INRAE-dfc-graph.yaml` | The same endpoint in flat `@graph` form. |

The two `INRAE` files are alternatives rather than duplicates, and must be kept
in step with each other.

## Validating a change

```bash
spectral lint *.yaml --fail-severity=error
```

Spectral is not installed by this repository; it is expected on your `PATH`.
Errors must be zero. The warnings are a known baseline and should not be
"fixed" by adding `operationId` or `tags` to the specs.

`dfc-ldp.yaml` is additionally validated in CI with Scalar's own OpenAPI
validator before being published.

## Contributing

`AGENTS.md` records the conventions these specs follow, including several
traps that are easy to fall into — duplicated YAML keys silently dropping
values, the two serialisations that must agree, and terms that the specs use
but the ontology does not declare.

## Licence

See [LICENSE](LICENSE).
