"""
Action node processor with api_call action type implementation.

Handles ACTION nodes with various action types including:
- set_variable: Set session variables
- increment/decrement: Numeric operations
- api_call: Internal API calls with authentication
- delete_variable: Remove variables
- aggregate: Aggregate values from a list using various operations
- emit_event: Fire-and-forget analytics event creation
"""

import datetime
import re
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models.cms import (
    ConnectionType,
    ConversationSession,
    FlowNode,
    InteractionType,
)
from app.repositories.chat_repository import chat_repo
from app.services.api_client import ApiCallConfig, get_api_client
from app.services.chat_runtime import NodeProcessor
from app.services.cloud_tasks import cloud_tasks

logger = get_logger()


_TEMPLATE_RE = re.compile(r"\{\{.*?\}\}")


def _strip_unresolved_templates(obj: Any) -> Any:
    """Replace unresolved ``{{...}}`` template strings with ``None``.

    Only matches actual template syntax (``{{var}}``) — stray ``{{`` or ``}}``
    in isolation are left untouched.  Recursively processes dicts and lists.
    """
    if isinstance(obj, str) and _TEMPLATE_RE.search(obj):
        logger.debug("Stripping unresolved template", value=obj)
        return None
    elif isinstance(obj, dict):
        return {k: _strip_unresolved_templates(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_strip_unresolved_templates(v) for v in obj]
    return obj


def _extract_nested(data: Dict[str, Any], key_path: str) -> Any:
    """Extract a value from a nested dict using dot notation."""
    keys = key_path.split(".")
    value = data
    try:
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value
    except (KeyError, TypeError):
        return None


class ActionNodeProcessor(NodeProcessor):
    """Processor for ACTION nodes with support for api_call actions."""

    async def process(
        self,
        db: AsyncSession,
        node: FlowNode,
        session: ConversationSession,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Process an action node."""
        # Debug: Verify session type
        logger.debug(
            "ActionNodeProcessor.process called",
            node_id=node.node_id,
            session_type=type(session).__name__,
            db_type=type(db).__name__,
        )
        node_content = node.content or {}
        actions = node_content.get("actions", [])

        # Determine if this should be processed asynchronously.
        # Note: api_call is intentionally excluded — chat flows need synchronous
        # results (e.g. book recommendations) so the flow can continue immediately.
        # Use the explicit "async": true flag on individual actions if needed.
        async_actions = {"external_service", "heavy_computation"}
        should_async = any(
            action.get("type") in async_actions or action.get("async", False)
            for action in actions
        )

        if should_async:
            # Enqueue task for async processing
            try:
                task_name = await cloud_tasks.enqueue_action_task(
                    session_id=session.id,
                    node_id=node.node_id,
                    session_revision=session.revision,
                    action_type="composite",
                    params={"actions": actions},
                )

                logger.info(
                    "Action task enqueued",
                    task_name=task_name,
                    session_id=session.id,
                    node_id=node.node_id,
                    actions=len(actions),
                )

                # For async actions, return immediately and let the task continue flow
                return {
                    "type": "action",
                    "async": True,
                    "task_name": task_name,
                    "actions_count": len(actions),
                    "session_ended": False,
                }

            except Exception as e:
                logger.error(
                    "Failed to enqueue action task",
                    error=str(e),
                    session_id=session.id,
                    node_id=node.node_id,
                )
                # Fallback to synchronous processing

        # Process synchronously
        result = await self._execute_actions_sync(
            db, session, actions, node.node_id, context
        )

        # Get next connection
        connection_type = (
            ConnectionType.SUCCESS if result["success"] else ConnectionType.FAILURE
        )
        next_connection = await self.get_next_connection(db, node, connection_type)

        # Fall back to default if specific connection not found
        if not next_connection:
            next_connection = await self.get_next_connection(
                db, node, ConnectionType.DEFAULT
            )

        if next_connection:
            next_node = await chat_repo.get_flow_node(
                db, flow_id=node.flow_id, node_id=next_connection.target_node_id
            )
            if next_node:
                # Refresh session to get updated state after action execution
                refreshed_session = await chat_repo.get_session_by_id(db, session.id)
                return await self.runtime.process_node(
                    db, next_node, refreshed_session or session, context
                )

        return {
            "type": "action",
            "success": result["success"],
            "variables": result["variables"],
            "errors": result["errors"],
            "session_ended": not next_connection,
        }

    async def _execute_actions_sync(
        self,
        db: AsyncSession,
        session: ConversationSession,
        actions: list,
        node_id: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute actions synchronously."""
        variables_updated = {}
        errors = []
        success = True

        current_state = session.state or {}

        # Inject db session and user ID for internal API calls
        context = {**context, "db": db}
        if session.user_id:
            context["session_user_id"] = str(session.user_id)

        for i, action in enumerate(actions):
            action_type = action.get("type")
            action_id = f"{node_id}_action_{i}"

            try:
                if action_type == "set_variable":
                    await self._handle_set_variable(
                        action, current_state, variables_updated
                    )

                elif action_type == "increment":
                    await self._handle_increment(
                        action, current_state, variables_updated
                    )

                elif action_type == "decrement":
                    await self._handle_decrement(
                        action, current_state, variables_updated
                    )

                elif action_type == "delete_variable":
                    await self._handle_delete_variable(
                        action, current_state, variables_updated
                    )

                elif action_type == "api_call":
                    await self._handle_api_call(
                        action, current_state, variables_updated, context
                    )

                elif action_type == "aggregate":
                    await self._handle_aggregate(
                        action, current_state, variables_updated
                    )

                elif action_type == "emit_event":
                    await self._handle_emit_event(
                        action, current_state, variables_updated, context
                    )

                else:
                    logger.warning(f"Unknown action type: {action_type}")
                    errors.append(f"Unknown action type: {action_type}")

            except Exception as e:
                error_msg = f"Action {action_id} failed: {str(e)}"
                logger.error(
                    "Action execution error", action_id=action_id, error=str(e)
                )
                errors.append(error_msg)
                success = False

        # Update session state if variables were modified
        if variables_updated:
            self._deep_merge(current_state, variables_updated)
            await chat_repo.update_session_state(
                db,
                session_id=session.id,
                state_updates=current_state,
                expected_revision=session.revision,
            )

        # Record action execution in history
        await chat_repo.add_interaction_history(
            db,
            session_id=session.id,
            node_id=node_id,
            interaction_type=InteractionType.ACTION,
            content={
                "type": "action_execution",
                "actions_count": len(actions),
                "variables_updated": list(variables_updated.keys()),
                "success": success,
                "errors": errors,
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "processed_async": False,
            },
        )

        return {
            "success": success and len(errors) == 0,
            "variables": variables_updated,
            "errors": errors,
        }

    async def _handle_set_variable(
        self, action: Dict[str, Any], state: Dict[str, Any], updates: Dict[str, Any]
    ) -> None:
        """Handle set_variable action."""
        variable = action.get("variable")
        value = action.get("value")

        if variable and value is not None:
            # Recursively substitute variables in the value.
            # substitute_object handles strings, lists, and dicts, and preserves
            # typed values when the entire string is a single {{var}} reference.
            value = self.runtime.substitute_object(value, state)

            self._set_nested_value(updates, variable, value)
            logger.debug(f"Set variable {variable} = {value}")

    async def _handle_increment(
        self, action: Dict[str, Any], state: Dict[str, Any], updates: Dict[str, Any]
    ) -> None:
        """Handle increment action."""
        variable = action.get("variable")
        amount = action.get("amount", 1)

        if variable:
            current = self._get_nested_value(state, variable) or 0
            new_value = current + amount
            self._set_nested_value(updates, variable, new_value)
            logger.debug(f"Incremented {variable}: {current} + {amount} = {new_value}")

    async def _handle_decrement(
        self, action: Dict[str, Any], state: Dict[str, Any], updates: Dict[str, Any]
    ) -> None:
        """Handle decrement action."""
        variable = action.get("variable")
        amount = action.get("amount", 1)

        if variable:
            current = self._get_nested_value(state, variable) or 0
            new_value = current - amount
            self._set_nested_value(updates, variable, new_value)
            logger.debug(f"Decremented {variable}: {current} - {amount} = {new_value}")

    async def _handle_delete_variable(
        self, action: Dict[str, Any], state: Dict[str, Any], updates: Dict[str, Any]
    ) -> None:
        """Handle delete_variable action."""
        variable = action.get("variable")

        if variable:
            self._set_nested_value(updates, variable, None)
            logger.debug(f"Deleted variable {variable}")

    async def _handle_api_call(
        self,
        action: Dict[str, Any],
        state: Dict[str, Any],
        updates: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        """Handle api_call action."""
        api_config_data = action.get("config", {})
        auth_type = api_config_data.get("auth_type", "internal")

        # For internal endpoints, try direct service call (bypasses HTTP + auth)
        if auth_type == "internal":
            from app.services.internal_api_handlers import INTERNAL_HANDLERS

            endpoint = api_config_data.get("endpoint", "")
            db = context.get("db")

            if endpoint in INTERNAL_HANDLERS and db is not None:
                resolved_body = self.runtime.substitute_object(
                    api_config_data.get("body", {}), state
                )
                resolved_body = _strip_unresolved_templates(resolved_body)
                resolved_params = self.runtime.substitute_object(
                    api_config_data.get("query_params", {}), state
                )
                resolved_params = _strip_unresolved_templates(resolved_params)

                try:
                    result_data = await INTERNAL_HANDLERS[endpoint](
                        db, resolved_body, resolved_params
                    )
                except Exception:
                    logger.error(
                        "Internal handler failed",
                        endpoint=endpoint,
                        exc_info=True,
                    )
                    fallback = api_config_data.get("fallback_response")
                    if fallback is not None:
                        result_data = fallback
                    else:
                        raise

                response_mapping = api_config_data.get("response_mapping", {})
                for response_path, variable_name in response_mapping.items():
                    value = _extract_nested(result_data, response_path)
                    if value is not None:
                        self._set_nested_value(updates, variable_name, value)

                logger.info(
                    "Internal API call via direct service call",
                    endpoint=endpoint,
                    variables_updated=list(response_mapping.values()),
                )
                return

        # For authenticated sessions, generate a short-lived JWT
        if auth_type == "internal" and context.get("session_user_id"):
            from app.services.security import create_access_token

            token = create_access_token(
                subject=f"Wriveted:User-Account:{context['session_user_id']}",
                expires_delta=datetime.timedelta(minutes=5),
            )
            api_config_data = {**api_config_data}
            api_config_data["auth_type"] = "bearer"
            api_config_data["auth_config"] = {"token": token}

        # Create API call configuration
        api_config = ApiCallConfig(**api_config_data)

        # Get composite scopes from context if available
        composite_scopes = context.get("composite_scopes")

        # Execute API call
        api_client = get_api_client()
        result = await api_client.execute_api_call(api_config, state, composite_scopes)

        if result.success:
            # Update variables with API response using nested paths
            for var_name, var_value in result.variables_updated.items():
                self._set_nested_value(updates, var_name, var_value)
            logger.info(
                "API call successful",
                endpoint=api_config.endpoint,
                variables_updated=list(result.variables_updated.keys()),
            )
        else:
            # Store error information
            error_var = api_config_data.get("error_variable", "api_error")
            self._set_nested_value(
                updates,
                error_var,
                {
                    "error": result.error_message,
                    "status_code": result.status_code,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                    "fallback_used": result.fallback_used,
                },
            )
            logger.error(
                "API call failed",
                endpoint=api_config.endpoint,
                error=result.error_message,
            )

    async def _handle_aggregate(
        self, action: Dict[str, Any], state: Dict[str, Any], updates: Dict[str, Any]
    ) -> None:
        """Handle aggregate action using CEL expressions.

        Evaluates a CEL expression against session state and stores the result
        in a target variable. Uses custom CEL functions for aggregation:
        - sum(list): Sum numeric values
        - avg(list): Calculate average
        - max(list): Find maximum value
        - min(list): Find minimum value
        - count(list): Count items
        - merge(list_of_dicts): Merge dicts by summing numeric values
        - merge_max(list_of_dicts): Merge dicts taking max values
        - merge_last(list_of_dicts): Merge dicts with last value wins
        - flatten(list_of_lists): Flatten nested lists
        - collect(list): Alias for flatten

        Action config (CEL-based - recommended):
        - expression: CEL expression to evaluate (e.g., "sum(temp.scores)")
        - target: Variable path to store result (e.g., "user.total_score")

        Legacy config (for backward compatibility):
        - source: Variable path containing a list of objects (e.g., "temp.answers")
        - field: Optional field to extract from each object (e.g., "score")
        - operation: Aggregation operation (sum, avg, max, min, count, merge, collect)
        - target: Variable path to store result (e.g., "user.total_score")
        - merge_strategy: For merge operation - "sum" (default), "max", "last"

        CEL expression examples:
        - sum(temp.scores)
        - sum(temp.quiz_answers.map(x, x.score))
        - avg(temp.ratings)
        - merge(temp.preferences.map(x, x.hue_map))
        - merge_max(temp.skill_assessments)
        - flatten(temp.selections.map(x, x.tags))
        """
        from app.services.cel_evaluator import evaluate_cel_expression

        expression = action.get("expression")
        target = action.get("target")

        if not target:
            logger.warning("Aggregate action missing required target")
            return

        # Merge state with updates to get current values
        current_context = {**state, **updates}

        # If expression is provided, use CEL evaluation directly
        if expression:
            try:
                result = evaluate_cel_expression(expression, current_context)
                if result is not None:
                    self._set_nested_value(updates, target, result)
                    logger.debug(f"CEL aggregate: {expression} -> {target} = {result}")
            except Exception as e:
                logger.error(
                    "CEL aggregate expression failed",
                    expression=expression,
                    error=str(e),
                )
            return

        # Legacy format: convert to CEL expression for backward compatibility
        source = action.get("source")
        field = action.get("field")
        operation = action.get("operation", "sum")
        merge_strategy = action.get("merge_strategy", "sum")

        if not source:
            logger.warning("Aggregate action missing required source or expression")
            return

        # Build CEL expression from legacy config
        cel_expression = self._build_cel_expression(
            source, field, operation, merge_strategy
        )

        try:
            result = evaluate_cel_expression(cel_expression, current_context)
            if result is not None:
                self._set_nested_value(updates, target, result)
                logger.debug(
                    f"Aggregated with CEL ({cel_expression}): {target} = {result}"
                )
        except Exception as e:
            logger.error(
                "CEL aggregate expression failed",
                expression=cel_expression,
                error=str(e),
            )

    def _build_cel_expression(
        self, source: str, field: str | None, operation: str, merge_strategy: str
    ) -> str:
        """Build a CEL expression from legacy aggregate action config.

        Converts the old-style aggregate config into equivalent CEL expressions.
        """
        # Build the data accessor - with optional field extraction
        if field:
            # Map over source to extract field: source.map(x, x.field)
            data_expr = f"{source}.map(x, x.{field})"
        else:
            data_expr = source

        # Map operation to CEL function
        if operation == "sum":
            return f"sum({data_expr})"
        elif operation == "avg":
            return f"avg({data_expr})"
        elif operation == "max":
            return f"max({data_expr})"
        elif operation == "min":
            return f"min({data_expr})"
        elif operation == "count":
            return f"count({data_expr})"
        elif operation == "merge":
            if merge_strategy == "max":
                return f"merge_max({data_expr})"
            elif merge_strategy == "last":
                return f"merge_last({data_expr})"
            else:
                # Default is sum strategy
                return f"merge({data_expr})"
        elif operation == "collect":
            return f"flatten({data_expr})"
        else:
            raise ValueError(f"Unknown aggregate operation: {operation}")

    def _deep_merge(self, base: Dict[str, Any], updates: Dict[str, Any]) -> None:
        """Recursively merge updates into base dict, preserving existing nested keys."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _get_nested_value(self, data: Dict[str, Any], key_path: str) -> Any:
        """Get nested value from dictionary using dot notation."""
        keys = key_path.split(".")
        value = data

        try:
            for key in keys:
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    return None
            return value
        except (KeyError, TypeError):
            return None

    def _set_nested_value(
        self, data: Dict[str, Any], key_path: str, value: Any
    ) -> None:
        """Set nested value in dictionary using dot notation."""
        keys = key_path.split(".")
        current = data

        # Navigate to parent of target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # Set the final value
        current[keys[-1]] = value

    # ------------------------------------------------------------------
    # emit_event action
    # ------------------------------------------------------------------

    async def _handle_emit_event(
        self,
        action: Dict[str, Any],
        state: Dict[str, Any],
        updates: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        """Handle emit_event action — fire-and-forget analytics event creation.

        Errors are logged but never raised so telemetry cannot break the chat flow.
        """
        try:
            await self._emit_event_inner(action, state, updates, context)
        except Exception:
            logger.error(
                "emit_event failed (fire-and-forget)",
                action_title=action.get("title"),
                exc_info=True,
            )

    async def _emit_event_inner(
        self,
        action: Dict[str, Any],
        state: Dict[str, Any],
        updates: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        """Inner implementation for emit_event — may raise on DB errors."""
        from app.repositories.event_repository import event_repository

        db: AsyncSession | None = context.get("db")
        if db is None:
            logger.warning("emit_event skipped: no db session in context")
            return

        # Merge state with updates to get current values
        current_state = {**state}
        self._deep_merge(current_state, updates)

        iterate_over = action.get("iterate_over")
        if iterate_over:
            await self._emit_iterated_events(
                action, current_state, context, db, event_repository
            )
            return

        title = self.runtime.substitute_variables(
            action.get("title", ""), current_state
        )
        description = action.get("description")
        if description:
            description = self.runtime.substitute_variables(description, current_state)

        info = self.runtime.substitute_object(action.get("info", {}), current_state)
        info = _strip_unresolved_templates(info)

        school = await self._resolve_school(db, current_state, context)
        service_account = await self._resolve_service_account(db, action, context)

        await event_repository.acreate(
            session=db,
            title=title,
            description=description,
            info=info,
            school=school,
            account=service_account,
            commit=False,
        )
        logger.debug("emit_event created", title=title)

    async def _emit_iterated_events(
        self,
        action: Dict[str, Any],
        current_state: Dict[str, Any],
        context: Dict[str, Any],
        db: "AsyncSession",
        event_repository: Any,
    ) -> None:
        """Create one event per item in a list variable."""
        iterate_over = action["iterate_over"]
        item_alias = action.get("item_alias", "item")
        items = self._get_nested_value(current_state, iterate_over)

        if not isinstance(items, list):
            logger.warning(
                "emit_event iterate_over is not a list",
                iterate_over=iterate_over,
                actual_type=type(items).__name__ if items is not None else "None",
            )
            return

        school = await self._resolve_school(db, current_state, context)
        service_account = await self._resolve_service_account(db, action, context)

        for item in items:
            # Inject item into temp.<alias> scope for template resolution
            iter_state = {**current_state}
            temp = dict(iter_state.get("temp", {}))
            temp[item_alias] = item
            iter_state["temp"] = temp

            title = self.runtime.substitute_variables(
                action.get("title", ""), iter_state
            )
            description = action.get("description")
            if description:
                description = self.runtime.substitute_variables(description, iter_state)

            info = self.runtime.substitute_object(action.get("info", {}), iter_state)
            info = _strip_unresolved_templates(info)

            try:
                await event_repository.acreate(
                    session=db,
                    title=title,
                    description=description,
                    info=info,
                    school=school,
                    account=service_account,
                    commit=False,
                )
            except Exception:
                logger.error(
                    "emit_event iteration failed",
                    title=title,
                    exc_info=True,
                )

        logger.debug(
            "emit_event iterated",
            title=action.get("title"),
            count=len(items),
        )

    async def _resolve_school(
        self,
        db: "AsyncSession",
        state: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Any:
        """Resolve School from context.school_wriveted_id, cached on context dict."""
        cached = context.get("_resolved_school")
        if cached is not None:
            return cached

        from uuid import UUID

        from sqlalchemy import select

        from app.models.school import School

        school_id_str = self._get_nested_value(state, "context.school_wriveted_id")
        if not school_id_str:
            return None

        try:
            school_uuid = UUID(str(school_id_str))
            result = await db.execute(
                select(School).where(School.wriveted_identifier == school_uuid)
            )
            school = result.scalars().first()
            if school:
                context["_resolved_school"] = school
            return school
        except Exception:
            logger.warning(
                "Could not resolve school for emit_event",
                school_id=school_id_str,
                exc_info=True,
            )
            return None

    async def _resolve_service_account(
        self,
        db: "AsyncSession",
        action: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Any:
        """Resolve ServiceAccount by name, cached on context dict."""
        sa_name = action.get("service_account")
        if not sa_name:
            return None

        cache_key = f"_resolved_sa_{sa_name}"
        cached = context.get(cache_key)
        if cached is not None:
            return cached

        from sqlalchemy import select

        from app.models.service_account import ServiceAccount

        try:
            result = await db.execute(
                select(ServiceAccount).where(ServiceAccount.name == sa_name)
            )
            sa = result.scalars().first()
            if sa:
                context[cache_key] = sa
            return sa
        except Exception:
            logger.warning(
                "Could not resolve service account for emit_event",
                service_account_name=sa_name,
                exc_info=True,
            )
            return None
