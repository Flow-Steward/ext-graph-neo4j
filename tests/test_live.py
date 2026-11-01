"""Opt-in disposable Neo4j integration; never uses a production connection."""

import json
from pathlib import Path
import uuid

import pytest

import main
from validation import connection_values

FIXTURE = Path(__file__).with_name("live-neo4j.local.json")
pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="Disposable Neo4j fixture absent")


def test_live_graph_vector_lifecycle(monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    connection = fixture.get("connection") or {
        "config": {k: v for k, v in fixture.items() if k not in {"username", "password"}},
        "secrets": {k: fixture[k] for k in ("username", "password")},
    }
    config, secrets = connection_values(connection)
    monkeypatch.setenv("FS_ALLOW_PRIVATE_REMOTE_URLS", "true")
    prefix = "FsTest" + uuid.uuid4().hex[:12]
    index = prefix + "Vector"

    def invoke(name, args, credentials=None):
        return main.handle(
            {
                "contract_version": "extension_host_v2",
                "mode": "action",
                "runtime_context": {
                    "group_id": None,
                    "for_each_id": None,
                    "step_id": "step",
                    "step_execution_id": uuid.uuid4().hex,
                    "account_id": "a",
                    "project_id": "p",
                    "external_effect_id": uuid.uuid4().hex,
                },
                "action": {
                    "action_id": name,
                    "input": {"connection_ref": "fixture", **args},
                    "target": {
                        "connection": {
                            "connection_id": "fixture",
                            "connection_type": "neo4j",
                            "config": config,
                            "secrets": credentials or secrets,
                        }
                    },
                },
            }
        )

    def run(name, **args):
        response = invoke(name, args)
        diagnostic = (
            invoke("get_vector_index", {"index_name": args["index_name"]})
            if not response["ok"] and name == "ensure_vector_index"
            else None
        )
        assert response["ok"], json.dumps({"response": response, "metadata": diagnostic})
        return response["result"]

    try:
        assert run("ensure_node_constraint", label=prefix)["applied"]
        assert run("ensure_node_constraint", label=prefix)["applied"]
        assert (
            run(
                "upsert_node",
                label=prefix,
                node_id="one",
                properties={"text": "first", "integer": 3},
            )["count"]
            == 1
        )
        assert (
            run("upsert_node", label=prefix, node_id="two", properties={"text": "second"})["count"]
            == 1
        )
        assert run("get_node", label=prefix, node_id="one")["count"] == 1
        assert run("find_nodes", label=prefix, filter={"text": "first"})["count"] == 1
        assert (
            run(
                "upsert_relationship",
                from_label=prefix,
                from_id="one",
                to_label=prefix,
                to_id="two",
                relationship_type="LINKS",
                relationship_id="edge",
                properties={"weight": 1},
            )["count"]
            == 1
        )
        assert run("find_relationships", label=prefix, node_id="one")["count"] == 1
        assert run(
            "delete_relationship",
            from_label=prefix,
            from_id="one",
            to_label=prefix,
            to_id="two",
            relationship_type="LINKS",
            relationship_id="edge",
        )["applied"]
        created = run("ensure_vector_index", index_name=index, label=prefix, dimensions=3)
        assert created["records"][0]["name"] == index
        assert run("get_vector_index", index_name=index)["count"] == 1
        mismatch = invoke(
            "ensure_vector_index", {"index_name": index, "label": prefix, "dimensions": 4}
        )
        assert mismatch["error_code"] == "index_configuration_mismatch"
        wrong_dimension = invoke(
            "upsert_vector_node",
            {"index_name": index, "node_id": "never-created", "vector": [1, 0]},
        )
        assert wrong_dimension["error_code"] == "invalid_vector"
        assert wrong_dimension["definitely_no_external_effect"]
        assert run("get_node", label=prefix, node_id="never-created")["count"] == 0
        assert any(r["name"] == index for r in run("list_vector_indexes", limit=100)["records"])
        run(
            "upsert_vector_node",
            index_name=index,
            node_id="one",
            vector=[1, 0, 0],
            properties={"text": "source passage"},
        )
        run("await_vector_index", index_name=index, wait_seconds=10)
        found = run("vector_search", index_name=index, vector=[1, 0, 0], limit=5)
        assert found["count"] >= 1
        hit = next(r for r in found["records"] if r["node_id"] == "one")
        assert hit["properties"]["text"] == "source passage"
        assert "embedding" not in hit["properties"]
        run(
            "upsert_vector_node",
            index_name=index,
            node_id="one",
            vector=[0, 1, 0],
            properties={"updated": True},
        )
        included = run("vector_search", index_name=index, vector=[0, 1, 0], include_embeddings=True)
        updated = next(r for r in included["records"] if r["node_id"] == "one")
        assert (
            updated["properties"]["updated"] and updated["properties"]["text"] == "source passage"
        )
        assert updated["properties"]["embedding"] == [0.0, 1.0, 0.0]
        run("delete_vector_node", index_name=index, node_id="one")
        assert run("get_node", label=prefix, node_id="one")["count"] == 0
        assert all(
            r["node_id"] != "one"
            for r in run("vector_search", index_name=index, vector=[0, 1, 0])["records"]
        )
        assert run("delete_node", label=prefix, node_id="two")["applied"]
        response = invoke(
            "find_nodes",
            {"label": prefix},
            {**secrets, "password": "deliberately-wrong-fixture-password"},
        )
        assert response["error_code"] == "authentication_failed", response
        assert "deliberately-wrong" not in json.dumps(response)

    finally:
        if run("get_vector_index", index_name=index)["count"]:
            run("drop_vector_index", index_name=index, confirm=True)
        for node_id in ("one", "two"):
            run("delete_node", label=prefix, node_id=node_id, detach=True)
