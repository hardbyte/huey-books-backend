"""
Extended node processors for advanced chatbot functionality.

This module provides specialized processors for complex node types including
condition logic, action execution, webhook calls, and composite node handling.
"""

import json
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models.cms import ConnectionType, ConversationSession, FlowNode, NodeType
from app.services.cel_evaluator import evaluate_cel_expression
from app.services.circuit_breaker import get_circuit_breaker
from app.services.node_input_validation import validate_node_input
from app.services.variable_resolver import VariableResolver

logger = get_logger()


class ConditionNodeProcessor:
    """
    Processes condition nodes that branch conversation flow based on session state.

    Evaluates conditional logic against session variables and determines
    the next node to execute in the conversation flow.
    """

    def __init__(self, runtime):
        self.runtime = runtime
        self.logger = logger

    async def process(
        self,
        db: AsyncSession,
        node: FlowNode,
        session: ConversationSession,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process a condition node by evaluating conditions against session state.

        Enhanced with rigorous input validation to prevent runtime errors.

        Args:
            db: Database session
            node: FlowNode with condition configuration
            session: Current conversation session
            context: Additional context data

        Returns:
            Dict with condition evaluation result and next node
        """
        try:
            node_content = node.content or {}

            # Validate condition node content before processing
            validation_report = validate_node_input(
                node_id=node.node_id,
                node_type=NodeType.CONDITION,
                node_content=node_content,
            )

            if not validation_report.is_valid:
                error_messages = [r.message for r in validation_report.errors]
                logger.error(
                    "Condition node validation failed",
                    node_id=node.node_id,
                    session_id=session.id,
                    errors=error_messages,
                )
                return {
                    "type": "condition",
                    "condition_result": False,
                    "validation_errors": error_messages,
                    "error": f"Node validation failed: {'; '.join(error_messages)}",
                    "node_id": node.node_id,
                }

            # Log validation warnings but continue processing
            for warning in validation_report.warnings:
                logger.warning(
                    "Condition node validation warning",
                    node_id=node.node_id,
                    session_id=session.id,
                    warning=warning.message,
                )

            conditions = node_content.get("conditions", [])
            default_path = node_content.get("default_path")

            # Evaluate each condition in order
            for condition in conditions:
                if await self._evaluate_condition(condition.get("if"), session.state):
                    target_path = condition.get("then")
                    logger.info(
                        "Condition matched, transitioning to path",
                        session_id=session.id,
                        target_path=target_path,
                        condition=condition.get("if"),
                    )

                    # Map condition result to connection type
                    connection_type = self._map_path_to_connection(target_path)
                    next_connection = await self._get_next_connection(
                        db, node, connection_type
                    )

                    next_node = None
                    if next_connection:
                        from app.repositories.chat_repository import chat_repo

                        next_node = await chat_repo.get_flow_node(
                            db,
                            flow_id=node.flow_id,
                            node_id=next_connection.target_node_id,
                        )

                    # If we have a next node, process it automatically
                    if next_node:
                        return await self.runtime.process_node(db, next_node, session)

                    return {
                        "type": "condition",
                        "condition_result": True,
                        "matched_condition": condition.get("if"),
                        "target_path": target_path,
                        "next_node": next_node,
                        "node_id": node.node_id,
                    }

            # No conditions matched, use default path
            logger.info(
                "No conditions matched, using default path",
                session_id=session.id,
                default_path=default_path,
            )

            connection_type = self._map_path_to_connection(default_path)
            next_connection = await self._get_next_connection(db, node, connection_type)

            next_node = None
            if next_connection:
                from app.repositories.chat_repository import chat_repo

                next_node = await chat_repo.get_flow_node(
                    db, flow_id=node.flow_id, node_id=next_connection.target_node_id
                )

            # If we have a next node, process it automatically
            if next_node:
                return await self.runtime.process_node(db, next_node, session)

            return {
                "type": "condition",
                "condition_result": False,
                "used_default": True,
                "default_path": default_path,
                "next_node": next_node,
                "node_id": node.node_id,
            }

        except Exception as e:
            logger.error(
                "Error processing condition node",
                session_id=session.id,
                error=str(e),
                exc_info=True,
            )
            return {"type": "error", "error": "Failed to evaluate conditions"}

    def _map_path_to_connection(self, path: str):
        """Map condition path to connection type."""
        from app.models.cms import ConnectionType

        if path == "$0":
            return ConnectionType.OPTION_0
        elif path == "$1":
            return ConnectionType.OPTION_1
        else:
            return ConnectionType.DEFAULT

    async def _get_next_connection(
        self,
        db: AsyncSession,
        node: FlowNode,
        connection_type=None,
    ):
        """Get the next connection from current node."""
        from app.models.cms import ConnectionType as CT
        from app.repositories.chat_repository import chat_repo

        if connection_type is None:
            connection_type = CT.DEFAULT

        connections = await chat_repo.get_node_connections(
            db, flow_id=node.flow_id, source_node_id=node.node_id
        )

        # Try to find specific connection type
        for conn in connections:
            if conn.connection_type == connection_type:
                return conn

        # Fall back to default if not found
        if connection_type != CT.DEFAULT:
            for conn in connections:
                if conn.connection_type == CT.DEFAULT:
                    return conn

        return None

    async def _evaluate_condition(
        self, condition: Dict[str, Any] | str, session_state: Dict[str, Any]
    ) -> bool:
        """
        Evaluate a single condition against session state.

        Supports both CEL expressions (strings) and JSON-based conditions (dicts).

        CEL Examples (recommended):
        - "user.age >= 18"
        - "user.age >= 18 && user.status == 'active'"
        - "size(user.preferences) > 0"
        - "user.role in ['admin', 'moderator']"
        - "has(user.email) && user.email.endsWith('@company.com')"

        JSON Examples (legacy, maintained for backward compatibility):
        - {"var": "user.age", "gte": 18}
        - {"and": [{"var": "user.age", "gte": 18}, {"var": "user.status", "eq": "active"}]}
        - {"or": [{"var": "user.role", "eq": "admin"}, {"var": "user.role", "eq": "moderator"}]}
        """
        if not condition:
            return False

        # Handle CEL expressions (string conditions)
        if isinstance(condition, str):
            try:
                result = evaluate_cel_expression(condition, session_state)
                logger.debug(
                    "CEL condition evaluated",
                    expression=condition,
                    result=result,
                    session_state_keys=list(session_state.keys()),
                )
                return bool(result)
            except Exception as e:
                logger.error(
                    "CEL condition evaluation failed, defaulting to False",
                    expression=condition,
                    error=str(e),
                )
                return False

        # Handle JSON-based conditions (legacy format)
        if not isinstance(condition, dict):
            logger.warning(
                "Invalid condition type, expected dict or str",
                condition_type=type(condition),
            )
            return False

        # Handle logical operators
        if "and" in condition:
            conditions = condition["and"]
            results = [
                await self._evaluate_condition(c, session_state) for c in conditions
            ]
            return all(results)

        if "or" in condition:
            conditions = condition["or"]
            results = [
                await self._evaluate_condition(c, session_state) for c in conditions
            ]
            return any(results)

        if "not" in condition:
            return not await self._evaluate_condition(condition["not"], session_state)

        # Handle variable comparisons
        if "var" in condition:
            var_path = condition["var"]
            var_value = self._get_nested_value(session_state, var_path)

            # Comparison operators
            if "eq" in condition:
                return var_value == condition["eq"]
            if "ne" in condition:
                return var_value != condition["ne"]
            if "gt" in condition:
                return var_value > condition["gt"]
            if "gte" in condition:
                return var_value >= condition["gte"]
            if "lt" in condition:
                return var_value < condition["lt"]
            if "lte" in condition:
                return var_value <= condition["lte"]
            if "in" in condition:
                return var_value in condition["in"]
            if "contains" in condition:
                return condition["contains"] in var_value if var_value else False
            if "exists" in condition:
                return var_value is not None

        return False

    def _get_nested_value(self, data: Dict[str, Any], path: str) -> Any:
        """Get nested value from dictionary using dot notation."""
        try:
            keys = path.split(".")
            value = data
            for key in keys:
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    return None
            return value
        except (KeyError, TypeError, AttributeError):
            return None


class WebhookNodeProcessor:
    """
    Processes webhook nodes that call external HTTP APIs.

    Features circuit breaker pattern, retry logic, secret injection,
    and response mapping for robust external integrations.
    """

    def __init__(self, runtime):
        self.runtime = runtime
        self.variable_resolver = VariableResolver()
        self.logger = logger

    async def process(
        self,
        db: AsyncSession,
        node: FlowNode,
        session: ConversationSession,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process a webhook node by making HTTP API calls.

        Args:
            db: Database session
            node: FlowNode with webhook configuration
            session: Current conversation session
            context: Additional context data

        Returns:
            Dict with webhook result and next node info
        """
        node_content = node.content or {}

        try:
            webhook_url = node_content.get("url")
            if not webhook_url:
                raise ValueError("Webhook node requires 'url' field")

            # Set up variable resolver with current session state
            from app.services.variable_resolver import create_session_resolver

            resolver = create_session_resolver(session.state)

            # Resolve webhook configuration
            resolved_url = resolver.substitute_variables(webhook_url)
            method = node_content.get("method", "POST")
            headers = self._resolve_headers(node_content.get("headers", {}), resolver)
            body = self._resolve_body(node_content.get("body", {}), resolver)
            timeout = node_content.get("timeout", 30)

            # Get circuit breaker for this webhook
            circuit_breaker = get_circuit_breaker(f"webhook_{resolved_url}")

            # Execute webhook call with circuit breaker protection
            response_data = await circuit_breaker.call(
                self._make_webhook_request, resolved_url, method, headers, body, timeout
            )

            # Process response mapping
            mapped_data = self._map_response(
                response_data, node_content.get("response_mapping", {})
            )

            # Update session state with mapped data
            if mapped_data:
                session.state.update(mapped_data)

            self.logger.info(
                "Webhook call completed successfully",
                session_id=session.id,
                webhook_url=resolved_url,
                status_code=response_data.get("status_code"),
            )

            return {
                "type": "webhook",
                "success": True,
                "webhook_response": response_data,
                "mapped_data": mapped_data,
                "url": resolved_url,
            }

        except Exception as e:
            self.logger.error(
                "Webhook call failed",
                session_id=session.id,
                node_id=node.node_id,
                error=str(e),
                exc_info=True,
            )

            # Return fallback response if available
            fallback = node_content.get("fallback_response", {})
            if fallback:
                session.state.update(fallback)
                return {
                    "type": "webhook",
                    "success": False,
                    "fallback_used": True,
                    "error": str(e),
                }

            return {
                "type": "webhook",
                "success": False,
                "error": str(e),
            }

    def _resolve_headers(
        self, headers: Dict[str, str], resolver: VariableResolver
    ) -> Dict[str, str]:
        """Resolve variable references in headers."""
        resolved = {}
        for key, value in headers.items():
            if isinstance(value, str):
                resolved[key] = resolver.substitute_variables(value)
            else:
                resolved[key] = value
        return resolved

    def _resolve_body(
        self, body: Dict[str, Any], resolver: VariableResolver
    ) -> Dict[str, Any]:
        """Resolve variable references in request body."""
        if isinstance(body, dict):
            return resolver.substitute_object(body)
        return body

    async def _make_webhook_request(
        self, url: str, method: str, headers: Dict[str, str], body: Any, timeout: int
    ) -> Dict[str, Any]:
        """Make the actual HTTP request (placeholder implementation)."""
        # This would use httpx or similar to make the actual HTTP request
        # For now, return a mock response

        return {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": {"success": True, "data": "mock_response"},
            "url": url,
            "method": method,
        }

    def _map_response(
        self, response_data: Dict[str, Any], mapping: Dict[str, str]
    ) -> Dict[str, Any]:
        """Map response data to session variables using JSONPath-like syntax."""
        if not mapping or not response_data:
            return {}

        mapped = {}
        response_body = response_data.get("body", {})

        for target_var, source_path in mapping.items():
            try:
                # Simple dot notation mapping (could be enhanced with JSONPath)
                if source_path.startswith("$."):
                    source_path = source_path[2:]  # Remove $. prefix

                value = self._get_nested_value(response_body, source_path)
                if value is not None:
                    mapped[target_var] = value

            except Exception as e:
                logger.warning(
                    "Failed to map response field",
                    target_var=target_var,
                    source_path=source_path,
                    error=str(e),
                )

        return mapped

    def _get_nested_value(self, data: Dict[str, Any], path: str) -> Any:
        """Get nested value from dictionary using dot notation."""
        try:
            keys = path.split(".")
            value = data
            for key in keys:
                if isinstance(value, dict):
                    value = value.get(key)
                elif isinstance(value, list) and key.isdigit():
                    value = value[int(key)]
                else:
                    return None
            return value
        except (KeyError, TypeError, AttributeError, IndexError, ValueError):
            return None


class CompositeNodeProcessor:
    """
    Processes composite nodes that encapsulate complex multi-step operations.

    Supports two modes:
    1. Sub-flow invocation: When composite_flow_id is present, invokes another flow
    2. Child nodes execution: Executes inline child nodes sequentially

    Provides explicit input/output mapping, variable scoping, and sequential
    execution of child nodes with proper isolation.
    """

    def __init__(self, runtime):
        """Initialize with runtime reference (matches NodeProcessor interface)."""
        self.runtime = runtime
        self.variable_resolver = VariableResolver()
        self.logger = logger

    async def process(
        self,
        db: AsyncSession,
        node: FlowNode,
        session: ConversationSession,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process a composite node following the standard NodeProcessor interface.

        Args:
            db: Database session
            node: The composite FlowNode to process
            session: Current conversation session
            context: Execution context

        Returns:
            Dict with processing results
        """
        node_content = node.content or {}

        # Check if this is a sub-flow invocation
        composite_flow_id = node_content.get("composite_flow_id")
        if composite_flow_id:
            return await self._process_subflow(
                db, node, session, composite_flow_id, node_content
            )

        # Otherwise, process inline child nodes
        return await self._process_inline_children(db, node, session, node_content)

    async def _process_subflow(
        self,
        db: AsyncSession,
        node: FlowNode,
        session: ConversationSession,
        composite_flow_id: str,
        node_content: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Process a composite node that invokes a sub-flow."""
        from uuid import UUID

        from app import crud
        from app.repositories.chat_repository import chat_repo

        try:
            # Get the sub-flow
            sub_flow_id = UUID(composite_flow_id)
            sub_flow = await crud.flow.aget(db, sub_flow_id)

            policy = (session.info or {}).get("library_chat")
            feature = (
                {
                    "huey-jokes": "jokes_enabled",
                    "huey-spelling": "spelling_enabled",
                }.get((sub_flow.info or {}).get("seed_key"))
                if sub_flow
                else None
            )
            if policy and feature and not policy[feature]:
                connections = await chat_repo.get_node_connections(
                    db, flow_id=node.flow_id, source_node_id=node.node_id
                )
                connection = next(
                    (
                        item
                        for item in connections
                        if item.connection_type == ConnectionType.DEFAULT
                    ),
                    None,
                )
                next_node = (
                    await chat_repo.get_flow_node(
                        db, flow_id=node.flow_id, node_id=connection.target_node_id
                    )
                    if connection
                    else None
                )
                return {
                    "type": "action",
                    "next_node": next_node,
                    "session_ended": next_node is None,
                }

            if not sub_flow:
                self.logger.error(
                    "Sub-flow not found",
                    composite_flow_id=composite_flow_id,
                    node_id=node.node_id,
                )
                return {
                    "type": "error",
                    "error": f"Sub-flow not found: {composite_flow_id}",
                }

            # Get entry node of sub-flow
            entry_node = await chat_repo.get_flow_node(
                db, flow_id=sub_flow_id, node_id=sub_flow.entry_node_id
            )

            if not entry_node:
                return {
                    "type": "error",
                    "error": f"Sub-flow entry node not found: {sub_flow.entry_node_id}",
                }

            # Get the composite node's outgoing connection (return node after sub-flow completes)
            connections = await chat_repo.get_node_connections(
                db, flow_id=node.flow_id, source_node_id=node.node_id
            )
            return_node_id = None
            for conn in connections:
                if conn.connection_type == ConnectionType.DEFAULT:
                    return_node_id = conn.target_node_id
                    break

            # Push parent flow context to session.info flow_stack for return after sub-flow
            flow_stack = list(session.info.get("flow_stack", []))
            flow_stack.append(
                {
                    "parent_flow_id": str(node.flow_id),
                    "return_node_id": return_node_id,
                    "composite_node_id": node.node_id,
                }
            )

            # Update session with sub-flow context
            session = await chat_repo.update_session_state(
                db,
                session_id=session.id,
                state_updates={},
                current_flow_id=sub_flow_id,
            )
            # Update info separately (using SQLAlchemy's mutable tracking)
            session.info["flow_stack"] = flow_stack
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(session, "info")
            await db.commit()
            await db.refresh(session)

            self.logger.info(
                "Invoking sub-flow",
                parent_flow_id=str(node.flow_id),
                sub_flow_id=composite_flow_id,
                sub_flow_name=sub_flow.name,
                entry_node_id=entry_node.node_id,
                return_node_id=return_node_id,
                flow_stack_depth=len(flow_stack),
            )

            # Process the sub-flow's entry node and continue through message nodes
            # until we hit a question node or end of flow
            all_messages = []
            current_node = entry_node
            final_result = None

            while current_node:
                result = await self.runtime.process_node(db, current_node, session)
                final_result = result

                # Collect messages from message nodes
                if result.get("type") == "messages":
                    messages = result.get("messages", [])
                    all_messages.extend(messages)

                    # Pause processing if the message requests acknowledgment
                    if result.get("wait_for_acknowledgment"):
                        final_result = {
                            "type": "messages",
                            "messages": all_messages,
                            "wait_for_acknowledgment": True,
                            "node_id": result.get("node_id"),
                            "next_node": result.get("next_node"),
                        }
                        break

                    # Check if there's a next node to process
                    next_node = result.get("next_node")
                    if next_node:
                        # If next node is a question, include it and stop
                        if next_node.node_type == NodeType.QUESTION:
                            question_result = await self.runtime.process_node(
                                db, next_node, session
                            )
                            final_result = {
                                "type": "messages",
                                "messages": all_messages,
                                "next_node": question_result,
                            }
                            break
                        # If next node is also a message, continue processing
                        elif next_node.node_type == NodeType.MESSAGE:
                            current_node = next_node
                            continue
                        else:
                            # For other node types (condition, action, etc.),
                            # continue processing them automatically
                            current_node = next_node
                            continue
                    else:
                        # No next node, we're done
                        break
                elif result.get("type") == "question":
                    # Hit a question node, include collected messages and stop
                    if all_messages:
                        final_result = {
                            "type": "messages",
                            "messages": all_messages,
                            "next_node": result,
                        }
                    break
                elif result.get("type") == "condition":
                    # Process condition and follow the path
                    next_node = result.get("next_node")
                    if next_node:
                        current_node = next_node
                        continue
                    else:
                        break
                elif result.get("type") == "action":
                    # Process action and continue
                    next_node = result.get("next_node")
                    if next_node:
                        current_node = next_node
                        continue
                    else:
                        break
                else:
                    # Unknown type or end, stop processing
                    break

            # Build final result with all collected messages
            if all_messages and final_result:
                if final_result.get("type") != "messages":
                    final_result = {
                        "type": "messages",
                        "messages": all_messages,
                        "next_node": final_result
                        if final_result.get("type") == "question"
                        else None,
                    }
                else:
                    final_result["messages"] = all_messages

            # Add composite node metadata to result
            if final_result:
                final_result["composite_name"] = node_content.get(
                    "composite_name", sub_flow.name
                )
                final_result["sub_flow_id"] = composite_flow_id

            return final_result or {
                "type": "error",
                "error": "Sub-flow produced no result",
            }

        except Exception as e:
            self.logger.error(
                "Error processing sub-flow composite node",
                node_id=node.node_id,
                composite_flow_id=composite_flow_id,
                error=str(e),
            )
            return {
                "type": "error",
                "error": f"Sub-flow invocation failed: {str(e)}",
            }

    async def _process_inline_children(
        self,
        db: AsyncSession,
        node: FlowNode,
        session: ConversationSession,
        node_content: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Process a composite node with inline child nodes."""
        # This is the original behavior for composite nodes with inline children
        return await self._process_legacy(session, node_content)

    async def _process_legacy(
        self,
        session: ConversationSession,
        node_content: Dict[str, Any],
        user_input: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a composite node by executing child nodes in sequence.

        Args:
            session: Current conversation session
            node_content: Node configuration with inputs, outputs, and child nodes
            user_input: User input (not used for composite nodes)

        Returns:
            Dict with processing results
        """
        try:
            # Extract composite configuration
            inputs = node_content.get("inputs", {})
            outputs = node_content.get("outputs", {})
            child_nodes = node_content.get("nodes", [])

            if not child_nodes:
                logger.warning(
                    "Composite node has no child nodes", session_id=session.id
                )
                return {
                    "type": "composite",
                    "status": "complete",
                    "warning": "No child nodes to execute",
                }

            # Create isolated scope for composite execution
            composite_scope = await self._create_composite_scope(session, inputs)

            # Execute child nodes in sequence
            execution_results = []
            for i, child_node in enumerate(child_nodes):
                try:
                    result = await self._execute_child_node(
                        child_node, composite_scope, session, i
                    )
                    execution_results.append(result)

                    # Update composite scope with results
                    if result.get("state_updates"):
                        for key, value in result["state_updates"].items():
                            if key.startswith("_composite_scope_"):
                                # Handle composite scope structured updates
                                # Format: "_composite_scope_output_processed_name" -> scope="output", key="processed_name"
                                # Remove the prefix "_composite_scope_" and split by first underscore
                                remainder = key[
                                    len("_composite_scope_") :
                                ]  # "output_processed_name"
                                parts = remainder.split(
                                    "_", 1
                                )  # ["output", "processed_name"]
                                if len(parts) == 2:
                                    scope, key_part = parts
                                    if scope in composite_scope:
                                        composite_scope[scope][key_part] = value
                            else:
                                # Handle regular state updates by setting them using dot notation
                                self._set_nested_value(composite_scope, key, value)

                except Exception as child_error:
                    logger.error(
                        "Child node execution failed in composite",
                        session_id=session.id,
                        child_index=i,
                        error=str(child_error),
                        exc_info=True,
                    )
                    return {
                        "type": "error",
                        "error": f"Child node {i} failed: {str(child_error)}",
                        "execution_results": execution_results,
                    }

            # Map outputs back to session state
            output_mapping = await self._map_outputs(composite_scope, outputs, session)

            logger.info(
                "Composite node execution completed",
                session_id=session.id,
                child_nodes_executed=len(child_nodes),
                outputs_mapped=len(output_mapping),
            )

            return {
                "type": "composite",
                "status": "complete",
                "execution_results": execution_results,
                "output_mapping": output_mapping,
                "child_nodes_executed": len(child_nodes),
            }

        except Exception as e:
            logger.error(
                "Error processing composite node",
                session_id=session.id,
                error=str(e),
                exc_info=True,
            )
            return {"type": "error", "error": "Failed to process composite node"}

    async def _create_composite_scope(
        self, session: ConversationSession, inputs: Dict[str, str]
    ) -> Dict[str, Any]:
        """Create isolated variable scope for composite node execution."""
        composite_scope = {"input": {}, "output": {}, "local": {}, "temp": {}}

        # Set up variable resolver with session state
        from app.services.variable_resolver import create_session_resolver

        resolver = create_session_resolver(session.state)

        # Map inputs to composite scope
        for input_name, input_source in inputs.items():
            try:
                # Check if input_source is a direct reference to a session state key (e.g., "user", "context")
                # without the dot notation, which means we want the entire object
                if "." not in input_source and input_source in session.state:
                    resolved_value = session.state[input_source]
                else:
                    # Use variable resolution for dot notation paths (e.g., "user.name")
                    resolved_value = resolver.substitute_variables(
                        f"{{{{{input_source}}}}}"
                    )
                    # Try to parse as JSON if it's a string that looks like structured data
                    if isinstance(resolved_value, str):
                        try:
                            if resolved_value.startswith(("{", "[")):
                                resolved_value = json.loads(resolved_value)
                        except json.JSONDecodeError:
                            pass  # Keep as string

                composite_scope["input"][input_name] = resolved_value

            except Exception as e:
                logger.warning(
                    "Failed to resolve composite input",
                    input_name=input_name,
                    input_source=input_source,
                    error=str(e),
                )
                composite_scope["input"][input_name] = None

        return composite_scope

    async def _execute_child_node(
        self,
        child_node: Dict[str, Any],
        composite_scope: Dict[str, Any],
        session: ConversationSession,
        node_index: int,
    ) -> Dict[str, Any]:
        """Execute a single child node within the composite scope."""
        node_type = child_node.get("type")
        node_content = child_node.get("content", {})

        # Create temporary variable resolver with composite scope
        from app.services.variable_resolver import create_session_resolver

        temp_resolver = create_session_resolver(session.state, composite_scope)

        # Process the child node based on its type
        if node_type == "action":
            # Execute inline actions directly
            return await self._execute_inline_actions(
                node_content, session, composite_scope, temp_resolver
            )

        elif node_type == "condition":
            # Evaluate inline condition using CEL
            # For inline child nodes, we don't need the full ConditionNodeProcessor
            conditions = node_content.get("conditions", [])
            default_path = node_content.get("default_path", "default")

            for condition in conditions:
                condition_expr = condition.get("if")
                if condition_expr:
                    try:
                        # Evaluate CEL expression against current composite scope
                        merged_context = {**session.state, **composite_scope}
                        result_value = evaluate_cel_expression(
                            condition_expr, merged_context
                        )
                        if result_value:
                            return {
                                "type": "condition",
                                "condition_result": True,
                                "matched_condition": condition_expr,
                                "target_path": condition.get("then"),
                            }
                    except Exception as e:
                        logger.warning(
                            "Inline condition evaluation failed",
                            condition=condition_expr,
                            error=str(e),
                        )

            # No conditions matched, return default
            return {
                "type": "condition",
                "condition_result": False,
                "used_default": True,
                "default_path": default_path,
            }

        else:
            logger.warning(
                "Unsupported child node type in composite",
                node_type=node_type,
                node_index=node_index,
            )
            return {"warning": f"Unsupported child node type: {node_type}"}

    async def _execute_inline_actions(
        self,
        node_content: Dict[str, Any],
        session: ConversationSession,
        composite_scope: Dict[str, Any],
        resolver: VariableResolver,
    ) -> Dict[str, Any]:
        """Execute inline action nodes within a composite scope.

        Supports set_variable and aggregate action types for inline child nodes.
        """
        actions = node_content.get("actions", [])
        action_results = []
        state_updates = {}

        for i, action in enumerate(actions):
            action_type = action.get("type")

            try:
                if action_type == "set_variable":
                    result = self._execute_set_variable(
                        action, session, composite_scope, resolver
                    )
                    action_results.append(result)
                    if result.get("state_updates"):
                        state_updates.update(result["state_updates"])

                elif action_type == "aggregate":
                    result = self._execute_aggregate(action, session, composite_scope)
                    action_results.append(result)
                    if result.get("state_updates"):
                        state_updates.update(result["state_updates"])

                else:
                    raise ValueError(f"Unsupported inline action type: {action_type}")

            except Exception as e:
                logger.error(
                    "Inline action execution failed",
                    action_index=i,
                    action_type=action_type,
                    error=str(e),
                )
                raise

        return {
            "type": "action",
            "actions_completed": len(actions),
            "action_results": action_results,
            "state_updates": state_updates,
        }

    def _execute_set_variable(
        self,
        action: Dict[str, Any],
        session: ConversationSession,
        composite_scope: Dict[str, Any],
        resolver: VariableResolver,
    ) -> Dict[str, Any]:
        """Execute a set_variable action within composite scope."""
        variable = action.get("variable")
        value = action.get("value")

        if not variable:
            raise ValueError("set_variable action requires 'variable' field")

        # Resolve value if it contains variable references
        if isinstance(value, str):
            resolved_value = resolver.substitute_variables(value)
            try:
                if resolved_value.startswith(("{", "[")):
                    resolved_value = json.loads(resolved_value)
            except json.JSONDecodeError:
                pass
        elif isinstance(value, (dict, list)):
            resolved_value = resolver.substitute_object(value)
        else:
            resolved_value = value

        # Determine target scope for the variable
        state_updates = {}
        if variable.startswith("output."):
            # Set in composite output scope
            key = variable[7:]  # Remove "output." prefix
            composite_scope.setdefault("output", {})[key] = resolved_value
            state_updates[variable] = resolved_value
        elif variable.startswith("local."):
            # Set in composite local scope
            key = variable[6:]  # Remove "local." prefix
            composite_scope.setdefault("local", {})[key] = resolved_value
            state_updates[variable] = resolved_value
        elif variable.startswith("temp."):
            # Set in session temp scope
            self._set_nested_value(session.state, variable, resolved_value)
            state_updates[variable] = resolved_value
        else:
            # Set directly in session state
            self._set_nested_value(session.state, variable, resolved_value)
            state_updates[variable] = resolved_value

        return {
            "type": "set_variable",
            "variable": variable,
            "value": resolved_value,
            "state_updates": state_updates,
        }

    def _execute_aggregate(
        self,
        action: Dict[str, Any],
        session: ConversationSession,
        composite_scope: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute an aggregate action using CEL expressions."""
        expression = action.get("expression")
        target = action.get("target")

        if not target:
            raise ValueError("Aggregate action requires 'target' field")

        # Merge session state with composite scope for evaluation
        merged_context = {**session.state, **composite_scope}

        if expression:
            try:
                result = evaluate_cel_expression(expression, merged_context)

                state_updates = {}
                if result is not None:
                    # Determine target scope
                    if target.startswith("output."):
                        key = target[7:]
                        composite_scope.setdefault("output", {})[key] = result
                    else:
                        self._set_nested_value(session.state, target, result)
                    state_updates[target] = result

                return {
                    "type": "aggregate",
                    "expression": expression,
                    "target": target,
                    "result": result,
                    "state_updates": state_updates,
                }

            except Exception as e:
                logger.error(
                    "CEL aggregate expression failed",
                    expression=expression,
                    error=str(e),
                )
                return {
                    "type": "aggregate",
                    "expression": expression,
                    "error": f"CEL evaluation failed: {str(e)}",
                    "state_updates": {},
                }

        # Legacy format support
        source = action.get("source")
        if not source:
            raise ValueError(
                "Aggregate action requires either 'expression' or 'source' field"
            )

        field = action.get("field")
        operation = action.get("operation", "sum")
        merge_strategy = action.get("merge_strategy", "sum")

        cel_expression = self._build_cel_expression(
            source, field, operation, merge_strategy
        )

        try:
            result = evaluate_cel_expression(cel_expression, merged_context)

            state_updates = {}
            if result is not None:
                if target.startswith("output."):
                    key = target[7:]
                    composite_scope.setdefault("output", {})[key] = result
                else:
                    self._set_nested_value(session.state, target, result)
                state_updates[target] = result

            return {
                "type": "aggregate",
                "operation": operation,
                "source": source,
                "target": target,
                "result": result,
                "state_updates": state_updates,
            }

        except Exception as e:
            logger.error(
                "CEL aggregate expression failed",
                expression=cel_expression,
                error=str(e),
            )
            return {
                "type": "aggregate",
                "operation": operation,
                "error": f"CEL evaluation failed: {str(e)}",
                "state_updates": {},
            }

    def _build_cel_expression(
        self, source: str, field: Optional[str], operation: str, merge_strategy: str
    ) -> str:
        """Build a CEL expression from legacy aggregate action config."""
        if field:
            data_expr = f"{source}.map(x, x.{field})"
        else:
            data_expr = source

        if operation == "sum":
            return f"sum({data_expr})"
        elif operation == "avg":
            return f"avg({data_expr})"
        elif operation == "max":
            return f"max({data_expr})"
        elif operation == "min":
            return f"min({data_expr})"
        elif operation == "count":
            return f"size({data_expr})"
        elif operation == "merge":
            if merge_strategy == "max":
                return f"merge_max({data_expr})"
            elif merge_strategy == "last":
                return f"merge_last({data_expr})"
            else:
                return f"merge({data_expr})"
        elif operation == "collect":
            return f"flatten({data_expr})"
        else:
            raise ValueError(f"Unknown aggregate operation: {operation}")

    async def _map_outputs(
        self,
        composite_scope: Dict[str, Any],
        outputs: Dict[str, str],
        session: ConversationSession,
    ) -> Dict[str, Any]:
        """Map composite outputs back to session state."""
        output_mapping = {}

        for output_name, target_path in outputs.items():
            try:
                # Get value from composite scope output
                output_value = composite_scope.get("output", {}).get(output_name)

                if output_value is not None:
                    # Set the value in session state
                    self._set_nested_value(session.state, target_path, output_value)
                    output_mapping[target_path] = output_value

            except Exception as e:
                logger.warning(
                    "Failed to map composite output",
                    output_name=output_name,
                    target_path=target_path,
                    error=str(e),
                )

        return output_mapping

    def _set_nested_value(self, data: Dict[str, Any], path: str, value: Any) -> None:
        """Set nested value in dictionary using dot notation."""
        keys = path.split(".")
        current = data

        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # Set the final value
        current[keys[-1]] = value

    def _get_nested_value(self, data: Dict[str, Any], path: str) -> Any:
        """Get nested value from dictionary using dot notation."""
        try:
            keys = path.split(".")
            value = data
            for key in keys:
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    return None
            return value
        except (KeyError, TypeError, AttributeError):
            return None


class ScriptNodeProcessor:
    """
    Processes SCRIPT nodes for frontend JavaScript/TypeScript execution.

    SCRIPT nodes are designed for client-side execution in the chat widget.
    The backend validates the script configuration but does NOT execute the code.
    Actual execution happens in the browser sandbox.
    """

    def __init__(self, runtime):
        self.runtime = runtime
        self.logger = logger

    async def process(
        self,
        db: AsyncSession,
        node: FlowNode,
        session: ConversationSession,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process a script node by validating configuration and preparing for frontend execution.

        This is a PLACEHOLDER implementation. The backend validates the script configuration
        but does not execute the code. Actual script execution will happen in the chat widget.

        Args:
            db: Database session
            node: FlowNode with script configuration
            session: Current conversation session
            context: Additional context data

        Returns:
            Dict with script metadata and execution instructions for frontend
        """
        try:
            node_content = node.content or {}

            # Validate script node content before processing
            validation_report = validate_node_input(
                node_id=node.node_id,
                node_type=NodeType.SCRIPT,
                node_content=node_content,
            )

            if not validation_report.is_valid:
                error_messages = [r.message for r in validation_report.errors]
                logger.error(
                    "Script node validation failed",
                    node_id=node.node_id,
                    session_id=session.id,
                    errors=error_messages,
                )
                return {
                    "type": "script",
                    "error": f"Node validation failed: {'; '.join(error_messages)}",
                    "validation_errors": error_messages,
                    "node_id": node.node_id,
                }

            # Log validation warnings but continue processing
            for warning in validation_report.warnings:
                logger.warning(
                    "Script node validation warning",
                    node_id=node.node_id,
                    session_id=session.id,
                    warning=warning.message,
                )

            # Extract script configuration
            code = node_content.get("code", "")
            language = node_content.get("language", "javascript")
            sandbox = node_content.get("sandbox", "strict")
            inputs = node_content.get("inputs", {})
            outputs = node_content.get("outputs", [])
            dependencies = node_content.get("dependencies", [])
            timeout = node_content.get("timeout", 5000)
            description = node_content.get("description", "")

            # Resolve input values from session state
            resolved_inputs = {}
            if inputs:
                from app.services.variable_resolver import create_session_resolver

                resolver = create_session_resolver(session.state)

                for input_name, input_path in inputs.items():
                    try:
                        # Check if input_path is a direct reference without dot notation
                        if "." not in input_path and input_path in session.state:
                            resolved_value = session.state[input_path]
                        else:
                            resolved_value = resolver.substitute_variables(
                                f"{{{{{input_path}}}}}"
                            )
                            # Try to parse as JSON if it's a string
                            if isinstance(resolved_value, str):
                                try:
                                    if resolved_value.startswith(("{", "[")):
                                        resolved_value = json.loads(resolved_value)
                                except json.JSONDecodeError:
                                    pass

                        resolved_inputs[input_name] = resolved_value

                    except Exception as e:
                        logger.warning(
                            "Failed to resolve script input",
                            node_id=node.node_id,
                            session_id=session.id,
                            input_name=input_name,
                            input_path=input_path,
                            error=str(e),
                        )
                        resolved_inputs[input_name] = None

            # Log that this is a placeholder
            logger.info(
                "Script node processed - execution will occur on frontend",
                node_id=node.node_id,
                session_id=session.id,
                language=language,
                sandbox=sandbox,
                timeout=timeout,
                has_dependencies=bool(dependencies),
                input_count=len(resolved_inputs),
                output_count=len(outputs),
            )

            logger.warning(
                "PLACEHOLDER: Script execution not implemented in backend - "
                "this will be handled by the chat widget frontend",
                node_id=node.node_id,
                session_id=session.id,
            )

            # Get next node
            next_connection = await self._get_next_connection(db, node)
            next_node = None

            if next_connection:
                from app.repositories.chat_repository import chat_repo

                next_node = await chat_repo.get_flow_node(
                    db, flow_id=node.flow_id, node_id=next_connection.target_node_id
                )

            # Return script configuration for frontend execution
            return {
                "type": "script",
                "execution_context": "frontend",
                "script_config": {
                    "code": code,
                    "language": language,
                    "sandbox": sandbox,
                    "inputs": resolved_inputs,
                    "outputs": outputs,
                    "dependencies": dependencies,
                    "timeout": timeout,
                    "description": description,
                },
                "node_id": node.node_id,
                "next_node": next_node,
                "placeholder_note": "Script execution will occur in the chat widget frontend",
            }

        except Exception as e:
            logger.error(
                "Error processing script node",
                session_id=session.id,
                node_id=node.node_id,
                error=str(e),
                exc_info=True,
            )
            return {
                "type": "error",
                "error": f"Failed to process script node: {str(e)}",
            }

    async def _get_next_connection(
        self,
        db: AsyncSession,
        node: FlowNode,
        connection_type=None,
    ):
        """Get the next connection from current node."""
        from app.models.cms import ConnectionType as CT
        from app.repositories.chat_repository import chat_repo

        if connection_type is None:
            connection_type = CT.DEFAULT

        connections = await chat_repo.get_node_connections(
            db, flow_id=node.flow_id, source_node_id=node.node_id
        )

        # Try to find specific connection type
        for conn in connections:
            if conn.connection_type == connection_type:
                return conn

        # Fall back to default if not found
        if connection_type != CT.DEFAULT:
            for conn in connections:
                if conn.connection_type == CT.DEFAULT:
                    return conn

        return None
