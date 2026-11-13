# Neo4j workflow actions

Configure a Neo4j project connection with the server host, port, TLS setting, database and stable ID property. Store username/password in connection secrets; Host takes a hostname or IP, not a URI. Grant graph read/write privileges for the chosen actions; constraint and index setup additionally requires schema privileges. Select that connection in each action. Node IDs are string business IDs stored in the configured property, not internal Neo4j IDs. Create/check a unique node ID constraint per label before concurrent node writes. Relationship actions use existing endpoint nodes.

Action names below match the workflow builder. IDs in parentheses remain compatible with saved workflows. Examples show field values; bind values from earlier steps as needed.

## Choose and configure an action

### Graph: Create or check a unique node ID (`ensure_node_constraint`)

Ensure uniqueness of the configured stable node ID within a label. Requires schema privileges.

```json
{
  "label": "Article"
}
```

### Graph: Create or update a node (`upsert_node`)

Merge a node by stable ID and patch its flat properties. Null values remove properties.

```json
{
  "label": "Article",
  "node_id": "manual_42",
  "properties": {
    "title": "Refund policy",
    "text": "Refunds are accepted within 30 days."
  }
}
```

### Graph: Read a node by stable ID (`get_node`)

Read a node by label and stable ID.

```json
{
  "label": "Article",
  "node_id": "manual_42"
}
```

### Graph: Find nodes by property values (`find_nodes`)

Read up to 100 nodes with an optional property equality filter.

```json
{
  "label": "Article",
  "filter": {
    "category": "refunds"
  }
}
```

### Graph: Delete a node (`delete_node`)

Delete a node. Detach must be explicitly enabled to remove its incident relationships.

```json
{
  "label": "Article",
  "node_id": "manual_42"
}
```

### Graph: Create or update a directed relationship (`upsert_relationship`)

Merge a directed relationship by endpoints, type and stable relationship ID, then patch properties.

```json
{
  "from_label": "Article",
  "from_id": "manual_42",
  "to_label": "Topic",
  "to_id": "refunds",
  "relationship_type": "ABOUT",
  "relationship_id": "manual_42_refunds",
  "properties": {
    "title": "Refund policy",
    "text": "Refunds are accepted within 30 days."
  }
}
```

### Graph: Read relationships and neighboring nodes (`find_relationships`)

Read bounded incident relationships and neighbors in the selected direction.

```json
{
  "label": "Article",
  "node_id": "manual_42"
}
```

### Graph: Delete a directed relationship (`delete_relationship`)

Delete a directed relationship identified by endpoints, type and relationship ID.

```json
{
  "from_label": "Article",
  "from_id": "manual_42",
  "to_label": "Topic",
  "to_id": "refunds",
  "relationship_type": "ABOUT",
  "relationship_id": "manual_42_refunds"
}
```

### Vector: Create or check a node index (`ensure_vector_index`)

Create or verify a node vector index with exact label, property, dimensions and similarity.

```json
{
  "index_name": "article_vectors",
  "label": "Article",
  "dimensions": 3
}
```

### Vector: Inspect a node index (`get_vector_index`)

Read a node vector index schema and ONLINE/POPULATING state.

```json
{
  "index_name": "article_vectors"
}
```

### Vector: List available indexes (`list_vector_indexes`)

List up to 100 vector indexes.

```json
{}
```

### Vector: Wait for a node index to be ready (`await_vector_index`)

Wait up to the bounded timeout for an existing node vector index to become ONLINE.

```json
{
  "index_name": "article_vectors"
}
```

### Vector: Delete an index and preserve nodes (`drop_vector_index`)

Drop a vector index only with confirm=true. Documents remain in the graph.

```json
{
  "index_name": "article_vectors",
  "confirm": true
}
```

### Vector: Save a node and its embedding (`upsert_vector_node`)

Merge a node with an embedding and metadata, validating dimensions against the selected index.

```json
{
  "index_name": "article_vectors",
  "node_id": "manual_42",
  "vector": [
    0.1,
    0.2,
    0.3
  ],
  "properties": {
    "title": "Refund policy",
    "text": "Refunds are accepted within 30 days."
  }
}
```

### Vector: Search similar nodes (`vector_search`)

Retrieve up to 100 similar nodes with scores and metadata using a supplied embedding. Use the indexing model and matching dimensions; embeddings are omitted from results by default. This step does not generate answers or embeddings.

```json
{
  "index_name": "article_vectors",
  "vector": [
    0.1,
    0.2,
    0.3
  ]
}
```

### Vector: Delete an indexed node (`delete_vector_node`)

Delete an indexed node by stable ID; the vector index updates with the graph.

```json
{
  "index_name": "article_vectors",
  "node_id": "manual_42"
}
```

## Requirements and result handling

Node and relationship upserts patch flat properties; omitted properties remain and null removes a property. Nested JSON objects are rejected, and property arrays must have a consistent scalar type. Do not put the stable ID property inside properties. Deleting a node with relationships requires `detach:true` to remove those relationships.

For vector retrieval, create/check a node index with a label, embedding property, model dimensions and similarity, then wait until it is ONLINE. Save a node and its embedding through that index; generate a question embedding with the same model before searching. A three-component example requires dimensions 3. Existing incompatible indexes are rejected rather than migrated. Dropping an index preserves nodes; deleting an indexed node removes that node (and only detaches relationships when explicitly enabled). Search returns scores and properties, not generated answers.

Workflow write actions are suppressed in test mode. Read/search actions still read the configured service. If a write fails after dispatch, inspect the service before repeating it; the write may already have applied.
