"""Fixed Cypher compilation and bounded graph/vector workflows."""

import hashlib
import json
import math
from validation import boolean, identifier, integer, properties, require, text, vector

WRITES = {
    "ensure_node_constraint",
    "upsert_node",
    "delete_node",
    "upsert_relationship",
    "delete_relationship",
    "ensure_vector_index",
    "drop_vector_index",
    "upsert_vector_node",
    "delete_vector_node",
}
FIELDS = {
    "ensure_node_constraint": {"label"},
    "upsert_node": {"label", "node_id", "properties"},
    "get_node": {"label", "node_id"},
    "find_nodes": {"label", "filter", "limit"},
    "delete_node": {"label", "node_id", "detach"},
    "upsert_relationship": {
        "from_label",
        "from_id",
        "to_label",
        "to_id",
        "relationship_type",
        "relationship_id",
        "properties",
    },
    "find_relationships": {"label", "node_id", "relationship_type", "direction", "limit"},
    "delete_relationship": {
        "from_label",
        "from_id",
        "to_label",
        "to_id",
        "relationship_type",
        "relationship_id",
    },
    "ensure_vector_index": {
        "index_name",
        "label",
        "embedding_property",
        "dimensions",
        "similarity",
    },
    "get_vector_index": {"index_name"},
    "list_vector_indexes": {"limit"},
    "await_vector_index": {"index_name", "wait_seconds"},
    "drop_vector_index": {"index_name", "confirm"},
    "upsert_vector_node": {"index_name", "node_id", "vector", "properties"},
    "vector_search": {"index_name", "vector", "limit", "min_score", "include_embeddings"},
    "delete_vector_node": {"index_name", "node_id", "detach"},
}


def validate(name, args, config):
    require(name in FIELDS, "Unknown Neo4j action.")
    require(set(args) <= FIELDS[name] | {"connection_ref"}, "Unknown action input.")
    optional = {
        "properties",
        "filter",
        "limit",
        "detach",
        "direction",
        "embedding_property",
        "similarity",
        "wait_seconds",
        "min_score",
        "include_embeddings",
    }
    required = FIELDS[name] - optional
    if name == "find_relationships":
        required -= {"relationship_type"}
    require(required <= set(args), "Missing required action input.")
    a = dict(args)
    for key in {
        "label",
        "from_label",
        "to_label",
        "relationship_type",
        "index_name",
        "embedding_property",
    } & set(a):
        identifier(a[key])
    for key in {"node_id", "from_id", "to_id", "relationship_id"} & set(a):
        text(a[key], key, 256, nonempty=True)
    for key in ("properties", "filter"):
        if key in a:
            properties(a[key], config["id_property"] if key == "properties" else None)
    if "limit" in FIELDS[name]:
        a["limit"] = integer(a.get("limit", 20), "limit", 1, 100)
    for key in {"detach", "include_embeddings", "confirm"} & set(a):
        boolean(a[key], key)
    if "vector" in FIELDS[name]:
        a["vector"] = vector(a.get("vector"))
    if name == "ensure_vector_index":
        a["dimensions"] = integer(a.get("dimensions"), "dimensions", 1, 4096)
        a["embedding_property"] = identifier(a.get("embedding_property", "embedding"))
        require(
            a["embedding_property"] != config["id_property"],
            "Embedding property conflicts with stable ID.",
        )
        a["similarity"] = a.get("similarity", "cosine")
        require(a["similarity"] in ("cosine", "euclidean"), "Invalid similarity.")
    if name == "await_vector_index":
        a["wait_seconds"] = integer(a.get("wait_seconds", 10), "wait_seconds", 1, 20)
    if name == "drop_vector_index":
        require(a.get("confirm") is True, "Dropping an index requires confirm=true.")
    if name == "find_relationships":
        a["direction"] = a.get("direction", "both")
        require(a["direction"] in ("out", "in", "both"), "Invalid relationship direction.")
    if name == "vector_search":
        score = a.get("min_score", 0)
        require(
            type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1,
            "min_score must be from 0 to 1.",
        )
        a["min_score"] = score
    return a


def q(value):
    return "`" + identifier(value) + "`"


def tool(statement, params, write=False):
    return {
        "type": "neo4j-cypher",
        "statement": statement,
        "staticParameters": json.dumps(params, allow_nan=False),
        "parameters": [],
        "readOnly": not write,
        "annotations": {
            "readOnlyHint": not write,
            "destructiveHint": write,
            "idempotentHint": False,
        },
    }, {}


def compile_command(name, a, config, context=None, connection_id=None):
    key = q(config["id_property"])
    p = {k: v for k, v in a.items() if k != "connection_ref"}
    label = q(a["label"]) if "label" in a else None
    node = f"(n:{label} {{{key}: $node_id}})"
    statement = ""
    if name == "ensure_node_constraint":
        cname = (
            "fs_"
            + hashlib.sha256((a["label"] + ":" + config["id_property"]).encode()).hexdigest()[:32]
        )
        statement = (
            f"CREATE CONSTRAINT {q(cname)} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
        )
    elif name == "upsert_node":
        p["properties"] = a.get("properties", {})
        statement = f"MERGE {node} SET n += $properties RETURN n AS node"
    elif name == "get_node":
        statement = f"MATCH {node} RETURN n AS node"
    elif name == "find_nodes":
        p["filter"] = a.get("filter", {})
        statement = f"MATCH (n:{label}) WHERE all(k IN keys($filter) WHERE (n[k] = $filter[k] OR (n[k] IS NULL AND $filter[k] IS NULL))) RETURN n AS node LIMIT $limit"
    elif name in ("delete_node", "delete_vector_node"):
        statement = (
            f"MATCH {node} WITH n, 1 AS applied "
            + ("DETACH DELETE" if a.get("detach", False) else "DELETE")
            + " n RETURN applied"
        )
    elif name in ("upsert_relationship", "delete_relationship"):
        left = f"(a:{q(a['from_label'])} {{{key}: $from_id}})"
        right = f"(b:{q(a['to_label'])} {{{key}: $to_id}})"
        rel = f"(a)-[r:{q(a['relationship_type'])} {{{key}: $relationship_id}}]->(b)"
        statement = f"MATCH {left}, {right} "
        if name == "upsert_relationship":
            p["properties"] = a.get("properties", {})
            statement += f"MERGE {rel} SET r += $properties RETURN r AS relationship"
        else:
            statement += f"MATCH {rel} DELETE r RETURN 1 AS applied"
    elif name == "find_relationships":
        r = "[r" + (":" + q(a["relationship_type"]) if a.get("relationship_type") else "") + "]"
        pattern = {
            "out": f"(n)-{r}->(neighbor)",
            "in": f"(n)<-{r}-(neighbor)",
            "both": f"(n)-{r}-(neighbor)",
        }[a["direction"]]
        statement = f"MATCH {node} MATCH {pattern} RETURN r AS relationship, neighbor LIMIT $limit"
    elif name in ("get_vector_index", "list_vector_indexes"):
        statement = (
            "SHOW VECTOR INDEXES YIELD name, state, entityType, labelsOrTypes, properties, options "
        )
        if name == "get_vector_index":
            statement += "WHERE name = $index_name "
        statement += "RETURN name, state, entityType, labelsOrTypes, properties, options"
        if name == "list_vector_indexes":
            statement += " LIMIT $limit"
    elif name == "ensure_vector_index":
        statement = f"CREATE VECTOR INDEX {q(a['index_name'])} IF NOT EXISTS FOR (n:{label}) ON (n.{q(a['embedding_property'])}) OPTIONS {{indexConfig: {{`vector.dimensions`: $dimensions, `vector.similarity_function`: $similarity}}}}"
    elif name == "drop_vector_index":
        statement = f"DROP INDEX {q(a['index_name'])} IF EXISTS"
    elif name == "await_vector_index":
        statement = "CALL db.awaitIndex($index_name, $wait_seconds)"
    require(bool(statement), "Unsupported graph command.")
    return tool(statement, p, name in WRITES)


def normalize_result(name, value, args=None):
    require(isinstance(value, list), "Invalid provider rows.", code="invalid_provider_response")
    rows = value[0] if len(value) == 1 and isinstance(value[0], list) else value
    require(
        isinstance(rows, list) and len(rows) <= 100 and all(isinstance(r, dict) for r in rows),
        "Invalid bounded provider rows.",
        code="invalid_provider_response",
    )
    return {
        "records": rows,
        "count": len(rows),
        "applied": name in WRITES
        and (
            bool(rows)
            or name in {"ensure_node_constraint", "ensure_vector_index", "drop_vector_index"}
        ),
    }


def execute(name, args, config, context, call):
    a = validate(name, args, config)

    def run(op, inputs):
        command, arguments = compile_command(op, inputs, config)
        return normalize_result(op, call(command, arguments), inputs)

    def metadata():
        rows = run("get_vector_index", {"index_name": a["index_name"]})["records"]
        require(len(rows) == 1, "Vector index was not found.", code="index_not_found")
        row = rows[0]
        require(
            row.get("name") == a["index_name"]
            and len(row.get("labelsOrTypes", [])) == 1
            and len(row.get("properties", [])) == 1,
            "Invalid vector index metadata.",
            code="index_configuration_mismatch",
        )
        require(
            row.get("entityType") == "NODE",
            "Only node vector indexes are supported.",
            code="index_configuration_mismatch",
        )
        identifier(row["labelsOrTypes"][0])
        identifier(row["properties"][0])
        return row

    if name == "ensure_node_constraint":
        run(name, a)
        statement = "SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties WHERE entityType = 'NODE' AND type CONTAINS 'UNIQUENESS' AND labelsOrTypes = [$label] AND properties = [$id_property] RETURN name, type, entityType, labelsOrTypes, properties LIMIT 2"
        result = normalize_result(
            name,
            call(*tool(statement, {"label": a["label"], "id_property": config["id_property"]})),
        )
        require(
            result["count"] > 0,
            "Node uniqueness constraint configuration differs.",
            code="index_configuration_mismatch",
        )
        return result
    if name == "ensure_vector_index":
        run(name, a)
        row = metadata()
        opts = row.get("options", {}).get("indexConfig", {})
        require(
            row["labelsOrTypes"] == [a["label"]]
            and row["properties"] == [a["embedding_property"]]
            and opts.get("vector.dimensions") == a["dimensions"]
            and str(opts.get("vector.similarity_function", "")).lower() == a["similarity"],
            "Existing index configuration differs.",
            code="index_configuration_mismatch",
        )
        return {"records": [row], "count": 1, "applied": True}
    if name in {
        "upsert_vector_node",
        "vector_search",
        "delete_vector_node",
        "drop_vector_index",
        "await_vector_index",
    }:
        row = metadata()
        if name in {"upsert_vector_node", "vector_search"}:
            opts = row.get("options", {}).get("indexConfig", {})
            require(
                len(a["vector"]) == opts.get("vector.dimensions"),
                "Vector dimension differs from index.",
                code="invalid_vector",
            )
            require(
                str(opts.get("vector.similarity_function", "")).lower() != "cosine"
                or any(v != 0 for v in a["vector"]),
                "Cosine vector must have nonzero norm.",
                code="invalid_vector",
            )
            if name == "vector_search":
                require(
                    row.get("state") == "ONLINE",
                    "Vector index must be ONLINE.",
                    code="index_not_online",
                )
                projection = (
                    "properties(node)"
                    if a.get("include_embeddings", False)
                    else "node{.*, " + q(row["properties"][0]) + ": null}"
                )
                result = normalize_result(
                    name,
                    call(
                        *tool(
                            "CALL db.index.vector.queryNodes($index_name, $limit, $vector) YIELD node, score WHERE score >= $min_score RETURN node[$id_property] AS node_id, labels(node) AS labels, "
                            + projection
                            + " AS properties, score",
                            {
                                "index_name": a["index_name"],
                                "limit": a["limit"],
                                "vector": a["vector"],
                                "min_score": a["min_score"],
                                "id_property": config["id_property"],
                            },
                        )
                    ),
                )
                if not a.get("include_embeddings", False):
                    for record in result["records"]:
                        require(
                            isinstance(record.get("properties"), dict),
                            "Invalid vector match.",
                            code="invalid_provider_response",
                        )
                        record["properties"].pop(row["properties"][0], None)
                return result
            props = dict(a.get("properties", {}))
            require(
                row["properties"][0] != config["id_property"],
                "Embedding property conflicts with stable ID.",
                code="index_configuration_mismatch",
            )
            props[row["properties"][0]] = a["vector"]
            return run(
                "upsert_node",
                {"label": row["labelsOrTypes"][0], "node_id": a["node_id"], "properties": props},
            )
        if name == "delete_vector_node":
            return run(
                "delete_node",
                {
                    "label": row["labelsOrTypes"][0],
                    "node_id": a["node_id"],
                    "detach": a.get("detach", False),
                },
            )
        if name == "await_vector_index":
            a["wait_seconds"] = min(
                a["wait_seconds"], max(1, config["request_timeout_seconds"] - 1)
            )
    return run(name, a)
