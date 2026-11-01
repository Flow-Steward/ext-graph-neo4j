import json
from unittest.mock import patch
import pytest
import main
from commands import compile_command, execute, validate
from validation import Failure, connection_values, properties

CONFIG = {
    "host": "example.com",
    "port": 7687,
    "tls": True,
    "database": "neo4j",
    "id_property": "id",
    "request_timeout_seconds": 15,
}


def test_closed_and_safe_inputs():
    for name, args in [
        ("get_node", {"label": "A` DELETE n", "node_id": "x"}),
        ("get_node", {"label": "A", "node_id": "x", "cypher": "MATCH(n)"}),
        ("upsert_node", {"label": "A", "node_id": "x", "properties": {"id": "replace"}}),
        ("find_nodes", {"label": "A", "limit": True}),
    ]:
        with pytest.raises(Failure):
            validate(name, args, CONFIG)
    for props in ({"a": {"nested": 1}}, {"a": 2**53}, {"a": [True, 1]}, {"a": [None]}):
        with pytest.raises(Failure):
            properties(props)


def test_static_parameters_and_fixed_identifier():
    args = {"label": "Document", "node_id": "$literal", "properties": {"value": 3, "weight": 3.0}}
    command, arguments = compile_command("upsert_node", args, CONFIG)
    assert arguments == {}
    assert "$literal" not in command["statement"]
    assert json.loads(command["staticParameters"])["properties"] == args["properties"]
    assert '"value": 3,' in command["staticParameters"]
    assert '"weight": 3.0' in command["staticParameters"]
    assert command["readOnly"] is False


def test_connection_is_closed():
    with pytest.raises(Failure):
        connection_values(
            {
                "config": {"host": "bolt://example.com"},
                "secrets": {"username": "u", "password": "p"},
            }
        )
    with pytest.raises(Failure):
        connection_values(
            {
                "config": {"host": "example.com", "allowPrivate": True},
                "secrets": {"username": "u", "password": "p"},
            }
        )


def test_vector_metadata_dimension_and_embeddings():
    row = {
        "name": "idx",
        "entityType": "NODE",
        "state": "ONLINE",
        "labelsOrTypes": ["Doc"],
        "properties": ["embedding"],
        "options": {
            "indexConfig": {"vector.dimensions": 2, "vector.similarity_function": "cosine"}
        },
    }
    calls = []

    def call(tool, args):
        calls.append(tool)
        if tool["statement"].startswith("SHOW"):
            return [[row]]
        return [
            [
                {
                    "node_id": "one",
                    "properties": {"text": "hello", "embedding": [1.0, 0.0]},
                    "score": 1,
                }
            ]
        ]

    result = execute("vector_search", {"index_name": "idx", "vector": [1, 0]}, CONFIG, {}, call)
    assert result == {
        "records": [{"node_id": "one", "properties": {"text": "hello"}, "score": 1}],
        "count": 1,
        "applied": False,
    }
    with pytest.raises(Failure):
        execute("vector_search", {"index_name": "idx", "vector": [0, 0]}, CONFIG, {}, call)
    assert len(calls) == 3


def test_missing_index_never_drops_nonvector():
    calls = []

    def call(command, args):
        calls.append(command)
        return [[]]

    with pytest.raises(Failure):
        execute("drop_vector_index", {"index_name": "ordinary", "confirm": True}, CONFIG, {}, call)
    assert len(calls) == 1 and calls[0]["readOnly"]


def test_empty_and_noop_results():
    assert execute(
        "get_node", {"label": "Doc", "node_id": "missing"}, CONFIG, {}, lambda *args: [[]]
    ) == {"records": [], "count": 0, "applied": False}
    assert (
        execute(
            "delete_node", {"label": "Doc", "node_id": "missing"}, CONFIG, {}, lambda *args: [[]]
        )["applied"]
        is False
    )


def test_testmode_suppresses_before_dispatch():
    payload = {
        "mode": "action",
        "action": {
            "action_id": "upsert_node",
            "input": {"connection_ref": "c", "label": "Doc", "node_id": "one"},
            "target": {
                "connection": {
                    "connection_id": "c",
                    "connection_type": "neo4j",
                    "config": {"host": "example.com"},
                    "secrets": {"username": "u", "password": "p"},
                }
            },
        },
        "runtime_context": {"test_mode": True, "account_id": "a", "project_id": "p"},
    }
    with (
        patch.object(main, "runtime_context_from_payload"),
        patch.object(main.toolbox, "invoke") as invoke,
    ):
        result = main.handle(payload)
    assert result["error_code"] == "test_mode_suppressed"
    assert result["definitely_no_external_effect"]
    invoke.assert_not_called()


def envelope(action="upsert_node"):
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "runtime_context": {
            "group_id": None,
            "for_each_id": None,
            "step_id": "step",
            "step_execution_id": "exec",
            "account_id": "a",
            "project_id": "p",
            "external_effect_id": "claim",
        },
        "action": {
            "action_id": action,
            "input": {"connection_ref": "conn", "label": "Doc", "node_id": "one"},
            "target": {
                "connection": {
                    "connection_id": "conn",
                    "connection_type": "neo4j",
                    "config": {"host": "example.com"},
                    "secrets": {"username": "u", "password": "p"},
                }
            },
        },
    }


def test_host_claim_connection_and_uncertainty(monkeypatch):
    payload = envelope()
    payload["runtime_context"].pop("external_effect_id")
    calls = []
    monkeypatch.setattr(main.toolbox, "invoke", lambda *a, **k: calls.append(a))
    assert main.handle(payload)["error_code"] == "effect_claim_required"
    assert not calls
    payload = envelope()
    payload["action"]["input"]["connection_ref"] = "different"
    assert main.handle(payload)["error_code"] == "invalid_connection"
    assert not calls
    payload = envelope()

    def fail(*args, **kwargs):
        calls.append(args)
        raise Failure("timeout", "Bounded provider timeout.", dispatched=True)

    monkeypatch.setattr(main.toolbox, "invoke", fail)
    result = main.handle(payload)
    assert result["external_effect_status"] == "timeout_unknown"
    assert result["retryable"] is False
    assert result["definitely_no_external_effect"] is False
    assert len(calls) == 1


def test_metadata_failure_before_write_is_definitely_not_applied(monkeypatch):
    payload = envelope("delete_vector_node")
    payload["action"]["input"] = {
        "connection_ref": "conn",
        "index_name": "Missing",
        "node_id": "one",
    }
    monkeypatch.setattr(main.toolbox, "invoke", lambda *a, **k: [[]])
    result = main.handle(payload)
    assert result["error_code"] == "index_not_found"
    assert result["definitely_no_external_effect"]
    assert result["external_effect_status"] == "not_applied"


@pytest.mark.parametrize(
    "name,args,fragment",
    [
        ("ensure_node_constraint", {"label": "Doc"}, "REQUIRE n.`id` IS UNIQUE"),
        (
            "upsert_node",
            {"label": "Doc", "node_id": "one", "properties": {"text": "updated"}},
            "SET n += $properties",
        ),
        ("get_node", {"label": "Doc", "node_id": "one"}, "MATCH (n:`Doc`"),
        ("find_nodes", {"label": "Doc", "filter": {"missing": None}}, "IS NULL"),
        ("delete_node", {"label": "Doc", "node_id": "one", "detach": True}, "DETACH DELETE"),
        (
            "upsert_relationship",
            {
                "from_label": "Doc",
                "from_id": "one",
                "to_label": "Doc",
                "to_id": "two",
                "relationship_type": "LINKS",
                "relationship_id": "r",
            },
            "MERGE (a)-[r:`LINKS` {`id`: $relationship_id}]->(b)",
        ),
        (
            "delete_relationship",
            {
                "from_label": "Doc",
                "from_id": "one",
                "to_label": "Doc",
                "to_id": "two",
                "relationship_type": "LINKS",
                "relationship_id": "r",
            },
            "DELETE r",
        ),
        (
            "find_relationships",
            {"label": "Doc", "node_id": "one", "direction": "in"},
            "(n)<-[r]-(neighbor)",
        ),
        ("list_vector_indexes", {}, "SHOW VECTOR INDEXES"),
        ("await_vector_index", {"index_name": "idx"}, "CALL db.awaitIndex"),
        ("drop_vector_index", {"index_name": "idx", "confirm": True}, "DROP INDEX `idx` IF EXISTS"),
    ],
)
def test_bounded_fixed_operation_plans(name, args, fragment):
    validated = validate(name, args, CONFIG)
    command, parameters = compile_command(name, validated, CONFIG)
    assert fragment in command["statement"]
    assert command["parameters"] == [] and parameters == {}
    if "limit" in validated:
        assert validated["limit"] == 20


def index_metadata(**changes):
    return {
        "name": "idx",
        "entityType": "NODE",
        "state": "ONLINE",
        "labelsOrTypes": ["Doc"],
        "properties": ["embedding"],
        "options": {
            "indexConfig": {"vector.dimensions": 2, "vector.similarity_function": "cosine"}
        },
        **changes,
    }


def test_ensure_vector_index_refuses_conflicting_existing_configuration():
    calls = []

    def call(command, args):
        calls.append(command)
        return [[index_metadata()]] if command["readOnly"] else [[]]

    with pytest.raises(Failure) as error:
        execute(
            "ensure_vector_index",
            {"index_name": "idx", "label": "Other", "dimensions": 2},
            CONFIG,
            {},
            call,
        )
    assert error.value.code == "index_configuration_mismatch"
    assert len(calls) == 2
    assert "IF NOT EXISTS" in calls[0]["statement"]


def test_vector_update_preserves_other_properties_and_rejects_wrong_dimension():
    calls = []

    def call(command, args):
        calls.append(command)
        return (
            [[index_metadata(state="POPULATING")]]
            if command["readOnly"]
            else [[{"node": {"id": "one"}}]]
        )

    result = execute(
        "upsert_vector_node",
        {
            "index_name": "idx",
            "node_id": "one",
            "vector": [1, 0],
            "properties": {"text": "preserved"},
        },
        CONFIG,
        {},
        call,
    )
    assert result["applied"]
    assert "SET n += $properties" in calls[-1]["statement"]
    assert json.loads(calls[-1]["staticParameters"])["properties"] == {
        "text": "preserved",
        "embedding": [1.0, 0.0],
    }
    calls.clear()
    with pytest.raises(Failure) as error:
        execute(
            "upsert_vector_node",
            {"index_name": "idx", "node_id": "one", "vector": [1, 0, 0]},
            CONFIG,
            {},
            call,
        )
    assert error.value.code == "invalid_vector"
    assert len(calls) == 1 and calls[0]["readOnly"]
    calls.clear()
    with pytest.raises(Failure) as error:
        execute("vector_search", {"index_name": "idx", "vector": [1, 0]}, CONFIG, {}, call)
    assert error.value.code == "index_not_online"
    assert len(calls) == 1


def test_delete_vector_uses_index_label_without_deleting_neighbor():
    calls = []

    def call(command, args):
        calls.append(command)
        return [[index_metadata()]] if command["readOnly"] else [[{"applied": 1}]]

    assert execute("delete_vector_node", {"index_name": "idx", "node_id": "one"}, CONFIG, {}, call)[
        "applied"
    ]
    assert "MATCH (n:`Doc` {`id`: $node_id})" in calls[-1]["statement"]
    assert "DETACH" not in calls[-1]["statement"]
    assert "DELETE n" in calls[-1]["statement"]


def test_invalid_destructive_inputs_do_not_dispatch():
    calls = []
    for action, args in [
        ("drop_vector_index", {"index_name": "idx"}),
        ("delete_node", {"label": "Doc"}),
        ("delete_relationship", {"from_label": "Doc"}),
        ("upsert_vector_node", {"index_name": "idx", "node_id": "one", "vector": [float("nan")]}),
    ]:
        with pytest.raises(Failure):
            execute(action, args, CONFIG, {}, lambda *a: calls.append(a))
    assert not calls


def test_response_budget_after_write_preserves_uncertainty(monkeypatch):
    monkeypatch.setattr(main.toolbox, "invoke", lambda *a, **k: [[{"value": "x" * (480 * 1024)}]])
    result = main.handle(envelope())
    assert result["error_code"] == "response_too_large"
    assert result["external_effect_status"] == "timeout_unknown"
    assert not result["definitely_no_external_effect"]


def test_relationship_vector_index_and_id_collision_rejected_before_mutation():
    calls = []

    def call(command, args):
        calls.append(command)
        return [[index_metadata(entityType="RELATIONSHIP")]]

    with pytest.raises(Failure) as error:
        execute(
            "upsert_vector_node",
            {"index_name": "idx", "node_id": "one", "vector": [1, 0]},
            CONFIG,
            {},
            call,
        )
    assert error.value.code == "index_configuration_mismatch"
    assert len(calls) == 1
    calls.clear()
    with pytest.raises(Failure):
        execute(
            "ensure_vector_index",
            {"index_name": "idx", "label": "Doc", "embedding_property": "id", "dimensions": 2},
            CONFIG,
            {},
            call,
        )
    assert not calls


def test_constraint_name_collision_cannot_claim_enforced_uniqueness():
    calls = []

    def call(command, args):
        calls.append(command)
        return [[]]

    with pytest.raises(Failure) as error:
        execute("ensure_node_constraint", {"label": "Doc"}, CONFIG, {}, call)
    assert error.value.code == "index_configuration_mismatch"
    assert len(calls) == 2 and calls[1]["readOnly"]


def test_vector_search_omits_large_embeddings_inside_database():
    calls = []

    def call(command, args):
        calls.append(command)
        return [[index_metadata()]] if command["statement"].startswith("SHOW") else [[]]

    execute("vector_search", {"index_name": "idx", "vector": [1, 0]}, CONFIG, {}, call)
    assert "node{.*, `embedding`: null} AS properties" in calls[-1]["statement"]


def test_actual_neo4j_uppercase_similarity_is_verified_and_zero_cosine_rejected():
    row = index_metadata()
    row["options"]["indexConfig"]["vector.similarity_function"] = "COSINE"

    def call(command, args):
        return [[row]] if command["readOnly"] else [[]]

    assert execute(
        "ensure_vector_index",
        {"index_name": "idx", "label": "Doc", "dimensions": 2},
        CONFIG,
        {},
        call,
    )["applied"]
    with pytest.raises(Failure) as error:
        execute(
            "upsert_vector_node",
            {"index_name": "idx", "node_id": "one", "vector": [0, 0]},
            CONFIG,
            {},
            call,
        )
    assert error.value.code == "invalid_vector"


def test_unexpected_invoke_exception_cannot_falsely_exclude_write_effects(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("private unexpected diagnostic")

    monkeypatch.setattr(main.toolbox, "invoke", fail)
    response = main.handle(envelope())
    assert not response["definitely_no_external_effect"]
    assert response["external_effect_status"] == "timeout_unknown"
    assert "private unexpected" not in json.dumps(response)
    read = main.handle(envelope("get_node"))
    assert read["definitely_no_external_effect"]
    assert read["external_effect_status"] == "not_applied"


@pytest.mark.parametrize("field", ["account_id", "project_id"])
@pytest.mark.parametrize("value", [None, "", " ", 42])
def test_project_scope_is_required_before_provider_io(monkeypatch, field, value):
    payload = envelope("get_node")
    payload["runtime_context"][field] = value
    calls = []

    def invoke(*args, **kwargs):
        calls.append(args)
        return [[]]

    monkeypatch.setattr(main.toolbox, "invoke", invoke)
    result = main.handle(payload)
    assert not result["ok"]
    assert result["definitely_no_external_effect"]
    assert not calls
