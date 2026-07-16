"""
MCP Adapter for CKS Runtime.

Provides a JSON-RPC request handler that delegates all operations
to the Runtime façade.  This adapter is the single integration point
between the MCP transport and the CKS Runtime operational layer.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from cks_runtime.runtime import Runtime
from cks_runtime.core_api.validation_result import RuntimeValidationResult


class MCPAdapter:
    """Translates MCP JSON‑RPC requests into Runtime API calls."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._tools = {
            "validate_knowledge": {
                "name": "validate_knowledge",
                "description": "Validate a Knowledge Structure and return diagnostics.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "json_data": {
                            "type": "string",
                            "description": "A JSON string representing a Knowledge Structure.",
                        },
                    },
                    "required": ["json_data"],
                },
            },
            "query_relations": {
                "name": "query_relations",
                "description": "Query all relations for a given entity.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "json_data": {
                            "type": "string",
                            "description": "A JSON string representing a Knowledge Structure.",
                        },
                        "entity_id": {
                            "type": "string",
                            "description": "The canonical identity of the entity to query.",
                        },
                    },
                    "required": ["json_data", "entity_id"],
                },
            },
            "compare_structures": {
                "name": "compare_structures",
                "description": "Compare two knowledge structures for semantic equivalence.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "json_data_a": {
                            "type": "string",
                            "description": "A JSON string representing the first Knowledge Structure.",
                        },
                        "json_data_b": {
                            "type": "string",
                            "description": "A JSON string representing the second Knowledge Structure.",
                        },
                    },
                    "required": ["json_data_a", "json_data_b"],
                },
            },
            "evolve_knowledge": {
                "name": "evolve_knowledge",
                "description": "Apply a sequence of structural operators to a knowledge structure.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "json_data": {
                            "type": "string",
                            "description": "A JSON string representing a Knowledge Structure.",
                        },
                        "operations": {
                            "type": "string",
                            "description": "A JSON string with an array of operation objects.",
                        },
                    },
                    "required": ["json_data", "operations"],
                },
            },
            "derive_knowledge": {
                "name": "derive_knowledge",
                "description": "Derive a new Knowledge Object from existing premises.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "json_data": {
                            "type": "string",
                            "description": "A JSON string representing a Knowledge Structure.",
                        },
                        "premises": {
                            "type": "string",
                            "description": "A JSON array of canonical identities used as premises.",
                        },
                        "rule": {
                            "type": "string",
                            "description": "The canonical inference rule identifier.",
                        },
                        "conclusion_id": {
                            "type": "string",
                            "description": "Canonical identity for the new Knowledge Object.",
                        },
                        "conclusion_type": {
                            "type": "string",
                            "description": "Canonical type for the new Knowledge Object.",
                        },
                        "conclusion_name": {
                            "type": "string",
                            "description": "Human-readable name for the new Knowledge Object.",
                        },
                    },
                    "required": ["json_data", "premises", "rule", "conclusion_id", "conclusion_type", "conclusion_name"],
                },
            },
        }

    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a single JSON‑RPC request and return a response."""
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": list(self._tools.values()),
            }

        if method == "tools/call":
            return self._handle_tool_call(req_id, params)

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found"},
        }

    def _handle_tool_call(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name not in self._tools:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
            }

        try:
            result = self._execute_tool(tool_name, args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result,
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Execute a single tool, delegating to Runtime."""
        if tool_name == "validate_knowledge":
            return self._validate_knowledge(args)
        if tool_name == "query_relations":
            return self._query_relations(args)
        if tool_name == "compare_structures":
            return self._compare_structures(args)
        if tool_name == "evolve_knowledge":
            return self._evolve_knowledge(args)
        if tool_name == "derive_knowledge":
            return self._derive_knowledge(args)
        raise ValueError(f"Unknown tool: {tool_name}")

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _validate_knowledge(self, args: Dict[str, Any]) -> str:
        json_data = args.get("json_data", "")
        structure = json.loads(json_data) if isinstance(json_data, str) else json_data
        result = self._runtime.core_adapter.validate(structure)
        return json.dumps({
            "valid": result.valid,
            "error_count": result.diagnostic_count if not result.valid else 0,
            "warning_count": 0,
            "diagnostics": list(result.diagnostics),
        })

    def _query_relations(self, args: Dict[str, Any]) -> str:
        json_data = args.get("json_data", "")
        entity_id = args.get("entity_id", "")
        # Parse the structure, then query relations
        structure = json.loads(json_data) if isinstance(json_data, str) else json_data
        from cks.core import KnowledgeStructure
        if isinstance(structure, dict):
            from cks.serialization import parse as cks_parse
            structure = cks_parse(structure)
        relations = [rel for rel in structure.relations() if entity_id in rel.participants]
        return json.dumps({
            "entity_id": entity_id,
            "relations": [
                {
                    "relation_id": rel.identity.id,
                    "relation_type": rel.relation_type,
                    "participants": list(rel.participants),
                }
                for rel in relations
            ],
        })

    def _compare_structures(self, args: Dict[str, Any]) -> str:
        json_a = args.get("json_data_a", "")
        json_b = args.get("json_data_b", "")
        from cks.serialization import parse as cks_parse
        struct_a = cks_parse(json.loads(json_a) if isinstance(json_a, str) else json_a)
        struct_b = cks_parse(json.loads(json_b) if isinstance(json_b, str) else json_b)
        is_equiv = struct_a.structurally_equivalent(struct_b)
        return json.dumps({
            "equivalent": is_equiv,
            "objects_a": len(struct_a.objects),
            "objects_b": len(struct_b.objects),
            "relations_a": len(struct_a.relations()),
            "relations_b": len(struct_b.relations()),
        })

    def _evolve_knowledge(self, args: Dict[str, Any]) -> str:
        json_data = args.get("json_data", "")
        operations = json.loads(args.get("operations", "[]"))
        structure = json.loads(json_data) if isinstance(json_data, str) else json_data
        from cks.serialization import parse as cks_parse
        struct = cks_parse(structure)
        evolved = self._runtime.core_adapter.evolve(struct, operations)
        from cks.serialization import serialize as cks_serialize
        return cks_serialize(evolved)

    def _derive_knowledge(self, args: Dict[str, Any]) -> str:
        json_data = args.get("json_data", "")
        premises = json.loads(args.get("premises", "[]"))
        rule = args.get("rule", "")
        conclusion_id = args.get("conclusion_id", "")
        conclusion_type = args.get("conclusion_type", "")
        conclusion_name = args.get("conclusion_name", "")
        structure = json.loads(json_data) if isinstance(json_data, str) else json_data
        from cks.serialization import parse as cks_parse
        struct = cks_parse(structure)
        # Derivation logic: create a new Knowledge Object
        from cks.core import KnowledgeObject, ObjectIdentity
        for pid in premises:
            if struct.get(pid) is None:
                raise ValueError(f"Premise '{pid}' not found in structure")
        if struct.get(conclusion_id) is not None:
            raise ValueError(f"Conclusion '{conclusion_id}' already exists")
        new_obj = KnowledgeObject(
            identity=ObjectIdentity(id=conclusion_id, type=conclusion_type, name=conclusion_name),
            structure={"derived_from": premises, "rule": rule},
        )
        objects = list(struct.objects) + [new_obj]
        from cks.core import KnowledgeStructure
        new_struct = KnowledgeStructure(objects)
        from cks.serialization import serialize as cks_serialize
        return cks_serialize(new_struct)