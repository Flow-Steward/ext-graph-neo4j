# Neo4j for Flow Steward

See [Workflow action guide](docs/workflow-actions.md) for action selection, setup requirements and JSON field examples.

Use Neo4j in Flow Steward for graph-backed records, relationship lookups and vector retrieval. Actions upsert, read, find and delete nodes using a label and stable string ID; create or patch directed relationships between existing nodes; and retrieve incident edges and neighboring nodes. Create a uniqueness constraint for each node label before concurrent upserts.

Vector actions create or verify a node vector index, inspect and list indexes, wait for an index to become ready, or drop it while preserving graph nodes. Write embeddings and metadata to indexed nodes, search for nearest neighbors with scores, and remove indexed nodes when source data changes. Indexes support cosine or Euclidean similarity and vectors of 1–4096 components. Combine retrieved source properties and graph relationships with your own answer-generation workflow.

Configure a direct Bolt endpoint, database and username/password. Supply embeddings externally; chunking, model calls and citation generation belong to your workflow. Reads return at most 100 records, and properties are flat values or homogeneous primitive arrays. Cluster routing and arbitrary Cypher are outside the operation set. Node deletion needs explicit detach to remove connected relationships, and schema operations require appropriate Neo4j privileges.

## Runtime and operation requirements

An independent Neo4j connector for graph reads and writes and native node vector search. It includes one minimal Google MCP Toolbox executable per installer archive. The connector adds workflow actions; it does not need changes to Core or database triggers.

## Install

Use the ZIP matching the **Linux extension host** architecture:

- `flowsteward.neo4j-1.0.10-linux-amd64.zip `
- `flowsteward.neo4j-1.0.10-linux-arm64.zip `

Verify the release's `SHA256SUMS ` before importing the archive. Each ZIP contains one executable for its own target, host-v2 contracts, UI forms, source patches and build provenance. Platform 1.4.0+ and extension SDK 0.4.0 are required.

## Connection and data model

Create a project **Neo4j** connection. Set `host ` to a hostname or IP, `port ` (default 7687), `database ` (default `neo4j `), `id_property ` (default `id `), and `request_timeout_seconds ` (1–30, default 15). Store both `username ` and `password ` in the secret fields.

The connection uses direct Bolt: `tls: true ` selects verified `bolt+s `, while `tls: false ` selects plaintext `bolt `. Public endpoints require TLS. Private endpoints require the administrator's `FS_ALLOW_PRIVATE_REMOTE_URLS ` opt-in. Hostnames and certificates are verified; each socket destination is resolved, checked and connected using an approved IP while retaining the original TLS hostname. Database credentials control access to the configured database. The connector writes into the selected labels and properties directly, without an added project prefix. Use database roles appropriate for that project.

Direct Bolt endpoints are supported. Cluster routing, custom certificate files, self-signed certificate bypasses and arbitrary URI options are outside this release. Aura requires an endpoint that accepts direct verified Bolt; a real Aura deployment was not used for local acceptance.

Nodes use a label plus a string ID stored in `id_property `. Run `ensure_node_constraint ` for each label before concurrent upserts to enforce uniqueness. A relationship uses source/target label+ID, relationship type and a string relationship ID stored in the same ID property. Relationships are directed. Labels, types, property identifiers and index names use `[A-Za-z_][A-Za-z0-9_]{0,63}`; IDs are nonempty strings up to 256 UTF-8 bytes.

Properties are flat maps of strings, booleans, finite numbers, null, or homogeneous primitive arrays. Null removes a property during a patch. Property maps cannot replace the stable ID. Nested property maps and arrays containing null are rejected. JSON integers must be within the safe range ±(2^53−1); existing larger Neo4j integers returned by the database are represented as `{"$integer":"9223372036854775807"}`. Lexically distinct integer and floating-point inputs retain their Neo4j numeric types.

## Graph actions

Every action requires `connection_ref `.

| Action | Inputs | Behavior |
| --- | --- | --- |
| `ensure_node_constraint ` | `label ` | Ensure a uniqueness constraint for the connection's ID property |
| `upsert_node ` | `label `, `node_id `, optional `properties ` | Merge by stable ID and patch properties |
| `get_node ` | `label `, `node_id ` | Read matching node records |
| `find_nodes ` | `label `, optional `filter `, `limit ` | Read bounded nodes using property equality |
| `delete_node ` | `label `, `node_id `, optional `detach ` | Delete node; connected relationships require explicit detach |
| `upsert_relationship ` | `from_label `, `from_id `, `to_label `, `to_id `, `relationship_type `, `relationship_id `, optional `properties ` | Merge/patch a directed edge between existing nodes |
| `find_relationships ` | `label `, `node_id `, optional `relationship_type `, `direction `, `limit ` | Read edges and adjacent nodes; direction is `out `, `in ` or `both ` |
| `delete_relationship ` | Same endpoint/type/relationship ID fields | Delete matching directed edges |

Upserts patch existing properties. Missing endpoints produce no relationship. Reads and relationship queries default to 20 records, with a maximum of100. Schema creation requires the corresponding Neo4j privileges. Existing duplicate IDs must be resolved before a uniqueness constraint can be created; graph operations use the configured identity fields as supplied by the database.

## Vector actions

Node vector indexes support dimensions 1–4096 and `cosine ` or `euclidean ` similarity. The connector checks actual index metadata before operating on vector nodes. Node vector index operations reject relationship vector indexes.

| Action | Inputs | Behavior |
| --- | --- | --- |
| `ensure_vector_index ` | `index_name `, `label `, `dimensions `, optional `embedding_property ` (default `embedding `), `similarity ` (default `cosine `) | Create/verify the exact index configuration |
| `get_vector_index ` | `index_name ` | Read schema, options and state |
| `list_vector_indexes ` | Optional `limit ` | List bounded vector index metadata |
| `await_vector_index ` | `index_name `, optional `wait_seconds ` (default 10,max 20) | Wait for an existing index to become ONLINE, within the shared request deadline |
| `drop_vector_index ` | `index_name `, `confirm: true ` | Drop the vector index; graph nodes remain |
| `upsert_vector_node ` | `index_name `, `node_id `, `vector `, optional `properties ` | Write/replace the embedding and patch metadata |
| `vector_search ` | `index_name `, `vector `, optional `limit `, `min_score `, `include_embeddings ` | Retrieve neighbors with scores and metadata |
| `delete_vector_node ` | `index_name `, `node_id `, optional `detach ` | Delete the indexed node; its index entry is removed |

The index determines the label, embedding property, dimension and metric. Configuration mismatches are errors rather than silent acceptance of an existing unrelated index. Embeddings must have the correct dimension and finite components; cosine embeddings must have nonzero norm. Search requires the index to be ONLINE. Existing data can be indexed; new index population may take time. Each action opens a new transaction, so committed vector updates are visible to later searches.

Search defaults to 20 neighbors (maximum100); `min_score ` filters the returned nearest neighbors. Approximate vector search may return fewer records. Scores apply to that vector index and metric. Set `include_embeddings: true ` only when the vector values are needed; the default omits the indexed embedding from search properties at the server. Text, document IDs, URLs and other source metadata remain available for downstream RAG steps.

Neo4j 5.26Community is used for live acceptance. Search uses `db.index.vector.queryNodes ` for compatibility with Neo4j5.x. This procedure remains available in current versions but is deprecated since 2026.04 in favor of `SEARCH `; newer server deployments require their own acceptance before relying on future compatibility.

## Results and RAG composition

Every action returns `records `, `count ` and `applied `. Read actions return `applied: false `; writes indicate the completed operation or affected records. An ensure operation can succeed when the requested schema already exists. Node records contain native element IDs, labels and properties; edge records contain type, endpoint IDs and properties. Vector search records include `node_id `, `labels `, `properties ` and `score `.

A typical retrieval pipeline:

1. Create the node uniqueness constraint and vector index; wait for ONLINE.
2. Generate embeddings using your embedding provider and write documents/chunks through `upsert_vector_node `. Store source IDs, text and URLs in properties.
3. Embed a question with the same model/dimension, then call `vector_search `.
4. Optionally follow graph relationships, then pass retrieved text and source metadata to your answer-generation step and attach citations from those metadata fields.
5. Update a document's vector/metadata using its stable ID, or delete the node when the source is removed.

This connector supplies graph storage and vector retrieval. Embedding generation, chunking and LLM answer generation belong to the surrounding workflow. Scores and metadata are returned as data; citations are not invented by this connector.

## Bounds and write outcomes

Inputs are limited to 256KiB, inline results to 480KiB, native rows to 100 and Bolt messages to 2 MiB. Reads stream within the result budget. Large existing property values or excessive projections can fail with `response_too_large `; use smaller limits and bounded data. The timeout covers all native calls inside an action, including vector metadata checks. Waiting for an index consumes that same budget.

Live writes require the host's durable `external_effect_id ` claim. Test mode validates input and suppresses writes before I/O. Fixed parameterized Cypher is compiled by the extension; action values are never executable query fragments. Native explicit transactions do not use managed automatic retries. Bounds are checked before commit, with rollback on pre-commit failures. A lost commit acknowledgement or process interruption can still leave an unknown write result: such failures are non-retryable and report `external_effect_status: timeout_unknown `. Reconcile the graph before retrying. An action that needs multiple transactions, such as ensuring an index and reading its metadata, is not one atomic transaction across those calls.

## Build and verify

```sh
./scripts/build_toolbox.sh
python3 scripts/package_extension.py --runtime-target linux-amd64
python3 scripts/package_extension.py --runtime-target linux-arm64
```

The build pins Toolbox v1.17.0 commit `45ed205cc8682ed066c82e0b65bcd0e8fc3becce`, Go image by digest, and Neo4j Go driver v6.4.0 by its Go module sum. Source and driver patch hashes, exact imported packages, executable SHA-256 and sizes are in `toolbox-provenance.json `. Native binaries are omitted from Git history. Packaging is deterministic and enforces the installer's 50MiB compressed/150MiB unpacked limits.

From a Flow Steward checkout, run the container gate:

```sh
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root /absolute/path/to/ext-graph-neo4j
```

Tests cover contracts, validation, shared deadlines, process/response bounds, effects, package boundaries and native provenance. Live tests opt in through ignored `tests/live-neo4j.local.json ` containing `host `, `port `, `database `, `id_property `, `username `, `password `, `tls `; use a disposable database. Without a fixture, the live test is explicitly skipped. Go verification covers patched source/tool/classifier suites, driver connector tests and Bolt bounds/timeouts. The upstream 16MiB Bolt fixture intentionally exceeds this distribution's 2MiB cap; an unrelated upstream local-time test is sensitive to the daylight-saving transition date and is not part of the focused patched-driver acceptance.

Apache-2.0. See `LICENSE ` and `NOTICE `. This project is unaffiliated with Neo4j, Inc.

## Automated CI and releases

Pushes to `main` and pull requests run the shared extension build workflow. The checked-in `.github/release-config.json` selects both Linux targets, the repository's package script and test inputs. Missing native payloads and published-only inventory are restored from checksum-pinned release archives; tracked source files are preserved. The source packager retains its provenance checks.

After the versioned release commit is on `main`, publish by pushing its version tag:

```sh
git tag v1.0.10
git push origin v1.0.10
```

The release workflow runs the shared checks, builds the target archives and publishes the GitHub release and checksums. Do not move an existing tag. To rerun a release for an existing tag, dispatch the same workflow:

```sh
gh workflow run release.yml --repo Flow-Steward/ext-graph-neo4j --ref main -f tag=v1.0.10
```

The manifest, descriptor (when present), package version and tag must identify the same release. Release publication uses the workflow token; no additional repository secret is required by these callers.

### Standalone CI coverage

The release recipe lists the standalone test files and individual test nodes explicitly, including package, source and provenance contracts. The shared runner clears repository pytest addopts so packaged contract checks are not silently deselected. This scope does not claim live provider or database verification.

The following integration coverage requires a separate environment and is not part of standalone CI:

- `tests/test_live.py`: Opt-in live provider/server test requires external credentials or a disposable service fixture.

Native CI builds Toolbox v1.17.0 from its pinned official source commit. Set `TOOLBOX_SOURCE` or `FS_TOOLBOX_SOURCE_DIR` only to an exact clean checkout. Generated native payloads and provenance are produced during CI and included in each target archive.
